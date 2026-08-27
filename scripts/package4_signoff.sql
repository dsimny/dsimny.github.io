-- =============================================================================
-- package4_signoff.sql -- end-to-end Package #4 validation on a captured slate
-- =============================================================================
-- Run AFTER a fresh live ingest, against the database that holds it.
--
--   psql "$OLP_DATABASE_URL" -f scripts/package4_signoff.sql
--
-- READ-ONLY. Writes nothing, changes nothing, places nothing.
--
-- Checks 1-10 are the sign-off report. Every violation check is written so that
-- ZERO is the passing answer and the query names what failed, not just how many.
-- Check 11 is the finish-line query: one game, the whole contract.
--
-- TIMING. The executable surface is TTL-bound (snapshot_ttl_seconds, 120s by
-- default). Run this within two minutes of the ingest, or checks 3 and 10 will
-- correctly report zero executable rows -- which is the TTL working, not a
-- defect. Checks 1, 2, 5, 6, 7 are equally TTL-bound; check 9 is not, because
-- movement is history.
-- =============================================================================

\timing on
\pset pager off

\echo ''
\echo '######## PACKAGE #4 LIVE SIGN-OFF ########'

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 0. BOARD CENSUS ==='
-- -----------------------------------------------------------------------------
SELECT
    (SELECT count(*) FROM public.events)                             AS events,
    (SELECT count(*) FROM public.market_snapshots)                   AS quotes,
    (SELECT count(DISTINCT sportsbook) FROM public.market_snapshots) AS books,
    (SELECT count(*) FROM public.market_snapshots s
      CROSS JOIN public.system_settings c WHERE c.id
        AND NOW() - s.captured_at
            <= make_interval(secs => c.snapshot_ttl_seconds))        AS fresh_quotes,
    (SELECT round(extract(epoch FROM max(NOW() - captured_at)))
       FROM public.market_snapshots)                                 AS oldest_secs;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 1. CANONICAL ROW COUNT (must equal distinct fresh keys) ==='
-- -----------------------------------------------------------------------------
WITH expected AS (
    SELECT count(*) AS n FROM (
        SELECT DISTINCT s.event_id, s.market_type, s.selection, s.line
        FROM public.market_snapshots s
        JOIN public.events e ON e.id = s.event_id
        CROSS JOIN public.system_settings c
        WHERE c.id AND s.is_in_play = FALSE AND e.is_closed = FALSE
          AND s.captured_at <= NOW()
          AND NOW() - s.captured_at <= make_interval(secs => c.snapshot_ttl_seconds)) d
),
actual AS (SELECT count(*) AS n FROM public.canonical_market)
SELECT actual.n AS canonical_rows, expected.n AS distinct_fresh_keys,
       CASE WHEN expected.n = actual.n THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM expected, actual;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 2. MODAL ROW COUNT (exactly one modal line per event/market/selection) ==='
-- -----------------------------------------------------------------------------
SELECT
    count(*) FILTER (WHERE is_modal_line)                    AS modal_rows,
    count(DISTINCT (event_id, market_type, selection))       AS selections,
    count(*)                                                 AS canonical_rows,
    CASE WHEN count(*) FILTER (WHERE is_modal_line)
            = count(DISTINCT (event_id, market_type, selection))
         THEN 'PASS' ELSE 'FAIL -- a selection has zero or several modal lines'
    END AS verdict
FROM public.canonical_market;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 3. EXECUTABLE ROW COUNT (must be a strict subset of canonical) ==='
-- -----------------------------------------------------------------------------
SELECT
    (SELECT count(*) FROM public.canonical_market)  AS canonical_rows,
    (SELECT count(*) FROM public.executable_market) AS executable_rows,
    (SELECT count(*) FROM public.executable_market x
      WHERE NOT EXISTS (SELECT 1 FROM public.canonical_market c
                        WHERE c.event_id = x.event_id AND c.market_type = x.market_type
                          AND c.selection = x.selection
                          AND c.line IS NOT DISTINCT FROM x.line)) AS orphans,
    CASE WHEN (SELECT count(*) FROM public.executable_market)
              <= (SELECT count(*) FROM public.canonical_market)
          AND (SELECT count(*) FROM public.executable_market x
               WHERE NOT EXISTS (SELECT 1 FROM public.canonical_market c
                                 WHERE c.event_id = x.event_id AND c.market_type = x.market_type
                                   AND c.selection = x.selection
                                   AND c.line IS NOT DISTINCT FROM x.line)) = 0
         THEN 'PASS' ELSE 'FAIL' END AS verdict;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 4. MARKET-QUALITY DISTRIBUTION ==='
