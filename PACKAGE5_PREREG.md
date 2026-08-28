# Package #5 — Model Layer — Pre-Registration

**Status: CONTRACT LOCKED IN FULL, no code written.** Written before
implementation, as `PACKAGE4_PREREG.md` was. All seven decisions settled.
Baseline commit `84e980b`; this revision locks decisions 6 and 7 and defines
Phase A.

---

## 1. What Package #5 is

Package #4 answers *what the market says*. Package #5 is the first layer
permitted to answer *what we believe*, and the first layer in the system whose
output cannot be checked against anything observable at the moment it is
produced.

That asymmetry is the entire design problem.

| | Package #4 | Package #5 |
|---|---|---|
| Nature | **observed** | **inferred** |
| Falsifiable | immediately, against the feed | slowly, against outcomes |
| Wrong how | a bug | a bug **or** an honest miss |
| Authority | market truth | an opinion with provenance |

Every mechanism below exists to keep a reader from confusing the right-hand
column for the left.

## 2. The prior that shapes this package

The NFL track was shelved on 2026-08-20 because **the market beat the model 9
times out of 9 at T−24**. That is the most informative result this project has
produced about modelling, and Package #5 is built as though it is the expected
outcome rather than a past embarrassment.

**The market is the incumbent model.** Package #5 does not begin from an
assumption that it can be beaten. It begins with the burden of proving that we
add information.

If the eventual finding is *the market is still better*, that is a **successful
scientific result** for Open Ledger. Nothing in this architecture may pressure
anyone to explain it away — which is why §6 makes "well calibrated" and "better
than the market" two separate, separately-recorded claims.

## 3. What a model may read

**Market data: `market_intelligence` only.** Package #4 is the one definition of
observed market reality. A model must not be able to quietly reconstruct
consensus probabilities using different rounding, de-vigging, tie-breaking,
freshness or book-selection rules.

Enforced by **privilege, not convention**, in the `olp_model` role.

Two schemas, and the split is load-bearing:

| Schema | Holds |
|---|---|
| `model_input` | what the model may **see** — read-through of the Package #4 surface |
| `model` | what the model **produces** |

The read-through exists because Package #4's views are `security_invoker`, so a
grant on `market_intelligence` alone would still require the caller to hold
`SELECT` on `market_snapshots` — handing the model the raw quotes this contract
forbids. **A nested owner-privileged view does not fix that**: `security_invoker`
checks the *original* invoker, not an intermediate view's owner. The bridge is a
`SECURITY DEFINER` function, the same mechanism Package #1 uses for its RPCs.

The disjoint-column rule in §7 governs `model` only. `model_input` is market
truth passed through unchanged, so its columns are deliberately identical —
which is exactly why outputs live in a different schema from inputs.

| Object | Access |
|---|---|
| `market_intelligence` | `SELECT` |
| `market_snapshots` | **denied** |
| `canonical_market` | **denied** |
| `executable_market` | **denied** |
| `market_movement` | **denied** |
| any write to Packages #1–#4 | **denied** |

Also readable: `ticket_closing_line_value`, `ticket_effective_results`,
`events`, `event_schedule_history`, and the model's own prior outputs.

**Not readable, ever:** anything that did not exist at the moment being
predicted (§8), and — **LOCKED for v1** — anything about the model's own
performance.

### 3.1 The model cannot see the grading system

| Allowed | Denied |
|---|---|
| `market_intelligence` at formation | settled outcomes |
| pre-declared external / model features | grading tables |
| model and version configuration | calibration results |
| | CLV history |
| | model standings |
| | prior model performance |

**The grading system can see the model. The model cannot see the grading
system.**

A model reading its own history opens a feedback channel that survives good
intentions. Even without explicitly training on results, features and prompts
begin adapting to past performance, and the prospective test stops being as
clean as it looks while continuing to look clean.

If an adaptive or online-learning model is wanted later, it is a **new
pre-registered model class with its own contract** — not a quiet expansion of
v1's permissions.

**LOCKED — market-only inputs for v1.** Ratings, injuries, weather, rest and
travel are out. Adding a feature source means adding a second provenance and
staleness regime, and the football track's failure was not caused by a shortage
of features. Features get their own package with Package #3's ingestion
discipline, not a side door here.

## 4. What a model may emit

One row per `(event_id, market_type, selection, line)` — the same key as
Package #4, so the two always join without interpretation.

