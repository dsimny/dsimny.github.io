# Football launch reset and fp-v0.4 amendment

Authorized by Daniel's request of September 11, 2026 to restart the official
football record, preserve prior history, and repair selection/grading defects.
Effective kickoff boundary: **2026-09-11 00:00 America/New_York (04:00 UTC)**.
This amendment is prospective. It does not claim it was published before any
game already in progress when implementation was requested.

## Limited amendment to House Rule 1

All existing graded entries remain byte-for-byte intact. All existing board
files and commitment hashes remain intact. No loss is deleted, no result is
changed, and no historical pick is recreated. The full ledger remains the audit
source. Aggregates are computed from explicitly named cohorts of that history.

### What "intact" means for each file, precisely

`data/football/reset_2026-09-11.json` records two different kinds of claim, and
conflating them would make one of them false within hours.

**Frozen files** — `historical_files_sha256`. `football_ledger.json`,
`commitments.json` and both `board_*.enc` files are finished records. Their
whole-file SHA-256 is recorded and is expected to match forever. Any change to
any of them breaks the amendment.

**Live append-only state** — `append_only_baselines`. `game_commitments.json` is
not a finished record: the pipeline appends a per-game fingerprint every time a
new game becomes evaluable at its own T−24, several times a day. Its whole-file
hash therefore **is expected to diverge**, and asserting byte-equality on it
would be a public claim that fails on the next capture. It was listed as frozen
in the first draft of the artifact and that was a category error, corrected here.

What is claimed for it instead, and what a reader may check:

- the **132 entries** committed at or before `2026-09-12T00:00:00Z` are all still
  present and byte-identical;
- later appends are **allowed and expected**;
- no pre-reset entry may disappear or be mutated, and nothing may be back-dated
  into that window;
- whole-current-file SHA equality is **not** expected and must not be asserted.

`baseline_entries_sha256` is the SHA-256 of the sorted `[game_key, sha256(entry)]`
pairs for that subset, so a deletion changes the count, a mutation changes the
digest, and a back-dated insertion changes both. `sha256_at_reset` is retained as
the file's value at the boundary — evidence of the starting point, not a
forever-claim. `selftest_capture_isolation.py` re-checks this against the live
file on every capture run and in the daily data monitor.

The official football launch cohort starts at 0-0 and accepts only fp-v0.4
premium/free selections whose kickoffs meet the date boundary and whose actual
weekly board was fingerprinted before kickoff. Existing entries are displayed
in **Pre-launch / invalidated selection test**, outside official W-L, CLV and
hypothetical returns. This is not an undefeated lifetime record.

The baseline contains 44 graded rows: five historically tiered selections (all
losses) and 39 coverage rows. The manifest at
`data/football/reset_2026-09-11.json` records the row count, canonical row hash,
and raw hashes of the original ledger, commitments and board files. Historical
tier labels are preserved but are not asserted to match published selections.
MLB and D.J. Mercer ledgers are unaffected. No further reset is authorized.

## Defects and prospective selection amendment

The previous absolute probability gap favoured tiny-probability underdogs when
both offered prices were worse than proportional de-vigged consensus. Under
uniform proportional vig, a side's negative absolute gap shrinks with its fair
probability; choosing the least negative gap therefore chooses the longshot.
The absolute effective-overround ranking and a one-percentage-point
corroboration tolerance did not prevent those extreme selections.

fp-v0.4 compares `(fair_probability - implied_best_price) / fair_probability`.
Equal discounts (rounded to ten decimal places) break toward higher consensus
probability, then side name. Games remain covered but cannot supply an official
play unless both consensus probabilities are at least 20% and the chosen
moneyline is no longer than +400. Coverage cards state the PASS reason.
Among eligible games, retain ascending effective overround, then book count,
kickoff, sport and matchup as deterministic tie breakers. Retain the existing
Tier-1 price requirement, five-book coverage floor and corroboration guard.

These are conservative launch product limits chosen after observing the defect,
not a validated predictive model, expected-value claim or backtest improvement.
No research holdout is accessed. Frozen research specifications and historical
`pipeline_rule.py` analysis retain their old rules and are not the live selector.

## Timing and commitments

The Saturday 14:00 Eastern weekly decision remains unchanged. Friday games are
not retroactively promoted merely because the official date starts Friday.
Only games still in the future at execution can be selected; no `--asof` write
is allowed. Captures after the decision moment cannot enter the selection pool.
Game evaluations must be committed by the decision moment and before kickoff.
fp-v0.4 game keys include the version, so old commitments cannot be overwritten
or silently reused as corrected selections. Frozen blocks are stored and
verified, and later disagreements are recorded while retaining the original.
The board writer checks kickoff again after preparing prose.

If an existing board already occupies a week, it is not replaced. That week
passes for the new cohort unless it already has an authentic fp-v0.4 board.
No result-aware retry or replacement selection is permitted.

## Grading and publication

The old grader re-ranked completed games separately by sport and locked an
entire sport/week on its first run. The new production path settles only the
premium/free positions in the hashed weekly board, at their committed prices.
It checks board and selected-game hashes before any ledger write. Results join
by sport, exact normalized matchup and kickoff; ambiguous joins fail closed.
Regular-season allowlists remain mandatory. A missing final stays pending.

Identity is board hash plus tier, allowing NFL/NCAAF finals to arrive separately
without duplicate grades or losing later results. Missing closing prices yield
null CLV and do not suppress a win/loss/push. All stakes remain zero; returns
are hypothetical. Corrupt ledgers fail rather than being treated as empty.
The full board is revealed once both selected positions settle. The grading
workflow supplies its encryption key and no longer reveals from the mere
existence of any entry for a week. Public rendering/delivery checks board hashes.

Local changes do not publish themselves. Production grading uses this amendment
only after the code reaches the deployed repository. A local hash timestamp is
not independent proof of publication: Git commit/push timing must still be
verified before claiming that a selection was publicly committed in advance.
