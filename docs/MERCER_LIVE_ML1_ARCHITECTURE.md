# Mercer Live — package ML-1, the live observation boundary

Built 2026-09-12. Spec: `docs/MERCER_LIVE_V0.1_PREREGISTRATION.md`. Code:
`scripts/mercer_live/`. Self-test: `scripts/mercer_live/selftest_mercer_live.py`
(hermetic; its own read-only gate is `.github/workflows/mercer-live-selftest.yml`).

ML-1 has exactly one job: **capture and preserve time-stamped live football
game state and live market observations, accurately.** It computes no edge,
generates no pick, allocates no units, posts nothing, grades nothing, calls no
LLM and changes nothing about the pregame football product. The self-test
proves each of those by absence.

---

## 1. What existed before — the Phase A audit

Read before anything was written. Findings, numbered to the audit brief.

1. **Football architecture.** Three layers (`docs/FOOTBALL_PIPELINE.md`):
   market-derived NUMBERS (`market.py`), LLM REASONING that may not produce a
   number (`writeup.py`), and a precommitted SELECTION rule. Weekly slate,
   per-game T−24 commitment, decision moment Saturday 14:00 ET. Frozen as
   fp-v0.3. Beside it, the D.J. Mercer Spotlight (`mercer.py`) is a
   human-picks record with its own commit gate and ledger.
2. **NFL/NCAAF event identity.** There is no single canonical event id. NFL
   sides carry `teams.py` franchise keys (32, relocations folded); NCAAF is
   "verbatim" — raw Odds API strings plus `espn_ncaaf.norm()`/`ALIASES` for
   the results join. Both results stores key by **ESPN event id**. Joins are
   exact on (away_key, home_key) within a capture, ambiguity refused
   (`market.find_event`, `mercer.resolve_event`).
3. **Odds API integration.** Three callers, all `/v4/sports/{sport}/odds`,
   all pregame: `fetch_data.py` (MLB board), `fetch_closing.py` (MLB close),
   `scripts/football/fetch_odds.py` (football T−24/close, h2h by default, us
   region). Credits read from headers before `raise_for_status`, appended to
   `data/odds_credits.json`. Paid tier, 100,000/month; **99,834 remaining** at
   the last reading (2026-09-12T10:47Z).
4. **Bookmaker identity.** Odds API `key` strings, canonicalised only by tier:
   `price_test.TIER1` (16 US-regulated) / `TIER2` (8 offshore), frozen in
   fb-v0.2. `market.unclassified_books()` refuses a book in neither.