**Primitives only.** The belief table stores facts about the belief and the
market it was formed against; anything comparative is derived downstream.

| Field | Meaning |
|---|---|
| `model_probability` | belief that this selection wins |
| `lower_bound`, `upper_bound` | uncertainty interval |
| `uncertainty_method` | how the interval was produced — bootstrap, conformal, analytic |
| `market_probability_at_formation` | the de-vigged market probability this belief was formed against |
| `market_binding_snapshot_id` | the market observation it was formed against |
| `model_id`, `model_version`, `feature_version` | which model, which frozen version, which input transformation |
| `formed_at` | when the claim was made |
| `inputs_hash` | hash of the exact inputs consumed |
| `calibration_status`, `standing` | §6 |

**Derived in an analytical view, never stored on the belief:**

```
probability_delta = model_probability - market_probability_at_formation
```

**No `edge` column.** A field named `edge` acquires decision semantics by
gravity — someone will eventually filter on it, then size on it. `edge` is a
judgement about whether a delta is worth acting on, and that judgement belongs
to Package #6.

**May NOT emit:**

- **A price.** Prices are market objects. A model emitting `-138` invites
  someone to read it as a quote. For display, derive with `olp_fair_american`
  and name it `model_fair_price` — never `consensus_price`, never `best_price`.
- **A stake**, **a pick**, or **`is_executable`.** The last belongs to
  Package #4 and means "the RPC will accept this snapshot". A model has no
  standing to assert it.

## 5. Belief, decision and allocation are three layers

**LOCKED.**

```
Package #5     MARKET                        →  BELIEF
Package #6     BELIEF + MARKET + POLICY      →  DECISION
Package #7     DECISION + BANKROLL / RISK    →  ALLOCATION
```

Because these answer three genuinely different questions:

- Was the **probability model** good?
- Was the **decision rule** good?
- Was the **sizing and risk policy** good?

Collapsed, a profitable run hides a bad model and a losing run condemns a good
model paired with a bad policy. Separated, the staking review due 2026-09-08 can
change Package #7 without invalidating a single thing Package #5 has claimed.

## 6. Calibration protocol

**LOCKED, and pre-registered before the first prediction** — otherwise the bar
gets chosen to fit whatever happened.

Two claims are recorded separately, because they are separate claims.

### 6.1 Eligibility

No model may emit a user-facing confidence tier until it has at least

```
500 settled out-of-sample predictions
```

genuinely prospective and **immutable before any outcome information existed**.

### 6.2 Primary calibration test

Ten **equal-count** probability bins where sample size permits — not fixed
10%-wide buckets. Calibration behaves differently at 52% than at 90%, and fixed
buckets ignore sample size.

Per bin: mean predicted probability, observed outcome frequency, sample count,
and a **95% Wilson confidence interval** on the observed frequency.

Primary summary: **weighted absolute calibration error**, weighted by bin count.

### 6.3 The bar

`CALIBRATED` requires **both**:

| Condition | Threshold |
|---|---|
| Weighted absolute calibration error, most recent 500 settled | **≤ 3 percentage points** |
| Absolute error in any adequately-populated bin | **≤ 7.5 percentage points** |

The second is necessary because a good weighted average can hide a large
systematic failure in one region of the probability space.

`calibration_status` ∈ `NONE` · `PROVISIONAL` · `CALIBRATED` · `DEGRADED`.
**`NONE` and `DEGRADED` beliefs may be stored, displayed and graded, but may not
reach any decision layer** — fail closed, exactly as a one-book market does in
Package #4.

### 6.4 Market-relative standing — a separate claim

A model may be perfectly calibrated and still add nothing. That is recorded, not
hidden:

| `standing` | Meaning |
|---|---|
| `RESEARCH` | not established as better than the market |
| `AT_PARITY` | indistinguishable from the market on proper scoring rules |
| `ADDS_INFORMATION` | Brier Skill Score vs market ≥ 0 **and** log-loss improvement vs market ≥ 0 |

**N = 500 unlocks evaluation, not victory.** A model that cannot beat the market
on proper scoring rules has not earned a claim of predictive superiority — but
it is **not required to beat the market at N = 500 in order to continue
existing**. Five hundred observations may be too few to establish a small real
edge. A model may remain in `RESEARCH` indefinitely, and that is a legitimate
resting state, not a failure to be engineered around.

## 7. Preventing model output from being mistaken for market truth

