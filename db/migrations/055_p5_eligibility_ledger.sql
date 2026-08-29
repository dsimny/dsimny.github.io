-- =============================================================================
-- 055_p5_eligibility_ledger.sql -- Package #5, increment 6: the denominator
-- =============================================================================
-- Every formation ATTEMPT is recorded, including the rejected ones, with an
-- explicit reason.
--
-- Without this a model can look well calibrated partly because the system
-- quietly filtered out the hard cases. Calibration measured over a population
-- you cannot describe is not a measurement; it is a survivorship statistic. The
-- ledger is the denominator that makes the numerator meaningful.
--
-- -----------------------------------------------------------------------------
-- THE STRUCTURAL PROBLEM, AND WHY THERE IS A NEW ENTRY POINT
-- -----------------------------------------------------------------------------
-- `model.form_belief()` RAISES on an ineligible market, and a raise rolls back
-- the transaction -- including any log row written inside it. A rejected attempt
-- therefore CANNOT be recorded from within it. That is not a flaw in 051: an
-- exception is the right answer to "form a belief here" when the answer is no.
--
-- So 055 adds `model.attempt_belief()`, which evaluates eligibility FIRST,
-- records the attempt either way, and calls `form_belief` only when the answer
-- is ELIGIBLE. `form_belief` keeps its own guards untouched as defence in depth,
-- and P5-T41 asserts the two agree: form_belief raises exactly when eligibility
-- is not ELIGIBLE. Two evaluations that can drift are a liability, so their
-- agreement is tested rather than assumed.
--
-- olp_model's EXECUTE on `form_belief` is REVOKED here. If the model could still
-- reach it directly the denominator would be incomplete by construction, and an
-- incomplete denominator is worse than none because it looks like one.
--
-- -----------------------------------------------------------------------------
-- THE EVALUATION POPULATION IS "EXECUTION-ELIGIBLE", NOT "THE MARKET"
-- -----------------------------------------------------------------------------
-- v1 forms beliefs only where a belief can be bound to a real executable
-- observation. That restriction stays, and the model's access is NOT widened to
-- improve sampling. What changes is that the restriction is now VISIBLE: the
-- ledger records what was excluded and why, so later analysis can compare
-- included against excluded rather than assuming they are alike.
--
-- Any calibration or standing result from this system describes the
-- EXECUTION-ELIGIBLE population. It does not describe "the market".
--
-- -----------------------------------------------------------------------------
-- PRECEDENCE -- fixed here, because a denominator whose categories shift is not
-- a denominator
-- -----------------------------------------------------------------------------
--   1. NO_MARKET_ROW              no book has ever quoted this wager
--   2. POST_KICKOFF               the event has started, is live, or is closed
--   3. STALE                      the newest observation is older than the TTL
--   4. INSUFFICIENT_MARKET_STATE  quoted and fresh, but no de-vigged consensus
--   5. NO_EXECUTABLE_MARKET       fresh and pre-kickoff, but not executable --
--                                 single book, wide dispersion, below the floor
--   6. ELIGIBLE
--
-- Steps 1 and 3 both read market_snapshots directly, and they have to: the
-- TTL-filtered market_intelligence view drops stale wagers entirely, so from
-- that surface alone STALE is indistinguishable from "nobody ever quoted it".
-- Collapsing those two would corrupt the denominator -- one is a market
-- condition, the other is a producer asking about nothing.
--
-- POST_KICKOFF is checked before STALE deliberately: after kickoff the quotes
-- are also stale, and reporting that as staleness would hide the real reason.
-- =============================================================================

CREATE TYPE model.eligibility_reason AS ENUM (
    'ELIGIBLE',
    'NO_EXECUTABLE_MARKET',
    'STALE',
    'POST_KICKOFF',
    'INSUFFICIENT_MARKET_STATE',
    -- Not in the original list, added because it is a materially different
    -- thing: the model asked about a wager that does not exist, which is a
    -- producer bug rather than a market condition, and folding it into
    -- INSUFFICIENT_MARKET_STATE would hide that.
    'NO_MARKET_ROW'
);

