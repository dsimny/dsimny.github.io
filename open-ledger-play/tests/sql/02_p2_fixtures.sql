-- =============================================================================
-- 02_p2_fixtures.sql -- TEST HARNESS ONLY. NEVER INSTALLED IN PRODUCTION.
-- =============================================================================
-- This file lives in tests/sql/ and is not in db/migrations/production_manifest.txt.
-- Nothing here may be depended on by a production migration; a migration whose
-- installation needs olp_test couples executable product state to the test
-- environment, which is what made 057 un-deployable.
--
-- Applied by tests/harness.py AFTER the full production manifest:
--
--     production manifest  ->  tests/sql/*  ->  run tests
--
-- Production does only the first step.
-- =============================================================================

-- =============================================================================
-- 029_p2_fixtures.sql -- Package #2 test fixtures (development only)
-- =============================================================================
-- Extends the olp_test schema from migration 019. Same rules apply: owner-only,
-- and a production deployment may skip both.
-- =============================================================================

-- Self-sufficient: 019 normally creates this schema, but 029 must not fail if
-- someone applies the fixture migrations on their own.
CREATE SCHEMA IF NOT EXISTS olp_test;
REVOKE ALL ON SCHEMA olp_test FROM PUBLIC;

-- Package #2 added two tables; the reset must clear them too or state leaks
-- between tests.
CREATE OR REPLACE FUNCTION olp_test.reset()
RETURNS VOID
LANGUAGE plpgsql
AS $fn$
BEGIN
    TRUNCATE
        public.event_lifecycle_log,
        public.ingestion_runs,
        public.ticket_result_adjustments,
        public.ticket_results,
        public.risk_reservations,
        public.wallet_transactions,
        public.tickets,
        public.market_snapshots,
        public.event_schedule_history,
        public.events,
        public.ledger_chapters,
        public.users
    RESTART IDENTITY CASCADE;

    -- auth.users is never truncated: on Supabase it is owned by
    -- supabase_auth_admin and CASCADE would reach auth.refresh_tokens, whose
    -- sequence `postgres` does not own. Scoped DELETE instead.
    DELETE FROM auth.users WHERE email LIKE '%@olp.test';

    -- Restore shipped policy defaults in case a test tuned them.
    UPDATE public.system_settings
       SET snapshot_ttl_seconds     = 120,
           snapshot_refresh_seconds = 60,
           max_ticket_fraction      = 0.1000,
           min_viable_wager         = 100.00,
           default_starting_capital = 10000.00,
           postponement_void_hours  = 48
     WHERE id = TRUE;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- A deterministic multi-book slate, ingested through the REAL RPCs so fixtures
-- exercise production code rather than shortcutting it.
--
-- Each event gets a SPREAD and a MONEYLINE from two books, which is what the
-- same-book closing-line rules need in order to mean anything.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.seed_slate(
    p_events   INT DEFAULT 3,
    p_starts_in INTERVAL DEFAULT INTERVAL '3 hours'
)
RETURNS TABLE (event_id UUID, source_event_id TEXT)
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_i      INT;
    v_res    JSONB;
    v_id     UUID;
    v_src    TEXT;
    v_book   TEXT;
    v_home   TEXT;
    v_away   TEXT;
BEGIN
    FOR v_i IN 1..p_events LOOP
        v_src  := format('NFL-SLATE-%s', v_i);
        v_home := format('HOME%s', v_i);
        v_away := format('AWAY%s', v_i);

        v_res := public.ingest_event_rpc(
            v_src, v_home, v_away, NOW() + p_starts_in, 'NFL', 'NFL', 'FIXTURE');
        v_id  := (v_res ->> 'event_id')::uuid;

        FOREACH v_book IN ARRAY ARRAY['BOOK_A', 'BOOK_B'] LOOP
            PERFORM public.ingest_market_snapshot_rpc(
                v_id, 'SPREAD', v_home, -3.0,
                CASE WHEN v_book = 'BOOK_A' THEN -110 ELSE -108 END,
                v_book, 'FIXTURE', NOW(), FALSE);

            PERFORM public.ingest_market_snapshot_rpc(
                v_id, 'MONEYLINE', v_home, NULL,
                CASE WHEN v_book = 'BOOK_A' THEN -155 ELSE -150 END,
                v_book, 'FIXTURE', NOW(), FALSE);
        END LOOP;

        event_id        := v_id;
        source_event_id := v_src;
        RETURN NEXT;
    END LOOP;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Append a BACK-DATED quote: exactly what a late-arriving provider row looks
-- like. Note this does NOT make the current quote stale -- ordering is by
-- captured_at DESC, ingest_seq DESC, so a back-dated row is recorded as history
-- and never becomes the executable price. To age the CURRENT quote, ingest the
-- first quote with an old captured_at instead.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.age_quote(
    p_event_id   UUID,
    p_market     public.market_type,
    p_selection  TEXT,
    p_sportsbook TEXT,
    p_age        INTERVAL
)
RETURNS UUID
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_last public.market_snapshots%ROWTYPE;
    v_id   UUID;
BEGIN
    SELECT * INTO v_last
      FROM public.market_snapshots
     WHERE event_id = p_event_id AND market_type = p_market
       AND selection = p_selection AND sportsbook = p_sportsbook
     ORDER BY captured_at DESC, ingest_seq DESC
     LIMIT 1;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no quote to age for % % %', p_market, p_selection, p_sportsbook;
    END IF;

    INSERT INTO public.market_snapshots (
        event_id, market_type, selection, line, price,
        sportsbook, source_provider, captured_at, is_in_play
    )
    VALUES (
        p_event_id, p_market, p_selection, v_last.line, v_last.price,
        p_sportsbook, 'FIXTURE', NOW() - p_age, FALSE
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$fn$;
