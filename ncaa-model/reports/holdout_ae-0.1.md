# Backtest report — holdout — model ae-0.1

Generated 2026-11-23 12:42 UTC. Walk-forward: each game predicted from ratings fitted only on games before its date. Hyper-parameters tuned on 2016–2022 only: `{"prior_games": 3.0, "prior_regress": 1.0, "tempo_prior_games": 3.0, "tempo_regress": 0.9, "half_life_days": 60.0, "hca_lambda": 10000.0, "nond1_off": -12.0, "nond1_dfn": 10.0, "newd1_off": -4.0, "newd1_dfn": 4.0}`. Win probability = Φ(margin / σ), σ by games played (tune seasons): {"0-2": 12.74, "3-5": 11.96, "6-10": 11.65, "11+": 11.03}. Market σ (closing spread residual, tune) = 11.23.

All betting figures are **simulated on recorded lines — no wagers were placed**. Closing lines come from ESPN's pickcenter (single book per game; consensus/Caesars before 2024, DraftKings from 2025-26); ESPN gives no capture timestamp for pre-2025 lines. No lines exist in the source for 2023-24 and 2024-25.

## HOLDOUT (single run) — seasons 2026–2026

### All D1-vs-D1 games (every model on the same games)
| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 5752 | 0.617 | 0.232 | 0.656 | 11.654 | 15.042 | — |
| Elo | 5752 | 0.710 | 0.187 | 0.550 | 9.706 | 12.384 | — |
| adj. efficiency ae-0.1 | 5752 | 0.713 | 0.181 | 0.530 | 9.273 | 11.724 | 13.904 |

### ae-0.1 by season
| season | n | mae | rmse | su_acc | brier | total_mae |
|---|---|---|---|---|---|---|
| 2026 | 5752 | 9.273 | 11.724 | 0.713 | 0.181 | 13.904 |

### ae-0.1 by month (early season = thin data)
| month | n | mae | bias | brier |
|---|---|---|---|---|
| Nov | 1245 | 10.192 | 0.830 | 0.148 |
| Dec | 963 | 9.370 | 0.044 | 0.152 |
| Jan | 1549 | 9.041 | -1.171 | 0.201 |
| Feb | 1369 | 8.805 | -0.346 | 0.200 |
| Mar | 613 | 8.881 | -0.672 | 0.197 |
| Apr | 13 | 9.696 | -1.587 | 0.207 |

### ae-0.1 by conference (conference games only, n ≥ 100)
| conf | n | mae | brier |
|---|---|---|---|
| MAAC | 139 | 8.285 | 0.194 |
| A-10 | 139 | 8.396 | 0.205 |
| CAA | 129 | 8.462 | 0.206 |
| MAC | 124 | 8.529 | 0.159 |
| Big West | 117 | 8.562 | 0.200 |
| ACC | 176 | 8.625 | 0.179 |
| Southland | 139 | 8.789 | 0.201 |
| Big East | 120 | 8.826 | 0.182 |
| CUSA | 129 | 8.829 | 0.229 |
| American | 126 | 8.907 | 0.211 |
| ASUN | 119 | 9.031 | 0.207 |
| WCC | 119 | 9.106 | 0.152 |
| Sun Belt | 139 | 9.126 | 0.231 |
| SWAC | 119 | 9.135 | 0.229 |
| Big 12 | 159 | 9.248 | 0.175 |
| MVC | 120 | 9.271 | 0.205 |
| Horizon | 120 | 9.287 | 0.245 |
| SEC | 159 | 9.294 | 0.208 |
| OVC | 117 | 9.333 | 0.211 |
| Mountain West | 131 | 9.334 | 0.166 |
| Big Ten | 197 | 9.561 | 0.178 |