CREATE TABLE model.formation_attempts (
    attempt_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id        TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    event_id        UUID NOT NULL REFERENCES public.events(id),
    market_type     public.market_type NOT NULL,
    selection_key   TEXT NOT NULL,
    line            NUMERIC(7,2),
    reason          model.eligibility_reason NOT NULL,
    belief_id       UUID REFERENCES model.beliefs(belief_id),
    -- market context at the moment of the attempt, so an exclusion can be
    -- characterised later without re-deriving a market state that has moved on
    market_quality  TEXT,
    book_count      INT,
    attempted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_belief_iff_eligible
        CHECK ((reason = 'ELIGIBLE') = (belief_id IS NOT NULL))
);

COMMENT ON TABLE model.formation_attempts IS
    'The denominator. Every formation attempt, accepted or rejected, with an '
    'explicit reason. Calibration measured over a population you cannot describe '
    'is a survivorship statistic, not a measurement.';

CREATE INDEX idx_attempts_model  ON model.formation_attempts
    (model_id, model_version, attempted_at DESC);
CREATE INDEX idx_attempts_reason ON model.formation_attempts (reason);

CREATE TRIGGER trg_formation_attempts_append_only
    BEFORE UPDATE OR DELETE ON model.formation_attempts
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- The single evaluator. SECURITY DEFINER because it must read events and the
-- TTL setting to tell POST_KICKOFF from STALE -- infrastructure work, not the
-- model's own authority.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.eligibility(
    p_event_id    UUID,
    p_market_type TEXT,
    p_selection   TEXT,
    p_line        NUMERIC
)
RETURNS model.eligibility_reason
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    mi        RECORD;
    ev        RECORD;
    ttl       INT;
    v_newest  TIMESTAMPTZ;
BEGIN
    -- 1. Did the model ask about something that exists at all? This CANNOT be
    --    answered from market_intelligence: that view is TTL-filtered, so a
    --    stale wager has no row there and would be indistinguishable from a
    --    wager nobody ever quoted. The raw table is the only place the
    --    difference is visible, which is why this function is definer.
    SELECT max(captured_at) INTO v_newest
      FROM public.market_snapshots
     WHERE event_id      = p_event_id
       AND market_type::text = p_market_type
       AND selection     = p_selection
       AND line IS NOT DISTINCT FROM p_line
       AND is_in_play    = FALSE;

    IF v_newest IS NULL THEN
        RETURN 'NO_MARKET_ROW';
    END IF;

    -- 2. After kickoff the quotes are stale too; reporting that as staleness
    --    would name a symptom instead of the cause.
    SELECT is_live, is_closed, actual_start_time, current_scheduled_start
      INTO ev FROM public.events WHERE id = p_event_id;

    IF ev.is_live OR ev.is_closed OR ev.actual_start_time IS NOT NULL
       OR NOW() >= ev.current_scheduled_start THEN
        RETURN 'POST_KICKOFF';
    END IF;

    -- 3. Fresh enough to act on?
    SELECT snapshot_ttl_seconds INTO ttl FROM public.system_settings WHERE id;
    IF NOW() - v_newest > make_interval(secs => ttl) THEN
        RETURN 'STALE';
    END IF;

    -- 4. Fresh and pre-kickoff, so a canonical row should exist.
    SELECT consensus_probability, is_executable, executable_snapshot_id
      INTO mi
      FROM public.market_intelligence
     WHERE event_id      = p_event_id
       AND market_type::text = p_market_type
       AND selection     = p_selection
       AND line IS NOT DISTINCT FROM p_line;

    IF NOT FOUND OR mi.consensus_probability IS NULL THEN
        RETURN 'INSUFFICIENT_MARKET_STATE';
    END IF;

    -- 5. Quotable, but not executable: one book, wide dispersion, below floor.
    IF NOT mi.is_executable OR mi.executable_snapshot_id IS NULL THEN
        RETURN 'NO_EXECUTABLE_MARKET';
    END IF;

    RETURN 'ELIGIBLE';
END;
$fn$;

-- -----------------------------------------------------------------------------
-- The formation path a model uses. Records the attempt either way.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.attempt_belief(
    p_model_id           TEXT,
    p_model_version      TEXT,
    p_feature_version    TEXT,
    p_event_id           UUID,
    p_market_type        TEXT,
    p_selection          TEXT,
    p_line               NUMERIC,
    p_probability        NUMERIC,
    p_inputs_hash        TEXT,
    p_lower_bound        NUMERIC DEFAULT NULL,
    p_upper_bound        NUMERIC DEFAULT NULL,
    p_uncertainty_method TEXT    DEFAULT NULL
)
RETURNS TABLE (belief_id UUID, reason model.eligibility_reason)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    v_reason  model.eligibility_reason;
    v_belief  UUID;
    v_quality TEXT;
    v_books   INT;
