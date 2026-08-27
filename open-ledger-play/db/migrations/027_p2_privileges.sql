-- =============================================================================
-- 027_p2_privileges.sql -- Run bookkeeping + Package #2 authorization
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Ingestion run bookkeeping
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.start_ingestion_run_rpc(
    p_source_provider TEXT,
    p_kind            public.ingestion_kind
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_id UUID;
BEGIN
    IF p_source_provider IS NULL OR btrim(p_source_provider) = '' THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_source_provider is required';
    END IF;

    INSERT INTO public.ingestion_runs (source_provider, kind)
    VALUES (p_source_provider, p_kind)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$fn$;

CREATE OR REPLACE FUNCTION public.finish_ingestion_run_rpc(
    p_run_id          UUID,
    p_status          public.ingestion_status,
    p_error_text      TEXT DEFAULT NULL,
    p_events_upserted INT  DEFAULT 0
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF p_status = 'RUNNING' THEN
        RAISE EXCEPTION 'INVALID_INPUT: a run cannot be finished as RUNNING';
    END IF;
    IF p_status = 'FAILED' AND (p_error_text IS NULL OR btrim(p_error_text) = '') THEN
        RAISE EXCEPTION 'INVALID_INPUT: a FAILED run must carry an error_text';
    END IF;

    UPDATE public.ingestion_runs
       SET status          = p_status,
           finished_at     = NOW(),
           error_text      = p_error_text,
           events_upserted = events_upserted + COALESCE(p_events_upserted, 0)
     WHERE id = p_run_id
       AND status = 'RUNNING';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'RUN_NOT_OPEN: no RUNNING ingestion run with that id';
    END IF;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Table privileges. Same principle as migration 012: stated, never inherited.
--
-- Both tables are operational rather than public. event_lifecycle_log carries
-- aggregate detail (how many tickets a postponement voided), which is platform
-- data, not market data -- so unlike `events` and `event_schedule_history` it is
-- NOT exposed to clients. Users learn about a postponement from the event and
-- its schedule history, which Package #1 already made public.
-- -----------------------------------------------------------------------------
REVOKE ALL ON public.ingestion_runs      FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.event_lifecycle_log FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT, UPDATE ON public.ingestion_runs      TO service_role;
GRANT SELECT                 ON public.event_lifecycle_log TO service_role;

ALTER TABLE public.ingestion_runs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.event_lifecycle_log ENABLE ROW LEVEL SECURITY;
-- No policies are defined for anon/authenticated: RLS denies by default, so a
-- future accidental GRANT still yields zero rows.

-- -----------------------------------------------------------------------------
-- Function privileges
-- -----------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.start_ingestion_run_rpc(TEXT, public.ingestion_kind)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.start_ingestion_run_rpc(TEXT, public.ingestion_kind)
    TO service_role;

REVOKE ALL ON FUNCTION public.finish_ingestion_run_rpc(
    UUID, public.ingestion_status, TEXT, INT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_ingestion_run_rpc(
    UUID, public.ingestion_status, TEXT, INT) TO service_role;
