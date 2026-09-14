# D.J. Mercer Developmental Cohorts — modeling protocol, evaluation, proposed v1

Written 2026-09-14; section 7 updated the same day for Daniel's owner
decisions. **Nothing here is registered or effective yet, and nothing here
authorizes publication.** Registration binds `data/mercer_dev/registrations/*.json`.

Two cohorts, never combined:

| strategy id | name |
|---|---|
| `mercer-nfl-dev` v1 | DJ Mercer NFL — Developmental Cohort v1 |
| `mercer-ncaaf-dev` v1 | DJ Mercer NCAA — Developmental Cohort v1 |

Related: `FOOTBALL_INCIDENT_ROOT_CAUSE_2026-09-14.md`,
`MERCER_DEVELOPMENTAL_RELEASE_CONTROLS.md`, `FOOTBALL_PREGAME_RELEASE_GATES_V1.md`,
`MERCER_SPOTLIGHT.md`.

---

## 1. What already exists and binds this work

- **fb-v0.1** (frozen 2026-08-20): a market-blind NFL model (Elo + opponent-
  adjusted EPA ridge) lost to the T−24 market 9 of 9 season-markets on 2022–24
  and added no incremental information (`FOOTBALL_RESULT_T24.md`).
- **fb-v0.2** (frozen 2026-08-24): best available price vs closing fair value,
  EV −1.05%, negative CLV (`FOOTBALL_RESULT_PRICE.md`).
- **fp-v0.4** (2026-09-11): launch product rule, no forecasting claim; its
  week-2026-09-08 board is the invalidated incident.
- **nfl-representation-v1** (frozen 2026-09-14, research only, not scored): a
  separate richer-feature NFL study that reserves 2015–21 for development,
  2022–24 as a labelled comparison, and **does not read 2025**.
- **Release gates v1**: the strategy gate is BLOCKED for both sports; "do not
  open the restricted 2025 holdout".
- **D.J. Mercer Spotlight**: human-researched picks with a frozen Research
  Filter v1.0, a required Case Against, a commit gate, premium withholding and
  its own append-only record. Its member delivery was active during the pause.

This protocol does not reopen any closed study. The owner's 2026-09-14 request
authorizes a *developmental* publication track for Mercer that is explicitly
not "proven". It does not relax the rule that an unvalidated strategy may not be
described as validated, and it leaves the release-gates document in force for
the automated pipeline.

## 2. Chronological split

Random splits are not used anywhere. All rating state is produced by
`asof.walk`, which applies a game's result only once
`result_available_at_utc <= T−24` of the game being predicted.

### NFL

| period | seasons | use | status |
|---|---|---|---|
| burn-in | 2010–2014 | rating state only | results + play-by-play |
| tuning | 2015–2021 | fb-v0.1 Elo/ridge parameters (frozen, reused, not re-tuned) | no timestamped odds exist |
| **train** | 2022–2023 | market blend weight and selection rule | Tier A T−24 + close odds; **previously scored** |
| **validation** | 2024 | confirm the train choice | Tier A odds; **previously scored** |
| untouched test | 2025 | **NOT RUN** | odds are on disk (gitignored); results are in `games.csv`; restricted holdout |
| live prospective | 2026 from effective date | the only record that counts | captures + grading |

**2022–2024 are contaminated.** fb-v0.1, fb-v0.2 and fp-v0.4 all scored them.
Nothing computed on them is described as out-of-sample or untouched.

**2025 is the only untouched NFL history, and it is not spent here.** Section 6
explains why the recommendation is to leave it for the representation study.

### NCAA

No historical college data exists in the repository: no games, scores,
play-by-play, efficiency, odds, team identity table, returning production,
recruiting or transfer data. **A train/validation/test split is not possible
without acquiring data.** NCAA v1 can only be prospective from its effective
date (section 5).

## 3. Leakage controls and what cannot be made point-in-time

Enforced in code for the NFL evaluation (`scripts/mercer_dev/nfl_v1_eval.py`):

| risk | control |
|---|---|
| final scores | ratings update only through `asof.walk` at result-availability time |
| full-season or end-of-season ratings | ratings are the walk state at each game's own T−24; `final_ratings` in the fit files are never read |
| closing information as a feature | closing consensus is used **only** for CLV; never enters `p_model` or selection |
| market movement after selection | only the T−24 snapshot feeds selection |
| restricted season | `asof.load_games` refuses 2025 without a holdout claim, and the script skips any odds snapshot requested on or after 2025-03-01 |
| postgame columns | `column_availability.json` tags QB, temp, wind, referee as postgame; they are not read |

