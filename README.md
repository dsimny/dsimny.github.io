# Open Ledger Play — OLP-M1 Package #1: Database Foundation

The transactional ledger foundation for one closed-loop ticket, built to the
frozen v0.3 architecture.

```
AUTHENTICATED USER → CURRENT LEDGER CHAPTER → CHAPTER_OPEN +10,000 LC
   → VIEW MARKET SNAPSHOT → PLACE TICKET → RESERVE CAPITAL
   → SETTLE TICKET → RELEASE ESCROW → PERMANENT LEDGER RESULT
```

**Status: Packages #1–#3 complete. #2 FROZEN at `pkg2-v1.0`, #3 at `pkg3-v1.1`.**
126/126 tests pass on **both** a real Supabase stack (PostgreSQL 17.6) and the
bundled PostgreSQL 16.2, including 15-way true-concurrency placement against
independent database connections.

- Package #1 — Database Foundation: 40/40. [TEST_REPORT.md](TEST_REPORT.md), [DEVIATIONS.md](DEVIATIONS.md)
- Package #2 — Market Ingestion & Event Lifecycle: 34/34 plus 14 boundary/concurrency. [PACKAGE2.md](PACKAGE2.md)
- Package #3 — The Odds API + resilience: 38/38, **FROZEN at `pkg3-v1.0`, amended by `pkg3-v1.1`** (post-freeze `captured_at` correction). [PACKAGE3.md](PACKAGE3.md)

---

## The one rule everything else serves

There is no mutable balance column anywhere in this schema. Balance is always:

```sql
SELECT COALESCE(SUM(amount), 0)
FROM public.wallet_transactions
WHERE chapter_id = :chapter_id;
```

`ledger_chapters.starting_capital` is display metadata and is **never** added to
that sum. Test `M1-T31` asserts the derived balance is 10,000 and explicitly
asserts it is not 20,000.

---

## Layout

```
db/
  migrations/        001-036, applied in order. This is what ships.
  testkit/           TEST ONLY. Never apply to Supabase.
  rollback/          ROLLBACK_NOTES.md
ingest/              Package #2 ingestion worker
  provider.py        the provider boundary (EventRow / QuoteRow / OddsProvider)
  fixture_provider.py deterministic offline provider
  worker.py          drives the RPCs; holds no business rules
  providers/         real adapters (The Odds API v4)
  resilience.py      retry, backoff, throttle, quota, circuit breaker
  http.py            stdlib transport; redacts credentials
  resilient.py       run_poll_cycle -- a guarded poll
scripts/
  live_smoke.py      opt-in live API check (spends quota; dry-run by default)
tests/
  harness.py         server boot, migration runner, assertion helpers
  test_acceptance.py section 33 acceptance tests
  test_security.py   section 35 security tests
  test_concurrency.py true multi-connection contention tests
  test_package2.py   ingestion & event lifecycle (M2)
  test_p2_boundary.py boundary & concurrency edge conditions (B01-B13)
  test_package3.py   provider integration & resilience (P3)
  run_all.py         full suite + section 40 report
```

| Migration | Contents |
|---|---|
| 001 | pgcrypto, six enums, `system_settings` |
| 002 | `users` bound to `auth.users` |
| 003 | `ledger_chapters` + one-current-chapter index |
| 004 | `wallet_transactions` (balance source of truth) |
| 005 | `events` + `event_schedule_history` + auto-log trigger |
| 006 | `market_snapshots` (immutable, `ingest_seq` ordered) |
| 007 | `tickets` (economics frozen at insert) |
| 008 | `risk_reservations` (escrow) |
| 009 | `ticket_results` (append-once) |
| 010 | `ticket_result_adjustments` (append-only corrections) |
| 011 | wallet→ticket FK, odds/P&L helper functions |
| 012 | REVOKEs, grants, RLS policies |
| 013 | `open_chapter_rpc()` |
| 014 | `place_ticket_rpc(chapter, snapshot, risk, key)` |
| 015 | `settle_ticket_rpc(...)` — service role only |
| 016 | `apply_settlement_correction_rpc(...)` — service role only |
| 017 | `declare_bankruptcy_rpc()` |
| 018 | `chapter_balances`, `ticket_effective_results` views |
| 019 | `olp_test` fixture helpers |

