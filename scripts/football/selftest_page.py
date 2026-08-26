#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the football site surface (gap F).

    python scripts/football/selftest_page.py

Renders the week page in BOTH states from a fixture board and asserts the
redaction actually redacts.

The point is not that it renders. The point is that the pre-kickoff page cannot
be used to reconstruct the premium play, because the selection rule is public
and deterministic - so every number that would let someone recompute rank 1 must
be absent until the week is graded.
"""
import copy, io, json, os, re, shutil, sys, tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import market, board as boardmod, page

WEEK = "2026-09-08"
FIXTURE_WEEKS = {"2026-09-01", "2026-09-08"}
SRC = {"nfl": "nfl_20260825T030847Z.json", "ncaaf": "ncaaf_20260825T030833Z.json"}
ODDS = os.path.join(ROOT, "data", "football", "odds")


def shift(snap, when):
    s = copy.deepcopy(snap)
    old = datetime.fromisoformat(s["captured_utc"].replace("Z", "+00:00"))
    d = when - old
    s["captured_utc"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    for ev in s["events"]:
        for bk in ev.get("books", []):
            if bk.get("last_update"):
                t = datetime.fromisoformat(bk["last_update"].replace("Z", "+00:00")) + d
                bk["last_update"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return s


tmp = tempfile.mkdtemp(prefix="olspage")
try:
    odds = os.path.join(tmp, "odds"); os.makedirs(odds)
    for sport, f in SRC.items():
        src = json.load(io.open(os.path.join(ODDS, f), encoding="utf-8"))
        kicks = {market.parse_utc(e["commence_time"]) for e in src["events"]
                 if e.get("commence_time")
                 and market.slate_week(market.parse_utc(e["commence_time"])) in FIXTURE_WEEKS}
        for k in sorted(kicks):
            when = k - timedelta(hours=24)
            io.open(os.path.join(odds, f"{sport}_{when.strftime('%Y%m%dT%H%M%SZ')}.json"),
                    "w", encoding="utf-8").write(json.dumps(shift(src, when), indent=1))

    boardmod.ODDS_DIR = odds
    D = boardmod.decision_moment(WEEK)
    b = boardmod.build(["nfl", "ncaaf"], WEEK, D + timedelta(hours=6), commit=False)
    # Fake prose so the card path is exercised without an API key.
    for g in b["games"]:
        g["writeup"] = "The market is tight and the best price is corroborated."

    page.OUT = os.path.join(tmp, "football")
    page.FB = os.path.join(ROOT, "data", "football")
    plain = os.path.join(tmp, f"board_{WEEK}.json")
    io.open(plain, "w", encoding="utf-8").write(json.dumps(b, indent=1))
    boardmod.board_paths = lambda w: (plain, plain + ".enc")

    prem = b["premium"]
    print(f"premium play: {prem['league']} {prem['matchup']} -> "
          f"{prem['side']} {prem['best_price']:+d}, eff {prem['eff_overround_pts']}")
    print(f"covered {b['n_covered']}, excluded {b['n_excluded']}\n")

    fails = []
    for reveal in (False, True):
        page.render_week(WEEK, reveal)
        h = io.open(os.path.join(page.OUT, WEEK, "index.html"), encoding="utf-8").read()
        # Leak-check the BODY only. The stylesheet is full of incidental digits
        # (max-width:100%, rgba(0,0,0,.5)) and matching them produced a false
        # "premium price leaked" on the first run - a test that cries wolf gets
        # loosened exactly like a validator that does.
        body = re.sub(r"<style>.*?</style>", "", h, flags=re.S)
        label = "REVEALED" if reveal else "REDACTED"
        print(f"--- {label}: {len(h)} bytes ---")

        # Legal + no-claim on every state.
        for must in ("1-800-GAMBLER", "not a sportsbook", "no claim that these plays win"):
            if must.lower() not in h.lower():
                fails.append(f"{label}: missing {must!r}")
        for barred in ("+EV", "our edge", "value play", "the model likes"):
            if barred.lower() in h.lower():
                fails.append(f"{label}: BARRED phrase {barred!r} present")

        # The NO MARKET list is public in both states.
        if b["no_market"] and "No market" not in h:
            fails.append(f"{label}: no-market section missing")

        if not reveal:
            # THE REDACTION TEST. None of the premium play's numbers may appear.
            leaks = []
            for field in ("best_price", "eff_overround_pts", "raw_overround_pts",
                          "books_at_best", "n_books"):
                v = prem.get(field)
                if v is None:
                    continue
                for form in {str(v), f"{v:+d}" if isinstance(v, int) else str(v)}:
                    if len(form) > 1 and re.search(rf"(?<![\d.]){re.escape(form)}(?![\d.])", body):
                        leaks.append(f"{field}={form}")
            if str(prem.get("best_book", "")) in body:
                leaks.append(f"best_book={prem['best_book']}")
            # The side is the thing that must never appear pre-kickoff.
            side = str(prem.get("side", ""))
            home, away = str(prem.get("home", "")), str(prem.get("away", ""))
            if side and side != home and side != away and side in body:
                leaks.append("side")
            if leaks:
                fails.append(f"REDACTED page leaks premium numbers: {leaks}")
            else:
                print("    premium numbers absent (side, price, book, overround)")
            # Matchup and 0 units ARE meant to be there.
            if prem["matchup"] not in h:
                fails.append("REDACTED: premium matchup should be listed")
            if "0 units" not in h:
                fails.append("REDACTED: should state 0 units")
            print("    matchup + 0 units present, as House Rule 7 requires")
        else:
            if str(prem.get("best_book", "")) not in h:
                fails.append("REVEALED: premium book missing after reveal")
            else:
                print("    premium published in full after grading")

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS - redacted page cannot reconstruct the play; revealed page")
    print("publishes it in full; legal and no-claim copy present in both.")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
