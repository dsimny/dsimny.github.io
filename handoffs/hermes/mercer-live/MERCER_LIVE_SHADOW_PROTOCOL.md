# Mercer Live — the shadow protocol and the promotion gate

**Nothing in this file is active.** Shadow positions do not exist yet, because
no model exists to produce them. This is the contract that will govern them when
they do, written down in advance so it cannot be negotiated later against a
number that looks encouraging.

Authoritative text: `docs/MERCER_LIVE_V0.1_PREREGISTRATION.md`, sections 2, 3, 4,
14, 15 and 16. This is the operational reading of those sections.

---

## 1. What shadow mode is

Shadow mode means the system may **observe, calculate, classify, record and
grade**, and may produce internal diagnostics — and may **not** send actionable
Discord picks, expose live bets publicly, send members live wagers, alter the
public pregame ledger, or merge live performance with pregame performance in any
display, total, post or file.

A shadow position is a wager that was never placed, recorded exactly as if it
had been, at the moment it would have been, with everything that was knowable
then and nothing that was not.

Its whole value comes from being honest about a counterfactual. That is fragile
in a specific way: every shortcut that makes a shadow record look better also
makes it mean less. Hence the rules below.

## 2. The rules a shadow position lives under

**Prospective only.** A position exists because the system generated it before
the event resolved. Retroactive creation is barred outright — not discouraged,
barred.

**No look-ahead.** Every input must be observable at the position's
`observed_at`. Barred as inputs: final scores, later drives, later injury
announcements, later line movement, closing odds, provider data revised after
capture, postgame statistics, and anything reconstructed later unless the
original timestamped observation exists in the raw store and is what is used.

**No silent edits.** Never delete a loss. Never alter a recorded entry after
grading. Never change the original line, observed market, or timestamp. Never
quietly fix a model output after seeing a result. A correction is a **new**
record naming the original by id, with its reason and its own provenance.

**Separate ledger.** `data/mercer_live/ledger.json` when it exists. It never
borrows a number from `data/football/football_ledger.json` or
`data/mercer/mercer_ledger.json`, and none of the three ever appear pooled.

**No chase.** No loss-based stake increase, no getting even, no averaging down,
no stake determined by running P/L on the day, week or season. Future sizing
depends only on predefined model and risk inputs at the signal, and the sizing
function must be pure in those inputs.

**Versioned.** Every signal carries `model_version`, `rules_version`,
`market_data_version` (ML-1 ships `ml1-v1`) and `game_state_source` with its
version. Materially different versions are reported separately and never pooled
without saying so.

## 3. The promotion gate

No public live play is permitted before **all** of the following hold, evaluated
**once**, at a **pre-announced review**, on **prospectively generated positions
only**.

### Sample

- **≥ 100 independently qualified shadow positions**
- spanning **≥ 4 distinct slate weeks**
- with **≥ 30 positions in any market family that is itself proposed for
  promotion**

"Independent" means not a reprice or correlated exposure of another under the
duplicate rules.

**Why 100, and what 100 cannot do.** At football prices the standard error of
ROI on 100 positions is roughly ±10 percentage points. 100 positions **cannot
prove an edge**, and the preregistration says so explicitly rather than
pretending otherwise. 100 is the floor at which the *operational* metrics become
interpretable — stale-market incidence, duplicate incidence, error rate,
missing-data rate — and those are what a shadow period exists to measure. A
statistical claim about edge needs a separate, later, pre-registered evaluation
with its own sample.

### Integrity conditions

- all positions generated prospectively
- no retroactive creation
- no deletion of a loss
- no threshold changed and then applied backwards
- no market family, sport, or game state dropped from the evaluation after
  results are known

That last one is the quiet killer in most public betting records, and it is
barred by name.

### The evaluation must report, each with its denominator

sample size · ROI · unit P/L · hit rate where the market makes it meaningful ·
implied-probability calibration (predicted vs realised, by decile) · performance
by market, by sport, by quarter/game-state bucket, by edge bucket, by book ·
line movement after signal (the market's next quote on the same side, and that
market's close) · stale-market incidence · duplicate-signal incidence ·
operational error rate (failed ticks / total ticks) · missing-data rate (records
with any REQUIRED field absent / total records).

### The decision rule

> **Positive ROI alone never authorises launch.**

Promotion requires *all* of:

1. operational rates within the bounds set by the review,
2. calibration not visibly worse than the de-vigged market's, and
3. line movement after signal that is not systematically against the position.

**A review that passes on ROI and fails on any of those is a fail.** Written
this way deliberately: ROI over 100 positions is mostly noise, and the three
conditions above are mostly signal. Reading it the other way round is how
projects like this talk themselves into launching.

## 4. Market-quality circuit breakers

Preregistration section 11. Every breaker is a **refusal that is recorded with
its reason**, never a silent drop.

- **Stale market guard** — a quote is usable only if `observed_at − last_update`
  is within the freshness limit. Provisional 90 s, to be set from measured
  cadence (see the roadmap's deferred items). Settable at or below 90 s, never
  above.
- **Stale game-state guard** — a stalled feed makes the state unusable, and so
  does a **backwards jump**. Must tolerate legitimate breaks and the window just
  after a period change.
- **Minimum data completeness** — a candidate requires every REQUIRED
  game-state field present and non-null and a fresh quote from the confirming
  books. OPTIONAL fields the model uses must be present or the candidate is
  declared unusable. **Never impute.**

These numbers **may be made stricter without a version bump. They may never be
made looser.**

## 5. What promotion would and would not mean

Passing the gate authorises moving from Mode 1 (shadow) toward Mode 2 (private
beta, restricted channel). It does **not** authorise a public or member live
desk; that is Mode 3, and it needs its own recorded decision.

It also does not retire any of section 2's rules. Append-only, no look-ahead, no
silent edits, separate ledgers and no chase survive promotion. They are not
training wheels.

## 6. Hermes' role in all of this

Today: none of it is live, so Hermes' role is to **not create any of it**, and
to keep gathering the observations that ML-2 through ML-6 will need.

When it is live: Hermes tracks progress toward the sample requirements, reports
the operational metrics weekly, and flags integrity risks. Hermes does **not**
run the promotion review, decide its outcome, or advocate for a position on it.
The review is pre-announced, evaluated once, and its conclusion is Daniel's.
