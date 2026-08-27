-- =============================================================================
-- 020_p2_config_and_provenance.sql -- Package #2 foundations
-- =============================================================================
-- Package #2 connects the hardened ledger to real schedules and odds. It adds
-- NOTHING to the financial tables: chapters, wallet transactions, tickets,
-- reservations, results and adjustments are untouched by this package.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Ingestion policy
-- -----------------------------------------------------------------------------
ALTER TABLE public.system_settings
    ADD COLUMN postponement_void_hours INT NOT NULL DEFAULT 48
        CHECK (postponement_void_hours > 0),
    ADD COLUMN snapshot_refresh_seconds INT NOT NULL DEFAULT 60
        CHECK (snapshot_refresh_seconds > 0);

-- CRITICAL INTERACTION WITH PACKAGE #1.
--
-- Ingestion de-duplicates unchanged quotes so the immutable history stays
-- meaningful rather than one row per poll. But place_ticket_rpc requires the
-- newest quote to be younger than snapshot_ttl_seconds. If de-duplication were
-- unbounded, a market whose price simply had not moved would age out of its own
-- TTL and placement would fail with SNAPSHOT_STALE on a perfectly valid line.
--
-- So an unchanged quote is still re-recorded once snapshot_refresh_seconds has
-- elapsed, and that interval is constrained to stay strictly inside the TTL.
-- This constraint is the thing that stops the two policies drifting apart.
ALTER TABLE public.system_settings
    ADD CONSTRAINT ck_refresh_inside_ttl
        CHECK (snapshot_refresh_seconds < snapshot_ttl_seconds);

-- -----------------------------------------------------------------------------
-- 2. Ingestion provenance -- every run is accountable.
-- -----------------------------------------------------------------------------
CREATE TYPE public.ingestion_kind AS ENUM ('SCHEDULE', 'ODDS');

CREATE TYPE public.ingestion_status AS ENUM ('RUNNING', 'SUCCEEDED', 'FAILED');

CREATE TABLE public.ingestion_runs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_seq            BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    source_provider    TEXT NOT NULL CHECK (length(btrim(source_provider)) > 0),
    kind               public.ingestion_kind NOT NULL,
    status             public.ingestion_status NOT NULL DEFAULT 'RUNNING',
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at        TIMESTAMPTZ,
    events_upserted    INT NOT NULL DEFAULT 0 CHECK (events_upserted    >= 0),
    snapshots_written  INT NOT NULL DEFAULT 0 CHECK (snapshots_written  >= 0),
    snapshots_skipped  INT NOT NULL DEFAULT 0 CHECK (snapshots_skipped  >= 0),
    error_text         TEXT,
    CONSTRAINT ck_run_completion CHECK (
        (status = 'RUNNING'   AND finished_at IS NULL)
        OR
        (status IN ('SUCCEEDED', 'FAILED') AND finished_at IS NOT NULL)
    ),
    CONSTRAINT ck_failed_has_reason CHECK (
        status <> 'FAILED' OR error_text IS NOT NULL
    )
);

CREATE INDEX idx_ingestion_runs_recent
    ON public.ingestion_runs (kind, started_at DESC);

-- -----------------------------------------------------------------------------
-- 3. Event lifecycle audit.
--
-- public.events is a Package #1 table and is deliberately NOT given new
-- columns. Lifecycle facts are appended here instead, which keeps the frozen
-- table frozen and gives the same append-only guarantees the ledger has.
-- -----------------------------------------------------------------------------
CREATE TYPE public.event_lifecycle_action AS ENUM (
    'INGESTED',
    'RESCHEDULED',
    'POSTPONED',
    'KICKED_OFF',
    'CLOSING_LINE_CAPTURED',
    'CLOSED',
    'CANCELLED',
    'TICKETS_VOIDED'
);

CREATE TABLE public.event_lifecycle_log (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_seq    BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    event_id   UUID NOT NULL
                   REFERENCES public.events(id)
                   ON DELETE RESTRICT,
    action     public.event_lifecycle_action NOT NULL,
    detail     JSONB,
    source     TEXT NOT NULL CHECK (length(btrim(source)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_event_lifecycle_event
    ON public.event_lifecycle_log (event_id, log_seq);

CREATE TRIGGER trg_event_lifecycle_append_only
    BEFORE UPDATE OR DELETE ON public.event_lifecycle_log
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

CREATE TRIGGER trg_ingestion_runs_no_delete
    BEFORE DELETE ON public.ingestion_runs
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

-- -----------------------------------------------------------------------------
-- 4. Internal helper: append a lifecycle fact.
-- SECURITY DEFINER for the same reason the schedule-history trigger is --
-- an audit trail that a missing grant can defeat is not an audit trail.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_log_lifecycle(
    p_event_id UUID,
    p_action   public.event_lifecycle_action,
    p_source   TEXT,
    p_detail   JSONB DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    INSERT INTO public.event_lifecycle_log (event_id, action, source, detail)
    VALUES (p_event_id, p_action, COALESCE(NULLIF(btrim(p_source), ''), 'SYSTEM'), p_detail);
END;
$fn$;

REVOKE ALL ON FUNCTION public.olp_log_lifecycle(
    UUID, public.event_lifecycle_action, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
