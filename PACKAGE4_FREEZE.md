# Package #4 — Market Intelligence Layer — Freeze Record

**Status: PENDING LIVE SIGN-OFF.** Everything below is settled except §6, which
one fresh live ingest fills in. Do not tag until §6 is complete.

Once tagged, this document is a **prospective contract**, on the same footing as
`PACKAGE2.md`: the semantics recorded here are not to be casually changed.

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

Migrations are append-only. `042`–`047` replace view bodies via
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
8. **Ranking is not money.** `olp_price_payout` ranks; `olp_american_profit`
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

## 5. Tests — 154/154

28 Package #4 tests (`P4-T01`…`P4-T28`) inside a 154-test suite, green on
**PostgreSQL 16.2** and **Supabase 17.6**.

Six defects were found during the package and each has a negative control
proving its test detects the defect rather than passing by construction:

| Defect | Found by | Fix | Negative control |
|---|---|---|---|
| Modal tie-break used recency → flapped between polls | a deliberate 2-vs-2 tie test | ordering rule | — |
| `line ASC` tie-break carried signed-direction bias → the two sides of a spread named different wagers (33.5%) | review of the sign-off output | `043` | `P4-T09` → `(-3.50, +3.00)`; `P4-T25` → `SPREAD 60/60` |
| `best_price` ranked with a rounding money function → named a worse price as best | `package4_signoff.sql` on a seeded board | `044` | `P4-T26` → `(-143, 'bookB')` |
| `+100` and `-100` tied on payout, so *positive > negative* was not absolute | review of the ordering rule | `045` | `P4-T26` → `(-100, 'bookA')` |
| Pipeline CTEs inlined and re-executed per output row → quadratic; check 3 ran >20 min | the live sign-off | `046` | `P4-T24` → `full scan took 14.5s` |
| Zero-crossing wager: both sides reported themselves favoured | the live sign-off, check 5 | `047` | `P4-T27` → `DAL -1.50 / PHI -1.50` |

The recurring lesson, recorded in `PACKAGE4_PREREG.md` §12.1 and §12.5: **an
invariant has to be tested on inputs that can actually violate it.** The modal
bias hid because the invariance tests ran on totals; the comparator bug hid
because the payout test used prices too far apart to collide.

## 6. Live sign-off — 8 PASS, 2 unexercised, 1 FAIL

Fresh capture, three polls: **272 events, 13,680 quotes, 10 bookmakers**
(The Odds API, NFL), at `14:09:45`, `14:16:01` and `14:17:53` UTC on
2026-08-28. Whole sign-off runs in **~16 s**; the first attempt, before `046`,
died on check 3 after twenty minutes.

*(The census line reads 273 events / 12 books: four stale `FIXTURE` quotes from
a prior test run are also in the table. They are 663 s old, contribute **0**
canonical rows and affect no check.)*

| # | Check | Result | Time |
|---|---|---|---|
| 1 | canonical = distinct fresh keys | **PASS** — 2,476 = 2,476 | 326 ms |
| 2 | modal = one per event/market/selection | **PASS** — 1,632 = 1,632 | 299 ms |
| 3 | executable subset, 0 orphans | **PASS** — 1,240 ⊂ 2,476 | 1,085 ms |
| 4 | market-quality distribution | UNUSABLE 48.7%, DEGRADED 40.4%, OK 10.9% | 311 ms |
| 5 | spread modal mirror violations | **FAIL** — 1 of 272 — *fixed in `047`, see below* | 1.8 s |
| 6 | de-vig pair failures | **PASS** — 0 unpaired; 1,238 paired wagers, worst deviation from 1 `0.000000` | 604 ms |
| 7 | cross-line leakage (two probes) | **PASS** — 0 and 0 | 935 / 288 ms |
| 8 | canonical-vs-executable substitutions | **UNEXERCISED** — 0 of 1,240, 0 impossible improvements | 2.1 s |
| 9 | movement rows / direction sanity | **UNEXERCISED** — 2,476 rows, 0 violations on both probes | 444 ms |
| 10 | execution handoff via `best_snapshot_id` | **PASS** — 0 on every sub-check | 427 ms |

Reason codes: `SINGLE_BOOK` 48.7%, `LOW_BOOK_COUNT` 40.0%,
`WIDE_DISPERSION` 1.2%, `LINE_FRAGMENTED` 0.3%. The large `SINGLE_BOOK` share is
the alternate-line long tail failing closed — the designed behaviour, not a
warning.

### Why checks 8 and 9 are recorded as unexercised, not passed

Across all three polls the market did not move **at all**:

```
4,560 quote series, 3 observations each
      0 with a price change   (max distinct prices per series = 1)
      0 with a line change
```

So `market_movement` reporting `FLAT` on all 2,476 rows is *correct*, and zero
substitutions is *correct* — but neither result can distinguish working code
from code that always returns zero. The cause is the slate, not the market being
quiet by chance: **no captured event kicks off within 24 hours.** The nearest is
298 hours out and the furthest 135 days. Lines that far from kickoff are close to
static, and the provider very likely serves an unchanged snapshot.

Validating these two checks requires a capture that spans real movement, which
means polling a slate near kickoff. That is a scheduling question, not a code
question, and it is carried forward as an open item rather than counted as a
pass.

### The check 5 failure

```
Cleveland Browns vs Atlanta Falcons
    Atlanta Falcons    modal_line  -1.50
    Cleveland Browns   modal_line  -1.50
```

Both sides report themselves favoured. Reproduced identically on two independent
captures, so it is deterministic, not a fluke.

This is **the exact residual case migration `043` documented as unresolvable by
any sign-blind rule**: the line has crossed zero. Books are split on who is
favoured in a near-pick'em, so Atlanta's candidate lines are `{-1.5, +1.5}` and
Cleveland's are `{-1.5, +1.5}`. Book counts tie, magnitudes tie at 1.5, and the
final `line ASC` tie-break picks the negative value **for each side
independently**.

Rate: 1 of 272 spread wagers, 0.37%.

No sign-blind, per-selection rule can fix it — for either selection the candidate
set is identical, so any deterministic function of that set returns the same
answer for both. The fix has to decide at the **wager** level and mirror outward:
pick the modal line once per `(event, market_type)` from a fixed reference side
(home for spreads, `OVER` for totals) and negate it for the other side. Mirrored
by construction; it changes nothing on the 271 wagers where the sides already
agree, and it leaves bookmaker count as the only substantive criterion.

### Resolved in migration 047

The fix was approved and implemented. Re-run against the same captured board:

```
=== 5. SPREAD MODAL MIRROR VIOLATIONS (must be 0) ===
 market_type | violations | wagers | verdict
-------------+------------+--------+---------
 SPREAD      |          0 |    272 | PASS
 TOTAL       |          0 |    272 | PASS

--- any violating wagers, named ---
(0 rows)
```

The Browns/Falcons wager now reads Cleveland `-1.50` / Atlanta `+1.50`, and
check 2 reports 1,634 modal rows across 1,634 selections with `several = 0` and
`none_at_centre = 0`.

**Remaining before freeze:** one clean sign-off from a fresh ingest — the run
above was replayed against a capture that had aged past the TTL — and checks 8
and 9 still need a slate near kickoff.

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