-- -----------------------------------------------------------------------------
SELECT market_quality, count(*) AS rows,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM public.canonical_market GROUP BY 1 ORDER BY 2 DESC;

\echo '--- reason codes (a row may carry several) ---'
SELECT reason, count(*) AS rows,
       round(100.0 * count(*)
             / (SELECT count(*) FROM public.canonical_market), 1) AS pct_of_board
FROM public.canonical_market, unnest(quality_reasons) AS reason
GROUP BY 1 ORDER BY 2 DESC;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 5. SPREAD MODAL MIRROR VIOLATIONS (must be 0) ==='
-- -----------------------------------------------------------------------------
-- The two sides of one wager must name the SAME wager. This is the check that
-- migration 043 exists to satisfy; under the old `line ASC` tie-break it failed
-- on a third of tied spread wagers.
WITH sides AS (
    SELECT DISTINCT a.event_id, a.market_type, a.home_team, a.away_team,
           a.selection AS sel_a, a.modal_line AS modal_a,
           b.selection AS sel_b, b.modal_line AS modal_b,
           CASE a.market_type
               WHEN 'SPREAD' THEN a.modal_line = -b.modal_line
               WHEN 'TOTAL'  THEN a.modal_line =  b.modal_line
               ELSE TRUE
           END AS mirrored
    FROM public.canonical_market a
    JOIN public.canonical_market b
      ON b.event_id = a.event_id AND b.market_type = a.market_type
     AND b.selection > a.selection
    WHERE a.modal_line IS NOT NULL AND b.modal_line IS NOT NULL
)
SELECT market_type,
       count(*) FILTER (WHERE NOT mirrored) AS violations,
       count(*)                             AS wagers,
       CASE WHEN count(*) FILTER (WHERE NOT mirrored) = 0
            THEN 'PASS' ELSE 'FAIL' END     AS verdict
FROM sides GROUP BY 1 ORDER BY 1;

\echo '--- any violating wagers, named ---'
WITH sides AS (
    SELECT DISTINCT a.event_id, a.home_team, a.away_team, a.market_type,
           a.selection AS sel_a, a.modal_line AS modal_a,
           b.selection AS sel_b, b.modal_line AS modal_b
    FROM public.canonical_market a
    JOIN public.canonical_market b
      ON b.event_id = a.event_id AND b.market_type = a.market_type
     AND b.selection > a.selection
    WHERE a.modal_line IS NOT NULL AND b.modal_line IS NOT NULL
      AND a.market_type = 'SPREAD' AND a.modal_line <> -b.modal_line
)
SELECT home_team, away_team, sel_a, modal_a, sel_b, modal_b FROM sides LIMIT 20;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 6. DE-VIG PAIR FAILURES ==='
-- -----------------------------------------------------------------------------
-- NO_DEVIG_PAIR is legitimate (a book quoting one side only), so this reports
-- incidence rather than asserting zero. What MUST be zero is a row that has a
-- pair count but no probability, or a probability with no pair.
SELECT market_type,
       count(*)                                                  AS rows,
       count(*) FILTER (WHERE devig_book_count = 0)              AS no_pair_rows,
       round(100.0 * count(*) FILTER (WHERE devig_book_count = 0)
             / NULLIF(count(*), 0), 2)                           AS pct_no_pair,
       count(*) FILTER (WHERE devig_book_count > 0
                          AND consensus_probability IS NULL)     AS paired_but_null,
       count(*) FILTER (WHERE devig_book_count = 0
                          AND consensus_probability IS NOT NULL) AS unpaired_but_priced
FROM public.canonical_market GROUP BY 1 ORDER BY 1;

\echo '--- de-vig soundness: both sides of one wager must sum to exactly 1 ---'
WITH paired AS (
    SELECT a.market_type,
           a.consensus_probability + b.consensus_probability AS total
    FROM public.canonical_market a
    JOIN public.canonical_market b
      ON b.event_id = a.event_id AND b.market_type = a.market_type
     AND b.selection > a.selection
     AND CASE a.market_type
             WHEN 'MONEYLINE' THEN b.line IS NULL AND a.line IS NULL
             WHEN 'SPREAD'    THEN b.line = -a.line
             WHEN 'TOTAL'     THEN b.line =  a.line
         END
    WHERE a.consensus_probability IS NOT NULL
      AND b.consensus_probability IS NOT NULL
)
SELECT market_type, count(*) AS paired_wagers, max(abs(total - 1)) AS worst_deviation,
       CASE WHEN max(abs(total - 1)) < 0.000001 THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM paired GROUP BY 1 ORDER BY 1;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 7. CROSS-LINE LEAKAGE VIOLATIONS (must be 0) ==='
