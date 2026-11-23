# Backtest report — report — model ae-0.1

Generated 2026-11-23 12:42 UTC. Walk-forward: each game predicted from ratings fitted only on games before its date. Hyper-parameters tuned on 2016–2022 only: `{"prior_games": 3.0, "prior_regress": 1.0, "tempo_prior_games": 3.0, "tempo_regress": 0.9, "half_life_days": 60.0, "hca_lambda": 10000.0, "nond1_off": -12.0, "nond1_dfn": 10.0, "newd1_off": -4.0, "newd1_dfn": 4.0}`. Win probability = Φ(margin / σ), σ by games played (tune seasons): {"0-2": 12.74, "3-5": 11.96, "6-10": 11.65, "11+": 11.03}. Market σ (closing spread residual, tune) = 11.23.

All betting figures are **simulated on recorded lines — no wagers were placed**. Closing lines come from ESPN's pickcenter (single book per game; consensus/Caesars before 2024, DraftKings from 2025-26); ESPN gives no capture timestamp for pre-2025 lines. No lines exist in the source for 2023-24 and 2024-25.

## Tune — seasons 2016–2022

### All D1-vs-D1 games (every model on the same games)
| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 36848 | 0.619 | 0.231 | 0.654 | 11.232 | 14.393 | — |
| Elo | 36848 | 0.718 | 0.184 | 0.545 | 9.321 | 11.922 | — |
| adj. efficiency ae-0.1 | 36848 | 0.728 | 0.178 | 0.529 | 8.975 | 11.405 | 13.631 |

### ae-0.1 by season
| season | n | mae | rmse | su_acc | brier | total_mae |
|---|---|---|---|---|---|---|
| 2016 | 5461 | 8.751 | 11.093 | 0.730 | 0.176 | 13.888 |
| 2017 | 5486 | 8.798 | 11.274 | 0.733 | 0.176 | 13.576 |
| 2018 | 5539 | 8.958 | 11.391 | 0.738 | 0.176 | 13.809 |
| 2019 | 5601 | 9.000 | 11.406 | 0.734 | 0.177 | 13.751 |
| 2020 | 5324 | 9.034 | 11.488 | 0.728 | 0.180 | 13.381 |
| 2021 | 3944 | 9.402 | 11.880 | 0.707 | 0.187 | 13.705 |
| 2022 | 5493 | 9.003 | 11.428 | 0.723 | 0.180 | 13.318 |

### ae-0.1 by month (early season = thin data)
| month | n | mae | bias | brier |
|---|---|---|---|---|
| Nov | 6574 | 9.752 | 0.420 | 0.159 |
| Dec | 7095 | 9.165 | 0.206 | 0.156 |
| Jan | 9752 | 8.772 | -0.619 | 0.192 |
| Feb | 9283 | 8.644 | -0.368 | 0.189 |
| Mar | 4117 | 8.630 | -0.063 | 0.191 |
| Apr | 27 | 9.614 | -1.617 | 0.189 |

### ae-0.1 by conference (conference games only, n ≥ 100)
| conf | n | mae | brier |
|---|---|---|---|
| MEAC | 650 | 8.148 | 0.191 |
| Big West | 579 | 8.206 | 0.198 |
| CUSA | 939 | 8.226 | 0.183 |
| Ivy | 348 | 8.269 | 0.183 |
| SWAC | 671 | 8.349 | 0.180 |
| Big 12 | 679 | 8.377 | 0.188 |
| CAA | 653 | 8.383 | 0.208 |
| Sun Belt | 811 | 8.416 | 0.219 |
| Big South | 727 | 8.437 | 0.193 |
| OVC | 773 | 8.556 | 0.184 |
| WAC | 510 | 8.572 | 0.153 |
| American | 759 | 8.611 | 0.167 |
| Southland | 813 | 8.628 | 0.187 |
| Southern | 680 | 8.630 | 0.176 |
| ASUN | 522 | 8.660 | 0.200 |
| Summit | 553 | 8.687 | 0.183 |
| ACC | 1048 | 8.702 | 0.186 |
| WCC | 626 | 8.717 | 0.145 |
| Horizon | 743 | 8.726 | 0.203 |
| Big East | 709 | 8.732 | 0.205 |
| MAAC | 791 | 8.739 | 0.220 |
| Big Sky | 780 | 8.748 | 0.193 |
| MVC | 692 | 8.750 | 0.189 |
| Big Ten | 1014 | 8.862 | 0.191 |
| Am. East | 560 | 8.943 | 0.191 |
| Patriot | 658 | 8.963 | 0.213 |
| NEC | 675 | 8.975 | 0.217 |
| A-10 | 924 | 8.986 | 0.192 |
| Mountain West | 761 | 8.995 | 0.168 |
| Pac-12 | 848 | 9.099 | 0.190 |
| SEC | 949 | 9.131 | 0.194 |
| MAC | 818 | 9.586 | 0.216 |

