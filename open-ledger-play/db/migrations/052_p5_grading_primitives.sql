-- =============================================================================
-- 052_p5_grading_primitives.sql -- Package #5, increment 3: grading primitives
-- =============================================================================
-- Outcome resolution, proper scoring rules, market-relative deltas, and CLV as
-- an observation. NO calibration aggregation -- that is 053. NO null model --
-- that is 054, and it will run through this grader with no special path.
--
-- The grader is built BEFORE the null model deliberately: the null model should
-- be just another producer of beliefs, not a privileged special case. If this
-- layer cannot grade planted beliefs correctly, testing the null model against
-- it proves nothing.
--
-- -----------------------------------------------------------------------------
-- THE GOVERNING RULE
-- -----------------------------------------------------------------------------
--     The grader grades STORED FACTS. It never reconstructs what the model
--     "must have meant".
--
-- Every quantity used here comes from the immutable belief row -- the
-- probability, the formation baseline, the binding -- or from a recorded
-- outcome. Nothing is re-derived from the current market. In particular the
-- market baseline is `market_probability_at_formation`, the value stamped when
-- the belief was formed, and never a later market state. A grader that re-read
-- the market would be scoring the model against a fact it never saw.
--
-- -----------------------------------------------------------------------------
-- A SEPARATE SCHEMA, BECAUSE THE ARROW ONLY POINTS ONE WAY
-- -----------------------------------------------------------------------------
--   grading.*     outcomes and grades.  olp_model is REFUSED, all of it.
--   model.beliefs the graded object.    olp_grader may READ it, never write it.
--
-- The grading system can see the model. The model cannot see the grading
-- system. Putting grades in `model` would have exposed them to the model role,
-- which already holds SELECT on model.beliefs.
--
-- -----------------------------------------------------------------------------
-- WHAT COUNTS AS SCORED -- pre-registered, not decided later
-- -----------------------------------------------------------------------------
-- A probabilistic forecast can only be scored against a binary outcome. PUSH
-- and VOID have no binary outcome, so they are RECORDED and EXCLUDED rather
-- than silently entering the sample:
--
--     WIN / LOSS   -> SCORED
--     PUSH         -> EXCLUDED_PUSH
--     VOID         -> EXCLUDED_VOID
--
-- Excluded rows carry NULL scores, enforced by CHECK, so they cannot leak into
-- any later aggregate by accident.
--
-- -----------------------------------------------------------------------------
-- CLV IS AN OBSERVATION, NEVER AN IDENTITY
-- -----------------------------------------------------------------------------
-- Closing-line value is recorded because it is a useful diagnostic about timing
-- and line shopping. It is NOT an assertion, and nothing in this package's
-- correctness depends on it. A null model has zero informational edge relative
-- to the observation it was formed from -- an identity -- but its CLV against a
-- LATER close is not zero, because the closing probability moves. Over a large
-- unbiased sample that movement may average near zero; that is an empirical
-- expectation, not a fact to encode.
--
-- Same-book comparison, and NULL when the line moved, exactly matching
-- Package #2's CLV semantics (M2-T26, M2-T27) rather than inventing a second
-- definition.
--
-- One deliberate difference from Package #2: the payout comparison uses
-- `olp_price_payout`, not `olp_american_profit`. The latter rounds to cents and
-- collapses adjacent prices -- the defect Package #4 found and fixed in
-- migration 044. Package #2's `ticket_closing_line_value` still carries that
-- collision; it is frozen and is not changed here, but a NEW field must not
-- inherit a known defect.
-- =============================================================================

CREATE SCHEMA grading;

COMMENT ON SCHEMA grading IS
    'Outcomes and grades. The grading system can see the model; the model '
    'cannot see the grading system. olp_model is refused everything in here.';

CREATE TYPE grading.wager_outcome  AS ENUM ('WIN', 'LOSS', 'PUSH', 'VOID');
CREATE TYPE grading.scoring_status AS ENUM
    ('SCORED', 'EXCLUDED_PUSH', 'EXCLUDED_VOID');

-- -----------------------------------------------------------------------------
-- Proper scoring rules. Pure, immutable, and hand-checkable -- P5-T15 asserts
-- them against independently computed values rather than against themselves.
-- -----------------------------------------------------------------------------
CREATE FUNCTION grading.brier(p NUMERIC, won BOOLEAN)
RETURNS NUMERIC
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT (p - CASE WHEN won THEN 1 ELSE 0 END) ^ 2;
$fn$;

CREATE FUNCTION grading.log_loss(p NUMERIC, won BOOLEAN)
RETURNS NUMERIC
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT CASE WHEN won THEN -ln(p) ELSE -ln(1 - p) END;
$fn$;

COMMENT ON FUNCTION grading.brier IS
    'Brier score for one forecast: (p - outcome)^2. Lower is better. Defined '
    'only for a binary outcome, which is why PUSH and VOID are excluded rather '
    'than scored.';

