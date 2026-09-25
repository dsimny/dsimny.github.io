# `mercer-live-ml1-v1.0` — what the freeze means

**Status: FROZEN 2026-09-25.**

| | |
|---|---|
| Package | ML-1, the Live Observation Boundary |
| Freeze name | `mercer-live-ml1-v1.0` |
| Freeze commit | `984293f` |
| Merge commit on `main` | `795fb1d` |
| Schema version | `ml1-v1` |
| Odds API credits spent | **zero** |

The authoritative record is section 19 of `docs/MERCER_LIVE_ML1_ARCHITECTURE.md`.
This file explains what it means operationally.

> A note on form: the repository has no git tags and never has. Open Ledger
> packages freeze in prose, in the architecture document, the same way Open
> Ledger Play's Package 3 did. The freeze name is a name, not a ref.

---

## What "frozen" means here

Frozen means the **contract** is settled and will not drift. It does not mean
the code is finished forever, and it does not mean the code is perfect. It means
that from here, a change to the contract is a visible, dated, deliberate event
rather than a commit.

A useful comparison: it is closer to publishing a measuring instrument's
calibration certificate than to sealing a box. The instrument still gets used
every week. What is frozen is the statement of what it measures and how.

## What is frozen

- **The record schema `ml1-v1`** and its three kinds: `game_state`,
  `market_event`, `market_quote`.
- **The canonical event id** `<sport>:<slate_week_et>:<AWAY>@<HOME>` and the ET
  anchor behind `slate_week_et`.
- **The join rules**, including every named refusal: `JOINED`, `UNJOINED`,
  `AMBIGUOUS`, `KICKOFF_MISMATCH`, `UNRESOLVED`.
- **The observation-time semantics**: `observed_at` is when we received;
  `provider_timestamp` is the provider's own and is `null` for ESPN.
- **The append-only store**: deterministic observation ids, confined writes,
  gitignored raw stream, committed daily digests.
- **The phase label**, and the rule that only the exact string `pregame` may be
  read as pregame.
- **The credit guards**: floor 5,000, daily cap 4,000, hourly booking throttle.
- **The package's non-goals** — the list in "what this freeze does not
  authorise" below.

## What is NOT frozen, and never was

These are **operational settings**, not contract. They can change without
amending anything:

- the capture cadence (one minute is a recommendation, not a rule)
- the lead window before kickoff
- the markets string
- the choice of scheduler and host

## The evidence behind it

All ESPN-only. Zero Odds API credits spent.

**23 observations across 5 smoke runs on two game nights** — 14 in progress,
5 final, 4 pregame.

| run | date | ticks | states observed |
|---|---|---:|---|
| 35644644064 | 2026-09-21 | 2 | 2 pregame (rehearsal, before kickoff) |
| 35673309148 | 2026-09-22 | 5 | 1 pregame, 4 in progress |
| 35682762374 | 2026-09-22 | 6 | 1 in progress (the stale payload), 5 final |
| 36080139388 | 2026-09-25 | 8 | 1 pregame, 7 in progress |
| 36080802566 | 2026-09-25 | 2 | 2 in progress (identical pair) |

Covering, specifically: a period transition; a two-hour-stale payload; two
byte-identical consecutive observations during live play; and a confirmed retry
that changed no shard byte.

Plus **22 test suites green**, including **229 hermetic checks** in
`selftest_mercer_live.py`, under Python 3.12.

## The amendment, and why it is visible

The freeze paragraph originally read "nineteen live observations across four
runs". Both numbers were wrong — a miscount on my part, not a change of
evidence. The run ids and tick counts are now listed so the total can be checked
rather than taken on trust, and the underlying runs, logs and artifacts are
unchanged.

It was recorded as a dated amendment rather than edited silently, because **a
frozen record whose numbers move without a note is worth nothing.** That is the
pattern for every future correction here.

## What would reopen it

- **A live defect found in the capture path.** The correction ships as a dated
  amendment to the architecture document, not by rewriting the freeze record.
- **Adding a field, a record kind, or a second provider.** That is a **new
  package**, not an edit here.

Nothing else. Not a result, not a preference, not a convenience.

## What the freeze does NOT authorise

No model. No candidate. No signal. No unit. No live ledger. No Discord mode. No
site surface. No public claim of any kind.

ML-1 observes. Everything downstream still has to earn its place.