Six mechanisms, in descending order of how much I trust them.

**1. The execution path is structurally immune.** `place_ticket_rpc` takes a
`market_snapshots.id`. A model row can *point at* one; it can never *be* one and
cannot invent a price. Enforced by a foreign key rather than by anybody's care.

**2. Separate schema.** Model output lives in `model.`, never in `public.`
alongside market views.

**3. Disjoint column names, mechanically checked.** No `model.` column may share
a name with any `market_intelligence` column except the join key, asserted
against `information_schema`.

**4. A `basis` discriminator** — `OBSERVED` / `INFERRED` — on any view
presenting both. No unlabelled blend.

**5. Staleness by binding, not by clock.** A belief records the market
observation it was formed against; if that snapshot is no longer the book's
latest, the belief is stale in exactly the sense `MARKET_MOVED` already means.

**6. Naming in prose.** The LLM writes copy but never models. Any model-derived
number in prose must be attributed. Listed last because it is a convention.

## 8. Point-in-time correctness and immutability

A belief may only consume information that existed at `formed_at`.

**No retroactive belief mutation.** Once a prediction crosses its formation
boundary it is append-only, **as a database property, not an application
convention** — the same append-only guard Package #1 applies to
`market_snapshots` and `wallet_transactions`.

A later model version says something new by adding a row:

```
event X   model v0.1 → 0.574
          model v0.2 → 0.601
```

never

```
UPDATE v0.1 SET probability = 0.601
```

Without this the grading dataset becomes contaminated over time without anyone
intentionally cheating.

Model versions are likewise **immutable once they have emitted a graded
prediction**; grading joins on the version that made the claim.

Determinism is required: same inputs, same version → byte-identical output.
Non-determinism is a defect, not a flourish — without it nothing here is
auditable.

## 9. Three scoreboards, kept completely separate

Conflating these is how a model gets credit for the wrong thing.

| Scoreboard | Question | Measure |
|---|---|---|
| **Probability quality** | is the belief any good? | log loss, Brier score |
| **Calibration** | do 60% predictions happen 60% of the time? | §6.2 |
| **Market-relative** | does it improve on the frozen market baseline? | Brier Skill Score, log-loss improvement; CLV as a *diagnostic* |

### 9.1 The null-model harness — build it first

Package #5's first deliverable is the ability to grade *any* model, validated
against a null model whose prediction is the de-vigged market probability at
formation.

**Asserted:**

```
edge vs formation market      exactly 0
model–market divergence       exactly 0
provenance / binding          exact
grading & calibration maths   independently verifiable
```

**NOT asserted:**

```
future CLV = 0
```

A null model has **zero informational edge relative to the market observation it
was formed from**. That is an identity and is testable. It does *not* follow
that its CLV against a *later* closing market is zero: the closing probability
moves. Over a large unbiased sample that movement may average near zero, but
that is an empirical expectation, not an identity, and baking it into a test
would encode an assumption as a fact.

CLV therefore stays a diagnostic in the market-relative scoreboard — informative
about timing and line-shopping, never a correctness assertion.

Building the harness first means a real model arrives with a scoreboard that
already works, rather than one built alongside it and tuned to flatter it.

## 10. What must not drift, once frozen

1. A model reads `market_intelligence`, never raw quotes or the intermediate views.
2. A model emits probability primitives — never price, stake, pick, executability, or edge.
3. Model output lives in its own schema with disjoint column names.
4. A belief binds to the market observation it was formed from.
5. Uncalibrated and degraded beliefs cannot reach a decision layer.
6. Calibration and market-relative standing are separate claims, separately recorded.
7. Beliefs are append-only; graded model versions are immutable.
8. Same inputs, same version → byte-identical output.
9. Package #5 writes nothing to Packages #1–#4.

## 11. Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Belief / decision / allocation split | **LOCKED** — three packages, §5 |
| 2 | Market-only inputs for v1 | **LOCKED** — §3 |
| 3 | Calibration protocol | **LOCKED** — §6 |
| 4 | Uncertainty representation | **LOCKED** — interval + stated method, §4 |
| 5 | Null-model harness before any model | **LOCKED** — §9.1 |
| 6 | First modelled market | **LOCKED** — moneyline. Binary outcome, straightforward probability semantics, and no push handling, spread geometry or total thresholds to mix in while the grader is being validated. The **harness stays market-type agnostic**; only the first model experiment is narrowed. |
| 7 | Model access to its own performance | **LOCKED** — none in v1. §3.1 |

