# Football market observation — fp-v0.4, cohort v1

STATUS: FROZEN. Research only. No recommendation or release authority.
frozen: 2026-09-17

## What this cohort is

This document defines the `fp-v0.4-market-observation-v1` research cohort.
It authorises live market observation using the fp-v0.4 selection rule as it
currently exists in `scripts/football/board.py` and `scripts/football/market.py`.

## What is observed

The fp-v0.4 rule, applied to live Odds API captures:

- **Coverage:** every NFL and NCAA FBS game with at least 5 eligible books,
  quotes fresh within 15 minutes of their snapshot (staleness filter), and a
  T-24 capture within ±6 hours of the 24-hour mark before kickoff.
- **Ranking:** ascending effective overround at best Tier-1 prices:
  `implied(best_home) + implied(best_away) − 1`.
- **Side selection:** the side whose best Tier-1 price sits farthest above its
  de-vigged consensus fair value, normalised by fair probability (relative price
  discount), corroborated by ≥2 Tier-1 books at or within 1 implied-probability
  point of the best price.
- **Eligibility limits:** both sides' de-vigged fair probabilities must be ≥20%;
  best moneyline price must be ≤+400.
- **Pool:** NFL and NCAA FBS rank in a single combined pool (fp-v0.2 decision).
- **Decision moment:** Saturday 14:00 US/Eastern; eligible kickoff window
  D < kickoff ≤ D+24h.
- **Sports:** nfl, ncaaf.

No model probability enters the selection. No play-by-play data enters. No
prior-result data enters. Everything is derived from Odds API captures.

## What this cohort is not

- **Not a recommendation.** No observation produced by this cohort is an
  instruction to bet. These are timestamped research observations.
- **Not part of the official football record.** Observations generated under
  this cohort have no authority to enter `data/football/football_ledger.json`,
  `data/football/commitments.json`, or any official W-L, CLV, or hypothetical
  return aggregate.
- **Not retroactively promotable.** No observation from this cohort may be
  reclassified into the official record at any later date, regardless of outcome.
  Any future official strategy must begin as a separately preregistered cohort with
  its own pre-registration document committed before observations begin.
- **Not a delivery authority.** This cohort does not authorise member or Discord
  delivery. `RESEARCH_DELIVERY_ENABLED` controls whether research observations are
  posted; this document does not change that flag and it remains False.
- **Not a modification to the underlying rule.** The fp-v0.4 selection logic in
  `market.py` and `board.py` is unchanged by this observation cohort. This cohort
  observes the rule as-is; it does not endorse, validate, or modify it.
- **Not a staking authority.** All observations are 0 units. No stake is implied,
  recorded, or authorised.

## Units

All observations are **0 units**. The `pnl_per_unit` field, when grading is later
implemented, will record what one unit *would have* returned — a hypothetical for
analysis purposes only, never money risked.

## Scope and data boundaries

- **Source:** Odds API captures in `data/football/odds/`.
- **Output:** `data/football/research/board_<week>_fp-v0.4-market-observation-v1.json`.
  Research boards are never written to `data/football/board_*.enc` or
  `data/football/board_*.json`.
- **Official state untouched:** `data/football/commitments.json`,
  `data/football/game_commitments.json`, `data/football/football_ledger.json`,
  and all official W-L, CLV, and delivery-status files are read-only to
  research generation.
- **Coverage window:** from the date `RESEARCH_DELIVERY_ENABLED` is first set
  to `True` onward. Prior captures may be used for evaluation but are not
  retroactively observed.
- **Grading:** not implemented in this step. When grading is implemented it
  will operate only on files under `data/football/research/`, never on official
  ledger files.

## Version boundary

Any of the following changes requires a new research version ID and a new
pre-observation document committed before the first observation under that version:

- The effective overround ranking formula
- The corroboration threshold (currently ≥2 Tier-1 books)
- The moneyline eligibility cap (currently ≤+400)
- The fair probability floor (currently 20%)
- The staleness window (currently 15 minutes)
- The T-24 tolerance (currently ±6 hours)
- The sports included (currently nfl, ncaaf)
- The decision moment (currently Saturday 14:00 US/Eastern)

Changes that do **not** require a new version: adding metadata fields to research
board output, fixing a grading bug that does not affect which play is selected,
changing how results are formatted in the research output file.

## Freeze evidence

The SHA-256 of this document and its first commit SHA are recorded in
`data/football/research_preregistrations.json` under id
`fp-v0.4-market-observation-v1`. The registry is append-only: this entry is never
replaced or edited.
