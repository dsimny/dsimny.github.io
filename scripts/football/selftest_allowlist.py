#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the season-type allowlist (spec section 3a).

Run after touching grade_football.py, espn_nfl.py, or the SPORTS config:
    python scripts/football/selftest_allowlist.py

THE FAILURE IT EXISTS TO PREVENT is specific and quiet. ESPN returns preseason
games alongside regular-season ones and nothing in the capture path
distinguishes them. Grade without the allowlist and preseason entries book into
a permanent, append-only ledger looking exactly like real ones. House Rule 1 is
what makes that unrecoverable - there is no delete.

Preseason is not a smaller sample of the same population. Playing time is a
coaching decision, the market prices exactly that variable, and we do not
observe it. A preseason result is uninformative BY CONSTRUCTION.

THE DESIGN IS A CONTROLLED EXPERIMENT. Every game in the fixture uses the same
real capture, the same real prices and the same fabricated final score. The ONLY
thing that varies between them is season_type. So if a preseason row reaches the
candidate list, it is the allowlist that failed and nothing else.

It also asserts an UNKNOWN type is refused. That is the whole reason section 3a
specifies an allowlist rather than a blocklist: a blocklist on type 1 would
silently grade any new type ESPN introduces, and the direction of that error is
a corrupted permanent ledger.
"""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))

import market                                        # noqa: E402
import espn_nfl                                      # noqa: E402
import grade_football as gf                          # noqa: E402

ODDS = os.path.join(ROOT, "data", "football", "odds")
KICK = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)

# BOTH SPORTS ARE TESTED. College has no preseason, which makes it easy to
# assume it needs no allowlist - it has a POSTSEASON. Verified live 2026-08-26:
# college-football 2025-12-27 returns 8 events, all type 3 'post-season'.
# Without a college allowlist the first December run books bowls into a
# permanent ledger with no decision taken.
SPORTS_UNDER_TEST = {
    "nfl": ("nfl_20260825T030847Z.json", "preseason"),
    "ncaaf": ("ncaaf_20260825T030833Z.json", "post-season"),
}

# (season_type, slug, should the grader accept it?)
CASES = [(2, "regular-season", True),
         (1, "preseason", False),
         (3, "post-season", False),     # deliberately not on the allowlist yet
         (99, "some-new-thing-espn-invented", False)]


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


def run_sport(sport, srcfile):
    src = json.load(io.open(os.path.join(ODDS, srcfile), encoding="utf-8"))
    # Use covered games only, so nothing fails for an unrelated reason.
    cap = market.parse_utc(src["captured_utc"])
    usable = []
    for ev in src["events"]:
        try:
            market.evaluate(market.eligible(ev, cap), ev["away_raw"], ev["home_raw"])
            usable.append(ev)
        except market.NoMarket:
            pass
    if len(usable) < len(CASES):
        return [f"{sport}: fixture needs {len(CASES)} covered games, "
                f"capture has {len(usable)}"]

    tmp = tempfile.mkdtemp(prefix="olsallow")
    try:
        odds = os.path.join(tmp, "odds")
        os.makedirs(odds)
        # Rewrite every chosen game to the SAME kickoff, so T-24 and closing
        # windows are identical for all of them and season type is the only
        # variable left.
        chosen = usable[:len(CASES)]
        fixture = copy.deepcopy(src)
        fixture["events"] = []
        for ev in chosen:
            e = copy.deepcopy(ev)
            e["commence_time"] = KICK.strftime("%Y-%m-%dT%H:%M:%SZ")
            fixture["events"].append(e)
        for when in (KICK - timedelta(hours=24), KICK - timedelta(hours=2)):
            s = shift(fixture, when)
            name = f"{sport}_{when.strftime('%Y%m%dT%H%M%SZ')}.json"
            io.open(os.path.join(odds, name), "w", encoding="utf-8").write(
                json.dumps(s, indent=1))

        results = {}
        for i, (stype, slug, _ok) in enumerate(CASES):
            ev = chosen[i]
            results[f"fx-{i}"] = {
                "espn_event_id": f"fx-{i}",
                "away": ev["away"], "home": ev["home"],
                "away_name": ev["away_raw"], "home_name": ev["home_raw"],
                "kickoff_utc": KICK.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "STATUS_FINAL", "final": True,
                "season_year": 2026, "season_type": stype, "season_slug": slug,
                "home_score": 24, "away_score": 17, "margin": 7, "total": 41,
            }

        gf.ODDS_DIR = odds
        snaps = gf.load_snapshots(sport)
        cands, skipped = gf.build(sport, snaps, results)

        accepted = {c["matchup"] for c in cands}
        reasons = {f"{a} @ {h}": w for a, h, w in skipped}
        fails = []
        print(f"--- {sport.upper()} --- {len(snaps)} fixture captures, "
              f"{len(CASES)} games, identical prices and scores;\n"
              f"    season type is the only variable")
        for i, (stype, slug, should) in enumerate(CASES):
            ev = chosen[i]
            m = f'{ev["away"]} @ {ev["home"]}'
            got = m in accepted
            why = reasons.get(m, "")
            mark = "OK " if got == should else "FAIL"
            verdict = "accepted" if got else f"refused ({why})"
            print(f"  {mark} type {stype:<3} {slug:<32} -> {verdict}")
            if got != should:
                fails.append(f"type {stype} ({slug}): accepted={got}, expected {should}")
            if not should and got is False and "NOT REGULAR SEASON" not in why:
                fails.append(f"type {stype} refused for the WRONG reason: {why!r}")

        # Every sport must HAVE an allowlist, and it must be a set of accepted
        # values rather than a rejected one.
        cfg = gf.SPORTS[sport]
        if cfg["gradeable"] is None:
            fails.append(f"{sport} has NO season-type allowlist")
        mod = cfg["results"]
        if getattr(mod, "GRADEABLE_SEASON_TYPES", None) != frozenset({2}):
            fails.append(f"{sport} allowlist changed: "
                         f"{getattr(mod, 'GRADEABLE_SEASON_TYPES', None)}")
        print()
        return fails
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    fails = []
    for sport, (srcfile, _real_slug) in sorted(SPORTS_UNDER_TEST.items()):
        fails += run_sport(sport, srcfile)
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS - for BOTH sports, only regular season is gradeable; preseason,")
    print("postseason and an unrecognised type are all refused as NOT REGULAR")
    print("SEASON, and neither sport is missing its allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
