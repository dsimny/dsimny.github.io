# Mercer Live — pre-registration, v0.1 (shadow mode)

STATUS: **FROZEN 2026-09-14**, before a single live observation has been
collected by this system. Every threshold, gate, limit and rule below was
written down before any shadow position existed. Changes ship as Mercer Live
v0.2 with their own freeze date, never as an edit in place. Three items are
explicitly marked **TO BE SET FROM ML-1 EVIDENCE**; each names the evidence
that sets it, the deadline (before ML-4 produces its first candidate), and the
rule that only provider-behaviour measurements — never outcomes — may inform
it. Anything not so marked is fixed.

This spec governs Mercer Live only. It does not govern, unlock or touch the
football 2025 holdout, and it is deliberately NOT registered in
`scripts/football/asof.SPECS`: that registry locks a holdout this spec never
claims. It inherits the Open Ledger house rules in `CLAUDE.md` in full.

Related:
- `MERCER_LIVE_AUDIT_2026-09-14.md` — what existed before, and the risks.
- `MERCER_LIVE_ML1_ARCHITECTURE.md` — the capture boundary this spec is built on.
- `FOOTBALL_PIPELINE.md`, `FOOTBALL_CONTAINMENT_2026-09-13.md`, `MERCER_SPOTLIGHT.md`
  — the pregame products this must never merge with.

frozen: 2026-09-14

---

## 0. What has already been looked at (disclosure)

- One scheduled pregame capture (`data/football/odds/nfl_20260913T183723Z.json`)
  happened to contain eight in-progress NFL events. Its STRUCTURE was inspected
  for this design: which books quoted live (6–7 per event, one event with zero
  books), and how old their `last_update` stamps were at capture (16–81 s). No
  live price was compared with any game state, no edge was computed, and no
  result was joined to it. That file remains the pregame model's evidence and
  is not Mercer Live data.
- Two frozen football studies (fb-v0.1, fb-v0.2) say the pregame NFL market
  beats a market-blind model and that best-price shopping does not clear the
  vig. Both are inputs to the prior in section 9: the live market is expected to
  be efficient too, and Mercer Live begins with the burden of proof.
- No live-market history, no live-game-state history, and no outcome joined to
  either exists in this repository. Nothing below was tuned on data.

## 1. Purpose

Mercer Live is an experimental live-football decision system for:

- NFL
- NCAA Football (FBS)

Initial candidate markets:

- live moneyline
- live spread
- live game total

Explicitly excluded from v0.1: player props, drive markets, quarter markets,
half markets, same-game parlays, alternate lines, touchdown props, any live
derivative not separately pre-registered, and every subjective or
LLM-originated recommendation.

The eventual chain is fixed and one-directional:

    DATA → MODEL → CIRCUIT BREAKERS → DECISION → LEDGER → D.J. MERCER PRESENTATION

Never `LLM → opinion → wager`. D.J. Mercer is a presentation layer over a
deterministic record; he is never the source of a decision (section 19).

## 2. Shadow-mode requirement

Mercer Live v0.1 is research-only. It may observe, calculate, classify, record,
grade and produce internal diagnostics. It may NOT send actionable Discord
picks, expose live bets publicly, send members live wagers, alter the public
pregame ledger, or merge live performance with pregame performance in any
display, total, post or file.

**Pregame and live results are separate strategies with separate ledgers.**
The football model's record (`data/football/football_ledger.json`), the Mercer
Spotlight record (`data/mercer/mercer_ledger.json`) and any future Mercer Live
record (`data/mercer_live/ledger.json`, section 15) are three records. None
may borrow a number from another.

Package ML-1 builds none of the decision chain. It is the DATA step only.

## 3. Promotion requirement

No public live play is permitted before ALL of the following hold, evaluated
once at a pre-announced review, on prospectively generated positions only:

**Sample.** At least **100 independently qualified shadow positions** (the
default from the brief, kept; see the note below), spanning at least
**4 distinct slate weeks**, with at least **30 positions in any market family
that is itself proposed for promotion**. A position is "independent" if it is
not a reprice or correlated exposure of another under section 13.

