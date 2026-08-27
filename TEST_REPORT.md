# OLP-M1 — Test Report

> Covers **Package #1 (Database Foundation)** in detail. Package #2 (Market
> Ingestion & Event Lifecycle) adds 34 further tests; its matrix and design
> record live in [PACKAGE2.md](PACKAGE2.md). Combined: **74/74 PASS**.

**Package:** #1 — Database Foundation
**Architecture:** v0.3 FROZEN
**Executed:** 2026-08-27
**Engines:** verified on BOTH
  - Supabase local stack — PostgreSQL 17.6, real `auth` schema, real API roles
  - bundled PostgreSQL 16.2 + `auth` shim (19 migrations + 1 shim file)
**Concurrency:** real threads, independent TCP connections, released by a barrier

```
OLP-M1 DATABASE FOUNDATION

Migrations:                PASS
Schema constraints:        PASS
Chapter baseline:          PASS
Ticket placement:          PASS
Market revalidation:       PASS
Escrow accounting:         PASS
Settlement:                PASS
Settlement idempotency:    PASS
Correction audit:          PASS
Deficit handling:          PASS
Bankruptcy exit:           PASS
Concurrency placement:     PASS
Concurrency settlement:    PASS
Authorization:             PASS

Acceptance tests:
29/29 PASS      (all IDs enumerated in section 33)

Security tests:
5/5 PASS

Additional hardening tests:
6/6 PASS

TOTAL: 40/40 PASS   (Package #1)
       74/74 PASS   (including Package #2)

Known deviations:
None that change the architecture. 14 implementation decisions
are documented in DEVIATIONS.md; all are additive.
```

Reproduce locally:

```bash
python tests/run_all.py
```

Reproduce against the real Supabase stack:

```bash
OLP_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres python tests/run_all.py
```

Both produce 40/40. Migrations 001–019 are applied byte-identically in each.

---

## Section 33 acceptance tests

| Test | Requirement | Result | Evidence |
|---|---|---|---|
| M1-T01 | Standard WIN | PASS | 1,000 @ −110 → +909.09; settled 10,909.09, escrow 0 |
| M1-T02 | Standard LOSS | PASS | settled 9,000; `SETTLEMENT_LOSS` −1,000 |
| M1-T03 | PUSH writes zero transaction | PASS | exactly 1 row, `SETTLEMENT_PUSH` 0.00; balance unmoved |
| M1-T04 | 15 concurrent placements cannot overdraw | PASS | 10 accepted / 5 rejected, escrow 10,000, available 0 |
| M1-T05 | Sunday slate static 1,000-unit sizing | PASS | 10 accepted, 11th `INSUFFICIENT_CAPITAL` |
| M1-T06 | Stale snapshot rejected | PASS | TTL+60s → `SNAPSHOT_STALE`, 0 tickets |
| M1-T07 | Duplicate submission UUID creates one ticket | PASS | 3 calls → 1 ticket, 1 reservation |
| M1-T08 | Post-kickoff ticket rejected | PASS | 4 paths: past start, live, actual start, closed |
| M1-T09 | Concurrent grading creates one settlement | PASS | 8 graders → 1 result, 1 wallet transaction |
| M1-T10 | Correction creates DEFICIT without rewriting history | PASS | original row byte-identical; status → `DEFICIT` |
| M1-T11 | 48-hour postponement void releases exposure | PASS | history row logged; escrow → 0; reservation `VOIDED` |
| M1-T12 | In-play quote cannot become closing quote | PASS | blocked on UPDATE and INSERT; uniqueness holds |
| M1-T13 | Fresh-but-superseded snapshot rejected | PASS | `MARKET_MOVED` while inside TTL |
| M1-T15 | Conflicting ordinary settlement raises conflict | PASS | `SETTLEMENT_CONFLICT`; original WIN stands |
| M1-T16 | Correction/placement race serializes on chapter | PASS | both orderings valid and both observed; settled always exactly 9,000 |
| M1-T17 | Second current chapter rejected | PASS | RPC *and* `uq_one_current_chapter_per_user` |
| M1-T19 | PUSH retry remains one settlement | PASS | 3 calls → 1 result, 1 zero transaction |
| M1-T20 | VOID retry remains one settlement | PASS | 3 calls → 1 result, 1 zero transaction |
| M1-T21 | Odds 0 rejected | PASS | 0, ±1, ±50, ±99 rejected; ±100, −110, +5000 accepted |
| M1-T23 | Moved line rejected despite TTL freshness | PASS | DAL −3 → −3.5 fixture; age asserted < TTL |
| M1-T26 | Correction retry idempotent | PASS | same key ×3 → 1 adjustment, 1 correction transaction |
| M1-T27 | Multiple corrections derive effective result | PASS | WIN → LOSS → PUSH; effective PUSH/0.00, original WIN/909.09 |
| M1-T29 | Client cannot directly write wallet transaction | PASS | INSERT/UPDATE/DELETE all `permission denied` |
| M1-T30 | Client cannot directly create accepted ticket | PASS | ticket, reservation and chapter writes all denied |
| M1-T31 | Chapter starts at exactly 10,000, never 20,000 | PASS | derived 10,000; second `CHAPTER_OPEN` blocked by index |
| M1-T32 | Equal timestamps ordered by `ingest_seq` | PASS | 25 identical queries → same snapshot; RPC agrees |
| M1-T33 | Insolvent DEFICIT chapter can become BUST | PASS | `DEFICIT_INSOLVENT`; chapter 2 opens at 10,000 |
| M1-T34 | ACTIVE chapter under 100 LC with no exposure can BUST | PASS | `BANKROLL_DEPLETED` after bankroll decay |
| M1-T35 | User cannot place against another user's chapter | PASS | rejected; RLS also hides the chapter entirely |

