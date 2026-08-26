# Football — pre-registration, fb-v0.2 (the price hypothesis)

STATUS: **FROZEN 2026-08-24.** Every threshold, book tier, filter and gate below
was chosen before any of them was scored. From here, changes ship as fb-v0.3 with
a new clean test season -- never as an edit in place.

This document is the methodology for a NEW question, written before that
question has been scored. fb-v0.1 is closed and is not reopened here: its
market-blind model lost to the de-vigged consensus on all three markets in all
three seasons and added no incremental value (`docs/FOOTBALL_RESULT_T24.md`).
Nothing below tries to make that model better. Adding an injury feed or weather
to a model that trails a 15-book consensus by 0.025 log loss is the sunk-cost
reflex the v0.1 priority order exists to prevent, and it is still prevented.

BOTH FREEZE CONDITIONS WERE MET BEFORE THE STAMP.

1. **2025-season historical odds purchased** 2026-08-24 — 123 snapshots, 3,690
   credits, via the schedule-only holdout read described in section 9. Holdout
   RESULTS were never loaded and the one-shot evaluation is unspent.
2. **Daniel read and accepted sections 4, 5 and 10** and instructed the freeze
   on 2026-08-24.

WHAT HAS BEEN RUN AS OF THE FREEZE. Nothing in section 8. No side has been
selected, no EV computed, no arm scored, on any season. The only price work that
predates this stamp is the dispersion characterisation disclosed in section 0 and
the book-set count in section 6, both recorded before the freeze precisely so
they cannot be presented afterwards as findings.

WHAT THE FREEZE DOES. The `frozen:` line at the bottom of THIS file gates the
2025 holdout, and does so in code as of 2026-08-24.

That was not true when this document was first drafted, and the gap is worth
recording because of how quietly it appeared. `scripts/football/asof.py`
originally hardcoded `PREREG = docs/FOOTBALL_PREREG.md` and read the first
`frozen:` line it found there. v0.1's file already reads `frozen: 2026-08-20`,
so `frozen_date()` returned a date and the holdout stood **unlocked for a
question whose methodology was still being written**. Nothing errored and
nothing warned — the guard simply stopped covering the thing it guarded, which
is the most dangerous way for a safety check to fail: it still looks present.

`asof.py` now carries a `SPECS` registry and refuses `claim_holdout()` while ANY
spec that exists on disk reads `frozen: 2026-08-24` — for every purpose, not just
that spec's own. An unfrozen spec in the repo means the methodology is still
moving, and spending a one-shot test while anything is still moving is the
failure mode whatever the stated reason. Verified 2026-08-24: with this file
unfrozen, a claim is refused and names this file as the reason.

Two protections therefore stand, and they catch different things:

1. **The freeze gate** — cannot claim the holdout before the methodology is
   fixed. Restored by the SPECS registry above.
2. **The append-only one-shot ledger** (`data/football/holdout_evaluations.json`,
   currently empty — the evaluation has never been taken) — cannot claim it
   twice, ever. The ledger now also records WHICH spec spent it and the freeze
   date of every spec at that moment, because the spec files can be edited
   afterwards and the ledger cannot.

---

## 0. What has already been looked at (disclosure)

Written down BEFORE the freeze, because a pre-registration that hides a prior
look is not a pre-registration. On **2026-08-24**, before this document existed,
the purchased snapshots were characterised for cross-book PRICE DISPERSION. That
was market characterisation, not a strategy evaluation — no side was selected,
no bet was simulated, no outcome was scored, and no game result was read. But it
is what motivated this hypothesis, so it is disclosed rather than omitted.

Measured over `data/football/odds/hist/` — 357 snapshots, 9,892 event-snapshots,
23 books, 12,462 side-observations with at least 5 books quoting:

| statistic | best h2h price vs consensus median, implied-probability points |
|---|---|
| mean | +1.271 |
| median | +1.184 |
| p75 | +1.736 |
| p90 | +2.269 |
| p99 | +3.704 |
| share ≥ 1.0 pt | 60.0% |
| share ≥ 2.0 pt | 16.0% |

