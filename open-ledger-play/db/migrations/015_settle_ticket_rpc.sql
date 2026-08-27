-- =============================================================================
-- 015_settle_ticket_rpc.sql
-- =============================================================================
-- TRUSTED BACKEND / SERVICE ROLE ONLY. Never granted to `authenticated`.
--
-- Lock order (must match every other financial RPC):
--   resolve chapter id -> chapter FOR UPDATE -> ticket FOR UPDATE
--
-- Idempotency is decided on the ORIGINAL result:
--   same result      -> idempotent success
--   different result -> SETTLEMENT_CONFLICT (never silently resolved)
-- =============================================================================

CREATE OR REPLACE FUNCTION public.settle_ticket_rpc(
    p_ticket_id                  UUID,
    p_result                     public.ticket_result_type,
    p_grading_source             TEXT,
    p_settlement_idempotency_key UUID
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_chapter_id   UUID;
    v_ticket       public.tickets%ROWTYPE;
    v_existing     public.ticket_results%ROWTYPE;
    v_pnl          NUMERIC(12,2);
    v_result_id    UUID;
    v_reservation_status public.reservation_status;
BEGIN
    IF p_ticket_id IS NULL OR p_result IS NULL
       OR p_settlement_idempotency_key IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: ticket, result and idempotency key are required';
    END IF;

    IF p_grading_source IS NULL OR btrim(p_grading_source) = '' THEN
        RAISE EXCEPTION 'INVALID_INPUT: grading_source is required';
    END IF;

    -- 1. Resolve the chapter, then take locks in the canonical order ---------
    SELECT chapter_id INTO v_chapter_id
      FROM public.tickets
     WHERE id = p_ticket_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'TICKET_NOT_FOUND: unknown ticket';
    END IF;

    PERFORM 1
       FROM public.ledger_chapters
      WHERE id = v_chapter_id
      FOR UPDATE;

    SELECT * INTO v_ticket
      FROM public.tickets
     WHERE id = p_ticket_id
     FOR UPDATE;

    -- 2. Original settlement already present? --------------------------------
    SELECT * INTO v_existing
      FROM public.ticket_results
     WHERE ticket_id = p_ticket_id;

    IF FOUND THEN
        IF v_existing.result = p_result THEN
            -- Idempotent replay. Exactly one settlement still exists.
            RETURN v_existing.id;
        END IF;

        RAISE EXCEPTION
            'SETTLEMENT_CONFLICT: ticket already settled as %, refusing to grade as %',
            v_existing.result, p_result
            USING ERRCODE = 'unique_violation';
    END IF;

    -- 3. P/L from the ticket's FROZEN economics ------------------------------
    v_pnl := public.olp_result_pnl(p_result, v_ticket.risk, v_ticket.potential_profit);

    INSERT INTO public.ticket_results (
        ticket_id, result, pnl, grading_source, settlement_idempotency_key
    )
    VALUES (
        p_ticket_id, p_result, v_pnl, p_grading_source, p_settlement_idempotency_key
    )
    RETURNING id INTO v_result_id;

    -- 4. Wallet effect. PUSH and VOID each write an explicit 0.00 row so the
    --    settlement remains a visible fact in the ledger.
    INSERT INTO public.wallet_transactions (
        user_id, chapter_id, ticket_id, transaction_type, amount, idempotency_key
    )
    VALUES (
        v_ticket.user_id,
        v_ticket.chapter_id,
        p_ticket_id,
        public.olp_settlement_tx_type(p_result),
        v_pnl,
        p_settlement_idempotency_key
    );

    -- 5. Release escrow ------------------------------------------------------
    v_reservation_status := CASE WHEN p_result = 'VOID'
                                 THEN 'VOIDED'::public.reservation_status
                                 ELSE 'RELEASED'::public.reservation_status
                            END;

    UPDATE public.risk_reservations
       SET status      = v_reservation_status,
           released_at = NOW()
     WHERE ticket_id = p_ticket_id
       AND status    = 'ACTIVE';

    -- 6. Transition the ticket ----------------------------------------------
    UPDATE public.tickets
       SET status     = CASE WHEN p_result = 'VOID'
                             THEN 'VOIDED'::public.ticket_status
                             ELSE 'SETTLED'::public.ticket_status
                        END,
           closed_at  = COALESCE(closed_at, NOW()),
           settled_at = NOW()
     WHERE id = p_ticket_id;

    RETURN v_result_id;

EXCEPTION
    WHEN unique_violation THEN
        IF SQLERRM LIKE 'SETTLEMENT_CONFLICT%' THEN
            RAISE;
        END IF;

        -- Concurrent grader won the race between our check and our insert.
        SELECT * INTO v_existing
          FROM public.ticket_results
         WHERE ticket_id = p_ticket_id;

        IF FOUND THEN
            IF v_existing.result = p_result THEN
                RETURN v_existing.id;
            END IF;
            RAISE EXCEPTION
                'SETTLEMENT_CONFLICT: ticket already settled as %, refusing to grade as %',
                v_existing.result, p_result
                USING ERRCODE = 'unique_violation';
        END IF;

        RAISE EXCEPTION
            'SETTLEMENT_KEY_REUSED: this settlement idempotency key belongs to another ticket'
            USING ERRCODE = 'unique_violation';
END;
$fn$;

REVOKE ALL ON FUNCTION
    public.settle_ticket_rpc(UUID, public.ticket_result_type, TEXT, UUID)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION
    public.settle_ticket_rpc(UUID, public.ticket_result_type, TEXT, UUID)
    FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION
    public.settle_ticket_rpc(UUID, public.ticket_result_type, TEXT, UUID)
    TO service_role;
