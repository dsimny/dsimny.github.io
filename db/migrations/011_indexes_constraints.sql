-- =============================================================================
-- 011_indexes_constraints.sql -- Close the wallet FK + shared pure helpers
-- =============================================================================

-- Deferred from 004: tickets did not exist yet.
ALTER TABLE public.wallet_transactions
    ADD CONSTRAINT fk_wallet_transaction_ticket
    FOREIGN KEY (ticket_id)
    REFERENCES public.tickets(id)
    ON DELETE RESTRICT;

-- -----------------------------------------------------------------------------
-- American-odds profit. The single definition of the risk->profit relationship
-- used by placement; settlement and corrections then read the FROZEN
-- potential_profit off the ticket rather than recomputing it.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_american_profit(
    p_risk  NUMERIC,
    p_price INT
)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT round(
        CASE
            WHEN p_price >= 100 THEN p_risk * (p_price::numeric / 100.0)
            ELSE                      p_risk * (100.0 / abs(p_price)::numeric)
        END,
        2
    );
$fn$;

-- -----------------------------------------------------------------------------
-- P/L for a given outcome, from the ticket's frozen economics.
-- WIN  -> +potential_profit   LOSS -> -risk   PUSH/VOID -> 0
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_result_pnl(
    p_result           public.ticket_result_type,
    p_risk             NUMERIC,
    p_potential_profit NUMERIC
)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT CASE p_result
               WHEN 'WIN'  THEN  p_potential_profit
               WHEN 'LOSS' THEN -p_risk
               ELSE 0::numeric
           END::numeric(12,2);
$fn$;

-- Settlement transaction type for an outcome.
CREATE OR REPLACE FUNCTION public.olp_settlement_tx_type(
    p_result public.ticket_result_type
)
RETURNS public.transaction_type
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT CASE p_result
               WHEN 'WIN'  THEN 'SETTLEMENT_WIN'
               WHEN 'LOSS' THEN 'SETTLEMENT_LOSS'
               WHEN 'PUSH' THEN 'SETTLEMENT_PUSH'
               WHEN 'VOID' THEN 'SETTLEMENT_VOID'
           END::public.transaction_type;
$fn$;