The ≥5-book filter and the use of the median as consensus were choices made
during that look; both are carried into section 8 unchanged rather than
re-selected now that their effect is visible.

**What this is NOT.** It is not an edge, and section 5 exists because it is very
easy to read it as one. It is a gross gap between two prices at the same instant,
most of which is the overround that a two-way market carries by construction. It
is the reason to run the test, not the result of one.

---

## 1. What this is, and what it is not

fb-v0.1 asked whether a market-blind MODEL could out-forecast the market. The
answer was no. fb-v0.2 asks a different question that does not require
out-forecasting anything:

> At the moment we can actually act — T−24 — is the BEST AVAILABLE PRICE across
> books better than the market's own final answer, by enough to be worth taking
> after vig and after execution constraints?

A price edge and a forecasting edge are different objects. The first needs a
better opinion than the market; the second needs only a better number than the
average book, at a moment when the books disagree. v0.1 measured the first and
closed it. This measures the second, which v0.1 never tested — its comparison
ran against the consensus median price and never asked what a bettor taking the
best of 23 books would have got.

It ships as a PAPER TRACK at 0 units. It does not enter `data/ledger.json`, it
does not size stakes, and it does not produce a bet recommendation. NO
QUALIFYING SIGNAL remains a valid and desirable result.

The honest prior is still NO. Books that are badly mispriced against the
consensus tend to be badly mispriced for reasons — stale quotes, limits that
vanish on contact, or a number nobody can actually take. Section 7 exists to
make those reasons visible instead of letting them look like profit.

## 2. Priority order (unchanged from v0.1, never reversed)

DATA INTEGRITY → NO LEAKAGE → PROBABILITY ACCURACY → CALIBRATION
→ CLOSING-LINE VALUE → EXPECTED VALUE → (staking is out of scope for v0.2)

One clarification this version needs, because it is the whole subject: in v0.1
PROBABILITY ACCURACY meant the model's accuracy, and its failure there ended the
question. fb-v0.2 makes no probability claim of its own. It inherits the
market's closing estimate as its probability, so the level that decides this
version is EXPECTED VALUE measured against that estimate — with CLOSING-LINE
VALUE demoted to a diagnostic for the reason given in section 5.

## 3. Scope of v0.2

IN: NFL, seasons 2022–2025 historical, 2026 prospective. Markets: moneyline,
spread, total. Best-price selection across a pre-declared book set.

OUT: NCAA FBS — still phase 2, still its own pre-registration and its own
holdout, still NOT added by widening this one.

OUT: the v0.1 market-blind model as a SIDE SELECTOR. It has no demonstrated
forecasting value, so using it to choose which side to price-shop would import a
known-null signal into a test that is trying to isolate price. It may appear in
section 8 only as a labelled diagnostic arm, never in the primary strategy.

OUT: player props, live betting, parlays, staking, sizing.

## 4. The hypothesis, stated so it can fail

**H1 (primary).** For a side selected WITHOUT any model opinion, the best
available price at T−24 across the Tier-1 book set carries positive expected
value when settled against the de-vigged CLOSING consensus probability, net of
the vig embedded in the price taken.

**H0 (the null this must beat).** Best-price EV at T−24 is ≤ 0 against closing
de-vigged fair value. The market is efficient at the best price too, and the
dispersion in section 0 is overround plus stale quotes, not opportunity.

**Pre-declared side-selection rule** (declared now, before scoring): the side
whose best available price implies the LOWEST probability relative to the
de-vigged consensus at the same T−24 instant — i.e. the largest single-book
disagreement with the market's own contemporaneous fair value. Ties break to the
side with more books quoting. No model input, no result input.

## 5. The tautology guard (the section that decides what a pass means)

**CLV CANNOT BE THE GATE FOR THIS HYPOTHESIS, AND TREATING IT AS ONE WOULD
MANUFACTURE A PASS.**

