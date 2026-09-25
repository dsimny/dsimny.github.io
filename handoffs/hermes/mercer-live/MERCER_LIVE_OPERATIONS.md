# Mercer Live — operations playbook

The working document. `MERCER_LIVE_SYSTEM.md` explains what the machine is; this
explains what to do with it, and when.

Hermes operates. Hermes does not code. Every fix in this playbook is either a
setting Daniel authorises, or a ticket written for Claude.

---

## 0. The standing objective

> **Operate and observe the frozen ML-1 live observation system while
> accumulating real football observations. Do not generate or publish live
> betting recommendations.**

Two halves. The first means: capture runs happen, they are checked, and what
they produced is written down. The second means: nothing that comes out of those
runs is ever turned into a suggestion about a wager, however informal.

---

## 1. Before a live football window

Run through this in the hour before kickoff of whatever slate is being observed.

1. **Confirm the objective for this window.** Which sport, which games, how many
   ticks, what cadence. Write it down before the run, not after.
2. **Confirm credit posture.** Default is **no spend**. If Daniel has authorised
   spend for this specific window, record the authorisation — who, when, what
   ceiling — in the observation log entry for that window. Absent an explicit
   authorisation, `spend_credits` stays `false` and `--no-odds` is used.
3. **Check the code gate is green.** `mercer-live-selftest.yml` on the current
   `main`. A red gate means the code is not in a known state; do not run a
   capture against real providers to "see what happens".
4. **Check the credit ledger if spending.** `data/odds_credits.json` — the latest
   remaining balance must clear the 5,000 floor with room, and the ET day's
   Mercer Live spend must be well under the 4,000 cap.
5. **Allow for ESPN's kickoff lag.** ESPN did not flip a game to
   `status_state: in` until roughly **32 minutes after nominal kickoff** on
   2026-09-21 (8:15 PM nominal, 8:47 PM actual). Plan the first tick for at
   least 30–45 minutes after the scheduled start if the goal is in-progress
   observations. Firing at nominal kickoff buys pregame records.
6. **Decide the tick count honestly.** The smoke workflow caps at 10 ticks. More
   observations are better evidence, but a run that outlives the interesting
   part of the game is just noise with a timestamp.

## 2. During a window

There is nothing to steer. The capture either runs or it does not.

- Do not intervene mid-run. Do not restart a run because a number looks odd.
- Do not form or voice a read on the game. Watching a live feed and saying "that
  line looks slow" is the exact failure mode house rule 3 exists to prevent.
- If a run dies, note the time and the failure and let it stay dead. A retry
  with the **same** `run_id` is safe and idempotent; a retry with a **new**
  `run_id` produces new observations, which is also fine but is a different
  sample. Record which was done.

## 3. After a window — the post-run check

This is the core operational deliverable. Work from the job log and the artifact.

**Completeness**
- Number of ticks requested vs run records written. Any gap is an anomaly.
- Number of `game_state` observations vs ticks × games observed.
- `missing_required` on every observation. Should be empty on healthy live
  payloads. A non-empty required list is an escalation.
- `missing_optional` — informative, not alarming. It is legitimately non-empty
  for pregame and post states.

**State progression**
- Does the clock move *monotonically downward* within a period?
- Does the period advance, and is the transition visible in `last_play`?
- Do possession, down, distance and yard line change over the run?
- **Any backwards jump is the headline finding of that run**: decreasing period,
  a clock that increases within a period, a decreasing score, or a `post → in`
  transition. Record it with both observations' `observed_at` and every field
  that moved. This is the evidence the deferred stale-state rule is waiting on.

**Identity**
- The `canonical_event_id` for each game, and whether it matches expectation.
- Any join that resolved to `UNJOINED`, `AMBIGUOUS`, `KICKOFF_MISMATCH` or
  `UNRESOLVED`, with its reason. These are findings, not failures.

**Retry proof**
- The smoke workflow re-runs the first tick's `run_id` and diffs shard
  checksums. Confirm `retry changed no shard byte`, and confirm the observation
  count equals the tick count.

