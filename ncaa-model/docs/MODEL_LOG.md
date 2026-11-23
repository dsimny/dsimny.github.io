# Model log

Every version, every result, including the rejected ones. Newest first.
Full tables: `reports/backtest_ae-0.1.md` (tune + validation),
`reports/holdout_ae-0.1.md` (holdout + current season), `reports/tuning_grid.csv`.

## ae-0.1 addendum — CBBD lines (2026-11-23)

Same model, no re-tuning. CollegeBasketballData lines were pulled inside the
read-only `NCAA model` workflow (key in the CBBD_API_KEY secret). Full table:
`reports/cbbd_lines.md`.

- **The 2023-24 / 2024-25 gap is filled, and the answer is unchanged.**
  Against ESPN BET lines the line wins both seasons: RMSE 11.51 vs the model's
  11.76 (2024), 11.44 vs 11.85 (2025). Simulated ATS was −0.9% and −5.4%. A blend
  fit on 2024 and scored on 2025 improved RMSE by 0.008 (11.436 → 11.428),
  which is too small to matter.
- **The stale-line finding holds up on an independent source.** Using CBBD's own
  copy of each provider, `consensus` gives c = 0.35–0.53 in every season
  but one. Named books are near zero: ESPN BET 0.03–0.19, DraftKings
  0.01 (2026) and −0.11 (2027). `teamrankings` and `numberfire` sit
  in between (they aren't books). Bovada 2026 (0.28) also looks like an early
  or stale capture.
- **Rule from here on:** a market test only counts against a named regulated
  book's close (DraftKings, ESPN BET, Caesars). Everything else is
  reported, never used to claim an edge.

## ae-0.1 — opponent-adjusted efficiency + tempo (2026-11-23)

**Model.** Per-possession offence/defence ratings and an additive tempo
rating, fitted daily by penalised least squares on the season's games before
the prediction date. Each team is pulled toward a prior equal to its previous
season's final rating (Gaussian prior, strength 3 games-equivalent; tempo
regressed 10%). Recency half-life 60 days. Fixed home edge measured on the
previous season's conference games (≈3 pts). Win probability = Φ(margin/σ),
σ by games played (12.7 → 11.0). Possessions = FGA − OREB + TO + 0.475·FTA.

**Splits** (fixed before any result was seen): tune 2016–2022 · validation
2023–2025 · holdout 2026 (run once) · current season 2027 forward.

### Results (D1 vs D1, walk-forward)

| split | n | model MAE | model Brier | Elo Brier | home-baseline Brier | market MAE (same games) | model MAE (same games) |
|---|---|---|---|---|---|---|---|
| tune 2016–22 | 36,848 | 8.98 | 0.178 | 0.184 | 0.231 | 8.79 | 8.99 |
| validation 2023–25 | 17,196 | 9.08 | 0.182 | 0.187 | 0.231 | 8.93 (2023 only) | 9.08 |
| **holdout 2026** | 5,752 | 9.27 | 0.181 | 0.187 | 0.232 | **8.86 (DraftKings)** | **9.07** |
| 2027 to date (Nov) | 945 | 10.57 | 0.155 | 0.164 | 0.199 | 9.57 | 10.57 |

Calibration on tune/validation is good: every decile within ~0.03 of observed.
Holdout calibration is a bit loose in the middle (the 0.4–0.5 bucket won 51%).

### The honest headline

1. **The model clearly beats the simple baselines.** It's better than Elo and
   the home-team rule on every split, holdout included.
2. **It does NOT beat the closing line.** On the 2026 holdout the DraftKings
   closing spread has an MAE of 8.86 against the model's 9.07, and the blend
   fitted on tune seasons is WORSE than the market alone out of sample
   (RMSE 11.199 vs 11.167). Simulated ATS at the close: 49.8%, ROI −4.9%
   (95% CI −7.9% to −1.5%). 2027 to date, same story.
3. **The validation result that looked like an edge was an artifact of the
   line source.** On 2023 the blend beat the "market" (RMSE 11.25 vs 11.33),
   and the model's coefficient beyond the line was c = 0.33 ± 0.02. Split by
   provider on the tune seasons: c = **0.41** for ESPN's `consensus` lines but
   only **0.11 ± 0.04** for Caesars. A model that "beats" a line source by
   that much more than it beats a real book is beating a stale or non-closing
   line, not the market. On DraftKings in the holdout the advantage was gone.
   **Lesson recorded:** never trust a market test against ESPN `consensus`
   lines. Use a named book with a known close.
4. **One signal worth following up (not an edge):** against the DraftKings
   OPENING line, the model's side gained +0.33 pts of closing-line value on
   average (beat the close 39%, line moved against it 27%, no move 34%).
   Simulated ROI at the open was still −2.4% (CI −5.4% to +0.7%). The model
   holds some information the opener lacks and the close has absorbed. That
   is the target for Phase 5, and it needs real timestamped opening prices.

### Rejected / changed during ae-0.1 development

- **Free home-court parameter → rejected.** It soaked up the strength gap in
  early-season "buy games" (strong host, weak visitor) and inflated the home
  edge to ~4.7 pts, biasing home projections by about −2.3 pts out of sample.
  Replaced with a fixed edge measured on the prior season's conference games
  (balanced schedules). Margin RMSE on 2017 went from 11.85 to 11.46.
- **Uncentred ratings → fixed.** Ratings are only defined up to a shift, so
  offence and defence are now centred on the D1 average.
- **Grid edge.** The 27-config grid picked prior_regress = 0.8 at the top of the
  grid. Extended on tune seasons only: 0.9 → RMSE 11.420, 1.0 → 11.415.
  Adopted 1.0 (carry last season in full, let the 3-game prior do the
  shrinking). The differences are small (<0.01 RMSE).
- **"Games where blend ≠ line by ≥1.5 pts" filter.** This was added during
  validation, so it was not pre-declared. It looked good on 2023 (55.7%,
  ROI +6.2%, CI −1.0% to +13.4%) and failed on the holdout (51.8%, ROI −1.6%).
  It's reported only as a cautionary example.

### Known weaknesses (inputs to Phase 3)

- Early season: Nov MAE is ~10.2 vs ~8.8 in Feb, with a +1 pt bias (pred too
  small in buy games). Priors carry no roster info. Transfers and returning
  minutes are the obvious next prior.
- There's no blowout dampening, so AdjEM can be inflated early (Illinois +44.7 on
  2026-11-23 after 6 games).
- On neutral sites ESPN's "home" team outperforms by ~1 pt. Some "neutral"
  games are semi-home; worth a parameter.
- Totals: MAE 13.7 vs the market's 13.3. Tempo has the same prior structure
  and no matchup terms.
