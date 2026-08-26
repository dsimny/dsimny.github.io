# Football — pre-registration, fb-v0.3 (the representation hypothesis)

STATUS: **DRAFT, NOT FROZEN.** Nothing in this document may be scored until it
is frozen by commit and this line is replaced with the freeze commit hash. A
pre-registration that can still be edited after a number is seen is not a
pre-registration. See section 12.

AUTHORISED BY: Daniel, 2026-08-26 — "launch first, then fb-v0.3", scope limited
to richer play-by-play features. This document exists now so that the freeze is
ready when the launch work clears; it is deliberately written BEFORE any of the
features below are computed.

---

## 0. What has already been looked at (disclosure)

The single most important section, and it goes first because everything below is
only as honest as this list.

**SEEN, and cannot be un-seen:**

- fb-v0.1's full result. On NFL 2022–2024 the market beat the market-blind model
  in all 9 season × market cells. Moneyline **0.63434 model vs 0.60908 market**
  log loss — a gap of **0.02526** — with spread 0.71308 vs 0.69283 and total
  0.72428 vs 0.69314. `docs/FOOTBALL_RESULT_T24.md`.
- fb-v0.2's full result: best-price EV −1.05%, CLV −0.49, the result reconciling
  to the vig. `docs/FOOTBALL_RESULT_PRICE.md`.
- Two v0.1 grid boundary hits, recorded and deliberately not acted on:
  `hfa = 30` (lowest in grid) and `lam = 100` (highest in grid).
- The VALIDATE seasons 2022–2024 have therefore been scored ONCE ALREADY, for
  v0.1. They are no longer virgin. This is the sharpest limitation of this study
  and section 8 says what is done about it.

**NOT SEEN, and this is what makes the study worth running:**

- None of the six feature families in section 4 has been computed at all. The
  only play-by-play aggregate that exists in this repo is EPA/play
  (`pbp_aggregate.py` reduces every scrimmage play to one EPA sum and a count).
  No pass/rush split, no success rate, no explosiveness, no disruption, no
  red-zone and no drive-level anything has ever been built, fitted, or looked at.
- The 2025 holdout. `data/football/holdout_evaluations.json` does not exist,
  which is the evidence that `asof.claim_holdout()` has never been called.

## 1. The hypothesis, stated so it can fail

v0.1 lost to the de-vigged market by 0.02526 of moneyline log loss. There are
two available explanations and they have opposite consequences.

- **H1 — REPRESENTATION.** The gap is an artefact of how thinly the plays were
  aggregated. The model reduced a season of football to one number per team per
  side, EPA/play, which discards the pass/rush split, discards how a team earns
  its EPA (steady conversion versus a few long gains), discards disruption, and
  discards what happens once a drive reaches scoring range. A richer aggregation
  of the same plays closes some of the gap.
- **H0 — INFORMATION.** The gap is information the market has and we do not:
  injuries, personnel, coaching intent, line-level scouting, and money. No
  re-aggregation of plays already in hand can close it.

**WHY THIS IS A CLEAN TEST, and the reason this particular v0.3 is worth running
before the expensive ones.** Every feature in section 4 is a re-aggregation of
*exactly the plays the v0.1 model already consumed*. No new data source, no new
observation, nothing the model did not already have in raw form. So the
experiment separates the two explanations about as cleanly as this domain
allows: if a richer reading of the same plays does not move the number, the
deficit is not representational, and every future modelling effort should go at
acquiring information the market has rather than at re-reading what we hold.

**A NULL HERE IS A LOAD-BEARING RESULT, not a disappointment.** It would close
off the entire "aggregate the play-by-play harder" family of ideas — which is
where most football modelling effort goes — and point the remaining budget at
the injury and personnel feed that section 5 currently blocks on. State that
now, before the number exists, so that a null cannot later be reframed as a
reason to try a seventh feature family.

## 2. Priority order (unchanged from v0.1 and v0.2, never reversed)

1. Not being wrong.
2. Knowing whether we are wrong.
3. Being right.

## 3. Scope: what changes, and what is frozen so it cannot confound

**THE ONE THING v0.3 CHANGES IS THE FEATURE SET.** Everything else is pinned to
its v0.1 value so that any movement in the score is attributable. This is the
whole design, and it is why the scope is narrow.

