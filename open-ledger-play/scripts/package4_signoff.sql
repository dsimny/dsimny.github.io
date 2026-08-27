-- =============================================================================
-- package4_signoff.sql -- end-to-end Package #4 validation on a captured slate
-- =============================================================================
-- Run AFTER a fresh live ingest, against the database that holds it.
--
--   psql "$OLP_DATABASE_URL" -f scripts/package4_signoff.sql
--
-- Read-only. Writes nothing, changes nothing.
--
-- Sections 1-6 are invariants that must hold on real data. Section 7 is the
-- finish-line query: one NFL game, the whole contract, one row per wager.
--
-- TIMING NOTE: the executable surface is TTL-bound (snapshot_ttl_seconds, 120s
-- by default). Run this within two minutes of the ingest or section 4 will
-- correctly report zero executable rows and that is not a failure.
-- =============================================================================

\timing on
\pset pager off

-- -----------------------------------------------------------------------------
-- 1. What was captured
-- -----------------------------------------------------------------------------
\echo '=== 1. BOARD CENSUS ==='
SELECT
    (SELECT count(*) FROM public.events)                          AS events,
    (SELECT count(*) FROM public.market_snapshots)                AS quotes,
    (SELECT count(DISTINCT sportsbook) FROM public.market_snapshots) AS books,
    (SELECT count(*) FROM public.market_snapshots
      WHERE NOW() - captured_at
            <= make_interval(secs => (SELECT snapshot_ttl_seconds
                                      FROM public.system_settings WHERE id))) AS fresh_quotes,
    (SELECT max(NOW() - captured_at) FROM public.market_snapshots) AS oldest_age;

-- -----------------------------------------------------------------------------
-- 2. Row identity -- one canonical row per distinct fresh key, no fan-out
-- -----------------------------------------------------------------------------
\echo '=== 2. ROW IDENTITY (canonical must equal distinct fresh keys) ==='
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
SELECT expected.n AS distinct_keys, actual.n AS canonical_rows,
       CASE WHEN expected.n = actual.n THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM expected, actual;

-- -----------------------------------------------------------------------------
-- 3. De-vig soundness -- two sides of one wager must sum to exactly 1
-- -----------------------------------------------------------------------------
\echo '=== 3. CONSENSUS SUMS TO 1 (worst deviation across the whole board) ==='
WITH paired AS (
    SELECT a.event_id, a.market_type, a.line,
           a.selection AS sel_a, b.selection AS sel_b,
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
SELECT count(*) AS paired_wagers,
       max(abs(total - 1)) AS worst_deviation,
       CASE WHEN count(*) = 0 THEN 'NO PAIRS -- INVESTIGATE'
            WHEN max(abs(total - 1)) < 0.000001 THEN 'PASS'
            ELSE 'FAIL' END AS verdict
FROM paired;

-- -----------------------------------------------------------------------------
-- 4. Executable surface must be a strict subset, and must be placeable
-- -----------------------------------------------------------------------------
\echo '=== 4. EXECUTABLE SUBSET AND RPC PARITY ==='
SELECT
    (SELECT count(*) FROM public.canonical_market)  AS canonical_rows,
    (SELECT count(*) FROM public.executable_market) AS executable_rows,
    (SELECT count(*) FROM public.executable_market x
      WHERE NOT EXISTS (SELECT 1 FROM public.canonical_market c
                        WHERE c.event_id = x.event_id AND c.market_type = x.market_type
                          AND c.selection = x.selection
                          AND c.line IS NOT DISTINCT FROM x.line)) AS orphans_must_be_0,
    -- every executable snapshot must still be its own book's latest, TTL-fresh,
    -- pre-kickoff: exactly what place_ticket_rpc re-checks
    (SELECT count(*) FROM public.executable_market x
      JOIN public.market_snapshots s ON s.id = x.best_snapshot_id
      WHERE s.id <> (SELECT y.id FROM public.market_snapshots y
                     WHERE y.event_id = s.event_id AND y.market_type = s.market_type
                       AND y.selection = s.selection AND y.sportsbook = s.sportsbook
                       AND y.is_in_play = FALSE AND y.captured_at <= NOW()
                     ORDER BY y.captured_at DESC, y.ingest_seq DESC LIMIT 1))
        AS superseded_must_be_0;

-- -----------------------------------------------------------------------------
-- 5. Quality distribution -- what the real board actually looks like
-- -----------------------------------------------------------------------------
\echo '=== 5. QUALITY DISTRIBUTION ==='
SELECT market_quality, count(*) AS rows,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM public.canonical_market GROUP BY 1 ORDER BY 2 DESC;

\echo '--- reason codes (a row may carry several) ---'
SELECT reason, count(*) AS rows
FROM public.canonical_market, unnest(quality_reasons) AS reason
GROUP BY 1 ORDER BY 2 DESC;

-- -----------------------------------------------------------------------------
-- 6. Line fragmentation -- the discovery that drove same-line isolation
-- -----------------------------------------------------------------------------
\echo '=== 6. LINE FRAGMENTATION BY MARKET ==='
SELECT market_type,
       count(*) FILTER (WHERE distinct_line_count > 1) AS fragmented,
       count(*)                                        AS total,
       round(100.0 * count(*) FILTER (WHERE distinct_line_count > 1)
             / NULLIF(count(*), 0), 1)                 AS pct_fragmented
FROM (SELECT DISTINCT event_id, market_type, selection, distinct_line_count
      FROM public.canonical_market) d
GROUP BY 1 ORDER BY 1;

-- -----------------------------------------------------------------------------
-- 7. THE FINISH LINE -- one NFL game, the whole contract, one row per wager
-- -----------------------------------------------------------------------------
\echo '=== 7. FINISH-LINE QUERY (one game, every question answered) ==='
WITH game AS (
    SELECT event_id FROM public.market_intelligence
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

\echo '=== END ==='
