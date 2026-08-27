# OLP-M1 Package #2 — Market Ingestion & Event Lifecycle

**Status: FROZEN** at tag `pkg2-v1.0` following architectural review.
87/87 tests pass on both a real Supabase stack (PostgreSQL 17.6) and bundled
PostgreSQL 16.2 — 40 from Package #1, 34 Package #2, 13 boundary/concurrency.

Package #1 arrived as a frozen written contract. This one did not: it was built
freehand from the one-line description in Package #1 §42, so this document was
written after the fact.

**From the freeze onward it is a PROSPECTIVE contract.** The semantics below —
the postponement rule, the refresh/TTL relationship, the lock order, the kickoff
boundary — are not to be changed casually. Changing any of them is a new package
with its own review, not an edit to this one.

Review outcome: passed, with one semantic change (postponement is now
ticket-relative, section 3.2) and a boundary/concurrency suite added
(section 4b).

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

### Migrations 020–032

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
| 030 | `tickets.accepted_event_start`; `place_ticket_rpc` takes the event share lock (review) |
| 031 | `reschedule_event_rpc` -> ticket-relative postponement (review) |
| 032 | `ingest_market_snapshot_rpc` -> event share lock + kickoff guard (review) |

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

### 3.2 Postponement is TICKET-RELATIVE, and absolute

*Changed at architectural review. The original rule measured displacement from
the event's `original_scheduled_start`, which charged a bettor for a slip that
happened before they ever placed.*

Threshold: `postponement_void_hours`, default **48**. Each open ticket is judged
on the displacement between the new start time and **the schedule that ticket
was accepted against**:

| Ticket | Counts against it |
|---|---|
| Placed before the first slip | all subsequent displacement |
| Placed after the first slip | only displacement occurring after it |

Displacement stays **absolute**: a game pulled 48 hours earlier is no more "the
game you bet on" than one pushed 48 hours later.

A single reschedule may therefore void some tickets and retain others. That is
intended, not a bug — they bought different schedules (`B03`).

The event lifecycle label still reports the event's own story (cumulative
displacement from its original start), because the log describes the event; the
per-ticket decision is what moves money.

**Implementation:** `tickets.accepted_event_start`, recorded at acceptance and
frozen with the rest of the accepted economics. This is the one place Package #2
touches a Package #1 table.

Deriving the baseline from `event_schedule_history` via `tickets.accepted_at`
was considered and rejected. `accepted_at` defaults to `NOW()`, which in
PostgreSQL is *transaction* start time; a placement that blocks on a concurrent
reschedule resumes and commits with a timestamp predating it, so the derivation
would hand it the abandoned schedule — exactly the ticket the sweep already
decided not to void. Recording the value the RPC validated against removes the
inference.

### 3.2b Placement takes an event share lock, before the chapter lock

**This deviates from Package #1 section 20**, which fixes the sequence as
idempotency then chapter `FOR UPDATE` then validation. Placement now takes
`events FOR SHARE` *before* the chapter lock.

Without it, placement and postponement never serialise: placement locks a
chapter, postponement locks an event. A placement in flight is invisible to a
postponement void sweep, so a ticket could be accepted against an abandoned
schedule and then never voided.

The order matters as much as the lock. Reschedule and cancel take
event(exclusive) then chapter(exclusive), so placement must take event(share)
then chapter(exclusive) and not the reverse, or the two paths deadlock.
**Every event-touching path in this schema acquires the event before any
chapter.**

The chapter `FOR UPDATE` is untouched; nothing about the capital decision moved.
`B04` asserts the race, `B13` asserts two void paths racing settle exactly
once.

### 3.3 Schedule changes have exactly one code path

`ingest_event_rpc` never moves a start time itself; it delegates to
`reschedule_event_rpc`. A schedule feed therefore cannot slide a game 48 hours
without triggering the void rules, because there is no second path that skips
them.

### 3.3b The kickoff boundary is closed on both sides

Ingestion takes `events FOR SHARE` too, and refuses a **non-in-play quote dated
at or after the actual kickoff**. After kickoff a pre-game price is a provider
error by definition, and accepting one would manufacture an executable price for
a game already under way. In-play pricing after kickoff remains legitimate, and
can never become a closing line (`B07`, `B08`).

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

## 4b. Boundary and concurrency suite

Added at review, to find problems here rather than after a live provider is
attached.

| Test | Requirement |
|---|---|
| B01 | Ticket placed before the first slip absorbs total subsequent displacement |
| B02 | Ticket placed after the first slip ignores earlier displacement |
| B03 | Mixed cohorts on one event are adjudicated separately |
| B04 | Postponement racing `place_ticket_rpc` has deterministic ordering |
| B05 | Quote refresh at 59 / 60 / 61 seconds |
| B06 | Executable TTL at 119 / 120 / 121 seconds |
| B07 | Kickoff and incoming quote race cannot create a post-close executable price |
| B08 | Post-kickoff pre-game quote refused; in-play still accepted |
| B09 | Duplicate and crossed provider messages remain idempotent |
| B10 | Late historical quote can never replace a newer executable quote |
| B11 | Cancel invoked repeatedly cannot double-release escrow |
| B12 | Postpone invoked repeatedly cannot double-release escrow |
| B13 | Cancel and postpone racing settle exactly once |

**13/13 PASS**, and 12/12 repeat runs of the whole group with zero failures.

Two measurement notes, stated rather than glossed:

- **B06 cannot assert exactly 120 end to end.** Wall-clock advances a few
  milliseconds between stamping `captured_at` and the RPC evaluating it, pushing
  a nominal 120 to 120.00x and over the line. The boundary is pinned from both
  sides instead — 119 executable, 121 stale — plus a sub-second probe at 119.75
  proving the comparison is inclusive rather than strict. B05 boundaries have a
  full second of margin on each side and are exact.
- **B04 outcome distribution is skewed.** Released from a barrier the reschedule
  won 12/12, because placement does an idempotency lookup and a snapshot read
  before reaching the event lock. The converse ordering is covered
  deterministically by B01 and B12. What B04 establishes is that the illegal
  outcome — an ACCEPTED ticket still bound to an abandoned schedule — is
  unreachable either way.

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

## 6. Freeze

Frozen at `pkg2-v1.0`. Provider-specific parsing, authentication, throttling,
retries, reconnects, rate limits and outage behaviour are integration concerns
and were deliberately kept out of the lifecycle domain model. They belong to
Package #3, which asks whether this deterministic system survives an unreliable
real feed.

## 7. Out of scope

Deliberately not built: automatic result grading from a scores feed (settlement
still requires an explicit `settle_ticket_rpc` call), retry/backoff policy,
provider rate-limit handling, and any frontend. Leaderboards, social and contest
features remain excluded by Package #1 §2.
