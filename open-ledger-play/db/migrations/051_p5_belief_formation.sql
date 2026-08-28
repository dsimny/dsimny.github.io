-- =============================================================================
-- 051_p5_belief_formation.sql -- Package #5, increment 2: immutable belief
-- storage and formation binding, and nothing more
-- =============================================================================
-- No grading. No calibration. No null model. One invariant, as in 050:
--
--     The database can answer, permanently and unambiguously, what the model
--     believed, when, which version said it, and exactly which market
--     observation that belief was formed against.
--
-- -----------------------------------------------------------------------------
-- THE MODEL PROPOSES; THE DATABASE DETERMINES
-- -----------------------------------------------------------------------------
-- `olp_model` has NO INSERT on model.beliefs. It calls `model.form_belief()`,
-- a SECURITY DEFINER function, and passes only what a model is entitled to
-- assert: a probability, an interval, and its own provenance.
--
-- The MARKET side of the row is not an input. The function resolves the
-- Package #4 observation itself and stamps `formation_snapshot_id` and
-- `market_probability_at_formation` from it. A model that could choose its own
-- formation market probability could manufacture an edge by understating the
-- market, and no downstream grading would ever notice.
--
-- -----------------------------------------------------------------------------
-- THE ANCHOR IS THE SNAPSHOT, NOT THE CLOCK
-- -----------------------------------------------------------------------------
-- `formed_at` alone is not enough: "the market at 14:03" stops being a single
-- fact once several books, selections and observations exist. `formation_snapshot_id`
-- is a concrete historical row, so the question "what was this belief formed
-- against" has exactly one answer forever.
--
-- REJECTED, and worth recording: a composite foreign key
-- (formation_snapshot_id, event_id, market_type, selection) referencing
-- market_snapshots would prove semantic consistency declaratively, which is
-- nicer than a trigger. Two reasons not to.
--
--   1. It needs a new UNIQUE constraint on `market_snapshots`, a Package #1
--      table frozen since `pkg2-v1.0`. The constraint would be behaviour-neutral
--      -- redundant given the primary key -- but it is still a schema change to
--      a frozen package to serve a later one's convenience.
--   2. `line` is NULL for MONEYLINE, and a foreign key with MATCH SIMPLE is not
--      enforced at all when any referencing column is NULL. It would therefore
--      silently stop protecting exactly the first market we intend to model
--      (decision 6), while continuing to look like protection.
--
-- So consistency is enforced on the insertion path instead: the function
-- resolves the binding so a mismatch is impossible by construction, and a
-- BEFORE INSERT trigger re-checks it for any direct insert by the owner.
-- Belt and braces, because the trigger is the only thing standing behind a
-- privileged mistake.
--
-- -----------------------------------------------------------------------------
-- LOCKED -- what formation_snapshot_id does and does not claim
-- -----------------------------------------------------------------------------
-- `formation_snapshot_id` identifies the EXECUTABLE market observation against
-- which the belief was formed, and from which Package #2's movement and
-- staleness semantics are inherited. It is an ANCHOR, not a complete
-- representation of every market fact the model used.
--
-- The distinction is load-bearing. `market_intelligence` encodes consensus,
-- dispersion, book counts, modal line and movement -- information derived from
-- many quotes across many books. Naming a single `market_snapshots.id` as the
-- belief's provenance would overstate what that id proves. It proves which
-- executable quote anchored the belief and whether that quote has since been
-- superseded; it does not prove what the rest of the surface said.
--
-- So the record carries two different proofs:
--
--     formation_snapshot_id   which executable quote anchored this belief, and
--                             is it stale under Package #2 semantics?
--     market_input_hash       what market-intelligence state did the model
--                             actually receive?
--
-- `market_input_hash` is an md5 over the whole `model_input.market_intelligence`
-- row the model consumed, stamped by the database. It lets a later reader prove
-- the input SURFACE has not been silently reconstructed differently -- if
-- Package #4's definition changes, replaying the same inputs produces a
-- different hash and the discrepancy is visible rather than absorbed.
--
-- Formation therefore requires an executable market: `executable_snapshot_id`
-- is the only snapshot anchor `market_intelligence` exposes, and reusing it
-- preserves the Package #4 boundary. Deliberately NOT built: a second staleness
-- system, or any grant of raw snapshot access to olp_model to obtain a "better"
-- anchor. The consequence -- v1 cannot form beliefs about single-book,
-- wide-dispersion, stale or post-kickoff markets -- is accepted.
--
-- -----------------------------------------------------------------------------
-- IDENTITY -- append-only does not mean one prediction forever
-- -----------------------------------------------------------------------------
-- UNIQUE (model_id, model_version, formation_snapshot_id).
--
-- One model version may state one belief per exact market observation. When the
-- market moves, that is a NEW observation, so a new prospective belief is
-- allowed and expected -- which is the point of append-only storage. A second
-- belief against the SAME observation is refused, because P5-T04 requires
-- determinism: same inputs and same version must give the same answer, so a
-- repeat is a duplicate rather than a new claim.
--
-- Deliberately NOT UNIQUE (event_id, model_version, selection): that would
-- prohibit legitimate repeated prospective beliefs as the market evolves, which
-- is the normal case, not an error.
-- =============================================================================

