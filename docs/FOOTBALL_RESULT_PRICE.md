# Result: best price vs closing fair value, NFL 2022–2024 (fb-v0.2)

Measured 2026-08-24 against methodology frozen the same day
(`docs/FOOTBALL_PREREG_V02.md`, `frozen: 2026-08-24`).

Separate from the pre-registration ON PURPOSE, for the same reason
`FOOTBALL_RESULT_T24.md` is: results do not get written back into a frozen spec,
or the spec stops being a record of what was committed to in advance.

## The question

> At the moment we can actually act — T−24 — is the BEST AVAILABLE PRICE across
> books better than the market's own closing fair value, by enough to be worth
> taking after vig?

Market: moneyline. No model opinion enters anywhere — the side is chosen by a
market rule and the probability is inherited from the closing consensus.

## The answer

**No.** Expected value is negative in every tier and in the VALIDATE season.
Line shopping recovers roughly half the vig and leaves you paying the rest.

### Tier 1 (US-regulated — the only tier that may satisfy a gate)

| arm | season | n | record | EV | CLV (pts) | realised ROI |
|---|---|---:|---|---:|---:|---:|
| live | 2022 (TUNE) | 284 | 111-171-2p | **+0.71%** | +0.01 | −5.07% |
| live | 2023 (TUNE) | 285 | 96-189 | −1.35% | −0.55 | −5.22% |
| live | **2024 (VALIDATE)** | 259 | 82-177 | **−2.64%** | −0.99 | −16.02% |
| live | ALL | 828 | 289-537-2p | −1.05% | −0.49 | −8.55% |
| null | ALL | 828 | 408-418-2p | −1.40% | −0.84 | −4.23% |

Section 10 requires EV > 0 on TUNE **AND** on VALIDATE. TUNE is negative
combined (2022 positive, 2023 negative) and VALIDATE is clearly negative.
**The gate fails at its first condition. The 2025 holdout is NOT claimed and
remains unspent.**

### Every arm, all seasons pooled

| tier | live EV | null EV | live CLV | books at/near best |
|---|---:|---:|---:|---:|
| Tier 1 (16 books) | −1.05% | −1.40% | −0.49 | 3.42 |
| Tier 2 (offshore, 8) | −2.70% | −2.42% | −0.99 | — |
| Tier 1 ∩ 2025 (6 books) | −2.42% | −2.32% | −0.91 | — |

## Why this is believed

Four independent checks, each of which could have failed and did not:

1. **The null arm is calibrated to 0.4 points.** Random side selection won
   49.3% of the time against a predicted 49.7%. A de-vig or settlement bug
   would show up here first and loudly; it does not.
2. **The measured overround is 4.26 points**, which is ordinary NFL moneyline
   vig. Nothing was tuned toward that number; it falls out.
3. **The arithmetic reconciles.** 4.26 points of overround is ~2.13 per side.
   Section 0 measured the best price beating the consensus median by ~1.27
   points. 2.13 − 1.27 ≈ 0.9 points of vig still being paid — and the measured
   Tier-1 EV is −1.05%. The result is the vig, almost exactly.
4. **Nothing was silently dropped.** 0 games lacked a T−24 snapshot, 0 lacked a
   closing snapshot, and 26 of 854 were excluded for too few books (3.0%, far
   under the 25% clause). The staleness filter proved inert — `last_update` was
   present on 100% of quotes, median 1.9 minutes from the snapshot, only 0.02%
   beyond 15 minutes — so it excluded essentially nothing rather than quietly
   shaping the sample.

## What it does not say

- **It does not say the selection rule is worthless.** The live arm beats the
  random null on EV in Tier 1 (−1.05% vs −1.40%). The rule finds something. It
  finds about 0.35 points of something, against a ~2.1 point toll.
- **It does not say line shopping is pointless.** Recovering half the vig is
  real and worth doing. It is simply not, by itself, a positive-expectation
  strategy — which is the specific claim that was tested.
- **CLV came out NEGATIVE (−0.49), which section 5 did not predict.** The
  section predicted CLV would be positive almost by construction. It is not,
  and the reason is instructive: the rule selects the side where ONE book
  disagrees most with consensus, which is usually a slow or wrong book, and the
  line converges toward consensus by close. Selecting the biggest outlier means
  systematically buying the number most likely to move against you. That is a
  finding the pre-registration did not anticipate, and it is recorded rather
  than smoothed over.
- **It does not generalise to spreads, totals, or to NCAA FBS.** Moneyline only.

## The trend is against the hypothesis

EV declines monotonically across the window: **+0.71% → −1.35% → −2.64%**. And
per section 6, the Tier-1 book count collapses from 15 to 6 by 2025, with the
6-book arm already at −2.42% on 2022–24. Fewer books means less dispersion means
less of the vig recovered. Nothing about the direction of travel suggests a
later season would look better.

## Consequence

Under section 10's decision tree, Tier-1 EV < 0 lands on: report it, publish it,
and do not spend the holdout.

**NO QUALIFYING SIGNAL is a valid and desirable result.** It arrived for 13,860
credits and a day, rather than after a season of paper losses — and it is the
second independent confirmation, by a completely different mechanism, that this
market is efficient at the moment we could act.

Recommended: do not stake this, do not publish it as a picks product, and do not
re-cut the book set, the staleness window or the minimum-book filter looking for
a version that clears. All three were fixed in advance for exactly this moment.

Reproduce with:

    python scripts/football/price_test.py --seasons 2022-2024
