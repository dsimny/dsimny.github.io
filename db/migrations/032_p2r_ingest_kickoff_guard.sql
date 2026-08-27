-- =============================================================================
-- 032_p2r_ingest_kickoff_guard.sql
-- =============================================================================
-- Two changes, both about the kickoff boundary.
--
-- 1. Ingestion takes `events FOR SHARE`. Without it, a quote arriving while
--    mark_event_live_rpc is capturing closing lines does not serialise against
--    it, so a pre-game quote could land AFTER capture had already chosen the
--    closing number -- leaving a later pre-game quote that is not the closing
--    line, and briefly executable.
--
-- 2. A NON-in-play quote timestamped at or after the actual kickoff is refused
--    outright. After kickoff a pre-game price is a provider error by
--    definition, and accepting one would manufacture an executable price for a
--    game already under way.
--
-- Lock order is unchanged and still safe: this path takes the event share lock
-- and no chapter lock, so it cannot participate in a cycle.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.ingest_market_snapshot_rpc(
    p_event_id        UUID,
    p_market_type     public.market_type,
    p_selection       TEXT,
    p_line            NUMERIC,
    p_price           INT,
    p_sportsbook      TEXT,
    p_source_provider TEXT,
    p_captured_at     TIMESTAMPTZ DEFAULT NULL,
    p_is_in_play      BOOLEAN DEFAULT FALSE
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_event     public.events%ROWTYPE;
    v_latest    public.market_snapshots%ROWTYPE;
    v_captured  TIMESTAMPTZ := COALESCE(p_captured_at, NOW());
    v_refresh   INT;
    v_id        UUID;
BEGIN
    IF p_event_id IS NULL OR p_market_type IS NULL
       OR p_selection IS NULL OR p_price IS NULL
       OR p_sportsbook IS NULL OR p_source_provider IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: event, market, selection, price, book and provider are required';
    END IF;

    IF p_price > -100 AND p_price < 100 THEN
        RAISE EXCEPTION 'INVALID_PRICE: american odds must be <= -100 or >= 100, got %', p_price;
    END IF;

    IF p_market_type = 'MONEYLINE' AND p_line IS NOT NULL THEN
        RAISE EXCEPTION 'INVALID_LINE: MONEYLINE carries no line';
    END IF;
    IF p_market_type IN ('SPREAD', 'TOTAL') AND p_line IS NULL THEN
        RAISE EXCEPTION 'INVALID_LINE: % requires a line', p_market_type;
    END IF;

    IF v_captured > NOW() + INTERVAL '5 seconds' THEN
        RAISE EXCEPTION 'INVALID_CAPTURE_TIME: quote is dated in the future';
    END IF;

    -- Share lock: serialises this quote against kickoff, close and reschedule.
    SELECT * INTO v_event
      FROM public.events
     WHERE id = p_event_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    IF v_event.is_closed THEN
        RAISE EXCEPTION 'EVENT_CLOSED: cannot ingest quotes for a closed event';
    END IF;

    IF v_event.actual_start_time IS NOT NULL
       AND NOT p_is_in_play
       AND v_captured >= v_event.actual_start_time THEN
        RAISE EXCEPTION
            'POST_KICKOFF_PREGAME_QUOTE: a pre-game price cannot be dated at or after kickoff';
    END IF;

    SELECT snapshot_refresh_seconds INTO v_refresh
      FROM public.system_settings WHERE id = TRUE;

    SELECT * INTO v_latest
      FROM public.market_snapshots
     WHERE event_id    = p_event_id
       AND market_type = p_market_type
       AND selection   = p_selection
       AND sportsbook  = p_sportsbook
     ORDER BY captured_at DESC, ingest_seq DESC
     LIMIT 1;

    IF FOUND THEN
        IF v_latest.line       IS NOT DISTINCT FROM p_line
           AND v_latest.price      = p_price
           AND v_latest.is_in_play = p_is_in_play
           AND v_captured - v_latest.captured_at
               < make_interval(secs => v_refresh)
        THEN
            RETURN NULL;   -- nothing changed and the quote is still fresh
        END IF;
    END IF;

    INSERT INTO public.market_snapshots (
        event_id, market_type, selection, line, price,
        sportsbook, source_provider, captured_at, is_in_play
    )
    VALUES (
        p_event_id, p_market_type, p_selection, p_line, p_price,
        p_sportsbook, p_source_provider, v_captured, p_is_in_play
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$fn$;

REVOKE ALL ON FUNCTION public.ingest_market_snapshot_rpc(
    UUID, public.market_type, TEXT, NUMERIC, INT, TEXT, TEXT, TIMESTAMPTZ, BOOLEAN)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ingest_market_snapshot_rpc(
    UUID, public.market_type, TEXT, NUMERIC, INT, TEXT, TEXT, TIMESTAMPTZ, BOOLEAN)
    TO service_role;
