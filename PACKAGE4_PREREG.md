# OLP-M1 Package #4 — Market Intelligence Layer

## PRE-REGISTRATION — written before implementation

**Status:** pre-registered, not implemented. This document fixes the definitions
*before* code exists so the implementation can be judged against a stated
contract rather than against itself.

Locked decisions from architectural review, carried verbatim:

1. Best price and consensus are **same-line only**, keyed by
   `(event, market, selection, line)`.
2. A separate **modal/consensus line** is reported; no cross-line normalisation
   in Package #4.
3. **Multiplicative de-vig is the production default**; `devig_method` is
   recorded per row.
4. Shin may exist behind config but is **not required** for completion.
5. `market_quality` is **advisory** in the canonical surface.
6. A separate **execution-safe surface gates** poor-quality rows.
7. **Explicit quality reason codes** are preserved, never collapsed into one
   opaque label.
8. Implementation in **SQL views/RPCs** wherever possible — the database owns
   the domain rules.

---

## 1. Canonical key

```
(event_id, market_type, selection, line)
```

The **sportsbook is deliberately not in the key** — aggregating across books is
the entire purpose. Each contributing book supplies exactly one observation: its
**newest** non-in-play quote for that key, per Package #2 ordering
(`captured_at DESC, ingest_seq DESC`).

`MONEYLINE` carries `line IS NULL`. PostgreSQL `GROUP BY` treats NULLs as one
group, so the key works unmodified; every join on `line` must use
`IS NOT DISTINCT FROM`, never `=`, or moneyline rows silently vanish.

**Scope:** events where `is_closed = FALSE`. In-play observations are excluded
entirely, consistent with Packages #1–#3.

---

## 2. Best price

Within one canonical key, the best price is the one paying the most per unit
staked:

```
payout(price) = public.olp_american_profit(1, price)
best_price    = the price maximising payout
```

Using the existing helper rather than a second odds implementation is
deliberate — a duplicate formula is how two parts of a system come to disagree.

**Comparing raw American odds numerically is forbidden.** `+100` and `-105` are
adjacent in probability but 205 apart as integers, and the sign flip at ±100
makes `MAX(price)` wrong in both directions.

**Eligibility:** a book contributes to best price if its newest observation for
the key is non-in-play and within `snapshot_ttl_seconds`. It does **not** need a
de-vig partner — a book quoting one side only can still offer the best price
while being excluded from consensus. Best-price eligibility and consensus
eligibility are different sets, by design, and both counts are reported.

**Tie-break** (two or more books at the identical best price), in order:
`sportsbook ASC`. Alphabetical rather than freshest, because "freshest" makes
`best_book` flap on every poll and downstream consumers would read that noise as
signal. `best_price_book_count` reports how many books share it, so a tie is
visible rather than hidden behind an arbitrary winner.

---

## 3. Consensus

**Order of operations is de-vig per book, then aggregate across books.**
Aggregating raw implied probabilities first and de-vigging the average is wrong,
because books carry different overrounds and the average would inherit a blend
of them.

```
1. per book: raw implied probability for this selection and its partner
2. per book: de-vig that pair          -> fair probability for this selection
3. across books: median of fair probabilities  -> consensus_probability
4. invert: consensus_price = fair American odds for consensus_probability
```

**Median, not mean** — with 3–10 books a single stale or erroneous quote moves a
mean materially and a median barely at all.

`consensus_price` is a **fair, vig-free** price. It is deliberately not
comparable to a book's posted price without remembering that; the field is named
so at the point of use.

### 3.1 Implied probability from American odds

```
price >= +100 :  p = 100 / (price + 100)
price <= -100 :  p = |price| / (|price| + 100)
```

### 3.2 Fair American odds from probability

```
p >  0.5 :  price = round(-100 * p / (1 - p))
p <= 0.5 :  price = round(+100 * (1 - p) / p)
```

`p = 0.5` yields `+100`; the `-100`/`+100` boundary is resolved by convention
rather than left ambiguous. `p <= 0` or `p >= 1` is undefined and yields NULL
with reason `DEGENERATE_PROBABILITY`.

### 3.3 De-vig — multiplicative (production default)

For a two-way market with raw implied probabilities `p₁`, `p₂` from one book:

```
booksum = p₁ + p₂                    (the overround; > 1 in a normal market)
fair₁   = p₁ / booksum
fair₂   = p₂ / booksum
```

`overround = booksum - 1` is retained per book for diagnostics.

`devig_method` is recorded on every row. `SHIN` may be added behind config; it
is **out of scope for completion** and its absence is not a defect.

### 3.4 De-vig partner rule — market-type aware

De-vigging requires both sides **from the same book**. The partner is:

| Market | Partner |
|---|---|
| `MONEYLINE` | same event, same book, other selection, `line IS NULL` both |
| `SPREAD` | same event, same book, other selection, **`line = -line`** |
| `TOTAL` | same event, same book, `OVER`↔`UNDER`, **same `line`** |

The spread case is the one that bites: `DAL -3` pairs with `PHI +3`, at a
*different* line value. That is not a cross-line comparison — it is the same
proposition seen from both sides, which is precisely what an overround is.

**If a book posts `DAL -3` and `PHI +3.5`, there is no valid pair.** That book is
excluded from consensus with reason `NO_DEVIG_PAIR` and is *not* silently paired
with the nearest line. This is the single most likely place for cross-line
leakage to enter, and it is closed by construction.

---

## 4. Stale-book exclusion

A book's observation is excluded when:

- `captured_at` is older than `snapshot_ttl_seconds` (**120s**, reused from
  Package #1), or
- `is_in_play = TRUE`.

**The existing TTL is reused deliberately. Package #4 introduces no second
freshness constant.** Two freshness rules that can drift apart is exactly the
defect corrected in `pkg3-v1.1`; the canonical market must age out on the same
clock the ledger already trusts.

If every book for a key is stale, the row still appears in the canonical surface
with `book_count = 0`, quality `UNUSABLE`, reason `ALL_BOOKS_STALE`. It is
absent from the executable surface. Silence and staleness must be
distinguishable — the same principle as `market_feed_health`.

---

## 5. Outlier rule

Applied to de-vigged probabilities, after staleness exclusion:

```
IF book_count >= mi_outlier_min_books (default 4):
    median_p = median(fair probabilities)
    exclude any book where |fair_p - median_p| > mi_outlier_probability_delta
                                                 (default 0.10, i.e. 10 points)
    never exclude more than floor(book_count / 3) books
```

**No outlier removal below 4 books.** With three books, removing one leaves two,
and "the outlier" is indistinguishable from "the two that agree are both wrong".
Small-n outlier detection is a way of manufacturing confidence.

Every exclusion is counted in `outliers_excluded` and flagged
`OUTLIERS_REMOVED`. Removal is never silent.

---

## 6. Modal line

Reported per `(event_id, market_type, selection)` — one level **above** the
canonical key, because it describes which line the market has settled on.

```
modal_line = the line quoted by the most distinct eligible books
```

**Tie-break: book count DESC, then `line ASC`.** Total and explicit — line
values within a group are distinct by construction, so exactly one row can win
and nothing depends on query order.

Recency was considered and **rejected**. It is deterministic but not *stable*:
with two lines tied on book count, whichever book updated last would take the
modal flag, so the modal line would flip between polls while the market had not
moved. That is the same flapping the `best_book` tie-break avoids by preferring
alphabetical order over freshest. A modal line that changes when nothing changed
is worse than an arbitrary but steady one.

*(Caught during implementation: the original rule placed recency ahead of
`line ASC`, and a deliberate 2-vs-2 tie test exposed the instability.)*

Also reported: `modal_line_book_count`, and `distinct_line_count` — the number
of different lines live at once, which is itself a market-instability signal.

The canonical row carries `is_modal_line BOOLEAN` so a consumer can filter to
the market's primary number in one predicate without performing the comparison
Package #4 refuses to make for it.

---

## 7. Movement and opening line

Two movements are reported, kept separate on purpose.

**Price movement — within a line.** For one canonical key:

```
opening_probability = consensus computed from each book's EARLIEST observation
current_probability = consensus computed from each book's NEWEST observation
probability_movement = current - opening      (probability points)
movement_direction   = IN   if movement >  mi_movement_epsilon (default 0.005)
                       OUT  if movement < -mi_movement_epsilon
                       FLAT otherwise
```

`IN` means the price shortened (probability rose). Both endpoints carry
timestamps: `opening_captured_at`, `current_captured_at`.

**Line movement — across lines.** `modal_line` compared against
`opening_modal_line` (the modal line at the earliest observation time for that
event/market/selection), reported as `line_movement = modal_line -
opening_modal_line`.

These are deliberately **not combined into a single "movement" number**. Doing so
requires converting a half-point of spread into probability, which is modelling
and belongs to Package #5. Reporting them separately is the honest form.

**Opening is our first observation, not the true market open.** If ingestion
began after a market opened, `opening_*` reflects when Open Ledger started
watching. `opening_is_first_observation` is always true and the field name is
chosen so no consumer mistakes it for a market-wide opening line.

---

## 8. Quality — advisory, with explicit reasons

The canonical surface carries **both** a summary label and the full reason list.
The label is advisory; the reasons are the record.

```
market_quality  ∈ { OK, DEGRADED, UNUSABLE }
quality_reasons TEXT[]      -- every applicable code, never collapsed
```

| Reason code | Raised when | Severity |
|---|---|---|
| `LOW_BOOK_COUNT` | eligible books < `mi_min_book_count` (3) | DEGRADED |
| `SINGLE_BOOK` | exactly 1 eligible book | UNUSABLE — **fails closed** |
| `NO_ELIGIBLE_BOOKS` | 0 eligible books | UNUSABLE |
| `ALL_BOOKS_STALE` | every observation older than the TTL | UNUSABLE |
| `NO_DEVIG_PAIR` | no book had a complete two-sided pair | UNUSABLE |
| `PARTIAL_DEVIG_COVERAGE` | some books pairable, some not | DEGRADED |
| `WIDE_DISPERSION` | `dispersion > mi_dispersion_wide_threshold` (0.05) | DEGRADED |
| `OUTLIERS_REMOVED` | ≥ 1 book excluded as an outlier | DEGRADED |
| `DEGENERATE_PROBABILITY` | consensus p ≤ 0 or ≥ 1 | UNUSABLE |
| `LINE_FRAGMENTED` | `distinct_line_count > mi_line_fragmentation_max` (3) | DEGRADED |

`market_quality` = `UNUSABLE` if any UNUSABLE reason applies; else `DEGRADED` if
any DEGRADED reason applies; else `OK`. **A row is never withheld from the
canonical surface** — a consumer may always see the raw truth and decide.

`dispersion` = `max(fair_p) - min(fair_p)` across contributing books, in
probability points. Chosen over standard deviation because the unit is
interpretable without knowing n.

---

## 9. Execution-gating contract

A **separate** view. Everything the canonical surface reports advisorily, this
one enforces.

A row appears only if **all** hold:

1. `market_quality <> 'UNUSABLE'`
2. `WIDE_DISPERSION` not among `quality_reasons`
3. `book_count >= mi_execution_min_book_count` (default **2**)
4. event is not closed, not live, `actual_start_time IS NULL`,
   `NOW() < current_scheduled_start`
5. the best-price observation is within `snapshot_ttl_seconds`
6. **the best-price snapshot is still the newest quote for its own book** —
   the exact `MARKET_MOVED` condition in `place_ticket_rpc`

Condition 6 is not optional. `current_market_board` already establishes the rule
that a surface offering a price must agree with the RPC that accepts it; an
execution surface that could offer a superseded snapshot would reintroduce the
disagreement Package #2 §3.9 exists to prevent.

The view exposes `best_snapshot_id`, so a consumer passes it **straight to
`place_ticket_rpc`** with no re-derivation. Any transformation between "what the
model chose" and "what was placed" is a place for the two to diverge.

**Package #4 writes nothing.** No ledger table, no market table, no RPC that
mutates. It is a read layer over Package #2/#3 data.

---

## 10. Canonical consumer contract

```
event_id, source_event_id, commence_time
market_type, selection, line
best_price, best_book, best_price_book_count, best_snapshot_id
consensus_price, consensus_probability, devig_method
book_count, devig_book_count, dispersion, outliers_excluded
modal_line, is_modal_line, modal_line_book_count, distinct_line_count
opening_probability, probability_movement, movement_direction
opening_captured_at, current_captured_at
line_movement, opening_modal_line
market_quality, quality_reasons
```

Deliberately absent: any blended cross-line price, any single "movement" scalar,
any model-facing EV or edge. Those belong to Package #5.

---

## 11. Configuration

Added to `public.system_settings`, consistent with existing practice:

| Setting | Default |
|---|---|
| `mi_min_book_count` | 3 *(advisory only)* |
| `mi_execution_min_book_count` | **2** |
| `mi_dispersion_wide_threshold` | 0.05 |
| `mi_outlier_min_books` | 4 |
| `mi_outlier_probability_delta` | 0.10 |
| `mi_line_fragmentation_max` | 3 |
| `mi_movement_epsilon` | 0.005 |
| `mi_devig_method` | `MULTIPLICATIVE` |

No new freshness setting. `snapshot_ttl_seconds` is reused.

**Threshold rationale (set at review, 2026-08-27).** The advisory threshold and
the execution floor are deliberately different numbers. Live book coverage on an
NFL slate is heavily skewed — `draftkings` quoted all 272 events,
`williamhill_us` 256, `betus` 143, and the remaining seven books 16–32 each — so
an execution floor of 3 would gate roughly half the slate out on book coverage
rather than on market quality. The floor is therefore **2**, while
`mi_min_book_count = 3` remains as the *advisory* signal that a market is thin.

**One-book markets fail closed** by two independent rules: `SINGLE_BOOK` is
classed `UNUSABLE`, and 1 is below the execution floor of 2. Neither alone is
relied upon.

---

## 12. Tests — same-line isolation and no cross-line leakage

The proofs this package must pass. Written before implementation.

### Same-line isolation

| Test | Asserts |
|---|---|
| `P4-T01` | Books at `-3` and `-3.5` produce **two** canonical rows, never one blended row |
| `P4-T02` | `best_price` at `-3` ignores every `-3.5` quote, even when `-3.5` pays more |
| `P4-T03` | `consensus_probability` at `-3` is computed from `-3` books only |
| `P4-T04` | `TOTAL OVER 44.5` and `OVER 45` never merge |
| `P4-T05` | `MONEYLINE` (`line IS NULL`) groups correctly and never collides with a spread row |

### Cross-line leakage canary

| Test | Asserts |
|---|---|
| `P4-T06` | A slate constructed so that blending would give a *materially different* best price — the leak is detectable if present, not merely absent by luck |
| `P4-T07` | `SPREAD` de-vig pairs `DAL -3` with `PHI +3` and **never** with `PHI +3.5`; the mismatched book yields `NO_DEVIG_PAIR` |
| `P4-T08` | `TOTAL` de-vig pairs `OVER 44.5` with `UNDER 44.5`, never `UNDER 45` |
| `P4-T09` | Line movement `-3 → -3.5` appears as `line_movement`, and **not** as `probability_movement` on either line |

### Correctness of the arithmetic

| Test | Asserts |
|---|---|
| `P4-T10` | Implied probability and its inverse round-trip across `±100 … ±10000` |
| `P4-T11` | Multiplicative de-vig of a known pair reproduces hand-computed fair probabilities; `fair₁ + fair₂ = 1` |
| `P4-T12` | `best_price` uses payout ordering — `+100` beats `-105` beats `-110` |
| `P4-T13` | Median consensus is unmoved by one extreme book that would swing a mean |

### Quality and gating

| Test | Asserts |
|---|---|
| `P4-T14` | Every reason code is reachable and appears in `quality_reasons` |
| `P4-T15` | Reasons accumulate — a row can carry several at once, uncollapsed |
| `P4-T16` | Stale books excluded on `snapshot_ttl_seconds`; no second freshness constant exists |
| `P4-T17` | No outlier removal below `mi_outlier_min_books`; never more than a third removed |
| `P4-T18` | `UNUSABLE` rows appear in the canonical surface and are absent from the executable one |
| `P4-T19` | Every `best_snapshot_id` on the executable surface is **accepted by `place_ticket_rpc`** — the surface and the RPC cannot disagree |
| `P4-T20` | A superseded snapshot never appears on the executable surface (`MARKET_MOVED` parity) |

### Live-shape

| Test | Asserts |
|---|---|
| `P4-T21` | Against the 272-event / 4,552-quote live shape: canonical row count equals distinct `(event, market, selection, line)`, and query time is bounded |

---

## 13. Out of scope

No model, no edge, no EV, no confidence, no sizing — Package #5 and beyond. No
cross-line normalisation. No writes of any kind. No polling-cadence work.
