-- =============================================================================
-- 007_tickets.sql -- Tickets (accepted economics are frozen at insert)
-- =============================================================================

CREATE TABLE public.tickets (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                   UUID NOT NULL
                                  REFERENCES public.users(id)
                                  ON DELETE RESTRICT,
    chapter_id                UUID NOT NULL
                                  REFERENCES public.ledger_chapters(id)
                                  ON DELETE RESTRICT,
    event_id                  UUID NOT NULL
                                  REFERENCES public.events(id)
                                  ON DELETE RESTRICT,
    market_snapshot_id        UUID NOT NULL
                                  REFERENCES public.market_snapshots(id)
                                  ON DELETE RESTRICT,
    market_type               public.market_type NOT NULL,
    selection                 TEXT NOT NULL,
    accepted_line             NUMERIC(7,2),
    accepted_price            INT NOT NULL
                                  CHECK (accepted_price <= -100 OR accepted_price >= 100),
    accepted_sportsbook       TEXT NOT NULL,
    snapshot_captured_at      TIMESTAMPTZ NOT NULL,
    risk                      NUMERIC(12,2) NOT NULL CHECK (risk > 0),
    potential_profit          NUMERIC(12,2) NOT NULL CHECK (potential_profit > 0),
    submission_idempotency_key UUID NOT NULL,
    status                    public.ticket_status NOT NULL DEFAULT 'ACCEPTED',
    submitted_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at                 TIMESTAMPTZ,
    settled_at                TIMESTAMPTZ,
    CONSTRAINT uq_user_idempotency
        UNIQUE (user_id, submission_idempotency_key)
);

CREATE INDEX idx_tickets_chapter ON public.tickets (chapter_id, status);
CREATE INDEX idx_tickets_user    ON public.tickets (user_id, submitted_at DESC);
CREATE INDEX idx_tickets_event   ON public.tickets (event_id, status);
CREATE INDEX idx_tickets_open    ON public.tickets (event_id)
    WHERE status = 'ACCEPTED';

-- -----------------------------------------------------------------------------
-- Accepted economics are immutable after insert. Only lifecycle columns
-- (status / closed_at / settled_at) may ever move.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_freeze_ticket_economics()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'APPEND_ONLY_VIOLATION: tickets cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF (NEW.id, NEW.user_id, NEW.chapter_id, NEW.event_id, NEW.market_snapshot_id,
        NEW.market_type, NEW.selection, NEW.accepted_line, NEW.accepted_price,
        NEW.accepted_sportsbook, NEW.snapshot_captured_at, NEW.risk,
        NEW.potential_profit, NEW.submission_idempotency_key,
        NEW.submitted_at, NEW.accepted_at)
       IS DISTINCT FROM
       (OLD.id, OLD.user_id, OLD.chapter_id, OLD.event_id, OLD.market_snapshot_id,
        OLD.market_type, OLD.selection, OLD.accepted_line, OLD.accepted_price,
        OLD.accepted_sportsbook, OLD.snapshot_captured_at, OLD.risk,
        OLD.potential_profit, OLD.submission_idempotency_key,
        OLD.submitted_at, OLD.accepted_at)
    THEN
        RAISE EXCEPTION
            'IMMUTABLE_TICKET: accepted economics cannot be modified after acceptance'
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_tickets_freeze_economics
    BEFORE UPDATE OR DELETE ON public.tickets
    FOR EACH ROW EXECUTE FUNCTION public.olp_freeze_ticket_economics();
