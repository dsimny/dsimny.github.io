# NCAA D-I men's basketball prediction engine

A research project, self-contained in `ncaa-model/`. It does not touch the
Open Ledger Sports site, its workflows, or its `data/` directory.

**Status (2026-11-23):** Phase 1 (data foundation), the Phase 2 baselines and
Phase 3 (roster/transfer/recruit priors + four-factor matchup layer, model
`ae-0.2`) are built and backtested. See [`docs/MODEL_LOG.md`](docs/MODEL_LOG.md) for
every result, including the ones that did not work.

## What's done vs. proposed

| | status |
|---|---|
| Data-source audit (access, cost, licence) | **done**: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) |
| Ingestion: 13 seasons of ESPN box scores + schedules (2014-15 → today) | **done**: `ncaa/ingest.py` |
| Historical betting lines (ESPN pickcenter) | **done** for 2015-16→2022-23 and 2025-26→today. No lines exist for 2023-25 |
| Data validation (17 checks) | **done**: `ncaa/validate.py` → `reports/data_validation.md` |
| Adjusted efficiency + tempo model with prior-season Bayesian shrinkage | **done**: `ncaa/ratings.py` (model `ae-0.1`) |
| Elo baseline, home-team baseline, market baseline | **done** |
| Walk-forward backtest, tune → validation → one-shot holdout | **done**: `ncaa/backtest.py`, `ncaa/evaluate.py`, `reports/` |
| Preliminary 2026-27 board (projections only, no prices) | **done**: `boards/` |
| Unit tests (math, odds parsing, rating recovery, no-leakage) | **done**: `tests/` |
| Returning-minutes, transfer and recruit priors with Bayesian shrinkage (CBBD) | **done**: `ncaa/priors.py`, `ncaa/cbbd_roster.py` → `data/derived/team_priors.csv` |
| Four Factors adjusted ratings + matchup residual layer | **done**: `ncaa/fourfactors.py` |
| Phase 3 experiments, validation vs named-book closes, one holdout run | **done**: `ncaa/phase3.py` → `reports/phase3_*.md`, `reports/holdout_ae-0.2.md` |
| Coaching-change prior | **not available**: CBBD has no coaches endpoint (404); no other free source found |
| Gradient boosting / ridge / ensemble with learned weights, isotonic/Platt calibration | proposed: Phase 4 |
| Live odds capture + Qualified/Watchlist/No Bet classification | proposed: Phase 5 (needs an odds feed, see DATA_SOURCES) |
| Streamlit dashboard | proposed: Phase 7. Deliberately not built before the model earns it (operating rule 6) |
| Daily automation + forward paper tracking | proposed: Phase 8 |

## Reproduce

```bash
cd ncaa-model
pip install -r requirements.txt
python -m ncaa.ingest                       # ~20 MB download, builds data/games.parquet + data/ncaa.duckdb
python -m ncaa.ingest --odds 2016 2017 2018 2019 2020 2021 2022 2023 2026 2027   # ~2.5 GB transfer (gzip), resumable
python -m ncaa.ingest --build
python -m ncaa.validate                     # -> reports/data_validation.md
python -m pytest -q tests                   # offline unit tests
python -m ncaa.backtest tune                # grid on TUNE seasons only -> model_params.json (~25 min)
python -m ncaa.evaluate predictions         # walk-forward predictions, all seasons
python -m ncaa.evaluate report              # tune + validation -> reports/backtest_ae-0.1.md
python -m ncaa.evaluate holdout             # ONE run per model version -> reports/holdout_ae-0.1.md
python -m ncaa.board 2026-11-23             # -> boards/2026-11-23.{md,csv} (never overwritten)

# Phase 3 (uses the committed data/derived/team_priors.csv; no key needed)
python -m ncaa.phase3 run --seasons tune    # prior-block experiments on tune seasons (~1 min per variant)
python -m ncaa.fourfactors build            # adjusted four-factor ratings before every game (~4 min)
python -m ncaa.phase3 final                 # ae-0.1 vs +priors vs +priors+FF (printed; no market section)
# needs CBBD_API_KEY (the `NCAA model` workflow runs these):
python -m ncaa.cbbd_roster features --refresh   # rebuild data/derived/team_priors.csv
python -m ncaa.phase3 final --lines         # + named-book closing lines -> reports/phase3_validation.md
python -m ncaa.phase3 holdout               # ONE run for ae-0.2 -> reports/holdout_ae-0.2.md
```

Raw data and the database are gitignored (re-fetchable, and ESPN-derived).
The committed artifacts are the code, `model_params.json`, `reports/`, and `boards/`.

## Rules this code enforces

- **No leakage.** A rating used for date *d* is fitted on games with
  `game_date < d` only (tested by scrambling future results). The D1 flag
  comes from the published schedule, not results. Elo predicts every game on
  a date before updating on any of them.
- **Chronological splits fixed in advance** (`ncaa/config.py`): tune
  2015-16→2021-22, validation 2022-23→2024-25, holdout 2025-26. The holdout
  command refuses to run twice for the same model version.
- **Predictions are never overwritten.** `board.py` refuses to replace an
  existing board.
- **No invented data.** Missing lines stay missing. Non-book "lines"
  (numberfire projections) are excluded from market tests.
- **Simulated ≠ real.** Every ROI figure is labelled hypothetical. No
  wagering is connected.
