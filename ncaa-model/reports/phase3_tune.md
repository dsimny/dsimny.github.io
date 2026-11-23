# Phase 3 tune-season experiments (2018–22)

Walk-forward, D1 vs D1, n = 25,901 games. Each row changes only the
preseason prior of the ae-0.1 rating model (mean and per-team strength).
The preseason model for season s is fit on target seasons before s
(expanding window from 2017), so 2018 is fit on one season and 2022 on five.
`k` scales the Normal-Normal prior strength (k = 1 is the textbook
posterior; k < 1 loosens the prior). Produced by
`python -m ncaa.phase3 run --seasons tune` (results printed by each run; the
table below collects them).

| variant | k | margin RMSE | RMSE, ≤5 games played | RMSE, Nov–Dec | Brier |
|---|---|---|---|---|---|
| ae-0.1 (last season's rating, slope 1.0, 3-game prior) | — | 11.498 | 12.495 | 12.132 | 0.1795 |
| P0 last season, slopes refit | 0.5 | 11.524 | 12.623 | 12.211 | 0.1798 |
| P0 | 1.0 | 11.589 | | | |
| P0 | 2.0 | 11.786 | | | |
| P1 + two seasons back | 0.5 | 11.490 | 12.544 | 12.146 | 0.1794 |
| P1 | 1.0 / 2.0 | 11.554 / 11.746 | | | |
| P2 + conference pooling | 0.25 | 11.537 | | | |
| P2 | 0.35 | 11.487 | 12.518 | | |
| **P2** | **0.5** | **11.466** | 12.474 | 12.096 | 0.1790 |
| P2 | 1.0 / 2.0 | 11.535 / 11.725 | | | |
| Q0 last season + end-of-season form (rejected) | 0.25 / 0.35 | 11.592 / 11.540 | | | |
| Q2 = P2 + end-of-season form (rejected: no gain) | 0.35 | 11.487 | | | |
| P3 + returning minutes | 0.5 | 11.430 | 12.346 | 12.011 | 0.1782 |
| P4 + incoming transfers | 0.5 | 11.411 | 12.291 | 11.971 | 0.1780 |
| P5 + recruits | 0.5 | 11.397 | 12.250 | 11.938 | 0.1778 |
| **P6 + roster-dependent prior width (adopted)** | **0.5** | **11.390** | **12.224** | **11.925** | **0.1777** |
| P6 | 0.75 | 11.407 | 12.244 | 11.944 | 0.1780 |
| P6 | 1.0 | 11.446 | 12.289 | 11.991 | 0.1787 |

Reading it:

- Every roster block adds a little and they stack: returning minutes is the
  biggest single gain, then transfers, then recruits, then letting the prior
  widen for rebuilt rosters. Together −0.108 RMSE overall and −0.27 in
  the first five games.
- Refitting last season's slope alone (P0) is *worse* than ae-0.1. ae-0.1's
  prior is an end-of-season rating that is already shrunk; the data-only
  lag ratings used here are noisier, and need two-season history and the
  conference mean (P1, P2) before they catch up.
- k = 0.5 wins for every variant. The textbook k = 1 is too tight, most
  likely because teams change during the season (the recency weighting is
  fighting the prior) and because roster snapshots are imperfect.

## Four-factor matchup layer, tune-only check

Ridge regression of the P6 model's residual on adjusted four-factor
matchup features (`ncaa/fourfactors.py`), fitted on early tune seasons and
scored on later tune seasons:

| fit on | scored on | margin RMSE before → after | total RMSE before → after |
|---|---|---|---|
| 2018–20 | 2021–22 | 11.554 → 11.488 | 16.900 → 16.737 |
| 2018–21 | 2022 | 11.259 → 11.199 | 16.796 → 16.674 |

Ridge alpha 10 / 100 / 1000 moved these by ≤ 0.001; 100 adopted. The
largest margin coefficient is on the home-minus-away expected 3P%
(negative): a team whose rating was built on hot three-point shooting, or
whose defence has been lucky on opponents' threes, gives some of it back.
That is the "3P% is mostly noise" finding from the public literature,
recovered from data.

Disclosure: before running the tune-only check above, the layer was also
scored once on validation (2023–25) with the same three alphas. That run
did not change the design or the alpha (all three were within 0.001), but it
means validation was seen once for this layer before it was frozen.
