-- =============================================================================
-- 058_p5_experiment_activation.sql -- Package #5, increment 9:
-- the experiment cohort, and the activation boundary that defines it
-- =============================================================================
-- THE LAST PRE-LIVE MIGRATION. After this the two clock jobs may be written and
-- enabled, and the moment ACTIVE is stamped has a precise meaning:
--
--     everything before it is infrastructure validation
--     everything attributable to that experiment after it is evidence
--
-- -----------------------------------------------------------------------------
-- WHY A TIMESTAMP FILTER IS NOT ENOUGH
-- -----------------------------------------------------------------------------
-- The first draft of this migration defined the sample as
--
--     WHERE beliefs.formed_at >= activated_at
--
-- That is useful and it is too weak. It admits ANY belief that happens to carry
-- a late enough timestamp -- a fixture row, a manual insert, a producer calling
-- model.attempt_belief directly, a shakedown belief formed a second after
-- activation. Time membership alone cannot distinguish a prospective
-- observation from a well-timed accident.
--
-- Worse, the draft only filtered grading.calibration_bins. standing_report
-- computed Brier, the Brier Skill Score, log loss AND its own n straight from
-- grading.belief_grades keyed on model_id + model_version, so the headline
-- scoreboard and the N=500 count never saw the boundary at all.
--
-- Cohort membership therefore needs TWO independent requirements:
--
--   TIME MEMBERSHIP      formed at or after activated_at
--   LINEAGE MEMBERSHIP   produced through the pre-registered runner lifecycle:
--                        experiment -> scheduled opportunity -> formation
--                        attempt -> belief
--
-- Both are tested, and tested SEPARATELY. A pre-activation but otherwise
-- perfectly valid belief must not increment n; and -- the control that actually
-- proves the timestamp is not doing all the work -- a POST-activation belief
-- formed outside the scheduled path must not increment n either.
--
-- -----------------------------------------------------------------------------
-- THE CHAIN
-- -----------------------------------------------------------------------------
--     model.experiments          experiment_id, model_version, status,
--                                    activated_at
--          v                     (formation_schedule.experiment_id)
--     scheduled opportunity      the denominator authority (056)
--          v                     (formation_attempts.schedule_id)
--     formation attempt          terminal, exactly once (056)
--          v                     (formation_attempts.belief_id)
--     belief                     immutable (051)
--          v
--     grade                      (052)
--
-- Every link is an existing foreign key. Nothing new has to be trusted: an
-- attempt with no schedule_id has no experiment, and a belief with no attempt
-- has no lineage. model.attempt_belief (055) -- the free-form producer path --
-- creates attempts WITHOUT a schedule_id, so it can never enter a cohort.
--
-- The experiment_run link (057) is recorded on the attempt for audit, but
-- cohort membership deliberately requires the SCHEDULED OPPORTUNITY rather than
-- the cron run: a manual resolve_v01 during an outage still passes through the
-- same window gate, the same eligibility-before-model ordering and the same
-- terminal record. It is the pre-registered lifecycle, just not triggered by a
-- clock. Whether a cycle drove it is visible as via_runner.
--
-- -----------------------------------------------------------------------------
-- WHY ACTIVATION NEEDS A LIFECYCLE, NOT JUST A COLUMN
-- -----------------------------------------------------------------------------
--     DRAFT  ->  ACTIVE  ->  COLLECTION_COMPLETE  ->  EVALUATED
--
--   DRAFT -> ACTIVE     stamps activated_at exactly once
--   ACTIVE and beyond   activated_at is IMMUTABLE
--   no return to DRAFT, no backwards transition at all
--
-- A movable activated_at is a free parameter. Slide it forward and early losses
-- vanish from the scoreboard; slide it backward and historical rows are
-- admitted. Either way the sample still looks pre-registered. The transitions
-- are enforced by trigger, so the guarantee does not depend on anyone
-- remembering it.
--
-- -----------------------------------------------------------------------------
-- NO ACTIVE EXPERIMENT MEANS AN EMPTY SAMPLE, NOT AN UNFILTERED ONE
-- -----------------------------------------------------------------------------
-- The tempting shape is "filter by the experiment IF one exists". That fails
-- open: an experiment nobody remembered to activate would quietly accumulate a
-- full sample and publish a standing on it. The cohort is an inner join, so no
-- experiment means no rows.
-- =============================================================================

