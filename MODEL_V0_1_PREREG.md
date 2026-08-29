# Model v0.1 — Pre-Registration

**Status: DRAFT. No code, no beliefs emitted.** Written before the first live
belief, as `PACKAGE5_PREREG.md` was written before the measurement system. One
decision (§9.1) is genuinely open and I should not settle it.

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

### 9.1 The value of `k` — **DECISION REQUIRED, and not mine to invent**

`k` must be fixed in advance from a source outside this evaluation. I am not
going to pick a number from intuition and present it as pre-registered: a
plausible-looking constant with no provenance is exactly the kind of unearned
precision the rest of this contract is built to prevent, and I do not have a
citation I can verify from here.

Three defensible routes, in my order of preference:

1. **A published estimate** of favourite–longshot bias in NFL moneyline markets,
   cited by name in this document before the first belief.
2. **A separate exploratory sample** — an earlier capture window, or a different
   sport — fitted once, then frozen and **never** used for evaluation. This
   requires the historical reconstruction problem in §9.2 to be solved for the
   exploratory data only, where approximation is acceptable because nothing
   confirmatory rests on it.
3. **A deliberately conservative fixed value** with the rationale stated as "a
   small correction in the hypothesised direction, chosen to be clearly
   non-fitted" — e.g. `k = 1.05`. Weakest scientifically, but honest, and it
   makes the experiment a test of *direction* rather than of magnitude.

Route 3 is the fastest path to a live experiment and I would take it if the
alternative is delay, provided the document says plainly that the magnitude was
not estimated.

### 9.2 Historical reconstruction — settled, recorded here

**Not used for the confirmatory evaluation.** Backfill would require
reconstructing the exact point-in-time `market_intelligence` state, and any
approximation there compromises precisely what Package #5 was built to protect.

Historical data may be used for exploratory work under §9.1 route 2, where the
result is a *parameter chosen in advance* and not a claim about performance.

### 9.3 Cadence

How often the producer runs, and at what point before kickoff, is unsettled. It
matters because it determines both the sample size and the market state being
predicted from — a belief formed at T−72h and one at T−2h are different
experiments. **Recommendation:** one belief per wager per pre-registered time
window, so the sample is not dominated by whichever games were polled most.

## 10. What this experiment is not

Not a betting strategy. Not a decision rule. Not a staking plan. v0.1 emits
beliefs and nothing else; whether any delta is worth acting on is Package #6, and
how much to stake is Package #7.

No result from this experiment licenses a wager.