-- -----------------------------------------------------------------------------
-- Outcome resolution. A wager is gradeable only once its outcome is recorded;
-- there is no scores feed yet, so this is written by the settlement operator,
-- exactly as Package #1's settlement is.
-- -----------------------------------------------------------------------------
CREATE TABLE grading.wager_outcomes (
    outcome_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id      UUID NOT NULL REFERENCES public.events(id),
    market_type   public.market_type NOT NULL,
    selection_key TEXT NOT NULL,
    line          NUMERIC(7,2),
    outcome       grading.wager_outcome NOT NULL,
    resolved_at   TIMESTAMPTZ NOT NULL,
    source        TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wager_outcome
        UNIQUE (event_id, market_type, selection_key, line)
);

COMMENT ON TABLE grading.wager_outcomes IS
    'One resolved outcome per wager. Append-only: a correction is a new fact '
    'about the world, and rewriting history here would silently re-grade every '
    'belief that depended on it.';

CREATE TRIGGER trg_wager_outcomes_append_only
    BEFORE UPDATE OR DELETE ON grading.wager_outcomes
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- The grade. One per belief, immutable, computed only from stored facts.
-- -----------------------------------------------------------------------------
CREATE TABLE grading.belief_grades (
    grade_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    belief_id      UUID NOT NULL REFERENCES model.beliefs(belief_id),
    outcome        grading.wager_outcome NOT NULL,
    scoring_status grading.scoring_status NOT NULL,

    -- proper scoring rules, model and market, from the FROZEN formation baseline
    model_brier     NUMERIC(12,8),
    model_log_loss  NUMERIC(12,8),
    market_brier    NUMERIC(12,8),
    market_log_loss NUMERIC(12,8),
    -- model minus market. Negative means the model scored better.
    brier_delta     NUMERIC(12,8),
    log_loss_delta  NUMERIC(12,8),

    -- CLV, observed and never asserted
    formation_price     INT,
    closing_price       INT,
    closing_snapshot_id UUID REFERENCES public.market_snapshots(id),
    clv_payout_delta    NUMERIC(12,8),
    clv_status          TEXT NOT NULL
        CHECK (clv_status IN ('OBSERVED', 'LINE_MOVED', 'NO_CLOSING_LINE')),

    graded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_grade_per_belief UNIQUE (belief_id),
    -- excluded rows carry no scores, so they cannot leak into an aggregate
    CONSTRAINT ck_scored_iff_scores
        CHECK ((scoring_status = 'SCORED') = (model_brier IS NOT NULL)),
    CONSTRAINT ck_scores_together
        CHECK ((model_brier IS NULL) = (model_log_loss IS NULL)
           AND (model_brier IS NULL) = (market_brier IS NULL)
           AND (model_brier IS NULL) = (market_log_loss IS NULL))
);

COMMENT ON TABLE grading.belief_grades IS
    'One immutable grade per belief. Every input is a stored fact: the belief''s '
    'own probability and its formation baseline, plus a recorded outcome. The '
    'grader never re-reads the market -- scoring a model against a fact it never '
    'saw would not be grading it.';

CREATE INDEX idx_grades_scored ON grading.belief_grades (scoring_status, graded_at);