**29/29 PASS.**

> On the count: section 33's table enumerates 29 tests (T14, T18, T22, T24, T25
> and T28 are absent from it), while section 40's example report shows `35/35`.
> All 29 enumerated tests are implemented and pass. No test was invented to
> reach 35 and none was weakened to go green. See DEVIATIONS.md §12.

---

## Section 35 security tests

| Test | Requirement | Result | Evidence |
|---|---|---|---|
| SEC-T01 | User A places on User B's chapter | PASS | `CHAPTER_NOT_AVAILABLE`; 0 tickets, 0 reservations |
| SEC-T02 | Authenticated INSERT `wallet_transactions` | PASS | `permission denied`; balance unchanged |
| SEC-T03 | Authenticated INSERT `ticket_results` | PASS | `permission denied` (adjustments too) |
| SEC-T04 | Authenticated calls `settle_ticket_rpc` | PASS | `permission denied` (correction RPC too) |
| SEC-T05 | Anonymous ticket placement | PASS | `permission denied`; no-JWT → `AUTH_REQUIRED` |

**5/5 PASS.**

---

## Additional hardening tests

| Test | Requirement | Result |
|---|---|---|
| M1-T07c | 10 concurrent submissions of the *same* key → 1 ticket | PASS |
| M1-T09b | Concurrent *conflicting* graders → 1 settlement, rest conflict | PASS |
| M1-T17c | 10 concurrent `open_chapter_rpc` → 1 chapter, 1 credit | PASS |
| M1-T34b | Bankruptcy refused while solvent or exposed | PASS |
| SEC-X01 | Neither `service_role` nor the table owner can rewrite settled history | PASS |
| SEC-X02 | Derived views inherit RLS (no read side-channel) | PASS |

**6/6 PASS.** `SEC-X01` is the notable one, and it asserts two independent
layers. Layer 1: `service_role` holds no write privilege on any ledger table, so
`UPDATE ticket_results` / `DELETE wallet_transactions` / editing ticket
economics all fail as `permission denied`. Layer 2: the table **owner** —
which no grant can restrain, and which is what a future migration or an admin
session runs as — is still refused by the append-only triggers. Grants stop
roles; triggers stop everyone.

`SEC-X02` closes the other obvious gap: both views are `security_invoker = true`,
so a second user reading `chapter_balances` or `ticket_effective_results` sees
only their own rows, and `anon` is refused outright.

---

## Section 34 — true concurrency

The requirement is *"Accepted ≤ 10 … Available ≥ 0. Every time."*

Workers are real OS threads, each holding its own TCP connection, released
simultaneously by a `threading.Barrier`. No `for` loop, no sequential awaits.

