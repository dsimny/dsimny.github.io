-- =============================================================================
-- 024_ingest_snapshots_rpc.sql -- Odds feed -> market_snapshots
-- =============================================================================
-- SERVICE ROLE ONLY.
--
-- Snapshots are immutable history, so ingestion only ever APPENDS. The job here
-- is deciding when an append is worth making:
--
--   price/line/in-play changed          -> append (the market moved)
--   unchanged, older than refresh window -> append (keeps the quote inside the
--                                           placement TTL -- see migration 020)
--   unchanged, inside refresh window     -> skip (a poll that told us nothing)
--
-- Out-of-order arrivals are appended too. They can never become the current
-- quote because every read orders by captured_at DESC, ingest_seq DESC.
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

    -- Small tolerance for provider clock skew; anything further ahead is a bug.
    IF v_captured > NOW() + INTERVAL '5 seconds' THEN
        RAISE EXCEPTION 'INVALID_CAPTURE_TIME: quote is dated in the future';
    END IF;

    SELECT * INTO v_event FROM public.events WHERE id = p_event_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    IF v_event.is_closed THEN
        RAISE EXCEPTION 'EVENT_CLOSED: cannot ingest quotes for a closed event';
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


-- -----------------------------------------------------------------------------
-- Batch form: one round trip per provider poll.
--
-- A single malformed row must not discard an entire poll of good quotes, so
-- per-row failures are captured and returned rather than aborting. They are
-- reported, never swallowed -- the caller records them against the run.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.ingest_market_snapshots_rpc(
    p_rows   JSONB,
    p_run_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_row      JSONB;
    v_id       UUID;
    v_written  INT := 0;
    v_skipped  INT := 0;
    v_failed   INT := 0;
    v_errors   JSONB := '[]'::jsonb;
BEGIN
    IF p_rows IS NULL OR jsonb_typeof(p_rows) <> 'array' THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_rows must be a JSON array';
    END IF;

    FOR v_row IN SELECT * FROM jsonb_array_elements(p_rows)
    LOOP
        BEGIN
            v_id := public.ingest_market_snapshot_rpc(
                (v_row ->> 'event_id')::uuid,
                (v_row ->> 'market_type')::public.market_type,
                 v_row ->> 'selection',
                NULLIF(v_row ->> 'line', '')::numeric,
                (v_row ->> 'price')::int,
                 v_row ->> 'sportsbook',
                 v_row ->> 'source_provider',
                NULLIF(v_row ->> 'captured_at', '')::timestamptz,
                COALESCE((v_row ->> 'is_in_play')::boolean, FALSE)
            );

            IF v_id IS NULL THEN
                v_skipped := v_skipped + 1;
            ELSE
                v_written := v_written + 1;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_failed := v_failed + 1;
            v_errors := v_errors || jsonb_build_object(
                'row',   v_row,
                'error', SQLERRM
            );
        END;
    END LOOP;

    IF p_run_id IS NOT NULL THEN
        UPDATE public.ingestion_runs
           SET snapshots_written = snapshots_written + v_written,
               snapshots_skipped = snapshots_skipped + v_skipped
         WHERE id = p_run_id;
    END IF;

    RETURN jsonb_build_object(
        'written', v_written,
        'skipped', v_skipped,
        'failed',  v_failed,
        'errors',  v_errors
    );
END;
$fn$;

REVOKE ALL ON FUNCTION public.ingest_market_snapshot_rpc(
    UUID, public.market_type, TEXT, NUMERIC, INT, TEXT, TEXT, TIMESTAMPTZ, BOOLEAN)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ingest_market_snapshot_rpc(
    UUID, public.market_type, TEXT, NUMERIC, INT, TEXT, TEXT, TIMESTAMPTZ, BOOLEAN)
    TO service_role;

REVOKE ALL ON FUNCTION public.ingest_market_snapshots_rpc(JSONB, UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ingest_market_snapshots_rpc(JSONB, UUID)
    TO service_role;
