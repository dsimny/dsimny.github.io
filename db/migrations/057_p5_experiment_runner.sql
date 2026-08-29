-- =============================================================================
-- 057_p5_experiment_runner.sql -- Package #5, increment 8:
-- the v0.1 experiment runner contract
-- =============================================================================
-- The database decides who owns an opportunity. Not process timing.
--
--   cron                 the clock
--   experiment runner    the orchestrator   (Package #5)
--   ingestion worker     the data collector (Package #3, untouched)
--
-- Package #3 must never learn that a Model v0.1 experiment exists. It owns
-- provider polling and market ingestion and nothing else; if v0.1 scheduling
-- logic leaked into it, the ingest layer would start carrying knowledge of an
-- experiment it has no business knowing about, and the two would have to be
-- versioned together forever after.
--
-- -----------------------------------------------------------------------------
-- TARGETED CAPTURE, NOT A POLLING WINDOW
-- -----------------------------------------------------------------------------
-- This is what makes automation safe. 056 documented a tension: if several
-- observations land inside +/-60m, "closest to T-24h" stops being answerable
-- without point-in-time reconstruction. Manual polling kept that theoretical.
-- Cron would have made it real within a day.
--
-- It is dissolved by treating the window as EXECUTION TOLERANCE rather than as
-- permission to collect candidates:
--
--     at the target, the runner performs ONE ingestion poll and resolves the
--     opportunity against that fresh state
--
--     +/-60m is how late the scheduled capture may actually execute -- it is
--     NOT an invitation to gather four competing observations and choose
--
-- One observation per event x selection, exactly as pre-registered, and nothing
-- to select after the fact.
--
-- Package #3's retry behaviour still retries a failed provider request within a
-- cycle. That is transport resilience; it does not create a second experiment
-- observation.
--
-- -----------------------------------------------------------------------------
-- ONE POLL SERVES MANY OPPORTUNITIES
-- -----------------------------------------------------------------------------
-- Sixteen games with two moneyline selections must not become 32 provider calls
-- for what is one board refresh. Enforced by CHECK, not by care: an experiment
-- run may perform AT MOST ONE ingestion poll, however many opportunities it
-- goes on to resolve.
--
-- -----------------------------------------------------------------------------
-- TWO INDEPENDENT GUARANTEES, DELIBERATELY NOT ONE
-- -----------------------------------------------------------------------------
--   claims          prevent duplicated WORK -- two overlapping cron invocations
--                   must not both fire an ingestion cycle for the same
--                   opportunities. A quota and efficiency concern.
--   attempt UNIQUE  prevents duplicated RECORDS -- from 056, and it holds even
--                   if the claim mechanism fails completely.
--
-- The second is what the denominator rests on. Keeping them separate means a
-- bug in leasing can waste credits but cannot corrupt the experimental record.
-- Resolution is therefore NOT gated on holding a live lease: the record-level
-- guarantee must not become dependent on the coordination-level one.
--
-- -----------------------------------------------------------------------------
-- CLAIMS ARE COORDINATION, NOT EVIDENCE
-- -----------------------------------------------------------------------------
-- model.formation_claims is the one MUTABLE table in Package #5. It has to be:
-- a lease must expire and be taken over after a crash. It carries no
-- experimental claim, which is exactly why it is not append-only while the
-- schedule and the attempts are.
--
-- -----------------------------------------------------------------------------
-- WHY resolve_v01 IS REDEFINED HERE RATHER THAN PATCHED
-- -----------------------------------------------------------------------------
-- formation_attempts is append-only (055). Stamping experiment_run_id onto the
-- attempt after resolution would be blocked by trg_formation_attempts_append_only
-- -- correctly. The attempt record is written ONCE, complete, so the run id has
-- to be an input to the insert rather than a later annotation. The full body
-- moves to the two-argument form and the 056 signature delegates, so existing
-- callers and tests are unaffected.
-- =============================================================================

CREATE TABLE model.experiment_runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    worker          TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    claimed_count   INT NOT NULL DEFAULT 0,
    ingestion_polls INT NOT NULL DEFAULT 0,
    resolved_count  INT NOT NULL DEFAULT 0,

    -- The invariant, enforced rather than trusted.
    CONSTRAINT ck_one_poll_per_cycle CHECK (ingestion_polls <= 1)
);

COMMENT ON TABLE model.experiment_runs IS
    'One row per runner cycle. ck_one_poll_per_cycle IS the "one poll serves '
    'many opportunities" invariant: a cycle may perform at most one ingestion '
    'poll however many opportunities it resolves.';

CREATE TABLE model.formation_claims (
    schedule_id      UUID PRIMARY KEY
                     REFERENCES model.formation_schedule(schedule_id),
    run_id           UUID REFERENCES model.experiment_runs(run_id),
    worker           TEXT NOT NULL,
    claimed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_expires_at TIMESTAMPTZ NOT NULL
);

COMMENT ON TABLE model.formation_claims IS
    'Coordination state, deliberately MUTABLE -- a lease must be able to expire '
    'and be taken over after a crash. Carries no experimental claim, which is '
    'why it is not append-only while the schedule and attempts are.';

ALTER TABLE model.formation_attempts
    ADD COLUMN experiment_run_id UUID REFERENCES model.experiment_runs(run_id);

-- -----------------------------------------------------------------------------
-- What is due: unresolved, and the capture window has opened.
--
-- An opportunity whose window has CLOSED still appears here. It has to: it must
-- terminate as NO_WINDOW_CAPTURE rather than quietly vanish from the work list,
-- or the denominator leaks exactly the games the collector failed on -- the most
-- likely place for a quiet bias to hide.
-- -----------------------------------------------------------------------------
CREATE VIEW model.due_opportunities
WITH (security_invoker = true) AS
SELECT s.schedule_id, s.model_id, s.model_version, s.event_id,
       s.market_type, s.selection_key, s.line,
       s.target_formation_at, s.window_seconds,
       round(extract(epoch FROM NOW() - s.target_formation_at))::int
           AS seconds_from_target,
       (abs(extract(epoch FROM NOW() - s.target_formation_at)) <= s.window_seconds)
           AS inside_window,
       c.worker           AS claimed_by,
       c.lease_expires_at,
       (c.schedule_id IS NOT NULL AND c.lease_expires_at > NOW()) AS actively_claimed
FROM model.formation_schedule s
LEFT JOIN model.formation_attempts a ON a.schedule_id = s.schedule_id
LEFT JOIN model.formation_claims   c ON c.schedule_id = s.schedule_id
WHERE a.attempt_id IS NULL
  AND NOW() >= s.target_formation_at - make_interval(secs => s.window_seconds);

COMMENT ON VIEW model.due_opportunities IS
    'Unresolved opportunities whose capture window has opened. Rows past their '
    'window remain visible so they terminate as NO_WINDOW_CAPTURE instead of '
    'disappearing from the denominator.';

CREATE FUNCTION model.start_experiment_run(p_worker TEXT)
RETURNS UUID
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
    INSERT INTO model.experiment_runs (worker) VALUES (p_worker) RETURNING run_id;
$fn$;

-- -----------------------------------------------------------------------------
-- Atomic claiming. The database decides ownership, not process timing.
--
-- ON CONFLICT ... DO UPDATE ... WHERE lease_expires_at < NOW() gives both halves
-- at once: a live lease is never stolen, and an expired one always can be. A
-- crashed worker therefore releases its work by itself, with no reaper job to
-- write, schedule, or forget to run.
--
-- Two workers racing for the same row: the second blocks on the primary key
-- until the first commits, then re-reads it, fails the WHERE against the fresh
-- lease, and returns nothing. Disjoint sets, with no advisory locks and no
-- application-side coordination.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.claim_due_opportunities(
    p_run_id     UUID,
    p_worker     TEXT,
    p_lease_secs INT DEFAULT 600,
    p_limit      INT DEFAULT 1000
)
RETURNS SETOF UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    v_n INT;
BEGIN
    RETURN QUERY
    WITH due AS (
        SELECT d.schedule_id
        FROM model.due_opportunities d
        WHERE NOT d.actively_claimed
        ORDER BY d.target_formation_at
        LIMIT p_limit
    ),
    taken AS (
        INSERT INTO model.formation_claims
            (schedule_id, run_id, worker, claimed_at, lease_expires_at)
        SELECT due.schedule_id, p_run_id, p_worker, NOW(),
               NOW() + make_interval(secs => p_lease_secs)
        FROM due
        ON CONFLICT (schedule_id) DO UPDATE
            SET run_id           = EXCLUDED.run_id,
                worker           = EXCLUDED.worker,
                claimed_at       = EXCLUDED.claimed_at,
                lease_expires_at = EXCLUDED.lease_expires_at
            WHERE model.formation_claims.lease_expires_at < NOW()
        RETURNING model.formation_claims.schedule_id
    )
    SELECT taken.schedule_id FROM taken;

    GET DIAGNOSTICS v_n = ROW_COUNT;
    UPDATE model.experiment_runs
       SET claimed_count = claimed_count + v_n
     WHERE run_id = p_run_id;
END;
$fn$;

COMMENT ON FUNCTION model.claim_due_opportunities IS
    'Atomically claims due, unclaimed opportunities under a lease. Overlapping '
    'workers receive disjoint sets; an expired lease is reclaimable, so crashed '
    'work recovers without a reaper job.';

-- -----------------------------------------------------------------------------
-- The runner declares its single poll BEFORE performing it, so a crash between
-- declaration and request cannot license a second one in the same cycle.
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.record_ingestion_poll(p_run_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
BEGIN
    UPDATE model.experiment_runs
       SET ingestion_polls = ingestion_polls + 1
     WHERE run_id = p_run_id;
EXCEPTION WHEN check_violation THEN
    RAISE EXCEPTION
        'ONE_POLL_PER_CYCLE: run % has already performed its ingestion poll. '
        'One poll serves every simultaneously due opportunity; polling per '
        'wager would turn one board refresh into dozens of provider calls.',
        p_run_id
        USING ERRCODE = 'check_violation';
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Resolution, attributable to the cycle that performed it.
--
-- This carries the whole 056 body rather than wrapping it, because the attempt
-- row is append-only and must be written once with experiment_run_id already in
-- place. The ordering that 056 exists to enforce is unchanged and load-bearing:
--
--     window capture  ->  market eligibility  ->  ONLY THEN invoke the model
-- -----------------------------------------------------------------------------
CREATE FUNCTION model.resolve_v01(p_schedule_id UUID, p_run_id UUID)
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

    -- The record-level guarantee, independent of any lease.
    IF EXISTS (SELECT 1 FROM model.formation_attempts
                WHERE schedule_id = p_schedule_id) THEN
        RAISE EXCEPTION
            'ALREADY_RESOLVED: schedule % has already terminated', p_schedule_id
            USING ERRCODE = 'unique_violation';
    END IF;

    SELECT current_scheduled_start INTO v_kick
      FROM public.events WHERE id = sc.event_id;

    -- 1. Did the scheduled capture actually execute inside the pre-registered
    --    window? A data-collection question, asked before any market question.
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
         seconds_from_target, seconds_to_kickoff, experiment_run_id)
    VALUES ('v01', sc.model_version, sc.event_id, sc.market_type,
            sc.selection_key, sc.line, v_reason, v_belief, v_quality, v_books,
            sc.schedule_id, sc.target_formation_at,
            CASE WHEN v_reason = 'ELIGIBLE' THEN v_obs_at END,
            round(extract(epoch FROM NOW() - sc.target_formation_at))::int,
            round(extract(epoch FROM v_kick - NOW()))::int,
            p_run_id);

    IF p_run_id IS NOT NULL THEN
        UPDATE model.experiment_runs
           SET resolved_count = resolved_count + 1
         WHERE run_id = p_run_id;
    END IF;

    -- Terminal: the opportunity can never be due again, so the claim is spent.
    DELETE FROM model.formation_claims WHERE schedule_id = p_schedule_id;

    RETURN v_reason;
END;
$fn$;

COMMENT ON FUNCTION model.resolve_v01(UUID, UUID) IS
    'Terminates one scheduled opportunity and attributes it to a runner cycle. '
    'Window capture is checked first, then market eligibility, and the model is '
    'invoked ONLY if the answer is ELIGIBLE -- so it can never see its own '
    'output and then decide whether the opportunity counted.';

-- The 056 signature is preserved and delegates: an unattributed resolution is
-- still a valid resolution (manual operation, or the runner itself failing over
-- to a bare call). experiment_run_id is then NULL, which reads correctly as
-- "resolved outside a runner cycle" rather than as missing data.
CREATE OR REPLACE FUNCTION model.resolve_v01(p_schedule_id UUID)
RETURNS model.eligibility_reason
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
    SELECT model.resolve_v01(p_schedule_id, NULL::uuid);
$fn$;

CREATE FUNCTION model.finish_experiment_run(p_run_id UUID)
RETURNS VOID
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
    UPDATE model.experiment_runs SET finished_at = NOW() WHERE run_id = p_run_id;
$fn$;

-- -----------------------------------------------------------------------------
-- Cycle-level audit. Makes the polling invariant checkable after the fact
-- rather than only enforceable at write time.
-- -----------------------------------------------------------------------------
CREATE VIEW model.runner_efficiency
WITH (security_invoker = true) AS
SELECT r.run_id, r.worker, r.started_at, r.finished_at,
       r.claimed_count, r.ingestion_polls, r.resolved_count,
       (SELECT count(*) FROM model.formation_attempts a
         WHERE a.experiment_run_id = r.run_id) AS attempts_recorded,
       CASE WHEN r.ingestion_polls = 0 THEN NULL
            ELSE r.resolved_count::numeric / r.ingestion_polls END
           AS opportunities_per_poll
FROM model.experiment_runs r;

COMMENT ON VIEW model.runner_efficiency IS
    'opportunities_per_poll is the quota story: it should rise with slate size, '
    'never sit at 1.0 across a full Sunday board.';

-- -----------------------------------------------------------------------------
-- Privileges.
--
-- The work queue is OPERATOR-ONLY, and olp_model is deliberately not on it.
-- due_opportunities is not a view of the experiment; it is a view of pending
-- work and lease state. A producer that could read it would gain advance sight
-- of which opportunities are about to arrive -- foreknowledge of the shape of
-- the upcoming population, which is precisely the kind of harmless-looking read
-- that lets a model condition on its own denominator. It already sees
-- formation_schedule and v01_ledger (056); that is the denominator, and it is
-- enough.
--
-- olp_grader gets it, along with SELECT on formation_claims -- required because
-- due_opportunities is a security_invoker view and joins that table, so a grant
-- on the view alone would hand out a read the grantee cannot perform.
-- -----------------------------------------------------------------------------
REVOKE ALL ON model.experiment_runs, model.formation_claims FROM PUBLIC;
REVOKE ALL ON FUNCTION model.start_experiment_run(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.claim_due_opportunities(UUID, TEXT, INT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.record_ingestion_poll(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.resolve_v01(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.resolve_v01(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION model.finish_experiment_run(UUID) FROM PUBLIC;

GRANT SELECT ON model.formation_claims  TO olp_grader;
GRANT SELECT ON model.due_opportunities TO olp_grader;
GRANT SELECT ON model.experiment_runs   TO olp_grader;
GRANT SELECT ON model.runner_efficiency TO olp_grader;

-- -----------------------------------------------------------------------------
-- Test hygiene. TRUNCATE public.events CASCADE already reaches the schedule,
-- the claims and the attempts through their foreign keys, but experiment_runs
-- has no path back to events -- so without this it would accumulate across
-- every test in the suite and quietly couple them together.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.reset()
RETURNS VOID
LANGUAGE plpgsql
AS $fn$
BEGIN
    TRUNCATE
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
