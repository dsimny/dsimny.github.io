-- =============================================================================
-- validate_market_pairing.sql -- Package #4 pre-implementation validation
-- =============================================================================
-- Read-only. Answers the six questions that must hold before migrations 037+
-- are written, against LIVE ingested data rather than fixtures.
--
--   1. spread side pairing        home -x  <->  away +x
--   2. total pairing              OVER x   <->  UNDER x
--   3. exact-line matching only   no near-miss pairs
--   4. NO_DEVIG_PAIR frequency
--   5. two-book vs three-plus-book market frequency
--   6. does a 2-book execution floor improve coverage without admitting
--      structurally bad markets
--
-- "Eligible" throughout means: newest observation per (key, book), non-in-play,
-- within snapshot_ttl_seconds, on an event that is not closed. Identical to the
-- pre-registration's eligibility rule.
-- =============================================================================

\set ON_ERROR_STOP on

CREATE TEMP VIEW eligible AS
SELECT DISTINCT ON (s.event_id, s.market_type, s.selection, s.line, s.sportsbook)
       s.event_id, s.market_type, s.selection, s.line, s.sportsbook,
       s.price, s.captured_at
FROM public.market_snapshots s
JOIN public.events e ON e.id = s.event_id
CROSS JOIN public.system_settings cfg
WHERE s.is_in_play = FALSE
  AND e.is_closed = FALSE
  AND cfg.id = TRUE
  AND NOW() - s.captured_at <= make_interval(secs => cfg.snapshot_ttl_seconds)
ORDER BY s.event_id, s.market_type, s.selection, s.line, s.sportsbook,
         s.captured_at DESC, s.ingest_seq DESC;

\echo ''
\echo '=== 0. SCOPE: what are we validating against? ==='
SELECT count(DISTINCT event_id) AS events,
       count(*)                 AS eligible_observations,
       count(DISTINCT sportsbook) AS books,
       min(captured_at)::timestamptz(0) AS oldest,
       max(captured_at)::timestamptz(0) AS newest
FROM eligible;

\echo ''
\echo '=== 1+2+3. PAIRING: does every side have its exact-line partner? ==='
\echo '--- expect unpaired = 0 and near_miss = 0 ---'
WITH paired AS (
  SELECT e.*,
         EXISTS (
           SELECT 1 FROM eligible p
           WHERE p.event_id = e.event_id
             AND p.market_type = e.market_type
             AND p.sportsbook = e.sportsbook
             AND CASE e.market_type
                   WHEN 'MONEYLINE' THEN p.selection <> e.selection AND p.line IS NULL
                   WHEN 'SPREAD'    THEN p.selection <> e.selection AND p.line = -e.line
                   WHEN 'TOTAL'     THEN p.selection <> e.selection AND p.line = e.line
                 END
         ) AS has_exact_partner,
         EXISTS (
           SELECT 1 FROM eligible p
           WHERE p.event_id = e.event_id
             AND p.market_type = e.market_type
             AND p.sportsbook = e.sportsbook
             AND p.selection <> e.selection
             AND CASE e.market_type
                   WHEN 'SPREAD' THEN p.line IS DISTINCT FROM -e.line
                   WHEN 'TOTAL'  THEN p.line IS DISTINCT FROM  e.line
                   ELSE FALSE
                 END
         ) AS has_wrong_line_counterpart
  FROM eligible e
)
SELECT market_type,
       count(*)                                              AS sides,
       count(*) FILTER (WHERE has_exact_partner)             AS paired,
       count(*) FILTER (WHERE NOT has_exact_partner)         AS unpaired,
       count(*) FILTER (WHERE NOT has_exact_partner
                          AND has_wrong_line_counterpart)    AS near_miss_only,
       round(100.0 * count(*) FILTER (WHERE NOT has_exact_partner)
             / NULLIF(count(*), 0), 2)                       AS pct_unpaired
FROM paired GROUP BY market_type ORDER BY market_type;

\echo ''
\echo '--- 3b. NEAR MISSES: books quoting both sides at MISMATCHED lines ---'
\echo '--- these are the rows a naive implementation would wrongly pair ---'
SELECT e.market_type, e.event_id, e.sportsbook,
       e.selection AS side_a, e.line AS line_a,
       p.selection AS side_b, p.line AS line_b
FROM eligible e
JOIN eligible p
  ON p.event_id = e.event_id AND p.market_type = e.market_type
 AND p.sportsbook = e.sportsbook AND p.selection <> e.selection
