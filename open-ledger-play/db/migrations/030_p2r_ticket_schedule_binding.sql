-- =============================================================================
-- 030_p2r_ticket_schedule_binding.sql
-- Package #2 revision: bind each ticket to the schedule it was accepted against
-- =============================================================================
-- Architectural review changed postponement from an EVENT-relative rule to a
-- TICKET-relative one: displacement is measured from the start time that was in
-- effect when THAT ticket was accepted, not from the event's original start.
--
-- That requires knowing, per ticket, which schedule it bought into.
--
-- WHY A COLUMN RATHER THAN DERIVING IT FROM event_schedule_history:
-- the history is complete, so `the row in effect at tickets.accepted_at` looks
-- sufficient. It is not. accepted_at defaults to NOW(), which in PostgreSQL is
-- TRANSACTION start time. A placement that blocks on a concurrent reschedule
-- resumes and commits with a timestamp that PREDATES the reschedule, so the
-- derivation would hand it the stale schedule -- precisely the ticket the sweep
-- already decided not to void. Recording the value the RPC actually validated
-- against removes the inference entirely.
--
-- This is the one place Package #2 touches a Package #1 table. The column is
-- additive, and it is frozen at insert exactly like the rest of the accepted
-- economics -- the schedule you bet into is part of what you accepted.
-- =============================================================================

ALTER TABLE public.tickets
    ADD COLUMN accepted_event_start TIMESTAMPTZ;

-- Backfill before the freeze trigger learns about the column. Existing rows get
-- the event's original start, which reproduces the previous event-relative
-- behaviour for anything placed under the old rule.
UPDATE public.tickets t
   SET accepted_event_start = e.original_scheduled_start
  FROM public.events e
 WHERE e.id = t.event_id
   AND t.accepted_event_start IS NULL;

ALTER TABLE public.tickets
    ALTER COLUMN accepted_event_start SET NOT NULL;

COMMENT ON COLUMN public.tickets.accepted_event_start IS
    'The event start time in effect when this ticket was accepted. Postponement '
    'displacement is measured from here, per ticket. Immutable after insert.';

-- -----------------------------------------------------------------------------
-- The new column joins the frozen set.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_freeze_ticket_economics()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'APPEND_ONLY_VIOLATION: tickets cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF (NEW.id, NEW.user_id, NEW.chapter_id, NEW.event_id, NEW.market_snapshot_id,
        NEW.market_type, NEW.selection, NEW.accepted_line, NEW.accepted_price,
        NEW.accepted_sportsbook, NEW.snapshot_captured_at, NEW.risk,
        NEW.potential_profit, NEW.submission_idempotency_key,
        NEW.submitted_at, NEW.accepted_at, NEW.accepted_event_start)
       IS DISTINCT FROM
       (OLD.id, OLD.user_id, OLD.chapter_id, OLD.event_id, OLD.market_snapshot_id,
        OLD.market_type, OLD.selection, OLD.accepted_line, OLD.accepted_price,
        OLD.accepted_sportsbook, OLD.snapshot_captured_at, OLD.risk,
        OLD.potential_profit, OLD.submission_idempotency_key,
        OLD.submitted_at, OLD.accepted_at, OLD.accepted_event_start)
    THEN
        RAISE EXCEPTION
            'IMMUTABLE_TICKET: accepted economics cannot be modified after acceptance'
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

