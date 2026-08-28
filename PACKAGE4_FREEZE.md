# Package #4 — Market Intelligence Layer — Freeze Record

**Status: FROZEN as `pkg4-v1.0`, 2026-08-28.**

This document is now a **prospective contract**, on the same footing as
`PACKAGE2.md`: the semantics recorded here are not to be casually changed.

Frozen with one open item accepted deliberately: **sign-off checks 8 and 9 are
unexercised, not passed.** Three polls across two sessions produced zero price
changes and zero line changes, because the captured slate has no event inside 24
hours. Neither check can currently distinguish working code from code that
always returns zero. They need a capture spanning real line movement — polling
near kickoff — which is a scheduling matter, not a code one. Recorded in §6 and
carried in §8.

---

## 1. What Package #4 is

One canonical market view per `(event, market_type, selection, line)` that a
downstream model consumes without ever reading `market_snapshots`. It answers,
for a given game:

| Question | Field |
|---|---|
| Where is the market centred? | `modal_line`, `is_modal_line` |
| What does each side believe? | `consensus_probability`, `consensus_price` |
| Where is the best executable price? | `best_price`, `best_book` |
| Can it actually be taken? | `is_executable`, `executable_snapshot_id` |
| How much do books disagree? | `book_count`, `dispersion` |
| How has the market moved? | `probability_movement`, `line_movement` |
| Is it safe to act on? | `market_quality`, `quality_reasons` |

Package #4 **writes nothing.** It is a read layer.

## 2. Migrations frozen

| Migration | Contents |
|---|---|
| `037` | Config columns, `market_quality` enum, pure odds math |
| `038` | `canonical_market` |
| `039` | `executable_market` |
| `040` | `market_movement` |
| `041` | `market_intelligence` — the consumer contract |
| `042` | Partner lookup off the CTE onto the index; `PARALLEL SAFE` metadata |
| `043` | Modal tie-break symmetry (`abs(line) ASC`) |
| `044` | Exact payout comparator (`olp_price_payout`) |
| `045` | Total price ordering (`positive > negative` made absolute) |
| `046` | Materialise the pipeline CTEs (stop per-row re-execution) |
| `047` | Wager-level modal line (decided once, mirrored outward) |
| `048` | Same rule without a CTE-to-CTE join, so the planner can estimate it |
| `049` | `best_ties` off the same anti-pattern (latent since `038`) |

Migrations are append-only. `042`–`049` replace view bodies via
`CREATE OR REPLACE` rather than editing `038`/`039`/`040` in place, so any
database that applied an earlier migration reaches the same final state.

## 3. Semantics that must not drift

1. **Same-line isolation.** A canonical row is derived only from quotes at its
   own line. Never blend `-3` with `-3.5`. Live data showed 64.3% of spread and
   79.4% of total selections carry more than one line at once.
2. **De-vig partner rule.** `MONEYLINE` pairs on `NULL` lines, `SPREAD` on the
   negated line, `TOTAL` on the same line. A near miss yields `NO_DEVIG_PAIR`
   and is **never** paired with the nearest line.
3. **Modal line describes, never defines.** Bookmaker count is the only
   substantive criterion. `abs(line) ASC, line ASC` are deterministic
   tie-breakers with no economic meaning. The modal line is a property of the
   **wager**: decided once per `(event, market_type)` from a fixed reference
   side (home for spreads, `OVER` for totals) and mirrored to the other, so the
   two sides can never name different wagers. The reference side is a frame of
   reference, not a preference.
4. **Executable ≠ filtered canonical.** `executable_market` recomputes best
   price over only still-placeable observations, because `place_ticket_rpc`'s
   `MARKET_MOVED` check is line-agnostic. The surface and the RPC cannot
   disagree.
5. **One freshness constant.** `snapshot_ttl_seconds`, inherited from Package
   #1. Package #4 introduced no second TTL.
6. **Price movement and line movement stay separate.** Combining them requires
   converting spread points to probability, which is modelling and belongs to
   Package #5.
7. **`opening_*` is our first observation**, not the true market open.
8. **Prefer a shape the planner can estimate.** Three separate quadratic plans
   in this package traced to statistics-free CTEs (`042`, `046`, `048`). Inside
   this pipeline, favour an aggregate, a window, or a `DISTINCT ON` over ONE
   relation over a join between CTEs — a CTE-to-CTE join has no statistics on
   either side and its estimate bottoms out at 1 row, which makes nested loops
   look free.