WHERE e.market_type IN ('SPREAD','TOTAL')
  AND CASE e.market_type
        WHEN 'SPREAD' THEN p.line IS DISTINCT FROM -e.line
        WHEN 'TOTAL'  THEN p.line IS DISTINCT FROM  e.line
      END
  AND NOT EXISTS (
        SELECT 1 FROM eligible q
        WHERE q.event_id=e.event_id AND q.market_type=e.market_type
          AND q.sportsbook=e.sportsbook AND q.selection<>e.selection
          AND CASE e.market_type WHEN 'SPREAD' THEN q.line = -e.line
                                 WHEN 'TOTAL'  THEN q.line =  e.line END)
ORDER BY 1,2,3 LIMIT 20;

\echo ''
\echo '=== 4. NO_DEVIG_PAIR frequency, per canonical key ==='
WITH k AS (
  SELECT e.event_id, e.market_type, e.selection, e.line, e.sportsbook,
         EXISTS (SELECT 1 FROM eligible p
                 WHERE p.event_id=e.event_id AND p.market_type=e.market_type
                   AND p.sportsbook=e.sportsbook AND p.selection<>e.selection
                   AND CASE e.market_type
                         WHEN 'MONEYLINE' THEN p.line IS NULL
                         WHEN 'SPREAD'    THEN p.line = -e.line
                         WHEN 'TOTAL'     THEN p.line =  e.line END) AS pairable
  FROM eligible e
)
SELECT market_type,
       count(DISTINCT (event_id, selection, line))                       AS canonical_keys,
       count(*) FILTER (WHERE pairable)                                  AS pairable_books,
       count(*) FILTER (WHERE NOT pairable)                              AS unpairable_books,
       round(100.0*count(*) FILTER (WHERE NOT pairable)/NULLIF(count(*),0),2) AS pct_unpairable
FROM k GROUP BY market_type ORDER BY market_type;

\echo ''
\echo '=== 5. BOOK COUNT DISTRIBUTION per canonical key ==='
WITH bc AS (
  SELECT event_id, market_type, selection, line,
         count(DISTINCT sportsbook) AS books
  FROM eligible GROUP BY 1,2,3,4
)
SELECT books,
       count(*) AS canonical_keys,
       round(100.0*count(*)/SUM(count(*)) OVER (), 2) AS pct
FROM bc GROUP BY books ORDER BY books;

\echo ''
\echo '--- 5b. rolled up against the two thresholds ---'
WITH bc AS (
  SELECT event_id, market_type, selection, line,
         count(DISTINCT sportsbook) AS books
  FROM eligible GROUP BY 1,2,3,4
)
SELECT count(*)                                   AS keys_total,
       count(*) FILTER (WHERE books = 1)          AS one_book_fails_closed,
       count(*) FILTER (WHERE books >= 2)         AS executable_floor_2,
       count(*) FILTER (WHERE books >= 3)         AS advisory_ok_3,
       round(100.0*count(*) FILTER (WHERE books>=2)/NULLIF(count(*),0),2) AS pct_at_floor_2,
       round(100.0*count(*) FILTER (WHERE books>=3)/NULLIF(count(*),0),2) AS pct_at_floor_3
FROM bc;

\echo ''
\echo '=== 6. DOES THE 2-BOOK FLOOR ADMIT STRUCTURALLY BAD MARKETS? ==='
\echo '--- dispersion of RAW implied probability by book count ---'
\echo '--- if 2-book markets are not materially wider, the floor is safe ---'
WITH p AS (
  SELECT event_id, market_type, selection, line, sportsbook,
         CASE WHEN price >= 100 THEN 100.0/(price+100)
              ELSE abs(price)::numeric/(abs(price)+100) END AS implied
  FROM eligible
), d AS (
  SELECT event_id, market_type, selection, line,
         count(DISTINCT sportsbook) AS books,
         max(implied) - min(implied) AS dispersion
  FROM p GROUP BY 1,2,3,4
)
SELECT CASE WHEN books = 1 THEN '1 book'
            WHEN books = 2 THEN '2 books'
            WHEN books BETWEEN 3 AND 4 THEN '3-4 books'
            ELSE '5+ books' END                       AS cohort,
       count(*)                                       AS keys,
       round(avg(dispersion), 4)                      AS avg_dispersion,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dispersion)::numeric, 4) AS median_dispersion,
       round(max(dispersion), 4)                      AS max_dispersion,
       count(*) FILTER (WHERE dispersion > 0.05)      AS would_be_WIDE
FROM d GROUP BY 1 ORDER BY 1;

\echo ''
\echo '--- 6b. event-level coverage gain from floor 2 vs floor 3 ---'
WITH bc AS (
  SELECT event_id, market_type, selection, line,
         count(DISTINCT sportsbook) AS books
  FROM eligible GROUP BY 1,2,3,4
)
SELECT count(DISTINCT event_id)                                        AS events_total,
       count(DISTINCT event_id) FILTER (WHERE books >= 2)              AS events_with_any_floor2_market,
       count(DISTINCT event_id) FILTER (WHERE books >= 3)              AS events_with_any_floor3_market
FROM bc;
