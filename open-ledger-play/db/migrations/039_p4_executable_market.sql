-- =============================================================================
-- 039_p4_executable_market.sql -- the execution-gated surface
-- =============================================================================
-- Everything canonical_market reports advisorily, this view enforces.
--
-- THE CRITICAL INTERACTION. place_ticket_rpc's MARKET_MOVED check is per
-- (event, market_type, selection, sportsbook) and does NOT filter on line. So
-- when a book moves -3 -> -3.5, its -3 snapshot stops being that book's latest
-- and becomes unplaceable -- correctly, because the book no longer offers -3.
--
-- Consequence: this view cannot simply filter canonical rows. It must RECOMPUTE
-- best price over only still-placeable observations. Reusing canonical
-- best_price would let the surface name a best_book whose quote the RPC would
-- reject, which is the disagreement PACKAGE2 3.9 exists to prevent.
--
-- A canonical row every book has moved off remains visible in canonical_market
-- as history, and is simply absent here.
-- =============================================================================

CREATE VIEW public.executable_market
WITH (security_invoker = true) AS
WITH cfg AS (
    SELECT * FROM public.system_settings WHERE id = TRUE
),
-- Observations place_ticket_rpc would actually accept, right now.
placeable_obs AS (
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
exec_best AS (
    SELECT DISTINCT ON (event_id, market_type, selection, line)
           event_id, market_type, selection, line,
           price       AS exec_best_price,
           sportsbook  AS exec_best_book,
           snapshot_id AS exec_best_snapshot_id,
           captured_at AS exec_best_captured_at
    FROM placeable_obs
    ORDER BY event_id, market_type, selection, line,
             public.olp_american_profit(1, price) DESC,
             sportsbook ASC
),
exec_counts AS (
    SELECT event_id, market_type, selection, line,
           count(DISTINCT sportsbook) AS executable_book_count
    FROM placeable_obs
    GROUP BY 1, 2, 3, 4
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
    x.executable_book_count,

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
FROM public.canonical_market c
CROSS JOIN cfg
JOIN exec_best b   ON b.event_id = c.event_id AND b.market_type = c.market_type
                  AND b.selection = c.selection AND b.line IS NOT DISTINCT FROM c.line
JOIN exec_counts x ON x.event_id = c.event_id AND x.market_type = c.market_type
                  AND x.selection = c.selection AND x.line IS NOT DISTINCT FROM c.line
WHERE c.market_quality <> 'UNUSABLE'
  AND NOT ('WIDE_DISPERSION' = ANY (c.quality_reasons))
  AND c.book_count >= cfg.mi_execution_min_book_count;

COMMENT ON VIEW public.executable_market IS
    'Execution-gated market. Every best_snapshot_id here is a snapshot '
    'place_ticket_rpc will accept: TTL-fresh, pre-kickoff, and still its own '
    'book''s latest quote for the selection across all lines. Deliberately NOT '
    'restricted to the modal line -- a superior alternate number is a legitimate '
    'wager, and refusing to expose it because most books sit elsewhere would be '
    'the wrong kind of caution.';

REVOKE ALL ON public.executable_market FROM PUBLIC;
GRANT SELECT ON public.executable_market TO anon, authenticated, service_role;
