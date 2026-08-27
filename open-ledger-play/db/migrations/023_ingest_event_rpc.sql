-- =============================================================================
-- 023_ingest_event_rpc.sql -- Schedule feed -> events
-- =============================================================================
-- SERVICE ROLE ONLY. Idempotent upsert keyed on source_event_id.
--
-- Schedule changes are NOT handled here: they are delegated to
-- reschedule_event_rpc so postponement policy lives in exactly one place and a
-- feed cannot slide a game 48 hours without triggering the void rules.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.ingest_event_rpc(
    p_source_event_id TEXT,
    p_home_team       TEXT,
    p_away_team       TEXT,
    p_scheduled_start TIMESTAMPTZ,
    p_sport           TEXT DEFAULT 'NFL',
    p_league          TEXT DEFAULT 'NFL',
    p_source          TEXT DEFAULT 'PROVIDER'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_event    public.events%ROWTYPE;
    v_id       UUID;
    v_result   JSONB;
    v_created  BOOLEAN := FALSE;
BEGIN
    IF p_source_event_id IS NULL OR btrim(p_source_event_id) = '' THEN
        RAISE EXCEPTION 'INVALID_INPUT: p_source_event_id is required';
    END IF;
    IF p_home_team IS NULL OR p_away_team IS NULL OR p_scheduled_start IS NULL THEN
        RAISE EXCEPTION 'INVALID_INPUT: teams and scheduled start are required';
    END IF;

    SELECT * INTO v_event
      FROM public.events
     WHERE source_event_id = p_source_event_id
     FOR UPDATE;

    -- ---- New event ---------------------------------------------------------
    IF NOT FOUND THEN
        PERFORM set_config('olp.schedule_source',
                           COALESCE(NULLIF(btrim(p_source), ''), 'PROVIDER'), TRUE);
        PERFORM set_config('olp.schedule_reason', 'INITIAL_SCHEDULE', TRUE);

        INSERT INTO public.events (
            source_event_id, sport, league, home_team, away_team,
            original_scheduled_start, current_scheduled_start
        )
        VALUES (
            p_source_event_id, p_sport, p_league, p_home_team, p_away_team,
            p_scheduled_start, p_scheduled_start
        )
        RETURNING id INTO v_id;

        PERFORM set_config('olp.schedule_source', '', TRUE);
        PERFORM set_config('olp.schedule_reason', '', TRUE);

        PERFORM public.olp_log_lifecycle(
            v_id, 'INGESTED', p_source,
            jsonb_build_object('source_event_id', p_source_event_id,
                               'scheduled_start', p_scheduled_start)
        );

        RETURN jsonb_build_object(
            'event_id', v_id, 'created', TRUE,
            'action', 'INGESTED', 'tickets_voided', 0);
    END IF;

    -- ---- Existing event ----------------------------------------------------
    -- A provider id that suddenly names different teams is an id collision or a
    -- feed bug, not a rename. Refuse rather than silently repoint an event that
    -- already has tickets against it.
    IF v_event.home_team IS DISTINCT FROM p_home_team
       OR v_event.away_team IS DISTINCT FROM p_away_team THEN
        RAISE EXCEPTION
            'EVENT_IDENTITY_MISMATCH: % already maps to %@% but feed reported %@%',
            p_source_event_id, v_event.away_team, v_event.home_team,
            p_away_team, p_home_team;
    END IF;

    IF v_event.is_closed OR v_event.actual_start_time IS NOT NULL THEN
        -- Nothing to do; a finished game keeps whatever schedule it had.
        RETURN jsonb_build_object(
            'event_id', v_event.id, 'created', FALSE,
            'action', 'IGNORED_FINISHED', 'tickets_voided', 0);
    END IF;

    IF p_scheduled_start = v_event.current_scheduled_start THEN
        RETURN jsonb_build_object(
            'event_id', v_event.id, 'created', FALSE,
            'action', 'UNCHANGED', 'tickets_voided', 0);
    END IF;

    v_result := public.reschedule_event_rpc(
        v_event.id, p_scheduled_start, p_source, 'SCHEDULE_FEED');

    RETURN jsonb_build_object(
        'event_id',       v_event.id,
        'created',        FALSE,
        'action',         v_result ->> 'action',
        'shift_hours',    (v_result ->> 'shift_hours')::numeric,
        'tickets_voided', (v_result ->> 'tickets_voided')::int
    );
END;
$fn$;

REVOKE ALL ON FUNCTION public.ingest_event_rpc(
    TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ingest_event_rpc(
    TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, TEXT, TEXT) TO service_role;