**Result:** 12/12 repeat runs on PostgreSQL 16.2 and 10/10 on the Supabase
stack (PostgreSQL 17.6) produced exactly 10 accepted, 5 rejected with
`INSUFFICIENT_CAPITAL`, escrow 10,000.00, available 0.00. Zero failures across
22 runs on two engines.

Those same Supabase runs exercised `M1-T16` in **both** serialization orders
(placement won 2, correction won 8), each handled correctly — see
"What real-Supabase testing caught".

### Negative control

To confirm the test detects the failure it claims to, `place_ticket_rpc` was
copied via `pg_get_functiondef` with the chapter `FOR UPDATE` stripped and
nothing else changed, then subjected to the same 15-way race:

| Variant | Accepted | Escrow | Available |
|---|---|---|---|
| Shipped RPC (`FOR UPDATE`) | **10** / 15 | 10,000.00 | **0.00** |
| Lock removed | **15** / 15 | 15,000.00 | **−5,000.00** |

Without the lock every request is accepted and the chapter overdraws by 5,000.
The test fails when the protection is absent, so its passing is meaningful.

---

## Section 36 — balance baseline

| Assertion | Value |
|---|---|
| `ledger_chapters.starting_capital` | 10,000.00 |
| `CHAPTER_OPEN` transaction count | 1 |
| `CHAPTER_OPEN` sum | +10,000.00 |
| Derived settled balance | 10,000.00 |
| Derived settled balance ≠ 20,000 | asserted explicitly |

A second `CHAPTER_OPEN` row is rejected by `uq_chapter_open_transaction`, so the
double-credit bug cannot recur even if application code tries.

## Section 37 — deterministic snapshot ordering

Two snapshots, identical `captured_at`, different `ingest_seq`. The latest-quote
query was run 25 times and returned the higher `ingest_seq` on every run.
`place_ticket_rpc` agrees: the lower `ingest_seq` is rejected `MARKET_MOVED`.

## Section 38 — bankruptcy

DEFICIT chapter, settled −3,000, escrow 0, minimum viable wager 100:

```
status       = BUST
closed_at    set
close_reason = DEFICIT_INSOLVENT
```

`open_chapter_rpc` then created chapter 2 at exactly 10,000. Prior chapter,
tickets and results were re-read afterwards and were unchanged.

---

## Section 42 — exit gate

| Gate | Status |
|---|---|
| Chapter opens with exactly 10,000 LC | PASS |
| Balance derived from wallet transactions only | PASS |
| Immutable market snapshots exist | PASS |
| Deterministic quote ordering works | PASS |
| Placement RPC authenticates via `auth.uid()` | PASS |
| Chapter lock prevents double spending | PASS |
| Current quote revalidation works | PASS |
| Escrow is created atomically | PASS |
| Settlement is idempotent | PASS |
| Conflicting settlement is rejected | PASS |
| Original results remain immutable | PASS |
| Corrections append cleanly | PASS |
| Deficit state works | PASS |
| Bankruptcy escape works | PASS |
| Direct financial table writes are blocked | PASS |
| Concurrency tests pass | PASS |

**Gate met — clear to proceed to Package #2: Market Ingestion & Event Lifecycle.**

---

## Caveats worth stating

1. **Supabase verification: DONE.** Run against a local Supabase stack
   (PostgreSQL **17.6**, GoTrue-managed `auth` schema, real `anon` /
   `authenticated` / `service_role`, `postgres` **not** a superuser). Result:
   **40/40 PASS**, plus 10 consecutive concurrency runs with zero failures.
   This surfaced two real defects that the local shim had hidden — see
   "What real-Supabase testing caught" below.

2. **`pgcrypto` is now exercised.** It is pre-installed on the Supabase stack,
   so migration 001's extension path ran for real there. The bundled engine has
   no contrib modules and took the core `gen_random_uuid()` fallback. Both
   paths are covered; see DEVIATIONS.md §1.

3. **Timing-dependent tests use generous margins.** With a 120s TTL, the stale
   test backdates a quote by TTL+60s (180s) and the fresh-but-superseded tests
   use quotes 10s old, whose age is asserted to be under the TTL before the
   rejection is checked. The tests are therefore not flaky, but the exact TTL
   boundary is not asserted to the second.

4. **`M1-T34` depends on bankroll decay converging.** It loops max-size losing
   tickets until the balance falls below 100 LC (~44 iterations from 10,000 at a
   10% max ticket). A guard aborts at 200 iterations rather than looping forever
   if the sizing rule ever changes.


