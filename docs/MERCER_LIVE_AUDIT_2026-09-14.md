# Mercer Live — repository audit before ML-1 (2026-09-14)

Stage 1 of the Mercer Live v0.1 build. READ-ONLY: nothing in the repository was
edited while this was written. It records what already exists, what can be
reused, what is frozen, and the risks a live-capture boundary has to be built
around. The preregistration (`MERCER_LIVE_V0.1_PREREGISTRATION.md`) and the
ML-1 architecture (`MERCER_LIVE_ML1_ARCHITECTURE.md`) both cite this file
rather than restating it.

Two limits on this audit, stated first:

- **No live endpoint was reachable from the audit environment** (egress to
  `site.api.espn.com` and `the-odds-api.com` was refused by policy). Every
  provider claim below is sourced from code and captures already in the
  repository, or from the OLP adapter's documented live findings, and is marked
  VERIFIED-IN-REPO or UNVERIFIED accordingly. ML-1 ships a read-only smoke
  workflow so the UNVERIFIED items can be checked against the real feeds
  without deploying anything.
- The audit environment runs Python 3.11; CI runs 3.12. One pre-existing suite
  (`scripts/selftest_closing.py`) fails to import here on a 3.12-only f-string
  in `odds_page.py`. That is an interpreter mismatch, not a defect, and it is
  outside Mercer Live's scope.

---

## 1. Current football architecture

Three products share the football surface and are deliberately separate:

| layer | what it is | code | record |
|---|---|---|---|
| Open Ledger Model (football) | market-derived pregame selection, fp-v0.4, **PAUSED** since 2026-09-13 (`delivery_policy.PAUSED`) | `scripts/football/{fetch_odds,capture_schedule,market,board,grade_football,grade_committed,settle,page,discord}.py` | `data/football/football_ledger.json`, `commitments.json`, `game_commitments.json`, `board_*.enc/.json` |
| D.J. Mercer Spotlight | human-researched weekly picks under a pen name | `scripts/football/mercer.py` | `data/mercer/mercer_ledger.json`, `data/mercer/commitments.json`, `data/mercer/weeks/` |
| Open Ledger Play (OLP) | a separate paper-ticket product on PostgreSQL/Supabase with a hosted runner on Fly | `open-ledger-play/` (excluded from Pages by `_config.yml`) | Postgres tables, never in this repo's `data/` |

Pipeline shape (football): `football-capture.yml` (hourly, cron-job.org primary +
GitHub cron backstop) → `capture_schedule.py` decides whether a T-24 or closing
window is open → `fetch_odds.py` pulls one board per sport → captures are
committed FIRST → board half (currently refuses to write while paused) → pages →
Discord (paused). `football-grade.yml` (daily) refreshes ESPN results, grades
committed positions, reveals, re-renders. `football-data-daily.yml` is the
read-only monitor. `football-selftest.yml` is the hermetic code gate running
15 suites by name.

The whole chassis is **market-derived and pregame**: every decision moment is a
kickoff-relative window, and nothing in it models a game in progress.

## 2. NFL / NCAAF event identity

Two identity modes, both already solved and both reusable:

- **NFL — canonical.** `teams.canonical(abbr, source="espn")` and
  `teams.from_name(full_name, source="odds-api")` both resolve to one of 32
  franchise keys, raise `UnknownTeam` on anything else, and never fuzzy-match.
  `data/football/team_names.json` is generated from ESPN, not typed. ESPN
  results and Odds API captures therefore meet at the franchise key.
- **NCAAF — verbatim + normalised join.** Captures store the Odds API's raw
  strings and claim no canonical key. `espn_ncaaf.norm()` (NFKD, strip marks,
  alphanumerics, casefold) plus one explained alias (`espn_ncaaf.ALIASES`)
  joins them to ESPN `displayName`; measured 16/16 on 2026-08-29. Exact match
  after normalisation, never approximate.