CREATE TABLE model.beliefs (
    belief_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- who is claiming this, and with what
    model_id         TEXT NOT NULL,
    model_version    TEXT NOT NULL,
    feature_version  TEXT NOT NULL,

    -- which wager. event_id / market_type / line are the join key back to
    -- Package #4 and are exempt from the disjoint-name rule; `selection_key`
    -- is deliberately not called `selection`.
    event_id         UUID NOT NULL REFERENCES public.events(id),
    market_type      public.market_type NOT NULL,
    selection_key    TEXT NOT NULL,
    line             NUMERIC(7,2),

    -- what is believed
    model_probability NUMERIC(9,6) NOT NULL,
    lower_bound       NUMERIC(9,6),
    upper_bound       NUMERIC(9,6),
    uncertainty_method TEXT,

    -- what the market said at formation. Stamped by the database, never by
    -- the model.
    market_probability_at_formation NUMERIC(9,6) NOT NULL,
    formation_snapshot_id UUID NOT NULL REFERENCES public.market_snapshots(id),
    market_input_hash TEXT NOT NULL,

    formed_at        TIMESTAMPTZ NOT NULL,
    inputs_hash      TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_belief_probability
        CHECK (model_probability > 0 AND model_probability < 1),
    CONSTRAINT ck_belief_market_probability
        CHECK (market_probability_at_formation > 0
           AND market_probability_at_formation < 1),
    -- bounds arrive together or not at all
    CONSTRAINT ck_belief_bounds_paired
        CHECK ((lower_bound IS NULL) = (upper_bound IS NULL)),
    CONSTRAINT ck_belief_bounds_range
        CHECK (lower_bound IS NULL
           OR (lower_bound >= 0 AND upper_bound <= 1)),
    CONSTRAINT ck_belief_bounds_order
        CHECK (lower_bound IS NULL
           OR (lower_bound <= model_probability
           AND model_probability <= upper_bound)),
    -- an interval without a stated method is a number with no provenance
    CONSTRAINT ck_belief_uncertainty_method
        CHECK ((lower_bound IS NULL) = (uncertainty_method IS NULL)),

    CONSTRAINT uq_belief_identity
        UNIQUE (model_id, model_version, formation_snapshot_id)
);

COMMENT ON TABLE model.beliefs IS
    'Immutable, append-only model beliefs. One row is one claim, by one model '
    'version, formed against exactly one market observation. A later run adds '
    'a row; it never rewrites one. The market columns are stamped by '
    'model.form_belief(), never supplied by the model.';

