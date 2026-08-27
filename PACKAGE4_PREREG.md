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

**Bookmaker count is the only substantive criterion.** The modal line is the
line the most distinct eligible books quote, and nothing else feeds into it.
Everything after `line_books DESC` exists solely to make the ordering total.

**Tie-break: `abs(line) ASC`, then `line ASC`.** These are deterministic
tie-breakers and **carry no economic meaning**. `abs(line) ASC` is not a claim
that smaller magnitudes are more central, more liquid, or more correct; it is
the cheapest sign-neutral total order available. Any interpretation of the
tie-break as a market signal is a misreading — when it fires, the market
genuinely has no single centre, and `distinct_line_count` is the field that says
so.

Two rules were considered and rejected before this one.

**Recency — rejected as unstable.** With two lines tied on book count, whichever
book updated last would take the modal flag, so the modal line would flip
between polls while the market had not moved. Deterministic but not *stable*.
This is the same flapping the `best_book` tie-break avoids by preferring
alphabetical order over freshest. A modal line that changes when nothing changed
is worse than an arbitrary but steady one. *(Caught during implementation: the
original rule placed recency ahead of `line ASC`, and a deliberate 2-vs-2 tie
test exposed it.)*

**Bare `line ASC` — rejected for signed-direction bias.** This one survived
longer because it is stable, order-independent and recency-independent, and the
invariance tests at the time only exercised totals. It is still wrong, for a
reason those tests could not see.

`line ASC` sorts a **signed** number, and it is evaluated **per selection** —
but the two sides of a spread carry opposite signs for the same wager. On a tie
the home side sorts over `{-3.5, -3.0}` and picks `-3.5`, while the away side
sorts over `{+3.0, +3.5}` and picks `+3.0`. Those are different wagers. The
market is then described as centred in two places at once, and which answer you
get depends only on which side you happened to ask about.

So `line ASC` does not express *no preference*. It expresses a standing
preference for the more negative number, which on a spread means a standing
preference for the favourite's larger number and the underdog's smaller one —
a directional bias smuggled in as a formatting rule. That is precisely the kind
of unearned economic content the modal line is not permitted to carry: it must
**describe** the market, never define truth about it.

Totals never exposed this, because `OVER` and `UNDER` mirror at the *same*
number, so both sides sort over the same set and agree by construction.
Moneylines have no line at all. Spreads are the only market where the defect can
appear, and the original invariance tests were written on totals.

Measured on a fully-tied 272-event slate: **33.5% of spread wagers** disagreed
across their two sides under `line ASC`; **0.0%** under `abs(line) ASC`. Fixed
in migration `043`; see §12.1 item 7 and tests `P4-T09` / `P4-T25`.

Taking the magnitude before the sign makes both sides sort over the same values,
so they resolve to the same wager. The trailing `line ASC` is retained only to
keep the order total when magnitudes are equal but signs differ — a line that
has crossed zero, e.g. one book at `DAL -3` and another at `DAL +3`. No
sign-blind rule can resolve that case symmetrically; it is left deterministic
rather than pretended away.

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

The proofs this package must pass. Pre-registered before implementation; the
table below is the **as-built** reconciliation, and §12.1 records every drift
from what was originally written.

### Same-line isolation

| Test | Asserts |
|---|---|
| `P4-T01` | Books at `-3` and `-3.5` produce **two** canonical rows, never one blended row |
| `P4-T02` | `best_price` at `-3` ignores every `-3.5` quote, even when `-3.5` pays more |
| `P4-T03` | `consensus_probability` at `-3` is computed from `-3` books only |
| `P4-T04` | `TOTAL OVER 44.5` and `OVER 45` never merge |
| `P4-T05` | `MONEYLINE` (`line IS NULL`) groups correctly and never collides with a spread row |

### Cross-line leakage canary and partner rules

| Test | Asserts |
|---|---|
| `P4-T06` | A slate constructed so blending would give a *materially different* best price — the leak is detectable if present, not merely absent by luck |
| `P4-T07` | `SPREAD` de-vig pairs `DAL -3` with `PHI +3` and **never** with `PHI +3.5`; the mismatched book yields `NO_DEVIG_PAIR` |
| `P4-T08` | `TOTAL` de-vig pairs `OVER 44.5` with `UNDER 44.5`, never `UNDER 45` |
| `P4-T23` | A pure `-3 → -3.5` line move reads as `line_movement` (`-0.5` / `+0.5` mirrored) and **not** as `probability_movement` on either line |

### Modal line — determinism, and independence from presentation

| Test | Asserts |
|---|---|
| `P4-T09` | On **spreads**, the modal line is invariant to insertion order, bookmaker order, observation recency, and **equivalent sign presentation** — the two sides of one wager must name the same wager. Totals retained as a control |
| `P4-T10` | Book count is the only substantive criterion: three books on the larger magnitude beat one book on the smaller, so count dominates the tie-break |
| `P4-T25` | Board-wide regression guard — across a fragmented 60-event board, zero wagers describe two different centres |