5. **Market schemas.** Football captures keep every book's `markets[key] ->
   [{name, price, point}]` for `h2h`; the two Mercer research pulls also hold
   `spreads` and `totals` in the same shape. MLB consolidates to median + best
   per side (`fetch_data.consolidate_odds`). Settlement conventions live in
   `settle.py` (line = number added to the selected team; moneyline pushes).
6. **Snapshots.** One JSON file per capture, named by capture second,
   `captured_utc` inside, refuse-to-overwrite. Research pulls go to
   `data/mercer/odds/` and are stamped `capture_role: research`.
7. **Capture timestamps.** `captured_utc` (second precision, stamped before
   the request is sent).
8. **Provider timestamps.** Per-bookmaker `last_update` kept verbatim; the
   15-minute staleness filter compares it to `captured_utc`.
9. **Grading.** `grade_football.py` (T−24 nearest capture, close = last
   capture strictly before kickoff, in-play close refused), `mercer.py grade`
   (fingerprint check before booking), MLB `grade.py`. All append-only.
10. **Frozen boundaries.** fb-v0.1/v0.2 (both `frozen:`), fp-v0.3, Mercer
    Research Filter v1.0, the 2025 holdout lock (`asof.SPECS`), the tier
    lists, the push epilogue and staged-set contracts, `FOOTBALL_PREREG_V03.md`
    is a DRAFT and — noted, not fixed here — is **not** in `asof.SPECS` and
    carries no `frozen:` line, contrary to CLAUDE.md's "adding a future spec
    means adding it to asof.SPECS". Pre-existing; outside this package.
11. **Discord.** Webhooks only, host-validated (`post_discord.webhook_host_ok`);
    alerts to `DISCORD_WEBHOOK_URL_ALERTS` and nowhere else; football
    delivery idempotent per slate week; MLB delivery deliberately not.
12. **Scheduling.** cron-job.org → `workflow_dispatch` is the trusted trigger;
    GitHub `schedule:` measured 15–23 min late on a watchdog and **zero runs
    in 9.5 h** on an hourly board cron. Nothing in the repo runs more often
    than hourly.
13. **Append-only integrity.** Fingerprint-before-reveal (`commitments.json`),
    ledgers refuse regrades, `pbp_manifest.json` / `hist_index.json` hash
    gitignored raw data, `selftest_staging.py` proves `git add` semantics.
14. **CI pattern.** Numbered `check()` suites, hermetic code gate
    (`football-selftest.yml`, nine suites) vs post-push data monitors, negative
    controls, comment-stripped scans scoped to the block they protect.
15. **Reusable game-state ingestion.** Yes, partially: `espn_slate.py`,
    `espn_nfl.py`, `espn_ncaaf.py` and `capture_schedule.py` all read the ESPN
    scoreboard, but each **discards** the in-play fields (period, clock,
    `situation`) and stores one row per event, overwritten on refresh.
    Reusable: the endpoint, identity functions, "0 is not a score", the
    season-type allowlist. Not reusable as-is: the overwrite-in-place store.
16. **ESPN integrated?** Yes (above). No other live source. The Odds API
    `/scores` endpoint is unused.
17. **Join protections.** Exact keys, no fuzzy matching; ambiguity raises
    (`AmbiguousEvent`); unresolvable NFL names abort a pregame capture;
    NCAAF orthography via a deterministic normaliser plus an explained alias
    table; stale quotes excluded at evaluation, not at capture; team-name map
    generated from ESPN, never typed.
18. **Pregame assumption in existing odds code?** **Yes, everywhere, and it
    is safe.** `fetch_odds.py` stores whatever the endpoint returns — an
    in-play event would be captured with no flag — but `grade_football.py`
    refuses any close at or after kickoff and `pick_snapshots` only considers
    captures before kickoff, so an in-play quote cannot become a T−24 or a
    close. `fetch_closing.py` freezes the last pre-pitch line explicitly.
    `market.py` has no notion of phase at all. Consequence for ML-1: live
    quotes must live in a different directory and a different file format
    (they do), because the pregame loader globs `data/football/odds/<sport>_*.json`.

**What the audit could not do.** `site.api.espn.com` and `the-odds-api.com`
are blocked by this session's egress proxy, so no live payload was fetched.
The in-play ESPN `situation` sub-schema and the Odds API's in-play behaviour
are taken from documentation and existing code, and `capture.py probe`
exists so one command verifies both against reality.

## 2. Sources

| source | used for | cost | verified in-session |
|---|---|---|---|
| ESPN scoreboard (`site.api.espn.com/.../scoreboard`, `groups=80` for FBS) | game state: status, score, period, clock, `situation` | free, no key | schema from existing modules + docs; `situation` NOT fetched here |
| The Odds API `/v4/sports/americanfootball_{nfl,ncaaf}/odds` | live + pregame quotes, every book, `h2h,spreads,totals`, region `us` | markets × regions per call = **3** | endpoint and headers identical to three existing callers; in-play semantics NOT observed here |

The odds endpoint returns upcoming and in-play events together with no live
flag; `commence_time` in the past is the only provider hint. ML-1 does not use
it to declare a quote live — see section 5.

Available but deliberately not used: the Odds API `/scores` endpoint
(paid, redundant with ESPN), ESPN's own `odds` block (a single partner book),
ESPN win probability (an external model output; excluded by the spec).

## 3. Capture flow (one tick, one league)

```
capture.tick(league)
  1  run_id  = --run-id | slot_run_id(league, now, interval)     # idempotency key
     refused if runs.txt already lists it                          # nothing written
  2  ESPN scoreboard -> gamestate.observe()  -> live_game_observation rows
     structural failure -> nothing written for ESPN, error in the tick record
  3  active filter: any game with state == "in"?
     no  -> odds not called (reason recorded)
     yes -> budget.allow(cost)?  no -> not called (reason recorded)
  4  Odds API -> odds.observe(payload, game_index) -> live_market_observation rows
                                                   + live_market_coverage rows
     each row: identity -> join -> phase  (section 5)
  5  write raw payloads (gzip) -> append rows (gzip NDJSON) -> tick record -> runs.txt
