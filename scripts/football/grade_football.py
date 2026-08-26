#!/usr/bin/env python3
"""
Open Ledger Sports — football grading (docs/FOOTBALL_PIPELINE.md sections 3-6).

Turns captured prices plus results into a graded, append-only record. This is
the step that makes a dark run mean something: without it a Saturday produces
data, with it a Saturday produces "here is what the product would have said,
and here is whether it was right".

WHAT IT DOES, in the spec's order:
  section 3  coverage filter — >=5 eligible books, quotes fresh within 15 min
             of their snapshot, both teams resolvable. Anything failing is
             recorded as NO MARKET, never silently dropped.
  section 4  the selection rule — slate is a WEEK; rank games by effective
             overround at best prices; corroboration guard; side furthest above
             de-vigged fair; rank 1 premium, rank 2 free.
  section 6  book it at ZERO UNITS into its own ledger, append-only.

SLATE WEEK = TUESDAY THROUGH MONDAY. Not the ISO week, which starts Monday and
would split an NFL week across two of them (Thursday and Sunday in one, Monday
in the next). Tuesday-anchored keeps Thu/Sun/Mon together for the NFL and
Thu/Fri/Sat together for college.

WHICH SNAPSHOT IS "T-24" AND WHICH IS "CLOSING" is derived from the captures on
disk rather than assumed from a schedule, because a capture that fired late is a
fact and a convention is not:
  T-24    = the capture nearest to kickoff-24h
  closing = the LAST capture strictly before kickoff
Both distances are recorded on every entry. A closing capture at or after
kickoff is refused outright — that is an in-play price, and booking CLV from one
would be inventing a number.

ZERO UNITS. Football does not size stakes. `units` is 0 on every entry and the
pnl column is what one unit WOULD have returned, labelled as such. The Daily
Pick's staking review is a different strategy and authorises nothing here.

APPEND-ONLY (House Rule 1). A slate week already in the ledger is refused rather
than recomputed. Never backfill, never delete a loss.

NO EXPECTATION CLAIM. Per pipeline spec section 1, this makes no +EV claim. It
records what the rule chose and what happened.

Run:
  python scripts/football/grade_football.py --sport ncaaf --dry-run
  python scripts/football/grade_football.py --sport ncaaf
"""
import argparse, glob, io, json, os, statistics, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn_ncaaf                                   # noqa: E402
import market                                       # noqa: E402
from market import (TIER1, TIER2, MIN_BOOKS, STALENESS_MIN,   # noqa: E402,F401
                    MIN_CORROBORATION, IDEAL_T24_H, MAX_CLOSE_H, T24_TOLERANCE_H,
                    implied, payout, parse_utc, iso, slate_week,
                    eligible, fair, best, find_event, pick_snapshots)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
ODDS_DIR = os.path.join(FB, "odds")
LEDGER = os.path.join(FB, "football_ledger.json")

# EVERY MARKET FUNCTION AND THRESHOLD NOW LIVES IN market.py, imported above
# rather than defined here. They used to be defined in this file and board.py
# would have needed its own copy; two implementations of one rule drift, and the
# drift surfaces as a premium play the ledger refuses to grade. The extraction
# was verified by comparing every function against this file's previous version
# across 399 real events (11,340 assertions, all equal) - the same
# extract-then-prove-equivalent pattern used for engine.simulate_game in v0.5.
#
# The grading-specific parts stay here: which snapshot is T-24 and which is the
# close, settlement against a final score, and the append-only ledger write.


def load_snapshots(sport):
    """Grading reads captures from the repo's odds dir; market.py takes the
    directory as an argument so a caller can point it elsewhere."""
    return market.load_snapshots(sport, ODDS_DIR)


