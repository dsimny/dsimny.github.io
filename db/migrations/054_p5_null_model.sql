-- =============================================================================
-- 054_p5_null_model.sql -- Package #5, increment 5: the null producer
-- =============================================================================
-- The null model must be BORING. That is the whole specification.
--
--   * no special grader path
--   * no privileged write path
--   * no null-specific scoring logic anywhere
--
-- It consumes the approved `model_input` surface, emits the market's formation
-- probability as its own, and creates an ordinary immutable belief through
-- `model.form_belief()` -- the same mechanism every future model will use. If
-- it needed anything else, that would be evidence the measurement system is not
-- yet general.
--
-- -----------------------------------------------------------------------------
-- WHY IT HAS NO PRIVILEGES OF ITS OWN
-- -----------------------------------------------------------------------------
-- Deliberately NOT `SECURITY DEFINER`. It runs as the caller and therefore holds
-- exactly the authority any model holds: SELECT on `model_input`, EXECUTE on
-- `model.form_belief`. If it were a definer function it would be a privileged
-- write path, and the null model would stop being a fair stand-in for a real
-- one.
--
-- -----------------------------------------------------------------------------
-- WHAT "NULL" MEANS HERE
-- -----------------------------------------------------------------------------
-- EXACT reproduction of the de-vigged market probability at formation. Not
-- "close enough". `probability_delta` must be identically zero, and P5-T37
-- proves the proof by perturbing the producer with +0.001 and requiring the
-- zero-divergence assertion to FAIL.
--
-- The null model states no uncertainty interval. It has no uncertainty model,
-- and inventing bounds it cannot justify would be exactly the kind of unearned
-- confidence Package #5 exists to prevent.
--
-- -----------------------------------------------------------------------------
-- WHAT IS AND IS NOT ASSERTED ABOUT IT
-- -----------------------------------------------------------------------------
-- ASSERTED: zero divergence from the market AT FORMATION. That is an identity
-- and is testable.
--
-- NOT ASSERTED: that its future CLV is zero. The closing probability moves, and
-- over a large unbiased sample that movement may average near zero -- an
-- empirical expectation, not a fact to encode. P5-T18 already proves a planted
-- non-zero CLV does not fail the harness; P5-T36 proves it does not disturb the
-- null identity either.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- probability_delta lives in a view, never on the belief row.
--
-- A stored `edge` column acquires decision semantics by gravity: someone filters
-- on it, then sizes on it. Keeping the comparison derived means the belief
-- record stays a record of what was believed, and the comparison stays an
-- analysis of it.
-- -----------------------------------------------------------------------------
CREATE VIEW model.belief_deltas
WITH (security_invoker = true) AS
SELECT b.belief_id,
       b.model_id,
       b.model_version,
       b.feature_version,
       b.event_id,
       b.market_type,
       b.selection_key,
       b.line,
       b.model_probability,
       b.market_probability_at_formation,
       (b.model_probability - b.market_probability_at_formation) AS probability_delta,
       b.formation_snapshot_id,
       b.market_input_hash,
       b.formed_at
FROM model.beliefs b;

COMMENT ON VIEW model.belief_deltas IS
    'Analytical view. probability_delta is DERIVED here and never stored on the '
    'belief -- a stored edge column acquires decision semantics by gravity. '
    'Whether a delta is worth acting on is a Package #6 judgement.';

-- -----------------------------------------------------------------------------
-- The null producer. This is the entire model.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.null_model_belief(
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
BEGIN
    -- The approved surface, and nothing else.
    --
    -- Note the comparison casts the COLUMN to text rather than the parameter to
    -- public.market_type. This function is deliberately NOT SECURITY DEFINER, so
    -- it runs with the model's own authority -- and 050 revoked USAGE on schema
    -- public from olp_model, which a cast TO a public type would require.
    -- Reading an enum column needs no such grant. Making this a definer function
    -- would have fixed the error by giving the null model a privileged path,
    -- which is precisely what it must not have.
    SELECT t.consensus_probability, md5(to_jsonb(t)::text)
      INTO v_probability, v_input_hash
      FROM model_input.market_intelligence t
     WHERE t.event_id       = p_event_id
       AND t.market_type::text = p_market_type
       AND t.selection       = p_selection
       AND t.line IS NOT DISTINCT FROM p_line;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'NO_SUCH_MARKET: no current market row for (%, %, %, %)',
            p_event_id, p_market_type, p_selection, p_line
            USING ERRCODE = 'no_data_found';
    END IF;

    -- The model, in full: say what the market says.
    -- No interval -- it has no uncertainty model and will not invent one.
    RETURN model.form_belief(
        'null',                          -- model_id
        p_model_version,
        'market-passthrough-1',          -- feature_version
        p_event_id, p_market_type, p_selection, p_line,
        v_probability,                   -- the whole of the model
        v_input_hash                     -- its own declaration of its inputs
    );
END;
$fn$;

COMMENT ON FUNCTION model.null_model_belief IS
    'The null producer. Emits the de-vigged market probability at formation as '
    'its own belief, through the ordinary formation function, with no special '
    'privileges and no special grading path. Its expected divergence from the '
    'market at formation is EXACTLY zero -- an identity. Its future CLV is NOT '
    'asserted to be zero: the closing probability moves, and that is an '
    'empirical expectation rather than a fact to encode.';

-- -----------------------------------------------------------------------------
-- Privileges: exactly what any model gets, and nothing more.
-- -----------------------------------------------------------------------------
REVOKE ALL ON FUNCTION model.null_model_belief(UUID, TEXT, TEXT, NUMERIC, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model.null_model_belief(UUID, TEXT, TEXT, NUMERIC, TEXT)
    TO olp_model;

REVOKE ALL ON model.belief_deltas FROM PUBLIC;
GRANT SELECT ON model.belief_deltas TO olp_model;
GRANT SELECT ON model.belief_deltas TO olp_grader;