- **Event-level identity** is provider-native on each side (`odds_api_event_id`,
  `espn_event_id`). There is no shared canonical event id today; the grader
  joins by sport + normalised matchup + kickoff, refuses ambiguity
  (`market.AmbiguousEvent`), and one adjudication file
  (`data/football/result_identity/`) exists because ESPN reported 03:59Z and the
  Odds API 04:00Z for the same fixture. **A canonical live event id must not
  embed the exact kickoff instant.**
- Slate week: `market.slate_week` is UTC-anchored (frozen; ledger keys depend on
  it); `mercer.slate_week` is ET-anchored so Monday night stays with its week.
  For a live product the ET anchor is the correct one and it is what ML-1 uses
  for its canonical id, with a drift test against `mercer.slate_week`.

## 3. Current Odds API integration

- Endpoint: `GET /v4/sports/{sport}/odds`, `regions=us`, `oddsFormat=american`.
  Sport keys: `americanfootball_nfl`, `americanfootball_ncaaf`,
  `americanfootball_nfl_preseason` (`fetch_odds.SPORT_KEYS`).
- Cost model VERIFIED-IN-REPO from headers: **markets × regions per call**,
  independent of event count. Football captures h2h only (1 credit); MLB
  captures h2h,totals (2 credits). Balance as at 2026-09-13T22:30Z: 99,799 of
  100,000 remaining this calendar month (`data/odds_credits.json`, 60-reading
  rolling window).
- Accounting: `scripts/odds_credits.py` is the ONE writer of
  `data/odds_credits.json`; `selftest_odds_credits.py` pins the four existing
  callers' behaviour. A new caller must go through `odds_credits.record()`. A
  per-minute caller booking every reading would evict the operational readings
  from the 60-slot window within an hour — the same hazard
  `fetch_historical_odds.py` already solved by booking one reading per batch.
- Key handling: `localenv.require("ODDS_API_KEY")`, `.env.local` gitignored,
  fingerprint-only logging. Reusable as-is.

## 4. Bookmaker identity / canonicalisation

Bookmaker identity is the Odds API `bookmakers[].key` string, kept verbatim.
Tiers are pre-declared in `price_test.py` (`TIER1` US-regulated, `TIER2`
offshore) and re-exported by `market.py`; a book in neither tier is refused by
the selection rule. Tier lists are frozen by pre-registration and may not be
re-cut. ML-1 stores book keys verbatim and does not classify; tiering is a
decision-layer concern (ML-4/5) and inherits these lists.

## 5. Market schemas

The capture schema (`fetch_odds.normalise`) keeps every book:
`books[].markets.{h2h,spreads,totals}[] = {name, price, point}`, plus
`books[].last_update` and, since 2026-09-13, `books[].market_last_update.{key}`.
Settlement sign convention is fixed in `settle.py`: `line` is the number added
to the selected team's score; totals sides are `over`/`under`. OLP's schema is
the same three markets (`MONEYLINE/SPREAD/TOTAL`) with `line` mandatory for
spread/total and forbidden for moneyline. ML-1 adopts the same conventions.

## 6. Snapshot architecture

One JSON file per capture per sport, named by capture second
(`nfl_YYYYMMDDTHHMMSSZ.json`), never overwritten (a same-second collision is
refused). Directory IS provenance: `data/football/odds/` is the model's
evidence trail, `data/mercer/odds/` is research. Every snapshot declares
`tier`, `identity`, `capture_role`, `markets`, `regions`, `credits`.
Grading derives T-24 and closing from what is on disk. **116 files on disk as
of this audit.**

## 7. Capture timestamps

`captured_utc` is stamped **before** the request is sent (`fetch_odds`), and it
is the moment Open Ledger observed the data. OLP made the same distinction the
hard way: its first live ingest used the feed's `last_update` as `captured_at`
and every quote arrived already stale (documented in
`open-ledger-play/ingest/providers/the_odds_api.py`). Both are consistent with
`asof.py`'s `event_time / available_at / ingested_at` split.