-- -----------------------------------------------------------------------------
-- The load-bearing property of Package #4. Every canonical row must be derived
-- ONLY from quotes at its own line. Two independent probes:
--
--   (a) the named best_price/best_book must exist as a real quote AT THAT LINE
--   (b) book_count must equal the number of distinct books quoting THAT LINE
\echo '--- (a) best_price must exist at its own line ---'
SELECT count(*) AS violations,
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM public.canonical_market c
WHERE c.best_price IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM public.market_snapshots s
      WHERE s.event_id = c.event_id AND s.market_type = c.market_type
        AND s.selection = c.selection AND s.line IS NOT DISTINCT FROM c.line
        AND s.sportsbook = c.best_book AND s.price = c.best_price
        AND s.is_in_play = FALSE);

\echo '--- (b) book_count must match books quoting that exact line ---'
WITH truth AS (
    SELECT s.event_id, s.market_type, s.selection, s.line,
           count(DISTINCT s.sportsbook) AS books
    FROM public.market_snapshots s
    JOIN public.events e ON e.id = s.event_id
    CROSS JOIN public.system_settings cfg
    WHERE cfg.id AND s.is_in_play = FALSE AND e.is_closed = FALSE
      AND s.captured_at <= NOW()
      AND NOW() - s.captured_at <= make_interval(secs => cfg.snapshot_ttl_seconds)
    GROUP BY 1, 2, 3, 4
)
SELECT count(*) AS violations,
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM public.canonical_market c
JOIN truth t ON t.event_id = c.event_id AND t.market_type = c.market_type
            AND t.selection = c.selection AND t.line IS NOT DISTINCT FROM c.line
WHERE c.book_count <> t.books;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 8. CANONICAL-BEST vs EXECUTABLE-BEST SUBSTITUTIONS ==='
-- -----------------------------------------------------------------------------
-- Not a failure -- this is the executable layer doing its job. A substitution
-- means canonical named a price that place_ticket_rpc would now reject (the
-- book moved off that line), and the executable layer recomputed over what is
-- still placeable. Reported so the rate is visible.
WITH j AS (
    SELECT c.event_id, c.market_type, c.selection, c.line,
           c.best_price AS canon_price, c.best_book AS canon_book,
           x.best_price AS exec_price,  x.best_book AS exec_book
    FROM public.canonical_market c
    JOIN public.executable_market x
      ON x.event_id = c.event_id AND x.market_type = c.market_type
     AND x.selection = c.selection AND x.line IS NOT DISTINCT FROM c.line
)
SELECT count(*) AS executable_rows,
       count(*) FILTER (WHERE canon_book IS DISTINCT FROM exec_book
                           OR canon_price IS DISTINCT FROM exec_price) AS substitutions,
       round(100.0 * count(*) FILTER (WHERE canon_book IS DISTINCT FROM exec_book
                                         OR canon_price IS DISTINCT FROM exec_price)
             / NULLIF(count(*), 0), 2) AS pct,
       -- a substitution must never IMPROVE on canonical: the executable set is
       -- a subset, so its best can only be equal or worse
       -- olp_price_payout, NOT olp_american_profit: the money function rounds
       -- to cents and would miss a sub-cent improvement, which is the same
       -- precision bug migration 044 fixed in the views themselves.
       count(*) FILTER (WHERE public.olp_price_payout(exec_price)
                            > public.olp_price_payout(canon_price)) AS impossible_improvements
FROM j;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 9. MOVEMENT ROWS AND DIRECTION SANITY ==='
-- -----------------------------------------------------------------------------
SELECT count(*) AS movement_rows,
       count(*) FILTER (WHERE opening_probability IS NOT NULL) AS with_opening,
       count(*) FILTER (WHERE probability_movement IS NOT NULL) AS with_movement
FROM public.market_movement;

