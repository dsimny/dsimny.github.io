-- =============================================================================
-- 041_p4_market_intelligence.sql -- the consumer contract
-- =============================================================================
-- The one surface a model reads. It answers, for a given game:
--
--   where is the market centred?          modal_line, is_modal_line
--   what does each side believe?          consensus_probability, consensus_price
--   where is the best executable price?   best_price, best_book, best_snapshot_id
--   how much do the books disagree?       dispersion, book_count
--   how has the market moved?             probability_movement, line_movement
--   is it safe to act on?                 is_executable, market_quality, reasons
--
-- A model should never need to query market_snapshots. If it does, this view
-- has failed at its job, because that is where a second odds implementation
-- starts.
--
-- Package #4 writes nothing. This is a read layer.
-- =============================================================================

CREATE VIEW public.market_intelligence
WITH (security_invoker = true) AS
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
FROM public.canonical_market c
LEFT JOIN public.executable_market x
       ON x.event_id = c.event_id AND x.market_type = c.market_type
      AND x.selection = c.selection AND x.line IS NOT DISTINCT FROM c.line
LEFT JOIN public.market_movement m
       ON m.event_id = c.event_id AND m.market_type = c.market_type
      AND m.selection = c.selection AND m.line IS NOT DISTINCT FROM c.line;

COMMENT ON VIEW public.market_intelligence IS
    'The Package #4 consumer contract. One row per (event, market, selection, '
    'line). is_executable is the single flag a model checks before acting; '
    'executable_snapshot_id can be passed straight to place_ticket_rpc. '
    'best_price falls back to the canonical best when nothing is executable, so '
    'the field is never silently absent -- check is_executable, not NULL.';

REVOKE ALL ON public.market_intelligence FROM PUBLIC;
GRANT SELECT ON public.market_intelligence TO anon, authenticated, service_role;
