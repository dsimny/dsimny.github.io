-- =============================================================================
-- 056_p5_v01_formation_lifecycle.sql -- Package #5, increment 7:
-- the experiment schedule becomes the denominator authority
-- =============================================================================
-- THE ARCHITECTURAL INVERSION
--
--   The producer no longer decides when it wants to attempt a prediction.
--   The experiment schedule decides that a prediction opportunity EXISTS.
--
-- 055 made every attempt leave a trace, but a producer could still compute a
-- probability, look at it, and simply never call -- silent abstention, leaving
-- no row at all. The denominator was complete only by good behaviour.
--
-- Here the opportunity is created BEFORE the model runs and independently of it,
-- and every scheduled opportunity terminates in exactly one recorded outcome:
--
--     scheduled formation
--          v
--     window observation resolution
--          v
--     eligibility evaluation
--          v
--     ELIGIBLE -> invoke model -> form immutable belief
--          or
--     INELIGIBLE / NO_WINDOW_CAPTURE -> record terminal attempt status
--
--     scheduled attempts = formed beliefs + ineligible attempts
--
-- The model is invoked only AFTER eligibility is decided. It never sees its own
-- probability and then gets to decide whether the opportunity qualified; if it
-- could, the ledger would stop being a denominator and become a selection
-- mechanism -- the failure 055 exists to prevent, reintroduced one layer up.
--
-- -----------------------------------------------------------------------------
-- NO_WINDOW_CAPTURE IS NOT A MARKET REASON
-- -----------------------------------------------------------------------------
-- A market may have been perfectly executable at T-24h and the ingestion system
-- simply failed to observe it inside the window. That is a DATA-COLLECTION
-- failure, not a market failure. Conflating the two would corrupt any later
-- analysis of missingness, which is the most likely place for a quiet bias to
-- hide.
--
-- -----------------------------------------------------------------------------
-- TWO CLOCKS, BOTH RECORDED, BECAUSE THEY ANSWER DIFFERENT QUESTIONS
-- -----------------------------------------------------------------------------
--   seconds_from_target   how close the selected observation was to T-24h
--   seconds_to_kickoff    how far the event actually was from starting
--
-- They diverge when a game is rescheduled: the first says what the system
-- believed the formation horizon was, the second says what it turned out to be.
-- Keeping only one would make a drifting horizon invisible -- and a horizon that
-- cannot be audited is not a horizon.
--
-- -----------------------------------------------------------------------------
-- LIVE RESOLUTION, AND THE TENSION IT RESOLVES
-- -----------------------------------------------------------------------------
-- "Choose the observation closest to exactly T-24h" implies selecting among
-- candidates after the window closes, which needs the market state as it stood
-- at each candidate moment -- the point-in-time reconstruction the experiment
-- pre-registration rules out.
--
-- So resolution happens LIVE, inside the window: the first run that finds an
-- eligible observation forms the belief from the surface as it stands, and
-- seconds_from_target records how close to T-24h it got. At current polling
-- density a +/-60m window will usually hold at most one observation, so
-- "closest to target" and "first inside the window" coincide. They stop
-- coinciding if polling becomes dense, and that must be revisited BEFORE it
-- does. Recorded in MODEL_V0_1_PREREG.md section 9.3.1 so it cannot later be
-- discovered as a convenient reinterpretation.
-- =============================================================================

ALTER TYPE model.eligibility_reason ADD VALUE IF NOT EXISTS 'NO_WINDOW_CAPTURE';

-- -----------------------------------------------------------------------------
-- Model v0.1, in full. logit(p_v01) = k * logit(p_market), which is exactly
-- p^k / (p^k + (1-p)^k). k = 1.10, NON-FITTED, testing direction not magnitude.
-- At k = 1 this is the identity, so the null model is nested inside it.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.v01_probability(p NUMERIC, k NUMERIC DEFAULT 1.10)
RETURNS NUMERIC
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT round(power(p, k) / (power(p, k) + power(1 - p, k)), 6);
$fn$;

COMMENT ON FUNCTION model.v01_probability IS
    'Model v0.1: a monotone sharpening of the market probability, k = 1.10, '
    'fixed before the first belief and never fitted. p = 0.5 is a fixed point at '
    'any k, so pick''em markets contribute nothing to the test. k = 1 reproduces '
    'the market exactly.';