\echo '--- direction distribution ---'
SELECT COALESCE(movement_direction, '(null)') AS direction, count(*) AS rows
FROM public.market_movement GROUP BY 1 ORDER BY 2 DESC;

\echo '--- direction must agree with the sign of probability_movement (must be 0) ---'
SELECT count(*) AS violations,
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM public.market_movement m
CROSS JOIN public.system_settings cfg
WHERE cfg.id AND m.probability_movement IS NOT NULL
  AND m.movement_direction IS DISTINCT FROM
      CASE WHEN m.probability_movement >  cfg.mi_movement_epsilon THEN 'IN'
           WHEN m.probability_movement < -cfg.mi_movement_epsilon THEN 'OUT'
           ELSE 'FLAT' END;

\echo '--- one side moving IN implies the other moved OUT (must be 0) ---'
SELECT count(*) AS violations,
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM public.market_movement a
JOIN public.market_movement b
  ON b.event_id = a.event_id AND b.market_type = a.market_type
 AND b.selection > a.selection
 AND CASE a.market_type
         WHEN 'MONEYLINE' THEN b.line IS NULL AND a.line IS NULL
         WHEN 'SPREAD'    THEN b.line = -a.line
         WHEN 'TOTAL'     THEN b.line =  a.line
     END
WHERE a.movement_direction IS NOT NULL AND b.movement_direction IS NOT NULL
  AND a.movement_direction <> 'FLAT'
  AND a.movement_direction = b.movement_direction;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 10. EXECUTION HANDOFF VALIDITY via best_snapshot_id (must be 0) ==='
-- -----------------------------------------------------------------------------
-- Every best_snapshot_id must be a snapshot place_ticket_rpc would accept right
-- now. Replicates the RPC's own preconditions read-only; the round trip through
-- the real RPC is asserted by P4-T19 in the test suite.
SELECT
  count(*) FILTER (WHERE s.id IS NULL)                          AS missing_snapshot,
  count(*) FILTER (WHERE s.event_id    IS DISTINCT FROM x.event_id
                      OR s.market_type IS DISTINCT FROM x.market_type
                      OR s.selection   IS DISTINCT FROM x.selection
                      OR s.line        IS DISTINCT FROM x.line
                      OR s.price       IS DISTINCT FROM x.best_price
                      OR s.sportsbook  IS DISTINCT FROM x.best_book) AS row_mismatch,
  count(*) FILTER (WHERE s.is_in_play)                          AS in_play,
  count(*) FILTER (WHERE NOW() - s.captured_at
                         > make_interval(secs => cfg.snapshot_ttl_seconds)) AS stale,
  count(*) FILTER (WHERE e.is_closed OR e.is_live
                      OR e.actual_start_time IS NOT NULL
                      OR NOW() >= e.current_scheduled_start)     AS event_not_open,
  count(*) FILTER (WHERE s.id <> (
        SELECT y.id FROM public.market_snapshots y
        WHERE y.event_id = s.event_id AND y.market_type = s.market_type
          AND y.selection = s.selection AND y.sportsbook = s.sportsbook
          AND y.is_in_play = FALSE AND y.captured_at <= NOW()
        ORDER BY y.captured_at DESC, y.ingest_seq DESC LIMIT 1)) AS superseded
FROM public.executable_market x
CROSS JOIN public.system_settings cfg
LEFT JOIN public.market_snapshots s ON s.id = x.best_snapshot_id
LEFT JOIN public.events e ON e.id = x.event_id
WHERE cfg.id;

-- -----------------------------------------------------------------------------
\echo ''
\echo '=== 11. FINISH-LINE QUERY (one game, every question answered) ==='
-- -----------------------------------------------------------------------------
WITH game AS (
    SELECT event_id FROM public.market_intelligence
    WHERE is_executable
    GROUP BY 1 ORDER BY count(*) DESC, min(commence_time) LIMIT 1
)
SELECT
    home_team || ' vs ' || away_team          AS matchup,
    market_type, selection, line,
    modal_line                                AS market_centre,
    is_modal_line,
    consensus_probability                     AS side_probability,
    consensus_price                           AS fair_price,
    best_price, best_book,
    is_executable,
    book_count, dispersion                    AS books_disagree_by,
    probability_movement, movement_direction, line_movement,
    market_quality, quality_reasons
FROM public.market_intelligence
WHERE event_id = (SELECT event_id FROM game)
ORDER BY market_type, selection, line;

\echo ''
\echo '######## END OF SIGN-OFF ########'
