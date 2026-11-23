# Phase 3: preseason priors + four-factor matchup layer

Walk-forward. Every prediction for date d uses only games before d; the preseason model for season s is fitted on seasons before s. Prior shrinkage k and the feature blocks were chosen on tune seasons 2018–22; the four-factor layer is fitted on 2018–22 residuals. Validation 2023–25 and the current season were not used for any choice. The 2026 holdout is not in this report.

## Accuracy (D1 vs D1)

| split | model | n | margin_rmse | margin_mae | su_acc | brier | log_loss | total_mae |
|---|---|---|---|---|---|---|---|---|
| tune 2018–22 (in-sample for the FF layer) | ae-0.1 (Phase 1) | 25901 | 11.498 | 9.060 | 0.727 | 0.179 | 0.532 | 13.589 |
| tune 2018–22 (in-sample for the FF layer) | + roster/transfer/recruit priors | 25901 | 11.390 | 8.953 | 0.730 | 0.178 | 0.527 | 13.481 |
| tune 2018–22 (in-sample for the FF layer) | + priors + four-factor matchup layer | 25901 | 11.325 | 8.899 | 0.731 | 0.177 | 0.524 | 13.307 |
| validation 2023–25 | ae-0.1 (Phase 1) | 17196 | 11.508 | 9.083 | 0.717 | 0.182 | 0.538 | 13.238 |
| validation 2023–25 | + roster/transfer/recruit priors | 17196 | 11.367 | 8.989 | 0.721 | 0.181 | 0.534 | 13.157 |
| validation 2023–25 | + priors + four-factor matchup layer | 17196 | 11.296 | 8.933 | 0.724 | 0.180 | 0.532 | 13.020 |
| current 2027 to date | ae-0.1 (Phase 1) | 945 | 13.274 | 10.573 | 0.759 | 0.155 | 0.463 | 14.211 |
| current 2027 to date | + roster/transfer/recruit priors | 945 | 12.543 | 10.061 | 0.765 | 0.147 | 0.438 | 13.778 |
| current 2027 to date | + priors + four-factor matchup layer | 945 | 12.494 | 10.026 | 0.769 | 0.146 | 0.435 | 13.658 |

## Validation 2023–25 by point in the season (margin RMSE)

| when | n | RMSE ae01 | RMSE prior | RMSE ff |
|---|---|---|---|---|
| 0–2 games played | 1465 | 12.793 | 12.196 | 12.145 |
| 3–5 games played | 1623 | 12.153 | 11.804 | 11.747 |
| rest of Nov–Dec | 3508 | 11.740 | 11.542 | 11.457 |
| Jan–Apr | 10600 | 11.135 | 11.119 | 11.048 |

### By season

| season | n | RMSE ae01 | RMSE prior | RMSE ff |
|---|---|---|---|---|
| 2023 | 5714 | 11.451 | 11.297 | 11.245 |
| 2024 | 5723 | 11.478 | 11.348 | 11.263 |
| 2025 | 5759 | 11.593 | 11.455 | 11.380 |

### Calibration, validation, full model

| range | n | predicted | observed | obs_95ci |
|---|---|---|---|---|
| 0.0-0.1 | 165 | 0.067 | 0.073 | ±0.040 |
| 0.1-0.2 | 461 | 0.158 | 0.137 | ±0.031 |
| 0.2-0.3 | 949 | 0.253 | 0.263 | ±0.028 |
| 0.3-0.4 | 1345 | 0.352 | 0.375 | ±0.026 |
| 0.4-0.5 | 1965 | 0.453 | 0.460 | ±0.022 |
| 0.5-0.6 | 2447 | 0.552 | 0.562 | ±0.020 |
| 0.6-0.7 | 2691 | 0.650 | 0.661 | ±0.018 |
| 0.7-0.8 | 2619 | 0.749 | 0.761 | ±0.016 |
| 0.8-0.9 | 2324 | 0.849 | 0.864 | ±0.014 |
| 0.9-1.0 | 2230 | 0.950 | 0.957 | ±0.008 |

## Against named-book closing lines (CBBD; simulated, no wagers)

`c` = weight on (model − line) in margin = a + b·line + c·(model − line), fit on that cell. c ≈ 0 means no information beyond the line. ATS assumes −110 (CBBD carries no spread prices) and bets the side with positive EV on every game.

