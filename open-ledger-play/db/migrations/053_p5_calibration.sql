-- =============================================================================
-- 053_p5_calibration.sql -- Package #5, increment 4: the calibration layer
-- =============================================================================
-- Equal-count bins, Wilson intervals, weighted calibration error, the per-bin
-- failure rule, and `calibration_status` kept separate from `standing`.
--
-- Still NO null model -- that is 054, and it will run through this layer with no
-- special path.
--
-- -----------------------------------------------------------------------------
-- TWO CLAIMS, DELIBERATELY NOT ONE
-- -----------------------------------------------------------------------------
--   calibration_status   do 60% predictions happen about 60% of the time?
--   standing             does this beat the market on proper scoring rules?
--
-- A model can be perfectly calibrated and add nothing. That state is
-- representable here on purpose -- `CALIBRATED` + `RESEARCH` -- because an
-- architecture that cannot express "correct but not useful" quietly pressures
-- everyone toward a more flattering conclusion. The market is the incumbent
-- model; "the market is still better" is a successful scientific result.
--
-- N = 500 unlocks EVALUATION, NOT VICTORY. A model is not required to beat the
-- market to continue existing and may remain in RESEARCH indefinitely.
--
-- -----------------------------------------------------------------------------
-- WHY EQUAL-COUNT BINS
-- -----------------------------------------------------------------------------
-- Not fixed 10%-wide buckets. Calibration behaves differently at 52% than at
-- 90%, and fixed-width buckets ignore sample size -- a bucket holding four
-- forecasts reports an "error" that is mostly noise. Equal-count binning puts
-- comparable evidence in every bin, and the Wilson interval then says how much
-- of the remaining gap is still noise.
--
-- The weighted average alone is not sufficient, which is why the per-bin rule
-- exists: a model can look fine at 3pp overall while being badly wrong in one
-- region of the probability space.
--
-- -----------------------------------------------------------------------------
-- THRESHOLDS -- what was pre-registered, and what was not
-- -----------------------------------------------------------------------------
-- Pre-registered in PACKAGE5_PREREG.md section 6 and NOT chosen here:
--     min_sample          500      eligibility
--     bin_count            10      equal-count bins
--     weighted_error_max  0.03     3 percentage points
--     bin_error_max       0.075    7.5 percentage points, any populated bin
--     wilson_z            1.96     95%
--
-- NOT pre-registered, introduced here, and FLAGGED FOR SIGN-OFF:
--     min_bin_count        30      what "adequately populated" means. With 500
--                                  observations in 10 equal-count bins each holds
--                                  ~50, so 30 admits every bin in a full sample
--                                  while excluding a stub bin in a partial one.
--     parity_epsilon    0.001      the band within which a model is called
--                                  AT_PARITY rather than better or worse. The
--                                  contract names the state but does not define
--                                  its width; a statistically defensible version
--                                  would be a bootstrap interval on the skill
--                                  score containing zero, which is more
--                                  machinery than this increment should carry.
--
-- They live in a config table rather than in function defaults so they are
-- auditable and changeable in one visible place.
-- =============================================================================

CREATE TYPE grading.calibration_status AS ENUM
    ('NONE', 'PROVISIONAL', 'CALIBRATED', 'DEGRADED');

CREATE TYPE grading.standing AS ENUM
    ('RESEARCH', 'AT_PARITY', 'ADDS_INFORMATION');

CREATE TABLE grading.calibration_config (
    id                 BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    min_sample         INT     NOT NULL DEFAULT 500,
    bin_count          INT     NOT NULL DEFAULT 10,
    weighted_error_max NUMERIC NOT NULL DEFAULT 0.0300,
    bin_error_max      NUMERIC NOT NULL DEFAULT 0.0750,
    wilson_z           NUMERIC NOT NULL DEFAULT 1.96,
    min_bin_count      INT     NOT NULL DEFAULT 30,
    parity_epsilon     NUMERIC NOT NULL DEFAULT 0.0010,
    CONSTRAINT ck_calibration_config_sane
        CHECK (min_sample > 0 AND bin_count > 1
           AND weighted_error_max > 0 AND bin_error_max >= weighted_error_max)
);

INSERT INTO grading.calibration_config (id) VALUES (TRUE);

COMMENT ON COLUMN grading.calibration_config.min_bin_count IS
    'NOT pre-registered -- introduced in 053 and awaiting sign-off. Defines '
    '"adequately populated" for the per-bin failure rule.';
COMMENT ON COLUMN grading.calibration_config.parity_epsilon IS
    'NOT pre-registered -- introduced in 053 and awaiting sign-off. The band '
    'within which a model is AT_PARITY. A defensible version would be a '
    'bootstrap interval on the skill score containing zero.';

-- -----------------------------------------------------------------------------
-- Wilson score interval on the observed frequency. Chosen over the normal
-- approximation because bins near 0 or 1 are exactly where the normal interval
-- misbehaves, and those are the bins a betting model most wants to be trusted in.
-- -----------------------------------------------------------------------------
CREATE FUNCTION grading.wilson_low(s BIGINT, n BIGINT, z NUMERIC)
RETURNS NUMERIC
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT greatest(0, (p + z*z/(2*n)) / (1 + z*z/n)
                     - (z / (1 + z*z/n)) * sqrt(p*(1-p)/n + z*z/(4*n*n)))
    FROM (SELECT s::numeric / n AS p) q;
$fn$;

CREATE FUNCTION grading.wilson_high(s BIGINT, n BIGINT, z NUMERIC)
RETURNS NUMERIC
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT least(1, (p + z*z/(2*n)) / (1 + z*z/n)
                  + (z / (1 + z*z/n)) * sqrt(p*(1-p)/n + z*z/(4*n*n)))
    FROM (SELECT s::numeric / n AS p) q;
