-- =============================================================================
-- 050_p5_trust_boundary.sql -- Package #5, increment 1: the trust boundary only
-- =============================================================================
-- No belief tables. No grader tables. No null model. This migration exists to
-- establish one thing and prove it:
--
--     Package #5 cannot bypass Package #4, and cannot see its own scoreboard.
--
-- Every later statistical claim in Package #5 rests on this being true, so it is
-- built and proven first, alone.
--
-- -----------------------------------------------------------------------------
-- THE OBSTACLE, and why there are two schemas
-- -----------------------------------------------------------------------------
-- Package #4's views are all `security_invoker = true`, deliberately, so that
-- RLS is inherited by the caller. That is right for user-facing consumers -- and
-- it directly defeats the boundary we want here. Under security_invoker,
-- granting SELECT on `market_intelligence` is not enough: PostgreSQL checks the
-- CALLER's privileges on the underlying `market_snapshots`, `events` and
-- `system_settings`. Granting those would hand the model exactly the raw quotes
-- the contract forbids it from reading.
--
-- A plain owner-privileged view over it does NOT solve this, and finding that
-- out was worth the attempt: security_invoker checks the ORIGINAL invoker, not
-- the owner of an intermediate view. Layering `model_input.market_intelligence`
-- (definer) over `public.market_intelligence` (invoker) still landed the check
-- on olp_model and was correctly refused --
--
--     permission denied for view canonical_market
--
-- The boundary held; the bridge did not. What actually changes current_user is
-- a SECURITY DEFINER FUNCTION, the same mechanism Package #1 uses for its RPCs.
-- Inside it the effective user is the owner, so Package #4's invoker views
-- resolve against the owner's privileges and the model never needs a grant on
-- anything behind them. This is safe because market data is global -- there are
-- no per-user rows for RLS to protect.
--
-- Cost, recorded honestly: a SECURITY DEFINER function is never inlined, so a
-- predicate on the view cannot be pushed into it. Package #4's MATERIALIZED
-- CTEs already prevent per-event pushdown (a single-event query costs about
-- what the whole board costs), so this does not make anything categorically
-- worse -- but it is a real constraint on any later per-event access pattern.
--
-- Two schemas, and the split is load-bearing:
--
--     model_input   what the model is allowed to SEE   (read-through, this file)
--     model         what the model PRODUCES            (empty until increment 2)
--
-- The pre-registered rule that no `model.` column may share a name with a
-- `market_intelligence` column is about model OUTPUT being mistaken for market
-- truth. `model_input` is market truth, passed through unchanged, so its columns
-- are deliberately IDENTICAL. Keeping outputs in a different schema from inputs
-- is what lets both rules hold at once.
--
-- -----------------------------------------------------------------------------
-- WHAT THE MODEL MAY SEE
-- -----------------------------------------------------------------------------
--   model_input.market_intelligence     the Package #4 consumer contract
--   model_input.events                  schedule identity and state; this table
--                                       carries NO result columns -- there are no
--                                       scores in it to leak
--   model_input.event_schedule_history  postponement context
--
-- WHAT IT MAY NOT SEE, and why each one matters
--
--   market_snapshots      raw quotes -- the whole point of Package #4
--   canonical_market      a second definition of observed market reality
--   executable_market     "
--   market_movement       "
--   current_market_board  Package #2's own market view -- same hazard
--   system_settings       Package #4 internals; the config is already applied
--
--   ticket_results             settled outcomes
--   ticket_result_adjustments  "
--   ticket_effective_results   "
--   ticket_closing_line_value  CLV history
--   tickets                    what was played, and at what price
--   wallet_transactions        realised P/L
--   ledger_chapters            "
--   chapter_balances           "
--   risk_reservations          exposure
--
--   ingestion_runs, provider_health, market_feed_health,
--   event_lifecycle_log, users      operational and identity surface
--
-- The grading system can see the model. The model cannot see the grading
-- system. A model reading its own history opens a feedback channel that
-- survives good intentions: features and prompts begin adapting to past
-- performance, and the prospective test stops being clean while continuing to
-- look clean.
--
-- These denials are PostgreSQL permission failures, not application filtering,
-- and P5-T01/P5-T02 prove that by ATTEMPTING each read as the actual role and
-- requiring the database to refuse. Inspecting grants would only test our
-- belief about the grants.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- The role. Cluster-wide, so it survives the schema drops the test harness
-- performs between runs and must be created idempotently.
--
-- NOINHERIT: privileges must be taken deliberately via SET ROLE, never picked up
-- by accident through some future membership.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'olp_model') THEN
        CREATE ROLE olp_model NOLOGIN NOINHERIT;
    END IF;