```

`capture.py once` runs one tick per league; `loop` repeats on interval-aligned
slots until `--until`/`--max-minutes`; `probe` saves raw payloads only;
`seal` hashes closed partitions into the manifest; `status` reports what is on
disk, the budget and the burn table. `--dry-run` fetches game state (free),
reports what would happen, spends nothing and writes nothing.

## 4. Schema (`schema_version: ml1-1.0`)

**live_game_observation** — one per game per tick

| field | type | note |
|---|---|---|
| obs_id | hex32 | sha256(run_id, "espn", provider_event_id) |
| run_id, observed_at, request_sent_at | | section 6 |
| provider, provider_http_date, provider_ts | | provider_ts is null for ESPN |
| raw_ref, raw_sha256 | | the archived payload and its hash |
| sport, league | | football; nfl / ncaaf |
| event_id | `nfl:401…` | league + ESPN event id |
| provider_event_id, season{year,type,slug} | | |
| home{key,name,abbr,espn_team_id}, away{…} | | key: franchise key (NFL) or normalised name (NCAAF) |
| identity_mode | canonical / verbatim | |
| scheduled_start, neutral_site, venue | | |
| state, status_name, status_detail | pre / in / post | |
| game_complete | bool | |
| score{home,away} | int / null | null before kickoff |
| period, clock_seconds, clock_display | | |
| situation | object / null | possession (id + key), down, distance, yard_line, is_red_zone, timeouts, texts, last_play{id,type,text,score_value,team_espn_id,wallclock=null} |
| situation_present | bool | |
| missing_required | [str] | REQUIRED fields absent on this row |
| anomalies | [str] | impossible states, named, never repaired |

**live_market_observation** — one per (event, book, market) per tick

| field | note |
|---|---|
| obs_id | sha256(run_id, "the-odds-api", provider_event_id, book, market) |
| run_id, observed_at, request_sent_at, provider, provider_http_date, raw_ref, raw_sha256 | |
| sport_key, league, provider_event_id | |
| event_id, gamestate_obs_id, join_status | joined / unjoined / ambiguous / home_away_conflict / identity_error |
| identity_error | the resolver's message, or null |
| away_raw, home_raw, away_key, home_key, commence_time | |
| phase, phase_basis, is_live | pregame / live / post / unknown; gamestate / commence_time |
| book, book_tier | tier1 / tier2 / unclassified (from `price_test`) |
| market | h2h / spreads / totals |
| outcomes[] | {name, side (home/away/over/under/null), point, price_american} |
| book_last_update, market_last_update, quote_age_s | provider stamps verbatim; age = observed_at − stamp |
| availability | "quoted" (absence lives in coverage rows) |

**live_market_coverage** — one per event per tick: n_books, books_present,
books_quoting{market: [book]}, books_not_quoting{market: [book]}, join_status,
phase, markets_requested.

**tick** — one per (league, run): espn{ok, n_games, active{live, starting_soon,
later, final, live_event_ids}, n_errors, n_missing_required, n_anomalies},
odds{called, decision, ok, n_events, n_rows, joins{}, phases{}, quote_age{},
credits{}}, budget{}, errors[], written{}, capture_version, dry_run.

## 5. Event joins, and why the wrong event cannot be joined

A market row joins a game-state row only when **all** of these hold in the
same tick:

1. both team names resolve through the pregame system's own identity —
   `teams.from_name` (NFL, raises on anything unknown) or
   `espn_ncaaf.key_for` (FBS normaliser + explained aliases);
2. `(away_key, home_key)` matches **exactly one** game observed this tick;
3. that game's `scheduled_start` is within 12 h of the quote's `commence_time`.

Anything else is recorded, not joined: `ambiguous` (two matches — refused,
never chosen), `home_away_conflict` (the pair matches only with home and away
swapped — a neutral-site disagreement or a feed error, either way not the same
orientation), `unjoined`, `identity_error`. An unjoined row keeps its raw
strings and its price; it is simply not attached to any event.

**Phase is derived from observed game state, never from the clock.** `live`
requires the joined game's `state == "in"`. A quote with no joined game state
is `pregame` only while its commence_time is still in the future and
`unknown` otherwise — never `live`, because "probably started" is not an
observation. A delayed kickoff whose game state still says `pre` is
`pregame` even after its scheduled start.

The pregame product cannot see any of this: `market.load_snapshots` globs
`data/football/odds/<sport>_*.json`; ML-1 writes gzip NDJSON under
`data/mercer_live/raw/` and its store refuses to open under `data/football/`,
`data/mercer/`, `football/` or the site directories (self-test group 5).

## 6. Timestamps

| | meaning | source |
|---|---|---|
| `observed_at` | the moment Open Ledger received the response | this process's UTC clock, ms |
| `request_sent_at` | the moment the request left | same clock |
| `provider_http_date` | HTTP `Date` header | provider, verbatim |
| `book_last_update`, `market_last_update` | when the bookmaker's odds last changed | The Odds API, verbatim |
| `provider_ts` | a provider server timestamp | null for ESPN; never substituted |
| `quote_age_s` | `observed_at − (market_last_update or book_last_update)` | derived, stored beside both |
| partition date | `observed_at`'s UTC calendar date | UTC on purpose: an ET partition would move at DST changes |

Every timestamp is timezone-aware UTC; `parse_utc` returns None for a string
with no zone rather than assuming one; `iso_ms` refuses a naive datetime.

## 7. Idempotency semantics

The observation identity is **"we looked at X during run R"**, not "X had
value V". `run_id` is the interval slot (`nfl:20260913T173100Z`) unless
overridden. Consequences, all tested:

- an accidental re-execution inside the same slot is refused before any
  append (`runs.txt` is consulted first; it is written only after every
  append succeeded);
- the next slot is a new observation even when nothing changed — identical
  values, distinct `obs_id` and `observed_at`, no deduplication;
- a crash mid-tick leaves the run re-runnable; partial lines carry the same
  `obs_id`s so a reader can collapse them.

## 8. Failure modes

| failure | behaviour |
|---|---|
| ESPN unreachable / non-200 / not JSON / no `events` list | nothing written for ESPN; error in the tick record; odds NOT called (a quote that cannot be classified is not captured); `once` exits 1 |
| one ESPN event unreadable | that event in `errors`, the rest kept |
| odds non-200 / not JSON / not a list | credits still recorded; nothing written for odds; game state kept; exit 1 |
| a team name the repo cannot resolve | row written with `identity_error`, never joined; the tick continues |
| 401 / 429 from the odds endpoint in `loop` | odds disabled for the rest of the loop; game state keeps sampling |
| budget cap or reserve reached | odds not called; reason in the tick record |
| duplicate run | refused, nothing appended |
| a sealed file's bytes change | manifest appends a discrepancy; the original entry stands; `seal` exits 1 |

Nothing is retried with stale data presented as fresh; nothing is fabricated;
nothing here can fail a workflow, because nothing here runs in one.

## 9. API cost

One odds call per league per tick, **3 credits** (h2h, spreads, totals × us),
made only while at least one game in that league is in progress.

| cadence | per live hour | NFL Sunday (10.5 h) | NFL week (Thu+Sun+Mon ≈ 17.5 h) | NCAAF Saturday (14 h) | both leagues, 4-week month |
|---|---|---|---|---|---|
| 60 s | 180 | 1,890 | 3,150 | 2,520 | **~22,700** |
| 120 s | 90 | 945 | 1,575 | 1,260 | ~11,300 |
| h2h only, 60 s | 60 | 630 | 1,050 | 840 | ~7,600 |

Against 100,000/month with ~99,800 unspent that is affordable at one minute
for both leagues and all three markets (~23% of the allowance). Two
protections are enforced before every call, not after: a **daily cap**
(default 2,000 — enough for an NFL Sunday, small enough that a runaway loop
cannot spend the account overnight) and a **reserve** (default 5,000 remaining
— the MLB board at ~250/month and the football T−24/close captures at ~208
are the product, and shadow research must never starve them). Every call and
every refusal is in the tick record, and each reading is appended to
`data/odds_credits.json` under `source: mercer_live:<league>`.

Disk, not credits, is the real cost: NCAAF Saturday at 60 s is on the order
of 3,000 market rows per tick. gzip-appended NDJSON keeps a full Saturday to
tens of MB; raw payload archives roughly double that.

## 10. Scheduler recommendation

**Not GitHub Actions.** Its scheduler has measured 15–23 minutes late on a
daily watchdog and zero runs in 9.5 hours on an hourly cron in this
repository; the production pipelines already moved to external dispatch for
jobs that run once an hour. Per-minute sampling on it is not sampling, and
per-minute commits to `main` would collide with seven workflows that already
race to push. ML-1 therefore ships as a callable process (`capture.py once`)
with an in-process loop, and no workflow invokes it (asserted by the
self-test).

| option | verdict |
|---|---|
| **A. Daniel's machine, `capture.py loop` during game windows** | **Recommended for the first sessions.** Zero infrastructure, zero secrets outside `.env.local`, the operator watches the first live payloads. Cost: a machine awake on Saturday/Sunday. |
| **B. A small always-on host (a $5 VM or container) running `loop` from a system cron/timer per game day** | Recommended once ML-2+ needs every game session. Same code, same store; add rsync/object-storage backup of `raw/` and run `seal` nightly. |
| C. cron-job.org → a small HTTP endpoint that runs one tick | Works for one-minute cadence (cron-job.org supports it) but needs a host anyway — B without the loop. |
| D. Cloudflare Worker cron (`* * * * *`) writing to R2/D1 | Viable and cheap, but a second runtime, a second storage format and a second secrets store for a shadow study; only worth it if B proves unreliable. |
| E. cron-job.org → `workflow_dispatch` (the repo's pattern) | No: it is still GitHub Actions per minute, with a runner cold-start and a commit per tick. |

Sealing and committing the manifest is a nightly, hand- or cron-run
`capture.py seal` followed by an ordinary commit of
`data/mercer_live/manifest.json` — one small file, one commit a day.

## 11. Privacy and security boundaries

- `ODDS_API_KEY` is read through `localenv.require` at the moment of a real
  odds call and nowhere else; a fixture or `--dry-run` tick never reads it.
  No key appears in any record, log line or payload archive (the query
  string is not stored; `raw_ref` holds the response body only).
- No Discord, email, social, Anthropic or other credential is read; the
  package has no code path that could use one (self-test group 20).
- The raw store is gitignored. `manifest.json` carries hashes and counts.
- `probe` writes to a directory the operator names, never the store.

## 12. Explicit non-goals of ML-1

No consensus, no de-vig, no edge, no model, no thresholds, no circuit breakers,
no positions, no ledger, no grading, no Discord, no site page, no LLM, no
change to any pregame path, no encryption (it holds no picks), no GitHub
Actions schedule.

## 13. Shadow-mode roadmap (not implemented; each package must earn its place)

| package | scope |
|---|---|
| **ML-2** | Game-state feature construction from ML-1 rows: score differential, time remaining (period + clock → seconds), possession, field position, down/distance, timeouts, expected remaining possessions, pace. Pure functions over observations, versioned, with a completeness report. |
| ML-3 | Baseline live win-probability and expected-final-score / total model. Fitted on historical play-by-play already on disk (`data/football/raw/pbp/`, hashed in `pbp_manifest.json`) with `asof.py`'s point-in-time discipline; evaluated on held-out seasons before it ever sees a live row. |
| ML-4 | Live market comparison + candidate generation: de-vig per book, compare to ML-3, apply the section-10 thresholds, emit candidates with diagnostics. No positions. |
| ML-5 | Circuit breakers (section 11) + shadow positions into `data/mercer_live/shadow_ledger.json`. Refuses to run while the spec reads `frozen: NOT YET`. |
| ML-6 | Prospective shadow grading + the evaluation report the promotion gate reads. |
| ML-7 | Private D.J. Mercer Discord beta (Mode 2), admin channel only, after ML-6 and written authorisation. |
| ML-8 | Interactive Discord application / slash commands. |

Not all eight need ship. ML-3's result decides whether ML-4 is worth building.

## 14. Which market to model first — recommendation

**Capture all three now (done: the marginal cost is two credits per call and
the payload is the same request). Model live totals first.** Reasons, from
the evidence on disk rather than preference:

- The pregame studies found the NFL moneyline efficient at T−24 and best-price
  shopping worth exactly the vig. Nothing on disk says anything about
  in-play, but the live moneyline is the market most directly downstream of
  the same efficient pregame number plus a score — the market maker's job is
  easiest there.
- A live total decomposes into (points already scored) + (expected remaining
  scoring), and the second term is a function of time remaining, pace and
  scoring environment — features ML-1 captures directly and that historical
  play-by-play (already on disk, 2010–2024) can fit and validate without
  buying anything. The MLB totals track was chosen for the same structural
  reason and turned out to be the model's best-calibrated output.
- The live spread is the hardest of the three to settle and to size (key
  numbers, section 10) and adds the least beyond moneyline + total.

This is a recommendation for ML-3's order of work, not a commitment that any
market is beatable. ML-3 must report all three against the market before
ML-4 chooses which to generate candidates for.
