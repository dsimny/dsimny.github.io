# Mercer Live — pre-registration, v0.1 (shadow mode)

STATUS: **RECORDED 2026-09-12, NOT YET FROZEN.** Every commitment below is
written before a single live observation has been captured and before any
model, threshold or circuit breaker has been run against one. The `frozen:`
line at the bottom follows the repository's convention
(`docs/FOOTBALL_PREREG_V02.md`, `scripts/football/asof.frozen_date`). It must
read a date, set on Daniel's instruction and by commit, **before package ML-5
generates its first shadow position.** Packages ML-1 through ML-4 produce
observations, features and diagnostics, not positions, and may run while this
reads NOT YET. `scripts/mercer_live/mlcommon.prereg_frozen()` is the hook a
later package refuses to run without.

This document is deliberately NOT registered in `scripts/football/asof.SPECS`.
That registry gates the 2025 NFL holdout season, which Mercer Live never
reads: it is a prospective study on live data from 2026 onward. Coupling the
two would let an unfrozen live spec lock a pregame holdout it has no claim on.

The test of this document is whether someone can later audit that the
goalposts did not move after results were seen. Section 0 says what has
already been looked at; everything after it is a commitment.

---

## 0. Disclosure — what has already been seen

- No live football observation exists anywhere in this repository as of the
  date above. `scripts/mercer_live/` has been exercised against fixtures only.
- The Odds API's in-play behaviour (payload shape, bookmaker update cadence,
  which books quote in-play, how suspensions present) has NOT been observed
  from this repository. Two hand-run pregame captures with spreads and totals
  exist under `data/mercer/odds/`; they are pregame and were not read for this.
- ESPN's in-play `situation` block has NOT been fetched in the session that
  wrote this. The field paths in `gamestate.py` come from the endpoint's
  documented, long-stable schema and from the four existing football modules
  that read its pregame subset. `capture.py probe` exists to verify them.
- The pregame football studies are closed and were read: the market-blind
  model lost 9/9 (`FOOTBALL_RESULT_T24.md`) and best-price shopping did not
  clear the vig (`FOOTBALL_RESULT_PRICE.md`). Nothing here reopens either;
  they say nothing about in-play markets and are not cited as evidence for
  or against this study.

## 1. Purpose

Mercer Live is an experimental **live-football decision system** for the
**NFL** and **NCAA Football (FBS)**, built to be validated in shadow mode
before it is allowed to say anything in public.

**Initial candidate markets:** live moneyline, live spread, live game total.

**Explicitly excluded from v0.1:** player props, drive markets, quarter
markets, half markets, same-game parlays, alternate lines, touchdown props,
any live derivative not separately pre-registered, and any subjective or
LLM-generated recommendation.

The pipeline shape is fixed:

    DATA -> MODEL -> CIRCUIT BREAKERS -> DECISION -> LEDGER -> D.J. MERCER PRESENTATION

and never `LLM -> opinion -> wager`. D.J. Mercer is a presentation layer.
Every actionable recommendation originates in deterministic, auditable code.

## 2. Shadow-mode requirement

Mercer Live v0.1 is **research-only**. It may observe, calculate, classify,
record, grade and produce internal diagnostics. It may not send actionable
Discord picks, expose live bets publicly, send members live wagers, alter the
public pregame ledger, or merge live performance with pregame performance —
silently or otherwise.

**Pregame and live results are separate strategies with separate ledgers.**
Nothing in `data/mercer_live/` is ever summed, averaged or displayed together
with `data/ledger.json`, `daily_ledger.json`, `totals_ledger.json`,
`watchlist.json`, `data/football/football_ledger.json` or
`data/mercer/mercer_ledger.json`. This is the same rule that already keeps the
Watch List, the Daily Pick, the football record and the Mercer Spotlight apart.

## 3. Promotion requirement

No public Mercer Live output of any kind before ALL of the following:

- **at least 100 independently qualified shadow positions**, every one
  generated prospectively (signal time-stamped before the outcome existed);
- no retroactive creation of plays, no deletion of losing plays;
- no threshold changed and applied retroactively;
- no cherry-picking of games, markets or sports after results are known.

