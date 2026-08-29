-- =============================================================================
-- 01_bootstrap_olp_test.sql -- TEST HARNESS ONLY. NEVER INSTALLED IN PRODUCTION.
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
-- 019_test_fixtures.sql -- Deterministic fixtures (isolated `olp_test` schema)
-- =============================================================================
-- Tests must never depend on a live odds provider. Everything the acceptance
-- suite needs is seeded from here.
--
-- The schema is granted to NOBODY (owner-only). A production deployment may
-- skip this migration entirely; nothing in 001-018 depends on it.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS olp_test;
REVOKE ALL ON SCHEMA olp_test FROM PUBLIC;

-- -----------------------------------------------------------------------------
-- Wipe all transactional state between tests. TRUNCATE does not fire the
-- row-level append-only triggers, which is exactly what a fixture reset needs.
--
-- auth.users is deliberately NOT truncated. On real Supabase it is owned by
-- supabase_auth_admin and TRUNCATE ... CASCADE would reach auth.refresh_tokens,
-- whose sequence `postgres` does not own ("must be owner of sequence
-- refresh_tokens_id_seq"). Test identities are removed by a scoped DELETE on the
-- @olp.test domain instead, which needs only DELETE and never touches the rest
-- of the auth system.
--
-- Truncating public.users does not cascade into auth.users: the FK points the
-- other way (public.users REFERENCES auth.users).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.reset()
RETURNS VOID
LANGUAGE plpgsql
AS $fn$
BEGIN
    TRUNCATE
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

    DELETE FROM auth.users WHERE email LIKE '%@olp.test';
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Identity
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.create_user(p_username TEXT)
RETURNS UUID
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_id UUID := gen_random_uuid();
BEGIN
    INSERT INTO auth.users (id, email)
    VALUES (v_id, p_username || '@olp.test');

    INSERT INTO public.users (id, username)
    VALUES (v_id, p_username);

    RETURN v_id;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Events
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.create_event(
    p_source_id  TEXT,
    p_home       TEXT DEFAULT 'DAL',
    p_away       TEXT DEFAULT 'PHI',
    p_starts_in  INTERVAL DEFAULT INTERVAL '3 hours'
)
RETURNS UUID
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_id    UUID;
    v_start TIMESTAMPTZ := NOW() + p_starts_in;
BEGIN
    INSERT INTO public.events (
        source_event_id, sport, league, home_team, away_team,
        original_scheduled_start, current_scheduled_start
    )
    VALUES (
        p_source_id, 'NFL', 'NFL', p_home, p_away, v_start, v_start
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Snapshots. captured_at is an explicit parameter so TTL and ordering cases
-- are reproducible rather than wall-clock dependent.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.add_snapshot(
    p_event_id    UUID,
    p_market_type public.market_type DEFAULT 'SPREAD',
    p_selection   TEXT              DEFAULT 'DAL',
    p_line        NUMERIC           DEFAULT -3.0,
    p_price       INT               DEFAULT -110,
    p_sportsbook  TEXT              DEFAULT 'TESTBOOK',
    p_captured_at TIMESTAMPTZ       DEFAULT NULL,
    p_in_play     BOOLEAN           DEFAULT FALSE
)
RETURNS UUID
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_id UUID;
BEGIN
    INSERT INTO public.market_snapshots (
        event_id, market_type, selection, line, price,
        sportsbook, source_provider, captured_at, is_in_play
    )
    VALUES (
        p_event_id, p_market_type, p_selection, p_line, p_price,
        p_sportsbook, 'FIXTURE', COALESCE(p_captured_at, NOW()), p_in_play
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- The canonical package fixture:
--
--   NFL Test Game A
--   12:00:00   DAL -3    -110
--   12:00:10   DAL -3.5  -110
--
-- Returns (event_id, first_snapshot_id, second_snapshot_id). The second quote
-- supersedes the first while BOTH remain inside a normal TTL -- this is the
-- fixture behind the fresh-but-superseded rejection.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.seed_game_a(
    p_source_id TEXT DEFAULT 'NFL-TEST-GAME-A',
    p_base      TIMESTAMPTZ DEFAULT NULL
)
RETURNS TABLE (event_id UUID, snapshot_1 UUID, snapshot_2 UUID)
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_event UUID;
    v_base  TIMESTAMPTZ := COALESCE(p_base, NOW() - INTERVAL '10 seconds');
    v_s1    UUID;
    v_s2    UUID;
BEGIN
    v_event := olp_test.create_event(p_source_id, 'DAL', 'PHI', INTERVAL '3 hours');

    v_s1 := olp_test.add_snapshot(
        v_event, 'SPREAD', 'DAL', -3.0, -110, 'TESTBOOK', v_base, FALSE);

    v_s2 := olp_test.add_snapshot(
        v_event, 'SPREAD', 'DAL', -3.5, -110, 'TESTBOOK',
        v_base + INTERVAL '10 seconds', FALSE);

    RETURN QUERY SELECT v_event, v_s1, v_s2;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Convenience: a user with an open chapter and a placeable market.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.seed_ready_user(p_username TEXT)
RETURNS TABLE (user_id UUID, chapter_id UUID, event_id UUID, snapshot_id UUID)
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_user    UUID;
    v_chapter UUID;
    v_event   UUID;
    v_snap    UUID;
BEGIN
    v_user := olp_test.create_user(p_username);

    -- Open the chapter through the real RPC so the baseline is produced by
    -- production code, not by fixture shortcuts.
    PERFORM set_config('request.jwt.claim.sub', v_user::text, TRUE);
    v_chapter := public.open_chapter_rpc();
    PERFORM set_config('request.jwt.claim.sub', '', TRUE);

    v_event := olp_test.create_event('EVT-' || p_username, 'DAL', 'PHI', INTERVAL '3 hours');
    v_snap  := olp_test.add_snapshot(v_event, 'SPREAD', 'DAL', -3.0, -110, 'TESTBOOK', NOW(), FALSE);

    RETURN QUERY SELECT v_user, v_chapter, v_event, v_snap;
END;
$fn$;
