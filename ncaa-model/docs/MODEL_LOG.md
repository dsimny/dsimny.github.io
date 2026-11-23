# Model log

Every version, every result, including the rejected ones. Newest first.
Full tables for ae-0.1: `reports/backtest_ae-0.1.md` (tune + validation),
`reports/holdout_ae-0.1.md` (holdout + current season), `reports/tuning_grid.csv`.

## ae-0.2 — roster/transfer/recruit priors + four-factor matchup layer (2026-11-23)

Full tables: `reports/phase3_tune.md` (every variant tried, tune 2018–22),
`reports/phase3_validation.md` (validation 2023–25 + current season, with
named-book closing lines), `reports/holdout_ae-0.2.md` (single holdout run).

**What changed.** Two things, nothing else (recency, home edge, possessions
unchanged):

1. *Preseason prior* (`ncaa/priors.py`). ae-0.1 started every team at last
   season's rating with one 3-game prior. ae-0.2 fits a linear preseason
   model on the seasons before s: last season and two seasons back,
   conference-mates' mean (partial pooling), returning share of last
   season's minutes and its interaction with last season's rating, incoming
   transfers' minutes and the strength of the teams they left, and the
   recruiting class. Prior strength per team = k·σ²_obs / σ²_prior, with
   σ²_prior from the preseason model's error (wider for rebuilt rosters) and
   k = 0.5 chosen on tune seasons. Roster data: CBBD via the workflow, one
   derived team-level table committed (`data/derived/team_priors.csv`).
2. *Four-factor layer* (`ncaa/fourfactors.py`). Opponent-adjusted
   eFG / TO / OR / FT rate / 3PA share / 3P% / 2P% / FT% before every game;
   a ridge regression of the rating model's residual on the matchup
   features, fitted on 2018–22.

**Results (walk-forward, D1 vs D1; margin RMSE unless noted)**

| | ae-0.1 | + priors | ae-0.2 (+ FF layer) |
|---|---|---|---|
| tune 2018–22 | 11.498 | 11.390 | 11.325 (FF in-sample) |
| validation 2023–25 | 11.508 | 11.367 | **11.296** |
| validation, ≤2 games played | 12.793 | 12.196 | **12.145** |
| **holdout 2026** | 11.724 | 11.593 | **11.505** |
| holdout 2026, ≤5 games played | 12.876 | 12.394 | **12.292** |
| holdout 2026 Brier | 0.181 | 0.179 | **0.178** |
| 2027 to date (n=945) | 13.274 | 12.543 | **12.494** |

Validation and holdout agree: ~0.2 points of RMSE overall and ~0.6 in the
first games of the season, which is where ae-0.1 was weakest. Every block
helped on tune seasons; returning minutes is the largest single gain.
Calibration on validation is within ±0.025 in every decile.

**Against the market (simulated, no wagers)**

| | c beyond the close (±SE) | model RMSE vs line RMSE | ATS at −110 | ROI (95% CI) |
|---|---|---|---|---|
| ESPN BET 2023–25, ae-0.1 | 0.093 ± 0.033 | 11.800 vs 11.466 | 50.5% | −3.6% (−5.3 to −1.8) |
| ESPN BET 2023–25, ae-0.2 | 0.281 ± 0.041 | 11.610 vs 11.466 | 51.7% | −1.3% (−3.2 to +0.5) |
| **DraftKings 2026 holdout, ae-0.1** | 0.010 ± 0.080 | 11.559 vs 11.310 | 50.1% | −4.3% (−7.7 to −0.7) |
| **DraftKings 2026 holdout, ae-0.2** | 0.204 ± 0.093 | 11.446 vs 11.310 | 52.0% | −0.7% (−4.2 to +3.1) |
| DraftKings 2027 to date, ae-0.2 | 0.147 ± 0.117 | 12.536 vs 12.035 | 49.6% | −5.2% (−12.3 to +2.1) |

Honest headline:

1. **ae-0.2 is a clearly better basketball model** and the gain held up on
   the holdout.
2. **It still does not beat the closing line.** The line alone is more
   accurate than the model alone on every named book, and simulated ATS at
   −110 is still slightly negative with a CI that spans zero.