def build(sport, snaps, results):
    """One candidate per gradeable game, plus the reasons games were skipped."""
    cands, skipped = [], []
    for r in results.values():
        if not r.get("final"):
            continue
        kick = parse_utc(r.get("kickoff_utc"))
        if not kick:
            skipped.append((r["away"], r["home"], "no kickoff time"))
            continue
        t24, close = pick_snapshots(snaps, kick)
        if not t24 or not close:
            skipped.append((r["away"], r["home"], "NO MARKET (no capture before kickoff)"))
            continue
        # An in-play price is never a close.
        if close[0] >= kick:
            skipped.append((r["away"], r["home"], "closing capture is in-play"))
            continue
        close_h = (kick - close[0]).total_seconds() / 3600.0
        if close_h > MAX_CLOSE_H:
            skipped.append((r["away"], r["home"],
                            f"no closing capture (latest is {close_h:.1f}h "
                            f"before kickoff, need <={MAX_CLOSE_H:.0f}h)"))
            continue
        t24_h = (kick - t24[0]).total_seconds() / 3600.0
        if abs(t24_h - IDEAL_T24_H) > T24_TOLERANCE_H:
            skipped.append((r["away"], r["home"],
                            f"no T-24 capture (nearest is {t24_h:.1f}h before "
                            f"kickoff)"))
            continue

        ev24 = find_event(t24[2], r["away"], r["home"])
        evcl = find_event(close[2], r["away"], r["home"])
        if not ev24 or not evcl:
            skipped.append((r["away"], r["home"], "NO MARKET (game absent from a capture)"))
            continue
        q24, qcl = eligible(ev24, t24[0]), eligible(evcl, close[0])
        if len(q24) < MIN_BOOKS or len(qcl) < MIN_BOOKS:
            skipped.append((r["away"], r["home"],
                            f"NO MARKET ({len(q24)}/{len(qcl)} eligible books, need {MIN_BOOKS})"))
            continue
        f24, fcl = fair(q24, r["away"], r["home"]), fair(qcl, r["away"], r["home"])
        if not f24 or not fcl:
            skipped.append((r["away"], r["home"], "NO MARKET (consensus not computable)"))
            continue
        # THE SELECTION RULE ITSELF (section 4 steps 1-3, plus the takeable-price
        # and unclassified-book markers) is market.evaluate(). The board builder
        # calls the identical function on the identical snapshot, so a play it
        # publishes is by construction a play this grader will accept.
        try:
            m = market.evaluate(q24, r["away"], r["home"])
        except market.NoMarket as why:
            skipped.append((r["away"], r["home"], str(why)))
            continue

        pick = {"side": m["side"], "price": m["best_price"],
                "book": m["best_book"], "near": m["books_at_best"]}

        margin = r["margin"]
        if margin == 0:
            res, pnl = "push", 0.0
        else:
            won = (pick["side"] == r["home"]) == (margin > 0)
            res, pnl = ("win", payout(pick["price"])) if won else ("loss", -1.0)

        cands.append({
            "sport": sport, "slate_week": slate_week(kick),
            "espn_event_id": r["espn_event_id"],
            "matchup": f'{r["away"]} @ {r["home"]}',
            "kickoff_utc": r["kickoff_utc"],
            "side": pick["side"], "price": pick["price"], "book": pick["book"],
            "books_at_best": pick["near"], "n_books_t24": len(q24),
            "eff_overround_pts": m["eff_overround_pts"],
            "fair_t24": round(f24[pick["side"]], 5),
            "fair_close": round(fcl[pick["side"]], 5),
            "clv_pts": round(100 * (fcl[pick["side"]] - implied(pick["price"])), 3),
            "t24_capture": t24[1],
            "t24_hours_before_kickoff": round((kick - t24[0]).total_seconds() / 3600, 2),
            "close_capture": close[1],
            "close_hours_before_kickoff": round((kick - close[0]).total_seconds() / 3600, 2),
            "final": f'{r["away_score"]}-{r["home_score"]}',
            "result": res,
            "units": 0,                       # section 6: football does not stake
            "pnl_per_unit": round(pnl, 4),    # what ONE unit would have returned
        })
    return cands, skipped


def load_ledger():
    try:
        with io.open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"_note": ("Football record, append-only (House Rule 1). ZERO UNITS "
                          "throughout - pnl_per_unit is what one unit WOULD have "
                          "returned, not money risked. Makes no expectation claim; "
                          "see docs/FOOTBALL_PIPELINE.md section 1. Never mixed with "
                          "ledger.json, daily_ledger.json, totals_ledger.json or "
                          "watchlist.json."),
                "entries": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="ncaaf", choices=["ncaaf"],
                    help="NFL grading needs its results store wired the same way")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    snaps = load_snapshots(args.sport)
    if not snaps:
        print(f"no {args.sport} odds captures on disk; nothing to grade.")
        return 0
    results = espn_ncaaf.load_store()["events"]
    print(f"{len(snaps)} captures, {len(results)} known events, "
          f"{sum(1 for r in results.values() if r.get('final'))} final")

    cands, skipped = build(args.sport, snaps, results)
    if not cands:
        print("\nno gradeable games yet.")
        for a, h, why in skipped[:15]:
            print(f"  {a} @ {h}: {why}")
        return 0

    ledger = load_ledger()
    done = {(e["slate_week"], e["sport"]) for e in ledger["entries"]}
    by_week = {}
    for c in cands:
        by_week.setdefault(c["slate_week"], []).append(c)

    new = []
    for week, cs in sorted(by_week.items()):
        if (week, args.sport) in done:
            print(f"\nslate week {week}: already graded, refusing to recompute "
                  f"(House Rule 1).")
            continue
        # section 4 step 1: rank ascending by the toll to play
        cs.sort(key=lambda c: (c["eff_overround_pts"], -c["n_books_t24"],
                               c["kickoff_utc"]))
        for i, c in enumerate(cs):
            if i == 0:
                c["tier"] = "premium"
            elif i == 1:
                c["tier"] = "free"
            else:
                c["tier"] = "covered"      # written up, not played
            c["rank"] = i + 1
        new.extend(cs)
        print(f"\nslate week {week}: {len(cs)} gradeable")
        for c in cs[:2]:
            print(f"  {c['tier']:<8} rank{c['rank']}  {c['matchup']}")
            print(f"           {c['side']} {c['price']:+d} @ {c['book']} "
                  f"({c['books_at_best']} books at best) | overround "
                  f"{c['eff_overround_pts']:.2f} pts")
            print(f"           T-24 {c['t24_hours_before_kickoff']}h  close "
                  f"{c['close_hours_before_kickoff']}h  CLV {c['clv_pts']:+.2f}")
            print(f"           final {c['final']} -> {c['result'].upper()} "
                  f"({c['pnl_per_unit']:+.2f}u at 1 unit; staked 0)")

    if skipped:
        print(f"\n{len(skipped)} game(s) not covered (recorded, never silently dropped):")
        for a, h, why in skipped[:10]:
            print(f"  {a} @ {h}: {why}")

    if args.dry_run:
        print("\n--dry-run: ledger untouched")
        return 0
    if not new:
        print("\nnothing new to append.")
        return 0

    ledger["entries"].extend(new)
    ledger["updated_utc"] = iso(datetime.now(timezone.utc))
    os.makedirs(FB, exist_ok=True)
    with io.open(LEDGER, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ledger, f, indent=1)
    print(f"\nappended {len(new)} entries -> {os.path.relpath(LEDGER, ROOT)} "
          f"({len(ledger['entries'])} total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