CREATE TYPE model.experiment_status AS ENUM
    ('DRAFT', 'ACTIVE', 'COLLECTION_COMPLETE', 'EVALUATED');

CREATE TABLE model.experiments (
    experiment_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id                TEXT NOT NULL,
    model_version           TEXT NOT NULL,
    status                  model.experiment_status NOT NULL DEFAULT 'DRAFT',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at            TIMESTAMPTZ,
    collection_completed_at TIMESTAMPTZ,
    evaluated_at            TIMESTAMPTZ,
    activated_by            TEXT,
    note                    TEXT,

    CONSTRAINT uq_experiment_per_version UNIQUE (model_id, model_version),
    -- A DRAFT has never been activated; anything past DRAFT has been, exactly
    -- once. The two facts cannot drift apart.
    CONSTRAINT ck_activated_iff_past_draft
        CHECK ((status = 'DRAFT') = (activated_at IS NULL))
);

COMMENT ON TABLE model.experiments IS
    'One experiment per model version. activated_at is stamped once on '
    'DRAFT->ACTIVE and is immutable thereafter: a movable boundary could erase '
    'early losses by sliding forward, or admit historical rows by sliding back.';

-- -----------------------------------------------------------------------------
-- The lifecycle guard. Forward-only, and the identity fields never move.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.olp_guard_experiment_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    allowed BOOLEAN;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'EXPERIMENT_IMMUTABLE: an experiment cannot be deleted (% / %)',
            OLD.model_id, OLD.model_version
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
       OR NEW.model_id      IS DISTINCT FROM OLD.model_id
       OR NEW.model_version IS DISTINCT FROM OLD.model_version
       OR NEW.created_at    IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION
            'EXPERIMENT_IMMUTABLE: identity fields cannot be changed'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- THE load-bearing rule. Once stamped, the boundary never moves.
    IF OLD.activated_at IS NOT NULL
       AND NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
        RAISE EXCEPTION
            'ACTIVATION_IMMUTABLE: % / % was activated at %. Moving that '
            'boundary forward would erase early losses from the scoreboard; '
            'moving it backward would admit historical rows.',
            OLD.model_id, OLD.model_version, OLD.activated_at
            USING ERRCODE = 'restrict_violation';
    END IF;

    allowed := (NEW.status = OLD.status)
            OR (OLD.status = 'DRAFT'               AND NEW.status = 'ACTIVE')
            OR (OLD.status = 'ACTIVE'              AND NEW.status = 'COLLECTION_COMPLETE')
            OR (OLD.status = 'COLLECTION_COMPLETE' AND NEW.status = 'EVALUATED');

    IF NOT allowed THEN
        RAISE EXCEPTION
            'ILLEGAL_TRANSITION: % -> % is not permitted. The lifecycle is '
            'forward-only: DRAFT -> ACTIVE -> COLLECTION_COMPLETE -> EVALUATED.',
            OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_experiment_transition
    BEFORE UPDATE OR DELETE ON model.experiments
    FOR EACH ROW EXECUTE FUNCTION model.olp_guard_experiment_transition();

-- -----------------------------------------------------------------------------
-- Declaring an experiment. DRAFT: opportunities may be scheduled and resolved
-- against it, and none of them count. That is exactly what the deployment
-- shakedown needs.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.create_experiment(
    p_model_id TEXT, p_model_version TEXT, p_note TEXT DEFAULT NULL)
RETURNS UUID
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
    INSERT INTO model.experiments (model_id, model_version, note)
    VALUES (p_model_id, p_model_version, p_note)
    RETURNING experiment_id;
$fn$;

-- -----------------------------------------------------------------------------
-- The one-time act. Stamps NOW(); there is deliberately NO timestamp parameter,
-- because a caller-supplied activation time is the free parameter this whole
-- migration exists to remove.
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
    e model.experiments%ROWTYPE;
