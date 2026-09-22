# Mercer Live — package ML-1, the Live Observation Boundary

Built 2026-09-14 against `MERCER_LIVE_V0.1_PREREGISTRATION.md` (frozen the same
day) and `MERCER_LIVE_AUDIT_2026-09-14.md`. Code: `scripts/mercer_live/`.
Data: `data/mercer_live/`. Gate: `.github/workflows/mercer-live-selftest.yml`.

ML-1 has exactly one job: **capture and preserve time-stamped live football
game state and live market observations accurately.** It computes no edge,
generates no pick, allocates no unit, posts nothing to Discord, grades
nothing, uses no LLM, and alters no pregame behaviour. Every one of those
"nots" is asserted by `selftest_mercer_live.py`, not merely promised.

Think of it as a court stenographer, not a commentator: it writes down what
was said, when, and by whom, and it never adds an opinion to the transcript.

---

## 1. Sources

| need | source | key | cost | verified |
|---|---|---|---|---|
| live game state (score, clock, period, status, situation) | ESPN public scoreboard, `site.api.espn.com/.../football/{nfl,college-football}/scoreboard?dates=YYYYMMDD` (+`groups=80` for FBS) | none | free | **VERIFIED LIVE 2026-09-21/22** against NYG at LA, runs 35673309148 and 35682762374. Every REQUIRED field present on every observation; the `situation` block present throughout the in-progress state. See section 17 |
| live market quotes (h2h, spreads, totals; every book) | The Odds API `GET /v4/sports/{key}/odds?regions=us&markets=h2h,spreads,totals&oddsFormat=american&dateFormat=iso&commenceTimeFrom=…&commenceTimeTo=…` | `ODDS_API_KEY` | markets × regions credits per call | in-play quotes and per-book/per-market `last_update` seen in repository captures; `commenceTime*` filtering is applied client-side as well, so it is safe if the provider ignores it |

Not used: The Odds API `/scores` (a future cross-check), ESPN summary /
play-by-play (FUTURE fields), any Discord or LLM endpoint.

## 2. Capture flow (one tick)

```
for each sport (nfl, ncaaf):
  1. ESPN scoreboard for today ET (and yesterday ET before 05:00 ET)
       observed_at = instant the response was received
       -> one game_state record per event; unreadable events -> named errors
  2. live window = events with state "in"
                 + events with state "pre" kicking off within --lead-min (20)
     if the window is empty            -> odds SKIPPED, reason recorded
     if no ODDS_API_KEY                -> odds SKIPPED, game state still stored
     if budget refuses (section 9)     -> odds SKIPPED, reason recorded
  3. ONE Odds API call for the sport, windowed [now-8h, now+lead]
       credit headers read BEFORE the status check, booked per section 9
       non-200 -> nothing parsed, reading kept
       200     -> one market_event per event; one market_quote per outcome,
                  joined to THIS tick's game_state records, phase-labelled
       pregame events kicking off beyond the lead window are dropped and
       COUNTED (they are the pregame pipeline's evidence, captured hourly there)
  4. append every record to its shard (duplicates by id refused and counted)
one run record per tick, written atomically, never overwritten
```

`capture.run_tick()` is the callable; `capture.py` is the CLI with a
bounded `--loop`. Every side effect (clock, both fetchers, credit booking,
store root) is injectable, which is how the suite runs the whole tick with no
network.

## 3. Schema (`schema_version: ml1-v1`)

Three record kinds, JSON Lines, one record per line, all fields UTC.

**game_state** (one per ESPN event per tick)

