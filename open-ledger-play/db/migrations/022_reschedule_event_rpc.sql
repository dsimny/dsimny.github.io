-- =============================================================================
-- 022_reschedule_event_rpc.sql -- Schedule changes and postponement policy
-- =============================================================================
-- SERVICE ROLE ONLY. The single code path for moving an event's start time, so
-- postponement policy is decided in exactly one place.
--
-- POLICY: the threshold is measured on the CUMULATIVE shift away from
-- original_scheduled_start, in absolute terms.
--   - cumulative, because two 24h slips are the same displacement as one 48h
--     slip and should not escape the rule by arriving separately
--   - absolute, because a game pulled 48h EARLIER is no more "the game you bet
--     on" than one pushed 48h later
-- Crossing the threshold voids every open ticket on the event and returns the
-- stake, rather than leaving capital escrowed against a game that moved.
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
    v_event         public.events%ROWTYPE;
    v_threshold     INT;
    v_shift         INTERVAL;
    v_shift_hours   NUMERIC;
    v_is_postponed  BOOLEAN;
    v_action        public.event_lifecycle_action;
    v_voided        INT := 0;
BEGIN
    IF p_event_id IS NULL OR p_new_start IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: event and new start time are required';
    END IF;

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

    -- No-op changes are reported, not recorded. Writing a history row for a
    -- feed that simply repeated itself would make the audit trail noise.
    IF p_new_start = v_event.current_scheduled_start THEN
        RETURN jsonb_build_object(
            'action',          'UNCHANGED',
            'event_id',        p_event_id,
            'shift_hours',     0,
            'tickets_voided',  0
        );
    END IF;

    SELECT postponement_void_hours INTO v_threshold
      FROM public.system_settings WHERE id = TRUE;

    v_shift       := p_new_start - v_event.original_scheduled_start;
    v_shift_hours := round(abs(extract(epoch FROM v_shift)) / 3600.0, 2);
    v_is_postponed := v_shift_hours >= v_threshold;

    -- Attribute the history row the trigger is about to write.
    PERFORM set_config('olp.schedule_source', COALESCE(NULLIF(btrim(p_source), ''), 'PROVIDER'), TRUE);
    PERFORM set_config('olp.schedule_reason', COALESCE(p_reason, ''), TRUE);

    UPDATE public.events
       SET current_scheduled_start = p_new_start
     WHERE id = p_event_id;

    v_action := CASE WHEN v_is_postponed THEN 'POSTPONED' ELSE 'RESCHEDULED' END;

    PERFORM public.olp_log_lifecycle(
        p_event_id,
        v_action,
        p_source,
        jsonb_build_object(
            'previous_start',  v_event.current_scheduled_start,
            'new_start',       p_new_start,
            'original_start',  v_event.original_scheduled_start,
            'shift_hours',     v_shift_hours,
            'threshold_hours', v_threshold,
            'reason',          p_reason
        )
    );

    IF v_is_postponed THEN
        v_voided := public.void_event_tickets_rpc(
            p_event_id,
            COALESCE(p_reason, 'POSTPONEMENT'),
            p_source
        );
    END IF;

    PERFORM set_config('olp.schedule_source', '', TRUE);
    PERFORM set_config('olp.schedule_reason', '', TRUE);

    RETURN jsonb_build_object(
        'action',         v_action::text,
        'event_id',       p_event_id,
        'shift_hours',    v_shift_hours,
        'tickets_voided', v_voided
    );
END;
$fn$;

REVOKE ALL ON FUNCTION public.reschedule_event_rpc(UUID, TIMESTAMPTZ, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reschedule_event_rpc(UUID, TIMESTAMPTZ, TEXT, TEXT)
    TO service_role;