Package #2 (see [PACKAGE2.md](PACKAGE2.md)):

| Migration | Contents |
|---|---|
| 020 | ingestion policy config, `ingestion_runs`, `event_lifecycle_log` |
| 021 | `void_event_tickets_rpc` |
| 022 | `reschedule_event_rpc` (postponement policy) |
| 023 | `ingest_event_rpc` |
| 024 | `ingest_market_snapshot_rpc` + batch form |
| 025 | `capture_closing_line_rpc` |
| 026 | `mark_event_live_rpc`, `close_event_rpc`, `cancel_event_rpc` |
| 027 | run bookkeeping + Package #2 privileges |
| 028 | `current_market_board`, `ticket_closing_line_value` |
| 029 | Package #2 fixtures |
| 030 | ticket/schedule binding + placement event lock (review) |
| 031 | ticket-relative postponement (review) |
| 032 | ingestion event lock + kickoff guard (review) |
| 033 | durable provider health / circuit state (Pkg #3) |
| 034 | feed health + fail-closed visibility (Pkg #3) |
| 035 | Package #3 fixtures |
| 036 | staleness guard-rail fixtures |

---

## Running the tests

Requires Python 3.9+. The suite boots its own PostgreSQL 16 — no Docker, no
system Postgres, no network.

```bash
python -m venv venv && venv/Scripts/python -m pip install pgserver "psycopg[binary]"
```

```bash
venv/Scripts/python tests/run_all.py
```

### Running against a real Supabase stack

Verified — the suite passes 40/40 here. Needs Docker running. From the repo
root (`supabase/config.toml` is already committed, so `init` is only needed on
a fresh clone):

```bash
npx --yes supabase@latest start -x studio,imgproxy,edge-runtime,logflare,vector,supavisor,realtime,storage-api,mailpit
```

That starts only what the ledger needs (db, auth, kong, rest, pg_meta).
`supabase start` prints a `DB URL`, normally
`postgresql://postgres:postgres@127.0.0.1:54322/postgres`. Point the suite at it:

```bash
OLP_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres python tests/run_all.py
```

In this mode the harness automatically skips `db/testkit/` and never drops the
`auth` schema — Supabase's own `auth.users`, `auth.uid()` and API roles are used
instead. Migrations 001–019 are applied byte-identically in both modes, so a
pass here means the shipped artifact works on the real platform.

Stop the stack with `npx supabase stop` (add `--no-backup` to discard volumes).

**Safety rail:** the suite drops `public` and truncates `auth.users`, so
`db_uri()` refuses any non-local host. Running it against a hosted project needs
a deliberate `OLP_ALLOW_REMOTE=1` — don't set that on a project with real users.

---

## Deploying to Supabase

Apply `db/migrations/` in numeric order, skipping the two fixture migrations.
Do **not** apply anything in `db/testkit/`.

| Apply | Skip in production |
|---|---|
| 001–018, 020–028, 030–034 | **019**, **029**, **035**, **036** (`olp_test` fixtures) |

The fixture migrations are development only: nothing else depends on them, and
they define `olp_test.reset()`, which truncates every ledger table.

Three things to confirm after deploying:

1. `SELECT * FROM public.system_settings;` — TTL 120s, max ticket fraction
   0.1000, min viable wager 100, starting capital 10,000.
2. `service_role` can execute `settle_ticket_rpc` and
   `apply_settlement_correction_rpc`; `authenticated` cannot.
3. `authenticated` has no INSERT/UPDATE/DELETE anywhere:
   `SELECT * FROM information_schema.role_table_grants WHERE grantee = 'authenticated' AND privilege_type <> 'SELECT';`
   should return zero rows.

---

## Authorization model

> Users can read permitted ledger information but cannot directly author
> financial truth.

`anon` and `authenticated` hold **no** INSERT/UPDATE/DELETE on any
ledger-critical table. Every write goes through a `SECURITY DEFINER` RPC with
`SET search_path = pg_catalog, pg_temp` and fully schema-qualified references.

| Role | May execute |
|---|---|
| `authenticated` | `open_chapter_rpc`, `place_ticket_rpc`, `declare_bankruptcy_rpc` |
| `service_role` | `settle_ticket_rpc`, `apply_settlement_correction_rpc`, and every Package #2 ingestion/lifecycle RPC |
| `anon` | nothing |

Table privileges are stated explicitly rather than inherited from Supabase's
defaults — including `service_role`, which is **read-only on every ledger
table** and holds write access only to market data (`events`,
`market_snapshots`, `event_schedule_history`). See DEVIATIONS.md §13.

No user-callable RPC accepts a user ID. Identity is `auth.uid()`, always.

RLS restricts reads to the caller's own rows. Both views are
`security_invoker = true`, so they inherit the caller's RLS rather than
bypassing it.

---

## Client integration notes

**Read balances from the server.** `public.chapter_balances` returns
`settled_balance`, `escrowed_risk`, `available_capital` and `max_ticket_size`.
The frontend must not reproduce this arithmetic.

**Read outcomes from `public.ticket_effective_results`**, not from
`tickets.status` — it reports the original result *and* the corrected effective
result side by side. (See deviation 9.)

**Placement errors are `P0001` with a stable `CODE:` prefix.** The ones a client
must handle:

| Code | Meaning |
|---|---|
| `MARKET_MOVED` | quote superseded — re-fetch and re-confirm |
| `SNAPSHOT_STALE` | quote older than TTL |
| `INSUFFICIENT_CAPITAL` | risk exceeds available capital |
| `TICKET_SIZE_LIMIT` | risk exceeds 10% of settled balance |
| `EVENT_STARTED` / `EVENT_LIVE` / `EVENT_CLOSED` | market no longer open |
| `IN_PLAY_NOT_ALLOWED` | in-play quotes are never executable |
| `CHAPTER_NOT_AVAILABLE` | not the caller's active chapter |

**Retries are safe.** Reuse the same `submission_idempotency_key` and you get
the same ticket back — never a second one, even under true concurrency
(`M1-T07c`).

---

## Running ingestion

The Odds API adapter ships in `ingest/providers/`. A guarded poll cycle wraps it
in retry, throttling, quota and a durable circuit breaker:

```python
from ingest import RetryPolicy, RateLimiter, QuotaGuard, run_poll_cycle
from ingest.providers import TheOddsApiProvider

result = run_poll_cycle(conn, TheOddsApiProvider(), retry=RetryPolicy(),
                        limiter=RateLimiter(min_interval=1.0),
                        quota=QuotaGuard(reserve=25))
```

Set `THE_ODDS_API_KEY` in the environment — never on the command line. Poll odds
faster than the 60s refresh window so quotes stay inside the placement TTL; see
[PACKAGE2.md](PACKAGE2.md) §3.1 for why that bound matters.

To check the live API (spends quota, dry-run by default):

```bash
python scripts/live_smoke.py
```

Read-only, `DATABASE WRITES: 0`. Add `--polls 3 --interval 75` to check
bookmaker-key stability across polls before ingesting anything.

Any other feed drops into the same seam: subclass `OddsProvider`, map onto
`EventRow` / `QuoteRow`. No migration and no line of `worker.py` changes.

---

## What this project deliberately does not do

No leaderboard, social, contest or casino functionality. No automatic result
grading from a scores feed — settlement still requires an explicit
`settle_ticket_rpc` call. No frontend.