| field | class | meaning |
|---|---|---|
| observation_id | id | sha256(kind, run_id, source, sport, provider_event_id) |
| run_id, observed_at, source (`espn-scoreboard`), provider_timestamp (always `null`) | provenance | section 5 |
| sport, league, provider_event_id, canonical_event_id | identity | section 4 |
| home, away, home_name, away_name, home_espn_team_id, away_espn_team_id | identity | keys per sport; display names never used for joins |
| scheduled_start, season_year, season_type, season_slug | REQUIRED | kickoff UTC; regular-season allowlist inherited for any future grading |
| status_name, status_state (pre/in/post), status_detail, completed | REQUIRED | unrecognised state is `null`, never coerced |
| home_score, away_score | REQUIRED once started | `null` before kickoff — ESPN's "0" is not a score |
| period, clock_seconds, display_clock | OPTIONAL | |
| possession (home/away/null), down, distance, yard_line, is_red_zone, home_timeouts, away_timeouts, down_distance_text, possession_text, last_play {id,type,text,score_value} | OPTIONAL | from `competitions[0].situation`; possession resolved by team id, else `null` |
| espn_win_probability_home | recorded, never an input | third-party model output kept as a reference series only |
| missing_required, missing_optional, complete | audit | names of absent fields; `complete` is false when any REQUIRED field is absent |

**market_event** (one per Odds API event per tick, even with zero books)

`observation_id` = sha256(kind, run_id, source, sport, provider_event_id);
identity fields as above plus `home_raw`/`away_raw` (provider strings),
`identity_status` (resolved/unresolved), `commence_time`,
`markets_requested`, `n_books`, `no_quotes`, `markets_offered` {book: [markets]},
`books_per_market` {h2h, spreads, totals}, `n_quotes`, `quote_phase`,
`phase_basis`, `join_status`, `game_state_observation_id`.

**market_quote** (one per event × book × market × outcome per tick)

`observation_id` = sha256(kind, run_id, source, sport, provider_event_id, book,
market, outcome, point); identity fields; `book` (verbatim key); `market`
(h2h/spreads/totals); `outcome` (team key, or `Over`/`Under`); `outcome_raw`;
`point` (float, `null` on h2h, required otherwise; sign belongs to the side, as
in `settle.py`); `price` (int American, |price| ≥ 100);
`provider_book_last_update`, `provider_market_last_update` (verbatim strings),
`provider_timestamp` (market stamp, else book stamp), `quote_age_seconds`
(observed_at − provider_timestamp, may be negative, `null` when no stamp),
`age_basis`; `quote_phase`, `phase_basis`, `join_status`,
`game_state_observation_id`.

**run record** (one per tick, `raw/runs/<ET date>/<run_id>.json`): per sport
the game-state report (dates read, observed_at, counts by state, incomplete
count, errors) and the odds report (decision + reason, observed_at, HTTP
status, credit reading, booking outcome, events returned/stored/dropped, quote
count, phase and join histograms, errors, budget snapshot); overall
records_written, duplicates_skipped, shards, flattened errors, exit code, the
API key's 12-hex fingerprint (never the key).

## 4. Event identity and joins

`canonical_event_id = <sport>:<slate_week_et>:<AWAY_KEY>@<HOME_KEY>`

- NFL keys: `teams.canonical()` for ESPN abbreviations, `teams.from_name()`
  for Odds API names — 32 franchise keys, `UnknownTeam` on anything else.
- NCAAF keys: `espn_ncaaf.norm()` / `key_for()` — the normalised display name
  with the one explained alias. Exact match after normalisation.
- Slate week is ET-anchored (`common.slate_week_et`, asserted equal to
  `mercer.slate_week`), so a Monday-night kickoff at 00:15Z Tuesday is in its
  own week, and the 03:59Z-vs-04:00Z kickoff disagreement that needed a manual
  adjudication in the pregame pipeline collapses to one id.
- A quote joins a game-state record only when: the id is resolved; EXACTLY ONE
  game-state record from THE SAME TICK carries it; both clocks are present;
  and they agree within 2 hours. Otherwise `join_status` is one of
  `unjoined`, `ambiguous`, `kickoff_mismatch`, `unresolved` — a named refusal
  stored on the record. No cross-tick joins, no nearest-name, no nearest-time.
- An unresolvable team produces a `market_event` marked `unresolved` and NO
  quote rows: the fact that a priced event existed is kept, the prices are not
  attached to a game they might not belong to.

## 5. Timestamps

| field | meaning | never |
|---|---|---|
| `observed_at` | the UTC instant Open Ledger's process received the provider response; one value per fetch, shared by every record from it | the provider's last-update; the kickoff; a later processing time |
| `provider_timestamp` | the provider's own stamp for the datum, verbatim (Odds API market `last_update`, else book `last_update`; ESPN: `null`) | substituted for `observed_at`; invented when absent |
| `quote_age_seconds` | `observed_at − provider_timestamp` | used to drop anything in ML-1 |