3. **It now carries some information the close does not**: c rose from ≈0 to
   0.20–0.28 on both named books, about 2–7 SE. That is the first sign of
   incremental value against a real close. A blend fit on 2023–24 and scored
   on 2025 improved RMSE by only 0.018 points (11.436 → 11.418), so it is small.
4. **CLV vs the DraftKings open rose from +0.25 to +0.42 points** on the
   holdout (the model's side, line movement open → close). This is still the
   Phase 5 lead, and now a larger one. It needs real timestamped opening
   prices to test as a bet.

**Rejected / not done**

- Refitting last season's slope alone (P0): worse than ae-0.1. Needed two
  seasons of history plus conference pooling to catch up.
- End-of-season form (60-day recency) as an extra lag feature: no gain.
- k = 1 (textbook Normal-Normal strength) and k ≥ 0.75: worse; k = 0.5 won
  for every variant. Probably in-season drift plus imperfect rosters.
- **Coaching changes: not built.** CBBD has no coaches endpoint (404) and no
  free source was found. Not faked with a proxy.
- The four-factor layer's alpha was seen once on validation before the
  tune-only check confirmed it; alpha 10/100/1000 differed by ≤ 0.001.
  Disclosed in `reports/phase3_tune.md`.
- CBBD's 2026 DraftKings lines have almost no early-season games (4 with
  ≤5 games played), so the holdout cannot test the early-season market edge;
  ESPN BET 2023–25 early games (n=2,526): c = 0.29 ± 0.07, ATS 52.7%, ROI
  +0.6% (CI −3.2% to +4.8%) — not significant.

**Caveat.** CBBD rosters are not timestamped (see `docs/DATA_SOURCES.md`);
minutes and production are always last season's, but roster membership is
"as published". The current season's roster is genuinely preseason.

## ae-0.1 addendum — CBBD lines (2026-11-23)

Same model, no re-tuning. CollegeBasketballData lines were pulled inside the
read-only `NCAA model` workflow (key in the CBBD_API_KEY secret). Full table:
`reports/cbbd_lines.md`.

- **The 2023-24 / 2024-25 gap is filled, and the answer is unchanged.**
  Against ESPN BET lines the line wins both seasons: RMSE 11.51 vs the model's
  11.76 (2024), 11.44 vs 11.85 (2025). Simulated ATS was −0.9% and −5.4%. A blend
  fit on 2024 and scored on 2025 improved RMSE by 0.008 (11.436 → 11.428),
  which is too small to matter.
- **The stale-line finding holds up on an independent source.** Using CBBD's own
  copy of each provider, `consensus` gives c = 0.35–0.53 in every season
  but one. Named books are near zero: ESPN BET 0.03–0.19, DraftKings
  0.01 (2026) and −0.11 (2027). `teamrankings` and `numberfire` sit
  in between (they aren't books). Bovada 2026 (0.28) also looks like an early
  or stale capture.
- **Rule from here on:** a market test only counts against a named regulated
  book's close (DraftKings, ESPN BET, Caesars). Everything else is
  reported, never used to claim an edge.

## ae-0.1 — opponent-adjusted efficiency + tempo (2026-11-23)

**Model.** Per-possession offence/defence ratings and an additive tempo
rating, fitted daily by penalised least squares on the season's games before
the prediction date. Each team is pulled toward a prior equal to its previous
season's final rating (Gaussian prior, strength 3 games-equivalent; tempo
regressed 10%). Recency half-life 60 days. Fixed home edge measured on the
previous season's conference games (≈3 pts). Win probability = Φ(margin/σ),
σ by games played (12.7 → 11.0). Possessions = FGA − OREB + TO + 0.475·FTA.

**Splits** (fixed before any result was seen): tune 2016–2022 · validation
2023–2025 · holdout 2026 (run once) · current season 2027 forward.

### Results (D1 vs D1, walk-forward)