CREATE TRIGGER trg_belief_grades_append_only
    BEFORE UPDATE OR DELETE ON grading.belief_grades
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- Recording an outcome.
-- -----------------------------------------------------------------------------
CREATE FUNCTION grading.record_outcome(
    p_event_id      UUID,
    p_market_type   TEXT,
    p_selection     TEXT,
    p_line          NUMERIC,
    p_outcome       TEXT,
    p_source        TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    v_id UUID;
BEGIN
    INSERT INTO grading.wager_outcomes
        (event_id, market_type, selection_key, line, outcome, resolved_at, source)
    VALUES (p_event_id, p_market_type::public.market_type, p_selection, p_line,
            p_outcome::grading.wager_outcome, NOW(), p_source)
    RETURNING outcome_id INTO v_id;
    RETURN v_id;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Grading one belief.
--
-- SECURITY DEFINER, for the same reason 051's binding trigger is: the function's
-- JOB requires reading market_snapshots to observe the closing line, and a
-- function that fails because its CALLER lacks a read is failing for the wrong
-- reason -- a permission error standing in for work that never ran. Authority to
-- invoke is controlled by the EXECUTE grant instead, which keeps olp_grader's
-- own grants minimal: it never needs raw market access of its own.
-- -----------------------------------------------------------------------------
CREATE FUNCTION grading.grade_belief(p_belief_id UUID)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    b            model.beliefs%ROWTYPE;
    v_outcome    grading.wager_outcome;
    v_status     grading.scoring_status;
    v_won        BOOLEAN;
    v_form       public.market_snapshots%ROWTYPE;
    v_close      public.market_snapshots%ROWTYPE;
    v_clv_status TEXT;
    v_clv_delta  NUMERIC;
    v_grade      UUID;
BEGIN
    SELECT * INTO b FROM model.beliefs WHERE belief_id = p_belief_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'NO_SUCH_BELIEF: %', p_belief_id
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT outcome INTO v_outcome
      FROM grading.wager_outcomes
     WHERE event_id      = b.event_id
       AND market_type   = b.market_type
       AND selection_key = b.selection_key
       AND line IS NOT DISTINCT FROM b.line;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'WAGER_UNRESOLVED: no recorded outcome for (%, %, %, %) -- a belief '
            'cannot be graded before the world has answered it',
            b.event_id, b.market_type, b.selection_key, b.line
            USING ERRCODE = 'no_data_found';
    END IF;

    v_status := CASE v_outcome
                    WHEN 'PUSH' THEN 'EXCLUDED_PUSH'::grading.scoring_status
                    WHEN 'VOID' THEN 'EXCLUDED_VOID'::grading.scoring_status
                    ELSE 'SCORED'::grading.scoring_status
                END;
    v_won := (v_outcome = 'WIN');

    -- CLV, same book, Package #2 semantics: NULL when the line moved.
    SELECT * INTO v_form FROM public.market_snapshots WHERE id = b.formation_snapshot_id;
    SELECT * INTO v_close
      FROM public.market_snapshots
     WHERE event_id   = v_form.event_id
       AND market_type = v_form.market_type
       AND selection   = v_form.selection
       AND sportsbook  = v_form.sportsbook
       AND is_closing_snapshot = TRUE;

    IF NOT FOUND THEN
        v_clv_status := 'NO_CLOSING_LINE';
        v_clv_delta  := NULL;
    ELSIF v_close.line IS DISTINCT FROM v_form.line THEN
        v_clv_status := 'LINE_MOVED';
        v_clv_delta  := NULL;
    ELSE
        v_clv_status := 'OBSERVED';
        -- olp_price_payout, not olp_american_profit: the latter rounds to cents
        -- and collapses adjacent prices. See migration 044.
        v_clv_delta  := public.olp_price_payout(v_form.price)
                      - public.olp_price_payout(v_close.price);
    END IF;

    INSERT INTO grading.belief_grades (
        belief_id, outcome, scoring_status,
        model_brier, model_log_loss, market_brier, market_log_loss,
        brier_delta, log_loss_delta,
        formation_price, closing_price, closing_snapshot_id,
        clv_payout_delta, clv_status)
    VALUES (
        b.belief_id, v_outcome, v_status,
        CASE WHEN v_status = 'SCORED' THEN grading.brier(b.model_probability, v_won) END,
        CASE WHEN v_status = 'SCORED' THEN grading.log_loss(b.model_probability, v_won) END,
        -- the baseline is the FROZEN formation probability, never a later market
        CASE WHEN v_status = 'SCORED'
             THEN grading.brier(b.market_probability_at_formation, v_won) END,
        CASE WHEN v_status = 'SCORED'
             THEN grading.log_loss(b.market_probability_at_formation, v_won) END,
        CASE WHEN v_status = 'SCORED'
             THEN grading.brier(b.model_probability, v_won)
                - grading.brier(b.market_probability_at_formation, v_won) END,
        CASE WHEN v_status = 'SCORED'
             THEN grading.log_loss(b.model_probability, v_won)
                - grading.log_loss(b.market_probability_at_formation, v_won) END,
        v_form.price, v_close.price, v_close.id,
        v_clv_delta, v_clv_status)
    RETURNING grade_id INTO v_grade;

    RETURN v_grade;
END;
$fn$;

COMMENT ON FUNCTION grading.grade_belief IS
    'Grades one belief from stored facts only. The market baseline is the '
    'belief''s own market_probability_at_formation -- never a later market '
    'state, because scoring a model against a fact it never saw is not grading '
    'it. CLV is recorded as an observation and no correctness claim rests on it.';

-- -----------------------------------------------------------------------------
-- The one-way arrow, as privileges.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'olp_grader') THEN
        CREATE ROLE olp_grader NOLOGIN NOINHERIT;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA grading FROM olp_grader';
    EXECUTE format('GRANT olp_grader TO %I', current_user);
EXCEPTION WHEN OTHERS THEN
    NULL;
END
$$;

REVOKE ALL ON SCHEMA grading FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA grading FROM PUBLIC;

-- The grader reads beliefs and writes grades.
GRANT USAGE  ON SCHEMA grading TO olp_grader;
GRANT USAGE  ON SCHEMA model   TO olp_grader;
GRANT SELECT ON model.beliefs  TO olp_grader;
GRANT SELECT ON ALL TABLES IN SCHEMA grading TO olp_grader;
GRANT EXECUTE ON FUNCTION grading.grade_belief(UUID) TO olp_grader;
GRANT EXECUTE ON FUNCTION grading.record_outcome(UUID, TEXT, TEXT, NUMERIC, TEXT, TEXT)
    TO olp_grader;

-- ...and cannot write beliefs. The arrow points one way.
REVOKE INSERT, UPDATE, DELETE ON model.beliefs FROM olp_grader;

-- The model sees none of this. No grant to olp_model anywhere in this file;
-- stated explicitly so a future reader does not add one by reflex.
REVOKE ALL ON SCHEMA grading FROM olp_model;
REVOKE ALL ON ALL TABLES IN SCHEMA grading FROM olp_model;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA grading FROM olp_model;