## 8. Provider timestamps

- Odds API: `bookmakers[].last_update` (when the book last moved) and
  `markets[].last_update` (per market), both RFC3339 Z. Retained verbatim in
  captures. Measured on the one in-play sample in the repo
  (`nfl_20260913T183723Z.json`, 8 events in progress): book ages at capture
  were 16–81 s for live events, 16–78 s for pregame ones.
- ESPN scoreboard: **no payload-level or event-level "last updated" timestamp is
  used anywhere in the repo**, and none is known to exist on the public
  scoreboard endpoint. ML-1 records `provider_timestamp: null` for
  game state rather than inventing one.
  **VERIFIED 2026-09-21/22** on eleven live observations: no such timestamp is
  supplied, and its absence has a measured consequence. A payload roughly two
  hours out of date arrived looking entirely fresh, with nothing in it to say
  so. See `MERCER_LIVE_ML1_ARCHITECTURE.md` section 17.

## 9. Grading mechanisms

`grade_committed.py` settles only committed positions at their committed
prices, checks board and game hashes before any ledger write, joins results by
sport + normalised matchup + kickoff, fails closed on ambiguity, refuses a
close at or after kickoff ("an in-play price is never a close"), and keeps
stakes at 0. `settle.py` is pure. `mercer.py grade` mirrors the pattern on its
own ledger. All of it is pregame and untouched by ML-1.

## 10. Frozen / immutable boundaries (do not modify)

| boundary | source of freeze |
|---|---|
| fb-v0.1, fb-v0.2 preregistrations and results; the 2025 holdout (unspent) | `FOOTBALL_PREREG*.md`, `asof.SPECS` |
| fp-v0.1..v0.4 selection rule; `market.py` arithmetic ("legacy, for faithful settlement/replay") | `FOOTBALL_PIPELINE.md`, `FOOTBALL_RESET_2026-09-11.md` |
| Football containment: `delivery_policy.PAUSED = True`; `record_policy.INVALIDATED_BOARD_SHA256` | `FOOTBALL_CONTAINMENT_2026-09-13.md` |
| `reset_2026-09-11.json` frozen hashes + append-only baseline | checked live by `selftest_capture_isolation.py` |
| Mercer Spotlight record, filter v1.0 | `MERCER_SPOTLIGHT.md` |
| OLP packages pkg2/pkg3 (frozen), pkg4/pkg5 contracts | `open-ledger-play/*.md` |
| `data/mercer/odds/` every file must be `capture_role: research` and carry h2h | `selftest_capture_isolation.py` [6] — **so Mercer Live may not write there** |
| Push epilogue byte-identity, one-path-per-`git add`, no `--autostash`, no bare push outside two known sites, `index.html`/`feed.xml` staged only by three workflows | `selftest_workflows.py`, `selftest_push_pattern.py`, `selftest_staging.py` |
| Ops alerts only to `DISCORD_WEBHOOK_URL_ALERTS` | `selftest_instagram.py` 22.5, scans every workflow |

## 11. Discord webhook architecture

`scripts/post_discord.py` (MLB; modes pick/board/recap/blog/alert; NOT
idempotent except alert) and `scripts/football/discord.py` (football; idempotent
per slate week via `post_status.json`; currently refused by `delivery_policy`).
Alert mode: ALERTS webhook only, host-validated, swallows every exception.
There is no interactive bot except the dark pick'em Worker
(`worker/pickem/`). **ML-1 imports none of this and posts nothing.**

## 12. Scheduled execution

- GitHub `schedule:` is measured 15–23 min late and once produced zero runs in
  9.5 h; production jobs are `workflow_dispatch` fired by cron-job.org.
- The repo's finest production cadence is hourly (`football-capture`) and a
  few captures a day (`capture-closing`).