Why 100 is kept rather than raised: at football prices the standard error of
ROI on 100 positions is roughly ±10 percentage points, so 100 cannot prove an
edge and this document does not pretend it can. 100 is the floor at which the
OPERATIONAL metrics below become interpretable — stale-market incidence,
duplicate incidence, error rate, missing-data rate — and those, not ROI, are
what a shadow period exists to measure. A statistical claim about edge needs a
separate, later, pre-registered evaluation with its own sample. Raising the
number now would only delay the operational read without buying a statistical
one.

**Integrity conditions.** All positions generated prospectively; no
retroactive creation; no deletion of a loss; no threshold changed and then
applied backwards; no market family, sport or game state dropped from the
evaluation after results are known.

**Evaluation must report**, each with its denominator: sample size; ROI; unit
P/L; hit rate where the market makes it meaningful; implied-probability
calibration (predicted vs realised, by decile); performance by market, by
sport, by quarter/game-state bucket, by edge bucket, by book; line movement
after signal (the market's next quote on the same side, and the close of that
market); stale-market incidence; duplicate-signal incidence; operational
error rate (failed ticks / total ticks); missing-data rate (records with any
REQUIRED field absent / total records).

**Positive ROI alone never authorises launch.** A promotion decision requires
the operational rates to be within the bounds set by the review, calibration
that is not visibly worse than the de-vigged market's, and line movement after
signal that is not systematically against the position. A review that passes
on ROI and fails on any of those is a fail.

## 4. No look-ahead rule

Every model input must be information observable at the recorded
`observed_at` of the signal. Prohibited as inputs: final scores; future drives
or possessions; future injury announcements; future line movement; closing
odds; provider data revised after capture; postgame statistics; anything
reconstructed later unless the original timestamped observation exists in the
raw store and is what is used.

The shadow ledger represents what Mercer could actually have known at that
moment. ML-1 enforces the precondition: observations are append-only, carry
`observed_at`, and are never rewritten, so there is nothing later to
reconstruct from except the record itself.

## 5. Observation-time semantics

Open Ledger already separates observation time from provider time
(`asof.py`: event_time / available_at / ingested_at; `fetch_odds.py` stamps the
moment WE received the payload). Preserved and made structural:

- `observed_at` — the UTC instant Open Ledger's process received the provider
  response. One value per fetch, shared by every record parsed from it.
- `provider_timestamp` — the provider's own time for the datum, if supplied
  (Odds API `last_update` per book and per market; ESPN supplies none for the
  scoreboard and the field is recorded as `null`, never substituted).
- `source` — the provider identity.
- `run_id` — the capture execution that produced the record.

`observed_at` is never the provider's last-update time, never the event start,
never the time a record was later processed. A freshness rule (section 11)
always compares `observed_at` with `provider_timestamp`; it never replaces one
with the other.

## 6. Immutable raw observations

Raw live snapshots are append-only observations. Observing one game 100 times
yields 100 records, not one updated record. No "latest" view may be the sole
source of truth; a convenience view is permitted only as a derivation from the
intact raw series. Each record has an immutable, content-derived identity.
Corrections are new records with provenance (section 16), never edits.

## 7. Required live-game-state data

Classification is by what the chosen source (ESPN public scoreboard) is
already proven to supply in this repository versus what it is believed to
supply but has not been verified from this environment.

| field | class | note |
|---|---|---|
| sport, league | REQUIRED | from the request, not the payload |
| canonical event id | REQUIRED | derived; section 7 of the architecture doc |
| provider event id | REQUIRED | ESPN `event.id` |
| home team, away team | REQUIRED | identity per sport: NFL franchise key, NCAAF normalised name key; display names kept |
| scheduled start | REQUIRED | ESPN `event.date`, UTC |
| current status | REQUIRED | `status.type.name` and `status.type.state` (pre / in / post) |
| game complete flag | REQUIRED | `status.type.completed` |
| current score | REQUIRED when state is in or post | recorded `null` when pre — ESPN reports "0" for an unplayed game and 0 is not a score |
| season year / type | REQUIRED | the season-type allowlist (regular season only) is inherited for any future grading |
| observation timestamp | REQUIRED | `observed_at` |
| period / quarter | OPTIONAL | `status.period` |
| clock | OPTIONAL | `status.displayClock` and `status.clock` |
| possession | OPTIONAL | `situation.possession` (team id, resolved to home/away) |
| down, distance, yard line | OPTIONAL | `situation.down/distance/yardLine`, plus the text forms |
| timeout state | OPTIONAL | `situation.homeTimeouts/awayTimeouts` |
| recent scoring event / last play | OPTIONAL | `situation.lastPlay` text, type, score value |
| red-zone flag | OPTIONAL | `situation.isRedZone` |
| play state, drive state | FUTURE | not on the scoreboard; would need the summary/play-by-play endpoint |
| most recent play timestamp | FUTURE | no timestamp is attached to `lastPlay` on the scoreboard |
| ESPN win probability | RECORDED, NEVER AN INPUT | a third party's model; may be kept as a reference series only |