This is the trap the whole document is built around. CLV asks whether the price
we took beat the closing price. If we take the BEST of 23 books at T−24 and
compare it to the closing CONSENSUS, we are comparing an extreme to a central
tendency. Whenever the market does not move much between T−24 and close — the
common case — best-at-T−24 beats consensus-at-close automatically, by
construction, with no skill involved whatsoever. A strategy that shops for the
best number will therefore post positive CLV essentially always, and that number
would mean nothing.

House Rule 9's discipline applies here in spirit: the metric is fixed before the
result, precisely because this metric could be chosen to flatter.

THE GATE IS THEREFORE EXPECTED VALUE, not CLV:

    q  = de-vigged CLOSING consensus probability for the side
    p  = best available T−24 American price for that side (Tier-1 books)
    EV = q · payout(p) − (1 − q − push_mass) · 1     [pushes settle at 0]

EV > 0 is a real claim: the number available at T−24 was better than the
market's own final fair estimate of the same outcome. That is not automatic and
it is not implied by dispersion.

THREE INDEPENDENT VALIDITY CHECKS, all pre-committed, all reported pass or fail:

1. **The de-vig is correct.** A properly de-vigged spread or total market is
   ~50/50 by construction, so its log loss must sit near ln 2 = 0.69315. v0.1
   measured 0.69283 and 0.69314 on this same data and this same code path. If
   fb-v0.2's de-vig does not reproduce those numbers, the price maths is wrong
   and nothing downstream is read.
2. **CLV is reported but is NOT a gate.** It is printed alongside EV precisely
   so the gap between "posted good CLV" and "had positive EV" is visible on the
   page rather than conflated. An arm that shows strong CLV and zero EV is the
   expected outcome under H0, and reporting both is how that gets said out loud.
3. **A null arm runs beside the live arm.** Same pipeline, side chosen at random
   with the same book set and the same filters. If the null arm also posts
   positive CLV (it should) and zero EV (it should), the machinery is behaving
   and the live arm's CLV can be read for what it is worth: nothing.

## 6. Data foundation

| source | use | notes |
|---|---|---|
| The Odds API historical | T−24 and closing prices, 2022–2025 | on disk for 2022–2024; **2025 must be purchased before the freeze** |
| nflverse | game results, settlement | already ingested, 2010–2026 |
| ESPN public API | 2026 live schedule, scores | grading + slate |

**BOOK TIERS, declared now, before any result is scored.** Choosing the book set
after seeing which books produce the edge is the most obvious way to overfit
this hypothesis, so the sets are fixed here and both are always reported.

- **Tier 1 — US-regulated (PRIMARY, and the only tier a gate may be met on):**
  `draftkings`, `fanduel`, `betmgm`, `williamhill_us`, `betrivers`, `superbook`,
  `pointsbetus`, `twinspires`, `barstool`, `wynnbet`, `sugarhouse`, `unibet_us`,
  `circasports`, `foxbet`, `betfair`, `fanatics`.
- **Tier 2 — offshore and other (REPORTED, never a gate):** `betonlineag`,
  `lowvig`, `betus`, `mybookieag`, `bovada`, `gtbets`, `intertops`, `unibet`.

Tier 2 is separated for a stated reason rather than a squeamish one: five of the
nine most frequently quoted books in the purchased data are offshore, so they
carry a large share of the observed dispersion. An edge that exists only in Tier
2 is not a product this site can honestly publish — the audience largely cannot
act on it, limits are unknown, and House Rule 5's legal footer sits awkwardly
beside a recommendation to use an unregulated book. If the edge lives only
there, that is the finding, and it gets published as that.

**THE BOOK SET IS NOT STABLE ACROSS THE VALIDATION WINDOW, and this was found
before the freeze rather than during scoring.** The 2025 odds were purchased
2026-08-24 and the book landscape had consolidated hard:

| | 2022–2024 | 2025 |
|---|---|---|
| distinct books quoting | 23 | 11 |
| of which Tier 1 | 15 | 6 |

