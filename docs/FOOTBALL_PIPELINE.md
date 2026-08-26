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

- at least **5 eligible books** quote the moneyline — ANY tier, see below, and
- every quote used is within **15 minutes** of the snapshot (staleness filter,
  carried over from `FOOTBALL_PREREG_V02.md` section 6), and
- the game has a resolvable kickoff time and both teams resolve to known ids, and
- the recommended price is available at a **Tier-1 (US-regulated) book**,
  corroborated by at least 2 Tier-1 books (section 4 step 2).

**MEASUREMENT AND ACTION HAVE DIFFERENT BOOK REQUIREMENTS, and conflating them
was a real defect caught before launch.** They are different jobs:

- The **de-vigged consensus** is a MEASUREMENT of where the market sits. More
  books make it more accurate, and an offshore book's price is perfectly good
  market information. So consensus is built from every eligible book, any tier.
- The **recommended price** is an ACTION. It has to be a number a reader can
  actually take. `FOOTBALL_PREREG_V02.md` section 6 already barred Tier 2 from
  satisfying a gate for exactly this reason, and the first live run picked
  `bovada` — a price most of the audience cannot use, sitting next to House
  Rule 5's legal footer.

Restricting BOTH to Tier 1 would have been the obvious fix and the wrong one:
only six Tier-1 books quote football at all, so a 5-book Tier-1 floor cuts NCAAF
coverage from 42 games to 27 and makes NFL coverage depend on all five available
books quoting every game. Measured on the first live captures, splitting the
requirement keeps **42 of 42 NCAAF and 16 of 16 NFL** covered games while
guaranteeing every recommendation is takeable and corroborated.

A game failing any of these is listed as **NO MARKET**, never silently dropped.
Section 6's rule applies: if the share excluded exceeds 25% in a week, the page
says "manual review", not "covered".

NCAA FBS is included on the same terms, and the 5-book floor is what keeps the
Saturday slate honest — thin-market games are named and skipped rather than
written up on two quotes.

## 3a. Season type — capture preseason, NEVER grade it

`FOOTBALL_PREREG_V02.md`'s predecessor settled this for the model and it carries
over to the product unchanged: **preseason tests the PIPELINE and nothing else.**
Preseason is a different population, not a smaller sample of the same one.
Playing time is a coaching decision, the market prices exactly that variable, and
we do not observe it — so a preseason result is uninformative by construction.

The failure this prevents is specific and quiet. ESPN's NFL scoreboard returns
preseason games alongside regular-season ones, and nothing in the capture path
distinguishes them. Wire NFL grading without this filter and preseason entries
book into the football ledger looking exactly like real ones. House Rule 1 makes
that ledger permanent, which is precisely why nothing uninformative may enter it.

**ALLOWLIST, NOT BLOCKLIST.** The gradeable set is named positively:

- ESPN `event["season"]["type"] == 2` (`slug: "regular-season"`) — GRADEABLE.
- Everything else — preseason, all-star, anything unrecognised — is REFUSED.

Verified 2026-08-25 against the live endpoint: the 2026-09-10 NFL opener returns
`{'year': 2026, 'type': 2, 'slug': 'regular-season'}`. An allowlist is used
because a blocklist on `type == 1` would silently grade any NEW type value ESPN
introduces, and the direction of that error is a corrupted permanent ledger.
Postseason (`type 3`) is deliberately NOT in the allowlist yet: it needs its own
decision about whether a neutral-site, layoff-affected market is the same product,
and that decision has not been made.

**CAPTURE preseason anyway, and label it.** A preseason capture is a free
rehearsal of the whole capture path against real games and real prices — exactly
what the pre-registration says preseason is for. `capture_schedule.py` and
`fetch_odds.py` therefore do NOT filter by season type; only grading does. The
split is: capture everything, grade only what is informative.

WHERE THIS IS ENFORCED. `grade_football.py` refuses any game whose season type is
not on the allowlist, by the same "recorded, never silently dropped" rule as the
rest of section 3 — the audit line reads NOT REGULAR SEASON. It is unimplemented
while grading is NCAAF-only; it must land in the same change that wires NFL
grading, and before the 2026-09-09 opener.

## 4. THE SELECTION RULE (precommitted)

Ranked in this order, computed only from layer 1:

**Step 0 — the slate is a WEEK, not a day, and ONE slate spans BOTH sports.**

Football is a weekly sport (`FOOTBALL_PREREG_V02.md` section 11 makes the same
point about capture windows). Thursday and Monday are single-game days, so a
per-day slate would leave no free play on half the calendar — measured: 80 of
162 game-days had only one qualifier, against 3 of 65 weeks.

NFL AND NCAA FBS RANK IN ONE POOL (decided 2026-08-26, fp-v0.2). The spec did
not previously say, and `grade_football.py`'s `--sport` argument implied two
separate slates and therefore two premium plays a week. One play a week is the
product; two was an implementation detail about to become a product decision by
accident.

**THE MEASURED CONSEQUENCE, recorded before week 1 so nobody "fixes" it later.**
Combining the pools does NOT usually hand the play to the NFL. It usually hands
it to college, and the reason is volume, not quality. Measured on the
2026-08-25 live capture (57 covered games, coverage filter and corroboration
guard applied, effective overround at best Tier-1 price, in percentage points):

