-- =============================================================================
-- 058_p5_experiment_activation.sql -- Package #5, increment 9:
-- the activation timestamp, and the sample boundary it defines
-- =============================================================================
-- "Anything formed after that activation timestamp belongs to the preregistered
-- sample; anything before it does not."
--
-- That sentence is only worth writing down if the system enforces it. Recorded
-- in a markdown file it is a promise; recorded here it is a boundary that the
-- scoreboards cannot be computed without.
--
-- -----------------------------------------------------------------------------
-- WHY THIS IS NOT JUST A NOTE IN THE PRE-REGISTRATION
-- -----------------------------------------------------------------------------
-- Before this migration, grading.calibration_bins drew from EVERY scored belief
-- for a model version. The deployment sequence deliberately invokes both hosted
-- endpoints once before enabling cron -- so the very first thing that would have
-- happened is a shakedown belief entering the pre-registered sample. Nobody
-- would have noticed, because nothing distinguished it.
--
-- The sample is now defined, and the reporting path filters on it.
--
-- -----------------------------------------------------------------------------
-- NO ACTIVATION MEANS AN EMPTY SAMPLE, NOT AN UNFILTERED ONE
-- -----------------------------------------------------------------------------
-- The tempting shape is "filter by activation IF an activation row exists".
-- That fails open: an experiment nobody remembered to activate would quietly
-- accumulate a full sample and report a standing on it. So the default is the
-- honest one --
--
--     no activation row  ->  the sample is EMPTY  ->  the experiment has not
--                            started, and the scoreboards say so
--
-- Activation is a deliberate one-time act, and it is the moment the experiment
-- becomes prospective.
--
-- -----------------------------------------------------------------------------
-- WHY ACTIVATION IS APPEND-ONLY AND CANNOT BE RE-STAMPED
-- -----------------------------------------------------------------------------
-- A movable activation timestamp is a free parameter: a disappointing first
-- month could be excluded by sliding it forward, and the sample would still look
-- pre-registered. One row per (model_id, model_version), written once, never
-- edited. A second attempt raises rather than updating.
--
-- A NEW model version starts a NEW sample from zero -- which is exactly what
-- MODEL_V0_1_PREREG.md section 4.1 already requires of any change to k.
-- =============================================================================

CREATE TABLE model.experiment_activation (
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    activated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_by  TEXT NOT NULL,
    note          TEXT,
    PRIMARY KEY (model_id, model_version)
);

COMMENT ON TABLE model.experiment_activation IS
    'The moment an experiment became prospective. One row per model version, '
    'written once, never edited -- a movable activation timestamp would be a '
    'free parameter that could exclude a disappointing month after the fact.';

CREATE TRIGGER trg_experiment_activation_append_only
    BEFORE UPDATE OR DELETE ON model.experiment_activation
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- The one-time act. Stamps NOW(); there is deliberately no timestamp parameter,
-- because a caller-supplied activation time is the free parameter this table
-- exists to remove. (Tests construct historical activations through an
-- explicitly-named olp_test fixture instead -- see 019/036 for the precedent.)
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.activate_experiment(
    p_model_id      TEXT,
    p_model_version TEXT,
    p_activated_by  TEXT,
    p_note          TEXT DEFAULT NULL
)
RETURNS TIMESTAMPTZ
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    v_at TIMESTAMPTZ;
BEGIN
    SELECT activated_at INTO v_at FROM model.experiment_activation
     WHERE model_id = p_model_id AND model_version = p_model_version;

    IF FOUND THEN
        RAISE EXCEPTION
            'ALREADY_ACTIVATED: %/% became prospective at %. An activation '
            'timestamp cannot be re-stamped -- a movable boundary could exclude '
            'a disappointing sample after the fact. A new sample needs a new '
            'model version.', p_model_id, p_model_version, v_at
            USING ERRCODE = 'unique_violation';
    END IF;

    INSERT INTO model.experiment_activation
        (model_id, model_version, activated_by, note)
    VALUES (p_model_id, p_model_version, p_activated_by, p_note)
    RETURNING activated_at INTO v_at;

    RETURN v_at;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- The sample predicate, in one place so every consumer agrees on it.
