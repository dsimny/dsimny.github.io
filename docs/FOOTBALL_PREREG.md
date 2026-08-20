# Football (NFL) — pre-registration, fb-v0.1

STATUS: DRAFT, not yet frozen. Nothing in this document has been run against
data. It is written first on purpose: every threshold below is chosen before
seeing a single football result, so that no number in it can be a post-hoc
rationalisation. Freeze = this file committed with `frozen:` set to a date and
the version-history row added. After the freeze, changes ship as fb-v0.2 with a
new clean test season, never as an edit in place.

frozen: NOT YET

## 1. What this is, and what it is not

A second sport for Open Ledger Sports, built on the same rig (commit/reveal,
append-only ledger, CLV capture, grading, circuit breakers, house rules) and a
completely new engine. Nothing from the MLB Monte Carlo transfers: that model is
wOBA-, pitcher- and run-distribution-shaped, and football has none of those.

It ships as a PAPER TRACK at 0 units. It does not enter `data/ledger.json`, it
does not size stakes, and it does not produce a bet recommendation. The question
it exists to answer is narrow and falsifiable:

> Does a market-blind football model add anything at the price we could actually
> have taken — the T−24 line — over and above the market itself?

The honest prior is NO. The prior research on this framework found the market
beat every pure model in every season tested at the closing line. This build is
not an attempt to overturn that; it is an attempt to measure it correctly at the
one moment the earlier work could not measure, and to report the answer either
way. "NO QUALIFYING SIGNAL" is a result, not a failure.

## 2. Priority order (never reversed)

DATA INTEGRITY → NO LEAKAGE → PROBABILITY ACCURACY → CALIBRATION
→ CLOSING-LINE VALUE → EXPECTED VALUE → (staking is out of scope for v0.1)

A 53% model that beats the closing line is worth more than a 60% model that got
there by variance. Any conflict between two levels is resolved in favour of the
earlier one, always.

## 3. Scope of v0.1

IN: NFL regular season, 2026. Markets: spread, total, moneyline.

OUT: NCAA FBS — phase 2. Separate data source, separate rating dynamics, its own
pre-registration and its own holdout. It is NOT added by widening this one.

OUT: player props, live betting, parlays, and any staking or sizing whatsoever.

## 4. The preseason decision (settled 2026-08-20)

Preseason games are used to test the PIPELINE and for nothing else.

WHY NOT THE MODEL. Preseason is a different population, not a smaller sample of
the same one. Playing time is a coaching decision — starters take 0–15 snaps and
which ones sit is unobservable to any rating fitted on regular-season football.
Worse, the preseason market prices exactly that variable: lines move on
playing-time reports, the one input the model structurally does not have. A test
where we are systematically on the wrong side of the only live variable cannot
produce an informative pass OR an informative fail, so it cannot be a gate.

WHAT PRESEASON IS FOR. Real gradeable games with real prices on a small slate:

- team-id mapping across nflverse / ESPN / The Odds API
- the as-of clock for a weekly sport (kickoff-relative, not morning-relative)
- football settlement mechanics: spread and total PUSH on the number, and the
  moneyline ALSO pushes, because NFL regular-season games can end tied (13 ties
  in the 4,363 games since 2010, 0.30%). "The moneyline cannot push" is true in
  baseball and false here; it was written into an earlier draft of this document
  from MLB intuition and caught by the settlement selftest. Every push books at 0.
- CLV capture at a T−24 that lands mid-week
- measured Odds API credit burn, replacing an estimate with a reading

RULE. Preseason rows are written to `data/football_pipeline_test.json` and
NOWHERE else. They never touch `ledger.json`, `daily_ledger.json`,
`totals_ledger.json`, `watchlist.json`, or the football paper ledger. That file
is labelled a plumbing test in its own header and is deleted or archived, never
promoted. House Rule 1 makes the real ledger permanent; that is exactly why
nothing uninformative is allowed into it.

## 5. Data foundation

| source | use | key | notes |
|---|---|---|---|
| nflverse | 2015–2025 games, plays, schedules | none | historical fit + validation |
| ESPN public API | 2026 live schedule, scores, status | none | grading + slate |
| The Odds API | live prices | ODDS_API_KEY | `americanfootball_nfl`, `americanfootball_nfl_preseason` |
| The Odds API historical | T−24 backtest 2020–2024 | same | 10× credits per market per region |

RULES.

- Nothing is fabricated or inferred. Missing data is recorded as MISSING and the
  affected check reads "manual review", never "passed" (House Rule 4).
- Every raw artifact is stored content-addressed with a manifest.
- Injuries: if 2026 injury data is not wired, the model does not adjust for
  injuries and the cards SAY SO. It is never silently treated as "healthy".

## 6. As-of semantics (the leakage guard)

Three clocks, never blurred:

- `event_time` — when the thing happened
- `available_at` — when we could first have known it
- `ingested_at` — when we actually stored it

Football-specific availability, pre-committed:

- game result: kickoff + 4h (games end; no 12h lag needed)
- box / advanced stats: kickoff + 36h
- injury report: the NFL practice-report schedule (Wed/Thu/Fri), taken at
  published time, never backfilled to earlier in the week
