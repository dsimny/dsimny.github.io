-- =============================================================================
-- 040_p4_market_movement.sql -- how the market has moved
-- =============================================================================
-- Two movements, kept deliberately separate:
--
--   PRICE movement -- within one line. Opening vs current consensus for the
--                     same (event, market, selection, line).
--   LINE  movement -- across lines. Where the modal line opened vs where it is.
--
-- They are NOT combined into a single scalar. Doing so requires converting half
-- a point of spread into probability, which is modelling and belongs to
-- Package #5. Reporting them separately is the honest form.
--
-- "Opening" means OUR FIRST OBSERVATION, not the true market open. If ingestion
-- started after a market opened, this reflects when Open Ledger began watching.
-- The column is named opening_is_first_observation so no consumer mistakes it
-- for a market-wide opening line.
--
-- No TTL filter here: history is historical by definition. The TTL governs what
-- is executable, never what was observed.
-- =============================================================================

CREATE VIEW public.market_movement
WITH (security_invoker = true) AS
WITH cfg AS (
    SELECT * FROM public.system_settings WHERE id = TRUE
),
-- Each book's EARLIEST observation per canonical key.
earliest AS MATERIALIZED (
    SELECT DISTINCT ON (s.event_id, s.market_type, s.selection, s.line, s.sportsbook)
           s.event_id, s.market_type, s.selection, s.line, s.sportsbook,
           s.price, s.captured_at
    FROM public.market_snapshots s
    JOIN public.events e ON e.id = s.event_id
    WHERE s.is_in_play = FALSE
      AND e.is_closed  = FALSE
    ORDER BY s.event_id, s.market_type, s.selection, s.line, s.sportsbook,
             s.captured_at ASC, s.ingest_seq ASC
),
-- Identical partner rule to canonical_market (038). Duplicated deliberately
-- rather than refactoring a verified view mid-package; test P4-T22 asserts the
-- two agree by construction on a single-observation slate, where opening and
-- current must be equal.
opening_partnered AS (
    SELECT n.*,
           public.olp_implied_probability(n.price) AS raw_p,
           pr.partner_raw_p
    FROM earliest n
    LEFT JOIN LATERAL (
        SELECT public.olp_implied_probability(p.price) AS partner_raw_p
        FROM earliest p
        WHERE p.event_id    = n.event_id
          AND p.market_type = n.market_type
          AND p.sportsbook  = n.sportsbook
          AND p.selection  <> n.selection
          AND CASE n.market_type
                  WHEN 'MONEYLINE' THEN p.line IS NULL
                  WHEN 'SPREAD'    THEN p.line = -n.line
                  WHEN 'TOTAL'     THEN p.line =  n.line
              END
        LIMIT 1
    ) pr ON TRUE
),
opening_agg AS (
    SELECT event_id, market_type, selection, line,
           (percentile_cont(0.5) WITHIN GROUP (
                ORDER BY public.olp_devig_multiplicative(raw_p, partner_raw_p)))::numeric
                                                       AS opening_probability,
           count(partner_raw_p)                        AS opening_devig_books,
           min(captured_at)                            AS opening_captured_at
    FROM opening_partnered
    GROUP BY 1, 2, 3, 4
),
-- Modal line at opening, using the same stable tie-break as 038:
-- book count DESC, then line ASC. Never recency.
opening_line_pop AS (
    SELECT event_id, market_type, selection, line,
           count(DISTINCT sportsbook) AS line_books
    FROM earliest
    GROUP BY 1, 2, 3, 4
),
opening_modal AS (
    SELECT DISTINCT ON (event_id, market_type, selection)
           event_id, market_type, selection, line AS opening_modal_line
    FROM opening_line_pop
    ORDER BY event_id, market_type, selection, line_books DESC, line ASC
)
SELECT
    c.event_id, c.source_event_id, c.market_type, c.selection, c.line,

    round(o.opening_probability, 6)                        AS opening_probability,
    public.olp_fair_american(o.opening_probability)         AS opening_price,
    o.opening_captured_at,
    o.opening_devig_books,
    TRUE                                                    AS opening_is_first_observation,

    c.consensus_probability                                 AS current_probability,
    c.consensus_price                                       AS current_price,
    c.current_captured_at,

    round(c.consensus_probability - o.opening_probability, 6) AS probability_movement,
    CASE
        WHEN o.opening_probability IS NULL
          OR c.consensus_probability IS NULL THEN NULL
        WHEN c.consensus_probability - o.opening_probability >  cfg.mi_movement_epsilon THEN 'IN'
        WHEN c.consensus_probability - o.opening_probability < -cfg.mi_movement_epsilon THEN 'OUT'
        ELSE 'FLAT'
    END                                                     AS movement_direction,

    om.opening_modal_line,
    c.modal_line,
    (c.modal_line - om.opening_modal_line)                  AS line_movement,

    c.market_quality,
    c.quality_reasons
FROM public.canonical_market c
CROSS JOIN cfg
LEFT JOIN opening_agg o
       ON o.event_id = c.event_id AND o.market_type = c.market_type
      AND o.selection = c.selection AND o.line IS NOT DISTINCT FROM c.line
LEFT JOIN opening_modal om
       ON om.event_id = c.event_id AND om.market_type = c.market_type
      AND om.selection = c.selection;

COMMENT ON VIEW public.market_movement IS
    'Price movement within a line and line movement across lines, kept separate. '
    'Combining them requires converting spread points to probability, which is '
    'modelling and belongs to Package #5. opening_* is OUR first observation, '
    'not the true market open.';

REVOKE ALL ON public.market_movement FROM PUBLIC;
GRANT SELECT ON public.market_movement TO anon, authenticated, service_role;