9. **Ranking is not money.** `olp_price_payout` ranks; `olp_american_profit`
   pays. Never substitute one for the other. The ordering rule, in full:

   ```
   1. positive beats negative              (+100 > -100, +100 > -101)
   2. among positive, larger is better     (+101 > +100)
   3. among negative, closer to zero wins  (-101 > -102, -142 > -143)
   ```

   Applied at both selection points and nowhere else: `canonical_market.best`
   and `executable_market.exec_best` — the latter being also the promoted book
   after a quote is superseded by a line move. `best_price_book_count` compares
   integer prices with `=` and never needed a comparator.

## 4. Configuration

| Setting | Value | Role |
|---|---|---|
| `mi_min_book_count` | 3 | advisory quality only |
| `mi_execution_min_book_count` | 2 | execution floor; one-book markets fail closed |
| `mi_dispersion_wide_threshold` | 0.0500 | `WIDE_DISPERSION` |
| `mi_outlier_min_books` | 4 | no outlier removal below this |
| `mi_outlier_probability_delta` | 0.1000 | outlier distance from median |
| `mi_line_fragmentation_max` | 3 | fragmentation reason code |
| `mi_movement_epsilon` | 0.0050 | `IN` / `OUT` / `FLAT` band |
| `mi_devig_method` | `MULTIPLICATIVE` | recorded on every row |

`CHECK (mi_execution_min_book_count <= mi_min_book_count)`.

## 5. Tests — 155/155

29 Package #4 tests (`P4-T01`…`P4-T29`) inside a 155-test suite, green on
**PostgreSQL 16.2** and **Supabase 17.6**.

Seven defects were found during the package and each has a negative control
proving its test detects the defect rather than passing by construction:

| Defect | Found by | Fix | Negative control |
|---|---|---|---|
| Modal tie-break used recency → flapped between polls | a deliberate 2-vs-2 tie test | ordering rule | — |
| `line ASC` tie-break carried signed-direction bias → the two sides of a spread named different wagers (33.5%) | review of the sign-off output | `043` | `P4-T09` → `(-3.50, +3.00)`; `P4-T25` → `SPREAD 60/60` |
| `best_price` ranked with a rounding money function → named a worse price as best | `package4_signoff.sql` on a seeded board | `044` | `P4-T26` → `(-143, 'bookB')` |
| `+100` and `-100` tied on payout, so *positive > negative* was not absolute | review of the ordering rule | `045` | `P4-T26` → `(-100, 'bookA')` |
| Pipeline CTEs inlined and re-executed per output row → quadratic; check 3 ran >20 min | the live sign-off | `046` | `P4-T24` → `full scan took 14.5s` |
| Zero-crossing wager: both sides reported themselves favoured | the live sign-off, check 5 | `047` | `P4-T27` → `DAL -1.50 / PHI -1.50` |
| `047`'s mirroring join collapsed the `modal` estimate 200 → 1, so it was rescanned 2,480× | bisecting `047` against `046` | `048` | `P4-T24` full-board guard |

The recurring lesson, recorded in `PACKAGE4_PREREG.md` §12.1 and §12.5: **an
invariant has to be tested on inputs that can actually violate it.** The modal
bias hid because the invariance tests ran on totals; the comparator bug hid
because the payout test used prices too far apart to collide.

### 5a. Planner-health guard, and what it is for

**`P4-T29` detects severe cardinality underestimation that causes pathological
repeated execution of an intermediate relation. It does not prohibit efficient
planner-selected materialisation.**

Two fixtures, exercising different planner regimes:

| Fixture | Shape |
|---|---|
| dense | 60 events × 3 markets × 2 sides × 5 books × 2 lines |
| single-event | 1 event, 1 market, 2 books |

But **timing on either fixture would have missed `047`**. Its collapse was
latent on a synthetic board — the estimate equally wrong, the clock fine —
because the surrounding plan happened not to nested-loop it. So the assertion is
on the plan:

```
underestimate_ratio = actual / max(estimated, 1)
repeated_rows       = actual × loops
```

Judged in two tiers, because two different things produce repeated work:

