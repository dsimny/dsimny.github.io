# Football pipeline — product spec (NFL / NCAA FBS)

Adopted 2026-08-24. This is a PRODUCT spec, not a pre-registration. It commits
to what ships and how the pick is chosen; it deliberately makes no claim about
expected value, for the reason in section 1.

Related, and both required reading before touching this:
- `docs/FOOTBALL_RESULT_T24.md` — the market-blind model does not beat the market.
- `docs/FOOTBALL_RESULT_PRICE.md` — best-price shopping does not clear the vig.
- `docs/FOOTBALL_PREREG_V02.md` — the frozen spec those results answer.

## 1. What this is, and the claim it does NOT make

The product is: **full reasoning on every covered game, one play we would
actually act on, and every one of them graded in public, win or lose.**

It is sold as process and receipts, NOT as alpha. That is not modesty, it is the
only position the evidence supports. Two independent tests, by different
mechanisms, both say this market is efficient at the moment we can act:

| test | finding |
|---|---|
| fb-v0.1 (model vs market) | market wins 9 of 9, zero incremental value |
| fb-v0.2 (best price vs closing fair) | EV −1.05%; the result IS the vig |

**THE SELECTION RULE BELOW MAKES NO EXPECTATION CLAIM.** We do not know, and do
not assert, that it performs better than picking at random. Its record will be
published either way. Any future claim that it is +EV requires fb-v0.3 with its
own pre-registration and its own clean test season; the 2025 holdout is unspent
and available for exactly that.

Site copy must say this plainly (House Rules 4 and 8). Language like "our edge",
"+EV play", or "the model likes" is BARRED in football until such a study exists.

## 2. Three layers, and the boundary between them is load-bearing

1. **NUMBERS** — deterministic, gradeable, market-derived: eligible quotes,
   de-vigged consensus, best price, effective overround, line movement, CLV.
   No model probability. Nothing here is invented.
2. **REASONING** — an LLM writes the per-game narrative FROM layer 1's numbers.
   It never produces a number, a probability, or a pick.
3. **SELECTION** — section 4's rule picks the play from layer 1. It never reads
   layer 2's prose.

WHY THE BOUNDARY MATTERS. An LLM has no calibrated probability, cannot be graded
as one, and drifts week to week. As a writer over fixed numbers it is genuinely
good; as the model it would cost the pre-registration, calibration, CLV and
append-only ledger that are the entire brand. If layer 2 ever originates a
number, the product has quietly become the thing all of the above disproved.

## 3. Coverage — what "passes our markers" means

In MLB the markers are model-derived (edge gates, circuit breakers). Football has
no model edge, so football markers are about COVERAGE QUALITY, not value. A game
is covered when, at its T−24 capture:

- at least **5 eligible books** quote the moneyline, and
- every quote used is within **15 minutes** of the snapshot (staleness filter,
  carried over from `FOOTBALL_PREREG_V02.md` section 6), and
- the game has a resolvable kickoff time and both teams resolve to known ids.

A game failing any of these is listed as **NO MARKET**, never silently dropped.
Section 6's rule applies: if the share excluded exceeds 25% in a week, the page
says "manual review", not "covered".

NCAA FBS is included on the same terms, and the 5-book floor is what keeps the
Saturday slate honest — thin-market games are named and skipped rather than
written up on two quotes.

## 4. THE SELECTION RULE (precommitted)

Ranked in this order, computed only from layer 1:

**Step 0 — the slate is a WEEK, not a day.** Football is a weekly sport
(`FOOTBALL_PREREG_V02.md` section 11 makes the same point about capture
windows). Thursday and Monday are single-game days, so a per-day slate would
leave no free play on half the calendar — measured: 80 of 162 game-days had
only one qualifier, against 3 of 65 weeks.

**Step 1 — market quality.** For each covered game compute the EFFECTIVE
OVERROUND at best prices:

    eff_overround = implied(best_price_home) + implied(best_price_away) − 1

This is the toll you pay to play that game at the best numbers available. Rank
games ascending. Lowest = tightest market = cleanest play.

**Step 2 — corroboration guard.** Discard any game whose chosen side's best
price is offered by fewer than **2 books** at or within 1 implied-probability
point of it.

