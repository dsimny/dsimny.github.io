# Database baseline resolved — October 6, 2026

The disposable PostgreSQL baseline now passes: **214/214 existing database tests**, **20/20 NBA offline tests**, and the added market-output regression check. Package 1's database prerequisite is satisfied on the development branch. NBA production readiness, experiment activation, and official publication remain blocked by their separate requirements.

Validated code commit: `fad1345`. Run: https://github.com/dsimny/dsimny.github.io/actions/runs/37558699934

## Finding and fix

The original full sequence reproducibly failed P4-T29 and stalled at P5-T01. A diagnostic rerun completed 212/214 with a bounded timeout and identified the expensive statement as `SELECT count(*) FROM public.market_intelligence`, with no blocking sessions. A 600-row intermediate result was repeatedly processed 1,080 times despite an estimate of one row. The isolated fresh-fixture queries did not reproduce the same plan; refreshing configuration-table statistics did not fix it.

New migration `059_p4_consumer_plan_boundaries.sql` avoids the separate execution-price/count join by computing the book count before the existing best-price selection. The unchanged latest-snapshot gate ensures at most one placeable observation per book and selection, so the partition count equals the former distinct-book count, including NULL moneyline lines. It also evaluates constituent market pipelines once at the consumer boundary using MATERIALIZED CTEs. Column names, types, pricing, eligibility, freshness, NULL matching, and security_invoker boundaries remain intact.

The reproduced dense market count improved from exceeding the 15-second diagnostic limit to 0.456 seconds; model counts and first-row reads completed in 0.374 and 0.368 seconds. These are fixture measurements, not a production latency guarantee.

The final existing P4-T29 assertion checked 301 plan nodes and reported zero pathological plans. P5-T01 verified three permitted reads and seven denied market reads. The assertion thresholds and prior migration SQL were not changed.

## Validation and limits

The regression compares every column of 600 dense rows and two small-fixture rows in both executable_market and market_intelligence before and after the migration. All 1,204 compared rows matched. Legacy rows are collected across every event with bounded per-event reads; the new dense consumer is also tested as a complete count and through the actual olp_model role. NOW() and quote eligibility are held constant within each comparison.

CI uses a fresh PostgreSQL 17.11 service container, the existing test-only authentication shim, no production credentials, and both destructive-target guards without overrides. Diagnostic stack/activity output and a 30-second default statement limit make future stalls observable; a timeout remains a failure. The added regression reads have a 15-second limit.

The change is committed and pushed to `codex/nba-contracts-v01`. **It is not merged, deployed, or applied to any production database.** The only modified pre-existing tracked file is the migration manifest, which adds migration 059. Existing production behavior and recovered files are untouched. The key is stored separately in Bitwarden; encrypted evidence and initial code backups are in OneDrive, with key retrieval/decryption verified and cloud upload reported by the owner.

Next: Package 2 can be developed against the same disposable CI database. Deployment state, actual NBA schedule reconciliation, current-season injury coverage, licensing, and the data/holdout/power supplement remain separate prerequisites.

PostgreSQL's CTE materialization behavior is documented at https://www.postgresql.org/docs/17/queries-with.html.