All storage is UTC. `iso()` refuses naive datetimes; `parse_utc()` returns
`None` for zone-less strings. Eastern time is used only for ESPN's `dates=`
parameter, the slate week in ids, and the shard directory names.

## 6. Phase label

The Odds API returns pregame and in-play events in one response with no flag.
Every quote and event carries `quote_phase` + `phase_basis`:

| phase | when | basis |
|---|---|---|
| pregame | joined state says `pre`, or unjoined and `commence_time > observed_at` | game_state / commence_time |
| live | joined state says `in` (wins over a provider clock that disagrees) | game_state |
| post | joined state says `post` | game_state |
| commenced_unjoined | `commence_time ≤ observed_at` and no joined state | commence_time |
| unknown | no `commence_time` | none |

`odds_quotes.is_pregame()` is true for exactly `pregame`. Every other label
fails closed toward "not pregame", so this stream cannot feed a closing-line
computation even if read by mistake — and nothing in `scripts/football/`
reads it at all (asserted).

## 7. Idempotency semantics

- `run_id` per execution (uuid4, or `--run-id` for a retry).
- Observation ids are content-derived from `run_id` + identity fields, so a
  retry of the same execution re-derives the same ids and the store refuses
  them as duplicates (counted). A retry whose run record already exists is
  replayed without any fetch or write.
- A NEW execution has a new `run_id`; its records are new observations even
  when every value is identical. Sampling again is a distinct observation.
- Shards are opened append-only; a torn trailing line from a crashed writer is
  left in place and the next append starts on a fresh line. Run records are
  written atomically (temp + rename) and never overwritten.

## 8. Failure modes

| failure | behaviour |
|---|---|
| ESPN transport/HTTP error | recorded on the run with the reason; no game-state records; odds skipped for that sport (no window to judge); exit 1 if nothing at all was observed |
| an ESPN event that cannot be read without guessing | skipped, error recorded with provider id and reason; every other event still stored |
| Odds API transport error | recorded; game-state records still stored |
| Odds API non-200 | credit reading captured and booked; nothing parsed; reason recorded |
| malformed outcome / book / event | that unit skipped and recorded; siblings survive |
| identity unresolved | event row marked `unresolved`, no quotes |
| store cannot write | `StoreError`, exit 2; nothing else in Open Ledger is involved |
| budget floor / cap | odds skipped, reason recorded; game state still captured |

Nothing is fabricated, nothing stale is re-stamped, and no other workflow
shares state with this one except a READ of `data/odds_credits.json` and the
throttled bookings described next.

## 9. API cost and credit protection

Per call: markets × regions credits — 3 at the default `h2h,spreads,totals`
over `us`. Calls happen only when a sport has a game in progress or within the
lead window. Estimate for one-minute cadence, both sports, three markets:

| window | hours/week | credits/week |
|---|---:|---:|
| NFL Thu + Sun (3 slots) + Mon | ~17.5 | ~3,150 |
| NCAAF Sat + weeknight games | ~24 | ~4,320 |
| **total** | | **~7,500/week ≈ 30,000/month** |

That is ~30% of the 100,000 allowance and ~100× today's football spend, so it
is protected mechanically (`credits.py`): no call while the shared ledger's
latest balance is below **5,000** (or unknown); no call once today's run
records show **4,000** credits spent by this package; every reading kept in
the run record; bookings into the shared `data/odds_credits.json` go through
`scripts/odds_credits.py` (the one writer) at most **once per hour per sport**
on success and **always** on a non-200. Cadence is a CLI argument
(`--interval`), so halving the spend is an operational choice, not a code
change; two-minute polling is ~15,000/month.

## 10. Storage

`data/mercer_live/raw/<sport>/<ET date>/<kind>_<ET hour>.jsonl` and
`raw/runs/<ET date>/<run_id>.json` — **gitignored**. Measured: one NFL Sunday at
one-minute cadence ≈ 4 MB gzipped, a college Saturday ≈ 4×, a season hundreds
of MB; the Pages repository is not the place. `data/mercer_live/digest/<ET
date>.json` — **committed**: SHA-256, bytes, line and parsed counts, observed_at
span per shard; run, error, call and credit totals per day. Whoever runs the
capture must keep the raw stream on persistent storage (a volume, object
storage); the digest is what proves those bytes are the observed bytes.

