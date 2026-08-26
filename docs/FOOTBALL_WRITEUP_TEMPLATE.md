# Football writeup template — layer 2

The reasoning layer from `docs/FOOTBALL_PIPELINE.md` section 2. An LLM writes
the prose; **every number comes from layer 1 and none are generated.**

Derived from Daniel's Gemini "Engine V4.0" format, which is kept for its
structure — BLUF, the card, the blunt no-filler voice, and above all its
zero-action slate audit, which is the best idea in it and is preserved almost
intact below.

---

## 1. THE RULE THAT MAKES THIS SAFE

**The model receives a filled data block and may not introduce a number that is
not in it.** Not a percentage, not a rate, not a temperature, not a projection.

This is not a style preference. The source format's analysis lines carried
`.354 xwOBA against right-handed power`, `0.88 1st-inning WHIP`, `86.3 pitches`,
`28.4% four-seam whiff`, `4.42 bullpen xFIP`, `84°F`, `95.8 -> 94.7 mph` — none
of which came from any feed. They were generated, and they look exactly like
measurements. Publishing them beside a public append-only ledger would put
invented statistics and audited results on the same page in the same voice.

The tell that the format cannot police itself: its own ledger contradicted
itself. The system prompt anchored `85-43 | +101.23u | +25.8%` while a sample
output reported `105-51 | +124.55u | +29.6%` as "unchanged" — two different
"verified" baselines in one document. A number a model can produce is a number a
model can produce differently next time.

**Ledger figures are read from `data/football/football_ledger.json` and
interpolated. The model never writes them.**

## 2. THE ONLY FACTS AVAILABLE

Layer 1 produces exactly this, per game. There is nothing else, and the writeup
may not imply there is:

| field | meaning |
|---|---|
| `n_books` | how many eligible books quoted the moneyline |
| `fair_away`, `fair_home` | de-vigged consensus probability per side |
| `raw_overround_pts` | the market's overround at consensus prices |
| `eff_overround_pts` | the toll at BEST prices — the tightness number |
| `best_price`, `best_book` | best TIER-1 price and where (never offshore) |
| `books_at_best` | Tier-1 books at or within 1 pt of it |
| `offshore_best` | best offshore price, mentionable as colour, never the play |
| `move_pts` | consensus movement between T-24 and close |
| `clv_pts` | after grading only |

NOT AVAILABLE, and therefore never mentioned: injuries, weather, travel, rest,
efficiency ratings, matchup history, coaching, momentum, or any projection of
score or margin. Football is a **market story, not a matchup story** — which is
what the evidence supports, since two studies say we cannot out-forecast this
market.

## 3. BANNED FIELDS, and why each is gone

- **`Confidence: 78%`** — a probability claim with no calibrated model behind
  it. It is the most dangerous field in the source format because it reads as
  model output. There is no football model. Removed entirely, not softened.
- **`Suggested Bet: 1.5 units (3.0% bankroll)`** — football ships at 0 units
  (pipeline spec section 6). The card states 0u and says why.
- **Parlays** — out of scope in `FOOTBALL_PREREG_V02.md` section 3, highest-vig
  product on the board, and the source format's `+474 / 62%` figures were
  generated.
- **`Risk Level`** — a value judgement dressed as a category. What we can say is
  the price and how many books offer it.
- **"+EV", "our edge", "the model likes"** — barred by pipeline spec section 1.

## 4. SLATE POST — the shape

```
BLUF: {n_covered} of {n_total} {league} games carry a real market this week.
{n_no_market} are listed without a play. The cleanest number on the board is
{premium_matchup}, where the toll to play is {premium_eff_overround} points
against a {median_eff_overround}-point slate median.

Record to date: {ledger_record} ({ledger_n} graded, 0 units staked).
```

Then one card per covered game, premium first.

## 5. GAME CARD — the shape

```
{away} @ {home} — {kickoff_local}

{n_books} books are pricing this. De-vigged consensus: {home} {fair_home}%,
{away} {fair_away}%.

Best available: {side} {best_price} at {best_book}, matched or within a point at
{books_at_best} of {n_tier1} regulated books. {counterparty_line}

Consensus overround is {raw_overround_pts} points; at the best numbers it is
{eff_overround_pts}. {tightness_sentence}

{movement_sentence}
```

`{tightness_sentence}` and `{movement_sentence}` are the only free prose, and
each may only restate its own number in words. "Shop it and you halve what you
pay to play" is allowed because it restates 4.64 -> 2.27. "TCU should roll" is
not, because no number says that.

### Worked example — real data, first live capture

> **North Carolina @ TCU** — Saturday 12:00 ET
>
> Eleven books are pricing this, six of them regulated. De-vigged consensus:
> TCU 73.1%, North Carolina 26.9%.
>
> Best available: North Carolina +265 at BetRivers, matched or within a point at
> 5 of 6 regulated books. Bovada shows the same +265 offshore, so pricing off
> regulated books costs nothing here.
>
> Consensus overround is 4.64 points; at the best numbers it is 2.27. Shopping
> this game halves what you pay to play it.

Every figure above is on disk. No adjective survives that a number does not.

A NOTE ON HOW THIS EXAMPLE WAS CORRECTED, because it is the failure mode the
whole template exists to prevent. The first draft said "4 of 6" and implied the
regulated price gave something up. Both were wrong: the real capture has 5 of 6
regulated books at or within a point, and the best regulated price TIES the best
offshore one. The draft numbers were plausible, specific, and produced without
looking — which is exactly what section 1 bans. Check the data even when the
sentence sounds right.

## 6. ZERO-ACTION POST — kept nearly intact

The source format's Sample Output B is the strongest thing in it and needs the
least change. A published slate audit naming every rejected game and the rule
that rejected it IS House Rule 6 — "passing is a position too" — with receipts.
Most services hide their no-play days.

```
BLUF: No qualifying play this week. {n_total} games audited, {n_no_market}
carry no usable market, {n_guard} fail the corroboration guard. 0 units. The
record is unchanged at {ledger_record}.

Slate audit
  1. {matchup} — {reason}
     {telemetry}
  ...
```

`{reason}` is the literal rule name from the pipeline spec (NO MARKET, fewer
than 5 eligible books, corroboration guard, no T-24 capture, no closing
capture). `{telemetry}` restates the numbers that triggered it. The disqualifying
reasons are enumerated in the spec, so the model selects one — it never invents
a reason.

## 7. VOICE

Kept from the source: blunt, witty, BLUF, concrete over descriptive, no
conversational filler, no "In conclusion".

Added, because the source format could not honour it: **never write a sentence a
reader could mistake for a forecast.** We report where the market is and what it
costs to enter. We do not say who wins.

## 8. Version history

| version | date (2026) | change |
|---|---|---|
| wt-v0.1 | Aug 25 | Adopted from the Gemini Engine V4.0 format. Structure, BLUF, card shape, zero-action audit and voice kept. Every generated statistic removed; Confidence, Suggested Bet, Risk Level and parlays deleted; ledger figures made a lookup rather than a model output. |
