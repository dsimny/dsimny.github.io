# Holdout 2025–26 — ae-0.2 candidate (single run)

Frozen before this run: prior blocks, k = 0.5, four-factor layer (alpha 100, fit on 2018–22). Nothing below was used to choose anything. All betting figures simulated; no wagers.

| split | model | n | margin_rmse | margin_mae | su_acc | brier | log_loss | total_mae |
|---|---|---|---|---|---|---|---|---|
| holdout 2026 | ae-0.1 (Phase 1) | 5752 | 11.724 | 9.273 | 0.713 | 0.181 | 0.530 | 13.904 |
| holdout 2026 | + roster/transfer/recruit priors | 5752 | 11.593 | 9.154 | 0.719 | 0.179 | 0.527 | 13.850 |
| holdout 2026 | + priors + four-factor matchup layer | 5752 | 11.505 | 9.100 | 0.723 | 0.178 | 0.524 | 13.639 |

Early season (≤5 games played):

| split | model | n | margin_rmse | margin_mae | su_acc | brier | log_loss | total_mae |
|---|---|---|---|---|---|---|---|---|
| holdout 2026, early | ae-0.1 (Phase 1) | 1026 | 12.876 | 10.176 | 0.779 | 0.142 | 0.429 | 14.853 |
| holdout 2026, early | + roster/transfer/recruit priors | 1026 | 12.394 | 9.812 | 0.798 | 0.137 | 0.418 | 14.702 |
| holdout 2026, early | + priors + four-factor matchup layer | 1026 | 12.292 | 9.729 | 0.805 | 0.136 | 0.414 | 14.438 |

## Against the DraftKings close (CBBD)

| split | model | n | clv_vs_open | line_rmse | model_rmse | c | c_se | ats | ats_pct | roi | roi_ci |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026 DraftKings | ae-0.1 (Phase 1) | 3534 | 0.248 | 11.310 | 11.559 | 0.010 | 0.080 | 1354-1346-0 | 0.501 | -0.043 | -7.7% to -0.7% |
| 2026 DraftKings | + roster/transfer/recruit priors | 3534 | 0.266 | 11.310 | 11.520 | 0.120 | 0.090 | 1387-1302-0 | 0.516 | -0.015 | -4.9% to +2.0% |
| 2026 DraftKings | + priors + four-factor matchup layer | 3534 | 0.415 | 11.310 | 11.446 | 0.204 | 0.093 | 1323-1221-0 | 0.520 | -0.007 | -4.2% to +3.1% |

| split | model | n | clv_vs_open | line_rmse | model_rmse | c | c_se | ats | ats_pct | roi | roi_ci |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026 DraftKings, early | ae-0.1 (Phase 1) | 4 | — | 7.730 | 7.845 | 0.655 | 3.083 | 1-2-0 | 0.333 | -0.364 | -100.0% to +90.9% |
| 2026 DraftKings, early | + roster/transfer/recruit priors | 4 | — | 7.730 | 8.345 | -0.443 | 4.415 | 3-1-0 | 0.750 | 0.432 | -52.3% to +90.9% |
| 2026 DraftKings, early | + priors + four-factor matchup layer | 4 | — | 7.730 | 8.159 | -0.224 | 4.393 | 1-1-0 | 0.500 | -0.045 | -100.0% to +90.9% |