1. **Cardinality collapse** — the planner believes a relation is tiny and the
   repetition is *accidental*. Applied to relation scans (`CTE Scan`,
   `Seq Scan`, `Subquery Scan`, …): fails at `ratio ≥ 20`, `loops ≥ 20`,
   `repeated ≥ 10,000`.
2. **Deliberate planner choice** — `Materialize` exists precisely to make
   rescans cheap; a `Nested Loop` with many loops is the symptom whose cause tier
   1 already catches. These fail only at `ratio ≥ 100`, `loops ≥ 100`,
   `repeated ≥ 1,000,000`, and are otherwise **reported as advisory**.

Chasing tier 2 would mean complicating a 180 ms query to satisfy a test.

```
P4-T29 PASS  305 plan nodes checked, 0 pathological
  advisory (allowed):
    executable_market/Materialize      est=1 act=600  loops=1080  touches=648,000
    executable_market/Merge Right Join est=1 act=600  loops=1080  touches=648,000
```

`EXPLAIN (ANALYZE)` must execute the query, so the guard is bounded at 60 s per
plan and a timeout is itself a failure — unbounded, it hung for over eight
minutes on an 1,800-quote fixture. It is scoped to `canonical_market` and
`executable_market`; the other two views are compositions of these, so a
collapse in them originates here.

**Benchmark hygiene is mandatory.** Every performance or plan measurement starts
from a fresh seed, `VACUUM ANALYZE`, and a session that has not been replacing
view definitions. Numbers taken after DDL churn against stale statistics are not
slow results, they are **invalid** results — they produced a phantom "048
regression" that cost an afternoon.

### 5b. What the guard found on its first run

A latent instance of the same anti-pattern in `best_ties`, present since `038`
and untouched by `046`: `MATERIALIZED` stops a CTE being recomputed but not
rescanned. Fixed in `049`, byte-exact across all four views.

```
best_ties   without 049 (join form):    est   1 / act 600   ratio 600x
            with 049 (window form):     est 180 / act 600   ratio   3x
```

On PostgreSQL 16.2 the collapse manifested as 600 rescans — 360,000 row touches,
fatal. On 17.6 it stayed latent at `loops = 1`. Same estimate, different
consequence, exactly like `047`.

**This is the durable result: one performance bug became a planner-health
framework, and it immediately found an eleven-migration-old latent defect
nobody was looking for.** Timing tests detect symptoms; plan-structure guards
detect latent scaling failures.

## 6. Live sign-off — 8 PASS, 2 unexercised, 0 FAIL

Fresh capture: **272 events, 4,552 quotes, 10 bookmakers** (The Odds API, NFL),
2026-08-28. Whole sign-off ~10.5 s. The first attempt, before `046`, died on
check 3 after twenty minutes.

*(The census reads 332 events / 6,352 quotes / 15 books: an 1,800-quote
synthetic fixture from a prior test run shares the table. It is 10,260 s old,
contributes 0 canonical rows and affects no check.)*

| # | Check | Result | Time |
|---|---|---|---|
| 1 | canonical = distinct fresh keys | **PASS** — 2,472 = 2,472 | 315 ms |
| 2 | modal per selection; several / none-at-centre | **PASS** — 1,632 = 1,632, 0, 0 | 327 ms |
| 3 | executable subset, 0 orphans | **PASS** — 1,240 ⊂ 2,472 | **779 ms** |
| 4 | market-quality distribution | UNUSABLE 48.6%, DEGRADED 40.5%, OK 10.8% | 288 ms |
| 5 | spread modal mirror violations | **PASS** — 0 of 272 spread, 0 of 272 total | 608 ms |
| 6 | de-vig pair failures | **PASS** — 0 unpaired; 1,236 paired wagers, worst deviation `0.000000` | 571 ms |
| 7 | cross-line leakage (two probes) | **PASS** — 0 and 0 | 307 / 300 ms |
| 8 | canonical-vs-executable substitutions | **UNEXERCISED** — 0 of 1,240, 0 impossible improvements | 772 ms |
| 9 | movement rows / direction sanity | **UNEXERCISED** — 2,472 rows, 0 violations on both probes | 485 ms |
| 10 | execution handoff via `best_snapshot_id` | **PASS** — 0 on every sub-check | 496 ms |

**Check 3 went from over twenty minutes to 779 ms**, and **check 5 from 1
violation to 0** — `047`/`048` confirmed on real data, not just on fixtures.

### Plan evidence on the live board

