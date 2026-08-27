# OLP-M1 Package #3 — Provider Integration & Resilience

**Status:** implemented and verified offline. 121/121 tests pass on both a real
Supabase stack (PostgreSQL 17.6) and bundled PostgreSQL 16.2 — 40 Package #1,
34 + 14 Package #2, 33 Package #3.

**Not yet run against the live API.** No key exists on this machine and a live
call spends real quota, so every test uses a recorded v4 payload or a fake
transport. See §6.

The question this package answers: *does the clean deterministic system survive
an unreliable real-world feed?*

---

## 1. What was built

| Piece | Where |
|---|---|
| The Odds API v4 adapter | `ingest/providers/the_odds_api.py` |
| Error taxonomy, retry, throttle, quota, breaker | `ingest/resilience.py` |
| HTTP transport (stdlib, key-redacting) | `ingest/http.py` |
| Guarded poll cycle | `ingest/resilient.py` |
| Durable circuit state | migration 033 |
| Feed health / fail-closed visibility | migration 034 |
| Live smoke test (opt-in) | `scripts/live_smoke.py` |

No new dependency: `urllib` and `json` from the standard library.

**Package #2 stays frozen.** No lifecycle or ingestion semantic moved. The only
database additions are new tables (`provider_health`), a new view, and new RPCs.

---

## 2. Where resilience lives, and why

Retries, backoff, throttling and quota are **worker** concerns and stay in
`ingest/`. The database owns domain rules; a second implementation of those
rules in application code is how the two drift apart.

**One exception: circuit state is persisted.** The worker is expected to run as
a cron tick or an Edge Function invocation — a fresh process every time.
In-memory circuit state resets on every tick, which means a dead provider gets
hammered once per tick forever and the breaker never actually breaks.
`P3-T22` asserts a brand-new breaker object on a brand-new connection still
refuses a provider that a previous "process" found to be down.

---

## 3. Design decisions

### 3.1 One HTTP call per cycle

The v4 `/odds` response already contains the fixtures, so fetching schedule and
odds separately would bill twice for identical data. The payload is fetched once
per cycle, cached, and served to both `fetch_schedule()` and `fetch_odds()`;
`new_cycle()` drops it. `P3-T02` and `P3-T23` assert exactly one call.

Requests are billed **regions × markets**, so those parameters are a cost
decision, not a filter.

### 3.2 The error taxonomy is the whole design

Everything above the transport reasons about one question — *is this worth
retrying?* — and never about HTTP.

| Condition | Class | Retried |
|---|---|---|
| 5xx, timeout, connection reset, DNS | `TransientProviderError` | yes |
| 429 | `RateLimitedError` | yes; honours `Retry-After` **if sent** |
| 401 / 403 | `PermanentProviderError` | **no** |
| 422, other 4xx | `PermanentProviderError` | **no** |
| Non-JSON or non-array body | `MalformedPayloadError` | **no** |
| Quota below reserve | `QuotaExhaustedError` | **no** |

Retrying a bad key is worse than useless: it burns quota and can never succeed
(`P3-T12`).

### 3.3 Full jitter, not fixed backoff

Backoff sleeps uniformly in `[0, min(max_delay, base × 2^(n-1))]`. Equal or zero
jitter means every worker instance retries on the same schedule, and a
recovering provider gets a synchronised thundering herd at the moment it can
least take one. A server-supplied `Retry-After` wins over the computed delay when it is
present — the server knows better than the curve. The v4 docs do not promise
one, so its absence is the expected case, not the exception (`P3-T13`,
`P3-T14`, `P3-T31`).

### 3.4 Quota keeps a reserve

The Odds API bills per request and reports what is left on every response.
`QuotaGuard` refuses **routine polling** below a floor (default 25) so a
quota-burning Sunday cannot leave the operator with nothing for the request they
actually need. Quota exhaustion is classed permanent, not transient — retrying
cannot conjure allowance (`P3-T16`, `P3-T26`).

