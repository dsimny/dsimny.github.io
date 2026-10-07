# NBA research preregistration v0.1

Status: Package 1 contracts frozen; predictive experiment NOT ACTIVATED.
No NBA predictions, training, holdout inspection or bets have been performed.

## Locked initial policy

ID nba-policy-v0.1. SHADOW_MODE=true, official publication=false. Per-game schedule
phase chooses rules; PRESEASON_MODE is only a safety ceiling. All candidates carry
NOT AN OFFICIAL PLAY. No A confidence before calibration. No force-pick promotion.
Regular spread floor 1.5 points, total floor 2.5 points, EV floor 0.03; preseason
3.0, 5.0 and 0.06. Both floors are required (ML has EV only). Provisional sanity
ceilings: model/no-vig disagreement 0.15 probability, EV 0.25, spread disagreement
8 points, total disagreement 15 points. Reaching a ceiling gives PASS_REVIEW, not
an exception. These are conservative research guards, not empirically optimal values.

Hard failure -> PASS. Otherwise pending explicit condition -> WATCH. Otherwise both
screens -> PLAY, one -> LEAN, neither -> PASS. Missing model validation blocks official
qualification but not clearly marked research screen classification. Quarter Kelly
is research only; exposure caps 0.5% position, 1% game/shared injury, 3% slate. Fixed
1-unit risk is the performance record, never Kelly-scaled performance rescue.

Preseason has zero default weight in regular-season efficiency training. Availability
probabilities without empirical status history remain explicit research assumptions.
10,000+ seeded simulations and discrete pushes/OT are required for later model packages.

## Evaluation contract and activation conditions

The primary unit is one game x market x preregistered decision window, not each
revision or each bookmaker. T-90 is the proposed primary window; a later official
release window is a separate stratum. Preserve all scheduled games and exclusion
reasons; no missing-game or losing-game deletion. Cluster uncertainty by game/date.
Market null benchmark uses contemporaneous canonical no-vig probabilities. Closing
market is later diagnostic only. Pure and market-aware producers are isolated.

Activation requires a signed, hashed data-inventory supplement stating available
seasons, observation cutoffs, timestamp evidence, training/validation/untouched final
periods, feature set, residual model, hyperparameter search budget, sample/power rule,
paired score test and success thresholds per market. Those parameters cannot honestly
be frozen before source coverage is known; unresolved fields block activation rather
than permit a silent choice after results. Football holdouts are not NBA holdouts.

The supplement is written before any final-period access or model selection. Tune
only inside past training/validation windows. Final data can be consumed once under
the declared test. A failed hypothesis stays failed; a new hypothesis needs a new
version and unused evidence. Probability/interval calibration, executable-price ROI
uncertainty, missing close coverage and CLV must all be reported. A positive point
estimate of ROI does not authorize production.

## Amendments

Record old/new version, date, rationale, files, expected effect, validation results
and whether evidence was previously observed. Never relabel past predictions. All
16 revised readiness requirements are independent promotion gates, including source
licensing/retention terms. Paid-source calls remain disabled until budget and source
coverage are verified.
