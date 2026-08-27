-- =============================================================================
-- post_ingest_check.sql -- read-back verification after a controlled --ingest
-- Read-only. Safe to run repeatedly.
--   psql "$OLP_DATABASE_URL" -f scripts/post_ingest_check.sql
-- =============================================================================
\echo '=== 1. EVENT + QUOTE COUNTS ==='
SELECT
  (SELECT count(*) FROM public.events)                              AS events,
  (SELECT count(*) FROM public.market_snapshots)                    AS snapshot_rows,
  (SELECT count(*) FROM (SELECT DISTINCT event_id, market_type, selection, sportsbook
                           FROM public.market_snapshots) x)         AS logical_quotes,
  (SELECT count(*) FROM public.event_schedule_history)              AS schedule_history,
  (SELECT count(*) FROM public.event_lifecycle_log)                 AS lifecycle_rows;

\echo '=== 2. FRESHNESS: newest quote must be seconds old, not minutes ==='
SELECT max(captured_at)                                             AS freshest_quote,
       round(extract(epoch FROM (NOW() - max(captured_at))))::int    AS age_seconds,
       min(captured_at)                                             AS oldest_quote,
       (SELECT snapshot_ttl_seconds FROM public.system_settings)     AS ttl_seconds
FROM public.market_snapshots WHERE is_in_play = FALSE;

\echo '=== 3. BOOKMAKERS OBSERVED ==='
SELECT sportsbook, count(*) AS quotes, count(DISTINCT event_id) AS events
FROM public.market_snapshots GROUP BY sportsbook ORDER BY quotes DESC;

\echo '=== 4. MARKET COVERAGE (normalisation survived persistence?) ==='
SELECT market_type,
       count(*)                                        AS quotes,
       count(*) FILTER (WHERE line IS NULL)            AS null_line,
       count(*) FILTER (WHERE line IS NOT NULL)        AS with_line,
       min(price) AS min_price, max(price) AS max_price
FROM public.market_snapshots GROUP BY market_type ORDER BY market_type;

\echo '--- TOTAL selections must be exactly OVER/UNDER ---'
SELECT selection, count(*) FROM public.market_snapshots
WHERE market_type = 'TOTAL' GROUP BY selection ORDER BY 1;

\echo '=== 5. DUPLICATE LOGICAL QUOTES (expect ZERO rows) ==='
-- The same observation recorded twice at the same instant would be a defect.
-- Multiple rows for one market at DIFFERENT captured_at is correct history.
SELECT event_id, market_type, selection, sportsbook, captured_at, count(*) AS times
FROM public.market_snapshots
GROUP BY 1,2,3,4,5 HAVING count(*) > 1
ORDER BY times DESC LIMIT 20;

\echo '=== 6. PROVIDER HEALTH (expect CLOSED, 0 failures, quota present) ==='
SELECT provider, circuit, consecutive_failures,
       quota_remaining, quota_used, last_success_at, last_error
FROM public.provider_health;

\echo '=== 7. INGESTION RUNS (expect SUCCEEDED, none stuck RUNNING) ==='
SELECT run_seq, kind, status, events_upserted, snapshots_written, snapshots_skipped,
       round(extract(epoch FROM (finished_at - started_at))::numeric, 2) AS seconds,
       error_text
FROM public.ingestion_runs ORDER BY run_seq;

\echo '=== 8. LEDGER MUST BE UNTOUCHED BY INGESTION (all zero) ==='
SELECT (SELECT count(*) FROM public.tickets)              AS tickets,
       (SELECT count(*) FROM public.wallet_transactions)  AS wallet_txns,
       (SELECT count(*) FROM public.ticket_results)       AS results,
       (SELECT count(*) FROM public.ledger_chapters)      AS chapters;

\echo '=== 9. BOARD SANITY: is anything actually placeable? ==='
SELECT count(*) AS board_rows,
       count(*) FILTER (WHERE is_placeable) AS placeable,
       min(quote_age_seconds) AS freshest_age,
       max(quote_age_seconds) AS stalest_age
FROM public.current_market_board;

\echo '=== 10. FEED HEALTH SUMMARY ==='
SELECT jsonb_pretty(public.feed_health_summary_rpc());
