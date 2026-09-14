# Daily Pick staking review — held 2026-09-14 (scheduled 2026-09-08)

**Decision: the Daily Pick stays at 0 units. No staking change, no version bump.**
Production is unchanged (`scripts/engine.py` `DAILY_PICK_UNITS = 0.0`). Turning
staking on would still require Daniel's explicit approval and a version bump, as
House Rule 9's amendment requires.

## 1. The governing rule, and what it did not say

Registered in advance (engine v0.15, commit `f67e90d`, 2026-08-09):

- `scripts/engine.py`: `DAILY_PICK_UNITS = 0.0`, `DAILY_PROVING_END = "2026-09-08"`,
  "scheduled staking review (target: flat 0.25u)", "never early, never in response
  to a hot or cold week".
- `CLAUDE.md` House Rule 9 amendment: the strategy "proves itself FORWARD at 0u
  with a SCHEDULED staking review (2026-09-08, target flat 0.25u)".
- `scripts/grade.py`: a flat 0.25u paper basis plus CLV is recorded "so the record
  the staking review reads exists from day one".

**No numeric pass criteria were registered** — no minimum sample, ROI, CLV,
calibration or significance threshold. Only the date, the target stake and the
numbers to be read were fixed. The review therefore cannot "pass" a test; it can
only weigh the evidence conservatively. That gap is closed in section 5, before
any further data exists.

**Timing.** The review was due 2026-09-08 and was not held until 2026-09-14.
Nothing was run on the due date. The primary cut below uses picks dated through
2026-09-08 only, as registered; the three later picks are shown separately and
changed nothing.

## 2. Evidence (`data/daily_ledger.json`)

Integrity: 30 entries, one per slate, no duplicates, no missing prices, no voids
or pushes, every `units_staked` 0.0, every `pnl_paper` recomputes from price and
result. All 30 are moneylines at the 0.25u paper basis. 4 slates honestly passed
(no positive edge); 2026-08-26 is a missed board (pipeline gap), not a pass.

| metric | through 2026-09-08 (registered window) | all graded to 2026-09-12 |
|---|---|---|
| graded | 27 | 30 |
| record | 16–11 | 17–13 |
| win rate (Wilson 95%) | 59.3% [40.7, 75.5] | 56.7% [39.2, 72.6] |
| break-even at prices taken | 50.8% | 50.8% |
| paper P&L (0.25u basis) | +1.13u on 6.75u risked | +0.90u on 7.50u risked |
| paper ROI per unit risked | +16.8%, 95% CI ≈ [−21%, +54%] | +12.0%, 95% CI ≈ [−24%, +48%] |
| CLV (ledger definition), mean | −0.45 pts (CI −1.64 to +0.74) | −0.36 pts |
| beat the close | 9/27 (33.3%) | 11/30 (36.7%) |
| price taken vs closing no-vig | −0.91 pts; EV at close −1.7%/unit | −0.82 pts; −1.6%/unit |
| Brier: model / blend / market close | 0.2425 / 0.2456 / 0.2504 | 0.2442 / 0.2476 / 0.2531 |
| max paper drawdown | 0.51u | 0.51u |
| longest losing streak | 2 | 2 |

Market mix: 100% moneyline; 17 favourites by price (9–8), 3 even, 10 underdogs
(6–4). Edge buckets show no monotone relationship with results.

## 3. Reading

- **The win-loss record is not evidence of edge.** At n = 27 the ROI interval spans
  roughly −21% to +54%; it cannot be told apart from zero or from the −4% a bettor
  pays in vig. Detecting a true +3% ROI at 80% power needs on the order of 8,700
  bets — decades at one pick a slate.
- **The faster signal points the wrong way.** CLV is slightly negative and the
  price taken is on average worse than the closing no-vig line; 18 of 27 picks
  failed to beat the close.
- **Calibration is inconclusive.** Every Brier score sits within ±0.005 of a coin
  flip; the small model advantage is noise at this sample.
- Staking a strategy whose only positive number is its win-loss record would be
  exactly the "streak" decision the rule forbids.

## 4. Decision and what changes

- **Remain at 0 units.** No version bump.
- Site, email, blog and Discord copy that still says the proving window "ends
  September 8" is updated to state that the review was held and kept the stake at
  zero (House Rule 8). No other Daily Pick behaviour changes.
- The paper basis and CLV recording continue unchanged.

## 5. Criteria for the next review — registered now, before the data exists

Proposed for Daniel's approval; binding only once approved and committed.

**When:** at the first MLB date on which the Daily Pick has **150 graded picks**,
or 2027-06-30, whichever is later. Never earlier, never in response to a streak.

**Flip to flat 0.25u staked only if ALL hold on the full record:**
1. Mean price-taken CLV (closing no-vig probability minus implied probability of
   the price taken) **> 0**, with the lower bound of its 95% interval **> −0.25 pts**.
2. Blend Brier score **≤** the closing no-vig market's Brier on the same picks.
3. No unresolved integrity defect in the ledger, the board or the grading run.

Win-loss record and paper ROI are reported but are **not** criteria. If any
condition fails, the stake stays at 0u and the next review is another 150 graded
picks later.
