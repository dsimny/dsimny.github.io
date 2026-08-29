# Model v0.1 — Pre-Registration

**Status: PRE-REGISTRATION COMPLETE. No beliefs emitted yet.** Every parameter
is locked below, before any belief exists. The producer and its scheduler are
NOT yet built — see §11.

> **Model v0.1** is a prospective, non-trained, monotonic favourite–longshot
> calibration experiment using **k = 1.10**, evaluated on **one
> execution-eligible observation per event × moneyline selection at T−24h**,
> with **no backfill** and **no self-performance access**. The market remains the
> null model, and v0.1 bears the burden of demonstrating incremental
> information.

---

## 1. What is being tested

**Hypothesis.** In execution-eligible NFL moneyline markets, the multiplicatively
de-vigged consensus probability carries **favourite–longshot bias**: longshots
are priced above their true frequency and favourites below it. A fixed,
pre-specified monotone correction improves Brier score and log loss against the
uncorrected consensus.

That is the whole claim. It is deliberately narrow, single-directional, and
falsifiable at N = 500.

**Why this and not something cleverer.** The first predictive experiment should
be simple enough that failure is interpretable. If a twelve-feature model fails,
almost nothing has been learned. If *this* fails, exactly one thing has been
learned: that this population does not exhibit the bias in a form this
correction can exploit — and that is a real, publishable-internally result.

## 2. The population

**Execution-eligible NFL moneyline wagers**, as recorded by
`model.formation_attempts`. Not "the market".

Every evaluation output must carry that label. The eligibility ledger (`055`)
exists so the excluded population can be described rather than assumed away, and
the first report must include the exclusion breakdown by reason alongside the
calibration result.

**Known selection effect, stated in advance:** execution-eligible markets have at
least two books, acceptable dispersion, freshness, and a pre-kickoff clock. They
are systematically the *more liquid, more agreed* markets. If favourite–longshot
bias is concentrated in thin markets, this design will under-detect it. That is
a limitation of the population, not a defect of the result.

## 3. Inputs

Exactly what `model_input.market_intelligence` exposes for the wager being
predicted, and nothing else:

- `consensus_probability` — the de-vigged market probability
- market-state context available on the same row (`book_count`, `dispersion`,
  `market_quality`) may be **read** but v0.1 uses **only `consensus_probability`**

No self-knowledge — no outcomes, grading, CLV, standings, or prior performance
(`050`, `055` enforce this by privilege). No non-market features. No raw quotes.

**What the approved surface cannot support, noted so nobody proposes it:**
per-book probabilities are not exposed, so any "weight the sharp books" model is
out of reach without changing Package #4. That is a feature of the boundary, not
an oversight.

## 4. The model

A one-parameter monotone transform of the consensus probability:

```
p_model = p^k / ( p^k + (1-p)^k )        with k fixed in advance
```

Three properties earn it the slot:

1. **It nests the null model exactly.** At `k = 1`, `p_model = p`. Model v0.1 is
   the null model plus one parameter, so the comparison is clean and the
   measurement system already knows how to score it.
2. **`k > 1` sharpens** — pushes favourites up and longshots down, which is the
   direction favourite–longshot bias predicts. `k < 1` would flatten.
3. **It is monotone and bounded**, so it cannot produce a probability outside
   `(0,1)` and cannot reorder two wagers. Any improvement is a *calibration*
   improvement, never a *ranking* one. That keeps the result interpretable.

### 4.1 The parameter — LOCKED

```
MODEL v0.1 PARAMETER

k = 1.10

Status:
NON-FITTED / HYPOTHESIS PARAMETER

Rationale:
The literature supports the hypothesized direction
(favorites understated, longshots overstated), but no
NFL-specific estimate has been identified that maps
directly to this model's transformation.

k = 1.10 therefore does not claim to estimate the
magnitude of favorite-longshot bias.

v0.1 tests direction:
Does a modest monotonic sharpening of the market
probability outperform the market baseline prospectively?
```

