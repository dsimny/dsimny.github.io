-- =============================================================================
-- 033_p3_provider_health.sql -- Package #3: durable provider health
-- =============================================================================
-- Package #2 is FROZEN. Nothing here changes its lifecycle semantics: this adds
-- new tables and new RPCs only, and no ingestion or postponement rule moves.
--
-- WHY CIRCUIT STATE LIVES IN THE DATABASE.
-- Retries and backoff are in-process concerns and stay in the worker. A circuit
-- breaker is not: the worker is expected to run as a cron tick or an Edge
-- Function invocation, so it is a fresh process every time. In-memory circuit
-- state would reset on every tick, which means a dead provider would be hammered
-- once per tick forever and the breaker would never actually break. Persisting
-- it is what makes the breaker real.
-- =============================================================================

CREATE TYPE public.circuit_state AS ENUM ('CLOSED', 'OPEN', 'HALF_OPEN');

CREATE TABLE public.provider_health (
    provider             TEXT PRIMARY KEY
                             CHECK (length(btrim(provider)) > 0),
    circuit              public.circuit_state NOT NULL DEFAULT 'CLOSED',
    consecutive_failures INT NOT NULL DEFAULT 0
                             CHECK (consecutive_failures >= 0),
    opened_at            TIMESTAMPTZ,
    last_success_at      TIMESTAMPTZ,
    last_failure_at      TIMESTAMPTZ,
    last_error           TEXT,
    -- Observed from the provider's own rate-limit headers.
    quota_remaining      INT,
    quota_used           INT,
    quota_observed_at    TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_open_circuit_has_timestamp CHECK (
        circuit <> 'OPEN' OR opened_at IS NOT NULL
    )
);

COMMENT ON TABLE public.provider_health IS
    'Durable per-provider circuit state. Survives worker restarts, which is the '
    'only way a breaker works for a stateless cron/serverless worker.';