COMMENT ON COLUMN model.beliefs.formation_snapshot_id IS
    'The executable market observation this belief was formed against, and the '
    'anchor from which Package #2 movement and staleness semantics are '
    'inherited. An ANCHOR, not a complete representation of every market fact '
    'the model used -- market_intelligence encodes information derived from many '
    'quotes, and naming one snapshot as the belief''s full provenance would '
    'overstate what it proves. formed_at alone is worse: it stops being a single '
    'fact once several books and observations exist.';

COMMENT ON COLUMN model.beliefs.market_input_hash IS
    'md5 of the whole model_input.market_intelligence row the model consumed, '
    'stamped by the database. Proves what market state the model actually '
    'received, so a later reconstruction of the input surface is visible as a '
    'hash mismatch rather than absorbed silently.';

COMMENT ON COLUMN model.beliefs.inputs_hash IS
    'The model''s own declaration of its inputs. Distinct from '
    'market_input_hash, which the database computes and the model cannot '
    'influence.';

CREATE INDEX idx_beliefs_wager
    ON model.beliefs (event_id, market_type, selection_key, line);
CREATE INDEX idx_beliefs_version
    ON model.beliefs (model_id, model_version, formed_at DESC);

-- -----------------------------------------------------------------------------
-- Immutability, reusing Package #1's guard so the rule reads identically to
-- market_snapshots and wallet_transactions.
-- -----------------------------------------------------------------------------
CREATE TRIGGER trg_beliefs_append_only
    BEFORE UPDATE OR DELETE ON model.beliefs
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- Semantic consistency of the binding, for any insert that bypasses the
-- formation function. A plain FK proves the snapshot EXISTS; this proves it is
-- the snapshot for THIS wager.
--
-- SECURITY DEFINER, and the reason is worth stating. Its job is to VALIDATE, so
-- it must be able to read market_snapshots regardless of who is inserting.
-- Without that it fails for callers who lack the read -- which happens to block
-- olp_model, but for the wrong reason: a confusing permission error standing in
-- for a validation that never ran. Correct behaviour is that each barrier
-- refuses for its own reason, independently, so a negative control can tell
-- them apart.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.olp_check_belief_binding()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    s public.market_snapshots%ROWTYPE;
BEGIN
    SELECT * INTO s FROM public.market_snapshots WHERE id = NEW.formation_snapshot_id;

    IF s.event_id    IS DISTINCT FROM NEW.event_id
    OR s.market_type IS DISTINCT FROM NEW.market_type
    OR s.selection   IS DISTINCT FROM NEW.selection_key
    OR s.line        IS DISTINCT FROM NEW.line THEN
        RAISE EXCEPTION
            'BELIEF_BINDING_MISMATCH: belief describes (%, %, %, %) but its '
            'bound snapshot is (%, %, %, %)',
            NEW.event_id, NEW.market_type, NEW.selection_key, NEW.line,
            s.event_id, s.market_type, s.selection, s.line
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_beliefs_binding_consistent
    BEFORE INSERT ON model.beliefs
    FOR EACH ROW EXECUTE FUNCTION model.olp_check_belief_binding();

