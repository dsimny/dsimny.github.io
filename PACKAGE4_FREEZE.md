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

Migrations are append-only. `042`–`044` replace view bodies via
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
   tie-breakers with no economic meaning.
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

## 5. Tests — 152/152

26 Package #4 tests (`P4-T01`…`P4-T26`) inside a 152-test suite, green on
**PostgreSQL 16.2** and **Supabase 17.6**.

Three defects were found during the package and each has a negative control
proving its test detects the defect rather than passing by construction:

| Defect | Found by | Fix | Negative control |
|---|---|---|---|
| Modal tie-break used recency → flapped between polls | a deliberate 2-vs-2 tie test | ordering rule | — |
| `line ASC` tie-break carried signed-direction bias → the two sides of a spread named different wagers (33.5%) | review of the sign-off output | `043` | `P4-T09` → `(-3.50, +3.00)`; `P4-T25` → `SPREAD 60/60` |
| `best_price` ranked with a rounding money function → named a worse price as best | `package4_signoff.sql` on a seeded board | `044` | `P4-T26` → `(-143, 'bookB')` |
| `+100` and `-100` tied on payout, so *positive > negative* was not absolute | review of the ordering rule | `045` | `P4-T26` → `(-100, 'bookA')` |

The recurring lesson, recorded in `PACKAGE4_PREREG.md` §12.1 and §12.5: **an
invariant has to be tested on inputs that can actually violate it.** The modal
bias hid because the invariance tests ran on totals; the comparator bug hid
because the payout test used prices too far apart to collide.

## 6. Live sign-off — TO BE COMPLETED

Run `scripts/package4_signoff.sql` within ~2 minutes of a fresh ingest.

| # | Check | Result |
|---|---|---|
| 1 | canonical row count = distinct fresh keys | _pending_ |
| 2 | modal row count = one per event/market/selection | _pending_ |
| 3 | executable row count, strict subset, 0 orphans | _pending_ |
| 4 | market-quality distribution | _pending_ |
| 5 | spread modal mirror violations = 0 | _pending_ |
| 6 | de-vig pair failures; both sides sum to 1 | _pending_ |
| 7 | cross-line leakage violations = 0 (two probes) | _pending_ |
| 8 | canonical-vs-executable substitutions (rate; 0 impossible improvements) | _pending_ |
| 9 | movement rows and direction sanity | _pending_ |
| 10 | execution handoff validity via `best_snapshot_id` = 0 failures | _pending_ |

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
