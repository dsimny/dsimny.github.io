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
from price_test import TIER1, TIER2                 # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
ODDS_DIR = os.path.join(FB, "odds")
LEDGER = os.path.join(FB, "football_ledger.json")

MIN_BOOKS = 5             # pipeline spec section 3
STALENESS_MIN = 15        # pipeline spec section 3
MIN_CORROBORATION = 2     # pipeline spec section 4 step 2
IDEAL_T24_H = 24.0

# A capture only COUNTS as the moment it claims to be if it landed near it.
# Without these two windows the grader will happily call a capture taken four
# days before kickoff a "closing" price, because it is technically the last one
# before the game — and then book CLV against it. That number would look fine
# and mean nothing, which is the Tier C failure the whole capture design exists
# to avoid. Caught while building the first test fixture, before any real slate.
MAX_CLOSE_H = 6.0         # later than this before kickoff and it is not a close
T24_TOLERANCE_H = 6.0     # T-24 must land within this of the 24h mark


def implied(a):
    a = float(a)
    return (-a) / ((-a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def payout(a):
    a = float(a)
    return 100.0 / (-a) if a < 0 else a / 100.0


def parse_utc(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def slate_week(kickoff):
    """The Tuesday on or before kickoff, as YYYY-MM-DD. See module docstring."""
    d = kickoff.date()
    return (d - timedelta(days=(d.weekday() - 1) % 7)).isoformat()


def load_snapshots(sport):
    out = []
    for f in sorted(glob.glob(os.path.join(ODDS_DIR, f"{sport}_*.json"))):
        with io.open(f, encoding="utf-8") as fh:
            s = json.load(fh)
        t = parse_utc(s.get("captured_utc"))
        if t:
            out.append((t, os.path.basename(f), s))
    return out


def eligible(ev, captured):
    """{book: {team: price}} for quotes fresh enough to have been takeable."""
    q = {}
    for bk in ev.get("books", []):
        lu = parse_utc(bk.get("last_update"))
        if captured and lu and abs((captured - lu).total_seconds()) > STALENESS_MIN * 60:
            continue
        h2h = (bk.get("markets") or {}).get("h2h") or []
        named = {o["name"]: o["price"] for o in h2h
                 if o.get("name") and o.get("price") is not None}
        if len(named) == 2:
            q[bk["book"]] = named
    return q


def fair(q, a, b):
    """Proportionally de-vigged consensus from the median price per side."""
    pa = [v[a] for v in q.values() if a in v]
    pb = [v[b] for v in q.values() if b in v]
    if len(pa) < MIN_BOOKS or len(pb) < MIN_BOOKS:
        return None
    ia, ib = implied(statistics.median(pa)), implied(statistics.median(pb))
    tot = ia + ib
    return None if tot <= 0 else {a: ia / tot, b: ib / tot, "_ovr": tot - 1.0}


def best(q, team, tier1_only=True):
    """Best price for a side.

    tier1_only because measurement and action need different book sets: the
    consensus wants breadth and an offshore price is fine market information,
    but a RECOMMENDATION has to be a number the reader can actually take. The
    first live capture picked bovada, which most of the audience cannot use.
    See docs/FOOTBALL_PIPELINE.md section 3.
    """
    ps = [(v[team], bk) for bk, v in q.items()
          if team in v and (not tier1_only or bk in TIER1)]
    if not ps:
        return None
    price, book = max(ps, key=lambda x: x[0])
    ib = implied(price)
    near = sum(1 for p, _ in ps if implied(p) <= ib + 0.01)
    return {"price": price, "book": book, "near": near}


def pick_snapshots(snaps, kickoff):
    """(t24, closing) captures for one kickoff, chosen from what exists."""
    before = [(t, n, s) for t, n, s in snaps if t < kickoff]
    if not before:
        return None, None
    target = kickoff - timedelta(hours=IDEAL_T24_H)
    t24 = min(snaps, key=lambda x: abs((x[0] - target).total_seconds()))
    if t24[0] >= kickoff:
        t24 = None
    return t24, before[-1]


def find_event(snap, away, home):
    for ev in snap.get("events", []):
        if ev.get("away") == away and ev.get("home") == home:
            return ev
    return None


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
        # Consensus already used every eligible book; the price must be Tier 1.
        ba, bh = best(q24, r["away"]), best(q24, r["home"])
        if not ba or not bh:
            skipped.append((r["away"], r["home"],
                            "NO TAKEABLE PRICE (no Tier-1 book quoting)"))
            continue
        unclassified = sorted({b for b in q24 if b not in TIER1 and b not in TIER2})
        if unclassified:
            # Fail loud: an unknown book is neither takeable nor known-offshore,
            # and guessing which would be exactly the wrong instinct.
            skipped.append((r["away"], r["home"],
                            f"UNCLASSIFIED BOOK(S) {unclassified} - add them to a "
                            f"tier in price_test.py before this game can be graded"))
            continue

        # section 4 step 1
        eff = implied(bh["price"]) + implied(ba["price"]) - 1.0
        # section 4 step 3
        opts = [{"side": r["away"], **ba, "gap": f24[r["away"]] - implied(ba["price"])},
                {"side": r["home"], **bh, "gap": f24[r["home"]] - implied(bh["price"])}]
        pick = max(opts, key=lambda o: o["gap"])
        # section 4 step 2
        if pick["near"] < MIN_CORROBORATION:
            skipped.append((r["away"], r["home"],
                            f"corroboration guard ({pick['near']} book at best price)"))
            continue

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
            "eff_overround_pts": round(100 * eff, 3),
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
