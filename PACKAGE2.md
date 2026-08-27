# OLP-M1 Package #2 — Market Ingestion & Event Lifecycle

**Status:** implemented and verified. 74/74 tests pass on both a real Supabase
stack (PostgreSQL 17.6) and bundled PostgreSQL 16.2 — 40 from Package #1, 34 new.

Package #1 arrived as a frozen written contract. This one did not: it was built
freehand from the one-line description in Package #1 §42, so **this document is
the contract, written down after the fact**. Every design decision that a spec
would normally have dictated is recorded here, and each is a decision you can
overrule.

---

## 1. Objective

Connect the hardened ledger to real NFL schedules and odds:

```text
SCHEDULE FEED ──► ingest_event_rpc ──► events (+ schedule history)
                       │
                       └─► reschedule_event_rpc ──► postponement? ──► void tickets
ODDS FEED ────► ingest_market_snapshots_rpc ──► market_snapshots (immutable)
                       │
KICKOFF ──────► mark_event_live_rpc ──► capture_closing_line_rpc (same book)
                       │
FINAL ────────► close_event_rpc / cancel_event_rpc
```

**Package #2 adds nothing to the financial tables.** Chapters, wallet
transactions, tickets, reservations, results and adjustments are untouched.
Every ticket-voiding path goes through Package #1's `settle_ticket_rpc`, so
escrow release, the zero-value wallet transaction and the append-once result all
behave exactly as they already did.

---

## 2. What was built

### Migrations 020–029

| Migration | Contents |
|---|---|
| 020 | ingestion policy config, `ingestion_runs`, `event_lifecycle_log` |
| 021 | `void_event_tickets_rpc` |
| 022 | `reschedule_event_rpc` (postponement policy) |
| 023 | `ingest_event_rpc` |
| 024 | `ingest_market_snapshot_rpc` + batch form |
| 025 | `capture_closing_line_rpc` |
| 026 | `mark_event_live_rpc`, `close_event_rpc`, `cancel_event_rpc` |
| 027 | run bookkeeping RPCs + Package #2 privileges/RLS |
| 028 | `current_market_board`, `ticket_closing_line_value` |
| 029 | Package #2 fixtures (development only) |

### The `ingest/` package

A provider-agnostic worker. `provider.py` defines the boundary (`EventRow`,
`QuoteRow`, `OddsProvider`); `fixture_provider.py` supplies deterministic
offline data; `worker.py` drives the RPCs. **The worker holds no business
rules** — it does not decide what a postponement is, when a quote is stale, or
whether a price is worth recording. Those live in the database, because a second
implementation in application code is how the two drift apart.

---

## 3. Design decisions

These are the calls a written spec would have made. All are reversible.

### 3.1 De-duplication is bounded by the placement TTL — the load-bearing one

Providers poll on a fixed cadence and mostly report unchanged prices. Recording
every poll would bury the immutable history in noise, so ingestion appends only
when a quote carries information.

Naive de-duplication breaks Package #1. `place_ticket_rpc` requires the newest
quote to be younger than `snapshot_ttl_seconds` (120s). If unchanged quotes were
simply skipped, **a market nobody moved would age out of its own TTL and every
placement against it would fail with `SNAPSHOT_STALE`** — a valid line made
unplaceable by the absence of news.

So an unchanged quote is still re-recorded once `snapshot_refresh_seconds` (60s)
has elapsed, and a CHECK constraint pins that interval strictly inside the TTL:

```sql
CHECK (snapshot_refresh_seconds < snapshot_ttl_seconds)
```

The constraint exists so the two policies cannot drift apart later. `M2-T04`
proves the mechanism end to end: a quote nearly TTL-old is refreshed on the next
poll and remains placeable at the same unchanged price. `M2-T05` proves the
constraint bites.

### 3.2 Postponement is measured cumulatively, and in absolute terms

Threshold: `postponement_void_hours`, default **48**, measured against
`original_scheduled_start`.

- **Cumulative**, because two 24-hour slips are the same displacement as one
  48-hour slip and should not escape the rule by arriving separately (`M2-T12`).
- **Absolute**, because a game pulled 48 hours *earlier* is no more "the game you
  bet on" than one pushed 48 hours later (`M2-T13`).

Crossing it voids every open ticket on the event and returns the stake, rather
than leaving capital escrowed against a game that moved.

**Worth your review:** measuring from `original_scheduled_start` means a ticket
placed *after* a first slip is judged against a displacement that partly predates
it. The alternative — per-ticket displacement from time of placement — is more
precise and considerably more complex. I chose the simpler rule; say the word if
you want the precise one.

### 3.3 Schedule changes have exactly one code path

`ingest_event_rpc` never moves a start time itself; it delegates to
`reschedule_event_rpc`. A schedule feed therefore cannot slide a game 48 hours
without triggering the void rules, because there is no second path that skips
them.

### 3.4 Closing lines are captured at kickoff, per book

The closing line is the last **pre-game** quote from **each book**, per market
and selection. Grouping by sportsbook is the entire point: comparing a ticket
taken at book A against a closing number from book B measures the spread between
books, not the quality of the bet.

