-- =============================================================================
-- 008_risk_reservations.sql -- Escrowed exposure
-- =============================================================================
-- Exactly ONE reservation record per ticket, for the life of the ticket. Its
-- status moves ACTIVE -> RELEASED / VOIDED. No redundant partial unique index
-- is added: ticket_id UNIQUE already guarantees one row per ticket.

CREATE TABLE public.risk_reservations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id   UUID UNIQUE NOT NULL
                    REFERENCES public.tickets(id)
                    ON DELETE RESTRICT,
    chapter_id  UUID NOT NULL
                    REFERENCES public.ledger_chapters(id)
                    ON DELETE RESTRICT,
    amount      NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    status      public.reservation_status NOT NULL DEFAULT 'ACTIVE',
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ
);

ALTER TABLE public.risk_reservations
    ADD CONSTRAINT ck_reservation_release_coherent CHECK (
        (status = 'ACTIVE' AND released_at IS NULL)
        OR
        (status IN ('RELEASED', 'VOIDED') AND released_at IS NOT NULL)
    );

-- Hot path: SUM(amount) WHERE chapter_id = ? AND status = 'ACTIVE'
CREATE INDEX idx_reservations_active_by_chapter
    ON public.risk_reservations (chapter_id)
    INCLUDE (amount)
    WHERE status = 'ACTIVE';

-- The escrowed amount and its ticket/chapter binding never change; only the
-- release transition is permitted, and it is one-way.
CREATE OR REPLACE FUNCTION public.olp_guard_reservation_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'APPEND_ONLY_VIOLATION: risk_reservations cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF (NEW.id, NEW.ticket_id, NEW.chapter_id, NEW.amount, NEW.reserved_at)
       IS DISTINCT FROM
       (OLD.id, OLD.ticket_id, OLD.chapter_id, OLD.amount, OLD.reserved_at)
    THEN
        RAISE EXCEPTION
            'IMMUTABLE_RESERVATION: only status/released_at may change'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status <> 'ACTIVE' AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION
            'IMMUTABLE_RESERVATION: a released reservation cannot be re-opened'
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_reservations_guard
    BEFORE UPDATE OR DELETE ON public.risk_reservations
    FOR EACH ROW EXECUTE FUNCTION public.olp_guard_reservation_update();