### Correctness of the arithmetic

| Test | Asserts |
|---|---|
| `P4-T11` | Implied probability and its inverse round-trip across `±100 … ±10000`, **within the quantisation bound** (see §12.2) |
| `P4-T12` | Multiplicative de-vig of a known pair reproduces hand-computed fair probabilities; `fair₁ + fair₂ = 1` |
| `P4-T13` | `best_price` uses payout ordering — `+100` beats `-105` beats `-110` |
| `P4-T26` | `best_price` ranks on **exact** payout, not the rounded money function — `-142` beats `-143` in canonical and executable, and four adjacent cent-colliding pairs rank strictly |
| `P4-T14` | Median consensus is unmoved by one extreme book that would swing a mean |

### Quality and gating

| Test | Asserts |
|---|---|
| `P4-T15` | Every reason code is reachable, and reasons accumulate uncollapsed on one row |
| `P4-T16` | A single-book market fails closed |
| `P4-T17` | Stale books excluded on `snapshot_ttl_seconds`; **no second freshness constant exists** |
| `P4-T18` | No outlier removal below `mi_outlier_min_books`; never more than a third removed |
| `P4-T19` | Every `best_snapshot_id` on the executable surface is **accepted by `place_ticket_rpc`** — the surface and the RPC cannot disagree |
| `P4-T20` | A superseded snapshot never appears on the executable surface (`MARKET_MOVED` parity) |
| `P4-T21` | A live event is absent from the executable surface |
| `P4-T22` | Opening equals current on a single-observation slate — `market_movement`'s duplicated partner rule agrees with `canonical_market`'s by construction |

### Live-shape

| Test | Asserts |
|---|---|
| `P4-T24` | At live shape (272 events / 5,712 quotes), canonical row count equals distinct `(event, market, selection, line)`, and the per-event query — the access pattern a model actually uses — stays bounded |

---

### 12.1 Deviations from the pre-registration

Recorded rather than renumbered away.

1. **IDs shifted by one from `P4-T10` onward.** The four modal-invariance
   properties added at review became `P4-T09`/`P4-T10`, displacing the
   arithmetic and gating blocks. Nothing was dropped; the mapping is
   old `T10→T11`, `T11→T12`, `T12→T13`, `T13→T14`, `T16→T17`, `T17→T18`.
2. **Old `P4-T14` and `P4-T15` merged** into `P4-T15`. Reachability and
   accumulation are asserted by the same fixture; splitting them duplicated
   setup without adding a proof.
3. **Old `P4-T09` (line vs price movement) was initially not implemented.**
   Caught during reconciliation and added as `P4-T23`.
4. **Old `P4-T21` (live-shape) was initially not implemented.** Added as
   `P4-T24`, against a synthetic slate of the live census's shape rather than
   captured data, so it runs without an API key.
5. **`P4-T18`'s original fixture was too weak.** A `-900` book paired against
   `+130` deviates only `0.0945` from the median — inside
   `mi_outlier_probability_delta = 0.10`, so it is *correctly* not an outlier.
   The fixture now uses a genuinely divergent book (`-2000 / +1500`).
6. **`P4-T20`'s original fixture was gated out for the right reason.** The
   superseded book was priced so far from the others that the row earned
   `WIDE_DISPERSION` and left the executable surface via the dispersion gate
   rather than the `MARKET_MOVED` parity being tested. Repriced so the test
   probes what it claims to.
7. **The locked `line ASC` tie-break was replaced with `abs(line) ASC, line
   ASC`** in migration `043`, on review, after the live sign-off script exposed
   a spread whose two sides reported different modal lines. The rule was stable
   and recency-independent as intended, but sorted a signed value per selection,
   so it carried a standing preference for the more negative number — see the
   modal-line section above. Book count remains the only substantive criterion
   and is untouched; `P4-T10` asserts it still dominates the tie-break. No other
   Package #4 behaviour was changed to accommodate this.
8. **`P4-T09` was extended and moved onto spreads.** It previously proved
   invariance to insertion order, bookmaker order and recency — on totals, the
   one market that structurally cannot exhibit the bias. It now proves all four
   invariants, including **equivalent sign presentation**, on spreads, and keeps
   the totals case as a control. `P4-T25` adds the board-wide regression guard.
   Negative control run: reverting `043` makes `P4-T09` report
   `(-3.50, +3.00)` under all five permutations and `P4-T25` report
   `SPREAD 60/60`, so both tests genuinely detect the defect rather than
   passing by construction.
9. **`best_price` was ranked with a rounding money function** — found by the
   sign-off script on a seeded board, not by this suite, and fixed in migration
   `044`. See §12.5. `P4-T26` added; negative control reverting `044` reports
   `canonical named a worse price as best: (-143, 'bookB')`.

### 12.2 Round-trip tolerance is a property of the odds format, not slop

