# Package #5 — Model Layer — Pre-Registration

**Status: DRAFT CONTRACT. No code written.** Written before implementation, as
`PACKAGE4_PREREG.md` was. Sections marked **DECISION** are not mine to settle.

---

## 1. What Package #5 is

Package #4 answers *what the market says*. Package #5 is the first layer
permitted to answer *what we believe*, and it is the first layer in the whole
system whose output cannot be checked against anything observable at the moment
it is produced.

That asymmetry is the entire design problem.

| | Package #4 | Package #5 |
|---|---|---|
| Nature | **observed** | **inferred** |
| Falsifiable | immediately, against the feed | slowly, against outcomes |
| Wrong how | a bug | a bug **or** an honest miss |
| Authority | market truth | an opinion with provenance |

Every mechanism below exists to keep a reader from confusing the right-hand
column for the left.

## 2. The prior that should shape this package

The NFL track was shelved on 2026-08-20 because **the market beat the model 9
times out of 9 at T−24**. That is the single most informative result this
project has produced about modelling, and Package #5 should be built as though
it is the expected outcome rather than a past embarrassment.

Two consequences run through this contract:

1. **The null model is the market.** A model that does not beat "quote the
   de-vigged consensus back" has produced nothing, and the system should be able
   to say so mechanically.
2. **Confidence must be earned before it is expressed.** An uncalibrated model
   has no entitlement to a confidence number, and the schema should make that
   impossible to fake.

## 3. What a model may read

**Market data: `market_intelligence` only.**

Not `market_snapshots`, not `canonical_market`, not the raw feed. If a model
needs to look at raw quotes, Package #4 failed at its job and the fix belongs
there, not here.

This is enforced by **privilege, not convention** — the same discipline
Package #1 used for RLS. A dedicated `olp_model` role holds `SELECT` on
`market_intelligence` and is explicitly denied `market_snapshots`. A model that
tries to reimplement odds maths gets a permission error, not a subtly different
answer.

Also readable:

| Source | Why |
|---|---|
| `ticket_closing_line_value` | CLV, the fast feedback signal |
| `ticket_effective_results` | realised outcomes, for calibration |
| `events`, `event_schedule_history` | schedule context |
| its own prior outputs | so a model can be sequential |

**Not readable, ever:** anything that did not exist at the moment being
predicted. See §8.

**DECISION — non-market features.** Ratings, injuries, weather, rest, travel.
None exist in this system yet. My recommendation is that Package #5 ships with
**market-only inputs**, because adding a feature source means adding a second
provenance and staleness regime, and the football track's failure was not caused
by a shortage of features. If you want them, they should be their own package
with Package #3's ingestion discipline, not a side door here.

## 4. What a model may emit

One row per `(event_id, market_type, selection, line)` — the same key as
Package #4, so the two can always be joined without interpretation.

**May emit:**

| Field | Meaning |
|---|---|
| `model_probability` | belief that this selection wins, `NUMERIC(9,6)` |
| `probability_low` / `probability_high` | uncertainty interval, by a stated method |
| `interval_method` | how the interval was produced — bootstrap, conformal, analytic |
| `calibration_status` | see §6 |
| `model_id`, `model_version` | which model, which frozen version |
| `market_binding_snapshot_id` | the market observation this belief was formed against |
| `inputs_hash` | hash of the exact inputs consumed |
| `generated_at` | when the claim was made |

**May NOT emit:**

- **A price.** Prices are market objects. A model that emits `-138` invites
  someone to read it as a quote. If a fair price is wanted for display, derive
  it at read time with `olp_fair_american` and name the column
  `model_fair_price` — never `consensus_price`, never `best_price`.
- **A stake.** Sizing is policy, not inference. See §5.
- **A pick.** A pick is a decision. See §5.
- **`is_executable`.** That word belongs to Package #4 and means "the RPC will
  accept this snapshot". A model has no standing to assert it.

## 5. Belief and decision are different layers

**DECISION, and the most consequential one here.**

A belief is *this selection wins with probability p*. A decision is *therefore
we play it, at this size*. My recommendation is that Package #5 emits **only
beliefs**, and a later package turns belief plus policy into a decision.

Why the separation earns its keep here specifically:

- Your staking review is due 2026-09-08. If sizing lives inside the model, you
  cannot change staking without invalidating the model's track record.
- A model can be graded on calibration alone, independently of whether the
  staking policy was any good. Conflated, a bad policy makes a good model look
  worthless and vice versa.
- The product is "process, not alpha". Showing belief and policy as separate,
  separately-auditable steps *is* the process.

The cost is one more layer before anything is playable. If you would rather
Package #5 produce a graded play end-to-end, that is a legitimate call — but
then the freeze record should say plainly that model quality and staking quality
are no longer separable.

## 6. Confidence, and the right to express it

Two failure modes, and they need different fields:

- **Epistemic** — we lack information. Shrinks with data.
- **Aleatoric** — the game is genuinely uncertain. Does not shrink.

A single "confidence: high" conflates them and is how a model talks itself into
a bet.

`calibration_status` is a required enum, and it gates everything downstream:

| Status | Meaning |
|---|---|
| `NONE` | never graded. The interval is a guess about a guess. |
| `PROVISIONAL` | graded on fewer than the pre-registered minimum |
| `CALIBRATED` | meets the pre-registered minimum, within the pre-registered error band |
| `DEGRADED` | was calibrated, has drifted outside the band |