-- Returns NULL when the experiment has not been activated, and every
-- comparison against NULL is false -- which is precisely the fail-closed
-- behaviour wanted: no activation, no sample.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.activated_at(p_model_id TEXT, p_model_version TEXT)
RETURNS TIMESTAMPTZ
LANGUAGE sql STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
    SELECT activated_at FROM model.experiment_activation
     WHERE model_id = p_model_id AND model_version = p_model_version;
$fn$;

COMMENT ON FUNCTION model.activated_at IS
    'NULL when the experiment has never been activated. Callers compare against '
    'it directly: NULL makes every comparison false, so an unactivated '
    'experiment has an EMPTY sample rather than an unfiltered one.';

-- -----------------------------------------------------------------------------
-- The sample itself, as a view, so "what is in the pre-registered sample" is a
-- question with exactly one answer.
-- -----------------------------------------------------------------------------
CREATE VIEW model.prereg_sample
WITH (security_invoker = true) AS
SELECT b.*,
       a.activated_at,
       a.activated_by
FROM model.beliefs b
JOIN model.experiment_activation a
  ON a.model_id = b.model_id AND a.model_version = b.model_version
WHERE b.formed_at >= a.activated_at;

COMMENT ON VIEW model.prereg_sample IS
    'Beliefs belonging to a pre-registered sample: formed at or after their own '
    'model version became prospective. An unactivated model contributes nothing.';