Not PostgreSQL, deliberately: the football pipeline's canonical persistence
is append-only JSON under `data/`, and the only Postgres in the repository
belongs to Open Ledger Play with its own frozen contracts. The store is one
small module (`store.py`) so a Postgres backend under a NEW `mercer_live`
schema could be added later without changing record semantics.

## 11. Scheduler recommendation

**Not GitHub Actions at one-minute cadence.** Measured in this repository:
`schedule:` fires 15–23 minutes late and once not at all for 9.5 hours; a
per-minute job would also mean a commit per tick or a lost runner per tick.

Recommended, in order of fit:

1. **A small always-on-during-games host running `capture.py --loop`**, started
   per game window by cron-job.org (the trigger the repo already trusts) and
   stopped by `--until`. The repository already has this exact shape in
   production for Open Ledger Play: cron-job.org POSTs to a bearer-token
   endpoint on a Fly machine that holds the API key in its environment and
   auto-stops when idle. A Mercer Live variant is `capture.py` behind that
   endpoint with a persistent volume for `data/mercer_live/raw/` and a nightly
   `--digest` push of the small digest file. Cost: one shared-cpu machine a few
   hours a week.
2. **cron-job.org every minute → the same endpoint, one tick per request** —
   same host, no long-lived loop, at the price of one HTTP round trip per
   tick and cron-job.org's own jitter.
3. **Cloudflare Workers Cron Triggers** — supports `* * * * *`, but Workers are
   JavaScript; this package would have to be ported, and D1 would replace the
   JSONL store. Only worth it if the host in (1) proves operationally costly.
4. **A GitHub Actions dispatch that runs the loop inside one job** (up to ~6 h)
   — works for a single window with an artifact upload, but artifacts expire,
   commits per window are large, and the start time inherits the lateness. Use
   only for a one-off dark rehearsal, never as production.

ML-1 ships the callable and the bounded loop, plus a dispatch-only, read-only
smoke workflow. It deliberately deploys nothing.

## 12. Verifying the unverified fields

`Actions → Mercer Live smoke capture (read-only) → Run workflow` with
`spend_credits=false` during a live window runs one ESPN-only tick into the
runner's temp directory and uploads it as a 7-day artifact. Inspect
`game_state_*.jsonl` for `missing_optional` on in-progress games: that list
is the answer to which OPTIONAL fields ESPN actually supplies. With
`spend_credits=true` it also makes one odds call per active sport (3 credits
each) and the `market_event` rows show live coverage per market and per book.
Nothing is committed or posted either way.

## 13. Privacy and security boundaries

- `ODDS_API_KEY` is read from the environment or the gitignored `.env.local`
  via `localenv`, never from an argument, never logged; the run record carries
  a 12-hex fingerprint. The suite asserts the key value appears nowhere in a
  run record.
- No Discord, Whop, GitHub, Cloudflare or LLM secret is referenced anywhere in
  the package (asserted by source scan with a negative control).
- Writes are confined to the store root by a path check; the suite proves two
  ticks change no byte under the repository's `data/`, and that
  `index.html` / `feed.xml` are untouched.
- Both workflows hold `contents: read`, have no `schedule:`, no `git add`,
  `commit`, `push`, and no alert step.
- ESPN game state contains no personal data; bookmaker keys are provider
  identifiers.

## 14. Explicit non-goals of ML-1

No consensus, de-vig, edge, fair value, model, candidate, breaker evaluation,
signal, position, unit, ledger, grading, Discord mode, persona, site page,
feed item, or copy of any kind. No change to `scripts/football/`,
`data/football/`, `data/mercer/`, any MLB file, or any workflow that existed
before. No production deployment.

## 15. Tests

