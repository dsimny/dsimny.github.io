-- =============================================================================
-- 004_wallet_transactions.sql -- THE balance source of truth
-- =============================================================================
-- Balance is ALWAYS: SELECT COALESCE(SUM(amount),0) WHERE chapter_id = ...
-- It is NEVER starting_capital + SUM(...). There is no mutable balance column
-- anywhere in this schema, by design.

CREATE TABLE public.wallet_transactions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL
                         REFERENCES public.users(id)
                         ON DELETE RESTRICT,
    chapter_id       UUID NOT NULL
                         REFERENCES public.ledger_chapters(id)
                         ON DELETE RESTRICT,
    ticket_id        UUID,                 -- FK added in 011, after tickets exist
    transaction_type public.transaction_type NOT NULL,
    amount           NUMERIC(12,2) NOT NULL,
    idempotency_key  UUID UNIQUE NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Exactly one opening credit per chapter. This is the structural defence
-- against the 20,000 double-credit bug.
CREATE UNIQUE INDEX uq_chapter_open_transaction
    ON public.wallet_transactions (chapter_id)
    WHERE transaction_type = 'CHAPTER_OPEN';

ALTER TABLE public.wallet_transactions
    ADD CONSTRAINT ck_chapter_open_is_positive CHECK (
        transaction_type <> 'CHAPTER_OPEN' OR amount > 0
    );

-- PUSH and VOID settle to exactly zero -- the row must still exist as a fact.
ALTER TABLE public.wallet_transactions
    ADD CONSTRAINT ck_neutral_settlements_are_zero CHECK (
        transaction_type NOT IN ('SETTLEMENT_PUSH', 'SETTLEMENT_VOID')
        OR amount = 0
    );

-- Every settlement-family row must name the ticket it settles.
ALTER TABLE public.wallet_transactions
    ADD CONSTRAINT ck_settlement_has_ticket CHECK (
        transaction_type = 'CHAPTER_OPEN' OR ticket_id IS NOT NULL
    );

ALTER TABLE public.wallet_transactions
    ADD CONSTRAINT ck_chapter_open_has_no_ticket CHECK (
        transaction_type <> 'CHAPTER_OPEN' OR ticket_id IS NULL
    );

CREATE INDEX idx_wallet_tx_chapter ON public.wallet_transactions (chapter_id);
CREATE INDEX idx_wallet_tx_user    ON public.wallet_transactions (user_id, created_at DESC);
CREATE INDEX idx_wallet_tx_ticket  ON public.wallet_transactions (ticket_id)
    WHERE ticket_id IS NOT NULL;

-- Append-only ledger: no UPDATE or DELETE, by anyone, ever.
CREATE OR REPLACE FUNCTION public.olp_block_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    RAISE EXCEPTION
        'APPEND_ONLY_VIOLATION: % on %.% is not permitted',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$fn$;

CREATE TRIGGER trg_wallet_transactions_append_only
    BEFORE UPDATE OR DELETE ON public.wallet_transactions
    FOR EACH ROW EXECUTE FUNCTION public.olp_block_mutation();