| split | model | n | clv_vs_open | line_rmse | model_rmse | c | c_se | ats | ats_pct | roi | roi_ci |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 ESPN BET | ae-0.1 (Phase 1) | 4664 | — | 11.455 | 11.798 | 0.072 | 0.056 | 1812-1814-2 | 0.500 | -0.046 | -7.6% to -1.6% |
| 2023 ESPN BET | + roster/transfer/recruit priors | 4664 | — | 11.455 | 11.644 | 0.163 | 0.070 | 1780-1750-2 | 0.504 | -0.037 | -6.7% to -0.5% |
| 2023 ESPN BET | + priors + four-factor matchup layer | 4664 | — | 11.455 | 11.586 | 0.296 | 0.068 | 1827-1713-2 | 0.516 | -0.015 | -4.5% to +1.3% |
| 2024 ESPN BET | ae-0.1 (Phase 1) | 4396 | — | 11.508 | 11.757 | 0.185 | 0.057 | 1829-1695-0 | 0.519 | -0.009 | -3.8% to +2.4% |
| 2024 ESPN BET | + roster/transfer/recruit priors | 4396 | — | 11.508 | 11.672 | 0.266 | 0.073 | 1707-1626-0 | 0.512 | -0.022 | -5.4% to +0.9% |
| 2024 ESPN BET | + priors + four-factor matchup layer | 4396 | — | 11.508 | 11.589 | 0.394 | 0.071 | 1771-1623-0 | 0.522 | -0.004 | -3.6% to +2.8% |
| 2025 ESPN BET | ae-0.1 (Phase 1) | 4453 | — | 11.436 | 11.845 | 0.029 | 0.057 | 1729-1759-0 | 0.496 | -0.054 | -8.5% to -2.3% |
| 2025 ESPN BET | + roster/transfer/recruit priors | 4453 | — | 11.436 | 11.730 | 0.073 | 0.072 | 1710-1716-0 | 0.499 | -0.047 | -7.8% to -1.5% |
| 2025 ESPN BET | + priors + four-factor matchup layer | 4453 | — | 11.436 | 11.657 | 0.161 | 0.074 | 1718-1634-0 | 0.513 | -0.022 | -5.3% to +1.2% |
| 2027 Draft Kings | ae-0.1 (Phase 1) | 817 | 0.310 | 12.035 | 13.376 | -0.105 | 0.086 | 343-370-0 | 0.481 | -0.082 | -15.1% to -0.7% |
| 2027 Draft Kings | + roster/transfer/recruit priors | 817 | 0.325 | 12.035 | 12.606 | 0.117 | 0.118 | 336-356-0 | 0.486 | -0.073 | -14.2% to -0.1% |
| 2027 Draft Kings | + priors + four-factor matchup layer | 817 | 0.330 | 12.035 | 12.536 | 0.147 | 0.117 | 348-353-0 | 0.496 | -0.052 | -12.3% to +2.1% |
| 2023–25 ESPN BET pooled | ae-0.1 (Phase 1) | 13513 | — | 11.466 | 11.800 | 0.093 | 0.033 | 5370-5268-2 | 0.505 | -0.036 | -5.3% to -1.8% |
| 2023–25 ESPN BET pooled | + roster/transfer/recruit priors | 13513 | — | 11.466 | 11.682 | 0.163 | 0.041 | 5197-5092-2 | 0.505 | -0.036 | -5.3% to -1.6% |
| 2023–25 ESPN BET pooled | + priors + four-factor matchup layer | 13513 | — | 11.466 | 11.610 | 0.281 | 0.041 | 5316-4970-2 | 0.517 | -0.013 | -3.2% to +0.5% |

### Blend fit on 2023–24 ESPN BET, scored on 2025

| model | c_train | rmse_line | rmse_blend |
|---|---|---|---|
| ae-0.1 (Phase 1) | 0.1252 | 11.4358 | 11.4192 |
| + roster/transfer/recruit priors | 0.2074 | 11.4358 | 11.4192 |
| + priors + four-factor matchup layer | 0.3359 | 11.4358 | 11.4175 |

### Early season only (≤5 games played), 2023–25 ESPN BET

| split | model | n | clv_vs_open | line_rmse | model_rmse | c | c_se | ats | ats_pct | roi | roi_ci |
|---|---|---|---|---|---|---|---|---|---|---|---|
| early 2023–25 | ae-0.1 (Phase 1) | 2526 | — | 11.983 | 12.769 | 0.119 | 0.050 | 1165-1044-2 | 0.527 | 0.007 | -3.5% to +4.6% |
| early 2023–25 | + roster/transfer/recruit priors | 2526 | — | 11.983 | 12.321 | 0.237 | 0.073 | 1099-971-2 | 0.531 | 0.014 | -2.5% to +5.2% |
| early 2023–25 | + priors + four-factor matchup layer | 2526 | — | 11.983 | 12.274 | 0.286 | 0.072 | 1117-1002-2 | 0.527 | 0.006 | -3.2% to +4.8% |