- OLP already runs a **hosted 5-minute cadence**: cron-job.org POSTs to
  `v01_service.py` on Fly (`fly.toml`, auto-stop machine, bearer token, API key
  only in the process environment). That is the closest existing precedent for
  a sub-hourly scheduler and its design notes (retries off, DB owns leases,
  one poll per cycle) transfer directly.

## 13. Append-only integrity checks

`record_policy.baseline_digest/check_append_only` (deletion, mutation,
back-dated insertion), commitment fingerprints (`commitments.json`,
`game_commitments.json`, `data/mercer/commitments.json`), grader refusals on
hash mismatch, OLP's `BEFORE UPDATE OR DELETE` triggers and `ingest_seq`. The
pattern to inherit: content hashes + monotonic sequence, never wall-clock as
the only order.

## 14. CI / self-test patterns

- Hermetic suites use `check(cond, msg)` printing `ok`/`FAIL`, or `unittest`.
  Fixtures are synthetic; several negative controls prove a guard still bites.
- The gate is split by WHAT A RED X MEANS: code regression vs live-data drift,
  in different workflows. A new subsystem must get its own gate rather than be
  appended to `football-selftest.yml`'s hard-coded `SUITES` list.
- Repository-wide contract scans that WILL evaluate any new workflow:
  `selftest_workflows.py` [9] (no `--autostash`, no `git pull`, every
  `git push` site enumerated, staged sets exact), [10] concurrency groups,
  `selftest_push_pattern.py`, `selftest_staging.py`, `selftest_instagram.py`
  22.5 (alert webhooks). A read-only workflow with no `git add`/`push` and no
  alert step passes all of them by construction.

## 15. Reusable football game-state ingestion

`espn_slate.py` (NFL preseason plumbing test) already tracks status
transitions `STATUS_SCHEDULED → IN_PROGRESS → FINAL` over repeated captures and
warns about ESPN's `"0"` score on unplayed games. `espn_nfl.py` / `espn_ncaaf.py`
read `status.type.name`, `season.type`, competitors' `score`, `abbreviation`,
`displayName`, `team.id`. None reads clock, period, possession, down/distance or
`situation`. So: the ESPN fetch, date ranging, team identity, season-type
allowlist and the "score is None unless final" rule are reusable; live-state
parsing does not exist and is ML-1's job.

## 16. Is ESPN or another football source integrated?

Yes — ESPN's public scoreboard (no key) for both sports, as results source and
for kickoff discovery (`capture_schedule.kickoffs`). nflverse for historical
games/pbp (research only). The Odds API for prices. No other live source.

## 17. Join protections today

| risk | protection |
|---|---|
| mismatched games | exact join on identity keys; `AmbiguousEvent` on >1 hit; `market_input.event_at` also requires the provider event id AND unchanged commence_time |
| duplicate events | dedupe by (away, home, commence_time) in `board.py`; refusal on repeated fixture identity in `market_input` |
| stale odds | `STALENESS_MIN = 15` vs snapshot time; `market_input` requires 0 ≤ age ≤ 900 s using the per-market timestamp and rejects captures lacking it |
| wrong team mapping | `UnknownTeam` aborts the whole NFL capture; NCAAF normalised-exact join; aliases explained one by one |
| in-play price used as pregame | capture time vs kickoff per game: `pick_snapshots` uses captures strictly before kickoff; grader refuses close ≥ kickoff; `market_input` refuses `captured >= kick` |

## 18. Does existing code assume all football odds are pregame?

**Yes, implicitly, and it is safe only because of per-game capture-time
guards.** `fetch_odds.normalise` labels no quote as pregame or live. The
scheduled Sunday capture at 2026-09-13T18:37:23Z contains eight in-progress
events (one with zero books) beside six pregame ones, in the model's evidence
directory, indistinguishable in the file. Nothing downstream is corrupted
because every consumer filters by capture time relative to each game's kickoff,
but the label does not exist in the data. ML-1 must (a) never write into
`data/football/odds/` or `data/mercer/odds/`, and (b) label every quote's
phase explicitly, with the basis for the label.

---

## Reusable components (import read-only, never modify)