**The 100 is kept.** It was considered against the pregame precedent (the
Daily Pick's staking review was scheduled, not sampled) and against the
statistical reality that 100 positions at football prices cannot distinguish
a 2% edge from zero. It is kept anyway because it is a floor on *operational*
evidence — enough live sessions to have hit stale feeds, suspensions,
duplicate signals and data gaps — not a claim that 100 proves an edge. The
evaluation below is what has to be convincing; the count only makes it
possible.

Promotion evaluation must report, at minimum: sample size; ROI; unit P/L; hit
rate where meaningful; implied-probability calibration; performance by market,
by sport, by quarter / game state, by edge bucket and by book; line movement
after the signal; stale-market incidence; duplicate-signal incidence;
operational error rate; missing-data rate.

**Positive ROI alone does not authorise launch.** A positive ROI with negative
post-signal line movement, or with a stale-quote incidence that means the
prices were not takeable, is a failed evaluation.

## 4. No look-ahead rule

Every model input must be information observable at the recorded
`observed_at` of the decision. Prohibited as inputs: final scores, future
drives or possessions, future injury announcements, future line movement,
closing odds, provider data revised after the capture, postgame statistics,
and any value reconstructed later unless the original time-stamped value can
be proven from the raw payload archive (`raw_ref` + `raw_sha256`, section 6).

The shadow ledger must represent what Mercer could actually have known at
that moment. A signal that cannot be recomputed from observations on disk with
`observed_at` no later than its own timestamp is not a valid signal.

## 5. Observation-time semantics

Open Ledger already distinguishes the moment WE fetched something from the
provider's own stamps (`captured_utc` vs `last_update` in every football odds
capture). Mercer Live preserves that and makes it explicit on every row:

| field | meaning |
|---|---|
| `observed_at` | the moment Open Ledger received the response, from this process's own UTC clock, millisecond precision |
| `request_sent_at` | the moment the request left |
| `provider_http_date` | the provider's HTTP `Date` header, verbatim |
| `book_last_update`, `market_last_update` | The Odds API's per-bookmaker and per-market stamps, verbatim |
| `provider_ts` | null for ESPN, which sends no server timestamp — never filled with anything else |
| `run_id` | the capture execution this observation belongs to |

`observed_at` is **never** a provider time, event start, or a later
reconstruction time. Derived quantities (`quote_age_s`) are computed from
`observed_at` minus the provider stamp and stored beside both, so the
derivation is checkable.

## 6. Immutable raw observations

Raw live snapshots are **append-only observations**. A game observed 100 times
is 100 rows. There is no "latest" record that gets overwritten; a latest view
is whatever reads the last row per event, and the history stays. The store
format enforces this (gzip members appended, no update path), the raw provider
bytes are archived per tick and hashed into every derived row, and closed
partitions are sealed into a committed, append-only manifest. A sealed file
whose bytes later disagree is reported, and the original entry stands.

## 7. Required live-game-state data

Classified for ESPN's public scoreboard, the chosen v0.1 source:

| field | class | note |
|---|---|---|
| sport, league | REQUIRED | |
| canonical event id | REQUIRED | `<league>:<espn_event_id>` — the key both pregame results stores already use |
| provider event id | REQUIRED | ESPN's |
| home team, away team | REQUIRED | NFL franchise key via `teams.py`; NCAAF normalised name via `espn_ncaaf.norm`; the mode is stamped on the row |
| scheduled start | REQUIRED | |
| current status (pre/in/post + name) | REQUIRED | |
| current score | REQUIRED | null before kickoff — "0 is not a score" |
| period / quarter | REQUIRED in play | |
| clock | REQUIRED in play | float seconds, plus the display string |
| game complete flag | REQUIRED | |
| observation timestamp | REQUIRED | section 5 |
| possession | OPTIONAL | ESPN team id, resolved to the team key when it matches |
| down, distance | OPTIONAL | |
| yard line / field position | OPTIONAL | |
| timeout state | OPTIONAL | home/away remaining |
| red-zone flag | OPTIONAL | |
| recent scoring event | OPTIONAL | via `last_play.score_value` |
| play state / drive state | FUTURE | not structured in this endpoint |
| most recent play timestamp | FUTURE | the scoreboard's `lastPlay` carries no wall-clock; the play-by-play endpoint does |
| ESPN win probability | EXCLUDED | an external model output; deliberately not captured so the shadow model cannot lean on it |

A REQUIRED field that is absent does not drop the row; it is listed in the
row's `missing_required`, so completeness is a measured rate, not an
assumption. No field is pretended available: the OPTIONAL rows above are
recorded exactly when ESPN sends them and null otherwise.

## 8. Required live-market data

Every `live_market_observation` row carries: event identity (provider id, and
the canonical `event_id` when joined), bookmaker key and tier, market, both
outcomes with side (home/away/over/under), point where applicable, American
price, the provider's last-update stamps, `observed_at`, the phase
(pregame / live / post / unknown) with the basis it was derived from, and the
source. A `live_market_coverage` row per event per tick records which books
quoted which markets, so a withdrawn or suspended market is a recorded
absence.