-- -----------------------------------------------------------------------------
-- The only sanctioned way to create a belief.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.form_belief(
    p_model_id           TEXT,
    p_model_version      TEXT,
    p_feature_version    TEXT,
    p_event_id           UUID,
    -- TEXT, not public.market_type, deliberately. A typed parameter would force
    -- the CALLER to cast to `public.market_type`, and that cast needs USAGE on
    -- schema public -- which 050 revoked from olp_model on purpose. Taking text
    -- and casting inside the definer function keeps the model with no reachable
    -- path into `public` at all. Reading enum COLUMNS needs no such grant, so
    -- model.beliefs remains readable.
    p_market_type        TEXT,
    p_selection          TEXT,
    p_line               NUMERIC,
    p_probability        NUMERIC,
    p_inputs_hash        TEXT,
    p_lower_bound        NUMERIC DEFAULT NULL,
    p_upper_bound        NUMERIC DEFAULT NULL,
    p_uncertainty_method TEXT    DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    mi        model_input.market_intelligence%ROWTYPE;
    v_belief  UUID;
BEGIN
    IF p_model_id IS NULL OR p_model_version IS NULL
    OR p_feature_version IS NULL OR p_inputs_hash IS NULL THEN
        RAISE EXCEPTION
            'BELIEF_PROVENANCE_REQUIRED: model_id, model_version, '
            'feature_version and inputs_hash are all mandatory'
            USING ERRCODE = 'not_null_violation';
    END IF;

    -- Read the surface the MODEL sees, not the public view, so the hash below
    -- is over exactly what was consumed.
    SELECT * INTO mi
      FROM model_input.market_intelligence
     WHERE event_id    = p_event_id
       AND market_type = p_market_type::public.market_type
       AND selection   = p_selection
       AND line IS NOT DISTINCT FROM p_line;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'NO_SUCH_MARKET: no current market row for (%, %, %, %)',
            p_event_id, p_market_type, p_selection, p_line
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Fail closed, exactly as Package #4 does. See the header: this is the
    -- open decision for this increment.
    IF NOT mi.is_executable OR mi.executable_snapshot_id IS NULL THEN
        RAISE EXCEPTION
            'MARKET_NOT_EXECUTABLE: (%, %, %, %) has no executable observation '
            'to bind a belief to',
            p_event_id, p_market_type, p_selection, p_line
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF mi.consensus_probability IS NULL THEN
        RAISE EXCEPTION
            'NO_MARKET_PROBABILITY: (%, %, %, %) has no de-vigged consensus, so '
            'there is nothing to form a belief against',
            p_event_id, p_market_type, p_selection, p_line
            USING ERRCODE = 'no_data_found';
    END IF;

    INSERT INTO model.beliefs (
        model_id, model_version, feature_version,
        event_id, market_type, selection_key, line,
        model_probability, lower_bound, upper_bound, uncertainty_method,
        market_probability_at_formation, formation_snapshot_id,
        market_input_hash, formed_at, inputs_hash)
    VALUES (
        p_model_id, p_model_version, p_feature_version,
        mi.event_id, mi.market_type, mi.selection, mi.line,
        p_probability, p_lower_bound, p_upper_bound, p_uncertainty_method,
        -- stamped from the market, never from the caller
        mi.consensus_probability, mi.executable_snapshot_id,
        md5(to_jsonb(mi)::text),
        NOW(), p_inputs_hash)
    RETURNING belief_id INTO v_belief;

    RETURN v_belief;
END;
$fn$;

COMMENT ON FUNCTION model.form_belief IS
    'The model proposes a belief; the database determines what market fact it '
    'was formed against. formation_snapshot_id, market_probability_at_formation '
    'and market_input_hash are resolved from Package #4 and are not caller '
    'inputs -- a model able to choose its own formation market probability could '
    'manufacture an edge that no later grading would detect.';

-- -----------------------------------------------------------------------------
-- Privileges. The model may form beliefs and read its own prior beliefs -- they
-- carry no outcome information -- but may not write the table directly.
-- -----------------------------------------------------------------------------
REVOKE ALL ON model.beliefs FROM PUBLIC;
REVOKE ALL ON FUNCTION model.form_belief FROM PUBLIC;
REVOKE ALL ON FUNCTION model.olp_check_belief_binding() FROM PUBLIC;

GRANT SELECT ON model.beliefs TO olp_model;
GRANT EXECUTE ON FUNCTION model.form_belief(
    TEXT, TEXT, TEXT, UUID, TEXT, TEXT, NUMERIC, NUMERIC, TEXT,
    NUMERIC, NUMERIC, TEXT) TO olp_model;
