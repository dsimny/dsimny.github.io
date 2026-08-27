-- =============================================================================
-- 021_void_event_tickets_rpc.sql -- Postponement / cancellation void processing
-- =============================================================================
-- SERVICE ROLE ONLY.
--
-- Voids every still-open ticket on an event. It does NOT write settlement rows
-- itself: it calls settle_ticket_rpc, so voids go through exactly the same
-- audited, idempotent path as ordinary grading. Escrow release, the zero-value
-- wallet transaction and the append-once result all come for free.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.void_event_tickets_rpc(
    p_event_id UUID,
    p_reason   TEXT,
    p_source   TEXT DEFAULT 'LIFECYCLE'
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_ticket   RECORD;
    v_voided   INT := 0;
    v_key      UUID;
BEGIN
    IF p_event_id IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_event_id is required';
    END IF;
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_reason is required';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.events WHERE id = p_event_id) THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    -- Deterministic ORDER BY chapter_id gives every concurrent caller the same
    -- lock ordering, so two void runs over the same event cannot deadlock.
    FOR v_ticket IN
        SELECT id, chapter_id
          FROM public.tickets
         WHERE event_id = p_event_id
           AND status   = 'ACCEPTED'
         ORDER BY chapter_id, id
    LOOP
        -- Stable key: re-running this RPC reuses it, so the wallet transaction
        -- is traceable back to this exact void rather than a fresh random id.
        v_key := md5(v_ticket.id::text || ':EVENT_VOID')::uuid;

        PERFORM public.settle_ticket_rpc(
            v_ticket.id,
            'VOID'::public.ticket_result_type,
            p_reason,
            v_key
        );

        v_voided := v_voided + 1;
    END LOOP;

    IF v_voided > 0 THEN
        PERFORM public.olp_log_lifecycle(
            p_event_id,
            'TICKETS_VOIDED',
            p_source,
            jsonb_build_object('tickets_voided', v_voided, 'reason', p_reason)
        );
    END IF;

    RETURN v_voided;
END;
$fn$;

REVOKE ALL ON FUNCTION public.void_event_tickets_rpc(UUID, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.void_event_tickets_rpc(UUID, TEXT, TEXT)
    TO service_role;