---

## What real-Supabase testing caught

The suite passed 40/40 against the local shim and **still had two defects**.
Both were found on the first real-Supabase run, which is the argument for
having done it before Package #2 rather than after.

### 1. `service_role` privileges were inherited, not stated — 4 tests failed

Migration 012 revoked mutations from `anon` / `authenticated` but said nothing
about `service_role`, assuming Supabase's default `GRANT ALL` would cover it.
It did not: every affected test failed with `permission denied for table events`.

The deeper problem was not the missing grant but the *inconsistency* — the
privilege set differed between environments, so a green local run said nothing
about production. Migration 012 now revokes `service_role` to a known state and
grants deliberately: read-only on the ledger, ingestion rights on market data.
The testkit file that mirrored the platform default was deleted, because it
would have masked exactly this. See DEVIATIONS.md §13.

### 2. `M1-T16` was over-specified and passed locally by luck

The correction/placement race has **two legitimate outcomes**:

| Order | Effect |
|---|---|
| Placement first | max ticket = 10% × 10,909.09 = 1,090.90 → 1,000 accepted |
| Correction first | settled drops to 9,000, max ticket = 900.00 → 1,000 correctly refused with `TICKET_SIZE_LIMIT` |

The original test asserted both operations must succeed, which silently assumed
placement-first. PostgreSQL 16 happened to always schedule it that way; on
PostgreSQL 17 the correction won and the test failed — correct behaviour
reported as a failure.

The test now asserts what actually matters regardless of ordering: the
correction applies exactly once, `settled` lands on exactly 9,000 with no lost
update, available capital never goes negative, and the final escrow matches
whichever branch occurred. Over 10 Supabase runs it exercised **both** branches
(placement won 2, correction won 8) and passed every time — the original test
never exercised one of them at all.

### Not a defect: the fixture reset

The first Supabase run failed 40/40 with `must be owner of sequence
refresh_tokens_id_seq`. That was `olp_test.reset()` truncating `auth.users`
with `CASCADE`, reaching `auth.refresh_tokens`, which `postgres` does not own on
Supabase. Fixture-only; no migration or RPC was involved. `reset()` now scopes
its cleanup to a `DELETE` on the `@olp.test` email domain and never touches the
rest of the auth system.


---

## Package #2 — Market Ingestion & Event Lifecycle

Built freehand (no written contract existed) and recorded in
[PACKAGE2.md](PACKAGE2.md). **34/34 PASS** on both engines.

| Group | Result |
|---|---|
| Market ingestion (T01–T09) | PASS |
| Schedule & postponement (T10–T15) | PASS |
| Closing-line capture (T16–T20) | PASS |
| Event lifecycle (T21–T24) | PASS |
| Market board & CLV (T25–T27) | PASS |
| Ingestion worker (T28–T30) | PASS |
| Authorization (T31–T33) | PASS |
| Quote ordering (T34) | PASS |

### The finding worth carrying forward

De-duplicating unchanged quotes — the obvious way to stop a polling feed burying
the immutable history in noise — silently breaks Package #1. `place_ticket_rpc`
requires the newest quote to be younger than the 120s TTL, so a market nobody
moved would age out of its own TTL and become unplaceable for want of news.

Ingestion therefore re-records an unchanged quote once
`snapshot_refresh_seconds` (60s) has elapsed, and a CHECK constraint pins that
interval strictly inside the TTL so the two policies cannot drift apart later.
`M2-T04` proves it end to end: a quote nearly TTL-old is refreshed on the next
poll and remains placeable at the same price.

### Two tests that were wrong before they were right

`M2-T04` failed on first run. The cause was the test, not the code: its fixture
appended a *back-dated* quote, which by design never becomes the newest quote,
so de-duplication correctly skipped it. Rewritten to age the current quote, it
passes — and the flawed premise became `M2-T34`, which now asserts that a
late-arriving older quote is retained as history but never becomes the
executable price.

`M2-T25` also carried a weak assertion: it checked that a stale snapshot was not
placeable on the board, but the board only ever shows the newest quote per group,
so that row was absent entirely and the check passed for the wrong reason. It now
asserts a real non-placeable case — once an event goes live, nothing on it is
offered and the RPC agrees.