`fanatics` is new in 2025 (1,640 quotes) and is added to Tier 1 above — it is a
US-regulated book. Thirteen books present in 2022–2024 do not quote in 2025 at
all: `barstool`, `betfair`, `circasports`, `foxbet`, `gtbets`, `intertops`,
`pointsbetus`, `sugarhouse`, `superbook`, `twinspires`, `unibet`, `unibet_us`,
`wynnbet`. That is real-world consolidation, not a data fault — several of those
brands were acquired or withdrew from the US market in that window.

WHY THIS MATTERS AND WHAT IT CHANGES. Best-of-N dispersion is mechanically a
function of N. The section 0 measurement (+1.27 points) was taken across 23
books; the holdout season offers 11, and only 6 in the tier that may satisfy a
gate. A smaller edge in 2025 is therefore the EXPECTED result even if the market
behaves identically, and it must not be read as the hypothesis failing to
generalise across time when it may only be failing to generalise across N.

Three consequences, all pre-committed here:

1. **N is reported alongside every EV figure**, per season and per tier. An EV
   number without its book count is not interpretable in this study.
2. **The TUNE/VALIDATE seasons are additionally scored on a 6-book Tier-1
   subset** — the books that survive into 2025 (`draftkings`, `fanduel`,
   `betmgm`, `williamhill_us`, `betrivers`, plus `fanatics` where present) — so
   there is a like-for-like comparison against the holdout instead of only a
   23-book-vs-11-book one. This is a second reported arm, not a replacement:
   the full-set numbers are still primary for TUNE and VALIDATE.
3. **The section 6 minimum of 5 eligible books is now close to binding in 2025**
   for Tier 1, which has 6. The share of sides excluded by that filter is
   reported per season, and if it exceeds 25% the result reads "manual review"
   under the existing House Rule 4 clause rather than "passed".

Nothing above is a gate change. The gate is still Tier-1 EV against the
de-vigged closing consensus, unchanged from section 10.

**STALENESS RULE, pre-committed:** a book's quote is eligible only if its
`last_update` is within **15 minutes** of the snapshot timestamp. A stale quote
is the single most likely source of a fake edge — it is a number the book has
not yet moved and would not have honoured. Ineligible quotes are excluded from
BOTH the best price and the consensus, and the exclusion rate is reported per
season. If that rate exceeds 25% in any season, the result reads "manual review",
not "passed" (House Rule 4).

**MINIMUM BOOKS:** a side is scored only when at least **5** eligible books
quote it, carried over from section 0 unchanged.

## 7. Execution realism (what separates a number from a bet)

Recorded now because each of these can only make the measured edge smaller, and
discovering them after a positive result would be discovering them too late:

- **Limits are unobservable.** The Odds API returns prices, never the stake a
  book would accept. We therefore cannot claim size, only direction, and the
  cards must say so.
- **The best price may be one book, once.** The count of Tier-1 books at or
  within 1 point of the best price is recorded per observation and reported. An
  edge that always rests on a single outlier book is fragile in a way an edge
  available at four books is not.
- **Line shopping assumes accounts everywhere.** Realising a best-of-15 price
  needs funded accounts at 15 books. This is a real constraint on the audience
  and belongs in the site copy, not in a footnote.
- **No interpolation, ever.** Consensus is built from OBSERVED lines only,
  carried over from v0.1 section 8.

## 8. Method

1. For each game and each T−24 snapshot, build the eligible quote set (section 6
   filters).
2. Compute the de-vigged consensus fair probability per side from the median of
   eligible books, proportionally de-vigged.
3. Select the side by the section-4 rule.
4. Record the best eligible Tier-1 price, and separately the best Tier-2 price.
5. At close, rebuild the de-vigged consensus from the last pre-kickoff snapshot.
6. Settle against the nflverse result, pushes at zero. Book EV against the
   closing de-vigged probability, and CLV as a reported diagnostic.
7. Run the null arm (section 5.3) through steps 3–6 unchanged.

DIAGNOSTIC ARM, clearly labelled and never a gate: the same pipeline with the
v0.1 game model choosing the side instead of the section-4 rule. It exists to
answer one question cheaply — does a known-null forecaster plus a real price
advantage add up to anything? — and its expected answer is no.