- the model's decision moment: T−24 before that game's own kickoff

A fail-closed guard raises if any input to a T−24 feature carries an
`available_at` later than that game's T−24. It raises. It does not warn and
continue.

## 7. Model spec (market-blind — frozen before any price is read)

RATINGS, two independent families, both fitted on football data only. No
sportsbook number is inspected at any point during parameter fitting.

- **Elo** — tuned on a pre-registered grid (k, HFA, season-carryover regression
  to mean, margin-of-victory multiplier). The carryover coefficient is a grid
  parameter, not a judgement call made in September.
- **Opponent-adjusted ridge efficiency** — offense/defense effects,
  possession-weighted, mean-zero centered.

GAME MODEL. Ridge over rating differences plus a site indicator, producing a
predicted margin and a predicted total.

UNCERTAINTY. From walk-forward OUT-OF-FOLD residuals only. In-sample residuals
are never used; they understate spread and would manufacture edge everywhere.

DISCRETISATION. Margin and total are converted to discrete probability mass
functions with a key-number variant. Key-number weights for |margin| of 3, 7 and
0 are derived from historical football margins ALONE — never fitted to make the
model agree or disagree with a line.

## 8. Market comparison (only after the model is frozen)

TIERING, enforced in code:

- **Tier A** — timestamped first-party snapshot. Eligible for CLV.
- **Tier B** — provider data with incomplete timing. Diagnostics only.
- **Tier C** — reference-only. STRUCTURALLY incapable of producing CLV, and the
  code refuses to compute CLV from it rather than computing a number that would
  look fine and mean nothing.

De-vig proportionally. Build the consensus from OBSERVED lines only — never
interpolate a line nobody offered. Expected value is settlement-aware: pushes
are valued at zero, not as half-wins, and the push mass at 3 and 7 is material
enough that ignoring it is a real error, not a rounding one. Measured on our own
2010-2025 games: |margin| = 3 occurs in 14.60% of games and |margin| = 7 in
8.71%, against 5.02% at 4 and 5.07% at 10. Those two spikes are the whole reason
the key-number variant exists, and they are derived from football results alone.

## 9. Validation plan

| seasons | role |
|---|---|
| 2015–2024 | development. Fit, tune, argue here. Walk-forward only — never random splits, which leak the future through the season. |
| 2025 | HOLDOUT. Locked until this document is frozen and committed. Evaluated EXACTLY ONCE. A failed holdout is never retuned in place; it becomes fb-v0.2 with its own clean test season. |
| 2026 | prospective forward test, live, at 0 units. |

## 10. Pre-committed gates and the review date

Nothing is staked in v0.1, so the only gate that matters is the one deciding
whether this graduates to a staked strategy at all. It is set now.

**REVIEW DATE: 2026-11-17**, after 10 completed NFL weeks (~155 games). On that
date only. Never early, never in response to a streak in either direction — that
is precisely when the temptation peaks (House Rule 9).

READ: `data/football_paper_ledger.json` aggregates — average CLV in points,
beat-close %, calibration (reliability curve + Brier), record.

DECISION TREE, pre-committed:

- CLV convincingly positive AND calibration holds → propose a staked strategy as
  fb-v0.2, with a gate study and its own holdout. Not before.
- CLV ≈ 0 → the T−24 football market is efficient for this model. Report it,
  publish it, and the honest move is to stop — not to add features until
  something looks like it works.
- CLV negative → the model is worse than the market at the moment of the bet.
  Report that too, on the site, in the Morning Line.

A 10-week sample cannot settle ROI and this document does not pretend it can. It
CAN move CLV meaningfully, which is why CLV is the read and record is not.

## 11. Odds budget

Football is a WEEKLY sport; the MLB daily cadence is the wrong shape. Captures
cluster on game days (Thu / Sun / Mon, plus late-season Sat), with the T−24
capture scheduled per game rather than per morning.

The cadence must be driven by the actual schedule, never by a hardcoded weekday
pattern. 2026 is the proof: Week 1 opens on WEDNESDAY Sept 9 (Seahawks–Patriots),
there is a Thursday Sept 10 game in Australia, and international kickoffs land at
hours no US-weekday assumption survives. The scheduler reads each game's real
kickoff and subtracts 24h. Any capture window derived from "it's Sunday" is a bug.

Live NFL projection: 3 markets × 1 region (us) × ~12 calls/week
= ~36 credits/week ≈ **155/month**, on top of MLB's ~250/month through October.

Historical backtest 2020–2024 (one-time): a snapshot returns the whole slate, so
cost scales with TIMESTAMPS, not games. ~6 timestamps/week × 18 weeks × 5
seasons × 3 markets × 10 credits ≈ **16,200 credits**, plus ~540 for historical
`/events` calls at 1 credit each.

This exceeds the 500-credit free tier and trips the pre-committed upgrade rule #2
(a market with a real product surface pushing projected spend over 450). The
upgrade is therefore rule-driven, not preference-driven, and
`data/odds_credits.json` remains the evidence.

## 12. Football engine version history

| version | date (2026) | change |
|---|---|---|
| fb-v0.1 | Aug 20 | pre-registration drafted; nothing fitted, nothing run |