Known limitations, documented rather than hidden:

- `ridge.py` updates EPA on the result clock (kickoff+4h) instead of the stats
  clock (kickoff+36h) the prereg specifies. Candidate B inherits this. It makes
  candidate B marginally *optimistic*, and B still adds nothing.
- Historical book `last_update` is per book, not per market.
- The 2022 Tier-1 book set includes books that no longer operate.

### NFL candidate inputs — availability

| input | point-in-time status | v1 |
|---|---|---|
| off/def EPA per play, success, pass/rush efficiency, explosive, pace | play-by-play 2010–24 on disk; frozen in nfl-representation-v1 (unscored) | EPA via candidate B only; the richer families belong to that study |
| opponent adjustment, strength of schedule | Elo and ridge | tested (A, B) |
| home field | Elo `hfa` (frozen) | tested |
| rest / short week | `away_rest`/`home_rest` are schedule columns (safe) | not tested — no evidence it adds to the market; v2 candidate |
| travel | derivable from stadium, not built | no |
| QB value / expected starter | `*_qb_id` are the actual starters (postgame); no historical depth charts | **excluded — leakage** |
| injuries | no historical point-in-time reports | **excluded**; live ESPN injury feed used only by the human research filter |
| OL / DL indicators | no reliable source | excluded |
| weather | `temp`/`wind` are observed (postgame); no historical forecasts | **excluded — leakage** |
| turnover / red-zone regression | play-by-play derivable | not built |
| market moneyline | Tier A T−24 2022–24 | anchor |
| market spread / total | Tier A 2022–24; live capture is moneyline only | not in the model; see section 7 |
| market movement before selection | only T−24 and close slots exist historically | excluded |

### NCAA candidate inputs — availability

Every requested input (efficiency, opponent adjustment, SOS, conference
strength, QB continuity, returning production, transfers, recruiting/talent,
pace, explosives, garbage-time filtering, program home field, travel, rest,
market spread/total/moneyline history, liquidity) is **unavailable
historically** in the repository. Live 2026 NCAAF captures are moneyline-only,
with 11 books and no per-market timestamps.

## 4. NFL v1 candidate specification and results

```
p_model = sigmoid( logit(p_market) + w · (logit(p_rating) − logit(p_market)) )
```

- `p_market`: median-price, proportionally de-vigged Tier-1 consensus at T−24,
  ≥5 eligible books, quotes ≤15 min old (the fb-v0.2 maths, reused).
- `p_rating`: A = frozen fb-v0.1 Elo; B = frozen fb-v0.1 game model (Elo + EPA ridge).
- `w` chosen on **train** log loss from {0, .05, .10, .15, .20, .30, .50}.
- Selection rule **fixed before any result was computed**: moneyline;
  edge = p_model − implied(best Tier-1 price) ≥ 2.0 pts; |p_model − p_market| ≤
  4 pts else HOLD; consensus fair 25–75%; price −300..+300; ≥2 books at best;
  ≤3 plays per week; never both sides of a game.

Reproduce: `python scripts/mercer_dev/nfl_v1_eval.py` →
`data/mercer_dev/research/nfl_v1_dev_eval.json`.

### Probability layer (regular season, pushes excluded)

| split | n | market LL | market Brier | Elo-only LL | game-model LL | chosen (A, w=0) LL |
|---|---|---|---|---|---|---|
| train 2022–23 | 541 | **0.61901** | 0.21518 | 0.65040 | 0.64974 | 0.61901 |
| validation 2024 | 257 | **0.59171** | 0.20174 | 0.60743 | 0.60950 | 0.59171 |

Train log loss rises monotonically with w for both candidates
(A: 0.61901 at w=0 → 0.63017 at w=0.5). **The best weight on the model is
exactly zero.** Accuracy: market 66.5% / 73.2%; Elo 61.6% / 67.3%.

### Selection layer

