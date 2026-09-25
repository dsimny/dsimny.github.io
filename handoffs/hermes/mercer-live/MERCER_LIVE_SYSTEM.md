# Mercer Live — the system as it exists today

Written 2026-09-25 against the frozen package `mercer-live-ml1-v1.0`.

Repository sources of truth, which win over this file if they ever disagree:
`docs/MERCER_LIVE_V0.1_PREREGISTRATION.md` (frozen 2026-09-14),
`docs/MERCER_LIVE_ML1_ARCHITECTURE.md`, `docs/MERCER_LIVE_AUDIT_2026-09-14.md`.

---

## 1. The chain, and where we actually are on it

```
DATA  →  MODEL  →  CIRCUIT BREAKERS  →  DECISION  →  LEDGER  →  D.J. MERCER PRESENTATION
 ^^^^
 ML-1: this is the ONLY link that exists.
```

The direction of that arrow is the entire product thesis. Mercer is a narrator
standing at the end of a pipeline, reading out what the records already say. He
is never the source of an opinion that then goes looking for a wager. The
forbidden shape, stated so it can be recognised on sight, is:

```
LLM  →  opinion  →  wager          ← never, in any package, in any channel
```

Everything to the right of DATA is documented and unbuilt. There is no model, no
probability, no candidate, no breaker evaluation, no decision, no live ledger and
no Mercer presentation layer for live football. ML-1 writes down what two
providers said, and when we asked them. That is the whole of it.

An analogy that has held up well: ML-1 is the tide gauge bolted to the pier. It
records the water level every minute and stamps each reading with the time. It
does not forecast the tide, does not advise whether to sail, and does not know
what a boat is. Those are later instruments, and each has to be built and proven
on its own.

## 2. Sources

**ESPN public scoreboard** — game state. Free, no key, no account. NFL and
NCAA FBS have separate endpoints. This is the only source of period, clock,
score, possession, down, distance, field position and last play.

**The Odds API v4** — market quotes. Keyed, metered, and billed at
markets × regions credits per call. Mercer Live makes **at most one call per
sport per tick**, and only inside a live window. Default markets are
`h2h,spreads,totals` in the `us` region, so 3 credits per call.

Neither provider is polled speculatively. If no game in a sport is in progress
or about to start, that sport's odds call is not made at all, which is how a
system that could cost 30,000 credits a month has so far cost **zero**.

## 3. Canonical event identity

The two providers do not share an id, so Mercer Live mints one:

```
<sport>:<slate_week_et>:<AWAY>@<HOME>
```

`slate_week_et` is the ET-anchored football week (Tuesday anchor), not a UTC
one. This matters for exactly the reason it mattered in Mercer Spotlight: a
Monday-night game at 20:15 ET is 00:15 UTC Tuesday, so a UTC anchor files it
under the following week and splits the slate.

Team keys resolve in one of two **identity modes**, declared per sport:

| sport | mode | how sides resolve |
|---|---|---|
| NFL | `canonical` | franchise keys from the existing `teams.py`, on both sides of the join |
| NCAAF | `normalised` | a normalising function, because no canonical FBS franchise table exists |