`teams.canonical/from_name`, `espn_ncaaf.norm/key_for`, `fetch_odds.SPORT_KEYS`
(and the ESPN path/group constants, restated with a drift test),
`odds_credits.record`, `localenv.require/fingerprint`, `settle.py` conventions,
the `check()` self-test idiom, the read-only workflow shape of
`football-data-daily.yml`.

## Identified risks for a live capture boundary

1. **Contamination of pregame evidence** by live quotes — solved by directory
   isolation plus explicit phase labels (`selftest_capture_isolation` already
   polices the two existing directories).
2. **Credit burn** — 1-minute polling of both sports at three markets is
   roughly 30,000 credits/month (section "Cost" in the architecture doc), about
   30% of the allowance and 100× today's football spend. Needs a floor, a
   daily cap, and per-call accounting.
3. **Evicting the shared credit ledger** — book at most one reading per hour
   per sport into `odds_credits.json`; keep per-call readings in Mercer Live's
   own run records.
4. **Repository bloat** — measured: one NFL Sunday at one-minute cadence
   compresses to ~4 MB; a college Saturday ~4× that; a season is hundreds of
   MB. Raw observations cannot be committed. Fingerprints can.
5. **Kickoff drift between sources** (03:59Z vs 04:00Z incident) — canonical id
   excludes the exact instant; the join tolerates minutes and refuses hours.
6. **ESPN live fields unverified** — record what is present, list what is
   missing per record, never default a missing field to a plausible value.
7. **GitHub Actions as a per-minute scheduler** — rejected by measured lateness
   and by the commit-per-tick it would imply.
8. **Product illusion** — no Discord, no site surface, no units, no "watch"
   anywhere in ML-1; copy bars remain (`edge`, `+EV`, `the model likes`).

## Likely data sources

| need | source | status |
|---|---|---|
| live score / clock / period / status | ESPN scoreboard (free) | **VERIFIED LIVE 2026-09-21/22** |
| possession / down / distance / field position / timeouts / last play | ESPN scoreboard `competitions[].situation` | **VERIFIED LIVE 2026-09-21/22 and 2026-09-24/25** while in progress, including the text forms and ESPN's win probability; absent once final, which is correct. See `MERCER_LIVE_ML1_ARCHITECTURE.md` section 18 |
| live moneyline / spread / total quotes per book | The Odds API `/odds`, markets `h2h,spreads,totals` | in-play events present in repo captures (VERIFIED-IN-REPO for h2h); spreads/totals live coverage UNVERIFIED |
| scores as a cross-check | The Odds API `/scores` | FUTURE, not used |
| ESPN win probability | `situation.lastPlay.probability` | must never be Open Ledger's model; may be recorded as a reference only |

## Does the provider supply what ML-1 needs?

For markets, yes: the Odds API returns in-play quotes for commenced events
(seen in repo data), with per-book and per-market timestamps, at a flat
markets × regions cost. What it does NOT supply is an explicit live flag, so
phase must be derived (game state when joined, else `commence_time` vs
`observed_at`), and it does not guarantee all books quote all three markets
live (one live event returned zero books). For game state, ESPN supplies the
universal fields with certainty and the football-specific ones probably; ML-1
treats the latter as optional and measures their presence.

## Recommended storage architecture

Not PostgreSQL: the football pipeline's canonical persistence is append-only
JSON under `data/`, and the only Postgres in the repo belongs to a different
product with its own frozen contracts. ML-1 therefore uses **append-only JSONL
shards, one record per line, deterministic `observation_id`, under
`data/mercer_live/raw/` (gitignored), with committed daily digests carrying the
SHA-256 of every shard, row counts, and credit usage** — the same split the
repo already uses for `data/football/raw/pbp/` + `pbp_manifest.json`. The
store is behind one small module so a Postgres backend (a NEW `mercer_live`
schema, never OLP's tables) can be added later if volume or querying demands
it, without changing record semantics.
