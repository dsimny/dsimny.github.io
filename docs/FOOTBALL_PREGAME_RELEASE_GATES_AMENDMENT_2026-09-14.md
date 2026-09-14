# Amendment to the pregame release gates — the Mercer developmental track

Dated 2026-09-14. Authorized by Daniel's owner decisions of the same date.
`FOOTBALL_PREGAME_RELEASE_GATES_V1.md` is not edited and stays in force.

## What this amendment does

It records a **separate, human-researched release track** — the DJ Mercer NFL and
NCAA developmental cohorts — that the v1 gates did not contemplate. The v1 gates
govern an automated or model-based pregame strategy, and their Gate 2 (a
registered, successfully evaluated strategy) remains **BLOCKED** for that purpose.

The Mercer track is not a validated strategy and does not claim to pass Gate 2.
It may release selections only because it is presented and recorded as what it
is: human judgement supported by market and analytical data, never a proven
model and never a demonstrated edge.

## Conditions it operates under (all binding)

1. Registered before publication; prospective only; no backfill.
2. NFL and NCAA are separate strategies with separate records.
3. No model selects, ranks or sizes plays; no model edge is claimed. NCAA has no
   model at all.
4. Flat 0.25 units per play; at most 1 unit of developmental exposure per ET day
   across both cohorts combined.
5. Daniel approves the exact immutable artifact of every play before members see it.
6. The sealed artifact and its commitment are on origin/main before any send.
7. Release checks, idempotent create-only delivery records, read-back
   verification, and no automatic retry of an uncertain send
   (`MERCER_DEVELOPMENTAL_RELEASE_CONTROLS.md`).
8. Gate 3's operational minimum is met through the offline commissioning dry run
   on real upcoming games in each sport before the first member send.
9. `MERCER_DEV_PUBLICATION` stays off until commissioning is complete and Daniel
   switches it on.

## What it does not change

- The automated fp-v0.4 pipeline stays paused (`delivery_policy.PAUSED`).
- The September 8 incident slate stays invalidated and visible.
- The 2025 NFL holdout stays unspent.
- Mercer Live stays capture-only and unscheduled.
- The Daily Pick stays at 0 units.