### 3.5 The key never reaches a log, an error, or the database

The Odds API takes its key in the query string, so every string the transport
can emit passes through `redact()` first, covering `apiKey`, `api_key`, `key`
and `token` in any case. A key that leaks into an exception ends up in logs, and
logs end up in places keys should not be. The key is read from
`THE_ODDS_API_KEY`, never persisted, and never accepted as a command-line
argument — argv is visible to other processes on the machine (`P3-T09`).

### 3.6 Parsing is tolerant, never silent

A malformed event or outcome is skipped and recorded in `last_parse_errors`
rather than discarding the slate — one bad row must not cost a poll. Unmodelled
markets (player props and the like) are ignored without being errors. The
worker surfaces the errors on the cycle result (`P3-T03`, `P3-T27`).

### 3.7 Retry wraps the fetch, not the database

Once bytes are in hand, ingestion is ordinary database work and is **not**
retried at this layer. The RPCs are already idempotent, and re-running a
partially applied batch inside a retry loop turns one bad poll into a stuck one.

### 3.8 A dead feed already fails closed — now it is visible

When the provider goes dark, quotes age past `snapshot_ttl_seconds`,
`current_market_board` stops offering them, and `place_ticket_rpc` refuses with
`SNAPSHOT_STALE`. That behaviour was already correct; what was missing is that
**a silent fail-closed looks exactly like a quiet Tuesday**.

`market_feed_health` and `feed_health_summary_rpc()` distinguish them.
`is_dark` means the market exists but nothing on it can be bet. The summary
deliberately counts only events that have **not** started — an event under way
is supposed to have nothing placeable, and counting those would make the alarm
always true and therefore useless (`P3-T28`).

---

## 4. Test matrix

| Group | Tests |
|---|---|
| Parsing the v4 payload | P3-T01 … T04 |
| Error taxonomy and secrets | P3-T05 … T10 |
| Retry and backoff | P3-T11 … T15 |
| Quota and throttling | P3-T16, T17, T26 |
| Circuit breaker (durable) | P3-T18 … T22 |
| End-to-end cycles, outage, fail-closed | P3-T23 … T29 |
| Authorization | P3-T30 |
| Rate-limit header absent, quota headers, smoke report | P3-T31 … T33 |

**33/33 PASS.** Highlights worth naming:

- **P3-T25** — an outage trips the breaker, and the next cycle is refused
  *without calling the provider at all*, with zero rows written.
- **P3-T22** — circuit state survives a simulated process restart.
- **P3-T28** — a dark event is refused and reported while a healthy event on the
  same slate keeps trading.
- **P3-T29** — three identical cycles produce 2 events and 16 quotes, not 48.
- **P3-T31** — a 429 with no `Retry-After` still backs off on its own curve.
- **P3-T33** — the live smoke report renders offline, so a formatting bug
  cannot waste a real request.

### The recurring mistake, now impossible

`P3-T28` originally faked a dead feed by inserting back-dated snapshot rows.
That models nothing: ordering is `captured_at DESC`, so a back-dated row never
becomes the current quote — and snapshots are immutable, so the fresh ones
cannot be removed. A dark feed is an event whose **newest** quote is old.

The identical mistake was made and corrected once already in Package #2's
`M2-T04`. Twice is a pattern, so it is now a guard rail rather than a note
(migration 036):

| Fixture | Behaviour |
|---|---|
| `olp_test.append_backdated_quote()` | renamed from `age_quote`, which implied something it never did. Appends a late-arriving observation; the market stays fresh. |
| `olp_test.make_current_quote_stale()` | **asserts, never mutates.** Succeeds if the newest quote is already that old; otherwise raises `STALENESS_FIXTURE_MISUSE`, explaining why ageing a fresh market is impossible and naming the alternative. |
| `olp_test.seed_stale_market()` | the honest construction: an event whose only observation is old. |

`B14` asserts the guard fires, that back-dating leaves the market placeable, and
that the honest construction produces a genuinely dark market.