## 12. Phase A — the grader and the null model, and nothing else

**No predictive model code is written in Phase A.** The deliverable is the
ability to grade *any* model, plus a null model whose answer is already known.

Phase A is complete only when the harness proves all of the following
end-to-end:

| # | Proof |
|---|---|
| 1 | A belief is formed **prospectively and immutably** |
| 2 | It is bound to the **exact** market observation that existed at formation |
| 3 | Settlement grades the probability correctly |
| 4 | Brier and log loss reproduce **independently known** examples |
| 5 | Equal-count calibration bins and Wilson intervals are correct |
| 6 | Market-relative comparisons use the **frozen formation probability** |
| 7 | Future CLV is recorded as an **observation, never treated as an identity** |
| 8 | The null model produces **exactly zero** formation-market divergence |
| 9 | A planted **bad** model scores worse than the null model |
| 10 | A planted **well-calibrated-but-useless** model legitimately ends `CALIBRATED / RESEARCH` |
| 11 | The grader **does not care whether a belief would have won** |

Proof 11 is the one that is easy to overlook and needs a negative control. A
70% prediction that loses can still be an excellent probabilistic forecast; a
51% prediction that wins is not thereby a good one. Betting-oriented systems
drift toward win/loss grading by default, and this package must not.

Only when all eleven hold does a real model become worth writing — arriving with
a scoreboard that already works, rather than one built beside it and tuned to
flatter it.

## 13. Pre-registered tests

Each needs a negative control proving it detects the defect rather than passing
by construction — the discipline that caught four real defects in Package #4,
including one latent since migration `038`.

### Boundary and integrity

| Test | Asserts |
|---|---|
| `P5-T01` | The **actual `olp_model` role** is refused by PostgreSQL on `market_snapshots`, `canonical_market`, `executable_market` and `market_movement` — by attempting the read and catching the error, **not** by inspecting grants |
| `P5-T02` | The same role is refused on settled outcomes, grading tables, calibration results, CLV history and model standings (§3.1), again by attempt |
| `P5-T02b` | The role is read-only — `INSERT` / `UPDATE` / `DELETE` / `CREATE` all refused by attempt |
| `P5-T03` | No `model.` column name collides with a `market_intelligence` column |
| `P5-T04` | Same inputs + same version → byte-identical output |
| `P5-T05` | A belief bound to a superseded snapshot is reported stale |
| `P5-T06` | `UPDATE` on a settled belief is refused by the database |
| `P5-T07` | A graded model version cannot be modified |
| `P5-T08` | Package #5 writes nothing to Packages #1–#4 tables |
| `P5-T09` | No model row can be used as a `place_ticket_rpc` argument |
| `P5-T10` | A belief consuming post-`formed_at` data is rejected |
| `P5-T11` | `NONE` / `DEGRADED` beliefs cannot reach the decision path |

### Phase A — the grading harness

| Test | Proof | Asserts |
|---|---|---|
| `P5-T12` | 1 | A belief is formed prospectively and is immutable thereafter |
| `P5-T13` | 2 | The belief binds to the exact market observation at formation |
| `P5-T14` | 3 | Settlement grades the probability correctly |
| `P5-T15` | 4 | Brier and log loss match hand-computed, independently known values |
| `P5-T16` | 5 | Equal-count bins and Wilson intervals match hand-computed values |
| `P5-T17` | 6 | Market-relative comparison uses the frozen formation probability, not a later one |
| `P5-T18` | 7 | A planted non-zero future CLV does **not** fail the harness — CLV is observed, not asserted |
| `P5-T19` | 8 | Null model: formation-market divergence is **exactly** zero |
| `P5-T20` | 9 | A planted bad model scores worse than the null on both proper scoring rules |
| `P5-T21` | 10 | A planted well-calibrated-but-useless model ends `CALIBRATED` **and** `RESEARCH` |
| `P5-T22` | 11 | The grader is indifferent to win/loss: a losing 70% forecast outscores a winning 51% forecast |
| `P5-T23` | — | Weighted calibration error ≤ 3pp passes, and a single bin at 8pp fails despite a good weighted average |

## 14. Out of scope

Sizing, bankroll policy, portfolio construction, correlation between plays,
live/in-play modelling, and any non-market feature source. Each is its own
package with its own contract.
