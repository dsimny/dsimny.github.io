-- Performance-only candidate; not deployed.
-- Preserve every public column and security_invoker boundary.
-- Placeable observations are unique per (event, market, selection, book)
-- because the existing line-agnostic latest-snapshot gate selects one ID.
-- Therefore the partition count before DISTINCT ON equals the previous
-- count(DISTINCT sportsbook) at the same key, including NULL moneyline lines.
-- Consumer CTE fences prevent a count projection from expanding and
-- repeatedly executing the constituent market pipelines.

CREATE OR REPLACE VIEW public.executable_market
WITH (security_invoker = true) AS
WITH canonical AS MATERIALIZED (SELECT * FROM public.canonical_market), cfg AS (
    SELECT * FROM public.system_settings WHERE id = TRUE
),
-- Observations place_ticket_rpc would actually accept, right now.
placeable_obs AS MATERIALIZED (
    SELECT s.id AS snapshot_id, s.event_id, s.market_type, s.selection, s.line,
           s.sportsbook, s.price, s.captured_at
    FROM public.market_snapshots s
    JOIN public.events e ON e.id = s.event_id
    CROSS JOIN cfg
    WHERE s.is_in_play = FALSE
      AND s.captured_at <= NOW()
      AND NOW() - s.captured_at <= make_interval(secs => cfg.snapshot_ttl_seconds)
      -- the event-state gate, matching place_ticket_rpc exactly
      AND e.is_closed = FALSE
      AND e.is_live   = FALSE
      AND e.actual_start_time IS NULL
      AND NOW() < e.current_scheduled_start
      -- MARKET_MOVED parity, line-agnostic exactly as the RPC checks
      AND s.id = (
            SELECT x.id FROM public.market_snapshots x
            WHERE x.event_id    = s.event_id
              AND x.market_type = s.market_type
              AND x.selection   = s.selection
              AND x.sportsbook  = s.sportsbook
              AND x.is_in_play  = FALSE
              AND x.captured_at <= NOW()
            ORDER BY x.captured_at DESC, x.ingest_seq DESC
            LIMIT 1)
),
exec_best AS MATERIALIZED (
    SELECT DISTINCT ON (event_id, market_type, selection, line)
           event_id, market_type, selection, line,
           price       AS exec_best_price,
           sportsbook  AS exec_best_book,
           snapshot_id AS exec_best_snapshot_id,
           captured_at AS exec_best_captured_at,
           count(*) OVER (PARTITION BY event_id, market_type, selection, line) AS executable_book_count
    FROM placeable_obs
    ORDER BY event_id, market_type, selection, line,
             public.olp_price_payout(price) DESC,
             (price > 0) DESC,   -- only fires at +100 vs -100; see 045
             sportsbook ASC
)
SELECT
    c.event_id, c.source_event_id, c.home_team, c.away_team, c.commence_time,
    c.market_type, c.selection, c.line,

    -- Recomputed over placeable observations only. Deliberately named the same
    -- concept but derived from a stricter set than canonical_market's.
    b.exec_best_price                       AS best_price,
    b.exec_best_book                        AS best_book,
    b.exec_best_snapshot_id                 AS best_snapshot_id,
    b.exec_best_captured_at                 AS best_captured_at,
    b.executable_book_count,

    c.consensus_price,
    c.consensus_probability,
    c.devig_method,
    c.book_count,
    c.devig_book_count,
    c.dispersion,
    c.modal_line,
    c.is_modal_line,
    c.distinct_line_count,
    c.market_quality,
    c.quality_reasons
FROM canonical c
CROSS JOIN cfg
JOIN exec_best b   ON b.event_id = c.event_id AND b.market_type = c.market_type
                  AND b.selection = c.selection AND b.line IS NOT DISTINCT FROM c.line
WHERE c.market_quality <> 'UNUSABLE'
  AND NOT ('WIDE_DISPERSION' = ANY (c.quality_reasons))
  AND c.book_count >= cfg.mi_execution_min_book_count;


CREATE OR REPLACE VIEW public.market_intelligence
WITH (security_invoker = true) AS
WITH canonical AS MATERIALIZED (SELECT * FROM public.canonical_market),
     executable AS MATERIALIZED (SELECT * FROM public.executable_market),
     movement AS MATERIALIZED (SELECT * FROM public.market_movement)
SELECT
    -- what game
    c.event_id,
    c.source_event_id,
    c.home_team,
    c.away_team,
    c.commence_time,

    -- what wager
    c.market_type,
    c.selection,
    c.line,

    -- where the market is centred
    c.modal_line,
    c.is_modal_line,
    c.modal_line_book_count,
    c.distinct_line_count,

    -- what the market believes (vig removed)
    c.consensus_probability,
    c.consensus_price,
    c.devig_method,

    -- where the best price is, and whether it can actually be taken
    (x.event_id IS NOT NULL)                AS is_executable,
    COALESCE(x.best_price, c.best_price)     AS best_price,
    COALESCE(x.best_book,  c.best_book)      AS best_book,
    x.best_snapshot_id                       AS executable_snapshot_id,
    c.best_price_book_count,
    x.executable_book_count,

    -- how much the books disagree
    c.book_count,
    c.devig_book_count,
    c.dispersion,
    c.outliers_excluded,
    c.avg_overround,

    -- how the market has moved
    m.opening_probability,
    m.opening_price,
    m.probability_movement,
    m.movement_direction,
    m.opening_modal_line,
    m.line_movement,
    m.opening_captured_at,
    c.current_captured_at,

    -- whether Open Ledger considers it safe
    c.market_quality,
    c.quality_reasons
FROM canonical c
LEFT JOIN executable x
       ON x.event_id = c.event_id AND x.market_type = c.market_type
      AND x.selection = c.selection AND x.line IS NOT DISTINCT FROM c.line
LEFT JOIN movement m
       ON m.event_id = c.event_id AND m.market_type = c.market_type
      AND m.selection = c.selection AND m.line IS NOT DISTINCT FROM c.line;
