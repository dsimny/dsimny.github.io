-- =============================================================================
-- 005_events_schedule_history.sql -- Events + immutable schedule audit trail
-- =============================================================================

CREATE TABLE public.events (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_event_id          TEXT UNIQUE NOT NULL,
    sport                    TEXT NOT NULL DEFAULT 'NFL',
    league                   TEXT NOT NULL DEFAULT 'NFL',
    home_team                TEXT NOT NULL,
    away_team                TEXT NOT NULL,
    original_scheduled_start TIMESTAMPTZ NOT NULL,
    current_scheduled_start  TIMESTAMPTZ NOT NULL,
    actual_start_time        TIMESTAMPTZ,
    is_live                  BOOLEAN NOT NULL DEFAULT FALSE,
    is_closed                BOOLEAN NOT NULL DEFAULT FALSE,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_start ON public.events (current_scheduled_start);
CREATE INDEX idx_events_open  ON public.events (current_scheduled_start)
    WHERE is_closed = FALSE AND is_live = FALSE;

-- original_scheduled_start is a historical fact and must never drift.
CREATE OR REPLACE FUNCTION public.olp_freeze_event_origin()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF NEW.original_scheduled_start IS DISTINCT FROM OLD.original_scheduled_start THEN
        RAISE EXCEPTION
            'IMMUTABLE_FIELD: events.original_scheduled_start cannot be changed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_events_freeze_origin
    BEFORE UPDATE ON public.events
    FOR EACH ROW EXECUTE FUNCTION public.olp_freeze_event_origin();

-- -----------------------------------------------------------------------------
-- Schedule history
-- -----------------------------------------------------------------------------
CREATE TABLE public.event_schedule_history (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    history_seq          BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    event_id             UUID NOT NULL
                             REFERENCES public.events(id)
                             ON DELETE RESTRICT,
    scheduled_start_time TIMESTAMPTZ NOT NULL,
    effective_from       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source               TEXT NOT NULL,
    reason               TEXT,
    recorded_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_schedule_history_event
    ON public.event_schedule_history (event_id, history_seq DESC);

CREATE TRIGGER trg_schedule_history_append_only
    BEFORE UPDATE OR DELETE ON public.event_schedule_history
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- EVERY change to current_scheduled_start produces a history row. Enforced by
-- trigger so no ingestion path can forget it. The attributing source/reason are
-- passed through session GUCs by the service-role ingestion RPCs.
-- -----------------------------------------------------------------------------
-- SECURITY DEFINER: the audit row must be written even when the caller has no
-- INSERT privilege on event_schedule_history. An audit trail that can be
-- defeated by revoking a grant is not an audit trail.
CREATE OR REPLACE FUNCTION public.olp_log_schedule_change()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.current_scheduled_start IS NOT DISTINCT FROM OLD.current_scheduled_start
    THEN
        RETURN NULL;
    END IF;

    INSERT INTO public.event_schedule_history (
        event_id, scheduled_start_time, effective_from, source, reason
    )
    VALUES (
        NEW.id,
        NEW.current_scheduled_start,
        NOW(),
        COALESCE(NULLIF(current_setting('olp.schedule_source', true), ''),
                 CASE WHEN TG_OP = 'INSERT' THEN 'INITIAL_SCHEDULE' ELSE 'SYSTEM' END),
        NULLIF(current_setting('olp.schedule_reason', true), '')
    );

    RETURN NULL;
END;
$fn$;

CREATE TRIGGER trg_events_log_schedule
    AFTER INSERT OR UPDATE OF current_scheduled_start ON public.events
    FOR EACH ROW EXECUTE FUNCTION public.olp_log_schedule_change();