A REQUIRED field that is absent makes the record `incomplete` and it is stored
as such with the missing names listed. An OPTIONAL field absent is stored as
`null`. Nothing is defaulted to a plausible value.

## 8. Required live-market data

Every market observation carries: canonical event id and provider event id;
bookmaker key (verbatim); market (`h2h` / `spreads` / `totals`); outcome
(team identity, or `Over` / `Under`); point where applicable; American price;
provider book `last_update` and market `last_update` if supplied;
`observed_at`; `quote_phase` (pregame / live / post / unknown) with the basis
for the label; market availability (an event returned with no books, or a
book missing a market, is recorded as such rather than omitted); source.

**Individual book quotes are never collapsed into a consensus in the raw
store.** Consensus is a derivation computed later from the intact quotes.

## 9. Candidate-generation philosophy

The v0.1 model is not built in ML-1. Its conceptual boundary is fixed now:

Permitted future components: pregame team strength; pregame market prior;
score differential; time remaining; possession; field position; down and
distance; expected remaining possessions; pace; timeout state; scoring
environment; offensive and defensive efficiency.

Any component added must (1) have a documented purpose, (2) use point-in-time
observable data, (3) be independently gradeable for incremental value where
practical, (4) earn its complexity. No feature is included because it sounds
predictive. The prior is that the live market is efficient and the model adds
nothing; every component must show otherwise against the de-vigged market as
the null.

**Market family order (evidence-based, decided now).** Capture all three
families in ML-1 — the marginal cost is per market, not per event, and the
capture is the only way to learn live coverage. Model **live totals first**:
the remaining-total problem is a state-space quantity (score, clock, pace)
whose inputs are all REQUIRED or OPTIONAL fields already in scope; the
football total model was the one market-blind family that beat its naive
baseline (fb-v0.1, by 0.21 points); MLB's totals track was where the
fundamentals model was calibrated. Live moneyline is dominated by publicly
available win-probability models and is expected to be the sharpest market;
live spread is the moneyline transformed through a margin distribution with
key-number effects, and adds complexity before it adds information. **This
order is conditional on ML-1 evidence**: if the first two slate weeks show live
totals quoted by fewer than three books at the median live tick, totals is
deferred and moneyline is modelled first. That switch is a data-availability
decision, not a result-driven one, and it must be recorded before ML-3 begins.

## 10. Initial candidate thresholds (provisional; create candidates, never wagers)

| market | candidate if | value |
|---|---|---|
| total | \|model expected final total − market total\| ≥ | **3.0 points** |
| moneyline | model probability − de-vigged market probability ≥ | **4.0 percentage points** |
| spread | model probability of covering the offered number − de-vigged market probability of covering it ≥ | **4.0 percentage points** |

The spread threshold is stated in probability, not points, on purpose. The
worth of a point on a spread depends on time remaining (a 1.5-point gap is
noise in the first quarter and decisive with two minutes left), so a fixed
point threshold would be a different rule at every game state. Probability is
the currency both the market and the model already speak, it is the same
number used for the moneyline, and it avoids inventing a points-to-probability
conversion whose precision does not exist. The same time-dependence applies to
the totals threshold; 3.0 points is kept as specified, and ML-6 must report
totals candidates by game-state bucket so the rule's looseness early and
tightness late is visible rather than averaged away.

These thresholds may change only by version bump, only before results of the
affected version are known, and only for a stated principled reason.

## 11. Market-quality circuit breakers (v0.1 protections, preregistered)

Every breaker is a refusal that is RECORDED with its reason, never a silent
drop. The set:

