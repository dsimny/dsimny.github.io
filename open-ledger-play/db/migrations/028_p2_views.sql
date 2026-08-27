-- =============================================================================
-- 028_p2_views.sql -- Market board + closing-line value
-- =============================================================================

-- The board reports placeability, so the client needs the TTL policy to be
-- readable. These are non-sensitive policy numbers (TTL, sizing fraction,
-- minimum wager), not secrets; Package #1 exposed them to `authenticated` and
-- the public board extends that to `anon`.
GRANT SELECT ON public.system_settings TO anon;
CREATE POLICY p_settings_read_anon ON public.system_settings
    FOR SELECT TO anon USING (TRUE);

-- -----------------------------------------------------------------------------
-- The current market board -- what "VIEW MARKET SNAPSHOT" reads.
--
-- This view MUST agree with place_ticket_rpc. It uses the same ordering
-- (captured_at DESC, ingest_seq DESC), the same in-play exclusion and the same
-- TTL from system_settings, so a row flagged is_placeable is a row the RPC will
-- actually accept. A board that disagreed with the RPC would show users prices
-- they cannot take.
-- -----------------------------------------------------------------------------
CREATE VIEW public.current_market_board
WITH (security_invoker = true) AS
SELECT
    e.id                       AS event_id,
    e.source_event_id,
    e.sport,
    e.league,
    e.home_team,
    e.away_team,
    e.current_scheduled_start,
    e.is_live,
    s.id                       AS snapshot_id,
    s.market_type,
    s.selection,
    s.line,
    s.price,
    s.sportsbook,
    s.captured_at,
    s.ingest_seq,
    floor(extract(epoch FROM (NOW() - s.captured_at)))::int AS quote_age_seconds,
    (
        e.is_live           = FALSE
        AND e.actual_start_time IS NULL
        AND NOW()           <  e.current_scheduled_start
        AND NOW() - s.captured_at <= make_interval(secs => cfg.snapshot_ttl_seconds)
    )                          AS is_placeable
FROM public.events e
CROSS JOIN public.system_settings cfg
JOIN LATERAL (
    SELECT DISTINCT ON (m.market_type, m.selection, m.sportsbook) m.*
      FROM public.market_snapshots m
     WHERE m.event_id    = e.id
       AND m.is_in_play  = FALSE
       AND m.captured_at <= NOW()
     ORDER BY m.market_type, m.selection, m.sportsbook,
              m.captured_at DESC, m.ingest_seq DESC
) s ON TRUE
WHERE cfg.id = TRUE
  AND e.is_closed = FALSE;

COMMENT ON VIEW public.current_market_board IS
    'Latest executable quote per event/market/selection/book. Ordering, in-play '
    'exclusion and TTL match place_ticket_rpc exactly.';

-- -----------------------------------------------------------------------------
-- Closing-line value.
--
-- The reason closing lines are captured per book: a ticket is compared against
-- the CLOSING price FROM THE SAME BOOK. Comparing across books would measure
-- the spread between sportsbooks rather than the quality of the bet.
--
-- beat_close is deliberately NULL when the line moved on a SPREAD/TOTAL. At a
-- different number it is a different bet, and reducing that to a price
-- comparison would silently report a falsehood. line_moved says so explicitly.
-- -----------------------------------------------------------------------------
CREATE VIEW public.ticket_closing_line_value
WITH (security_invoker = true) AS
SELECT
    t.id                AS ticket_id,
    t.user_id,
    t.chapter_id,
    t.event_id,
    t.market_type,
    t.selection,
    t.accepted_sportsbook,
    t.accepted_line,
    t.accepted_price,
    c.line              AS closing_line,
    c.price             AS closing_price,
    c.captured_at       AS closing_captured_at,
    (t.accepted_line IS DISTINCT FROM c.line) AS line_moved,
    CASE
        WHEN t.accepted_line IS DISTINCT FROM c.line THEN NULL
        ELSE public.olp_american_profit(1, t.accepted_price)
             > public.olp_american_profit(1, c.price)
    END                 AS beat_close,
    CASE
        WHEN t.accepted_line IS DISTINCT FROM c.line THEN NULL
        ELSE round(
            public.olp_american_profit(1, t.accepted_price)
            - public.olp_american_profit(1, c.price), 4)
    END                 AS payout_edge_per_unit
FROM public.tickets t
JOIN public.market_snapshots c
    ON  c.event_id            = t.event_id
    AND c.market_type         = t.market_type
    AND c.selection           = t.selection
    AND c.sportsbook          = t.accepted_sportsbook
    AND c.is_closing_snapshot = TRUE;

COMMENT ON VIEW public.ticket_closing_line_value IS
    'Same-book CLV. beat_close is NULL when the line moved -- at a different '
    'number it is a different bet, not a better price.';

REVOKE ALL ON public.current_market_board       FROM PUBLIC;
REVOKE ALL ON public.ticket_closing_line_value  FROM PUBLIC, anon;
GRANT SELECT ON public.current_market_board      TO anon, authenticated;
GRANT SELECT ON public.ticket_closing_line_value TO authenticated;