FROZEN AT v0.1 VALUES, deliberately, including the parts known to be imperfect:

| frozen | value | why it is not fixed here |
|---|---|---|
| Elo grid | as selected in v0.1, `hfa = 30` boundary hit included | Widening the grid AND adding features means a gain cannot be attributed to either. The boundary hit stays a v0.4 candidate. The 128-cell grid spanned 0.63466–0.66506 on TUNE, so the knobs barely matter. |
| ridge penalty | `lam = 100` boundary hit included | Immaterial: the penalty moves TUNE RMSE by 0.013 points across the whole declared range. |
| discretisation | v0.1 key-number PMF, `KEY_SHRINK = 0.75` | The overstated P(tie) (1.44% modelled vs 0.23% empirical) stays a known limitation. It affects moneyline push pricing only. |
| as-of clocks | v0.1 section 6, unchanged | The leakage guard is not an experimental variable. |
| walk-forward split | v0.1 section 9, unchanged | Comparability with the v0.1 baseline is the entire point of the study. |
| market blending | none — the model stays market-blind through fitting | Same rule as v0.1: no sportsbook number is inspected during parameter fitting. |

## 4. The six feature families

Each is computed for OFFENCE and DEFENCE, opponent-adjusted through the existing
ridge, and each is graded independently (section 9).

| # | family | definition | columns |
|---|---|---|---|
| 1 | **Pass/rush EPA split** | EPA per dropback and EPA per rush, separately | `epa`, `pass`, `rush` |
| 2 | **Success rate** | share of plays with `success = 1`, split pass/rush | `success`, `pass`, `rush` |
| 3 | **Explosiveness** | rate of plays exceeding a fixed yardage threshold, split pass/rush | `yards_gained`, `pass`, `rush` |
| 4 | **Pass-rush disruption** | sack rate and QB-hit rate per dropback | `sack`, `qb_hit`, `pass` |
| 5 | **Red-zone efficiency** | points per trip inside the 20 | `yardline_100`, `fixed_drive`, `fixed_drive_result` |
| 6 | **Points per drive** | points per offensive possession | `fixed_drive`, `fixed_drive_result` |

**COLUMN AVAILABILITY VERIFIED 2026-08-26** against the cached 2024 season: all
of the above are present in nflverse play-by-play (372 columns), across all 15
cached seasons 2010–2024. No acquisition is required for stage 1.

**FAMILY 4 IS NOT "PRESSURE RATE", AND THE NAME MATTERS.** Real pressure rate is
a charting product (PFF, NGS) that this repo does not have and has not paid for.
`sack` and `qb_hit` are the observable residue of pressure, not pressure — they
count the plays where disruption completed and miss every hurry that did not.
Calling it pressure would put a claim in the feature name that the data cannot
support. If pressure proper is ever wanted it is a data acquisition, and it
belongs in its own version with its own point-in-time availability argument.

**THRESHOLDS ARE DECLARED HERE, NOT SEARCHED.** Family 3's explosive thresholds
are fixed now at **15 yards passing / 10 yards rushing** — the conventional
definitions, adopted *because* they are conventional and therefore not chosen by
us against this data. They are not tuned. A version that searches the threshold
is a different study with a different multiple-comparisons budget.

**LEAKAGE, CONCRETELY.** All six families resolve on the **stats clock**
(kickoff + 36h), like every other box-derived quantity. Every new column must be
registered in `data/football/column_availability.json` on that clock before it
is fitted — the as-of engine reads that file rather than a hardcoded list
precisely so that the guard and the data cannot drift apart. A family whose
columns are not registered must fail closed, not fall back.

## 5. What is explicitly OUT of scope, and why

Recorded so that scope creep has to be a decision someone makes in writing.

- **QB and personnel modelling — BLOCKED, not deferred.** It is the largest known
  gap in the model and it stays out until there is a **trustworthy point-in-time
  injury source**. v0.1 section 5 already established that if injury data is not
  wired the model does not adjust for injuries and the cards say so. A
  retrospectively scraped injury report is worse than no injury feature: it is
  leakage wearing a useful disguise, and it would manufacture edge everywhere.
  Note that `column_availability.json` already puts `home_qb_id` / `away_qb_id`
  on the **result** clock — from nflverse we do not know who actually started
  until after the game.