### Calibration (home win probability)
| range | n | predicted | observed | obs_95ci |
|---|---|---|---|---|
| 0.0-0.1 | 80 | 0.059 | 0.050 | ±0.048 |
| 0.1-0.2 | 191 | 0.157 | 0.157 | ±0.052 |
| 0.2-0.3 | 303 | 0.253 | 0.294 | ±0.051 |
| 0.3-0.4 | 451 | 0.353 | 0.364 | ±0.044 |
| 0.4-0.5 | 603 | 0.450 | 0.511 | ±0.040 |
| 0.5-0.6 | 737 | 0.551 | 0.525 | ±0.036 |
| 0.6-0.7 | 768 | 0.650 | 0.647 | ±0.034 |
| 0.7-0.8 | 876 | 0.749 | 0.697 | ±0.030 |
| 0.8-0.9 | 810 | 0.849 | 0.835 | ±0.026 |
| 0.9-1.0 | 933 | 0.956 | 0.962 | ±0.012 |

### By prediction confidence
| conf_bucket | n | favourite_won | mean_conf |
|---|---|---|---|
| (0.499, 0.6] | 1340 | 0.509 | 0.551 |
| (0.6, 0.7] | 1219 | 0.643 | 0.649 |
| (0.7, 0.8] | 1179 | 0.700 | 0.749 |
| (0.8, 0.9] | 1001 | 0.836 | 0.848 |
| (0.9, 1.0] | 1013 | 0.962 | 0.955 |

### Against the market (4766 games with a real-book line; providers: DraftKings)

| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 4766 | 0.588 | 0.239 | 0.672 | 11.215 | 14.390 | — |
| Elo | 4766 | 0.695 | 0.195 | 0.569 | 9.405 | 11.938 | — |
| adj. efficiency ae-0.1 | 4766 | 0.698 | 0.189 | 0.552 | 9.065 | 11.447 | 13.720 |
| market (closing line) | 4766 | 0.709 | 0.184 | 0.541 | 8.863 | 11.167 | — |

Totals: ae-0.1 MAE 13.73 vs market total MAE 13.32 (n=4751).

**Incremental value test** (fit on tune-season lines, scored here): margin = a + 0.992·market + c·(model − market), c = 0.325 ± 0.020 (SE, train n=27648). Out of sample RMSE: market 11.167, blend 11.199, model alone 11.447.

By line provider (a 'line' that is not a true close would let the model look better than it is):

| provider | c fitted on tune lines of this provider (±SE) | n here | RMSE market | RMSE blend |
|---|---|---|---|---|

**Simulated ATS, market+model blend, closing line** (HYPOTHETICAL): 959-988-0 (49.3%) on 1947 bets, -97.4u, ROI -5.0% (95% CI -9.5% to -0.8%).
Of those, games where the blend differs from the line by ≥1.5 pts: 176-164-0 (51.8%), ROI -1.6% (95% CI -12.0% to +8.2%), n=340.

**Simulated ATS, closing line** (HYPOTHETICAL, 1u flat, rule: bet the positive-EV side; betting the CLOSE is the hardest possible test): 1808-1823-0 (49.8%), -176.3u, ROI -4.9% (95% CI -7.9% to -1.5%), max drawdown 208.3u; real prices on 100% of games, -110 assumed on the rest.

**Simulated ATS, OPENING line** (DraftKings open; HYPOTHETICAL): 1810-1729-0 (51.1%), -84.6u, ROI -2.4% (95% CI -5.4% to +0.7%). CLV on the model's side vs close: mean +0.33 pts, beat the close 39.3%, matched 33.6%, n=4766.

## Current season to date (forward, not a holdout) — seasons 2027–2027

### All D1-vs-D1 games (every model on the same games)
| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 945 | 0.741 | 0.199 | 0.589 | 14.610 | 18.782 | — |
| Elo | 945 | 0.754 | 0.164 | 0.492 | 11.847 | 15.166 | — |
| adj. efficiency ae-0.1 | 945 | 0.759 | 0.155 | 0.463 | 10.573 | 13.274 | 14.211 |

### ae-0.1 by season
| season | n | mae | rmse | su_acc | brier | total_mae |
|---|---|---|---|---|---|---|
| 2027 | 945 | 10.573 | 13.274 | 0.759 | 0.155 | 14.211 |