Capture runs inside `mark_event_live_rpc` rather than as a later job, because the
moment an event goes live is exactly when "the last pre-game quote" stops being a
moving target. `close_event_rpc` re-attempts it as a safety net for an event
whose live signal was missed; it is a no-op when capture already happened.

Selection is deterministic (`captured_at DESC, ingest_seq DESC`), and setting
`is_closing_snapshot` false → true is the only mutation Package #1's snapshot
immutability trigger permits.

### 3.5 CLV reports nothing rather than something false

`ticket_closing_line_value` compares a ticket to the closing price **from the
same book**. When the line moved on a SPREAD or TOTAL, `beat_close` is `NULL`
and `line_moved` is `true`: at a different number it is a different bet, and
reducing that to a price comparison would report a falsehood (`M2-T27`).

### 3.6 `public.events` gained no columns

Lifecycle facts are appended to a new `event_lifecycle_log` instead. This keeps
Package #1's frozen table frozen and gives lifecycle history the same
append-only guarantees the ledger has.

### 3.7 A bad row must not discard a good poll

`ingest_market_snapshots_rpc` captures per-row failures and returns them rather
than aborting the batch. They are **reported, never swallowed** — the worker
records them against the run, and `M2-T07` asserts the good rows still land.

### 3.8 Operational tables are not public

`events` and `event_schedule_history` stay public (Package #1 made them so), and
users learn about a postponement from those. `ingestion_runs` and
`event_lifecycle_log` are service-role only: they carry platform detail such as
how many tickets a postponement voided, which is operational, not market data.

### 3.9 `current_market_board` must agree with `place_ticket_rpc`

The board applies the same ordering, the same in-play exclusion and the same TTL
from `system_settings`, so a row flagged `is_placeable` is a row the RPC will
accept. `M2-T25` asserts both directions — every placeable row places, and once
an event goes live nothing on it is offered.

---

## 4. Test matrix

| Test | Requirement |
|---|---|
| M2-T01 | Event ingestion is idempotent |
| M2-T02 | Unchanged quote inside refresh window is skipped |
| M2-T03 | Price move appended, prior quote preserved |
| M2-T04 | Unchanged quote refreshed before it outlives the TTL |
| M2-T05 | Refresh window constrained inside the TTL |
| M2-T06 | Invalid quotes rejected (odds 0, bad line, future timestamp) |
| M2-T07 | Batch survives one bad row |
| M2-T08 | Quotes for unknown or closed events rejected |
| M2-T09 | Event identity mismatch rejected |
| M2-T10 | Sub-threshold reschedule does not void |
| M2-T11 | Postponement voids open tickets and returns stakes |
| M2-T12 | Cumulative shift crosses the threshold |
| M2-T13 | Large shift earlier also voids |
| M2-T14 | Started/closed events cannot be rescheduled |
| M2-T15 | No-op reschedule writes no history row |
| M2-T16 | Closing line captured per book |
| M2-T17 | In-play quote never becomes the closing quote |
| M2-T18 | Closing-line capture is idempotent |
| M2-T19 | Quotes after kickoff excluded |
| M2-T20 | Kickoff captures closing lines automatically |
| M2-T21 | `mark_event_live_rpc` is idempotent |
| M2-T22 | `close_event_rpc` reports ungraded tickets, is idempotent |
| M2-T23 | `cancel_event_rpc` voids and closes |
| M2-T24 | Placement blocked once the event is live |
| M2-T25 | Board agrees with the placement RPC, both directions |
| M2-T26 | Same-book CLV computed correctly |
| M2-T27 | CLV is NULL when the line moved |
| M2-T28 | Worker full poll cycle, provenance recorded |
| M2-T29 | Worker reports unknown events without losing good rows |
| M2-T30 | Failed run is recorded as FAILED with its error |
| M2-T31 | Clients cannot call any ingestion or lifecycle RPC |
| M2-T32 | Operational tables are not client-readable |
| M2-T33 | Clients cannot write market data directly |
| M2-T34 | Out-of-order quote never becomes the current price |

**34/34 PASS** on PostgreSQL 17.6 (Supabase) and 16.2.

---

## 5. Adding a real provider

No provider is wired up — none was chosen. The seam is `ingest/provider.py`:

```python
class TheOddsApiProvider(OddsProvider):
    name = "THE_ODDS_API"

    def fetch_schedule(self) -> Iterable[EventRow]: ...
    def fetch_odds(self) -> Iterable[QuoteRow]: ...
```

Map the payload onto `EventRow` / `QuoteRow` and pass an instance to
`poll_once(conn, provider)`. **No migration and no line of `worker.py` changes.**
`EventRow` rejects naive datetimes and `QuoteRow` rejects impossible odds, so a
malformed feed fails at the boundary rather than deep in the ledger.

Production shape: run `ingest_schedule` on a slow cadence (minutes) and
`ingest_odds` on a fast one (under the 60s refresh window), from a Supabase Edge
Function, a cron container, or anything else holding a service-role connection.

---

## 6. Out of scope

Deliberately not built: automatic result grading from a scores feed (settlement
still requires an explicit `settle_ticket_rpc` call), retry/backoff policy,
provider rate-limit handling, and any frontend. Leaderboards, social and contest
features remain excluded by Package #1 §2.