| split | n | model MAE | model Brier | Elo Brier | home-baseline Brier | market MAE (same games) | model MAE (same games) |
|---|---|---|---|---|---|---|---|
| tune 2016–22 | 36,848 | 8.98 | 0.178 | 0.184 | 0.231 | 8.79 | 8.99 |
| validation 2023–25 | 17,196 | 9.08 | 0.182 | 0.187 | 0.231 | 8.93 (2023 only) | 9.08 |
| **holdout 2026** | 5,752 | 9.27 | 0.181 | 0.187 | 0.232 | **8.86 (DraftKings)** | **9.07** |
| 2027 to date (Nov) | 945 | 10.57 | 0.155 | 0.164 | 0.199 | 9.57 | 10.57 |

Calibration on tune/validation is good: every decile within ~0.03 of observed.
Holdout calibration is a bit loose in the middle (the 0.4–0.5 bucket won 51%).

### The honest headline

1. **The model clearly beats the simple baselines.** It's better than Elo and
   the home-team rule on every split, holdout included.
2. **It does NOT beat the closing line.** On the 2026 holdout the DraftKings
   closing spread has an MAE of 8.86 against the model's 9.07, and the blend
   fitted on tune seasons is WORSE than the market alone out of sample
   (RMSE 11.199 vs 11.167). Simulated ATS at the close: 49.8%, ROI −4.9%
   (95% CI −7.9% to −1.5%). 2027 to date, same story.
3. **The validation result that looked like an edge was an artifact of the
   line source.** On 2023 the blend beat the "market" (RMSE 11.25 vs 11.33),
   and the model's coefficient beyond the line was c = 0.33 ± 0.02. Split by
   provider on the tune seasons: c = **0.41** for ESPN's `consensus` lines but
   only **0.11 ± 0.04** for Caesars. A model that "beats" a line source by
   that much more than it beats a real book is beating a stale or non-closing
   line, not the market. On DraftKings in the holdout the advantage was gone.
   **Lesson recorded:** never trust a market test against ESPN `consensus`
   lines. Use a named book with a known close.
4. **One signal worth following up (not an edge):** against the DraftKings
   OPENING line, the model's side gained +0.33 pts of closing-line value on
   average (beat the close 39%, line moved against it 27%, no move 34%).
   Simulated ROI at the open was still −2.4% (CI −5.4% to +0.7%). The model
   holds some information the opener lacks and the close has absorbed. That
   is the target for Phase 5, and it needs real timestamped opening prices.

### Rejected / changed during ae-0.1 development

- **Free home-court parameter → rejected.** It soaked up the strength gap in
  early-season "buy games" (strong host, weak visitor) and inflated the home
  edge to ~4.7 pts, biasing home projections by about −2.3 pts out of sample.
  Replaced with a fixed edge measured on the prior season's conference games
  (balanced schedules). Margin RMSE on 2017 went from 11.85 to 11.46.
- **Uncentred ratings → fixed.** Ratings are only defined up to a shift, so
  offence and defence are now centred on the D1 average.
- **Grid edge.** The 27-config grid picked prior_regress = 0.8 at the top of the
  grid. Extended on tune seasons only: 0.9 → RMSE 11.420, 1.0 → 11.415.
  Adopted 1.0 (carry last season in full, let the 3-game prior do the
  shrinking). The differences are small (<0.01 RMSE).
- **"Games where blend ≠ line by ≥1.5 pts" filter.** This was added during
  validation, so it was not pre-declared. It looked good on 2023 (55.7%,
  ROI +6.2%, CI −1.0% to +13.4%) and failed on the holdout (51.8%, ROI −1.6%).
  It's reported only as a cautionary example.

### Known weaknesses (inputs to Phase 3)

- Early season: Nov MAE is ~10.2 vs ~8.8 in Feb, with a +1 pt bias (pred too
  small in buy games). Priors carry no roster info. Transfers and returning
  minutes are the obvious next prior.
- There's no blowout dampening, so AdjEM can be inflated early (Illinois +44.7 on
  2026-11-23 after 6 games).
- On neutral sites ESPN's "home" team outperforms by ~1 pt. Some "neutral"
  games are semi-home; worth a parameter.
- Totals: MAE 13.7 vs the market's 13.3. Tempo has the same prior structure
  and no matchup terms.