- **Possession Monte Carlo simulation** — a separate future experiment. The
  current closed-form key-number PMF already scores +0.0925 over a plain normal;
  a simulator has to beat that, not merely exist.
- **GBM and matchup interactions (XGBoost, LightGBM, unit-versus-unit crosses)**
  — a separate future experiment, and the one carrying the highest overfit risk.
  It must not ride along inside v0.3, because a gain from a nonlinear learner and
  a gain from richer features would be inseparable.
- **Garbage-time filtering.** Genuinely promising for the NFL and not only for
  college — but it changes the SAMPLE rather than the features, so including it
  would confound every one of the six families. A v0.4 candidate.
- **Weather.** `temp` and `wind` sit on the **result** clock in
  `column_availability.json` — nflverse records them post hoc. Using them at
  T−24 would be leakage. A forecast feed is an acquisition, not a feature.
- **NCAA FBS.** A different market, and v0.1's null is not evidence about it
  either way. Its own pre-registration and its own holdout, if ever.

## 6. Data foundation

| source | use | status |
|---|---|---|
| nflverse play-by-play 2010–2024 | all six families | ON DISK, gitignored, sha256 in `pbp_manifest.json` |
| nflverse play-by-play 2025 | holdout only, stage 2 | free download, not yet fetched |
| The Odds API historical, NFL 2022–24 | VALIDATE market comparison | ON DISK — 357 Tier-A snapshots, backed up per the hub manifest |
| The Odds API historical, NFL 2025 | HOLDOUT market comparison | **NOT PURCHASED.** Stage 2 only. See section 10. |

The manifest matters more than usual here: the six families are recomputed from
the same cached seasons the v0.1 baseline was fitted on, so a season silently
rebuilt upstream would move the baseline and the treatment together and look
like nothing had happened. Verify hashes before fitting.

## 7. Validation plan (unchanged from v0.1, and that is the point)

| seasons | role |
|---|---|
| 2010–2014 | BURN-IN. Never scored. |
| 2015–2021 | TUNE. Every selection decision happens here and is reported as in-sample. |
| 2022–2024 | VALIDATE. Scored ONCE for v0.3. |
| 2025 | HOLDOUT. Stage 2 only, exactly once, via `asof.claim_holdout()`. |
| 2026 | prospective forward test, live, at 0 units. |

Walk-forward only. Never random splits.

## 8. The contamination this study cannot escape, stated plainly

**VALIDATE 2022–2024 HAS BEEN SCORED ONCE ALREADY, FOR v0.1.** Its numbers are
in section 0 and in a published document. So the v0.3 VALIDATE read is not a
virgin read in the sense v0.1's was, and no amount of procedure makes it one.

What is actually done about it, rather than what would sound reassuring:

1. **All six families are declared in section 4 before any is computed.** They
   come from football first principles and from the directive that authorised
   this study — not from inspecting which residuals v0.1 got wrong.
2. **No family may be added, removed, redefined or re-thresholded after any
   VALIDATE number is seen.** That is what the freeze in section 12 buys.
3. **Selection happens on TUNE only.** VALIDATE is scored once and reported once.
4. **The 2025 holdout remains the only genuinely clean test**, and section 10
   makes reaching it conditional rather than automatic.

Anyone reading a v0.3 result should discount the VALIDATE number accordingly and
weight the holdout, if it is ever spent, far more heavily.

## 9. Method — how a family earns its place

Seven fits, fixed in advance. This is not a search over subsets.

- **Six solo fits.** Baseline (v0.1 exactly) plus family *i* alone, for each
  *i* in 1..6. This is the "independently gradeable against the current
  baseline" requirement, and it is why the count is six rather than one.
- **One combined fit.** Baseline plus every family that passed its solo gate,
  fitted once, after the solo gates are resolved.

**NO STEPWISE SEARCH.** There are 64 subsets of six families, and trying them is
how a null becomes a false positive. Six solo tests and one combination, with the
combination's membership decided by the solo results rather than by its own
score.

**SOLO GATE, pre-committed.** Family *i* enters the combined model if and only
if, on VALIDATE moneyline log loss against the baseline:

- the paired difference is **negative** (an improvement), AND
- its point estimate is at least **0.002** — about 8% of the 0.02526 market gap,
  set as the smallest movement worth carrying a feature for, AND