-- -----------------------------------------------------------------------------
-- The schedule. Created before the model runs, and the authority on what the
-- denominator contains.
-- -----------------------------------------------------------------------------
CREATE TABLE model.formation_schedule (
    schedule_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id            TEXT NOT NULL,
    model_version       TEXT NOT NULL,
    event_id            UUID NOT NULL REFERENCES public.events(id),
    market_type         public.market_type NOT NULL,
    selection_key       TEXT NOT NULL,
    line                NUMERIC(7,2),
    target_formation_at TIMESTAMPTZ NOT NULL,
    window_seconds      INT NOT NULL,
    scheduled_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- NULLS NOT DISTINCT is load-bearing, not decoration. `line` is NULL for
    -- MONEYLINE -- the first market v0.1 runs on -- and a plain UNIQUE treats
    -- two NULLs as different, so it would have let the scheduler create a
    -- duplicate opportunity for every single wager in the experiment while
    -- looking like it prevented them. Same NULL trap that ruled out a composite
    -- foreign key in 051.
    CONSTRAINT uq_schedule_per_wager
        UNIQUE NULLS NOT DISTINCT
            (model_id, model_version, event_id, market_type, selection_key, line),
    CONSTRAINT ck_window_positive CHECK (window_seconds > 0)
);

COMMENT ON TABLE model.formation_schedule IS
    'The denominator authority. One scheduled prediction opportunity per wager '
    'per model version, created BEFORE the model runs and independently of it. '
    'Append-only: a schedule that can be edited after the fact is not a '
    'denominator.';

CREATE TRIGGER trg_formation_schedule_append_only
    BEFORE UPDATE OR DELETE ON model.formation_schedule
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- Resolution stamps on the attempt record. One attempt per schedule row, so a
-- scheduled opportunity terminates exactly once.
-- -----------------------------------------------------------------------------
ALTER TABLE model.formation_attempts
    ADD COLUMN schedule_id            UUID REFERENCES model.formation_schedule(schedule_id),
    ADD COLUMN target_formation_at    TIMESTAMPTZ,
    ADD COLUMN selected_observation_at TIMESTAMPTZ,
    ADD COLUMN seconds_from_target    INT,
    ADD COLUMN seconds_to_kickoff     INT;

CREATE UNIQUE INDEX uq_attempt_per_schedule
    ON model.formation_attempts (schedule_id) WHERE schedule_id IS NOT NULL;

COMMENT ON COLUMN model.formation_attempts.seconds_from_target IS
    'How close the selected observation was to the target horizon. Distinct from '
    'seconds_to_kickoff: if a game is rescheduled these diverge, and only both '
    'together show whether the horizon held.';

