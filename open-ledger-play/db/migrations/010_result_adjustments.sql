-- =============================================================================
-- 010_result_adjustments.sql -- Corrections append new facts, never replace old
-- =============================================================================
-- adjustment_seq exists for the same reason ingest_seq does: created_at can tie,
-- and the effective result depends on a TOTAL order. Never order corrections by
-- timestamp alone, and never by random UUID.

CREATE TABLE public.ticket_result_adjustments (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    adjustment_seq            BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    ticket_id                 UUID NOT NULL
                                  REFERENCES public.tickets(id)
                                  ON DELETE RESTRICT,
    previous_effective_result public.ticket_result_type NOT NULL,
    new_effective_result      public.ticket_result_type NOT NULL,
    pnl_delta                 NUMERIC(12,2) NOT NULL,
    reason_code               TEXT NOT NULL
                                  CHECK (length(btrim(reason_code)) > 0),
    reason_text               TEXT,
    correction_idempotency_key UUID UNIQUE NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by                TEXT NOT NULL
                                  CHECK (length(btrim(created_by)) > 0)
);

CREATE INDEX idx_adjustments_ticket
    ON public.ticket_result_adjustments (ticket_id, adjustment_seq);

CREATE TRIGGER trg_adjustments_append_only
    BEFORE UPDATE OR DELETE ON public.ticket_result_adjustments
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

COMMENT ON TABLE public.ticket_result_adjustments IS
    'APPEND-ONLY correction log. Effective result = last new_effective_result by '
    'adjustment_seq, or the original ticket_results.result when no rows exist.';