- a **90% paired bootstrap CI over games excludes zero**, AND
- the sign is **consistent across all three VALIDATE seasons** — a family that
  helps in 2022 and hurts in 2024 is noise wearing a trend.

**MULTIPLE COMPARISONS.** Six solo tests, so the bootstrap CIs are Holm-adjusted
at family-wise alpha = 0.10. Declared now, because declaring it after seeing
which families passed is the same as not declaring it at all.

**REPORTED REGARDLESS:** every family's solo delta, passing or failing, on both
TUNE and VALIDATE, each labelled as such. A family that fails is published as
having failed. Brier score and calibration accompany log loss throughout, and
spread and total PMF log scores are reported alongside moneyline — though the
gates are set on moneyline, because that is where v0.1's cleanest comparison
sits.

## 10. Pre-committed gates — two stages, the cheap one guarding the expensive one

**STAGE 1 — FREE. Runs on data already on disk. No purchase, no holdout.**

The question: do richer features improve football forecasting at all?

- **Zero families pass their solo gate** → H0. Re-aggregating the plays we
  already hold does not help. Report it, publish it, STOP. Do not invent a
  seventh family. The 2025 holdout stays unspent, and the finding redirects
  effort at the injury and personnel acquisition that section 5 blocks on.
- **Some families pass and the combined model improves on baseline** → proceed
  to the stage-2 gate below. Note that this is a claim about the BASELINE and
  not about the market, and no copy may say otherwise.

**STAGE 2 GATE — the one that authorises spending money and the holdout.**

The combined model must close at least **one third of the 0.02526 moneyline gap
on VALIDATE** — that is, reach a VALIDATE moneyline log loss of **0.62592 or
better** — before the 2025 odds purchase is authorised.

WHY A THIRD, AND WHY FIXED NOW. It is a judgement, and judgements of this kind
are worth exactly as much as their timing, so it is made before the number
exists. The reasoning: closing less than a third of a gap on a contaminated
VALIDATE set is not a plausible precursor to closing the whole of it on unseen
data, and buying a season of odds in order to confirm a loss is precisely the
spending that the freeze discipline exists to prevent. A model that clears the
bar has earned the one-shot test; one that does not has been answered for free.

- **Gate cleared** → purchase NFL 2025 T−24 odds (estimate ~3,600 credits, on
  the same timestamps-not-games basis as v0.2 section 11), fetch nflverse 2025,
  call `asof.claim_holdout(purpose="fb-v0.3")`, score ONCE, and publish whatever
  it says. A failed holdout is never retuned in place.
- **Gate not cleared** → report the partial improvement honestly, as an
  improvement over our own baseline that does not approach the market. Publish.
  Stop. The holdout stays unspent.

**NO FORWARD REVIEW DATE IS SET BY THIS DOCUMENT.** v0.1's 2026-11-17 review
stands on its own terms and is not moved, extended, or reinterpreted by anything
here (House Rule 9). v0.3 is a historical study; it books no plays.

## 11. What a v0.3 pass would and would not license

- It would **not** authorise staking. Football ships at 0 units through the
  proving window regardless, per pipeline spec section 6.
- It would **not** change the shipped product's selection rule, book tiers,
  staleness window or corroboration guard. `docs/FOOTBALL_PIPELINE.md` section 7
  governs those, and only a COMPLETED v0.3 may revisit them — completed meaning
  holdout-scored and published, not stage-1-passed.
- It would **not** license expectation language in copy. "our edge", "+EV play"
  and "the model likes" stay barred until a holdout says otherwise.
- It **would** license writing fb-v0.4 against whichever of the blocked
  components section 5 lists, with the v0.3 result as its baseline.

## 12. Freeze protocol

This document is DRAFT until it is committed with its status line replaced by a
freeze commit hash, and no number from section 9 may be computed before then.
After the freeze: no edit in place, ever. A changed threshold, an added family, a
redefined explosive yardage, a moved gate — each is fb-v0.4 with its own clean
test season, exactly as v0.1 became v0.2 and v0.2 became this.

## 13. Version history

- **fb-v0.3, drafted 2026-08-26.** The representation hypothesis. Scope set by
  Daniel to richer play-by-play features only, with QB and personnel modelling,
  possession simulation, and GBM/matchup interactions explicitly isolated as
  separate future experiments. Not frozen.
