#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for board.py (docs/FOOTBALL_PIPELINE.md gap B).

Run it after touching board.py or market.py:
    python scripts/football/selftest_board.py

THE INVARIANT IT EXISTS TO PROTECT: what the board publishes, the grader
accepts. board.py and grade_football.py both call market.evaluate(), and this
re-evaluates the chosen premium play through that same function from the same
capture and requires an identical verdict. If someone ever gives either script
its own copy of the rule, this is what fails.

Happy-path test for board.py, on a fixture built from real captures.

The live captures are ~4.5 days early for every game, so the real run correctly
covers nothing. This fabricates what a WEEK OF CAPTURING would leave on disk:
one capture per distinct kickoff, each landing exactly at that game's T-24 -
which is what capture_schedule.py produces. Books, prices and team names are
real; only the timestamps move.

It also demonstrates the open policy question rather than asserting it away, by
building the same week at three different decision moments and showing the field
change.
"""
import copy, io, json, os, shutil, sys, tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import market, board

WEEK = "2026-09-08"
# College coverage lives in Week 1 (2026-09-01, 25 covered) and NFL coverage in
# 2026-09-08 (9 covered), so the fixture carries both to test the pooling.
FIXTURE_WEEKS = {"2026-09-01", "2026-09-08"}
SRC = {"nfl": os.path.join(ROOT, "data", "football", "odds", "nfl_20260825T030847Z.json"),
       "ncaaf": os.path.join(ROOT, "data", "football", "odds", "ncaaf_20260825T030833Z.json")}


def shift(snap, new_utc):
    s = copy.deepcopy(snap)
    old = datetime.fromisoformat(s["captured_utc"].replace("Z", "+00:00"))
    d = new_utc - old
    s["captured_utc"] = new_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for ev in s["events"]:
        for bk in ev.get("books", []):
            if bk.get("last_update"):
                t = datetime.fromisoformat(bk["last_update"].replace("Z", "+00:00")) + d
                bk["last_update"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return s


def make(tmp):
    odds = os.path.join(tmp, "odds"); os.makedirs(odds)
    n = 0
    for sport, path in SRC.items():
        src = json.load(io.open(path, encoding="utf-8"))
        kicks = set()
        for ev in src["events"]:
            k = market.parse_utc(ev.get("commence_time"))
            if k and market.slate_week(k) in FIXTURE_WEEKS:
                kicks.add(k)
        for k in sorted(kicks):
            when = k - timedelta(hours=24)
            s = shift(src, when)
            name = f"{sport}_{when.strftime('%Y%m%dT%H%M%SZ')}.json"
            io.open(os.path.join(odds, name), "w", encoding="utf-8").write(json.dumps(s, indent=1))
            n += 1
    return odds, n


tmp = tempfile.mkdtemp(prefix="olsboard")
try:
    odds, n_caps = make(tmp)
    board.ODDS_DIR = odds
    print(f"fixture: {n_caps} captures (one per distinct kickoff, each at that game's T-24)\n")

    moments = [("Wed, before anything kicks",  "2026-09-09T12:00:00Z"),
               ("Sat morning",                 "2026-09-12T14:00:00Z"),
               ("Sun 11:00Z, NFL Sunday T-24s in", "2026-09-13T11:00:00Z")]
    boards = []
    for label, iso in moments:
        b = board.build(["nfl", "ncaaf"], WEEK, market.parse_utc(iso))
        boards.append((label, b))
        prem = b["premium"]
        print(f"--- decision moment: {label} ({iso}) ---")
        print(f"    {b['n_covered']} covered / {b['n_excluded']} excluded -> {b['coverage_status']}")
        if prem:
            print(f"    premium: {prem['league']:<9} {prem['matchup']:<34} "
                  f"eff {prem['eff_overround_pts']:.2f}  {prem['side']} {prem['best_price']:+d} "
                  f"@ {prem['best_book']} ({prem['books_at_best']} books)")
            fr = b["free"]
            if fr:
                print(f"    free   : {fr['league']:<9} {fr['matchup']:<34} "
                      f"eff {fr['eff_overround_pts']:.2f}")
        else:
            print("    premium: none (no qualifying game)")
        print()

    # ---- assertions on the richest board ----
    b = max((x[1] for x in boards), key=lambda x: x["n_covered"])
    fails = []

    if not b["premium"]:
        fails.append("no premium play on the richest board")
    else:
        # rank 1 must be the strict minimum of effective overround
        effs = [g["eff_overround_pts"] for g in b["games"]]
        if b["premium"]["eff_overround_pts"] != min(effs):
            fails.append("premium is not the tightest market")
        if b["premium"]["rank"] != 1 or b["premium"]["tier"] != "premium":
            fails.append("premium not tagged rank 1")
        if b["free"] and b["free"]["rank"] != 2:
            fails.append("free is not rank 2")
        if b["free"] and b["free"]["matchup"] == b["premium"]["matchup"]:
            fails.append("free duplicates premium")
        if effs != sorted(effs):
            fails.append("games not ranked ascending by effective overround")

    # ---- ONE POOL (fp-v0.2), tested directly ----
    # The 2026-09-08 week cannot test this from live data: its 6 college games
    # were ~2.5 weeks out when captured, so only 1-3 books had posted and the
    # coverage filter correctly excludes all of them. A fixture can move a
    # timestamp; it cannot invent books that had not opened. So the pooling is
    # tested where coverage actually exists on both sides - college Week 1
    # (2026-09-01, 90 games) unioned with the NFL week above - asserting the
    # ranking is SPORT-BLIND rather than that any given week has both.
    ncaaf_wk = board.build(["ncaaf"], "2026-09-01",
                           market.parse_utc("2026-08-31T00:00:00Z"))
    pooled = market.rank(b["games"] + ncaaf_wk["games"])
    sports_in = {g["sport"] for g in pooled}
    if len(sports_in) < 2:
        fails.append(f"union still one sport: {sports_in}")
    else:
        effs_p = [g["eff_overround_pts"] for g in pooled]
        if effs_p != sorted(effs_p):
            fails.append("pooled ranking is not ascending by effective overround")
        # the rule must be able to hand rank 1 to either sport
        prem_p, _ = market.assign(pooled)
        interleaved = any(pooled[i]["sport"] != pooled[i + 1]["sport"]
                          for i in range(min(len(pooled), 12) - 1))
        if not interleaved:
            fails.append("pooled top of board never interleaves sports")
        print(f"    pooled check: {len(pooled)} games, {sorted(sports_in)}, "
              f"rank 1 = {prem_p['league']} {prem_p['matchup']} "
              f"(eff {prem_p['eff_overround_pts']:.2f})")

    # corroboration guard actually enforced everywhere
    for g in b["games"]:
        if g["books_at_best"] < market.MIN_CORROBORATION:
            fails.append(f"corroboration guard breached: {g['matchup']}")
        if g["n_books"] < market.MIN_BOOKS:
            fails.append(f"under-booked game covered: {g['matchup']}")
        if g["move_pts"] is not None or g["clv_pts"] is not None:
            fails.append(f"pre-kickoff board carries a post-close number: {g['matchup']}")

    # every excluded game carries a reason
    for nm in b["no_market"]:
        if not nm["reason"]:
            fails.append(f"excluded with no reason: {nm['matchup']}")

    # THE KEY PROPERTY: what the board publishes, the grader accepts.
    # Re-evaluate the premium play through the identical function the grader
    # calls, from the same capture, and require an identical verdict.
    prem = b["premium"]
    snaps = market.load_snapshots(prem["sport"], odds)
    kick = market.parse_utc(prem["kickoff_utc"])
    t24, _ = market.pick_snapshots(snaps, kick)
    ev = market.find_event(t24[2], prem["matchup"].split(" @ ")[0],
                           prem["matchup"].split(" @ ")[1])
    again = market.evaluate(market.eligible(ev, t24[0]), prem["away"], prem["home"])
    for k in ("side", "best_price", "best_book", "books_at_best",
              "eff_overround_pts", "fair_side", "n_books"):
        if again[k] != prem[k]:
            fails.append(f"board/grader disagree on {k}: {prem[k]} vs {again[k]}")

    # layer 2 containment: the writeup may only see these fields
    ALLOWED = {"n_books", "fair_away", "fair_home", "raw_overround_pts",
               "eff_overround_pts", "side", "best_price", "best_book",
               "books_at_best", "fair_side", "offshore_best", "move_pts",
               "clv_pts", "sport", "league", "matchup", "away", "home",
               "kickoff_utc", "t24_capture", "t24_hours_before_kickoff",
               "rank", "tier"}
    extra = set(prem) - ALLOWED
    if extra:
        fails.append(f"board game carries unexpected fields: {sorted(extra)}")

    print("=" * 62)
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print(f"PASS - {b['n_covered']} covered, both sports pooled, rank 1 is the")
    print("tightest market, guards enforced, and the grader's own evaluate()")
    print("reproduces the premium play exactly.")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