**Individual bookmaker quotes are never collapsed into a consensus at capture.**
Consensus is a later, recomputable derivation.

## 9. Candidate-generation philosophy

Not implemented in ML-1. The conceptual boundary is recorded now. Potential
future components: pregame team strength, pregame market prior, score
differential, time remaining, possession, field position, down/distance,
expected remaining possessions, pace, timeout state, scoring environment,
offensive/defensive efficiency.

Any component added must (1) have a documented purpose, (2) use only
point-in-time observable data, (3) be independently gradeable for incremental
value where practical, and (4) earn its complexity. Nothing is included because
it sounds predictive. The pregame football studies are the precedent: features
were added in a pre-declared priority order and each had to beat the prior.

## 10. Initial candidate thresholds (provisional, recorded before results)

These create **candidate signals for the shadow ledger**, never public wagers,
and they are not launch thresholds. They may be changed before shadow
collection begins, with the reason recorded here; after the first shadow
position exists they change only by a versioned amendment that applies
forward.

| market | candidate when | value |
|---|---|---|
| live total | model expected final total vs market line | ≥ **3.0 points** |
| live moneyline | model win probability minus de-vigged market probability | ≥ **4.0 percentage points** |
| live spread | model probability of covering the quoted line minus de-vigged market probability of the same | ≥ **4.0 percentage points** |

**Why the spread threshold is in probability, not points.** Football margins
pile up on key numbers (3 and 7 alone are ~23% of NFL finals), so one point of
line is worth very different amounts of probability in different places, and
a points threshold would be tight at 2.5 and loose at 5.5 without anyone
choosing that. Probability is the currency EV is measured in, it puts spread
and moneyline signals on one scale so "performance by edge bucket" compares
like with like, and it is what the model actually produces. The point-gap
equivalent is recorded on every signal as a diagnostic, not a gate. Nothing
more precise than a whole number of points or percentage points is claimed.

The totals threshold stays in points because a live total is quoted in
points and the model's output for it is an expected final total; ML-4 must
also report the probability equivalent so the three markets can be compared.

## 11. Market-quality circuit breakers (proposed for v0.1, gate before any signal)

| breaker | rule as pre-registered |
|---|---|
| stale market guard | quote older than the freshness target (below) at `observed_at` → no signal |
| stale game-state guard | game-state observation older than one sampling interval, or ESPN clock unchanged across three in-play samples with no stoppage reason → no signal |
| event identity guard | market row not `join_status = joined` → no signal, ever |
| market/game join guard | phase must be `live` from game state; `unknown` or `commence_time`-based is not live |
| multi-book confirmation | the signal's price must be available at ≥ 2 Tier-1 books within 1 implied-probability point (the pregame corroboration guard, carried over) |
| duplicate recommendation guard | section 13 |
| same-market cooldown | no second signal on the same event + market family within 10 minutes of a signal, whatever the model says |
| recent-score instability guard | no signal within 90 seconds of a scoring play (the market re-prices; the model may not have) |
| recent-turnover instability guard | same 90 seconds after a change of possession on a turnover, where `last_play` reveals one |
| suspended-market guard | a book absent from `books_quoting` for that market is not a takeable price |
| impossible clock/state guard | any `anomalies` on the game-state row → no signal |
| price-movement guard | a quote that moved more than 2 implied points between the two most recent samples → no signal until it holds |
| event-ended guard | state `post`, or clock 0:00 in the 4th with no OT → no signal |
| halftime / state-transition guard | no signal during halftime, between quarters, or in the first sample after any period change |
| minimum data completeness | `missing_required` must be empty on the game-state row; score, period and clock all present |

**Freshness target: provisionally 90 seconds** for a live quote. The number is
NOT encoded in ML-1, which records every quote whatever its age, because the
right threshold depends on the provider's actual in-play update cadence, which
has not been measured. ML-1's `quote_age_s` distribution over at least five
game sessions is the evidence; if the median in-play refresh exceeds 60 s the
90-second figure is unusable and will be replaced BEFORE any shadow signal is
generated, as a recorded amendment. The reason is written here so it cannot be
re-tuned to the results later.

## 12. Chase prevention

Mercer Live must never become an automated martingale. Pre-registered: no
loss-based stake increases; no "get even" logic; no automatic averaging down;
no increased exposure because an earlier play lost; no stake determined by
emotional or prior-result state. Future position sizing depends only on
predefined model and risk inputs — the signal's edge bucket and the exposure
limits in section 14 — and the sizing function must be pure in those inputs.