| | n covered | min | median | p90 |
|---|---:|---:|---:|---:|
| NFL | 15 | 1.22 | 2.45 | 3.16 |
| NCAA FBS | 42 | **0.63** | 2.70 | 3.95 |

The typical college market is LOOSER than the typical NFL market — median 2.70
against 2.45. But college fields roughly three times the games, and rank 1 is a
minimum, not a median. Three times the draws produces a better tail, so the
tightest game on a combined board is usually a college game: NCAAF took rank 1
on this board and 15 of the tightest 20.

So expect the premium play to be college most weeks, and often a low-profile
one — the tightest market on this board was Wyoming @ Colorado State. **That is
the rule working, not failing.** Step 1 ranks by the toll you pay to play, and
it does not know or care which sport is on television. Copy must set that
expectation up front; a reader who bought expecting an NFL play every week will
otherwise read a correct result as a broken product.

NOT CHARACTERISED HISTORICALLY, and this is a real limit rather than an
oversight: section 4a's 2022–2024 table is NFL-only, because the purchased
historical odds are NFL-only. There is no NCAA FBS price history on disk to
re-run, so the combined rule's season-long behaviour will be OBSERVED LIVE
rather than known in advance. The single capture above is one board, not a
season. Publish that caveat with the first week's numbers rather than after
someone notices.

WHY THIS IS NOT THE THING SECTION 7 FORBIDS. Section 7 and House Rule 9 bar
moving the rule IN RESPONSE TO RESULTS. No football result exists — the ledger
is empty, week 1 has not been played, and this decision is about what the
product IS, not about how it performed. It ships as a version bump with its
reasoning and its measured consequence attached, which is the process House
Rule 9 prescribes. After week 1 books its first entry, this door closes.

**Step 0b — WHEN the play is chosen (fp-v0.3, decided 2026-08-26).**

Each game is evaluated at ITS OWN T−24, so the week's full field never exists at
one moment: a Sunday NFL game's T−24 lands Saturday, after most of the college
slate has kicked off. **There is no instant at which every T−24 exists and no
game has started.** That is arithmetic, not a defect, and it follows directly
from step 0's single pool.

THE RULE, in two parts:

1. **COMMIT PER GAME, at its own T−24.** The first time a game becomes evaluable
   its layer-1 block is fingerprinted and the hash published, append-only. That
   evaluation is then frozen: a later capture never revises it. This is what
   makes the eventual play a commitment rather than a running opinion.
2. **CHOOSE PER WEEK, at a precommitted DECISION MOMENT D.** At D, rank every
   game committed so far that has not kicked off, and assign premium and free
   per step 4. One play per week, chosen once.

**D = SATURDAY 14:00 US/EASTERN**, stated in Eastern rather than UTC because the
rest of the project is (and because a UTC constant would silently shift the
window by an hour when DST ends mid-season).

THE CONSEQUENCE, stated because it is not obvious and it constrains the product.
A game is eligible at D only if its T−24 has passed (kickoff − 24h ≤ D) and it
has not started (kickoff > D). Those two conditions are one condition:

    D < kickoff ≤ D + 24h

**The eligible pool is always the next 24 hours of football, and choosing D is
choosing which 24 hours.** Saturday 2pm ET was chosen because its window —
Saturday 2pm ET through Sunday 2pm ET — is the one that genuinely spans both
sports: the bulk of the college Saturday slate (afternoon and evening) plus the
NFL's Sunday 1pm ET block, which is its largest. It is the window that makes
step 0's combined pool mean something rather than quietly reverting to
one-sport-per-week.

WHAT IT EXCLUDES, and this is accepted rather than hidden: Saturday's noon-ET
college games, and the NFL's 4pm ET, Sunday-night, Thursday and Monday games.
Those still get full layer-2 coverage and still appear on the board; they are
simply not eligible to BE the one committed play. A product that promised
otherwise would have to publish a play after its own kickoff.

**Step 1 — market quality.** For each covered game compute the EFFECTIVE
OVERROUND at best prices:

    eff_overround = implied(best_price_home) + implied(best_price_away) − 1

This is the toll you pay to play that game at the best numbers available. Rank
games ascending. Lowest = tightest market = cleanest play.

**Step 2 — corroboration guard, Tier 1 only.** The best price considered is the
best price at a **Tier-1 book**. Discard any game whose chosen side's best
Tier-1 price is offered by fewer than **2 Tier-1 books** at or within 1
implied-probability point of it. Offshore prices inform the consensus and may be
mentioned in the writeup as market colour; they are never the recommendation.

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
| fp-v0.2 | Aug 26 | Section 4 step 0: ONE combined NFL + NCAA FBS slate, so one premium play a week rather than one per sport. Measured consequence recorded — college takes rank 1 most weeks on volume, not quality — along with the limit that no NCAA FBS price history exists to characterise it on. Decided before week 1 and before any football result existed; no rule was moved in response to a result. |
| fp-v0.3 | Aug 26 | Section 4 step 0b: commit per game at its own T−24 (append-only, evaluation frozen), choose per week at a precommitted decision moment D = Saturday 14:00 US/Eastern. Closes the gap that fp-v0.2 opened — the week's full field never exists at one instant, so without a stated D the play could silently change after being committed. Records the consequence that the eligible pool is always `D < kickoff ≤ D+24h`, and names what that excludes. Still before week 1 and before any result. |
