# NFL representation study — executable preregistration v1

STATUS: FROZEN. Research only. No recommendation or release authority.
frozen: 2026-09-14

This document supersedes the unfrozen methodology in `FOOTBALL_PREREG_V03.md`.
That draft remains in history and must not be edited into a scored specification.
This version resolves its selection/evaluation conflict before any new feature is
aggregated or scored. The freeze evidence records the first commit containing this
file and its exact SHA-256 in `data/football/research_preregistrations.json`.

## Question and scope

NFL regular-season, full-game pregame forecasting only. The falsifiable question:
does a richer, point-in-time-safe representation of the same nflverse plays used
by fb-v0.1 improve NFL moneyline probability forecasts over fb-v0.1?

This is not an NCAA study. It does not use prices as predictive inputs, choose a
bet, estimate an executable edge, stake units, publish to Discord, change fp-v0.4,
or authorize spending. A null closes this six-family representation experiment.
No seventh family may be added after scoring begins.

## Data boundaries

- 2010–2014: burn-in only; never scored.
- 2015–2021: development and all family/model selection.
- 2022–2024: one historical comparison after the family set is locked. These
  seasons have already been examined for prior models and are not called untouched.
- 2025: restricted holdout; neither read nor acquired by this study.
- 2026 onward: future prospective evidence under a separate preregistration.

Only the 2010–2024 source bytes matching `data/football/pbp_manifest.json` may be
used. Every derived value resolves at the game's `stats_available_at_utc`
(kickoff + 36 hours in the existing availability contract). A prediction for game
G may use only prior games whose `stats_available_at_utc <= G.t_minus_24_utc`.
Thursday may inform Sunday when that inequality holds; Sunday early cannot inform
Sunday late. All preprocessing fits only on the training side of each time split.

## Frozen play and drive definitions

All numeric parsing is finite-only. Blank, nonnumeric and nonfinite values are
missing. Binary flags accept only numeric 0 or 1. Eligible play rows require a
nonblank `game_id`, `posteam`, `defteam`, `season_type == REG`, and finite `epa`.
Kneels and spikes are excluded. Penalty rows remain when nflverse supplies a
finite EPA and a pass/rush flag; no retrospective penalty interpretation is added.

A **dropback** is `pass == 1`. nflverse's pass flag includes sacks and scrambles.
A **rush** is `rush == 1 AND pass != 1`, making the groups disjoint. Rows in
neither group do not enter play-rate features. For each team-game:

1. Pass/rush EPA: mean EPA per dropback and mean EPA per rush.
2. Success: mean of the binary `success` flag in each play group. Missing success
   is excluded from its denominator and its count is retained.
3. Explosiveness: dropback with `yards_gained >= 15`; rush with
   `yards_gained >= 10`. Missing yards is excluded and counted.
4. Disruption: sack rate and QB-hit rate per dropback. Sack and QB-hit are separate
   flags and may overlap; missing flags are excluded from their own denominator.

A **drive** is the unique `(game_id, posteam, fixed_drive)` group. Missing drive
identity is excluded and counted. Each drive must have exactly one nonblank
`fixed_drive_result`; conflicting values invalidate that drive and are counted.
Drive categories are retained verbatim. Frozen scoring proxy:

| result | value |
|---|---:|
| Touchdown | +7 |
| Field goal | +3 |
| Safety | -2 |
| Opp touchdown | -7 |
| Punt, Turnover, Turnover on downs, End of half, Missed field goal | 0 |

An unknown result invalidates the drive. This is a fixed drive-value proxy, not
the exact scoreboard contribution: extra-point outcomes are unavailable from the
declared columns and must not be reconstructed from final scores. "Points per
drive" in the old draft is replaced by the accurate name **drive value per drive**.

A **red-zone trip** is a valid drive with at least one eligible offensive play at
`yardline_100 <= 20`. Red-zone features are scoring-trip rate (result Touchdown or
Field goal) and drive value per red-zone trip. Drives beginning inside the 20 count.
Repeated red-zone entries on the same drive count once. Overtime drives are retained.