## 9. Validation plan

| seasons | role | odds on disk? |
|---|---|---|
| 2022–2023 | TUNE. Every threshold is chosen here and reported as in-sample. | yes (116 + 120 snapshots) |
| 2024 | VALIDATE. The selected configuration is scored here ONCE. | yes (121 snapshots) |
| 2025 | HOLDOUT. One evaluation, enforced by `asof.claim_holdout()`. | **NO — must be purchased** |
| 2026 | prospective forward test, live, at 0 units | n/a |

Walk-forward only. No random splits.

**The holdout is shared with fb-v0.1 and is still unspent.** `asof.HOLDOUT` is
2025 and `data/football/holdout_evaluations.json` is empty, so the single
permitted evaluation has never been taken. v0.1's result doc reserved it
explicitly "for a genuinely different fb-v0.2", and a price hypothesis is
genuinely different from a forecasting one. It is claimed ONCE, only after the
TUNE and VALIDATE gates in section 10 are met, and never to rescue a failure —
a failed holdout becomes fb-v0.3 with a new test season, exactly as v0.1 said.

Two conditions on that claim, and they are now enforced differently — which is
worth knowing, because only one of them will stop you:

- **This file must read a real `frozen:` date.** ENFORCED in code since
  2026-08-24 (`asof.SPECS`). A claim while this reads NOT YET is refused and
  names this file.
- **The 2025 odds must exist on disk.** NOT enforced, and deliberately left
  that way rather than half-checked: `asof.py` governs game data and knows
  nothing about the odds store, and a file-existence test there would be a
  weaker claim than it appears (files can exist and be empty, partial, or the
  wrong season). The scoring script for section 8 is what must refuse to run on
  a missing or short 2025 odds set, and it must say which. Until that script
  exists this is a manual check, recorded here as manual (House Rule 4).

## 10. Pre-committed gates and the review date

**HISTORICAL GATE (no calendar date — the data is on disk, so this runs once and
reports):**

- Tier-1 EV > 0 on TUNE **AND** Tier-1 EV > 0 on VALIDATE **AND** the section-5
  validity checks all pass → claim the 2025 holdout. Not before.
- Tier-1 EV ≈ 0 while CLV is positive → this is the H0 outcome and the section-5
  prediction coming true. Report it, publish it, stop. Do not re-cut the book
  set, the staleness window, or the minimum-book filter to find a version that
  works; those are all fixed above for exactly this moment.
- Tier-1 EV ≤ 0 but Tier-2 EV > 0 → report as an offshore-only finding. Not
  productised, not staked, published as what it is.
- Tier-1 EV < 0 → best price at T−24 is worse than closing fair value. Report
  that too, on the site, in the Morning Line.

**FORWARD REVIEW DATE: 2026-11-17**, after 10 completed NFL weeks — the same
date fb-v0.1 already set, deliberately not moved. On that date only. Never
early, never in response to a streak in either direction (House Rule 9).

READ at that date: `data/football_paper_ledger.json` aggregates — EV realised,
CLV, exclusion rates, and the Tier-1 book-count distribution from section 7.

## 11. Odds budget

**THE PURCHASE THIS DOCUMENT DEPENDS ON.** The paid 100K tier is live as of
2026-08-21 with 89,287 credits remaining, and a monthly allowance does not carry
over — `CLAUDE.md`'s tier history records ~89,000 paid credits already lost that
way once. The 2025-season historical pull must therefore happen in the CURRENT
billing period or the holdout cannot be tested without paying $59 again.

Estimate, on the same basis as v0.1 section 11 (cost scales with TIMESTAMPS, not
games): ~120 snapshots for a full season including playoffs, matching the 116 /
120 / 121 already on disk for 2022 / 2023 / 2024, × 3 markets × 10 credits
≈ **3,600 credits**, plus ~20 for historical `/events` calls. Roughly 4% of the
remaining balance.