## 13. Duplicate-position rules

A **thesis** is (event, market family, direction): e.g. (game, total, under).
Pre-registered principle:

- **one open position per thesis per game.** Under 52.5 followed three
  minutes later by Under 51.5 is the same thesis at a different price: the
  second is a diagnostic ("would have re-qualified"), never a new position.
  Market movement alone is never new information for this purpose.
- **a reversal** (over after under) is a new thesis, is recorded as one,
  counts against the per-game exposure cap, and is tagged `reversal` so the
  evaluation can look at reversals separately.
- **correlated exposure** is counted as one: spread and moneyline on the same
  side of the same game are one directional thesis; at most one directional
  and one total position per game.
- **genuinely new model information** (a score, a turnover, an injury) may
  re-qualify a thesis only after the instability guards in section 11 clear
  and never adds to an existing open position.

The system is re-evaluating every minute; that cadence must not manufacture
plays from one idea.

## 14. Exposure boundaries (future shadow sizing, paper units)

- max **1.0u** per live signal
- max **2.0u** aggregate exposure per game
- max **5.0u** aggregate Mercer Live exposure per day

Checked against existing conventions: MLB's tiers cap a single play at 3u
under a 10u daily cap for a staked product with a public record; football and
the Mercer Spotlight stake 0u through their proving windows. For an unproven
live system the figures above are deliberately smaller than the MLB product's
and are paper only. They will not be raised to improve a historical number.

## 15. Separate ledger

`data/mercer_live/` is the record. ML-1 writes only raw observations there
(gitignored, manifest committed). The future shadow ledger will be
`data/mercer_live/shadow_ledger.json` (append-only, the repo's JSON pattern —
there is no database layer in this repository and none is introduced here)
and will carry, per position: signal_id, event_id, sport, league, market,
side, point, price, book, model value, market value, edge, timestamp, game
state at signal, units, status, result, P/L, model version, rules version,
market-data source/version, game-state source/version, and the `obs_id`s of
the raw observations it was computed from. ML-1 creates no graded
recommendations and no ledger file.

## 16. No silent edits

Once a shadow recommendation exists: never delete a loss; never alter an
entry after grading; never change the original line, the observed market, or
the original timestamp; never fix a model output after seeing the result.
Corrections are explicit amendments with provenance (a new entry referencing
the old, both kept), exactly as the Mercer Spotlight renders an
"edited after grading" block rather than hiding the edit.

## 17. Model versioning

Every future signal carries `model_version`, `rules_version`,
`market_data_version` (source + schema), and `gamestate_version` (source +
schema). ML-1 already stamps `schema_version` and `capture_version` on every
row and tick. Results from materially different versions are never pooled
without saying so.

## 18. Discord boundary

| mode | output | allowed when |
|---|---|---|
| **Mode 1 — Shadow** | none | now |
| **Mode 2 — Private beta** | a restricted admin/test channel only | after section 3 is met AND Daniel authorises it in writing |
| **Mode 3 — Public / member live desk** | members channel | only after explicit promotion under section 3 |

No code in ML-1 activates Mode 2 or Mode 3, and the ML-1 self-test proves the
package contains no Discord code path at all.

## 19. D.J. Mercer persona boundary

Mercer may eventually explain a deterministic signal, summarise the model's
current state, say why a play qualified or failed a gate, summarise the
record, and discuss observed game state. Mercer may not invent an unmodeled
play, override a circuit breaker, increase an allocation, turn a PASS into a
PLAY, manufacture confidence, or conceal losing history. If the LLM is
unavailable the engine behaves identically: the presentation layer is
optional, the decision layer is not.

## 20. Legal boundary

Any future actionable posting carries the standing requirements unchanged:
analytics not a sportsbook, no bets accepted, 21+, 1-800-GAMBLER, and the
no-guarantee / not-betting-advice language (House Rule 5, `post_discord.FOOTER`).
Nothing in v0.1 posts anywhere.

## 21. Version history

| version | date (2026) | change |
|---|---|---|
| ml-v0.1 | Sep 12 | Recorded before any live observation. Purpose, shadow-only scope, 100-position promotion floor with the full evaluation list, no-look-ahead rule, observation-time semantics, append-only raw store, field classification, provisional thresholds (3.0 pts totals; 4.0 pp moneyline and spread, spread in probability terms with the reason), circuit-breaker set with the 90 s freshness target left unencoded pending measurement, chase prevention, duplicate/correlation rules, paper exposure limits, separate ledger, versioning, three Discord modes with only Mode 1 permitted. |

frozen: NOT YET