BEGIN
    SELECT * INTO e FROM model.experiments
     WHERE model_id = p_model_id AND model_version = p_model_version;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'NO_SUCH_EXPERIMENT: declare % / % with model.create_experiment '
            'before activating it', p_model_id, p_model_version
            USING ERRCODE = 'no_data_found';
    END IF;

    IF e.status <> 'DRAFT' THEN
        RAISE EXCEPTION
            'ALREADY_ACTIVATED: % / % became prospective at % and is now %. An '
            'activation cannot be re-stamped -- a movable boundary could '
            'exclude a disappointing sample after the fact. A new sample needs '
            'a new model version.',
            p_model_id, p_model_version, e.activated_at, e.status
            USING ERRCODE = 'unique_violation';
    END IF;

    UPDATE model.experiments
       SET status = 'ACTIVE', activated_at = NOW(),
           activated_by = p_activated_by,
           note = COALESCE(p_note, note)
     WHERE experiment_id = e.experiment_id
    RETURNING activated_at INTO e.activated_at;

    RETURN e.activated_at;
END;
$fn$;

CREATE FUNCTION model.advance_experiment(
    p_model_id TEXT, p_model_version TEXT, p_to model.experiment_status)
RETURNS model.experiment_status
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    v model.experiment_status;
BEGIN
    UPDATE model.experiments
       SET status = p_to,
           collection_completed_at = CASE WHEN p_to = 'COLLECTION_COMPLETE'
                                          THEN NOW() ELSE collection_completed_at END,
           evaluated_at            = CASE WHEN p_to = 'EVALUATED'
                                          THEN NOW() ELSE evaluated_at END
     WHERE model_id = p_model_id AND model_version = p_model_version
    RETURNING status INTO v;

    IF v IS NULL THEN
        RAISE EXCEPTION 'NO_SUCH_EXPERIMENT: % / %', p_model_id, p_model_version
            USING ERRCODE = 'no_data_found';
    END IF;
    RETURN v;
END;
$fn$;

CREATE FUNCTION model.activated_at(p_model_id TEXT, p_model_version TEXT)
RETURNS TIMESTAMPTZ
LANGUAGE sql STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
    SELECT activated_at FROM model.experiments
     WHERE model_id = p_model_id AND model_version = p_model_version
       AND status <> 'DRAFT';
$fn$;

COMMENT ON FUNCTION model.activated_at IS
    'NULL for an experiment that has never been activated. Callers compare '
    'against it directly: NULL makes every comparison false, so the sample is '
    'EMPTY rather than unfiltered.';

-- -----------------------------------------------------------------------------
-- Lineage: an opportunity belongs to an experiment.
-- -----------------------------------------------------------------------------
ALTER TABLE model.formation_schedule
    ADD COLUMN experiment_id UUID REFERENCES model.experiments(experiment_id);

CREATE INDEX idx_schedule_experiment ON model.formation_schedule (experiment_id);