Designing that helper surfaced the same confusion one level deeper. Its first
implementation *inserted* a row at the target time, which could only ever
produce a quote **newer** than the current one — reducing staleness, never
creating it. A function named `make_` that cannot make anything is worse than
no function. It now asserts and refuses, keeping the name so the attempt lands
somewhere that explains itself.

---

## 5. Running it

```bash
export THE_ODDS_API_KEY=...
```

```bash
python scripts/live_smoke.py
```

Read only: one request, parse and report, **no writes**, ending in an explicit
`DATABASE WRITES: 0`. It reports HTTP status, events received vs parsed, parse
errors, all three quota headers, every bookmaker key observed, which markets came
back, one full event sample, and a credential-leakage check over the URL, the
rendered report and a real exception.

The report is buffered and scanned for the key **before** anything is printed —
a leak check that runs after printing is not a check. Nothing is emitted at all
if the key appears anywhere in it.

To settle the bookmaker-key stability question empirically:

```bash
python scripts/live_smoke.py --polls 3 --interval 75
```

Three read-only polls, then a stability summary comparing bookmaker keys and
event IDs across them, plus the quota actually consumed. Costs 3 requests.

Only after that: `--ingest`, with `OLP_DATABASE_URL` set. Trim `--markets` /
`--regions` / `--bookmakers` to spend less.

`P3-T33` renders this entire report offline against the recorded payload, so a
formatting bug cannot waste a live request.

Production shape — a cron tick or Edge Function holding a service-role
connection:

```python
from ingest import RetryPolicy, RateLimiter, QuotaGuard, run_poll_cycle
from ingest.providers import TheOddsApiProvider

result = run_poll_cycle(
    conn, TheOddsApiProvider(bookmakers="draftkings,fanduel"),
    retry=RetryPolicy(max_attempts=4),
    limiter=RateLimiter(min_interval=1.0),
    quota=QuotaGuard(reserve=25),
)
```

Poll odds faster than the 60s refresh window so quotes stay inside the placement
TTL (PACKAGE2.md §3.1). `run_poll_cycle` returns a `CycleResult` rather than
raising when the breaker is open, so a tick can log and move on.

---

## 6. What is NOT verified

Stated plainly, because this is the part a passing suite cannot tell you.

1. **No live call has been made.** The adapter is written to the documented v4
   shape and validated against a recorded payload. Only a real call settles
   whether documentation and production agree. `scripts/live_smoke.py` is one
   command away and writes nothing.
2. **Bookmaker keys are taken verbatim** (`draftkings`, `fanduel`). Same-book
   closing-line capture depends on that key being stable across polls. The v4
   docs describe `key` as the bookmaker identifier alongside `title`, which
   supports the design — but stability *across polls* is an empirical question,
   which is why `--polls 3 --interval 75` exists.
3. **`Retry-After` on a 429 is NOT assumed.** The v4 documentation has a
   "Rate Limiting (status code 429)" section but does not document a
   `Retry-After` header. It is therefore treated as a bonus: present, it
   overrides the computed delay; absent or malformed, backoff stands on its own
   jittered curve. `P3-T31` asserts exactly that, including `""`, `"soon"` and a
   missing header.
4. **Quota accounting is read from headers, never computed.** All three
   documented headers are read — `x-requests-remaining`, `x-requests-used` and
   `x-requests-last` (the per-call cost, which is how you discover what a given
   `regions x markets` combination actually bills). If a header is absent the
   guard passes rather than guessing (`P3-T32`).

   `x-requests-last` is read and reported but **not yet persisted** to
   `provider_health`; that needs a new RPC signature and was deliberately
   deferred until after the live check.

---

## 7. Out of scope

No result grading — settlement still requires an explicit `settle_ticket_rpc`
call, and a scores feed remains unbuilt. No scheduler: `run_poll_cycle` is
designed to be *called* by cron or an Edge Function, but nothing schedules it
yet. No frontend.
