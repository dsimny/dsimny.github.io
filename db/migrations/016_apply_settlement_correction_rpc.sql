-- =============================================================================
-- 016_apply_settlement_correction_rpc.sql
-- =============================================================================
-- SERVICE / ADMIN ONLY.
--
-- Corrections APPEND new facts. This function contains no UPDATE against
-- public.ticket_results and never will -- the original graded result is a
-- permanent historical record.
--
-- Lock order: chapter FOR UPDATE -> ticket FOR UPDATE (same as settlement, so
-- a correction and a placement on the same chapter serialize rather than race).
-- =============================================================================

CREATE OR REPLACE FUNCTION public.apply_settlement_correction_rpc(
    p_ticket_id                  UUID,
    p_new_result                 public.ticket_result_type,
    p_reason_code                TEXT,
    p_reason_text                TEXT,
    p_correction_idempotency_key UUID,
    p_created_by                 TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_chapter_id        UUID;
    v_chapter_status    public.chapter_status;
    v_ticket            public.tickets%ROWTYPE;
    v_original          public.ticket_results%ROWTYPE;
    v_effective_result  public.ticket_result_type;
    v_effective_pnl     NUMERIC(12,2);
    v_new_pnl           NUMERIC(12,2);
    v_delta             NUMERIC(12,2);
    v_adjustment_id     UUID;
    v_settled           NUMERIC(12,2);
    v_escrow            NUMERIC(12,2);
    v_available         NUMERIC(12,2);
BEGIN
    IF p_ticket_id IS NULL OR p_new_result IS NULL
       OR p_correction_idempotency_key IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: ticket, new result and idempotency key are required';
    END IF;
    IF p_reason_code IS NULL OR btrim(p_reason_code) = '' THEN
        RAISE EXCEPTION 'INVALID_INPUT: reason_code is required';
    END IF;
    IF p_created_by IS NULL OR btrim(p_created_by) = '' THEN
        RAISE EXCEPTION 'INVALID_INPUT: created_by is required';
    END IF;

    -- 1. Idempotency (fast path) --------------------------------------------
    SELECT id INTO v_adjustment_id
      FROM public.ticket_result_adjustments
     WHERE correction_idempotency_key = p_correction_idempotency_key;
    IF FOUND THEN
        RETURN v_adjustment_id;
    END IF;

    -- 2. Locks ---------------------------------------------------------------
    SELECT chapter_id INTO v_chapter_id
      FROM public.tickets
     WHERE id = p_ticket_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TICKET_NOT_FOUND: unknown ticket';
    END IF;

    SELECT status INTO v_chapter_status
      FROM public.ledger_chapters
     WHERE id = v_chapter_id
     FOR UPDATE;

    SELECT * INTO v_ticket
      FROM public.tickets
     WHERE id = p_ticket_id
     FOR UPDATE;

    -- 3. Idempotency re-check under the lock ---------------------------------
    SELECT id INTO v_adjustment_id
      FROM public.ticket_result_adjustments
     WHERE correction_idempotency_key = p_correction_idempotency_key;
    IF FOUND THEN
        RETURN v_adjustment_id;
    END IF;

    -- 4. Original result (read only -- never updated) ------------------------
    SELECT * INTO v_original
      FROM public.ticket_results
     WHERE ticket_id = p_ticket_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'NOT_SETTLED: ticket has no original settlement to correct';
    END IF;

    -- 5. Derive the CURRENT effective state from original + ordered history --
    SELECT new_effective_result INTO v_effective_result
      FROM public.ticket_result_adjustments
     WHERE ticket_id = p_ticket_id
     ORDER BY adjustment_seq DESC
     LIMIT 1;

    IF NOT FOUND THEN
        v_effective_result := v_original.result;
    END IF;

    SELECT v_original.pnl + COALESCE(SUM(pnl_delta), 0)
      INTO v_effective_pnl
      FROM public.ticket_result_adjustments
     WHERE ticket_id = p_ticket_id;

    IF p_new_result = v_effective_result THEN
        RAISE EXCEPTION
            'CORRECTION_NO_CHANGE: effective result is already %', v_effective_result;
    END IF;

    -- 6. New effective P/L, from the ticket's frozen economics ---------------
    v_new_pnl := public.olp_result_pnl(p_new_result, v_ticket.risk, v_ticket.potential_profit);
    v_delta   := v_new_pnl - v_effective_pnl;

    -- 7. Append the correction fact -----------------------------------------
    INSERT INTO public.ticket_result_adjustments (
        ticket_id, previous_effective_result, new_effective_result,
        pnl_delta, reason_code, reason_text,
        correction_idempotency_key, created_by
    )
    VALUES (
        p_ticket_id, v_effective_result, p_new_result,
        v_delta, p_reason_code, p_reason_text,
        p_correction_idempotency_key, p_created_by
    )
    RETURNING id INTO v_adjustment_id;

    -- 8. Append the compensating ledger movement ----------------------------
    INSERT INTO public.wallet_transactions (
        user_id, chapter_id, ticket_id, transaction_type, amount, idempotency_key
    )
    VALUES (
        v_ticket.user_id, v_ticket.chapter_id, p_ticket_id,
        'SETTLEMENT_CORRECTION', v_delta, p_correction_idempotency_key
    );

    -- 9. Recalculate available capital and flag deficit ----------------------
    SELECT COALESCE(SUM(amount), 0) INTO v_settled
      FROM public.wallet_transactions
     WHERE chapter_id = v_chapter_id;

    SELECT COALESCE(SUM(amount), 0) INTO v_escrow
      FROM public.risk_reservations
     WHERE chapter_id = v_chapter_id
       AND status     = 'ACTIVE';

    v_available := v_settled - v_escrow;

    IF v_available < 0 AND v_chapter_status = 'ACTIVE' THEN
        UPDATE public.ledger_chapters
           SET status = 'DEFICIT'
         WHERE id = v_chapter_id;
    ELSIF v_available >= 0 AND v_chapter_status = 'DEFICIT' THEN
        -- A favourable correction restores solvency; do not strand the chapter.
        UPDATE public.ledger_chapters
           SET status = 'ACTIVE'
         WHERE id = v_chapter_id;
    END IF;

    RETURN v_adjustment_id;

EXCEPTION
    WHEN unique_violation THEN
        SELECT id INTO v_adjustment_id
          FROM public.ticket_result_adjustments
         WHERE correction_idempotency_key = p_correction_idempotency_key;
        IF FOUND THEN
            RETURN v_adjustment_id;
        END IF;
        RAISE;
END;
$fn$;

REVOKE ALL ON FUNCTION public.apply_settlement_correction_rpc(
    UUID, public.ticket_result_type, TEXT, TEXT, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.apply_settlement_correction_rpc(
    UUID, public.ticket_result_type, TEXT, TEXT, UUID, TEXT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_settlement_correction_rpc(
    UUID, public.ticket_result_type, TEXT, TEXT, UUID, TEXT) TO service_role;
