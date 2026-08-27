-- =============================================================================
-- 034_p3_feed_health.sql -- Package #3: is the feed actually alive?
-- =============================================================================
-- When a provider goes dark the system already behaves correctly: quotes age
-- past snapshot_ttl_seconds, current_market_board stops calling anything
-- placeable, and place_ticket_rpc refuses with SNAPSHOT_STALE. It FAILS CLOSED,
-- which is the right direction to fail in.
--
-- What it does not do on its own is tell anyone. A silent fail-closed looks
-- identical to a quiet Tuesday. These are the read surfaces that distinguish
-- them.
-- =============================================================================

CREATE VIEW public.market_feed_health
WITH (security_invoker = true) AS
SELECT
    e.id                          AS event_id,
    e.source_event_id,
    e.current_scheduled_start,
    e.is_live,
    count(b.snapshot_id)          AS quoted_markets,
    count(b.snapshot_id) FILTER (WHERE b.is_placeable) AS placeable_markets,
    min(b.quote_age_seconds)      AS freshest_quote_age_seconds,
    max(b.quote_age_seconds)      AS stalest_quote_age_seconds,
    (count(b.snapshot_id) FILTER (WHERE b.is_placeable) = 0) AS is_dark
FROM public.events e
LEFT JOIN public.current_market_board b
       ON b.event_id = e.id
WHERE e.is_closed = FALSE
GROUP BY e.id, e.source_event_id, e.current_scheduled_start, e.is_live;

COMMENT ON VIEW public.market_feed_health IS
    'Per open event: how fresh its quotes are and whether anything is still '
    'placeable. is_dark = the market exists but nothing on it can be bet.';

-- -----------------------------------------------------------------------------
-- One row an operator can alert on.
--
-- Deliberately counts only events that have NOT yet started: an event already
-- under way is supposed to have nothing placeable, and counting those as dark
-- would make the alarm useless by making it always true.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.feed_health_summary_rpc()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_open        INT;
    v_dark        INT;
    v_stalest     INT;
    v_last_quote  TIMESTAMPTZ;
    v_last_run    TIMESTAMPTZ;
    v_providers   JSONB;
BEGIN
    SELECT count(*),
           count(*) FILTER (WHERE placeable_markets = 0),
           max(stalest_quote_age_seconds)
      INTO v_open, v_dark, v_stalest
      FROM public.market_feed_health
     WHERE is_live = FALSE
       AND current_scheduled_start > NOW();

    SELECT max(captured_at) INTO v_last_quote
      FROM public.market_snapshots
     WHERE is_in_play = FALSE;

    SELECT max(started_at) INTO v_last_run
      FROM public.ingestion_runs
     WHERE kind = 'ODDS' AND status = 'SUCCEEDED';

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'provider', provider,
               'circuit', circuit::text,
               'consecutive_failures', consecutive_failures,
               'quota_remaining', quota_remaining,
               'last_success_at', last_success_at,
               'last_error', last_error)), '[]'::jsonb)
      INTO v_providers
      FROM public.provider_health;

    RETURN jsonb_build_object(
        'open_events',              COALESCE(v_open, 0),
        'dark_events',              COALESCE(v_dark, 0),
        'stalest_quote_age_seconds', v_stalest,
        'last_pregame_quote_at',    v_last_quote,
        'last_successful_odds_run', v_last_run,
        'seconds_since_last_run',
            CASE WHEN v_last_run IS NULL THEN NULL
                 ELSE floor(extract(epoch FROM (NOW() - v_last_run)))::int END,
        'providers',                v_providers);
END;
$fn$;

REVOKE ALL ON public.market_feed_health FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.market_feed_health TO service_role;

REVOKE ALL ON FUNCTION public.feed_health_summary_rpc()
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.feed_health_summary_rpc() TO service_role;