Each team-game row records every numerator, denominator, missing count, invalid
drive count, and the source-file SHA. No zero denominator becomes zero: its feature
is null. The model adds a missingness indicator and imputes the median learned from
the training fold only. Continuous features are centered/scaled using training-fold
means and population standard deviations; zero-variance columns remain zero after
centering. Defence uses the opponent's same observed offensive outcome, never a
separately inferred label.

## Frozen model comparison

The baseline is the already recorded fb-v0.1 game model with its existing Elo,
ridge, key-number profile, feature clocks and T-24 prediction mechanics. Baseline
predictions are regenerated only after existing artifacts and source hashes verify.
They are not retuned.

Each family is tested alone by adding its opponent-adjusted offensive/defensive
rating difference to the baseline margin regression. Every scalar feature is fit
with the existing ridge mechanics: `lam=100`, 180-day half-life, team-game sample
count as weight, weekly refit at the earliest T-24, and training-only scaling.
For multi-feature families, all declared scalar features enter together; there is
no within-family search. Stage-two linear coefficients and residual spread are fit
only on observations earlier than the scored season. The existing discrete margin
PMF converts margin to home-win probability. No market number enters fitting.

## Chronological development and selection

Outer development seasons are 2018, 2019, 2020 and 2021. For outer season Y, all
ratings, scaling, imputation and stage-two coefficients use only games available
before Y. Score every eligible regular-season game in Y once. The paired primary
statistic is candidate minus baseline moneyline log loss by game; negative is
better. Ties use outcome 0.5 exactly as fb-v0.1.

For each family, calculate:

- overall mean paired difference across the four outer seasons;
- each season's mean paired difference;
- a one-sided cluster-bootstrap p-value for mean difference < 0.

Bootstrap exactly 10,000 draws with `random.Random(314159)`. Within each outer
season, resample its `(season, week)` clusters with replacement, preserving every
game in a selected cluster and drawing the original number of clusters. Combine
the four resampled seasons and calculate the game-weighted mean. The p-value is
`(1 + number of bootstrap means >= 0) / 10001`. Apply Holm's step-down correction
to the six p-values at family-wise alpha 0.10, ordered by `(p_value, family_name)`.

A family enters the combined model only if all four conditions hold:

1. overall mean paired log-loss difference <= -0.002;
2. each of the four season differences is strictly negative;
3. its Holm hypothesis is rejected;
4. no leakage, source-integrity or population-reconciliation check failed.

This membership is frozen before any 2022–2024 candidate score is computed. If
no family passes, stop and publish the null. If some pass, fit exactly one combined
model containing all and only passing families. It must improve over baseline on
development overall and in every outer season or the combined candidate fails.

Report log loss, Brier score, calibration intercept/slope, margin RMSE, eligible
and excluded counts, missingness, and every family result regardless of outcome.
The primary decision remains paired moneyline log loss. No ROI or CLV gate exists
in this market-blind development experiment.

## Historical comparison and decision boundary

After membership is frozen, run the baseline, each solo family, and combined model
once on 2022–2024, using chronological training only. Publish every result and mark
it **previously contaminated historical comparison**. Do not choose or modify the
model from those values. The old draft's 0.62592 gate is retired because using this
already-seen comparison to authorize purchasing and opening a holdout would give it
selection authority it cannot honestly bear.

No outcome from this study validates a customer recommendation. A future candidate
requires an independent market/execution rule and a preregistered prospective 2026+
sample with an effect size, power calculation, end date, complete PASS population,
and separate NFL release review. The 2025 holdout remains locked. Football delivery
remains paused. Changing this boundary requires a new version, never an edit here.

## Required implementation gates before scoring

- Freeze manifest matches this file's exact bytes and first commit.
- Pure synthetic tests pin every play/drive definition, missing and conflict case.
- Tests prove no 2025 read, no market/Discord/ledger import, and no output outside a
  research-only path.
- Source manifests, row populations and availability clocks verify before fitting.
- An independent review confirms the code implements this specification.

Only after these gates pass may development feature aggregation begin. Aggregation
and development scoring are separate commits. Any defect found after scoring starts
is documented; the frozen spec is not rewritten to accommodate the result.