**`NONE` and `DEGRADED` beliefs may be stored, displayed and graded, but may not
feed any decision layer.** Fail closed, exactly as a one-book market does in
Package #4.

**DECISION — the calibration bar.** What N, over what window, within what
Brier/log-loss band, before a model earns `CALIBRATED`? This must be fixed
*before* the first graded prediction, or it will be chosen to fit whatever
happened.

## 7. Preventing model output from being mistaken for market truth

Six mechanisms, in descending order of how much I trust them.

**1. The execution path is structurally immune.** `place_ticket_rpc` takes a
`market_snapshots.id`. A model row can *point at* one; it can never *be* one and
cannot invent a price. This is already true and must stay true — it is the
strongest guarantee in the design, because it is enforced by a foreign key
rather than by anybody's care.

**2. Separate schema.** Model output lives in `model.`, never in `public.`
alongside market views. Crossing that boundary is visible in every query.

**3. Disjoint column names, mechanically checked.** No column in `model.` may
share a name with any column of `market_intelligence` except the join key. A
test asserts this against `information_schema`, so the day someone adds
`model.consensus_probability` the suite fails.

**4. A `basis` discriminator on every combined view.** Anything presenting both
must label each number `OBSERVED` or `INFERRED`. No unlabelled blend, ever.

**5. Staleness by binding, not by clock.** A belief records the market
observation it was formed against. If that snapshot is no longer the book's
latest, the belief is stale in exactly the sense `MARKET_MOVED` already means —
reusing Package #2's machinery instead of inventing a second staleness regime.

**6. Naming in prose.** The LLM writes copy but never models (a rule already
established for the football product). Any model-derived number in prose must be
attributed as such. This is the weakest mechanism because it is a convention;
it is listed last deliberately.

## 8. Point-in-time correctness

A belief may only consume information that existed at `generated_at`.

This is where backtests die quietly, so it is a schema property rather than a
discipline: every model row records `inputs_hash` and
`market_binding_snapshot_id`, and a re-run against the same frozen inputs must
reproduce the same output exactly. Non-determinism is a defect, not a flourish —
without it nothing here can be audited.

Package #1's append-only tables and `ingest_seq` ordering already make
reconstructing "what was known at time T" possible. Package #5 must not add
anything mutable that would break that.

## 9. Grading, and the null model

Every belief must be gradeable on two horizons:

- **CLV** — did the price beaten by this belief hold up to the close? Fast,
  available per event, already computed by `ticket_closing_line_value`.
- **Outcome** — did it win? Slow, noisy, and the only thing that ultimately
  matters.

**Recommendation — build the harness before the model.** Package #5's first
deliverable should be the ability to grade *any* model, validated against a
**null model that quotes the de-vigged consensus back**. The null model's
expected CLV is known: zero, minus the vig. If the harness says otherwise, the
harness is wrong, and finding that out costs nothing.

Only then is a real model worth writing — and it arrives with a scoreboard that
already works, rather than one built alongside it and tuned to flatter it.

Model versions are **immutable once they have emitted a graded prediction**.
Grading joins on the version that made the claim. Otherwise a model gets edited
after the fact and the record means nothing.

## 10. What must not drift, once frozen

1. A model reads `market_intelligence`, never raw quotes.
2. A model emits probability, never price, never stake, never executability.
3. Model output lives in its own schema with disjoint column names.
4. A belief binds to the market observation it was formed from.
5. Uncalibrated and degraded beliefs cannot reach a decision layer.
6. Same inputs, same version → byte-identical output.
7. A model version that has been graded is immutable.
8. Package #5 writes nothing to Packages #1–#4.

## 11. Open decisions

| # | Decision | My recommendation |
|---|---|---|
| 1 | Belief and decision in one package or two? | **Two.** §5 |
| 2 | Market-only inputs, or add features now? | **Market-only.** §3 |
| 3 | Calibration bar — N, window, error band | must be set before the first graded prediction |
| 4 | Uncertainty representation — interval, distribution, or tier | interval plus a stated method |
| 5 | Null-model harness first, or model first? | **Harness first.** §9 |
| 6 | Which markets first — moneyline only, or all three? | moneyline; one line per wager, no fragmentation to reason about |
| 7 | Does a model see its own past performance? | not in v1 — it invites fitting to recent noise |

## 12. Proposed test matrix

Written before code, in the Package #4 style, and each will need a negative
control proving it detects the defect rather than passing by construction.

| Test | Asserts |
|---|---|
| `P5-T01` | The `olp_model` role cannot read `market_snapshots` — permission denied |
| `P5-T02` | No `model.` column name collides with a `market_intelligence` column |
| `P5-T03` | Same inputs + same version → byte-identical output |
| `P5-T04` | A belief bound to a superseded snapshot is reported stale |
| `P5-T05` | `NONE` / `DEGRADED` beliefs cannot reach the decision path |
| `P5-T06` | The null model's measured CLV is zero minus the vig, within tolerance |
| `P5-T07` | A graded model version cannot be modified |
| `P5-T08` | Package #5 writes nothing to Packages #1–#4 tables |
| `P5-T09` | No model row can be used as a `place_ticket_rpc` argument |
| `P5-T10` | A belief consuming post-`generated_at` data is rejected |

## 13. Out of scope

Sizing, bankroll policy, portfolio construction, correlation between plays,
live/in-play modelling, and any non-market feature source. Each is its own
package with its own contract.
