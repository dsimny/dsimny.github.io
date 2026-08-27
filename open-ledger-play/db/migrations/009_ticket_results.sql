-- =============================================================================
-- 009_ticket_results.sql -- The ORIGINAL settlement. Append-once. Immutable.
-- =============================================================================
-- Nothing -- not a client, not an admin, not a correction RPC -- may ever
-- UPDATE or DELETE a row here. Corrections append to
-- ticket_result_adjustments instead.

CREATE TABLE public.ticket_results (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id                  UUID UNIQUE NOT NULL
                                   REFERENCES public.tickets(id)
                                   ON DELETE RESTRICT,
    result                     public.ticket_result_type NOT NULL,
    pnl                        NUMERIC(12,2) NOT NULL,
    grading_source             TEXT NOT NULL,
    settlement_idempotency_key UUID UNIQUE NOT NULL,
    settled_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Neutral outcomes are exactly zero P/L.
ALTER TABLE public.ticket_results
    ADD CONSTRAINT ck_neutral_result_zero_pnl CHECK (
        result NOT IN ('PUSH', 'VOID') OR pnl = 0
    );

ALTER TABLE public.ticket_results
    ADD CONSTRAINT ck_win_pnl_positive CHECK (result <> 'WIN'  OR pnl > 0);

ALTER TABLE public.ticket_results
    ADD CONSTRAINT ck_loss_pnl_negative CHECK (result <> 'LOSS' OR pnl < 0);

CREATE TRIGGER trg_ticket_results_append_only
    BEFORE UPDATE OR DELETE ON public.ticket_results
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();

COMMENT ON TABLE public.ticket_results IS
    'APPEND-ONCE. The original graded result, preserved verbatim forever. '
    'Corrections are appended to ticket_result_adjustments; this table is never updated.';