-- =============================================================================
-- place_ticket_rpc -- now takes a share lock on the event before the chapter.
-- =============================================================================
-- DEVIATION FROM PACKAGE #1 SECTION 20, stated plainly.
--
-- Section 20 fixes the sequence as: idempotency -> chapter FOR UPDATE -> ...
-- Placement now acquires `events FOR SHARE` BEFORE the chapter lock.
--
-- Without it, placement and postponement do not serialise at all: placement
-- locks a chapter, postponement locks an event. A placement in flight is
-- invisible to a postponement's void sweep, so a ticket could be accepted
-- against a schedule that had already been abandoned and then never voided.
--
-- The share lock makes the two conflict. The ORDER matters as much as the lock:
-- reschedule/cancel take event(exclusive) -> chapter(exclusive), so placement
-- must take event(share) -> chapter(exclusive) and not the reverse, or the two
-- paths deadlock. Every event-touching path in this schema now acquires the
-- event before any chapter.
--
-- The chapter FOR UPDATE is untouched. Nothing about the capital decision moved.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.place_ticket_rpc(
    p_chapter_id      UUID,
    p_snapshot_id     UUID,
    p_risk            NUMERIC,
    p_idempotency_key UUID
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_user_id      UUID;
    v_ticket_id    UUID;
    v_chapter      public.ledger_chapters%ROWTYPE;
    v_snapshot     public.market_snapshots%ROWTYPE;
    v_event        public.events%ROWTYPE;
    v_latest_id    UUID;
    v_ttl_seconds  INT;
    v_fraction     NUMERIC(5,4);
    v_settled      NUMERIC(12,2);
    v_escrow       NUMERIC(12,2);
    v_available    NUMERIC(12,2);
    v_max_ticket   NUMERIC(12,2);
    v_profit       NUMERIC(12,2);
BEGIN
    -- 0. Identity ------------------------------------------------------------
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'AUTH_REQUIRED: an authenticated identity is required'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF p_idempotency_key IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_idempotency_key is required';
    END IF;
    IF p_chapter_id IS NULL OR p_snapshot_id IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: chapter and snapshot are required';
    END IF;

    -- 1. Idempotency lookup (pre-lock fast path) -----------------------------
    SELECT id INTO v_ticket_id
      FROM public.tickets
     WHERE user_id = v_user_id
       AND submission_idempotency_key = p_idempotency_key;
    IF FOUND THEN
        RETURN v_ticket_id;
    END IF;

    -- 2. Resolve the snapshot. Snapshots are immutable, so this needs no lock.
    SELECT * INTO v_snapshot
      FROM public.market_snapshots
     WHERE id = p_snapshot_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SNAPSHOT_NOT_FOUND: unknown market snapshot';
    END IF;

    -- 3. Event share lock. Serialises against reschedule / kickoff / cancel,
    --    and is taken BEFORE the chapter lock so those paths cannot deadlock.
    SELECT * INTO v_event
      FROM public.events
     WHERE id = v_snapshot.event_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: snapshot has no parent event';
    END IF;

    -- 4. Chapter lock. Serialises ALL capital decisions for this chapter. ----
    SELECT * INTO v_chapter
      FROM public.ledger_chapters
     WHERE id      = p_chapter_id
       AND user_id = v_user_id
       AND status  = 'ACTIVE'
     FOR UPDATE;

    IF NOT FOUND THEN
        IF EXISTS (
            SELECT 1 FROM public.ledger_chapters
            WHERE id = p_chapter_id AND user_id = v_user_id
        ) THEN
            RAISE EXCEPTION 'CHAPTER_NOT_ACTIVE: chapter is not accepting tickets';
        END IF;
        RAISE EXCEPTION 'CHAPTER_NOT_AVAILABLE: no such active chapter for this user'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- 5. Re-check idempotency under the lock.
    SELECT id INTO v_ticket_id
      FROM public.tickets
     WHERE user_id = v_user_id
       AND submission_idempotency_key = p_idempotency_key;
    IF FOUND THEN
        RETURN v_ticket_id;
    END IF;

    -- 6. Market validation. v_event is the locked, current row.
    IF v_snapshot.is_in_play THEN
        RAISE EXCEPTION 'IN_PLAY_NOT_ALLOWED: in-play quotes are not executable';
    END IF;

    IF v_event.is_closed THEN
        RAISE EXCEPTION 'EVENT_CLOSED: event is closed';
    END IF;

    IF v_event.is_live THEN
        RAISE EXCEPTION 'EVENT_LIVE: event is already live';
    END IF;

    IF v_event.actual_start_time IS NOT NULL THEN
        RAISE EXCEPTION 'EVENT_STARTED: event has already started';
    END IF;

    IF NOW() >= v_event.current_scheduled_start THEN
        RAISE EXCEPTION 'EVENT_STARTED: scheduled start time has passed';
    END IF;

    SELECT snapshot_ttl_seconds, max_ticket_fraction
      INTO v_ttl_seconds, v_fraction
      FROM public.system_settings
     WHERE id = TRUE;

    IF v_snapshot.captured_at > NOW() THEN
        RAISE EXCEPTION 'SNAPSHOT_NOT_YET_EFFECTIVE: quote is future-dated';
    END IF;

    IF NOW() - v_snapshot.captured_at > make_interval(secs => v_ttl_seconds) THEN
        RAISE EXCEPTION 'SNAPSHOT_STALE: quote is older than the configured TTL';
    END IF;

    SELECT id INTO v_latest_id
      FROM public.market_snapshots
     WHERE event_id    = v_snapshot.event_id
       AND market_type = v_snapshot.market_type
       AND selection   = v_snapshot.selection
       AND sportsbook  = v_snapshot.sportsbook
       AND is_in_play  = FALSE
       AND captured_at <= NOW()
     ORDER BY captured_at DESC, ingest_seq DESC
     LIMIT 1;

    IF v_latest_id IS DISTINCT FROM p_snapshot_id THEN
        RAISE EXCEPTION 'MARKET_MOVED: a newer quote supersedes this one';
    END IF;

    -- 7. Risk shape ----------------------------------------------------------
    IF p_risk IS NULL THEN
        RAISE EXCEPTION 'INVALID_RISK: risk is required';
    END IF;
    IF p_risk <= 0 THEN
        RAISE EXCEPTION 'INVALID_RISK: risk must be greater than zero';
    END IF;
    IF p_risk <> round(p_risk, 2) THEN
        RAISE EXCEPTION 'INVALID_RISK: risk supports at most 2 decimal places';
    END IF;

    -- 8. Balance + exposure, computed under the chapter lock -----------------
    SELECT COALESCE(SUM(amount), 0)
      INTO v_settled
      FROM public.wallet_transactions
     WHERE chapter_id = p_chapter_id;

    SELECT COALESCE(SUM(amount), 0)
      INTO v_escrow
      FROM public.risk_reservations
     WHERE chapter_id = p_chapter_id
       AND status     = 'ACTIVE';

    v_available  := v_settled - v_escrow;
    v_max_ticket := GREATEST(round(v_fraction * v_settled, 2), 0);

    IF v_available <= 0 THEN
        RAISE EXCEPTION 'INSUFFICIENT_CAPITAL: no available capital remains';
    END IF;
    IF p_risk > v_available THEN
        RAISE EXCEPTION 'INSUFFICIENT_CAPITAL: risk exceeds available capital';
    END IF;
    IF p_risk > v_max_ticket THEN
        RAISE EXCEPTION 'TICKET_SIZE_LIMIT: risk exceeds the max ticket size';
    END IF;

    -- 9. Frozen economics, now including the schedule bought into ------------
    v_profit := public.olp_american_profit(p_risk, v_snapshot.price);
    IF v_profit < 0.01 THEN
        RAISE EXCEPTION 'INVALID_RISK: risk is too small to return any profit';
    END IF;

    INSERT INTO public.tickets (
        user_id, chapter_id, event_id, market_snapshot_id,
        market_type, selection, accepted_line, accepted_price,
        accepted_sportsbook, snapshot_captured_at,
        risk, potential_profit, submission_idempotency_key, status,
        accepted_event_start
    )
    VALUES (
        v_user_id, p_chapter_id, v_snapshot.event_id, v_snapshot.id,
        v_snapshot.market_type, v_snapshot.selection, v_snapshot.line,
        v_snapshot.price, v_snapshot.sportsbook, v_snapshot.captured_at,
        p_risk, v_profit, p_idempotency_key, 'ACCEPTED',
        v_event.current_scheduled_start
    )
    RETURNING id INTO v_ticket_id;

    INSERT INTO public.risk_reservations (ticket_id, chapter_id, amount, status)
    VALUES (v_ticket_id, p_chapter_id, p_risk, 'ACTIVE');

    RETURN v_ticket_id;

EXCEPTION
    WHEN unique_violation THEN
        SELECT id INTO v_ticket_id
          FROM public.tickets
         WHERE user_id = v_user_id
           AND submission_idempotency_key = p_idempotency_key;
        IF FOUND THEN
            RETURN v_ticket_id;
        END IF;
        RAISE;
END;
$fn$;

REVOKE ALL     ON FUNCTION public.place_ticket_rpc(UUID, UUID, NUMERIC, UUID) FROM PUBLIC;
REVOKE ALL     ON FUNCTION public.place_ticket_rpc(UUID, UUID, NUMERIC, UUID) FROM anon;
GRANT  EXECUTE ON FUNCTION public.place_ticket_rpc(UUID, UUID, NUMERIC, UUID) TO authenticated;