WORTH PULLING IN THE SAME PERIOD, since the allowance dies either way: NFL
2020–2021 T−24 odds (deliberately never purchased — they sit inside v0.1's TUNE
window, so they are useless as a model test but perfectly good as additional
PRICE-dispersion seasons, which is a different question and the one this document
asks). Estimate ~7,200 credits for both.

Live 2026 capture is unchanged from v0.1 section 11: ~155 credits/month, weekly
cadence driven by each game's real kickoff minus 24h, never by a weekday
assumption.

## 11a. ERRATUM, 2026-08-24 — the purchased data cannot execute section 8 yet

Found immediately after the freeze, before any arm was scored. Recorded here in
full rather than quietly fixed, because an erratum a reader cannot see is worse
than the mistake.

**THE GAP.** Section 8 step 5 says to rebuild the de-vigged consensus "from the
last pre-kickoff snapshot". The purchased snapshots sit on a T−24 grid — they
were bought for fb-v0.1, whose question was entirely about the T−24 price — so
for most games the latest available snapshot is nowhere near kickoff:

| distance from last available snapshot to kickoff | share of 2022–24 games |
|---|---|
| within 6 h | 12.9% |
| within 12 h | 12.9% |
| within 24 h | 86.7% |
| median | 17.0 h |

So for ~87% of games there is no closing price in this dataset at all, and the
EV gate — the ONLY gate, per section 5 — cannot be computed for them.

**WHY THE 12.9% CANNOT SIMPLY BE SCORED INSTEAD.** Those are the games whose
kickoff happens to coincide with some other game's T−24 hour, i.e. games in the
busiest scheduling windows. Scoring only them would select on a schedule
property correlated with market attention and liquidity, and would answer a
different question than the one frozen above while looking like an answer to
this one.

**WHAT IS BEING DONE.** Buying one snapshot per distinct kickoff hour, floored
to the hour at kickoff − 1h (the closest a snapshot can sit to a close without
risking an in-play price): 462 new slots across 2022–2025, 13,860 credits.

**WHAT IS NOT CHANGING.** No hypothesis, no gate, no threshold, no book tier, no
staleness window, no minimum-book count, no decision tree. Section 8 already
specified a closing consensus; this purchase makes that instruction executable
rather than altering it. Had it changed any of them after the freeze, the honest
remedy would have been fb-v0.3 with a new test season, and that is still the
remedy for any change that touches the method itself.

**WHOSE MISTAKE.** The sequencing error was mine: the snapshot-to-kickoff
distance was checkable from the 2022–24 files at any point before either the
2025 purchase or the freeze, and checking it after both meant the 2025 pull went
out without its closing half and the freeze stamp went on a spec that could not
yet be run. The corrected purchase covers 2025 as well, so the holdout is not
left short.

## 12. Version history

| version | date (2026) | change |
|---|---|---|
| fb-v0.2 | Aug 24 | pre-registration drafted. Hypothesis, tautology guard, book tiers, staleness rule and gates declared. Prior dispersion look disclosed in section 0. Nothing scored, holdout never loaded, `frozen: 2026-08-24` pending the 2025 odds purchase. |
| fb-v0.2 | Aug 24 | 2025-season odds purchased (123 snapshots, 3,690 credits) via a new schedule-only read that reaches the holdout season's KICKOFF TIMES without claiming the holdout — the alternative was burning the one-shot evaluation on a calendar lookup. Holdout results still locked and still unspent. Purchase revealed the book set is not stable across the window (23 books -> 11; Tier 1 15 -> 6); `fanatics` classified into Tier 1 and the consequences pre-committed in section 6, before the freeze. |
| fb-v0.2 | Aug 24 | holdout lock widened from one hardcoded path to the `asof.SPECS` registry, restoring the freeze gate this document had silently lost. Claim now refused while ANY existing spec reads NOT YET; ledger records the claiming spec and every spec's freeze date. Verified against the refusal AND success paths, the latter against a redirected temp ledger so the real holdout stayed unspent. |

frozen: 2026-08-24
