-- =============================================================================
-- 031_p2r_ticket_relative_postponement.sql
-- =============================================================================
-- Postponement is now TICKET-RELATIVE.
--
-- Each open ticket is judged on the displacement between the new start time and
-- the schedule THAT TICKET was accepted against. A bettor who bought into an
-- already-slipped schedule is not charged for a slip that happened before they
-- ever placed.
--
--   ticket placed before the first slip  -> total subsequent displacement counts
--   ticket placed after the first slip   -> only displacement after it counts
--
-- Displacement stays ABSOLUTE: a game pulled 48h earlier is no more the game you
-- bet on than one pushed 48h later.
--
-- Consequence: one reschedule can void some tickets and retain others. That is
-- the intended behaviour, not a bug -- they bought different schedules.
--
-- The event-level lifecycle label still reports the event's own story
-- (cumulative displacement from its original start), because the log describes
-- the event; the per-ticket decision is what moves money.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.reschedule_event_rpc(
    p_event_id  UUID,
    p_new_start TIMESTAMPTZ,
    p_source    TEXT DEFAULT 'PROVIDER',
    p_reason    TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_event        public.events%ROWTYPE;
    v_threshold    INT;
    v_cumulative   NUMERIC;
    v_step         NUMERIC;
    v_ticket       RECORD;
    v_displacement NUMERIC;
    v_voided       INT := 0;
    v_retained     INT := 0;
    v_action       public.event_lifecycle_action;
BEGIN
    IF p_event_id IS NULL OR p_new_start IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: event and new start time are required';
    END IF;

    -- Event lock first, matching the order placement now uses.
    SELECT * INTO v_event
      FROM public.events
     WHERE id = p_event_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    IF v_event.is_closed THEN
        RAISE EXCEPTION 'EVENT_CLOSED: a closed event cannot be rescheduled';
    END IF;

    IF v_event.actual_start_time IS NOT NULL THEN
        RAISE EXCEPTION 'EVENT_STARTED: a started event cannot be rescheduled';
    END IF;

    -- A feed repeating itself is reported, not recorded.
    IF p_new_start = v_event.current_scheduled_start THEN
        RETURN jsonb_build_object(
            'action', 'UNCHANGED', 'event_id', p_event_id,
            'step_hours', 0, 'cumulative_hours',
            round(abs(extract(epoch FROM (v_event.current_scheduled_start
                                          - v_event.original_scheduled_start))) / 3600.0, 2),
            'tickets_voided', 0, 'tickets_retained', 0);
    END IF;

    SELECT postponement_void_hours INTO v_threshold
      FROM public.system_settings WHERE id = TRUE;

    v_step := round(abs(extract(epoch FROM
                  (p_new_start - v_event.current_scheduled_start))) / 3600.0, 2);
    v_cumulative := round(abs(extract(epoch FROM
                  (p_new_start - v_event.original_scheduled_start))) / 3600.0, 2);

    PERFORM set_config('olp.schedule_source',
                       COALESCE(NULLIF(btrim(p_source), ''), 'PROVIDER'), TRUE);
    PERFORM set_config('olp.schedule_reason', COALESCE(p_reason, ''), TRUE);

    UPDATE public.events
       SET current_scheduled_start = p_new_start
     WHERE id = p_event_id;

    -- ---- per-ticket adjudication ------------------------------------------
    -- Deterministic ORDER BY chapter_id gives every caller the same lock order.
    FOR v_ticket IN
        SELECT id, chapter_id, accepted_event_start
          FROM public.tickets
         WHERE event_id = p_event_id
           AND status   = 'ACCEPTED'
         ORDER BY chapter_id, id
    LOOP
        v_displacement := abs(extract(epoch FROM
                              (p_new_start - v_ticket.accepted_event_start))) / 3600.0;

        IF v_displacement >= v_threshold THEN
            PERFORM public.settle_ticket_rpc(
                v_ticket.id,
                'VOID'::public.ticket_result_type,
                COALESCE(p_reason, 'POSTPONEMENT'),
                md5(v_ticket.id::text || ':EVENT_VOID')::uuid
            );
            v_voided := v_voided + 1;
        ELSE
            v_retained := v_retained + 1;
        END IF;
    END LOOP;

    v_action := CASE
                    WHEN v_voided > 0 OR v_cumulative >= v_threshold THEN 'POSTPONED'
                    ELSE 'RESCHEDULED'
                END;

    PERFORM public.olp_log_lifecycle(
        p_event_id, v_action, p_source,
        jsonb_build_object(
            'previous_start',   v_event.current_scheduled_start,
            'new_start',        p_new_start,
            'original_start',   v_event.original_scheduled_start,
            'step_hours',       v_step,
            'cumulative_hours', v_cumulative,
            'threshold_hours',  v_threshold,
            'tickets_voided',   v_voided,
            'tickets_retained', v_retained,
            'reason',           p_reason)
    );

    IF v_voided > 0 THEN
        PERFORM public.olp_log_lifecycle(
            p_event_id, 'TICKETS_VOIDED', p_source,
            jsonb_build_object('tickets_voided', v_voided,
                               'reason', COALESCE(p_reason, 'POSTPONEMENT'))
        );
    END IF;

    PERFORM set_config('olp.schedule_source', '', TRUE);
    PERFORM set_config('olp.schedule_reason', '', TRUE);

    RETURN jsonb_build_object(
        'action',           v_action::text,
        'event_id',         p_event_id,
        'step_hours',       v_step,
        'cumulative_hours', v_cumulative,
        'tickets_voided',   v_voided,
        'tickets_retained', v_retained);
END;
$fn$;

REVOKE ALL ON FUNCTION public.reschedule_event_rpc(UUID, TIMESTAMPTZ, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reschedule_event_rpc(UUID, TIMESTAMPTZ, TEXT, TEXT)
    TO service_role;