- **stale market guard** — a quote is usable only if `observed_at −
  market last_update` is within the freshness limit. Provisional limit **90
  seconds**. **TO BE SET FROM ML-1 EVIDENCE:** the limit is fixed at or below
  90 s once ML-1 has measured the distribution (p50, p95) of quote age at
  observation for live quotes over at least two slate weeks. Measured provider
  cadence, not any result, sets it. Deadline: before ML-4's first candidate.
- **stale game-state guard** — while `state == in` and the clock is running,
  two consecutive observations with identical (period, clock, score) at a
  cadence of one minute indicate a stalled feed; the state is unusable until
  it moves. **TO BE SET FROM ML-1 EVIDENCE:** the exact rule (consecutive
  count and clock tolerance) is set from the measured ESPN update cadence, same
  deadline, same evidence-only constraint.
- **event identity guard** — a quote is usable only when its canonical event
  id joins exactly one game-state record for the same tick; zero or several
  is unusable.
- **market/game join guard** — the joined game state's `scheduled_start` and
  the quote's `commence_time` must agree within **2 hours**, and the joined
  state must be `in`. A quote whose event the game-state source calls `pre`
  or `post` is never a live quote.
- **multi-book confirmation** — a candidate side must be offered by at least
  **3 books** at the same point (totals/spreads) or within 1 implied-point
  (moneyline), all fresh. **TO BE SET FROM ML-1 EVIDENCE:** if measured live
  coverage makes 3 unattainable for a family at the median tick, that family
  is deferred (section 9) rather than the guard loosened.
- **duplicate recommendation guard** — section 13.
- **same-market cooldown** — after a signal on (event, market family, side),
  no new signal on that key for **10 minutes**, and none at all unless the
  model's own fair value has moved by at least the section-10 threshold since
  the last signal.
- **recent-score instability guard** — no signal within **90 seconds** of an
  observed change in score.
- **recent-turnover instability guard** — where possession data exists, no
  signal within **90 seconds** of an observed possession change not explained
  by a score.
- **suspended-market guard** — an event returned with no books, or a book
  returning no outcome for a market, is unavailable; a side quoted by fewer
  books than the confirmation guard is unavailable.
