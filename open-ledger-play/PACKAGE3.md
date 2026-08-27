# OLP-M1 Package #3 — Provider Integration & Resilience

**Status: FROZEN** at `pkg3-v1.0` (2026-08-27), **amended by `pkg3-v1.1`** —
a post-freeze live-boundary correction to `captured_at` semantics (§6b).

The original freeze stands as issued. It was made on the evidence available at
the time: a clean read-only live boundary. The first controlled `--ingest`
afterwards exposed a defect that no read-only check could have found, which is
precisely the case the freeze clause reserved — *frozen unless a live defect is
found*. One was. This is that correction, recorded as an amendment rather than
by rewriting history.

126/126 tests pass on both a real Supabase stack (PostgreSQL 17.6) and bundled
PostgreSQL 16.2 — 40 Package #1, 34 + 14 Package #2, 38 Package #3.

The question this package answers: *does the clean deterministic system survive
an unreliable real-world feed?* The read-only half of that is answered (§6). The
persistence half — one controlled `--ingest`, then a repeat for idempotency — is
the next step and is deliberately not part of this freeze.

**Frozen unless a live defect is found:** the v4 adapter and its field mapping,
the error taxonomy and retry/backoff behaviour, quota-header handling, the
bookmaker-key stability assumption, and the read-only smoke contract. Changing
any of them is a new package with its own review.

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
| `captured_at` semantics (post-freeze correction, §6b) | P3-T34 … T38 |

**38/38 PASS.** Highlights worth naming:

- **P3-T25** — an outage trips the breaker, and the next cycle is refused
  *without calling the provider at all*, with zero rows written.
- **P3-T22** — circuit state survives a simulated process restart.
- **P3-T28** — a dark event is refused and reported while a healthy event on the
  same slate keeps trading.
- **P3-T29** — three identical cycles produce 2 events and 16 quotes, not 48.
- **P3-T31** — a 429 with no `Retry-After` still backs off on its own curve.
- **P3-T33** — the live smoke report renders offline, so a formatting bug
  cannot waste a real request.
- **P3-T37** — an unchanged price is still re-recorded after the refresh
  window. This is the test that would have caught the `captured_at` defect.

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

## 6. Live boundary verification

**Run against production on 2026-08-27.** Operator-run and reported to the
author, not observed directly — no API key exists on the build machine, which is
deliberate (§3.5). The numbers below are as reported.

Sequence was one single-poll read-only check as an abort point, then a spaced
three-poll run.

| Check | Result |
|---|---|
| HTTP | 200 |
| Events received vs parsed | 272 / 272, on all three polls |
| Parse errors | 0 |
| Bookmaker keys | 10, **identical across all three polls** |
| Event IDs in common | 272 of 272 |
| Markets present | moneyline, spread, total |
| Quotes parsed | 4,552 |
| `x-requests-last` | 3 per request |
| Quota consumed | 6 credits across the two intervals after poll 1 |
| Transient conditions | none — no 429, no 5xx, no timeout |
| Credential leakage (URL / stdout / exception) | PASS / PASS / PASS |
| Database writes | 0 |

### What this settles

The previous version of this section listed four unknowns. Three are now closed:

1. **Authentication and payload shape match the documented v4 contract.**
   272 received, 272 parsed, zero parse errors — the adapter's field mapping is
   correct against production, not just against a recording. Any gap between
   *received* and *parsed* would have been an adapter fault; there was none.
2. **Bookmaker keys are stable across polls.** Ten keys, unchanged over roughly
   150 seconds. Same-book closing-line capture (§3.4, PACKAGE2 §3.4) rests
   entirely on this and it now has evidence rather than an assumption.
3. **`x-requests-last` reads cleanly and reported 3**, exactly matching
   `regions × markets` (1 × 3). The documented billing model is confirmed.

One remains open, and cannot be closed on demand:

4. **`Retry-After` on a 429 is still unobserved** — no rate limit was hit. This
   is fine: the v4 docs never promised the header, and the fallback stands
   without it (`P3-T31`). It stays unverified until a real limit breach happens.

### The economics are now measured, not estimated

At 3 credits per poll, a naive 60-second cadence costs ~4,320 credits/day
(~130k/month). Cost is **per request, not per event** — the same 3 credits
returned all 272 events — so the single-call-per-cycle design (§3.1) is already
the right shape and there is nothing to win by batching differently.