-- -----------------------------------------------------------------------------
-- May we call the provider right now?
--
--   CLOSED     -> yes
--   OPEN       -> no, until the cooldown elapses; then one HALF_OPEN probe
--   HALF_OPEN  -> yes, this is the probe
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.provider_attempt_begin_rpc(
    p_provider          TEXT,
    p_failure_threshold INT DEFAULT 5,
    p_cooldown_seconds  INT DEFAULT 300
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_row       public.provider_health%ROWTYPE;
    v_elapsed   NUMERIC;
    v_retry_in  NUMERIC := 0;
BEGIN
    IF p_provider IS NULL OR btrim(p_provider) = '' THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_provider is required';
    END IF;

    INSERT INTO public.provider_health (provider)
    VALUES (p_provider)
    ON CONFLICT (provider) DO NOTHING;

    SELECT * INTO v_row
      FROM public.provider_health
     WHERE provider = p_provider
     FOR UPDATE;

    IF v_row.circuit = 'OPEN' THEN
        v_elapsed := extract(epoch FROM (NOW() - v_row.opened_at));

        IF v_elapsed < p_cooldown_seconds THEN
            v_retry_in := round(p_cooldown_seconds - v_elapsed, 2);
            RETURN jsonb_build_object(
                'allowed', FALSE,
                'circuit', 'OPEN',
                'reason', 'CIRCUIT_OPEN',
                'retry_in_seconds', v_retry_in,
                'consecutive_failures', v_row.consecutive_failures);
        END IF;

        UPDATE public.provider_health
           SET circuit = 'HALF_OPEN', updated_at = NOW()
         WHERE provider = p_provider;

        RETURN jsonb_build_object(
            'allowed', TRUE, 'circuit', 'HALF_OPEN', 'reason', 'PROBE',
            'retry_in_seconds', 0,
            'consecutive_failures', v_row.consecutive_failures);
    END IF;

    RETURN jsonb_build_object(
        'allowed', TRUE,
        'circuit', v_row.circuit::text,
        'reason', 'OK',
        'retry_in_seconds', 0,
        'consecutive_failures', v_row.consecutive_failures);
END;
$fn$;

-- -----------------------------------------------------------------------------
-- A successful call closes the circuit and records observed quota.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.provider_attempt_success_rpc(
    p_provider        TEXT,
    p_quota_remaining INT DEFAULT NULL,
    p_quota_used      INT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_row public.provider_health%ROWTYPE;
BEGIN
    INSERT INTO public.provider_health (provider)
    VALUES (p_provider)
    ON CONFLICT (provider) DO NOTHING;

    UPDATE public.provider_health
       SET circuit              = 'CLOSED',
           consecutive_failures = 0,
           opened_at            = NULL,
           last_success_at      = NOW(),
           last_error           = NULL,
           quota_remaining      = COALESCE(p_quota_remaining, quota_remaining),
           quota_used           = COALESCE(p_quota_used, quota_used),
           quota_observed_at    = CASE WHEN p_quota_remaining IS NOT NULL
                                       THEN NOW() ELSE quota_observed_at END,
           updated_at           = NOW()
     WHERE provider = p_provider
    RETURNING * INTO v_row;

    RETURN jsonb_build_object(
        'circuit', v_row.circuit::text,
        'quota_remaining', v_row.quota_remaining);
END;
$fn$;

-- -----------------------------------------------------------------------------
-- A failure counts toward the threshold. A failed HALF_OPEN probe re-opens the
-- circuit immediately -- one probe is the whole point of half-open.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.provider_attempt_failure_rpc(
    p_provider          TEXT,
    p_error             TEXT,
    p_failure_threshold INT DEFAULT 5
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_row      public.provider_health%ROWTYPE;
    v_failures INT;
    v_circuit  public.circuit_state;
BEGIN
    INSERT INTO public.provider_health (provider)
    VALUES (p_provider)
    ON CONFLICT (provider) DO NOTHING;

    SELECT * INTO v_row
      FROM public.provider_health
     WHERE provider = p_provider
     FOR UPDATE;

    v_failures := v_row.consecutive_failures + 1;

    v_circuit := CASE
                     WHEN v_row.circuit = 'HALF_OPEN' THEN 'OPEN'
                     WHEN v_failures >= p_failure_threshold THEN 'OPEN'
                     ELSE 'CLOSED'
                 END::public.circuit_state;

    UPDATE public.provider_health
       SET circuit              = v_circuit,
           consecutive_failures = v_failures,
           opened_at            = CASE WHEN v_circuit = 'OPEN'
                                       THEN COALESCE(
                                           CASE WHEN v_row.circuit = 'OPEN'
                                                THEN v_row.opened_at END,
                                           NOW())
                                       ELSE NULL END,
           last_failure_at      = NOW(),
           last_error           = left(COALESCE(p_error, 'unknown'), 2000),
           updated_at           = NOW()
     WHERE provider = p_provider;

    RETURN jsonb_build_object(
        'circuit', v_circuit::text,
        'consecutive_failures', v_failures,
        'tripped', (v_circuit = 'OPEN' AND v_row.circuit <> 'OPEN'));
END;
$fn$;

-- Manual reset, for an operator who has fixed the underlying problem.
CREATE OR REPLACE FUNCTION public.provider_reset_circuit_rpc(p_provider TEXT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    UPDATE public.provider_health
       SET circuit = 'CLOSED', consecutive_failures = 0,
           opened_at = NULL, updated_at = NOW()
     WHERE provider = p_provider;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Privileges: operational data, service role only.
-- -----------------------------------------------------------------------------
REVOKE ALL ON public.provider_health FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.provider_health TO service_role;
ALTER TABLE public.provider_health ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON FUNCTION public.provider_attempt_begin_rpc(TEXT, INT, INT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provider_attempt_begin_rpc(TEXT, INT, INT)
    TO service_role;

REVOKE ALL ON FUNCTION public.provider_attempt_success_rpc(TEXT, INT, INT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provider_attempt_success_rpc(TEXT, INT, INT)
    TO service_role;

REVOKE ALL ON FUNCTION public.provider_attempt_failure_rpc(TEXT, TEXT, INT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provider_attempt_failure_rpc(TEXT, TEXT, INT)
    TO service_role;

REVOKE ALL ON FUNCTION public.provider_reset_circuit_rpc(TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provider_reset_circuit_rpc(TEXT)
    TO service_role;