$fn$;

-- -----------------------------------------------------------------------------
-- The sample: the most recent N SCORED grades for one model version, ordered by
-- when the world answered them. PUSH and VOID never appear -- 052 gave them NULL
-- scores and EXCLUDED_* status precisely so they cannot reach here.
-- -----------------------------------------------------------------------------
CREATE FUNCTION grading.calibration_bins(p_model_id TEXT, p_model_version TEXT)
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
-- calibration_status. Both conditions are necessary; the weighted average alone
-- can hide a systematic failure in one region.
-- -----------------------------------------------------------------------------
CREATE FUNCTION grading.calibration_report(p_model_id TEXT, p_model_version TEXT)
RETURNS TABLE (
    n_scored        BIGINT,
    eligible        BOOLEAN,
    weighted_error  NUMERIC,
    worst_bin_error NUMERIC,
    worst_bin       INT,
    status          grading.calibration_status
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    c        grading.calibration_config%ROWTYPE;
    v_n      BIGINT;
    v_werr   NUMERIC;
    v_worst  NUMERIC;
    v_wbin   INT;
BEGIN
    SELECT * INTO c FROM grading.calibration_config WHERE id;

    SELECT sum(b.n),
           round(sum(b.n * b.abs_error) / NULLIF(sum(b.n), 0), 6)
      INTO v_n, v_werr
      FROM grading.calibration_bins(p_model_id, p_model_version) b;

    SELECT b.abs_error, b.bin INTO v_worst, v_wbin
      FROM grading.calibration_bins(p_model_id, p_model_version) b
     WHERE b.adequately_populated
     ORDER BY b.abs_error DESC, b.bin
     LIMIT 1;

    v_n := COALESCE(v_n, 0);

    RETURN QUERY SELECT
        v_n,
        v_n >= c.min_sample,
        v_werr,
        v_worst,
        v_wbin,
        CASE
            WHEN v_n = 0                THEN 'NONE'::grading.calibration_status
            WHEN v_n <  c.min_sample    THEN 'PROVISIONAL'
            WHEN v_werr <= c.weighted_error_max
             AND COALESCE(v_worst, 0) <= c.bin_error_max
                                        THEN 'CALIBRATED'
            ELSE                             'DEGRADED'
        END;
END;
$fn$;

COMMENT ON FUNCTION grading.calibration_report IS
    'CALIBRATED requires BOTH a weighted absolute calibration error within the '
    'configured bound AND no adequately-populated bin outside the per-bin bound. '
    'DEGRADED here means "eligible and outside the band" -- see PACKAGE5_PREREG '
    'section 12.2 C3, which widens the contract''s narrower "was calibrated, has '
    'drifted" wording to a state that is actually computable without a history '
    'of past reports.';

-- -----------------------------------------------------------------------------
-- standing -- a SEPARATE claim from calibration.
-- -----------------------------------------------------------------------------
CREATE FUNCTION grading.standing_report(p_model_id TEXT, p_model_version TEXT)
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

    SELECT count(*), avg(g.model_brier), avg(g.market_brier),
           avg(g.model_log_loss), avg(g.market_log_loss)
      INTO v_n, v_mb, v_kb, v_ml, v_kl
      FROM grading.belief_grades g
      JOIN model.beliefs b ON b.belief_id = g.belief_id
     WHERE g.scoring_status = 'SCORED'
       AND b.model_id = p_model_id AND b.model_version = p_model_version;

    IF v_n = 0 THEN
        RETURN QUERY SELECT 0::bigint, NULL::numeric, NULL::numeric, NULL::numeric,
                            NULL::numeric, NULL::numeric, NULL::numeric,
                            'RESEARCH'::grading.standing;
        RETURN;
    END IF;

    -- Brier Skill Score against the market baseline: 1 - model/market.
    -- Positive means the model improved on the market.
    v_bss := round(1 - (v_mb / NULLIF(v_kb, 0)), 6);
    v_lli := round(v_kl - v_ml, 6);

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

COMMENT ON FUNCTION grading.standing_report IS
    'Market-relative standing, recorded SEPARATELY from calibration because they '
    'are separate claims. A model may be CALIBRATED and RESEARCH at once: well '
    'calibrated, adding nothing. N = 500 unlocks evaluation, not victory -- a '
    'model may remain in RESEARCH indefinitely and that is a legitimate resting '
    'state, not a failure to engineer around.';

-- -----------------------------------------------------------------------------
-- Privileges. Calibration is scoreboard: the model sees none of it.
-- -----------------------------------------------------------------------------
REVOKE ALL ON grading.calibration_config FROM PUBLIC;
REVOKE ALL ON FUNCTION grading.calibration_bins(TEXT, TEXT)   FROM PUBLIC;
REVOKE ALL ON FUNCTION grading.calibration_report(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION grading.standing_report(TEXT, TEXT)    FROM PUBLIC;

GRANT SELECT  ON grading.calibration_config TO olp_grader;
GRANT EXECUTE ON FUNCTION grading.calibration_bins(TEXT, TEXT)   TO olp_grader;
GRANT EXECUTE ON FUNCTION grading.calibration_report(TEXT, TEXT) TO olp_grader;
GRANT EXECUTE ON FUNCTION grading.standing_report(TEXT, TEXT)    TO olp_grader;

REVOKE ALL ON grading.calibration_config FROM olp_model;
REVOKE ALL ON FUNCTION grading.calibration_bins(TEXT, TEXT)   FROM olp_model;
REVOKE ALL ON FUNCTION grading.calibration_report(TEXT, TEXT) FROM olp_model;
REVOKE ALL ON FUNCTION grading.standing_report(TEXT, TEXT)    FROM olp_model;