- **impossible clock/state guard** — clock outside [0, period length], period
  outside the sport's range, score decreasing, or `completed` with `state !=
  post` marks the state record unusable.
- **price-movement guard** — a quote that moved by more than **25 cents of
  American price on a favourite or 40 on an underdog** since the previous
  tick, or a total/spread point that moved by **≥ 2.0 points** in one tick, is
  unusable until it holds for a second tick.
- **event-ended guard** — `state == post` or `completed` disables every
  signal on the event permanently.
- **halftime / state-transition guard** — no signal while the observed period
  is a break (halftime, end of quarter) or within **60 seconds** after the
  observed period changes.
- **minimum data completeness** — a candidate requires every REQUIRED
  game-state field present and non-null and a fresh quote from the confirming
  books; OPTIONAL fields the model uses must be present or the model must
  declare the candidate unusable, never impute.

The numbers above are the v0.1 values. They exist so ML-5 has something to
implement and ML-6 something to measure; they are not tuned and they may be
made STRICTER without a version bump, never looser.

## 12. Chase prevention

Mercer Live is never a martingale. No loss-based stake increase; no "get even"
logic; no averaging down; no increased exposure because an earlier play lost;
no stake determined by emotional or prior-result state; no stake determined by
running P/L on the day, week or season. Future position sizing depends only on
predefined model and risk inputs at the moment of the signal, and the sizing
function must be pure in those inputs.

## 13. Duplicate-position rules

The unit of exposure is the **thesis**: (canonical event id, market family,
side). `Under 52.5` followed three minutes later by `Under 51.5` is one thesis
repriced, not two plays.

- The first qualifying quote on a thesis opens ONE shadow position at that
  price and point. Later qualifying quotes on the same thesis are recorded as
  **reprices** of the open position — they are evidence about line movement
  after signal, never new positions.
- A new position on an already-open thesis requires **genuinely new model
  information**: the model's fair value has moved by at least the section-10
  threshold since the open, the cooldown has elapsed, and the move is not
  attributable solely to the market moving. Even then the new position is
  flagged `correlated` and counts once toward the independence requirement in
  section 3.
- The opposite side of an open thesis is recorded as a `hedge`, flagged
  correlated, and never counted as independent.
- Moneyline and spread on the same side of the same game are treated as
  correlated exposure and count once toward independence.
- Re-evaluating every minute manufactures nothing: a thesis is opened once,
  observed continuously, and closed by the event-ended guard.

## 14. Exposure boundaries (future shadow sizing)

- max **1.0u** per live signal
- max **2.0u** aggregate exposure per game
- max **5.0u** aggregate Mercer Live exposure per day

These are stricter than the MLB convention (10u daily cap, 3u tier cap) on
purpose: the live market is faster and the sample is zero. They may not be
raised to improve historical results. Shadow positions are always paper: 0u
real, 1.0u paper basis, exactly as `watchlist.json` and `daily_ledger.json`
already distinguish paper from staked.

## 15. Separate ledger

Location, when it exists: `data/mercer_live/ledger.json`, append-only, on the
same conventions as `totals_ledger.json` and `watchlist.json` (aggregates
recomputed from full history, never stored as truth). ML-1 does NOT create it.

Each future entry carries: `signal_id`, `event_id` (canonical), `sport`,
`league`, `market`, `side`, `point`, `price`, `book`, `model_value`,
`market_value`, `edge`, `observed_at`, `game_state` (period, clock, score,
possession where present), `units` (0 real / paper basis), `status`,
`result`, `pnl_paper`, `model_version`, `rules_version`,
`market_data_version`, `game_state_source`, and the `observation_id`s of the
raw records it was computed from. Entries are never edited after grading.

## 16. No silent edits

Once a shadow recommendation exists: never delete a loss; never alter a
recorded entry after grading; never change the original line, observed
market, or timestamp; never silently fix a model output after seeing a
result. A correction is a new record that names the original by id, states
the reason, and carries its own provenance — the pattern already used by
`data/football/result_identity/` and OLP's `ticket_result_adjustments`.

## 17. Model versioning

Every future signal carries `model_version`, `rules_version` (this document's
version plus the breaker set implemented), `market_data_version` (the
observation schema version — ML-1 ships `ml1-v1`) and `game_state_source`
with its version. Results from materially different versions are reported
separately and never pooled without saying so.

## 18. Discord boundary

Three future modes, none activated by ML-1:

- **Mode 1 — Shadow.** No Discord output of any kind. This is v0.1.
- **Mode 2 — Private beta.** Output only to a restricted test/admin channel,
  through a webhook name that does not exist yet and is not any of
  `DISCORD_WEBHOOK_URL`, `_MEMBERS`, `_LEDGER` or `_ALERTS`.
- **Mode 3 — Public/member live desk.** Only after the section-3 promotion
  passes and is recorded.

No code in ML-1 references a Discord webhook, imports a Discord module, or
carries a mode switch.

## 19. D.J. Mercer persona boundary

Mercer may eventually explain a deterministic signal, summarise model state,
say why a play qualified or failed a gate, summarise the record, and describe
observed game state. Mercer may not invent an unmodelled play, override a
circuit breaker, change an allocation, turn a PASS into a PLAY, manufacture
confidence, or conceal losing history. If the LLM is unavailable the engine
behaves identically; the persona is a renderer over records. The football
copy bars (`edge`, `+EV`, `the model likes`) apply to every Mercer Live
surface until a promotion has been recorded.

## 20. Legal boundary

Any future actionable posting carries, verbatim from the existing project
copy: analytics-not-a-sportsbook framing, 21+, 1-800-GAMBLER, and the
no-guarantee / not-betting-advice language. Nothing public is implemented now.

## 21. Version history

| version | date (2026) | change |
|---|---|---|
| ml-v0.1 | Sep 14 | Pre-registration frozen before any live observation. Scope (NFL, NCAAF; ML/spread/total), shadow rule, 100-position promotion floor with operational conditions, no-look-ahead, observation-time semantics, immutable raw store, field classification, thresholds (3.0 pts / 4.0 pp / 4.0 pp), breaker set, chase prevention, thesis-based duplicate rule, exposure limits, separate ledger, versioning, Discord modes, persona and legal boundaries. Three provider-cadence items deferred to ML-1 evidence with deadlines. Market order: capture all three, model totals first, conditional on measured coverage. |