This guard exists because of a measured failure, and the reason is recorded so
nobody removes it later as noise: fb-v0.2's rule selected the single largest
book-vs-consensus disagreement and posted CLV of **−0.49**. The biggest outlier
is usually a slow or wrong book, and its number converges toward consensus by
close — so selecting outliers means systematically buying the price most likely
to move against you. Requiring corroboration removes the lone-outlier case.

**Step 3 — side.** Within the chosen game, take the side whose best price sits
furthest above its de-vigged consensus fair value. After step 2 this is a
corroborated price in a tight market, not an outlier in a loose one.

**Step 4 — assignment.**
- **Rank 1 → premium.** The cleanest play on the slate.
- **Free → the highest-ranked qualifier that is NOT rank 1**, mirroring House
  Rule 2, which gives away a genuinely good play without giving away the
  product.
- **No qualifying games → publish the no-play message.** House Rule 6 carries
  over unchanged: passing is a position too. This will happen, and the copy is
  written for it in advance.

**Ties** break to the game with more eligible books, then to the earlier
kickoff. Deterministic, so two runs of the same slate produce the same play.

## 4a. What the rule did on 2022–2024 (characterisation, NOT a gate)

Run before launch so the rule's behaviour is known rather than discovered by
members. `scripts/football/pipeline_rule.py`. **The rule does not change in
response to these numbers** — that is the whole point of writing them down here.

| tier | n | record | win% | EV | CLV | ROI | eff. overround |
|---|---:|---|---:|---:|---:|---:|---:|
| premium (rank 1) | 65 | 23-42 | 35.4% | −1.20% | −0.49 | −11.69% | **1.11 pts** |
| free (rank 2) | 62 | 23-39 | 37.1% | −0.47% | −0.28 | +2.88% | 1.47 pts |
| all candidates | 632 | 204-426-2p | 32.4% | −1.69% | −0.67 | −11.47% | 2.01 pts |

**The rule does the job it was designed for.** Effective overround on the
premium play is 1.11 points against 4.26 for the raw consensus — it finds the
week's tightest market and the best corroborated number in it, cutting the toll
to roughly a quarter.

**It does not turn that into positive expectation.** EV stays negative, as both
prior results predicted. No expectation claim is made and none may be made in
copy.

**Do not read the ROI column.** 65 premium plays across three seasons is one a
week; at these odds the standard error on ROI is larger than the spread between
the rows. Premium at −11.69% and free at +2.88% is noise, not an ordering, and
anyone treating it as evidence that the free play is better has misread the
sample size. This is also why the product is sold on process and receipts: its
ledger will take years to say anything about ROI, and saying so up front is
cheaper than being caught by it later.

## 5. Premium is early access, not secrecy

House Rule 7 carries over: every play is fingerprinted before kickoff and
published in full after grading, win or lose. Members buy timing and the full
slate; the public ledger stays complete.

This is what lets a paywall coexist with the transparency thesis. Withholding a
pick before kickoff is the product; withholding it after is fraud.

## 6. Grading and the ledger

- Football gets its **own ledger file**, never mixed with `ledger.json`,
  `daily_ledger.json`, `totals_ledger.json` or `watchlist.json`. House Rule 1
  applies to it from the first entry: append-only, no backfill, no deleted loss.
- **0 units** through the proving window. Football does not size stakes. The
  Daily Pick's 2026-09-08 staking review is a separate decision about a separate
  strategy and does not authorise anything here.
- Both the premium play and the free play are graded and published. So is the
  CLV of each, since closing capture already exists.

## 7. What would change any of this

Only a completed fb-v0.3: a new pre-registration, frozen before scoring, with
its own clean test season, following the pattern of v0.1 and v0.2. Not a good
month, not a hot streak, not a plausible-sounding new feature.

House Rule 9's principle governs: the selection rule above is fixed before week
1 and does not move in response to results in either direction. That is
precisely when the temptation peaks.

## 8. Version history

| version | date (2026) | change |
|---|---|---|
| fp-v0.1 | Aug 24 | Adopted. Three layers, coverage markers, the effective-overround selection rule with its corroboration guard, free/premium split under House Rules 2 and 7, own ledger at 0u. No expectation claim made. |
