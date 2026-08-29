-- =============================================================================
-- 05_p5_helpers.sql -- TEST HARNESS ONLY. NEVER INSTALLED IN PRODUCTION.
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
-- 059_p5_fixtures.sql -- Package #5 test fixtures
-- =============================================================================
-- FIXTURE MIGRATION. Never applied to a production database.
--
-- scripts/migrate.py skips every file whose name contains "fixtures", which is
-- why these objects live here rather than in 057 and 058 where they were first
-- written. Two things were wrong with that:
--
--   1. 057 did CREATE OR REPLACE FUNCTION olp_test.reset() unconditionally, so
--      a production install skipping fixture migrations FAILED outright --
--      olp_test only exists via migration 019.
--
--   2. The only way to make it succeed was to ship the fixtures, which would
--      install olp_test.reset() -- a function that TRUNCATEs every table in the
--      database -- into production. A loaded gun behind a thin grant.
--
-- The rule this encodes: a migration that creates test scaffolding belongs in a
-- file named for what it is, so the deployer can tell the two apart WITHOUT a
-- hand-maintained skip list that will eventually drift.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Test fixtures. Named so they cannot be mistaken for the production path.
-- -----------------------------------------------------------------------------
CREATE FUNCTION olp_test.activate_experiment_at(
    p_model_id TEXT, p_model_version TEXT, p_at TIMESTAMPTZ)
RETURNS UUID
LANGUAGE sql
AS $fn$
    INSERT INTO model.experiments
        (model_id, model_version, status, activated_at, activated_by, note)
    VALUES (p_model_id, p_model_version, 'ACTIVE', p_at, 'olp_test',
            'FIXTURE: historical activation, not a production activation')
    RETURNING experiment_id;
$fn$;

COMMENT ON FUNCTION olp_test.activate_experiment_at IS
    'FIXTURE ONLY. Back-dates an activation so a test can construct a cohort. '
    'model.activate_experiment deliberately has no timestamp parameter.';

-- Give an already-formed belief the lineage a cohort requires: a scheduled
-- opportunity under the experiment, and a terminal attempt naming the belief.
-- A test that wants a belief OUTSIDE the cohort simply does not call this.
CREATE FUNCTION olp_test.enroll_belief(p_belief_id UUID)
RETURNS UUID
LANGUAGE plpgsql
AS $fn$
DECLARE
    b   model.beliefs%ROWTYPE;
    v_e UUID;
    v_s UUID;
BEGIN
    SELECT * INTO b FROM model.beliefs WHERE belief_id = p_belief_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ENROLL_UNKNOWN_BELIEF: %', p_belief_id;
    END IF;

    SELECT experiment_id INTO v_e FROM model.experiments
     WHERE model_id = b.model_id AND model_version = b.model_version;
    IF v_e IS NULL THEN
        RAISE EXCEPTION
            'ENROLL_NO_EXPERIMENT: % / % has not been declared',
            b.model_id, b.model_version;
    END IF;

    INSERT INTO model.formation_schedule
        (model_id, model_version, event_id, market_type, selection_key, line,
         target_formation_at, window_seconds, experiment_id)
    VALUES (b.model_id, b.model_version, b.event_id, b.market_type,
            b.selection_key, b.line, b.formed_at, 3600, v_e)
    RETURNING schedule_id INTO v_s;

    INSERT INTO model.formation_attempts
        (model_id, model_version, event_id, market_type, selection_key, line,
         reason, belief_id, schedule_id, target_formation_at,
         selected_observation_at, seconds_from_target, seconds_to_kickoff)
    VALUES (b.model_id, b.model_version, b.event_id, b.market_type,
            b.selection_key, b.line, 'ELIGIBLE', p_belief_id, v_s, b.formed_at,
            b.formed_at, 0, 86400);

    RETURN v_s;
END;
$fn$;

COMMENT ON FUNCTION olp_test.enroll_belief IS
    'FIXTURE ONLY. Builds the experiment -> schedule -> attempt -> belief '
    'lineage that cohort membership requires. A test proving a belief is '
    'EXCLUDED simply omits this call.';

-- -----------------------------------------------------------------------------
-- Test hygiene. Like experiment_runs in 057, model.experiments has no
-- foreign-key path back to public.events, so TRUNCATE ... CASCADE does not
-- reach it -- and an experiment leaking between tests is precisely the
-- contamination this migration exists to prevent.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.reset()
RETURNS VOID
LANGUAGE plpgsql
AS $fn$
BEGIN
    TRUNCATE
        model.experiments,
        model.experiment_runs,
        public.provider_health,
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