**Integrity**
- Distinct `observation_id` count equals total observation count.
- Distinct `observed_at` count equals total (identical *values* across two ticks
  are fine and expected; identical *timestamps* would not be).
- Digest present for the ET date, with `kind` reading `game_state` /
  `market_event` / `market_quote` in full — never a truncated `game` or
  `market`. That truncation was a real bug, fixed; this check is the guard
  against its return.

**Credits**
- If the run spent: the reading before and after, the delta, and whether it was
  booked. If the run did not spend: confirm the balance is unchanged.

Write the result into the observation log (section 5) whether it was clean or
not. A clean run recorded is how an unclean run later becomes legible.

## 4. Weekly — the system-health report

One per week, covering the slate weeks observed. Sections, in this order:

1. **Runs.** Every run: id, date, sport, tick count, states observed, credits
   spent. A table, so totals can be checked rather than trusted.
2. **Observation totals.** Cumulative observations by state
   (pregame / in progress / final), by sport, by slate week. Progress toward the
   ML-2 precondition of **≥ 2 slate weeks of complete records**.
3. **Data quality.** Missing-required rate with its denominator. Missing-optional
   rate. Join outcomes by category. Failed ticks / total ticks.
4. **Anomalies.** Every backwards jump, every stale-looking payload, every
   refused join, every provider error — each with `observed_at` and the fields
   involved. This section is the most valuable thing Hermes produces, because
   the three deferred preregistration items can only be settled from it.
5. **Credits.** Mercer Live spend this week, running total, and headroom against
   floor and cap. Zero is a perfectly good number to report.
6. **Open tickets.** What is with Claude, what is with Daniel, what is blocked.
7. **Boundary confirmation.** An explicit line: no live plays generated, no live
   plays published, no Discord output, shadow mode intact.

Section 7 is not ceremony. A weekly report that stops asserting the boundary is
a report from a system that has quietly stopped observing one.

## 5. The observation log

Keep a running record, one entry per capture window, with:

- date, sport, games, run ids, tick count and cadence
- the objective stated *before* the run
- credit authorisation (or its explicit absence)
- the post-run check results from section 3
- anomalies, verbatim where possible
- anything that would be needed to reproduce or explain the run in six months

This log is bookkeeping, not analysis. It records what happened. It does not
record what it might mean for a wager.

## 6. Recurring cadence

| when | what |
|---|---|
| before each capture window | section 1 pre-flight |
| after each capture window | section 3 post-run check, logged |
| weekly | section 4 system-health report |
| whenever an anomaly appears | `MERCER_LIVE_ESCALATION.md` triage, ticket if warranted |
| whenever the roadmap moves | update `MERCER_LIVE_ROADMAP.md` status |

## 7. Things that look like emergencies and are not

- **ESPN is late flipping a game to `in`.** Normal; measured at ~32 minutes once.
  Adjust the schedule, not the code.
- **Consecutive identical observations.** Normal during a timeout or a stoppage.
  Observed twice at 10:34 on 2026-09-25 with every field identical. Both stored,
  distinct ids, distinct `observed_at`. That is the append-only rule working.
- **`missing_optional` non-empty on pregame or final states.** Expected. Fields
  sourced from `situation` are simply absent once a game is over.
- **A `0:00` clock.** Real at the end of a period in progress, and also what
  ESPN sends before kickoff and after the final. Gate on `status_state`.
- **A run that observed nothing.** If no game was live and none was near
  kickoff, a tick that writes only a run record is correct behaviour.

## 8. Things that are escalations

- Any backwards jump in game state.
- Any `missing_required` field on an in-progress observation.
- Any join refusal that repeats across runs for the same fixture.
- Any duplicate `observation_id`, or any observation the store accepted twice.
- Any write outside `data/mercer_live/`.
- Any credit spend that was not authorised for that run, or any booking that did
  not reach `data/odds_credits.json`.
- Any digest whose `kind` values are truncated.
- A red `mercer-live-selftest.yml` on `main`.

Triage them with `MERCER_LIVE_ESCALATION.md`. Write the ticket; do not write the
fix.
