# NCAA historical data — what a college model would need

Written 2026-09-14. **Planning only. Nothing here has been acquired, bought or
fetched, and nothing may be without Daniel's approval.** Until this data exists
and a model built on it has been tested, the DJ Mercer NCAA Developmental Cohort
is human-researched only and no NCAA model may be claimed.

Prices, limits and coverage below come from the provider documentation and
experience at the time of writing. Every figure marked **(verify)** must be
confirmed against the provider's current pricing page before any purchase.

## 1. What exists today

Nothing historical. The repository holds 2026 live NCAAF moneyline captures
(`data/football/odds/ncaaf_*.json`, 11 books, no per-market timestamps) and 2026
ESPN results (`data/football/ncaaf_results.json`). There are no historical games,
scores, play-by-play, efficiency, odds, team identity table, returning
production, recruiting or transfer data.

## 2. Required datasets and the point-in-time rule for each

A value may be used for game G only if it was knowable at G's decision time
(T−24). Each dataset needs an **availability clock**, the same contract
`data/football/column_availability.json` gives the NFL.

| dataset | needed for | point-in-time requirement | risk |
|---|---|---|---|
| Games and final scores (FBS + FCS opponents), 2014–2025 | labels, Elo-style ratings | result usable only after `kickoff + 4h` | FCS opponents and neutral sites need explicit handling |
| Play-by-play with EPA / success / explosiveness | efficiency ratings | game stats usable only after `kickoff + 36h`; season aggregates rebuilt game by game, never read as season totals | garbage-time filter must be frozen before scoring |
| Opponent-adjusted efficiency (e.g. SP+, FPI) | priors and comparison | **only preseason editions** are point-in-time; end-of-season ratings leak the whole season | providers overwrite values; archived preseason snapshots are required |
| Returning production | preseason priors | usable from its publication date each summer | published once, sometimes revised; store the fetch date |
| Recruiting / roster talent composite | preseason priors | usable from signing-period publication | revisions; store the fetch date |
| Transfer portal entries | roster change | usable only up to each entry's date; the historical as-of view is often unavailable | **may not be reconstructable point-in-time; likely excluded** |
| Quarterback starter / depth | QB continuity | actual starters are postgame; pregame depth charts are rarely archived | **likely excluded, as for the NFL** |
| Coaching changes | continuity | dated announcements | low volume; can be curated by hand |
| Venue, travel, rest | schedule features | schedule-only fields, safe | neutral-site flag needed |
| Historical odds at T−24 and near close (h2h, spreads, totals) | market anchor, evaluation, CLV | timestamped snapshots only; untimestamped consensus lines may never feed a model | college books are thin before ~2021 |
| Team identity table (ESPN id ↔ data-provider id ↔ Odds API name) | every join | stable ids, dated renames (e.g. Sam Houston) | conference realignment 2024; FCS name variants |

## 3. Potential sources

| source | provides | cost | notes |
|---|---|---|---|
| **CollegeFootballData.com API** (CFBD) | games, scores, play-by-play with EPA, advanced game stats, returning production, recruiting, talent, portal, SP+ and other ratings, venues, a betting-lines endpoint | free API key with a monthly call limit; paid supporter tiers raise it **(verify)** | the betting-lines endpoint has **no quote timestamps** — reference only, never a model input. Ratings endpoints may return current values: preseason snapshots must be archived at fetch time |
| **cfbfastR / its data releases** | pre-built play-by-play with EPA/WPA by season | free | derived from the same public feeds; fix the release version and hash each season file, as `pbp_manifest.json` does for nflverse |
| **ESPN scoreboard / summary** | schedules, finals, ESPN event ids | free, unofficial | already used in production for identity and grading |
| **The Odds API historical endpoint** (`/v4/historical/sports/americanfootball_ncaaf/odds`) | timestamped snapshots of every event at a chosen time | **10 credits per market per region per snapshot** (this repository's NFL pull cost 30 per snapshot for h2h,spreads,totals) **(verify)**; history from mid-2020 **(verify)** | one snapshot returns every listed event at that instant, so cost scales with distinct kickoff hours, not games |
| Archived preseason ratings (Wayback Machine snapshots of public rating pages) | point-in-time preseason SP+/FPI | free | patchy; only if the provider's API cannot serve historical preseason editions |

## 4. Expected cost

**Data providers (cash):** CFBD free tier may be enough for a one-time season
backfill if calls are batched by season and week; a supporter tier for one or two
months is the likely worst case **(verify amounts)**. cfbfastR and ESPN are free.

**Historical odds (credits).** A college season has roughly 15 regular-season
weeks with about 15 distinct kickoff hours per week, so about 225 T−24 snapshots
plus 225 near-close snapshots per season:

| scope | per season | 2020–2024 (5 seasons) | plus 2025 as a holdout |
|---|---|---|---|
| h2h, spreads, totals (30 credits/snapshot) | ~13,500 | ~67,500 | ~81,000 |
| h2h only (10 credits/snapshot) | ~4,500 | ~22,500 | ~27,000 |

These are planning estimates from the schedule shape, not quotes; a `--plan`
dry run (as `fetch_historical_odds.py` does for the NFL) must count exact slots
first. The Odds API plan currently in use is the 100,000-credit monthly tier
recorded in `CLAUDE.md`; allowances do not carry over, so a pull larger than one
month's headroom must be split across billing months or needs a temporary upgrade.
**No incremental cash cost if it fits existing headroom; otherwise a plan change
Daniel must approve.** Monthly usage today is small (about 200–300 credits
recorded this cycle), but Mercer Live, if ever scheduled, is budgeted at ~30,000/month.

## 5. Ingestion work

| step | work | notes |
|---|---|---|
| 1 | Preregistration for an NCAA research study, frozen **before** any data is loaded: seasons, split, features, target, power | mirrors `FOOTBALL_PREREG_NFL_REPRESENTATION_V1.md`; add it to `asof.SPECS` so the holdout lock knows it |
| 2 | Team identity table and alias discipline | ESPN ids as the spine; FCS teams included; dated renames; no fuzzy matching |
| 3 | Games ingest with availability clocks and column classification | `ncaaf_games.csv` + `ncaaf_column_availability.json`; an unclassified column fails the run |
| 4 | Play-by-play ingest, hashed manifest, frozen garbage-time rule | gitignore raw season files like `data/football/raw/pbp/` |
| 5 | Game-level efficiency aggregation, walk-forward only | reuse `asof.walk` semantics (result clock and stats clock) |
| 6 | Preseason priors with fetch dates | returning production, recruiting, talent; portal likely excluded |
| 7 | Historical odds pull with `--plan`, `--probe`, credit floor and resumability | a college variant of `fetch_historical_odds.py`, booking credits through `scripts/odds_credits.py` |
| 8 | Holdout lock for a college holdout season | `asof.claim_holdout` extended per sport |
| 9 | Evaluation harness against the market anchor | the `scripts/mercer_dev/nfl_v1_eval.py` shape; untouched test run once |

Rough effort: steps 1–6 several focused sessions; step 7 one session plus a
monitored pull; steps 8–9 one or two sessions. The binding constraint is
discipline around point-in-time availability, not code volume.

## 6. What approval is needed, in order

1. Approve (or decline) an NCAA research track at all.
2. Approve the preregistration before any data is loaded.
3. Approve a CFBD key and any supporter tier (Daniel creates the account; the key
   goes in `.env.local` or a repository secret, never chat).
4. Approve the historical odds credit budget after a `--plan` count.
5. Only then: ingest, freeze, evaluate once, and report whatever it shows.
