# Data validation report

| check | result | detail |
|---|---|---|
| unique game_id | PASS | 0 duplicates |
| home != away team | PASS | 0 games |
| no tied finals | PASS | 0 ties |
| plausible D1-vs-D1 scores (>=20 each side) | PASS | 0 D1 games below 20; 4 non-D1 blowouts kept (real results) |
| box score == schedule score | PASS | 0 mismatches |
| schedule score present | PASS | 1 games where the schedule shows 0-0 but the box score has a final (box score used): [401726454] |
| four-factor inputs present | PASS | 0 games missing a box stat |
| possessions in [50,100] (OT allowed to 100) | PASS | 59 games outside |
| home/away possession estimates agree (median |gap| < 3) | PASS | median 1.15, p99 4.98 |
| points per 100 poss in [50,160] | PASS | 117 outside |
| full seasons have >= 5,500 games | PASS | {2015: 5927, 2016: 5881, 2017: 5922, 2018: 6002, 2019: 6047, 2020: 5757, 2021: 4283, 2022: 5965, 2023: 6220, 2024: 6240, 2025: 6286, 2026: 6299, 2027: 1226} |
| chronologically sorted | PASS |  |
| home win rate (non-neutral, D1) in [0.55,0.70] | PASS | 0.649 |
| odds: spread is the home line (corr(spread, margin) < -0.5) | PASS | corr=-0.614, n=39009 |
| odds: closing-spread MAE in [7,10] pts | PASS | 8.84 |
| odds: total MAE in [10,16] pts | PASS | 13.14, n=31795 |
| odds: coverage by season (real books only) | PASS | {2016: 3711, 2017: 3487, 2018: 3926, 2019: 2056, 2020: 5281, 2021: 3837, 2022: 5403, 2023: 5598, 2026: 4766, 2027: 944} |