CREATE OR REPLACE FUNCTION model.schedule_v01(
    p_model_version TEXT DEFAULT '0.1.0',
    p_horizon       INTERVAL DEFAULT INTERVAL '24 hours',
    p_window_secs   INT DEFAULT 3600
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    v_added INT;
    v_exp   UUID;
BEGIN
    SELECT experiment_id INTO v_exp FROM model.experiments
     WHERE model_id = 'v01' AND model_version = p_model_version;

    IF v_exp IS NULL THEN
        RAISE EXCEPTION
            'NO_SUCH_EXPERIMENT: v01 / % has not been declared. An opportunity '
            'with no experiment could never belong to a cohort, so it must not '
            'be created.', p_model_version
            USING ERRCODE = 'no_data_found';
    END IF;

    WITH candidate AS (
        SELECT e.id AS event_id,
               e.current_scheduled_start - p_horizon AS target_at,
               s.selection_key
        FROM public.events e
        CROSS JOIN LATERAL (VALUES (e.home_team), (e.away_team)) s(selection_key)
        WHERE e.is_closed = FALSE
          AND e.is_live   = FALSE
          AND e.actual_start_time IS NULL
          AND NOW() BETWEEN e.current_scheduled_start - p_horizon
                            - make_interval(secs => p_window_secs)
                        AND e.current_scheduled_start - p_horizon
                            + make_interval(secs => p_window_secs)
    )
    INSERT INTO model.formation_schedule
        (model_id, model_version, event_id, market_type, selection_key, line,
         target_formation_at, window_seconds, experiment_id)
    SELECT 'v01', p_model_version, c.event_id, 'MONEYLINE', c.selection_key,
           NULL, c.target_at, p_window_secs, v_exp
    FROM candidate c
    ON CONFLICT ON CONSTRAINT uq_schedule_per_wager DO NOTHING;

    GET DIAGNOSTICS v_added = ROW_COUNT;
    RETURN v_added;
END;
$fn$;

-- =============================================================================
-- THE COHORT. One definition, used by every scoreboard.
-- =============================================================================
-- DELIBERATELY OWNER-PRIVILEGED (no security_invoker), unlike every other view
-- in Package #5. It is a narrow bridge, the same pattern 050 used to let the
-- model reach Package #4: olp_model needs the cohort FLAG on its 056 ledger,
-- but must not gain SELECT on model.experiments, where activated_by, note and
-- the status of every other experiment live. Making this a security_invoker
-- view and granting it would have handed out a read the grantee cannot perform
-- -- the same trap that made a 057 grant unusable.
CREATE VIEW model.experiment_cohort AS
SELECT e.experiment_id,
       e.model_id,
       e.model_version,
       e.status            AS experiment_status,
       e.activated_at,
       s.schedule_id,
       a.attempt_id,
       a.experiment_run_id,
       (a.experiment_run_id IS NOT NULL) AS via_runner,
       b.belief_id,
       b.formed_at,
       b.model_probability,
       b.market_probability_at_formation
FROM model.experiments e
JOIN model.formation_schedule s ON s.experiment_id = e.experiment_id
JOIN model.formation_attempts a ON a.schedule_id   = s.schedule_id
JOIN model.beliefs           b ON b.belief_id      = a.belief_id
WHERE e.status IN ('ACTIVE', 'COLLECTION_COMPLETE', 'EVALUATED')
  AND b.formed_at >= e.activated_at;

COMMENT ON VIEW model.experiment_cohort IS
    'The prospective evaluation sample. TIME membership (formed at or after '
    'activation) AND LINEAGE membership (experiment -> scheduled opportunity -> '
    'terminal attempt -> belief). Both are required; the timestamp alone would '
    'admit any well-timed row.';

CREATE VIEW grading.evaluation_sample
WITH (security_invoker = true) AS
SELECT c.experiment_id, c.model_id, c.model_version, c.experiment_status,
       c.belief_id, c.model_probability, c.formed_at, c.via_runner,
       g.outcome, g.model_brier, g.market_brier,
       g.model_log_loss, g.market_log_loss, g.graded_at,
       (g.outcome = 'WIN') AS won
FROM model.experiment_cohort c
JOIN grading.belief_grades g ON g.belief_id = c.belief_id
WHERE g.scoring_status = 'SCORED';

COMMENT ON VIEW grading.evaluation_sample IS
    'Every scoreboard draws from here. Aggregating on model_version alone -- as '
    'standing_report did before this migration -- lets any graded row carrying '
    'the right version string into Brier, the skill score and the N=500 count.';

-- -----------------------------------------------------------------------------
-- calibration_bins over the cohort. Body otherwise identical to 053.
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
        SELECT s.model_probability, s.won
        FROM grading.evaluation_sample s
        WHERE s.model_id = p_model_id AND s.model_version = p_model_version
        ORDER BY s.graded_at DESC
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
-- standing_report over the cohort. This is the one that mattered most: it
-- previously computed Brier, the Brier Skill Score, log loss and its own n
-- straight from belief_grades keyed on model_id + model_version, so the
-- headline scoreboard and the N=500 gate never saw the boundary at all.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION grading.standing_report(p_model_id TEXT, p_model_version TEXT)
RETURNS TABLE (
    n_scored             BIGINT,
    mean_model_brier     NUMERIC,
    mean_market_brier    NUMERIC,
    brier_skill_score    NUMERIC,
    mean_model_log_loss  NUMERIC,
    mean_market_log_loss NUMERIC,
    log_loss_improvement NUMERIC,
    standing             grading.standing
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    c      grading.calibration_config%ROWTYPE;
    v_n    BIGINT;
    v_mb   NUMERIC;
    v_kb   NUMERIC;
    v_ml   NUMERIC;
    v_kl   NUMERIC;
    v_bss  NUMERIC;
    v_lli  NUMERIC;
BEGIN
    SELECT * INTO c FROM grading.calibration_config WHERE id;

    SELECT count(*), avg(s.model_brier), avg(s.market_brier),
           avg(s.model_log_loss), avg(s.market_log_loss)
      INTO v_n, v_mb, v_kb, v_ml, v_kl
      FROM grading.evaluation_sample s
     WHERE s.model_id = p_model_id AND s.model_version = p_model_version;

    IF v_n = 0 THEN
        RETURN QUERY SELECT 0::bigint, NULL::numeric, NULL::numeric, NULL::numeric,
                            NULL::numeric, NULL::numeric, NULL::numeric,
                            'RESEARCH'::grading.standing;
        RETURN;
    END IF;

    v_bss := round(1 - (v_mb / NULLIF(v_kb, 0)), 6);
    v_lli := round(v_kl - v_ml, 6);

    -- Verdict logic reproduced EXACTLY from 053. This migration changes the
    -- POPULATION the scoreboard is computed over, and nothing else. An earlier
    -- draft of this function quietly added a calibration gate here; that would
    -- have been a semantic change smuggled in under a contamination fix.
    RETURN QUERY SELECT
        v_n, round(v_mb,8), round(v_kb,8), v_bss, round(v_ml,8), round(v_kl,8), v_lli,
        CASE
            -- N unlocks EVALUATION, not victory. Below the bar, no claim is made.
            WHEN v_n < c.min_sample                        THEN 'RESEARCH'::grading.standing
            WHEN abs(v_bss) <= c.parity_epsilon
             AND abs(v_lli) <= c.parity_epsilon            THEN 'AT_PARITY'
            WHEN v_bss >= 0 AND v_lli >= 0                 THEN 'ADDS_INFORMATION'
            ELSE                                                'RESEARCH'
        END;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- The v0.1 ledger gains the cohort flag, so an operator can see which
-- opportunities count without reconstructing the join.
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
       s.experiment_id,
       EXISTS (SELECT 1 FROM model.experiment_cohort c
                WHERE c.belief_id = a.belief_id) AS in_prereg_sample
FROM model.formation_schedule s
LEFT JOIN model.formation_attempts a ON a.schedule_id = s.schedule_id;

-- -----------------------------------------------------------------------------
-- Privileges.
--
-- olp_model keeps the v01_ledger read 056 granted it, so it must be able to
-- evaluate the cohort flag -- hence SELECT on experiment_cohort. It already
-- sees every attempt, reason and timestamp in that ledger, and v0.1 is a
-- memoryless function of the market with no self-performance access
-- (Decision 7), so the flag tells it nothing it could act on. What it does NOT
-- get is the experiments table itself, or any ability to declare, activate or
-- advance one.
-- -----------------------------------------------------------------------------
REVOKE ALL ON model.experiments FROM PUBLIC;
REVOKE ALL ON FUNCTION model.create_experiment(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.activate_experiment(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.advance_experiment(TEXT, TEXT, model.experiment_status) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.activated_at(TEXT, TEXT) FROM PUBLIC;

GRANT SELECT  ON model.experiment_cohort   TO olp_model, olp_grader;
GRANT SELECT  ON model.experiments         TO olp_grader;
GRANT SELECT  ON grading.evaluation_sample TO olp_grader;
GRANT EXECUTE ON FUNCTION model.activated_at(TEXT, TEXT) TO olp_grader;