American odds are integers. `-110` and `-111` are adjacent representable prices
whose implied probabilities differ by ~0.0011, so `fair_american(implied(p))`
cannot round-trip exactly — there is frequently no integer price for the exact
fair probability. `P4-T11` asserts the round-trip lands within one representable
step, which is the tightest true statement available. Asserting equality would
require either non-integer odds or a lie.

### 12.3 Performance finding: the partner lookup was quadratic

`P4-T24` exists because "bounded query time" was pre-registered. It found that
the de-vig partner LATERAL in `038` and `040` scanned the whole `newest` /
`earliest` CTE once per row — a CTE has no index, and the planner estimated 571
rows against an actual 5,712. Measured cost: `0.325 ms × 5,712 loops = 1.86 s`,
growing quadratically with board size.

Migration `042` points the same LATERAL at `public.market_snapshots`, where
`idx_snapshots_canonical` serves it. That node fell to `0.010 ms × 5,712 =
57 ms`, a 32× reduction, and the growth is no longer quadratic. Semantics are
unchanged — `LIMIT 1` and the ordering are preserved, so it selects exactly the
row the CTE held.

An equi-join on a derived partner key was tried first and was **worse**: the
planner still chose a nested loop off the bad CTE estimate, and without the
LATERAL's early exit it scanned to completion. Rejected and recorded.

Remaining honest numbers, 272 events / 5,712 quotes:

| | PostgreSQL 16.2 | Supabase 17.6 |
|---|---|---|
| one event (the real access pattern) | 1.4 s cold | 0.87 s cold |
| full-board `canonical_market` scan | 14.7 s | 10.7 s |

The full-board scan is **not** dominated by the partner lookup any more; it is
dominated by the CTE chain, which `market_intelligence` re-evaluates once
directly and twice more inside `executable_market` and `market_movement`.
Collapsing that is a materialisation decision, not a view rewrite, and it is
deliberately **not** taken in Package #4 — no consumer scans the whole board,
and materialising would introduce a staleness surface this package exists to
avoid. Recorded here so the decision is visible rather than forgotten.

### 12.5 `best_price` was ranked with a money function that rounds

The largest defect found in Package #4, and the test suite did not find it — the
live sign-off script did, while validating the *reporting*, on a board built to
exercise canonical-vs-executable substitution.

`best_price` was ordered by `olp_american_profit(1, price) DESC`. That is
Package #1's **money** function: it rounds to two decimals because ledger
amounts are cents, which is correct for money and wrong for comparison. On a
one-unit stake, adjacent American prices collapse into the same cent:

```
-142  ->  100/142 = 0.704225  ->  round(...,2) = 0.70
-143  ->  100/143 = 0.699301  ->  round(...,2) = 0.70
```

They tied, `sportsbook ASC` then decided, and it could name the **worse** price.
Observed live in the sign-off output: with `book4` at `-141` excluded as
superseded, the executable surface reported `book2` at `-143` while `book3` was
offering `-142`. The one field the whole package exists to produce — "where is
the best executable price" — was wrong, quietly, by one price step.

The collision is not rare. Any two prices whose per-unit payouts land in the
same cent bucket tie, which is most adjacent pairs once magnitudes pass roughly
`-110`.

Fixed in `044` with `olp_price_payout(INT)`: exact, never rounded, deliberately
a separate function so no caller can confuse *what ranks higher* with *what gets
paid*. `olp_american_profit` is untouched — it is right for money, Package #1
froze it, and tickets carry its output as `potential_profit`. Only the two
`ORDER BY` clauses moved.

**Why the tests missed it.** `P4-T13` asserted payout ordering with `+100` /
`-105` / `-110` — prices far enough apart that rounding cannot collapse them.
The property was true and the test was honest; the fixture simply never entered
the range where the defect lives. The same lesson as §12.1 item 8: an invariant
has to be tested on inputs that can actually violate it.

**Reported, not fixed:** `ticket_closing_line_value` (migration `028`, Package
#2, frozen `pkg2-v1.0`) computes `beat_close` by comparing
`olp_american_profit(1, accepted_price)` against
`olp_american_profit(1, closing_price)`, and carries the identical collision — a
ticket that beat the close by one point can read as not having beaten it, and
`payout_edge_per_unit` inherits the rounding before its own `round(..., 4)`.
Package #2's semantics are frozen and were not changed as a side effect of a
Package #4 fix. This needs its own decision.

### 12.4 A benchmarking trap worth naming

The synthetic slate is seeded at `NOW()` and ages out of `snapshot_ttl_seconds`
while a benchmark runs. During this work a sequence of timings took 84 seconds,
by which point every quote was stale and the views correctly returned zero rows
— which reads exactly like a correctness bug and is not one. Keep any
measurement inside the TTL window, or re-seed between runs.

---

## 13. Out of scope

No model, no edge, no EV, no confidence, no sizing — Package #5 and beyond. No
cross-line normalisation. No writes of any kind. No polling-cadence work.
