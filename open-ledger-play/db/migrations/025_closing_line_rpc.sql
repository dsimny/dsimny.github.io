-- =============================================================================
-- 025_closing_line_rpc.sql -- Same-book closing-line capture
-- =============================================================================
-- SERVICE ROLE ONLY.
--
-- The closing line is the last PRE-GAME quote from EACH BOOK, per market and
-- selection. Grouping by sportsbook is the whole point: comparing a ticket
-- taken at book A against a closing number from book B measures the spread
-- between books, not the quality of the bet.
--
-- Selection is deterministic (captured_at DESC, ingest_seq DESC) and the
-- operation only ever sets is_closing_snapshot false -> true, which is the one
-- mutation the snapshot immutability trigger permits. In-play quotes are
-- excluded here AND forbidden structurally by ck_closing_snapshot_not_in_play.
--
-- Idempotent: a group that already has a closing quote is left alone.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.capture_closing_line_rpc(
    p_event_id UUID,
    p_source   TEXT DEFAULT 'LIFECYCLE'
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_event    public.events%ROWTYPE;
    v_kickoff  TIMESTAMPTZ;
    v_snap     RECORD;
    v_captured INT := 0;
BEGIN
    IF p_event_id IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_event_id is required';
    END IF;

    SELECT * INTO v_event
      FROM public.events
     WHERE id = p_event_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    -- Prefer the real kickoff. A game that started late still had legitimate
    -- pre-game quotes between its scheduled and actual start.
    v_kickoff := COALESCE(v_event.actual_start_time, v_event.current_scheduled_start);

    FOR v_snap IN
        SELECT DISTINCT ON (market_type, selection, sportsbook)
               id, market_type, selection, sportsbook, line, price
          FROM public.market_snapshots
         WHERE event_id    = p_event_id
           AND is_in_play  = FALSE
           AND captured_at <= v_kickoff
         ORDER BY market_type, selection, sportsbook,
                  captured_at DESC, ingest_seq DESC
    LOOP
        CONTINUE WHEN EXISTS (
            SELECT 1 FROM public.market_snapshots
             WHERE event_id            = p_event_id
               AND market_type         = v_snap.market_type
               AND selection           = v_snap.selection
               AND sportsbook          = v_snap.sportsbook
               AND is_closing_snapshot = TRUE
        );

        UPDATE public.market_snapshots
           SET is_closing_snapshot = TRUE
         WHERE id = v_snap.id;

        v_captured := v_captured + 1;
    END LOOP;

    IF v_captured > 0 THEN
        PERFORM public.olp_log_lifecycle(
            p_event_id, 'CLOSING_LINE_CAPTURED', p_source,
            jsonb_build_object('lines_captured', v_captured, 'kickoff', v_kickoff)
        );
    END IF;

    RETURN v_captured;
END;
$fn$;

REVOKE ALL ON FUNCTION public.capture_closing_line_rpc(UUID, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.capture_closing_line_rpc(UUID, TEXT)
    TO service_role;