### Calibration (home win probability)
| range | n | predicted | observed | obs_95ci |
|---|---|---|---|---|
| 0.0-0.1 | 533 | 0.063 | 0.083 | ±0.023 |
| 0.1-0.2 | 1319 | 0.155 | 0.182 | ±0.021 |
| 0.2-0.3 | 2078 | 0.252 | 0.268 | ±0.019 |
| 0.3-0.4 | 2955 | 0.352 | 0.380 | ±0.018 |
| 0.4-0.5 | 3814 | 0.452 | 0.464 | ±0.016 |
| 0.5-0.6 | 4543 | 0.551 | 0.567 | ±0.014 |
| 0.6-0.7 | 5223 | 0.651 | 0.661 | ±0.013 |
| 0.7-0.8 | 5560 | 0.750 | 0.740 | ±0.012 |
| 0.8-0.9 | 5400 | 0.850 | 0.853 | ±0.009 |
| 0.9-1.0 | 5423 | 0.952 | 0.945 | ±0.006 |

### By prediction confidence
| conf_bucket | n | favourite_won | mean_conf |
|---|---|---|---|
| (0.499, 0.6] | 8357 | 0.553 | 0.550 |
| (0.6, 0.7] | 8178 | 0.646 | 0.650 |
| (0.7, 0.8] | 7638 | 0.738 | 0.749 |
| (0.8, 0.9] | 6719 | 0.846 | 0.849 |
| (0.9, 1.0] | 5956 | 0.942 | 0.950 |

### Against the market (27648 games with a real-book line; providers: Caesars Sportsbook (New Jersey), consensus)

| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 27648 | 0.611 | 0.232 | 0.656 | 11.081 | 14.148 | — |
| Elo | 27648 | 0.714 | 0.186 | 0.550 | 9.265 | 11.798 | — |
| adj. efficiency ae-0.1 | 27648 | 0.724 | 0.181 | 0.536 | 8.985 | 11.412 | 13.555 |
| market (closing line) | 27648 | 0.730 | 0.177 | 0.527 | 8.786 | 11.232 | — |

Totals: ae-0.1 MAE 13.53 vs market total MAE 13.15 (n=20459).

**Simulated ATS, closing line** (HYPOTHETICAL, 1u flat, rule: bet the positive-EV side; betting the CLOSE is the hardest possible test): 10588-10265-415 (50.8%), -663.2u, ROI -3.1% (95% CI -4.3% to -1.9%), max drawdown 698.9u; real prices on 74% of games, -110 assumed on the rest.

## Validation — seasons 2023–2025

### All D1-vs-D1 games (every model on the same games)
| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 17196 | 0.616 | 0.231 | 0.654 | 11.201 | 14.307 | — |
| Elo | 17196 | 0.712 | 0.187 | 0.552 | 9.409 | 11.969 | — |
| adj. efficiency ae-0.1 | 17196 | 0.717 | 0.182 | 0.538 | 9.083 | 11.508 | 13.238 |

### ae-0.1 by season
| season | n | mae | rmse | su_acc | brier | total_mae |
|---|---|---|---|---|---|---|
| 2023 | 5714 | 9.069 | 11.451 | 0.713 | 0.185 | 13.273 |
| 2024 | 5723 | 9.090 | 11.478 | 0.715 | 0.183 | 13.314 |
| 2025 | 5759 | 9.089 | 11.593 | 0.723 | 0.178 | 13.130 |

### ae-0.1 by month (early season = thin data)
| month | n | mae | bias | brier |
|---|---|---|---|---|
| Nov | 3562 | 9.824 | 1.095 | 0.166 |
| Dec | 3034 | 9.150 | 0.432 | 0.166 |
| Jan | 4294 | 8.781 | -0.580 | 0.193 |
| Feb | 4160 | 8.849 | -0.040 | 0.192 |
| Mar | 2120 | 8.826 | 0.342 | 0.189 |
| Apr | 26 | 7.798 | 1.245 | 0.199 |