BEGIN
    v_reason := model.eligibility(p_event_id, p_market_type, p_selection, p_line);

    SELECT market_quality::text, book_count INTO v_quality, v_books
      FROM public.market_intelligence
     WHERE event_id      = p_event_id
       AND market_type::text = p_market_type
       AND selection     = p_selection
       AND line IS NOT DISTINCT FROM p_line;

    IF v_reason = 'ELIGIBLE' THEN
        v_belief := model.form_belief(
            p_model_id, p_model_version, p_feature_version,
            p_event_id, p_market_type, p_selection, p_line,
            p_probability, p_inputs_hash,
            p_lower_bound, p_upper_bound, p_uncertainty_method);
    END IF;

    INSERT INTO model.formation_attempts
        (model_id, model_version, event_id, market_type, selection_key, line,
         reason, belief_id, market_quality, book_count)
    VALUES (p_model_id, p_model_version, p_event_id,
            p_market_type::public.market_type, p_selection, p_line,
            v_reason, v_belief, v_quality, v_books);

    RETURN QUERY SELECT v_belief, v_reason;
END;
$fn$;

COMMENT ON FUNCTION model.attempt_belief IS
    'The formation path a model uses. Evaluates eligibility, records the attempt '
    'whatever the answer, and forms a belief only when ELIGIBLE. form_belief '
    'raises on rejection and a raise would roll back the log, which is why the '
    'decision is made here rather than inside it.';

-- -----------------------------------------------------------------------------
-- A model may no longer reach form_belief directly. If it could, the
-- denominator would be incomplete by construction -- which is worse than having
-- none, because it still looks like one.
-- -----------------------------------------------------------------------------
REVOKE EXECUTE ON FUNCTION model.form_belief(
    TEXT, TEXT, TEXT, UUID, TEXT, TEXT, NUMERIC, NUMERIC, TEXT,
    NUMERIC, NUMERIC, TEXT) FROM olp_model;

REVOKE ALL ON FUNCTION model.attempt_belief(
    TEXT, TEXT, TEXT, UUID, TEXT, TEXT, NUMERIC, NUMERIC, TEXT,
    NUMERIC, NUMERIC, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model.attempt_belief(
    TEXT, TEXT, TEXT, UUID, TEXT, TEXT, NUMERIC, NUMERIC, TEXT,
    NUMERIC, NUMERIC, TEXT) TO olp_model;

REVOKE ALL ON FUNCTION model.eligibility(UUID, TEXT, TEXT, NUMERIC) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model.eligibility(UUID, TEXT, TEXT, NUMERIC) TO olp_model;

REVOKE ALL ON model.formation_attempts FROM PUBLIC;
GRANT SELECT ON model.formation_attempts TO olp_model;
GRANT SELECT ON model.formation_attempts TO olp_grader;

-- -----------------------------------------------------------------------------
-- The null producer moves onto the attempt path too, so it contributes to the
-- denominator like any other model.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION model.null_model_belief(
    p_event_id      UUID,
    p_market_type   TEXT,
    p_selection     TEXT,
    p_line          NUMERIC,
    p_model_version TEXT DEFAULT '1.0.0'
)
RETURNS UUID
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    v_probability NUMERIC;
    v_input_hash  TEXT;
    v_belief      UUID;
BEGIN
    SELECT t.consensus_probability, md5(to_jsonb(t)::text)
      INTO v_probability, v_input_hash
      FROM model_input.market_intelligence t
     WHERE t.event_id          = p_event_id
       AND t.market_type::text = p_market_type
       AND t.selection         = p_selection
       AND t.line IS NOT DISTINCT FROM p_line;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'NO_SUCH_MARKET: no current market row for (%, %, %, %)',
            p_event_id, p_market_type, p_selection, p_line
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT a.belief_id INTO v_belief FROM model.attempt_belief(
        'null', p_model_version, 'market-passthrough-1',
        p_event_id, p_market_type, p_selection, p_line,
        v_probability, v_input_hash) a;

    IF v_belief IS NULL THEN
        RAISE EXCEPTION
            'MARKET_NOT_ELIGIBLE: (%, %, %, %) was recorded as ineligible',
            p_event_id, p_market_type, p_selection, p_line
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN v_belief;
END;
$fn$;