| rule | train plays | validation plays |
|---|---|---|
| chosen model (w=0) | **0** | **0** |
| market-only baseline | 0 | 0 |
| random-side machinery check (50 games) | 25–24–1, ROI −8.0%, CLV −0.60 | 25–25, ROI −10.1%, CLV −1.69 |

Why zero: the edge reachable at the best Tier-1 price, over every side inside
the odds window:

| split | sides | median | p90 | p99 | max | share ≥ 2 pts |
|---|---|---|---|---|---|---|
| train | 888 | −0.63 | +0.37 | +1.30 | +2.47 | 0.23% |
| validation | 438 | −1.31 | −0.32 | +0.54 | +0.96 | **0.00%** |

Sensitivity (train only, **not used to choose anything**): a 1.0-pt threshold
selects 3 plays in two seasons (1–2).

Against-the-spread, totals, moneyline return, flat-stake ROI, drawdown, losing
streak, confidence and edge-bucket tables are **not reportable for v1 because
the registered rule selects no plays**. fb-v0.1's spread and total head-to-head
results (market better in every season) remain the reference.

### Reading

This is the fb-v0.1 answer again, from a different angle. A market-anchored
model built from these inputs reduces to the market, and the market's own
number, even shopped across books, does not clear a positive edge. **No
automated NFL model-edge strategy is supportable on this evidence.**

## 5. NCAA v1

Nothing can be trained, validated or tested. Options, for Daniel:

1. **Prospective human track (recommended for v1)** — the same Mercer process
   and guardrails as NFL, registered before publication, reported entirely
   separately, with no model claim.
2. **Shadow only** — record selections privately for a season, publish nothing.
3. **Acquire data first** — historical college games/play-by-play (e.g. College
   Football Data API, free key Daniel would create) and historical NCAAF odds
   (Odds API; would need the budget proposal the feasibility audit requires).
   A college model could then follow the NFL protocol. Not before 2027 for
   meaningful validation.

## 6. The 2025 NFL holdout — recommendation: do not spend it here

Running the untouched test would evaluate a w=0 model, i.e. the market against
itself, with a rule that selected nothing in 800 development games. It cannot
change the conclusion, and it would permanently consume the only untouched NFL
season that `nfl-representation-v1` may later need. **Recommendation: leave
2025 unclaimed.** If Daniel wants it claimed anyway, that is a one-time,
irreversible decision recorded through `asof.claim_holdout()`.

## 7. v1 design — owner decisions of 2026-09-14 (supersedes the earlier proposal)

The registrations in `data/mercer_dev/registrations/` are the binding text once
registered; this section summarises them.

- **Human-researched, not a model.** Both cohorts are D.J. Mercer's human research
  process supported by market and analytical data. No model selects, ranks or sizes
  plays; no model edge is claimed. The recorded probability is the market reference.
- **NFL:** historical model research may continue separately. Section 4's
  evaluation found no independent edge, and its output does not select plays.
- **NCAA:** human-researched only. No NCAA model exists or may be claimed until
  point-in-time historical data is acquired and tested (`NCAA_DATA_ACQUISITION_PLAN.md`).
- **Spotlight:** stays the weekly premium featured selection. From a cohort's
  effective date, a Spotlight pick in that sport is a cohort play and is counted
  on that cohort's record only (`MERCER_DEVELOPMENTAL_RELEASE_CONTROLS.md` §2).
- **Prospective only.** No backfill.
- **Staking:** flat 0.25 units per play; at most 1 unit per ET day across all
  developmental plays in both cohorts, released-but-not-started plays included.
- **Approval:** Daniel approves the exact immutable artifact of every play.
- **Guardrails** unchanged from the proposal: pregame only, ≥30 minutes before
  start, moneyline −250..+250, spread/total prices −130..+115, spread magnitude
  ≤10.5 (NFL) / ≤17.5 (NCAA), taken price ≤3.0 pts worse than consensus, ≥5
  fresh Tier-1 books and ≥3 at the number, recorded walk-away boundary, no live plays.
- **2025 NFL holdout:** unspent.
- **Publication:** off until commissioning is complete.

**What makes a new version:** any change to selection source, features, model
use, market blending, eligibility, edge or divergence thresholds, markets, sizing,
exposure, approval or data-quality rules. Ratings and rolling inputs updating is
not a new version.

**Record:** each cohort's ledger starts empty at its effective date. Historical
development results (section 4) are shown separately from the live record and
labelled.
