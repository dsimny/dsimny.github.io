# Mercer Live — roadmap, ML-1 through ML-8

Documented, not built. Each package has a **precondition**, and the precondition
is a gate rather than a suggestion: complexity has to earn its place, and a
package that starts before its precondition holds is building on a number nobody
has measured yet.

The canonical table is section 16 of `docs/MERCER_LIVE_ML1_ARCHITECTURE.md`.

---

| package | what it is | precondition | status |
|---|---|---|---|
| **ML-1** | Live observation boundary: ESPN game state + Odds API quotes, canonical identity, append-only store, phase labels, credit guards | — | **DONE, FROZEN** `mercer-live-ml1-v1.0` |
| **ML-2** | Game-state feature construction from ML-1 records: time remaining in seconds, score differential, possession and field-position encodings, pace, and **observed feed-cadence statistics** | ML-1 has **≥ 2 slate weeks** of complete records, and the smoke runs have settled which OPTIONAL fields exist | **NOT STARTED** |
| **ML-3** | Baseline live model — expected final total first (the preregistration's market order), win probability second; fitted on point-in-time features, versioned, evaluated against the de-vigged live market as the null | ML-2's coverage report clears the multi-book guard for the chosen family | not started |
| **ML-4** | Live market comparison and candidate generation at the frozen thresholds; consensus and de-vig from intact quotes | ML-3 beats or matches the market's calibration on held-out weeks | not started |
| **ML-5** | Circuit breakers (preregistration section 11) plus shadow positions with thesis-based dedup. Still no output anywhere | ML-4 produces candidates whose refusal reasons are fully recorded | not started |
| **ML-6** | Prospective shadow grading and the evaluation report the promotion gate requires | ML-5 has **≥ 100 independent positions** | not started |
| **ML-7** | Private D.J. Mercer Discord beta (Mode 2) — explanation over records only | the promotion review passes | not started |
| **ML-8** | Interactive Discord application / slash commands | ML-7 sustains use and adds nothing to the decision chain | not started |

---

## What ML-2 needs from Hermes

ML-2 is the next package, and it is **blocked on observations, not on
engineering**. Its precondition is data Hermes gathers:

1. **At least two slate weeks of complete records.** Complete meaning every
   REQUIRED field present on in-progress observations, across enough of a slate
   to be representative — not two Monday-night games.
2. **A settled answer on which OPTIONAL fields exist.** Partly answered already:
   `down_distance_text`, `possession_text` and ESPN's win probability **are**
   supplied during genuine live play (section 17 got this wrong from a stale
   payload; section 18 corrected it). More weeks would confirm it holds across
   NCAAF as well as NFL, and across different game situations.
3. **Feed-cadence statistics.** How often the payload actually changes; the
   distribution of quote age at observation for live quotes. This is the raw
   material for the deferred freshness limit.

Until those exist, ML-2 has nothing to build on and must not start.

## The three deferred preregistration items

These were deliberately left unset on 2026-09-14, with the reason stated in
advance: **measured provider behaviour sets them, never a result.** They are the
sharpest reason the observation log matters.

| item | provisional value | what settles it | deadline |
|---|---|---|---|
| **Quote freshness limit** (stale market guard) | 90 seconds | the measured p50/p95 distribution of `observed_at − last_update` for live quotes over **≥ 2 slate weeks**. May be fixed at or below 90 s, never above | before ML-4's first candidate |
| **Stale game-state rule** | 2 consecutive identical (period, clock, score) at 1-minute cadence | ML-1 evidence, and it must catch a **backwards jump** — not just a stalled feed. Must tolerate legitimate breaks (halftime, end of quarter) and the ~60 s after a period change | before ML-4's first candidate |
| **Multi-book confirmation feasibility** | — | whether enough books quote the same live market closely enough in time for a confirmation rule to be workable at all | before ML-3 chooses a family |

Note that the second item was **tightened** by ML-1 evidence, not loosened: the
two-hour-stale payload on 2026-09-22 proved a stalled-feed test alone would have
sailed straight past it.

## What is off the roadmap entirely

Not "later" — **not planned**, and requiring a fresh decision with its own
justification if ever proposed:

- Any path where an LLM originates a position.
- Any merging of live and pregame records.
- Any public live surface before the section-3 promotion passes and is recorded.
- Any automatic promotion of a shadow position to a real one.
- Any capture scheduled on GitHub Actions cron at live cadence.

## Maintaining this file

Hermes owns keeping the **status column** current and appending evidence as it
accumulates. Hermes does not change a precondition, add a package, or reorder
them. Those are Claude changes with Daniel's approval.