**The join names its refusals instead of guessing.** Every attempted join lands
on one of: `JOINED`, `UNJOINED`, `AMBIGUOUS`, `KICKOFF_MISMATCH` (the two
providers' kickoff times differ by more than the two-hour tolerance), or
`UNRESOLVED`. A quote that cannot be joined confidently is stored as unjoined
with its reason recorded. It is never attached to a game on a hunch. This is the
single most important property of the identity layer: a wrong join would
silently poison every downstream package, and a refused join is visible.

## 4. Observation-time semantics

Two timestamps, and conflating them would break the no-look-ahead rule the
preregistration is built on:

- **`observed_at`** — the instant *we received the response*. Always present.
  This is the timestamp that answers "what could Mercer have known, and when".
- **`provider_timestamp`** — the provider's own stamp for the payload. Kept
  verbatim when supplied. **ESPN supplies none**, at payload or event level, so
  this is `null` on every game_state record. Measured, not assumed.

The Odds API does supply a per-bookmaker `last_update`, which is what a future
freshness guard will measure against — and that measurement is one of the three
items the preregistration explicitly deferred to ML-1 evidence.

## 5. Append-only storage

```
data/mercer_live/raw/<sport>/<ET date>/<kind>_<ET hour>.jsonl   GITIGNORED
data/mercer_live/raw/runs/<ET date>/<run_id>.json               GITIGNORED
data/mercer_live/digest/<ET date>.json                          COMMITTED
```

Three record kinds: `game_state`, `market_event`, `market_quote`. One run record
per tick, always, including a tick that observed nothing.

`observation_id` is a deterministic SHA-256 over the record's identifying
content, so a retry of the same `run_id` re-derives the same ids and the store
refuses them as duplicates. That is what makes a crash-and-retry safe.

**The raw stream is gitignored on purpose.** One NFL Sunday at one-minute
cadence is roughly 4 MB compressed; a season is hundreds of MB. What gets
committed is the **digest**: SHA-256 of every shard plus run and credit counts.
Whoever runs the capture keeps the bytes; the digest proves them. This is the
same split the repository already uses for `data/football/raw/pbp/` and its
manifest.

Writes are **confined**: the store refuses any path outside its own data
directory, and the self-test asserts it.

## 6. Phase labeling

Every market quote carries an explicit `quote_phase`:

`pregame` · `live` · `post` · `commenced_unjoined` · `unknown`

**Only the exact string `pregame` may be read as pregame.** Not "anything that
isn't live", not a truthy check. A live in-game price mistaken for a pregame or
closing price would corrupt CLV, the football ledger, and any comparison built
on either. The label is derived from the same tick's game state, so the two
cannot drift apart.

## 7. Live / pregame isolation

This was verified against production code, not argued from design. A probe tick
was written into the real `data/mercer_live/`, then every existing consumer was
re-run before and after — `market.load_snapshots`, `capture_schedule.captures`,
`espn_ncaaf.latest_odds_snapshot`, the closing-file glob, the board globs. All
returned identical results. No production script walks `data/` broadly, and
`mercer_live` appears nowhere outside its own package.

Asserted by the suite: Mercer Live writes nothing outside `data/mercer_live/`;
references no ledger, board, commitment store, `data/football/odds/` or
`data/mercer/odds/`; and nothing under `scripts/football/` imports it.

## 8. Retry behaviour

A tick has a `run_id` (uuid4 by default). Re-running with the **same** `run_id`
after a crash is a no-op — the run record already exists, and the observation ids
re-derive identically and are refused as duplicates. A **new** tick gets a new
`run_id`, and its records are new observations even when every field is
unchanged: sampling the world again a minute later is a different observation of
the world, and the preregistration says so.

Proven live. The smoke workflow re-runs the first tick's `run_id` and diffs shard
checksums; run 35673309148 printed `retry changed no shard byte`, and the
observation counts on the 2026-09-25 runs matched the tick counts exactly.

## 9. The stale-payload hazard — read this one carefully

On 2026-09-22 at 03:20:21Z, ESPN returned a **fully formed, complete,
in-progress payload**: period 1, 0:11 on the clock, 0-7, last play an extra
point. Nothing was malformed and nothing was missing.

The game had kicked off at 00:47Z and was in fact **FINAL 6-28**. The payload was
roughly two hours out of date. Ninety seconds later, and on five consecutive
observations after that, the same game read FINAL.

There was no provider timestamp to expose it, because ESPN sends none.

**A single observation cannot be shown stale from its own contents.** Staleness
is a property of the *series*, and the series is exactly what ML-1 stores. ML-1
recorded the stale payload faithfully — correct behaviour for a capture boundary;
nothing was smoothed, corrected or dropped.

The consequence is written into preregistration section 11 as a requirement,
before any model exists: the stale-state guard must catch a payload that has
**jumped backwards**, not only a feed that has stopped moving. Decreasing period,
increasing clock within a period, decreasing score, a post→in transition.

One tempting shortcut is explicitly ruled out: the stale payload happened to be
*impoverished* (it lacked `down_distance_text`, `possession_text` and win
probability, which fresh live payloads do supply). Sparseness must **not** be
used as a staleness detector, because a fresh *pregame* payload is also sparse.
The backwards-jump rule is the guard.

## 10. The yard-line ambiguity

`yard_line` is **absolute, measured from the HOME team's goal line on a 0–100
field**. It is not "yards to the end zone for the team with the ball".

Measured: with Atlanta in possession at their own 24, `down_distance_text` read
`"2nd & 3 at ATL 24"` while `yard_line` read **76**. Consistent across seven
observations with both teams in possession at different times.

Classified **PRESENT BUT AMBIGUOUS**. Any future feature that uses field position
must resolve it against possession first. Reading it naively would silently
mirror the field for one of the two teams on every play.

## 11. Two zero-value traps

- **Scores** read `null` before kickoff, because ML-1 refuses ESPN's `"0"` for an
  unplayed game — an unplayed 0 and a scored 0 are different facts.
- **`period` and `clock` get no such treatment.** ESPN sent `period 0` with a
  `0:00` clock before kickoff and `period 4` with `0:00` after the final, and
  `0:00` is *also* a real value at the end of a period in progress. These are
  recorded verbatim and **must be gated on `status_state` by whoever reads
  them.**

## 12. Credit protections

`scripts/mercer_live/credits.py`:

| guard | value | meaning |
|---|---|---|
| `CREDIT_FLOOR` | 5,000 | no live call is made below this remaining balance |
| `DAILY_CAP` | 4,000 | Mercer Live credits per ET day, both sports combined |
| `BOOK_INTERVAL_S` | 3,600 | ledger booking cadence per sport on success |

Bookings go into `data/odds_credits.json` through `scripts/odds_credits.py` — the
repository's one canonical credit ledger — at most hourly per sport on success,
and **always** on a non-200. The throttle exists so Mercer Live cannot evict the
MLB and football readings from that file's 60-slot window. Every reading is also
written into the run record regardless of whether it was booked.

Sizing context, so the guards read as more than arbitrary numbers: one-minute
cadence across both sports would be roughly **30,000 credits a month** — about
30% of the 100,000 allowance, and roughly 100× current football spend. Spend to
date: **zero**.

## 13. Current workflows

| workflow | trigger | permissions | spends |
|---|---|---|---|
| `.github/workflows/mercer-live-selftest.yml` | push / PR on Mercer Live paths | `contents: read` | nothing |
| `.github/workflows/mercer-live-smoke.yml` | `workflow_dispatch` only | `contents: read` | nothing by default |

The self-test workflow is a **hermetic code gate**: a red X there means Mercer
Live code regressed and nothing else. It is its own workflow precisely so that
signal stays unambiguous.

The smoke workflow takes one to ten sequential ticks against the real providers
into a runner temp directory, commits nothing, pushes nothing, posts nothing,
and uploads a 7-day artifact. `spend_credits` defaults to **false**, in which
case the API key never reaches the process at all. It has no `schedule:` and
**must never get one** — GitHub's scheduler is measured 15–23 minutes late in
this repository, and a one-minute cadence needs a host that can keep time.

## 14. Scheduler

**Nothing is deployed.** The deliverable is a callable function (`run_tick`) and
a bounded CLI loop. The recommended host shape is documented in architecture
section 11: cron-job.org → a bearer-token endpoint → a small machine, the same
shape Open Ledger Play already runs. Deploying it is a Daniel decision, not a
Hermes one, and it would be the first thing that could ever spend a credit
unattended.

## 15. Where everything lives

| | |
|---|---|
| Code | `scripts/mercer_live/` — `capture.py`, `common.py`, `credits.py`, `espn_state.py`, `identity.py`, `odds_quotes.py`, `store.py` |
| Tests | `scripts/mercer_live/selftest_mercer_live.py` — 229 hermetic checks, groups [1]–[24] |
| Data | `data/mercer_live/` — `raw/` (gitignored), `digest/` (committed), `README.md` |
| Preregistration | `docs/MERCER_LIVE_V0.1_PREREGISTRATION.md` |
| Architecture + freeze | `docs/MERCER_LIVE_ML1_ARCHITECTURE.md` |
| Audit | `docs/MERCER_LIVE_AUDIT_2026-09-14.md` |
| Workflows | `.github/workflows/mercer-live-selftest.yml`, `.github/workflows/mercer-live-smoke.yml` |

## 16. Running a capture by hand

```
python scripts/mercer_live/capture.py --sport nfl --no-odds
python scripts/mercer_live/capture.py --sport nfl --sport ncaaf --loop --interval 60 --max-ticks 60
python scripts/mercer_live/capture.py --sport nfl --data-dir /tmp/ml --dry-run
```

`--no-odds` spends nothing. `--dry-run` fetches and reports without writing.
`--data-dir` redirects the store. `--digest` writes the day's digest.
`--run-id` reuses a run id to make a retry idempotent.

Python 3.12 is the interpreter the CI uses; use it locally too.