### ae-0.1 by month (early season = thin data)
| month | n | mae | bias | brier |
|---|---|---|---|---|
| Nov | 945 | 10.573 | 1.003 | 0.155 |

### ae-0.1 by conference (conference games only, n ≥ 100)
| conf | n | mae | brier |
|---|---|---|---|

### Calibration (home win probability)
| range | n | predicted | observed | obs_95ci |
|---|---|---|---|---|
| 0.0-0.1 | 9 | 0.072 | 0.444 | ±0.325 |
| 0.1-0.2 | 25 | 0.153 | 0.320 | ±0.183 |
| 0.2-0.3 | 42 | 0.254 | 0.333 | ±0.143 |
| 0.3-0.4 | 55 | 0.350 | 0.436 | ±0.131 |
| 0.4-0.5 | 68 | 0.456 | 0.559 | ±0.118 |
| 0.5-0.6 | 91 | 0.551 | 0.560 | ±0.102 |
| 0.6-0.7 | 96 | 0.648 | 0.604 | ±0.098 |
| 0.7-0.8 | 108 | 0.755 | 0.750 | ±0.082 |
| 0.8-0.9 | 131 | 0.852 | 0.817 | ±0.066 |
| 0.9-1.0 | 320 | 0.964 | 0.966 | ±0.020 |

### By prediction confidence
| conf_bucket | n | favourite_won | mean_conf |
|---|---|---|---|
| (0.499, 0.6] | 159 | 0.509 | 0.548 |
| (0.6, 0.7] | 151 | 0.589 | 0.649 |
| (0.7, 0.8] | 150 | 0.727 | 0.752 |
| (0.8, 0.9] | 156 | 0.795 | 0.851 |
| (0.9, 1.0] | 329 | 0.954 | 0.963 |

### Against the market (944 games with a real-book line; providers: DraftKings)

| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 944 | 0.740 | 0.199 | 0.589 | 14.605 | 18.782 | — |
| Elo | 944 | 0.754 | 0.164 | 0.493 | 11.848 | 15.170 | — |
| adj. efficiency ae-0.1 | 944 | 0.758 | 0.155 | 0.464 | 10.574 | 13.278 | 14.208 |
| market (closing line) | 944 | 0.761 | 0.139 | 0.415 | 9.570 | 12.018 | 13.041 |

Totals: ae-0.1 MAE 14.21 vs market total MAE 13.04 (n=944).

**Incremental value test** (fit on tune-season lines, scored here): margin = a + 0.992·market + c·(model − market), c = 0.325 ± 0.020 (SE, train n=27648). Out of sample RMSE: market 12.018, blend 12.210, model alone 13.278.

By line provider (a 'line' that is not a true close would let the model look better than it is):

| provider | c fitted on tune lines of this provider (±SE) | n here | RMSE market | RMSE blend |
|---|---|---|---|---|

**Simulated ATS, market+model blend, closing line** (HYPOTHETICAL): 303-344-0 (46.8%) on 647 bets, -65.3u, ROI -10.1% (95% CI -17.0% to -3.1%).
Of those, games where the blend differs from the line by ≥1.5 pts: 157-173-0 (47.6%), ROI -9.2% (95% CI -19.0% to +1.2%), n=330.

**Simulated ATS, closing line** (HYPOTHETICAL, 1u flat, rule: bet the positive-EV side; betting the CLOSE is the hardest possible test): 406-425-0 (48.9%), -55.1u, ROI -6.6% (95% CI -13.0% to -0.2%), max drawdown 68.5u; real prices on 100% of games, -110 assumed on the rest.

**Simulated ATS, OPENING line** (DraftKings open; HYPOTHETICAL): 417-411-0 (50.4%), -32.7u, ROI -3.9% (95% CI -10.3% to +2.4%). CLV on the model's side vs close: mean +0.43 pts, beat the close 42.6%, matched 25.7%, n=944.
