-- =============================================================================
-- 026_event_lifecycle_rpc.sql -- Kickoff, close, cancel
-- =============================================================================
-- SERVICE ROLE ONLY. All three are idempotent: a lifecycle feed that repeats
-- itself must not double-void or re-capture.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Kickoff. Closing lines are captured as part of this transaction, because the
-- moment the event goes live is exactly when "the last pre-game quote" stops
-- being a moving target. Doing it as a separate later job would leave a window
-- in which in-play quotes have already arrived.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.mark_event_live_rpc(
    p_event_id     UUID,
    p_actual_start TIMESTAMPTZ DEFAULT NULL,
    p_source       TEXT DEFAULT 'LIFECYCLE'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_event    public.events%ROWTYPE;
    v_start    TIMESTAMPTZ := COALESCE(p_actual_start, NOW());
    v_captured INT := 0;
BEGIN
    SELECT * INTO v_event FROM public.events WHERE id = p_event_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    IF v_event.is_closed THEN
        RAISE EXCEPTION 'EVENT_CLOSED: a closed event cannot go live';
    END IF;

    IF v_event.is_live AND v_event.actual_start_time IS NOT NULL THEN
        RETURN jsonb_build_object(
            'event_id', p_event_id, 'action', 'ALREADY_LIVE',
            'actual_start_time', v_event.actual_start_time,
            'closing_lines_captured', 0);
    END IF;

    IF v_start > NOW() + INTERVAL '5 seconds' THEN
        RAISE EXCEPTION 'INVALID_START_TIME: kickoff cannot be in the future';
    END IF;

    -- Record kickoff first so closing-line capture uses the real cutoff.
    UPDATE public.events
       SET is_live           = TRUE,
           actual_start_time = v_start
     WHERE id = p_event_id;

    v_captured := public.capture_closing_line_rpc(p_event_id, p_source);

    PERFORM public.olp_log_lifecycle(
        p_event_id, 'KICKED_OFF', p_source,
        jsonb_build_object('actual_start_time', v_start,
                           'closing_lines_captured', v_captured)
    );

    RETURN jsonb_build_object(
        'event_id', p_event_id, 'action', 'KICKED_OFF',
        'actual_start_time', v_start,
        'closing_lines_captured', v_captured);
END;
$fn$;


-- -----------------------------------------------------------------------------
-- Close. Capture is attempted again as a safety net for an event that was never
-- explicitly kicked off (a feed can miss the live signal); it is a no-op when
-- capture already happened.
--
-- Closing does NOT require every ticket to be graded -- grading is a separate
-- concern -- but the count of ungraded tickets is recorded so a stuck event is
-- visible rather than silent.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.close_event_rpc(
    p_event_id UUID,
    p_source   TEXT DEFAULT 'LIFECYCLE'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_event     public.events%ROWTYPE;
    v_captured  INT := 0;
    v_ungraded  INT := 0;
BEGIN
    SELECT * INTO v_event FROM public.events WHERE id = p_event_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    IF v_event.is_closed THEN
        RETURN jsonb_build_object(
            'event_id', p_event_id, 'action', 'ALREADY_CLOSED',
            'closing_lines_captured', 0, 'ungraded_tickets', 0);
    END IF;

    v_captured := public.capture_closing_line_rpc(p_event_id, p_source);

    SELECT count(*) INTO v_ungraded
      FROM public.tickets
     WHERE event_id = p_event_id AND status = 'ACCEPTED';

    UPDATE public.events
       SET is_closed = TRUE,
           is_live   = FALSE
     WHERE id = p_event_id;

    PERFORM public.olp_log_lifecycle(
        p_event_id, 'CLOSED', p_source,
        jsonb_build_object('closing_lines_captured', v_captured,
                           'ungraded_tickets', v_ungraded)
    );

    RETURN jsonb_build_object(
        'event_id', p_event_id, 'action', 'CLOSED',
        'closing_lines_captured', v_captured,
        'ungraded_tickets', v_ungraded);
END;
$fn$;


-- -----------------------------------------------------------------------------
-- Cancel. Every open ticket is voided and stakes returned before the event is
-- closed, so no capital stays escrowed against a game that will never be played.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.cancel_event_rpc(
    p_event_id UUID,
    p_reason   TEXT DEFAULT 'EVENT_CANCELLED',
    p_source   TEXT DEFAULT 'LIFECYCLE'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_event  public.events%ROWTYPE;
    v_voided INT := 0;
BEGIN
    SELECT * INTO v_event FROM public.events WHERE id = p_event_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'EVENT_NOT_FOUND: unknown event';
    END IF;

    IF v_event.is_closed THEN
        RETURN jsonb_build_object(
            'event_id', p_event_id, 'action', 'ALREADY_CLOSED', 'tickets_voided', 0);
    END IF;

    v_voided := public.void_event_tickets_rpc(p_event_id, p_reason, p_source);

    UPDATE public.events
       SET is_closed = TRUE,
           is_live   = FALSE
     WHERE id = p_event_id;

    PERFORM public.olp_log_lifecycle(
        p_event_id, 'CANCELLED', p_source,
        jsonb_build_object('tickets_voided', v_voided, 'reason', p_reason)
    );

    RETURN jsonb_build_object(
        'event_id', p_event_id, 'action', 'CANCELLED', 'tickets_voided', v_voided);
END;
$fn$;

REVOKE ALL ON FUNCTION public.mark_event_live_rpc(UUID, TIMESTAMPTZ, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_event_live_rpc(UUID, TIMESTAMPTZ, TEXT)
    TO service_role;

REVOKE ALL ON FUNCTION public.close_event_rpc(UUID, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.close_event_rpc(UUID, TEXT) TO service_role;

REVOKE ALL ON FUNCTION public.cancel_event_rpc(UUID, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.cancel_event_rpc(UUID, TEXT, TEXT) TO service_role;