**Provenance, stated honestly.** Favourite–longshot bias is well documented in
betting markets generally, and Berkowitz, Depken and Gandar find it in U.S.
fixed-odds **college** football moneylines
([ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1062976916000041)),
with broader cross-market evidence surveyed elsewhere
([Wiley](https://onlinelibrary.wiley.com/doi/10.1111/1467-8586.00174)). Neither
yields an NFL-specific `k` that maps to this transformation, and the pattern is
known to differ across sports and market structures — which is a reason to keep
the prior weak rather than to borrow a number that looks precise.

`1.10` was chosen over `1.25` or `1.50` because this is a **conservative
directional perturbation, not a magnitude claim**.

In log-odds the model is simply:

```
logit(p_v0.1) = 1.10 x logit(p_market)
```

which behaves like this:

| market | v0.1 | shift |
|---|---|---|
| 50.0% | 50.0% | +0.00pp |
| 55.0% | 55.5% | +0.50pp |
| 60.0% | 61.0% | +0.97pp |
| 70.0% | 71.7% | +1.75pp |
| 80.0% | 82.1% | +2.13pp |
| 90.0% | 91.8% | +1.81pp |
| 95.0% | 96.2% | +1.23pp |

Two properties worth noting before the data arrives, so neither is discovered as
a surprise afterwards. `p = 0.5` is a **fixed point** at any `k`, so pick'em
markets contribute nothing to the test. And the shift **peaks around 80% and
shrinks at the extremes** — the correction is largest in the mid-favourite range,
not at the longshot tail where the bias is most often described. If the effect
lives entirely in extreme longshots, this `k` will barely probe it.

**NO SEARCHING OVER `k`.** 1.05, 1.10 and 1.15 are not to be run so the winner
can be selected. v0.1 owns 1.10 whatever happens; a fitted or alternative `k` is
v0.2, with its own pre-registration and its own sample starting from zero.

The model states **no uncertainty interval**. It has no uncertainty model, and
inventing bounds it cannot justify is the unearned confidence Package #5 exists
to prevent. `calibration_status` starts at `NONE`.

## 5. Training boundary

**There is none. v0.1 does not learn.**

`k` is fixed before the first belief is emitted and never updated during the
evaluation. This is not a simplification for convenience — it is what makes the
evaluation confirmatory rather than exploratory. A `k` fitted on the evaluation
sample would guarantee an improvement and prove nothing.

Consequences, binding:

- Refitting `k` produces **Model v0.2**, a new pre-registered version whose
  evaluation starts from zero settled predictions. It does not inherit v0.1's
  sample.
- v0.1's `model_version` is immutable once it has emitted a graded prediction
  (`051`), so a quiet change is not possible.
- Reporting "the best `k` in hindsight" is permitted only as an explicitly
  labelled **exploratory** note. It is never the headline, and never converts a
  failure into a success.

## 6. Success, failure, and the null result

Read once, at N = 500 settled predictions, using `053` unchanged.

| Outcome | Condition | Meaning |
|---|---|---|
| **Adds information** | `standing = ADDS_INFORMATION` (BSS ≥ 0 **and** log-loss improvement ≥ 0) | the correction helped |
| **Parity** | `standing = AT_PARITY` (\|BSS\| ≤ 0.001) | the correction changed nothing detectable |
| **Failure** | `standing = RESEARCH` with BSS < 0 | the correction actively hurt |

Independently, and reported separately:

| | |
|---|---|
| `calibration_status = CALIBRATED` | weighted error ≤ 3pp **and** no populated bin > 7.5pp |
| `calibration_status = DEGRADED` | eligible but outside the band |

**All four combinations are reportable outcomes.** `CALIBRATED` + `RESEARCH` —
well calibrated, adds nothing — is the single most likely result given that the
market beat the previous model 9/9, and the architecture can already express it
(`P5-T21`).

**A failure is a result, not a setback.** The pre-registered response to
`BSS < 0` is to record it and stop, not to search for a variant that rescues it.

## 7. Stopping rule

**No optional stopping.** The standing verdict is read at N = 500 and not before.

Monitoring for *operational* faults — eligibility rates, formation errors, ledger
gaps — is expected and encouraged throughout. Reading `standing` or
`calibration_status` early and acting on it is not, because stopping when the
number looks good is how a null result becomes a positive one.

If the sample cannot reach 500 in a reasonable window, the experiment is
**inconclusive**, and inconclusive is reported as inconclusive.

## 8. What would falsify the hypothesis

- `BSS < 0` at N = 500 — the correction hurt.
- `AT_PARITY` — no detectable effect at this `k`.
- Calibration bins showing the residual bias running the *opposite* way to the
  hypothesis, i.e. the market already over-corrects for favourite–longshot bias
  in this population.

The third is the most informative failure and the bin table is where it becomes
visible, which is one more reason `053` reports per-bin errors rather than only a
weighted summary.

## 9. Open decisions

### 9.1 The value of `k` — **RESOLVED**, see §4.1

`k = 1.10`, non-fitted, testing direction rather than magnitude. Route 3 of the
three originally offered: a deliberately conservative value whose rationale
states plainly that the magnitude was never estimated. Chosen over borrowing
false precision from literature that does not cover NFL moneylines under this
transformation.

### 9.2 Historical reconstruction — settled, recorded here

**Not used for the confirmatory evaluation.** Backfill would require
reconstructing the exact point-in-time `market_intelligence` state, and any
approximation there compromises precisely what Package #5 was built to protect.

Historical data may be used for exploratory work under §9.1 route 2, where the
result is a *parameter chosen in advance* and not a claim about performance.

### 9.3 Cadence — **RESOLVED**

```
MODEL v0.1 FORMATION CADENCE

Unit of observation:
one belief per event x moneyline selection

Target:
T-24h

Eligible capture window:
T-24h +/- 60 minutes

Selection:
use the eligible market observation closest to exactly T-24h

Tie:
earlier observation wins

If no eligible observation exists inside the window:
record NO_WINDOW_CAPTURE;
emit no belief.

No substitution from outside the window.
No later replacement.
No second belief at T-12h, T-2h, or kickoff.
```

`NO_WINDOW_CAPTURE` is kept **distinct from every market eligibility reason**.
A market may have been perfectly executable at T−24h and the ingestion system
simply failed to observe it inside the window. That is a **data-collection
failure, not a market failure**, and conflating the two would corrupt any later
analysis of missingness — the most likely place for a quiet bias to hide.

**Why T−24h.** It keeps continuity with the earlier NFL result, where the market
beat the model 9/9 **at T−24**. Moving the horizon after that result would be
moving the goalposts. It also prevents six hourly observations of one game from
being counted as six independent opportunities.

**On the window width — `±60m`, decided before collection.** `±30m` was not
operationally credible to pre-register: the question is not whether the system
*could* occasionally hit ±30, but whether the capture mechanism can reliably
produce that observation **without selective intervention**. With polling still
manual, it cannot. An honestly pre-registered `±60m` is far better than
accumulating missing games and loosening the rule afterwards to recover them.

### 9.3.1 A tension in "closest to target" worth naming

The selection rule implies choosing among candidate observations *after* the
window closes. Doing that faithfully needs the market state as it stood at each
candidate moment — which is the point-in-time reconstruction §9.2 rules out.

`056` therefore resolves **live, inside the window**: the first run that finds an
eligible observation forms the belief from the surface as it stands then, and
`seconds_from_target` records how close to T−24h it actually got.

At current polling density a ±60m window will usually contain **at most one**
observation, so "closest to target" and "first inside the window" coincide. They
stop coinciding if polling becomes dense — and that choice must be revisited
**before** that happens, not after, or the selection rule silently changes
meaning mid-experiment. Recorded here so it cannot be discovered later as a
convenient reinterpretation.

### 9.4 Time-to-kickoff is part of the immutable record — **LOCKED**

`seconds_to_kickoff` is stamped on every formation attempt and every belief, not
because v0.1 uses it as a feature, but so the sample can **prove** that "T−24"
really was T−24 and did not drift to T−18 or T−6 as polling reality intervened.
A horizon that cannot be audited is not a horizon.

### 9.5 Eligibility precedes model output — **LOCKED, and load-bearing**

**Eligibility is determined before the model's probability is computed.**

The model must never see its proposed probability and then decide whether the
opportunity qualifies. If it could, the eligibility ledger would stop being a
denominator and become a *selection mechanism* — the exact failure `055` was
built to prevent, reintroduced one layer up.

The invariant, which the implementation must enforce structurally rather than by
convention:

```
scheduled attempts  =  formed beliefs  +  ineligible attempts
```

For every **scheduled** v0.1 attempt at the formation horizon, the ledger holds
either an eligible belief or an immutable rejection reason. Never nothing.

This requires something that does not exist yet: a **schedule** created
independently of the model, and a resolution step that checks eligibility first
and only then asks the model for a probability. `055`'s `attempt_belief` takes
the probability as an argument, so today a producer could compute one, look at
it, and simply not call — leaving no trace. See §11.

## 10. What this experiment is not

Not a betting strategy. Not a decision rule. Not a staking plan. v0.1 emits
beliefs and nothing else; whether any delta is worth acting on is Package #6, and
how much to stake is Package #7.

No result from this experiment licenses a wager.

## 11. What is not built yet

The pre-registration is complete. The machinery to honour it is not, and beliefs
must not begin accumulating until it exists — a belief emitted under a rule the
system cannot enforce is not a pre-registered observation.

| Needed | Why |
|---|---|
| `model.v01_probability(p)` | the transform, as a pure immutable function |
| a formation **schedule** | §9.5: scheduled attempts must exist independently of the model, or the denominator is unenforceable |
| a resolution step | evaluates eligibility, then asks the model — in that order |
| `seconds_to_kickoff` | §9.4, stamped on attempts and beliefs |
| the T−24h ± window selector | §9.3, including the closest-to-target and earlier-wins-ties rules |

That is migration `056`, and it is the last thing between this document and the
first real belief.