END
$$;

-- Start from nothing, every run. The role persists across schema rebuilds, so
-- yesterday's grants must not survive into today's boundary.
DO $$
BEGIN
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM olp_model';
    EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM olp_model';
    EXECUTE 'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM olp_model';
    EXECUTE 'REVOKE ALL ON SCHEMA public FROM olp_model';
END
$$;

-- So the test harness, which connects as the owner, can SET ROLE olp_model.
-- The owner is not necessarily a superuser -- on Supabase it is not.
DO $$
BEGIN
    EXECUTE format('GRANT olp_model TO %I', current_user);
EXCEPTION WHEN OTHERS THEN
    NULL;   -- already a member, or the grant is unnecessary for a superuser
END
$$;

-- -----------------------------------------------------------------------------
-- Schemas
-- -----------------------------------------------------------------------------
CREATE SCHEMA model_input;
CREATE SCHEMA model;

COMMENT ON SCHEMA model_input IS
    'What the Package #5 model layer is permitted to SEE. Read-through views '
    'that run with the owner''s privileges, so the model reaches Package #4''s '
    'answers without any grant on the raw market tables behind them. Columns '
    'are deliberately identical to the public views -- this is market truth '
    'passed through unchanged, not model output.';

COMMENT ON SCHEMA model IS
    'What the Package #5 model layer PRODUCES. Empty until increment 2. No '
    'column here may share a name with a market_intelligence column, so model '
    'output can never be read as market truth.';

REVOKE ALL ON SCHEMA model_input FROM PUBLIC;
REVOKE ALL ON SCHEMA model       FROM PUBLIC;

-- -----------------------------------------------------------------------------
-- The permitted surface. NOT security_invoker -- see the header.
-- -----------------------------------------------------------------------------
-- market_intelligence is reached through a SECURITY DEFINER function, because
-- it is a security_invoker view and no amount of view nesting changes that.
CREATE FUNCTION model_input.read_market_intelligence()
RETURNS SETOF public.market_intelligence
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
    SELECT * FROM public.market_intelligence;
$fn$;

REVOKE ALL ON FUNCTION model_input.read_market_intelligence() FROM PUBLIC;

CREATE VIEW model_input.market_intelligence
WITH (security_invoker = false) AS
SELECT * FROM model_input.read_market_intelligence();

-- Explicit column list rather than SELECT *: if a future migration adds a
-- result column to events, this view must not start leaking it silently.
CREATE VIEW model_input.events
WITH (security_invoker = false) AS
SELECT id, source_event_id, sport, league, home_team, away_team,
       original_scheduled_start, current_scheduled_start, actual_start_time,
       is_live, is_closed, created_at
FROM public.events;

CREATE VIEW model_input.event_schedule_history
WITH (security_invoker = false) AS
SELECT * FROM public.event_schedule_history;

COMMENT ON VIEW model_input.events IS
    'Schedule identity and state only. public.events carries no score or result '
    'column; the column list here is explicit so that adding one later cannot '
    'leak it to the model without somebody editing this view on purpose.';

-- -----------------------------------------------------------------------------
-- Minimum access. USAGE on model_input and model, SELECT on three views.
-- Deliberately no USAGE on schema public: the model has no business reaching
-- into it at all, and the read-through does not require it.
-- -----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA model_input TO olp_model;
GRANT USAGE ON SCHEMA model       TO olp_model;

GRANT EXECUTE ON FUNCTION model_input.read_market_intelligence() TO olp_model;
GRANT SELECT ON model_input.market_intelligence    TO olp_model;
GRANT SELECT ON model_input.events                 TO olp_model;
GRANT SELECT ON model_input.event_schedule_history TO olp_model;

-- Read-only, permanently. Package #5 writes nothing to Packages #1-#4, and
-- nothing to its own input surface either.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA model_input FROM olp_model;