The remaining lever is *when* to poll, not *how*. Package #2 §3.1 pins the
refresh window at 60s inside the 120s TTL, and that bound is load-bearing:
loosening it to save quota would let quotes age out and break placement. So the
correct optimisation is **schedule-aware polling windows** — poll at full cadence
only near kickoff, and rarely or not at all otherwise. Restricting to game-day
windows cuts the bill by roughly an order of magnitude without touching a single
freshness rule.

This is recorded here as a frozen architectural conclusion: *tighten the polling
schedule, never the freshness guarantees.*

## 6b. Post-freeze correction — `captured_at` semantics (`pkg3-v1.1`)

**Found by the first controlled `--ingest`, 2026-08-27. Read-only checks could
not have found it: it only manifests once quotes are persisted and read back
through the placement TTL.**

### What went wrong

The adapter set `captured_at` from the v4 `last_update` field. That is **one
timestamp per bookmaker** — when *that book* last moved its prices — not when we
observed them. On a real NFL slate the books ran 122–221 seconds behind the
fetch:

```
bovada          17:16:29   → 221s old at read time
draftkings      17:17:13   → 176s
betus           17:18:08   → 122s   ← the freshest anything got
```

Against a 120s TTL, all 4,552 quotes arrived already stale. Four minutes after a
successful ingest the board was **100% unplaceable** — `placeable 0 of 4552`.

### Why it was worse than it looked

It silently defeated the refresh mechanism, the one PACKAGE2 §3.1 calls
load-bearing. A book that does not move its price reports an **unchanged**
`last_update`, so de-duplication computed `new − previous = 0`, saw `0 < 60`,
and skipped the quote as fresh. It could never be re-recorded. A stable market
would have gone dark permanently and no amount of polling would have recovered
it.

Two mechanisms in direct contradiction: the refresh window existed to keep
unchanged quotes inside the TTL, and the `captured_at` choice guaranteed it
could not.

### The correction

`captured_at` is now **our observation time** — one timestamp per poll, shared by
every quote in that payload. The TTL asks *"how long since we confirmed this
price?"*, and we confirm every quote at fetch. A price that has not moved is the
**most** reliable price there is, not the stalest.

The feed's own timestamp is retained as `QuoteRow.provider_updated_at` for
provenance and diagnostics. It is deliberately **not persisted** —
`market_snapshots` has no column for it and one is not worth a migration.

Scope: `ingest/providers/the_odds_api.py` and one optional field on
`ingest/provider.py`. **No migration. No database change. No Package #2
semantic touched.**

### Proofs added

| Test | Asserts |
|---|---|
| `P3-T34` | a fresh fetch is inside the TTL — 16/16 placeable immediately, oldest 0s, even with bookmaker `last_update` five TTLs old |
| `P3-T35` | `last_update` six hours old does not make a quote stale; provenance preserved, quote placeable |
| `P3-T36` | three polls inside the refresh window still de-duplicate: 16 rows, 0 duplicates |
| `P3-T37` | a poll after the refresh window re-records an **unchanged** price: 32 observation rows, still 16 logical quotes |
| `P3-T38` | five polls → 120 observations, 24 logical quotes; cardinality flat, no duplicate observations |

`P3-T37` is the one that would have caught this originally. It fails outright
against the old semantics, because unchanged `last_update` made the gap zero.

### Two existing tests had encoded the defect

`P3-T01` asserted `captured_at == last_update`; it now asserts
`provider_updated_at == last_update` and that `captured_at` is the fetch time.

`P3-T28` built its dark-feed scenario by back-dating `last_update` in the
payload. That is no longer possible — and that is the fix working. A dark market
is now one we have **not polled recently**, so the test builds it at the database
layer via `olp_test.seed_stale_market()`. This is the third appearance of the
same underlying confusion the guard rail in migration 036 exists for: staleness
is about *our newest observation*, never about a timestamp we can choose.

### What this says about the original freeze

The read-only smoke was correct and its conclusions still stand — authentication,
payload shape, bookmaker-key stability and quota accounting were all validated
and none of them changed. The defect lived in a field that a read-only check
never evaluates, because nothing computes an age against a TTL until the data is
persisted and read back. **The lesson is about sequencing, not about the freeze:
a provider boundary is not fully proven until one controlled write has been read
back.**

---

## 7. Out of scope

No result grading — settlement still requires an explicit `settle_ticket_rpc`
call, and a scores feed remains unbuilt. No scheduler: `run_poll_cycle` is
designed to be *called* by cron or an Edge Function, but nothing schedules it
yet. No frontend.
