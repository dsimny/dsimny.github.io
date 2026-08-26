# Result: model vs market at T−24, NFL 2022–2024

Measured 2026-08-20 against methodology frozen the same day
(`docs/FOOTBALL_PREREG.md`, `frozen: 2026-08-20`, commit `6ecc261`).

This file is separate from the pre-registration ON PURPOSE. That document is
frozen; results do not get written back into a frozen spec, or the spec stops
being a record of what was committed to in advance.

## The question

> Does a market-blind football model add anything at the price we could actually
> have taken — the T−24 line — over and above the market itself?

## The answer

**No.** The market beats the model on every market, in every season, and the
model adds no information on top of the market.

### Head to head, log loss (lower is better)

| market | n | model | market | difference |
|---|---|---|---|---|
| moneyline | 852 | 0.63434 | **0.60908** | +0.02526 |
| spread | 826 | 0.71308 | **0.69283** | +0.02025 |
| total | 840 | 0.72428 | **0.69314** | +0.03113 |

### By season — 9 of 9 to the market

| market | 2022 | 2023 | 2024 |
|---|---|---|---|
| moneyline | +0.03033 | +0.02982 | +0.01568 |
| spread | +0.01623 | +0.02804 | +0.01652 |
| total | +0.03639 | +0.05598 | +0.00118 |

Positive = market better. There is no season and no market where the model wins.
The closest call is 2024 totals (+0.00118, a tie in practice); nothing else is
within 0.015.

### Incremental value: none

Losing head-to-head does not by itself prove a model carries no information — a
weak model can still add something on top of a strong one. So the outcome was
regressed on both logits together:

| market | model coefficient | se | z | reading |
|---|---|---|---|---|
| moneyline | −0.1533 | 0.2085 | −0.74 | indistinguishable from zero |
| spread | −0.0792 | 0.1840 | −0.43 | indistinguishable from zero |
| total | +0.0902 | 0.1761 | +0.51 | indistinguishable from zero |

All three are inside ±1 z. **This test is IN-SAMPLE and therefore biased in the
model's favour** — the blend weights were fitted on the same games they were
scored on. Even with that thumb on the scale, the model contributes nothing.
An out-of-sample test could only be worse.

## Why this is believed

Three things had to be right for the number to mean anything, and each has an
independent check:

1. **The de-vig is correct.** A properly de-vigged spread or total market is
   ~50/50 by construction, so its log loss should sit at ln 2 = 0.69315. Measured:
   0.69283 (spread) and 0.69314 (total). That is not a number we tuned toward;
   it falls out, and it validates the price maths independently of any model.
2. **The matching is near-complete.** 852 of 854 moneyline games matched, and
   ZERO games lacked a usable snapshot. A null produced by silently dropping
   half the sample would be worthless; this one is measured on essentially the
   whole out-of-sample period.
3. **The seasons are genuinely out-of-sample.** 2020 and 2021 sit inside the
   model's TUNE window and were deliberately NOT purchased, so no game here was
   used to fit the coefficients being tested.

## What it does not say

- It does not say the model is broken. Against football baselines it is fine:
  0.63437 log loss vs 0.68600 for always-home, margin RMSE 12.892 vs 13.778 for
  predict-the-mean. It knows real things about football.
- It says the market already knows all of them, 24 hours before kickoff, and
  more besides. The moneyline gap (0.609 vs 0.634) is roughly the distance
  between our model and a 15-book consensus that has priced injuries, weather,
  line movement and everything else we never ingested.
- It does not generalise to NCAA FBS. Different market, thinner books, larger
  talent gaps. It is evidence about the NFL market only.

## Consequence

Under the pre-registered priority order — CLOSING-LINE VALUE and EXPECTED VALUE
sit *below* PROBABILITY ACCURACY, and a model that cannot match the market's
accuracy at the moment of the bet has nothing to convert into either — there is
no qualifying signal.

**NO QUALIFYING SIGNAL is a valid and desirable result, not a failure.** It is
the outcome the pre-registration named as the honest prior, and it arrived for
$59 and 10,713 credits rather than after a season of paper losses.

The 2025 holdout is NOT spent and should not be. It exists to test whether a
promising methodology generalises; there is nothing promising to generalise. It
remains available, unspent, for a genuinely different fb-v0.2.

Recommended: do not stake this, do not publish it as a picks product, and do not
start adding features to make the number move. Adding an injury feed or weather
to a model that trails a 15-book consensus by 0.025 log loss on the moneyline is
not a plan, it is the sunk-cost reflex the priority order exists to prevent.

Reproduce with:

    python scripts/football/market_compare.py --seasons 2022-2024
