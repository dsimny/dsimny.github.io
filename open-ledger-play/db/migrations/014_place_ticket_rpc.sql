-- =============================================================================
-- 014_place_ticket_rpc.sql
-- =============================================================================
-- The only path by which a ticket may come into existence.
-- Signature deliberately omits p_user_id: identity is auth.uid() only.
--
-- Lock order (never vary this):
--   idempotency lookup -> chapter FOR UPDATE -> ownership/status -> market
--   -> balance/exposure -> ticket insert -> reservation insert -> commit
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

    -- 2. Chapter lock. Serializes ALL capital decisions for this chapter. ----
    SELECT * INTO v_chapter
      FROM public.ledger_chapters
     WHERE id      = p_chapter_id
       AND user_id = v_user_id
       AND status  = 'ACTIVE'
     FOR UPDATE;

    IF NOT FOUND THEN
        -- Distinguish "not yours" from "not active" without leaking ownership.
        IF EXISTS (
            SELECT 1 FROM public.ledger_chapters
            WHERE id = p_chapter_id AND user_id = v_user_id
        ) THEN
            RAISE EXCEPTION 'CHAPTER_NOT_ACTIVE: chapter is not accepting tickets';
        END IF;
        RAISE EXCEPTION 'CHAPTER_NOT_AVAILABLE: no such active chapter for this user'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- 3. Re-check idempotency under the lock. A truly concurrent duplicate
    --    submission blocks above and observes the winner's ticket here.
    SELECT id INTO v_ticket_id
      FROM public.tickets
     WHERE user_id = v_user_id
       AND submission_idempotency_key = p_idempotency_key;
    IF FOUND THEN
        RETURN v_ticket_id;
    END IF;

    -- 4. Market validation ---------------------------------------------------
    SELECT * INTO v_snapshot
      FROM public.market_snapshots
     WHERE id = p_snapshot_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SNAPSHOT_NOT_FOUND: unknown market snapshot';
    END IF;

    SELECT * INTO v_event
      FROM public.events
     WHERE id = v_snapshot.event_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: snapshot has no parent event';
    END IF;

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

    -- Fresh is not enough: it must still be the CURRENT same-book quote.
    -- Deterministic order: captured_at DESC, then ingest_seq DESC.
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

    -- 5. Risk shape ----------------------------------------------------------
    IF p_risk IS NULL THEN
        RAISE EXCEPTION 'INVALID_RISK: risk is required';
    END IF;
    IF p_risk <= 0 THEN
        RAISE EXCEPTION 'INVALID_RISK: risk must be greater than zero';
    END IF;
    IF p_risk <> round(p_risk, 2) THEN
        RAISE EXCEPTION 'INVALID_RISK: risk supports at most 2 decimal places';
    END IF;

    -- 6. Balance + exposure, computed under the chapter lock -----------------
    --    settled = SUM(wallet_transactions). starting_capital is NOT involved.
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

    -- 7. Frozen economics ----------------------------------------------------
    v_profit := public.olp_american_profit(p_risk, v_snapshot.price);
    IF v_profit < 0.01 THEN
        RAISE EXCEPTION 'INVALID_RISK: risk is too small to return any profit';
    END IF;

    INSERT INTO public.tickets (
        user_id, chapter_id, event_id, market_snapshot_id,
        market_type, selection, accepted_line, accepted_price,
        accepted_sportsbook, snapshot_captured_at,
        risk, potential_profit, submission_idempotency_key, status
    )
    VALUES (
        v_user_id, p_chapter_id, v_snapshot.event_id, v_snapshot.id,
        v_snapshot.market_type, v_snapshot.selection, v_snapshot.line,
        v_snapshot.price, v_snapshot.sportsbook, v_snapshot.captured_at,
        p_risk, v_profit, p_idempotency_key, 'ACCEPTED'
    )
    RETURNING id INTO v_ticket_id;

    -- 8. Escrow, in the same transaction as the ticket -----------------------
    INSERT INTO public.risk_reservations (ticket_id, chapter_id, amount, status)
    VALUES (v_ticket_id, p_chapter_id, p_risk, 'ACTIVE');

    RETURN v_ticket_id;

EXCEPTION
    WHEN unique_violation THEN
        -- Last-resort idempotency guard (uq_user_idempotency).
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