### ae-0.1 by conference (conference games only, n ≥ 100)
| conf | n | mae | brier |
|---|---|---|---|
| SWAC | 347 | 7.594 | 0.194 |
| MEAC | 190 | 8.094 | 0.176 |
| Southland | 322 | 8.159 | 0.185 |
| Big South | 259 | 8.208 | 0.200 |
| NEC | 237 | 8.291 | 0.204 |
| American | 377 | 8.301 | 0.187 |
| CUSA | 299 | 8.339 | 0.210 |
| Sun Belt | 417 | 8.460 | 0.195 |
| OVC | 320 | 8.530 | 0.215 |
| A-10 | 448 | 8.644 | 0.208 |
| WAC | 319 | 8.679 | 0.188 |
| ASUN | 356 | 8.685 | 0.198 |
| Mountain West | 338 | 8.702 | 0.165 |
| MAAC | 378 | 8.722 | 0.231 |
| Big East | 360 | 8.753 | 0.152 |
| Big West | 350 | 8.761 | 0.176 |
| Big Sky | 296 | 8.762 | 0.205 |
| Ivy | 177 | 8.796 | 0.186 |
| Big 12 | 414 | 8.910 | 0.194 |
| ACC | 522 | 9.008 | 0.189 |
| CAA | 407 | 9.013 | 0.183 |
| MVC | 393 | 9.051 | 0.180 |
| Big Ten | 499 | 9.089 | 0.218 |
| Horizon | 360 | 9.177 | 0.186 |
| Patriot | 297 | 9.206 | 0.224 |
| Am. East | 237 | 9.335 | 0.189 |
| SEC | 437 | 9.425 | 0.191 |
| Pac-12 | 262 | 9.428 | 0.197 |
| MAC | 345 | 9.552 | 0.194 |
| WCC | 278 | 9.684 | 0.143 |
| Summit | 259 | 9.929 | 0.198 |
| Southern | 297 | 9.949 | 0.190 |

### Calibration (home win probability)
| range | n | predicted | observed | obs_95ci |
|---|---|---|---|---|
| 0.0-0.1 | 221 | 0.065 | 0.063 | ±0.032 |
| 0.1-0.2 | 566 | 0.158 | 0.180 | ±0.032 |
| 0.2-0.3 | 973 | 0.253 | 0.279 | ±0.028 |
| 0.3-0.4 | 1356 | 0.352 | 0.396 | ±0.026 |
| 0.4-0.5 | 1898 | 0.452 | 0.490 | ±0.022 |
| 0.5-0.6 | 2334 | 0.551 | 0.568 | ±0.020 |
| 0.6-0.7 | 2562 | 0.650 | 0.657 | ±0.018 |
| 0.7-0.8 | 2537 | 0.749 | 0.743 | ±0.017 |
| 0.8-0.9 | 2453 | 0.850 | 0.854 | ±0.014 |
| 0.9-1.0 | 2296 | 0.952 | 0.950 | ±0.009 |

### By prediction confidence
| conf_bucket | n | favourite_won | mean_conf |
|---|---|---|---|
| (0.499, 0.6] | 4232 | 0.542 | 0.550 |
| (0.6, 0.7] | 3918 | 0.638 | 0.649 |
| (0.7, 0.8] | 3510 | 0.737 | 0.749 |
| (0.8, 0.9] | 3019 | 0.848 | 0.849 |
| (0.9, 1.0] | 2517 | 0.949 | 0.950 |

### Against the market (5598 games with a real-book line; providers: Caesars Sportsbook (New Jersey), consensus)

| model | n | su_acc | brier | log_loss | margin_mae | margin_rmse | total_mae |
|---|---|---|---|---|---|---|---|
| home-team baseline | 5598 | 0.612 | 0.231 | 0.655 | 10.956 | 13.914 | — |
| Elo | 5598 | 0.706 | 0.189 | 0.556 | 9.308 | 11.775 | — |
| adj. efficiency ae-0.1 | 5598 | 0.713 | 0.185 | 0.547 | 9.083 | 11.466 | 13.274 |
| market (closing line) | 5598 | 0.722 | 0.181 | 0.538 | 8.930 | 11.330 | 12.957 |

Totals: ae-0.1 MAE 13.27 vs market total MAE 12.96 (n=5598).

**Incremental value test** (fit on tune-season lines, scored here): margin = a + 0.992·market + c·(model − market), c = 0.325 ± 0.020 (SE, train n=27648). Out of sample RMSE: market 11.330, blend 11.249, model alone 11.466.

By line provider (a 'line' that is not a true close would let the model look better than it is):

| provider | c fitted on tune lines of this provider (±SE) | n here | RMSE market | RMSE blend |
|---|---|---|---|---|
| consensus | 0.409 ± 0.023 | 5597 | 11.331 | 11.246 |

**Simulated ATS, market+model blend, closing line** (HYPOTHETICAL): 1163-1075-35 (52.0%) on 2273 bets, -16.6u, ROI -0.7% (95% CI -4.8% to +3.3%).
Of those, games where the blend differs from the line by ≥1.5 pts: 342-272-13 (55.7%), ROI +6.2% (95% CI -1.0% to +13.4%), n=627.

**Simulated ATS, closing line** (HYPOTHETICAL, 1u flat, rule: bet the positive-EV side; betting the CLOSE is the hardest possible test): 2136-2056-63 (51.0%), -117.0u, ROI -2.7% (95% CI -5.7% to +0.1%), max drawdown 215.2u; real prices on 100% of games, -110 assumed on the rest.
