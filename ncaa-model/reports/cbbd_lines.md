# CBBD betting lines: coverage, provider check, and the 2023-25 market test

Model ae-0.1, walk-forward predictions (unchanged). Lines from CollegeBasketballData; only aggregates are published here. **All betting figures are simulated; no wagers.**

Produced by the `NCAA model` workflow (run 45205721831, 2026-11-23), copied from its job summary.

## Coverage and join (ESPN game matched on date ±1 and final score)

| season | CBBD games with lines | matched to ESPN | flipped home/away |
|---|---|---|---|
| 2016 | 5413 | 4563 | 1 |
| 2017 | 5353 | 4512 | 0 |
| 2018 | 5398 | 4607 | 0 |
| 2019 | 5524 | 4632 | 0 |
| 2020 | 5161 | 4315 | 0 |
| 2021 | 4495 | 3313 | 0 |
| 2022 | 5712 | 4513 | 0 |
| 2023 | 5686 | 4768 | 0 |
| 2024 | 5196 | 4401 | 0 |
| 2025 | 5309 | 4453 | 5 |
| 2026 | 5416 | 4619 | 0 |
| 2027 | 982 | 817 | 41 |

## Sign check and accuracy, by season and provider

corr(spread, margin) must be strongly negative if `spread` is the home line.

| season | provider | n | corr | line MAE | model MAE (same games) | c (±SE), fit on this cell |
|---|---|---|---|---|---|---|
| 2016 | consensus | 2965 | -0.631 | 8.75 | 8.87 | 0.429 ± 0.065 |
| 2016 | numberfire | 3166 | -0.645 | 8.58 | 8.87 | 0.010 ± 0.071 |
| 2016 | teamrankings | 3263 | -0.648 | 8.57 | 8.88 | -0.062 ± 0.071 |
| 2017 | consensus | 2789 | -0.598 | 9.15 | 9.18 | 0.528 ± 0.068 |
| 2017 | numberfire | 3195 | -0.619 | 9.03 | 9.19 | 0.220 ± 0.074 |
| 2017 | teamrankings | 3232 | -0.621 | 9.00 | 9.20 | 0.103 ± 0.077 |
| 2018 | consensus | 3260 | -0.614 | 8.95 | 9.08 | 0.373 ± 0.056 |
| 2018 | numberfire | 3024 | -0.622 | 8.93 | 9.06 | 0.390 ± 0.055 |
| 2018 | teamrankings | 3337 | -0.625 | 8.88 | 9.08 | 0.110 ± 0.069 |
| 2019 | consensus | 1701 | -0.600 | 9.11 | 9.36 | 0.138 ± 0.088 |
| 2019 | numberfire | 4472 | -0.622 | 9.10 | 9.26 | 0.298 ± 0.057 |
| 2019 | teamrankings | 4488 | -0.630 | 9.06 | 9.23 | 0.219 ± 0.059 |
| 2020 | consensus | 4274 | -0.592 | 9.20 | 9.31 | 0.509 ± 0.042 |
| 2020 | numberfire | 3942 | -0.622 | 9.12 | 9.35 | 0.202 ± 0.057 |
| 2020 | teamrankings | 4256 | -0.619 | 9.06 | 9.30 | 0.217 ± 0.054 |
| 2021 | consensus | 3174 | -0.617 | 9.46 | 9.68 | 0.351 ± 0.059 |
| 2021 | numberfire | 3202 | -0.625 | 9.42 | 9.74 | 0.149 ± 0.065 |
| 2021 | teamrankings | 3177 | -0.632 | 9.34 | 9.66 | 0.158 ± 0.065 |
| 2022 | consensus | 4165 | -0.621 | 9.09 | 9.22 | 0.490 ± 0.042 |
| 2022 | teamrankings | 4440 | -0.636 | 8.95 | 9.24 | 0.219 ± 0.051 |
| 2023 | ESPN BET | 4664 | -0.613 | 9.09 | 9.37 | 0.072 ± 0.056 |
| 2023 | consensus | 4558 | -0.595 | 9.22 | 9.38 | 0.381 ± 0.048 |
| 2023 | teamrankings | 4613 | -0.603 | 9.16 | 9.36 | 0.313 ± 0.049 |
| 2024 | ESPN BET | 4396 | -0.645 | 9.12 | 9.32 | 0.185 ± 0.057 |
| 2025 | ESPN BET | 4453 | -0.665 | 9.01 | 9.31 | 0.029 ± 0.057 |
| 2026 | Bovada | 3926 | -0.650 | 9.18 | 9.38 | 0.282 ± 0.053 |
| 2026 | Draft Kings | 3534 | -0.635 | 8.98 | 9.16 | 0.010 ± 0.080 |
| 2026 | ESPN BET | 1091 | -0.696 | 9.68 | 10.23 | 0.290 ± 0.067 |
| 2027 | Bovada | 708 | -0.744 | 9.59 | 10.71 | -0.078 ± 0.092 |
| 2027 | Draft Kings | 817 | -0.745 | 9.58 | 10.68 | -0.105 ± 0.086 |

`c` is the weight on (model − line) in margin = a + b·line + c·(model − line). Near 0 means the model adds nothing beyond that line. A provider with a much larger c than the others is likely publishing a stale or non-closing number.

## Validation seasons 2023-24 and 2024-25 against a real line

- **2024, ESPN BET** (n=4396): line RMSE 11.508, model RMSE 11.757. Simulated ATS at this line (-110 assumed; CBBD carries no spread prices): 1831-1696-0 (51.9%), ROI -0.9% (95% CI -3.9% to +2.4%).
- **2025, ESPN BET** (n=4453): line RMSE 11.436, model RMSE 11.845. Simulated ATS at this line (-110 assumed; CBBD carries no spread prices): 1732-1762-0 (49.6%), ROI -5.4% (95% CI -8.5% to -2.2%).
- **Blend fit on 2024, scored on 2025 (ESPN BET)**: c = 0.185 ± 0.057; RMSE line 11.436 vs blend 11.428.