-- -----------------------------------------------------------------------------
-- Scheduling. Enumerates the opportunities whose target horizon is reachable
-- now, and creates them once.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.schedule_v01(
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
BEGIN
    WITH candidate AS (
        SELECT e.id AS event_id,
               e.current_scheduled_start - p_horizon AS target_at,
               s.selection_key
        FROM public.events e
        CROSS JOIN LATERAL (VALUES (e.home_team), (e.away_team)) s(selection_key)
        WHERE e.is_closed = FALSE
          AND e.is_live   = FALSE
          AND e.actual_start_time IS NULL
          -- the target horizon is inside the window right now
          AND NOW() BETWEEN e.current_scheduled_start - p_horizon
                            - make_interval(secs => p_window_secs)
                        AND e.current_scheduled_start - p_horizon
                            + make_interval(secs => p_window_secs)
    )
    INSERT INTO model.formation_schedule
        (model_id, model_version, event_id, market_type, selection_key, line,
         target_formation_at, window_seconds)
    SELECT 'v01', p_model_version, c.event_id, 'MONEYLINE', c.selection_key,
           NULL, c.target_at, p_window_secs
    FROM candidate c
    ON CONFLICT ON CONSTRAINT uq_schedule_per_wager DO NOTHING;

    GET DIAGNOSTICS v_added = ROW_COUNT;
    RETURN v_added;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Resolution. Eligibility is decided BEFORE the model is invoked.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.resolve_v01(p_schedule_id UUID)
RETURNS model.eligibility_reason
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    sc        model.formation_schedule%ROWTYPE;
    v_reason  model.eligibility_reason;
    v_market  NUMERIC;
    v_hash    TEXT;
    v_quality TEXT;
    v_books   INT;
    v_obs_at  TIMESTAMPTZ;
    v_kick    TIMESTAMPTZ;
    v_belief  UUID;
BEGIN
    SELECT * INTO sc FROM model.formation_schedule WHERE schedule_id = p_schedule_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'NO_SUCH_SCHEDULE: %', p_schedule_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF EXISTS (SELECT 1 FROM model.formation_attempts
                WHERE schedule_id = p_schedule_id) THEN
        RAISE EXCEPTION
            'ALREADY_RESOLVED: schedule % has already terminated', p_schedule_id
            USING ERRCODE = 'unique_violation';
    END IF;

    SELECT current_scheduled_start INTO v_kick
      FROM public.events WHERE id = sc.event_id;

    -- 1. Are we inside the pre-registered capture window? A data-collection
    --    question, answered before any market question is asked.
    IF abs(extract(epoch FROM NOW() - sc.target_formation_at))
       > sc.window_seconds THEN
        v_reason := 'NO_WINDOW_CAPTURE';
    ELSE
        -- 2. Market eligibility -- still before the model is invoked.
        v_reason := model.eligibility(sc.event_id, sc.market_type::text,
                                      sc.selection_key, sc.line);
    END IF;

    SELECT consensus_probability, md5(to_jsonb(t)::text),
           market_quality::text, book_count, current_captured_at
      INTO v_market, v_hash, v_quality, v_books, v_obs_at
      FROM public.market_intelligence t
     WHERE event_id      = sc.event_id
       AND market_type   = sc.market_type
       AND selection     = sc.selection_key
       AND line IS NOT DISTINCT FROM sc.line;

    -- 3. ONLY NOW is the model invoked, and only when the answer is yes.
    IF v_reason = 'ELIGIBLE' THEN
        v_belief := model.form_belief(
            'v01', sc.model_version, 'market-sharpen-k1.10',
            sc.event_id, sc.market_type::text, sc.selection_key, sc.line,
            model.v01_probability(v_market),
            v_hash);
    END IF;

    INSERT INTO model.formation_attempts
        (model_id, model_version, event_id, market_type, selection_key, line,
         reason, belief_id, market_quality, book_count,
         schedule_id, target_formation_at, selected_observation_at,
         seconds_from_target, seconds_to_kickoff)
    VALUES ('v01', sc.model_version, sc.event_id, sc.market_type,
            sc.selection_key, sc.line, v_reason, v_belief, v_quality, v_books,
            sc.schedule_id, sc.target_formation_at,
            CASE WHEN v_reason = 'ELIGIBLE' THEN v_obs_at END,
            round(extract(epoch FROM NOW() - sc.target_formation_at))::int,
            round(extract(epoch FROM v_kick - NOW()))::int);

    RETURN v_reason;
END;
$fn$;

COMMENT ON FUNCTION model.resolve_v01 IS
    'Terminates one scheduled opportunity. Window capture is checked first, then '
    'market eligibility, and the model is invoked ONLY if the answer is '
    'ELIGIBLE -- so it can never see its own output and then decide whether the '
    'opportunity counted.';

-- -----------------------------------------------------------------------------
-- The experiment ledger. Unresolved rows are visible as such, so the identity
-- scheduled = formed + ineligible can be checked rather than trusted.
-- -----------------------------------------------------------------------------
CREATE VIEW model.v01_ledger
WITH (security_invoker = true) AS
SELECT s.schedule_id, s.model_id, s.model_version, s.event_id,
       s.market_type, s.selection_key, s.line,
       s.target_formation_at, s.window_seconds,
       a.attempt_id, a.reason, a.belief_id,
       a.selected_observation_at, a.seconds_from_target, a.seconds_to_kickoff,
       (a.attempt_id IS NULL)        AS unresolved,
       (a.belief_id  IS NOT NULL)    AS belief_formed
FROM model.formation_schedule s
LEFT JOIN model.formation_attempts a ON a.schedule_id = s.schedule_id;

COMMENT ON VIEW model.v01_ledger IS
    'The v0.1 experiment population. Every scheduled opportunity with its '
    'terminal outcome, or flagged unresolved. scheduled = formed + ineligible '
    'is checkable here rather than assumed.';

-- -----------------------------------------------------------------------------
-- Privileges. The producer role does not schedule and does not resolve -- an
-- operator does. The model has no way to create or decline an opportunity.
-- -----------------------------------------------------------------------------
REVOKE ALL ON model.formation_schedule FROM PUBLIC;
REVOKE ALL ON FUNCTION model.schedule_v01(TEXT, INTERVAL, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.resolve_v01(UUID) FROM PUBLIC;

GRANT SELECT ON model.formation_schedule TO olp_model, olp_grader;
GRANT SELECT ON model.v01_ledger          TO olp_model, olp_grader;
GRANT EXECUTE ON FUNCTION model.v01_probability(NUMERIC, NUMERIC) TO olp_model;