-- -----------------------------------------------------------------------------
-- Calibration now measures the pre-registered sample, not every belief that
-- happens to be scored. Body identical to 053 apart from the sample join --
-- reproduced rather than wrapped so there is one readable definition of what
-- gets binned.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION grading.calibration_bins(p_model_id TEXT, p_model_version TEXT)
RETURNS TABLE (
    bin                  INT,
    n                    BIGINT,
    mean_predicted       NUMERIC,
    observed_frequency   NUMERIC,
    wilson_low           NUMERIC,
    wilson_high          NUMERIC,
    abs_error            NUMERIC,
    adequately_populated BOOLEAN
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    c grading.calibration_config%ROWTYPE;
BEGIN
    SELECT * INTO c FROM grading.calibration_config WHERE id;

    RETURN QUERY
    WITH sample AS (
        SELECT b.model_probability, (g.outcome = 'WIN') AS won
        FROM grading.belief_grades g
        JOIN model.beliefs b ON b.belief_id = g.belief_id
        JOIN grading.wager_outcomes o
          ON o.event_id      = b.event_id
         AND o.market_type   = b.market_type
         AND o.selection_key = b.selection_key
         AND o.line IS NOT DISTINCT FROM b.line
        WHERE g.scoring_status = 'SCORED'
          AND b.model_id      = p_model_id
          AND b.model_version = p_model_version
          -- THE SAMPLE BOUNDARY. NULL when unactivated, so the comparison is
          -- false and the sample is empty rather than unfiltered.
          AND b.formed_at >= model.activated_at(p_model_id, p_model_version)
        ORDER BY o.resolved_at DESC, g.graded_at DESC
        LIMIT c.min_sample
    ),
    binned AS (
        SELECT s.*, ntile(c.bin_count) OVER (ORDER BY s.model_probability) AS bin_no
        FROM sample s
    )
    SELECT bin_no::int,
           count(*),
           round(avg(model_probability), 6),
           round((count(*) FILTER (WHERE won))::numeric / count(*), 6),
           round(grading.wilson_low (count(*) FILTER (WHERE won), count(*), c.wilson_z), 6),
           round(grading.wilson_high(count(*) FILTER (WHERE won), count(*), c.wilson_z), 6),
           round(abs(avg(model_probability)
                   - (count(*) FILTER (WHERE won))::numeric / count(*)), 6),
           count(*) >= c.min_bin_count
    FROM binned
    GROUP BY bin_no
    ORDER BY bin_no;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- The v0.1 ledger gains the boundary too, so an operator can see at a glance
-- which opportunities count. CREATE OR REPLACE only appends columns.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW model.v01_ledger
WITH (security_invoker = true) AS
SELECT s.schedule_id, s.model_id, s.model_version, s.event_id,
       s.market_type, s.selection_key, s.line,
       s.target_formation_at, s.window_seconds,
       a.attempt_id, a.reason, a.belief_id,
       a.selected_observation_at, a.seconds_from_target, a.seconds_to_kickoff,
       (a.attempt_id IS NULL)        AS unresolved,
       (a.belief_id  IS NOT NULL)    AS belief_formed,
       (a.attempted_at >= model.activated_at(s.model_id, s.model_version))
                                     AS in_prereg_sample
FROM model.formation_schedule s
LEFT JOIN model.formation_attempts a ON a.schedule_id = s.schedule_id;

-- -----------------------------------------------------------------------------
-- Test fixture. Named so it cannot be mistaken for the production path: it
-- constructs a HISTORICAL activation, which model.activate_experiment refuses
-- to do by design.
-- -----------------------------------------------------------------------------
CREATE FUNCTION olp_test.activate_experiment_at(
    p_model_id      TEXT,
    p_model_version TEXT,
    p_at            TIMESTAMPTZ
)
RETURNS TIMESTAMPTZ
LANGUAGE sql
AS $fn$
    INSERT INTO model.experiment_activation
        (model_id, model_version, activated_at, activated_by, note)
    VALUES (p_model_id, p_model_version, p_at, 'olp_test',
            'FIXTURE: historical activation, not a production activation')
    RETURNING activated_at;
$fn$;

COMMENT ON FUNCTION olp_test.activate_experiment_at IS
    'FIXTURE ONLY. Back-dates an activation so a test can construct a sample. '
    'model.activate_experiment deliberately has no timestamp parameter.';

-- -----------------------------------------------------------------------------
-- Test hygiene. Like experiment_runs in 057, this table has no foreign-key path
-- back to public.events, so TRUNCATE ... CASCADE does not reach it. Without
-- this an activation would leak between tests -- and an activation leaking into
-- a test that never declared one is precisely the contamination this migration
-- exists to prevent.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.reset()
RETURNS VOID
LANGUAGE plpgsql
AS $fn$
BEGIN
    TRUNCATE
        model.experiment_activation,
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

-- -----------------------------------------------------------------------------
-- Privileges.
--
-- olp_model keeps the v01_ledger read 056 granted it, so it must be able to
-- evaluate the new column -- hence EXECUTE on activated_at. That is a
-- deliberate, narrow allowance rather than an oversight: the model already sees
-- every attempt, reason and timestamp in that ledger, so the activation instant
-- tells it nothing it could not already bound, and v0.1 is a memoryless
-- function of the market with no self-performance access (Decision 7) that
-- could act on it. What it does NOT get is the activation row itself --
-- activated_by and note stay with the grader.
--
-- Withholding EXECUTE while adding the column would not protect anything; it
-- would only break a working grant and make v01_ledger unreadable to the role
-- 056 deliberately gave it to.
-- -----------------------------------------------------------------------------
REVOKE ALL ON model.experiment_activation FROM PUBLIC;
REVOKE ALL ON FUNCTION model.activate_experiment(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.activated_at(TEXT, TEXT) FROM PUBLIC;

GRANT SELECT  ON model.experiment_activation TO olp_grader;
GRANT SELECT  ON model.prereg_sample         TO olp_grader;
GRANT EXECUTE ON FUNCTION model.activated_at(TEXT, TEXT) TO olp_grader, olp_model;
