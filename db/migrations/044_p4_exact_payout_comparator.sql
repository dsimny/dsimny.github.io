-- =============================================================================
-- 044_p4_exact_payout_comparator.sql -- stop ranking prices with a money
-- function that rounds
-- =============================================================================
-- FOUND BY THE SIGN-OFF SCRIPT, not by the test suite.
--
-- THE DEFECT. `best_price` was ranked with
-- `olp_american_profit(1, price) DESC`. That function is Package #1's MONEY
-- function: it rounds to 2 decimal places, because ledger amounts are in cents
-- and that is correct for money. It is not correct as a COMPARATOR.
--
-- On a 1-unit stake, adjacent American prices collapse into the same cent:
--
--     -142  ->  100/142 = 0.704225  ->  round(...,2) = 0.70
--     -143  ->  100/143 = 0.699301  ->  round(...,2) = 0.70
--
-- They tie, the `sportsbook ASC` tie-break then decides, and it can pick the
-- WORSE price. Observed on a seeded board: with book4 (-141) excluded as
-- superseded, the executable surface named book2 at -143 while book3 was
-- offering -142. The surface claimed a best executable price that was not the
-- best executable price.
--
-- The collision is not rare. Any two prices whose per-unit payouts fall in the
-- same cent bucket tie, which is most adjacent pairs once magnitudes exceed
-- roughly -110.
--
-- THE FIX. A dedicated, UNROUNDED comparator. `olp_american_profit` is left
-- exactly as it is -- it is right for money and Package #1 froze it, tickets
-- carry its output as `potential_profit`, and nothing about payouts actually
-- paid changes here. Only the ORDER BY that decides which quote is best moves
-- to the exact value.
--
-- SCOPE. Two ORDER BY clauses: `canonical_market.best` and
-- `executable_market.exec_best`. No gate, threshold, partner rule, de-vig,
-- modal rule or aggregation is touched.
--
-- NOT FIXED HERE, REPORTED INSTEAD: `ticket_closing_line_value` (028, Package
-- #2, FROZEN as pkg2-v1.0) compares `olp_american_profit(1, accepted_price)`
-- against `olp_american_profit(1, closing_price)` for `beat_close`, and carries
-- the identical rounding collision -- a ticket that beat the close by one point
-- can read as not having beaten it. Package #2's semantics are frozen and are
-- not changed as a side effect of a Package #4 fix.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Profit per 1 unit staked, EXACT. Deliberately not rounded, and deliberately
-- separate from olp_american_profit so that no caller can confuse "what ranks
-- higher" with "what gets paid".
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_price_payout(p_price INT)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT CASE
               WHEN p_price >= 100 THEN p_price::numeric / 100.0
               ELSE                     100.0 / abs(p_price)::numeric
           END;
$fn$;

COMMENT ON FUNCTION public.olp_price_payout(INT) IS
    'Exact profit per 1 unit staked, for RANKING prices. Never rounded. Use '
    'olp_american_profit for money -- it rounds to cents, which is correct for '
    'ledger amounts and wrong for comparisons.';

REVOKE ALL ON FUNCTION public.olp_price_payout(INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.olp_price_payout(INT)
    TO anon, authenticated, service_role;

CREATE OR REPLACE VIEW public.canonical_market
WITH (security_invoker = true) AS
WITH cfg AS (
    SELECT * FROM public.system_settings WHERE id = TRUE
),
-- Newest eligible observation per (canonical key, book).
-- Eligibility is Package #1's TTL reused verbatim -- Package #4 introduces no
-- second freshness constant.
newest AS MATERIALIZED (
    SELECT DISTINCT ON (s.event_id, s.market_type, s.selection, s.line, s.sportsbook)
           s.id AS snapshot_id, s.event_id, s.market_type, s.selection, s.line,
           s.sportsbook, s.price, s.captured_at
    FROM public.market_snapshots s
    JOIN public.events e ON e.id = s.event_id
    CROSS JOIN cfg
    WHERE s.is_in_play  = FALSE
      AND e.is_closed   = FALSE
      AND s.captured_at <= NOW()
      AND NOW() - s.captured_at <= make_interval(secs => cfg.snapshot_ttl_seconds)
    ORDER BY s.event_id, s.market_type, s.selection, s.line, s.sportsbook,
             s.captured_at DESC, s.ingest_seq DESC
),
-- De-vig partner, from the SAME book, market-type aware.
--   MONEYLINE : other selection, both lines NULL
--   SPREAD    : other selection at the NEGATED line   (DAL -3 <-> PHI +3)
--   TOTAL     : OVER <-> UNDER at the SAME line
-- A near miss (DAL -3 against PHI +3.5) yields NO partner. It is never paired
-- with the nearest line; that is the exact leak this package exists to refuse.
partnered AS (
    SELECT n.*,
           public.olp_implied_probability(n.price) AS raw_p,
           pr.partner_raw_p
    FROM newest n
    CROSS JOIN cfg
    LEFT JOIN LATERAL (
        SELECT public.olp_implied_probability(p.price) AS partner_raw_p
        FROM public.market_snapshots p
        WHERE p.event_id    = n.event_id
          AND p.market_type = n.market_type
          AND p.sportsbook  = n.sportsbook
          AND p.selection  <> n.selection
          AND p.is_in_play  = FALSE
          AND p.captured_at <= NOW()
          AND NOW() - p.captured_at
              <= make_interval(secs => cfg.snapshot_ttl_seconds)
          AND CASE n.market_type
                  WHEN 'MONEYLINE' THEN p.line IS NULL
                  WHEN 'SPREAD'    THEN p.line = -n.line
                  WHEN 'TOTAL'     THEN p.line =  n.line
              END
        ORDER BY p.captured_at DESC, p.ingest_seq DESC
        LIMIT 1
    ) pr ON TRUE
),
-- De-vig PER BOOK, before any aggregation. Aggregating raw probabilities first
-- and de-vigging the average would inherit a blend of different overrounds.
fair AS (
    SELECT p.*,
           public.olp_devig_multiplicative(p.raw_p, p.partner_raw_p) AS fair_p,
           public.olp_overround(p.raw_p, p.partner_raw_p)            AS overround
    FROM partnered p
),
-- First-pass median, used only to identify outliers.
med AS (
    SELECT event_id, market_type, selection, line,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY fair_p)::numeric AS median_p,
           count(fair_p) AS devig_books
    FROM fair
    GROUP BY 1, 2, 3, 4
),
-- Rank candidates by distance so the "never more than a third" cap removes the
-- worst offenders first, deterministically.
ranked AS (
    SELECT f.*, m.median_p, m.devig_books,
           row_number() OVER (
               PARTITION BY f.event_id, f.market_type, f.selection, f.line
               ORDER BY abs(f.fair_p - m.median_p) DESC NULLS LAST, f.sportsbook ASC
           ) AS distance_rank
    FROM fair f
    JOIN med m ON m.event_id = f.event_id AND m.market_type = f.market_type
              AND m.selection = f.selection AND m.line IS NOT DISTINCT FROM f.line
),
-- No outlier removal below mi_outlier_min_books: with three books, removing one
-- leaves two and "the outlier" is indistinguishable from "the two that agree
-- are both wrong".
kept AS (
    SELECT r.*,
           (r.fair_p IS NOT NULL
            AND r.devig_books >= cfg.mi_outlier_min_books
            AND abs(r.fair_p - r.median_p) > cfg.mi_outlier_probability_delta
            AND r.distance_rank <= floor(r.devig_books / 3.0)) AS is_excluded_outlier
    FROM ranked r CROSS JOIN cfg
),
-- Aggregate to the canonical key.
agg AS (
    SELECT event_id, market_type, selection, line,
           count(*)                                                   AS book_count,
           count(fair_p) FILTER (WHERE NOT is_excluded_outlier)       AS devig_book_count,
           count(*) FILTER (WHERE is_excluded_outlier)                AS outliers_excluded,
           count(*) FILTER (WHERE fair_p IS NULL)                     AS unpairable_books,
           (percentile_cont(0.5) WITHIN GROUP (
               ORDER BY fair_p) FILTER (WHERE NOT is_excluded_outlier))::numeric
                                                                      AS consensus_probability,
           max(fair_p) FILTER (WHERE NOT is_excluded_outlier)
             - min(fair_p) FILTER (WHERE NOT is_excluded_outlier)     AS dispersion,
           avg(overround) FILTER (WHERE NOT is_excluded_outlier)      AS avg_overround,
           max(captured_at)                                           AS current_captured_at
    FROM kept
    GROUP BY 1, 2, 3, 4
),
-- Best price: payout ordering, never numeric comparison of American odds.
-- A book needs no de-vig partner to offer the best price -- best-price
-- eligibility and consensus eligibility are different sets, by design.
best AS (
    SELECT DISTINCT ON (event_id, market_type, selection, line)
           event_id, market_type, selection, line,
           price       AS best_price,
           sportsbook  AS best_book,
           snapshot_id AS best_snapshot_id
    FROM newest
    ORDER BY event_id, market_type, selection, line,
             public.olp_price_payout(price) DESC,
             sportsbook ASC                      -- stable, not "freshest"
),
best_ties AS (
    SELECT n.event_id, n.market_type, n.selection, n.line, count(*) AS best_price_book_count
    FROM newest n
    JOIN best b ON b.event_id = n.event_id AND b.market_type = n.market_type
               AND b.selection = n.selection AND b.line IS NOT DISTINCT FROM n.line
    WHERE n.price = b.best_price
    GROUP BY 1, 2, 3, 4
),
-- Modal line, one level ABOVE the canonical key: it describes where the market
-- is concentrated, and never defines which line is "true".
--
-- TIE-BREAK: book count DESC, then line ASC. Total and explicit -- line values
-- within a group are distinct by construction, so exactly one row can win, and
-- nothing depends on query order.
--
-- Recency was deliberately REJECTED as a tie-break. It is deterministic but not
-- STABLE: with two lines tied on book count, whichever book happened to update
-- last would take the modal flag, so the reported modal line would flip between
-- polls while the market itself had not moved. That is the same flapping the
-- best_book tie-break avoids by preferring alphabetical order over freshest,
-- and the same reasoning has to apply here. A modal line that changes when
-- nothing changed is worse than an arbitrary but steady one.
line_pop AS (
    SELECT event_id, market_type, selection, line,
           count(DISTINCT sportsbook) AS line_books,
           max(captured_at)           AS line_newest
    FROM newest
    GROUP BY 1, 2, 3, 4
),
modal AS (
    SELECT DISTINCT ON (event_id, market_type, selection)
           event_id, market_type, selection,
           line       AS modal_line,
           line_books AS modal_line_book_count
    FROM line_pop
    -- Book count is the ONLY substantive criterion. abs(line) and line are
    -- deterministic tie-breakers and carry no economic meaning -- see 043.
    ORDER BY event_id, market_type, selection,
             line_books DESC, abs(line) ASC, line ASC
),
line_spread AS (
    SELECT event_id, market_type, selection, count(*) AS distinct_line_count
    FROM line_pop
    GROUP BY 1, 2, 3
)
SELECT
    a.event_id,
    e.source_event_id,
    e.home_team,
    e.away_team,
    e.current_scheduled_start                                   AS commence_time,
    a.market_type,
    a.selection,
    a.line,

    b.best_price,
    b.best_book,
    bt.best_price_book_count,
    b.best_snapshot_id,

    public.olp_fair_american(a.consensus_probability)           AS consensus_price,
    round(a.consensus_probability, 6)                           AS consensus_probability,
    cfg.mi_devig_method                                         AS devig_method,

    a.book_count,
    a.devig_book_count,
    round(a.dispersion, 6)                                      AS dispersion,
    a.outliers_excluded,
    round(a.avg_overround, 6)                                   AS avg_overround,

    m.modal_line,
    (a.line IS NOT DISTINCT FROM m.modal_line)                  AS is_modal_line,
    m.modal_line_book_count,
    ls.distinct_line_count,

    a.current_captured_at,

    -- Quality: advisory. Reasons accumulate and are never collapsed.
    CASE
        WHEN a.book_count = 0
          OR a.devig_book_count = 0
          OR a.book_count = 1
          OR a.consensus_probability IS NULL
          OR a.consensus_probability <= 0
          OR a.consensus_probability >= 1
            THEN 'UNUSABLE'
        WHEN a.book_count < cfg.mi_min_book_count
          OR a.dispersion > cfg.mi_dispersion_wide_threshold
          OR a.outliers_excluded > 0
          OR a.unpairable_books > 0
          OR ls.distinct_line_count > cfg.mi_line_fragmentation_max
            THEN 'DEGRADED'
        ELSE 'OK'
    END::public.market_quality                                  AS market_quality,

    array_remove(ARRAY[
        CASE WHEN a.book_count = 1 THEN 'SINGLE_BOOK' END,
        CASE WHEN a.book_count = 0 THEN 'NO_ELIGIBLE_BOOKS' END,
        CASE WHEN a.devig_book_count = 0 AND a.book_count > 0 THEN 'NO_DEVIG_PAIR' END,
        CASE WHEN a.unpairable_books > 0 AND a.devig_book_count > 0
             THEN 'PARTIAL_DEVIG_COVERAGE' END,
        CASE WHEN a.book_count > 1 AND a.book_count < cfg.mi_min_book_count
             THEN 'LOW_BOOK_COUNT' END,
        CASE WHEN a.dispersion > cfg.mi_dispersion_wide_threshold
             THEN 'WIDE_DISPERSION' END,
        CASE WHEN a.outliers_excluded > 0 THEN 'OUTLIERS_REMOVED' END,
        CASE WHEN a.consensus_probability IS NOT NULL
              AND (a.consensus_probability <= 0 OR a.consensus_probability >= 1)
             THEN 'DEGENERATE_PROBABILITY' END,
        CASE WHEN ls.distinct_line_count > cfg.mi_line_fragmentation_max
             THEN 'LINE_FRAGMENTED' END
    ], NULL)                                                    AS quality_reasons

FROM agg a
CROSS JOIN cfg
JOIN public.events e ON e.id = a.event_id
LEFT JOIN best  b  ON b.event_id = a.event_id AND b.market_type = a.market_type
                  AND b.selection = a.selection AND b.line IS NOT DISTINCT FROM a.line
LEFT JOIN best_ties bt ON bt.event_id = a.event_id AND bt.market_type = a.market_type
                  AND bt.selection = a.selection AND bt.line IS NOT DISTINCT FROM a.line
LEFT JOIN modal m  ON m.event_id = a.event_id AND m.market_type = a.market_type
                  AND m.selection = a.selection
LEFT JOIN line_spread ls ON ls.event_id = a.event_id AND ls.market_type = a.market_type
                  AND ls.selection = a.selection;

COMMENT ON VIEW public.canonical_market IS
    'One row per (event, market, selection, line). Same-line only: nothing is '
    'compared or blended across line values. Quality is advisory here; gating '
    'lives in executable_market.';

-- Supports the canonical key lookup and the modal-line filter, which live data
-- showed is the filter that makes this surface usable (74.5% of modal keys
-- clear the execution floor, versus 51.1% of all keys).
CREATE INDEX IF NOT EXISTS idx_snapshots_canonical
    ON public.market_snapshots (event_id, market_type, selection, line, sportsbook,
                                captured_at DESC, ingest_seq DESC)
    WHERE is_in_play = FALSE;

REVOKE ALL ON public.canonical_market FROM PUBLIC;
GRANT SELECT ON public.canonical_market TO anon, authenticated, service_role;


CREATE OR REPLACE VIEW public.market_movement
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
        FROM public.market_snapshots p
        WHERE p.event_id    = n.event_id
          AND p.market_type = n.market_type
          AND p.sportsbook  = n.sportsbook
          AND p.selection  <> n.selection
          AND p.is_in_play  = FALSE
          AND CASE n.market_type
                  WHEN 'MONEYLINE' THEN p.line IS NULL
                  WHEN 'SPREAD'    THEN p.line = -n.line
                  WHEN 'TOTAL'     THEN p.line =  n.line
              END
        ORDER BY p.captured_at ASC, p.ingest_seq ASC
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
    -- Same tie-break as canonical_market. See 043.
    ORDER BY event_id, market_type, selection,
             line_books DESC, abs(line) ASC, line ASC
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


CREATE OR REPLACE VIEW public.executable_market
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
             public.olp_price_payout(price) DESC,
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