`scripts/mercer_live/selftest_mercer_live.py` — hermetic (requests.get/post
replaced with functions that fail the suite if called), temp-dir only. Groups
[1]–[24] follow the brief's required list in order; [24] also holds the
repository contracts (workflows, gitignore, credit policy, store integrity,
slate-week drift, CLI plumbing). Run in CI by `mercer-live-selftest.yml`, a
separate gate so its red X means "Mercer Live code", nothing else.

## 16. Shadow-mode roadmap (not implemented; complexity must earn its place)

| package | scope | ships only if |
|---|---|---|
| **ML-2** | game-state feature construction from ML-1 records: time remaining in seconds, score differential, possession/field-position encodings, pace (plays or points per elapsed minute), observed feed cadence statistics (the evidence the three deferred preregistration items need) | ML-1 has ≥ 2 slate weeks of complete records and the smoke run has settled which OPTIONAL fields exist |
| **ML-3** | baseline live model — expected final total (first, per the preregistration's market order) and, second, win probability; fitted on point-in-time features, versioned, evaluated against the de-vigged live market as the null | ML-2's coverage report clears the multi-book guard for the chosen family |
| **ML-4** | live market comparison + candidate generation at the frozen thresholds; consensus/de-vig from intact quotes | ML-3 beats or matches the market's calibration on held-out weeks |
| **ML-5** | circuit breakers (section 11 of the preregistration) + shadow positions with thesis-based dedup; still no output anywhere | ML-4 produces candidates whose refusal reasons are fully recorded |
| **ML-6** | prospective shadow grading + the evaluation report the promotion gate requires | ML-5 has ≥ 100 independent positions |
| **ML-7** | private D.J. Mercer Discord beta (Mode 2) — explanation over records only | the promotion review passes |
| **ML-8** | interactive Discord application / slash commands | ML-7 sustains use and adds nothing to the decision chain |

Any package may be the last one. "No play" is the expected steady state.


## 17. Measured provider behaviour (first live captures, 2026-09-21/22)

ESPN only, no Odds API call, no credits spent. Two runs against NYG at LA:
run 35673309148 took five observations a minute apart starting 00:46:30Z, and
run 35682762374 took six observations ninety seconds apart starting 03:20:21Z.
Both wrote to a runner temp directory, committed nothing and posted nothing.

**What the feed supplies.** While `status_state == "in"`: status, both scores,
period, clock in both forms, possession resolved to home or away, down,
distance, yard line, red-zone flag, both timeout counts, and the last play with
its type, text and score value. Once `status_state == "post"` every field
sourced from `situation` is absent, which is correct rather than a gap. Never
supplied at all, on any observation: `downDistanceText`, `possessionText`,
ESPN's win probability, any drive object, any play timestamp, and any
payload-level or event-level provider timestamp. `provider_timestamp` is
therefore `null` on every game_state record, as the schema already required.

**Two zero-value traps, both now confirmed.** Scores read `null` before kickoff
because ML-1 refuses ESPN's `"0"` for an unplayed game. `period` and `clock` get
no such treatment: ESPN sent `period 0` with a `0:00` clock before kickoff and
`period 4` with a `0:00` clock after the final, and a `0:00` clock is also a
real, meaningful value at the end of a period in progress. Those fields are
recorded verbatim and must be gated on `status_state` by whoever reads them.

**A stale payload is indistinguishable from a fresh one within a single
observation, and this was observed rather than theorised.** At 03:20:21Z the
scoreboard returned a fully formed in-progress payload reading period 1, 0:11,
0-7, last play an extra point. The game had kicked off at 00:47Z and was in
fact over: ninety seconds later, and on five consecutive observations after
that, the same game read FINAL 6-28. The first payload was roughly two hours
out of date, contained no malformed or missing field, and carried no provider
timestamp to expose it.

ML-1 recorded it faithfully, which is the correct behaviour for a capture
boundary: the observation says what ESPN said at the instant Open Ledger asked,
and the append-only series preserves the jump for a later consumer to detect.
Nothing was smoothed, corrected or dropped. The consequence is a REQUIREMENT
written into `MERCER_LIVE_V0.1_PREREGISTRATION.md` section 11 before any model
exists: the stale-state guard must catch a payload that jumps BACKWARDS, not
only a feed that stops moving. A single observation cannot carry that judgement
on its own, so it is a property of the series, and the series is exactly what
this package stores.