The reason this closes, rather than merely passing:

```
canonical_market   CTE Scan on modal      est 200 / act 1992 / loops 1   ratio 10x
canonical_market   CTE Scan on best_ties  est 200 / act 3072 / loops 1   ratio 15x

pathological relation-scan rescans: 0
```

Under `047` that `modal` node was `est 1 / act 1634 / loops 2480`. The estimate
is sane and nothing is rescanned, on the dense shape that caused the pathology
in the first place.

One advisory node, and it is instructive: `executable_market`'s `Materialize`
measured `est 32 / act 6352 / loops 332` — **2,108,864 row touches**, tripping
every absolute threshold — while consuming **13.8%** of an 825 ms execution.
That is what forced `P4-T29`'s second tier onto share-of-execution-time instead
of row counts. Absolute counts do not transfer between board sizes; the same
healthy node reads 108,000 touches on the 1,800-quote fixture.

### Checks 8 and 9 remain unexercised

Recorded as *not disconfirming* rather than as passes. A single poll leaves
opening equal to current, so every row reads `FLAT` and no book has moved off a
line — the code cannot be distinguished from code that always returns zero.
Validating them needs a capture spanning real movement, i.e. polls against a
slate near kickoff. Carried forward as an open item.

### 6a. Migration 046 equivalence proof

`046` is a planner/performance correction, not a semantic change, and that is
proved rather than asserted. `scripts/verify_046_equivalence.py` emits a
transaction that installs the **pre-046** definitions of all four views into a
`zz_old` schema alongside the current ones, compares them, and rolls back.

Both versions run inside **one transaction**, so `now()` — and therefore the TTL
window and the pre-kickoff gate — is identical for both. The comparison is the
symmetric difference of the full-row JSON multisets:

```sql
(SELECT to_jsonb(t) FROM public.V t EXCEPT ALL SELECT to_jsonb(t) FROM zz_old.V t)
UNION ALL
(SELECT to_jsonb(t) FROM zz_old.V t EXCEPT ALL SELECT to_jsonb(t) FROM public.V t)
```

No column is named, so nothing can be quietly left out of the check, and it
catches differing values, differing row counts and changed duplicate
multiplicity alike.

| View | rows (old = new) | differing rows |
|---|---|---|
| `canonical_market` | 2,360 | **0** |
| `market_movement` | 2,360 | **0** |
| `executable_market` | 1,808 | **0** |
| `market_intelligence` | 2,360 | **0** |

That covers canonical keys, best price, consensus, modal lines, quality codes,
movement and execution gating together — every column of every row is identical.

Performance on the same board (272 events, 5,712 quotes):

| | before `046` | after |
|---|---|---|
| `canonical_market` | 14.7 s | **0.34 s** |
| `executable_market` | — | 0.40 s |
| `market_movement` | — | 0.49 s |
| `market_intelligence` | 43.8 s | **1.28 s** |
| sign-off check 3 | > 20 min | **~1.0 s** |

Regression guard: `P4-T24` asserts the full-board canonical scan stays under 5 s.
Negative control — stripping `046`'s markers makes it report
`full scan took 14.5s`.

## 7. Deliberately not done

- **No materialisation** of the CTE chain that `market_intelligence` evaluates
  three times. No consumer scans the whole board (one event ≈ 0.9 s), and
  materialising introduces the staleness surface this package exists to avoid.
- **No cross-line normalisation**, no model, no edge, no EV, no sizing — those
  are Package #5 and beyond.
- **No change to Package #2.** `ticket_closing_line_value` carries the same
  rounding collision `044` fixed, in `beat_close` and `payout_edge_per_unit`.
  `pkg2-v1.0` is frozen; this needs its own decision and is listed here so it is
  not lost.

## 8. Open items carried forward

- Rotate The Odds API key (pasted into a transcript 2026-08-27).
- Full audit of `olp_american_profit()` call sites is recorded in `045`'s
  header. Only `028` (Package #2) still ranks with it.
- Package #2 `beat_close` rounding collision — decision needed.
- Nothing schedules the ingestion worker; 3 credits per poll.
- **Sign-off checks 8 and 9 remain unexercised.** They need a capture spanning
  real line movement, i.e. polls against a slate near kickoff. The 2026-08-28
  capture had no event inside 24 hours (nearest 298 h out) and recorded zero
  price and zero line changes across three polls.
