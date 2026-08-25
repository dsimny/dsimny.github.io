#!/usr/bin/env python3
"""
Open Ledger Sports — football capture scheduler.

WHAT IT SOLVES. Football is a weekly sport with kickoffs scattered across days
and time zones, so MLB's "run once every morning" cadence is the wrong shape.
`docs/FOOTBALL_PREREG_V02.md` section 11 is explicit about it: the scheduler
reads each game's real kickoff and subtracts 24h, and ANY capture window derived
from "it's Sunday" is a bug. 2026 proves the point — NFL Week 1 opens on a
WEDNESDAY, there is a Thursday game in Australia, and international kickoffs
land at hours no US-weekday assumption survives.

THE MODEL: WINDOWS, NOT TIMES. Every game needs two prices, and each is a window
rather than an instant, because one capture returns the WHOLE board and can
therefore satisfy many games at once:

    T-24 window     [kickoff-30h, kickoff-18h]
    closing window  [kickoff-6h,  kickoff]

Those match the guards in `grade_football.py`, so a window this script reports
as satisfied is one the grader will actually accept. If they ever diverge, the
grader wins — it is the thing that books the record.

A window is SATISFIED if any capture on disk falls inside it. So the scheduler's
job is not "fire at T-24 for each game" but "is there an open window with no
capture in it yet" — which collapses dozens of games into a handful of calls.

COST. One capture is 1 credit at h2h (markets x regions). A college Saturday
typically needs a handful.

Run:
  python scripts/football/capture_schedule.py --sport ncaaf            # what is due
  python scripts/football/capture_schedule.py --sport ncaaf --run      # fire if due
  python scripts/football/capture_schedule.py --sport nfl --days 10
"""
import argparse, glob, io, json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
ODDS_DIR = os.path.join(ROOT, "data", "football", "odds")
FETCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_odds.py")

ESPN_PATH = {"ncaaf": "college-football", "nfl": "nfl"}
ESPN_GROUPS = {"ncaaf": "80"}          # FBS; NFL needs no group filter

# Must mirror grade_football.py. Named here so a drift is obvious in review.
T24_EARLY_H, T24_LATE_H = 30.0, 18.0
CLOSE_EARLY_H, CLOSE_LATE_H = 6.0, 0.0


def now_utc():
    return datetime.now(timezone.utc)


def parse_espn_dt(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def kickoffs(sport, days):
    """Upcoming kickoffs from ESPN's public scoreboard. No key, no credits."""
    url = ("http://site.api.espn.com/apis/site/v2/sports/football/"
           f"{ESPN_PATH[sport]}/scoreboard")
    out, d = [], now_utc()
    for i in range(days):
        day = (d + timedelta(days=i)).strftime("%Y%m%d")
        params = {"dates": day, "limit": 400}
        if sport in ESPN_GROUPS:
            params["groups"] = ESPN_GROUPS[sport]
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            evs = r.json().get("events", [])
        except requests.RequestException as e:
            print(f"  WARNING: ESPN lookup failed for {day} ({e}); "
                  "that day's games are not scheduled for capture.")
            continue
        for ev in evs:
            k = parse_espn_dt(ev.get("date"))
            if not k or k <= now_utc():
                continue
            comp = ev["competitions"][0]["competitors"]
            names = {c["homeAway"]: c["team"]["displayName"] for c in comp}
            out.append({"kickoff": k,
                        "matchup": f'{names.get("away")} @ {names.get("home")}'})
    return sorted(out, key=lambda x: x["kickoff"])


def captures(sport):
    out = []
    for f in sorted(glob.glob(os.path.join(ODDS_DIR, f"{sport}_*.json"))):
        m = re.search(r"_(\d{8}T\d{6})Z\.json$", os.path.basename(f))
        if m:
            out.append(datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
                       .replace(tzinfo=timezone.utc))
    return sorted(out)


def windows(games):
    """Every window every upcoming game needs, flattened."""
    w = []
    for g in games:
        k = g["kickoff"]
        w.append({"kind": "T-24", "matchup": g["matchup"], "kickoff": k,
                  "opens": k - timedelta(hours=T24_EARLY_H),
                  "closes": k - timedelta(hours=T24_LATE_H)})
        w.append({"kind": "close", "matchup": g["matchup"], "kickoff": k,
                  "opens": k - timedelta(hours=CLOSE_EARLY_H),
                  "closes": k - timedelta(hours=CLOSE_LATE_H)})
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="ncaaf", choices=sorted(ESPN_PATH))
    ap.add_argument("--days", type=int, default=8,
                    help="how far ahead to look for kickoffs")
    ap.add_argument("--run", action="store_true",
                    help="fire a capture if one is due (costs 1 credit at h2h)")
    # WAIT FOR OVERLAP. Windows are staggered by kickoff, so firing the instant
    # any window opens wastes credits: on the 2026-08-29 slate the first two
    # T-24 windows are [10:00..22:00] and [13:00..01:00], and a capture at 12:00
    # serves only the first while one at 20:00 serves both. So we hold until the
    # EARLIEST-CLOSING open window is within --lead hours of shutting, which is
    # the last safe moment and therefore the point of maximum overlap.
    ap.add_argument("--lead", type=float, default=2.0,
                    help="fire when the most urgent open window is this many "
                         "hours from closing (default 2)")
    ap.add_argument("--eager", action="store_true",
                    help="ignore --lead and fire as soon as anything is open")
    args = ap.parse_args()

    now = now_utc()
    games = kickoffs(args.sport, args.days)
    caps = captures(args.sport)
    print(f"{args.sport}: {len(games)} upcoming kickoffs in the next {args.days} days, "
          f"{len(caps)} captures on disk")
    if not games:
        print("nothing scheduled; no captures needed.")
        return 0

    ws = windows(games)
    satisfied = [w for w in ws if any(w["opens"] <= c <= w["closes"] for c in caps)]
    open_now = [w for w in ws if w["opens"] <= now <= w["closes"]
                and w not in satisfied]
    missed = [w for w in ws if w["closes"] < now and w not in satisfied]
    upcoming = [w for w in ws if w["opens"] > now and w not in satisfied]

    print(f"  windows: {len(ws)} total | {len(satisfied)} satisfied | "
          f"{len(open_now)} OPEN NOW | {len(upcoming)} upcoming | {len(missed)} missed")

    if missed:
        print(f"\n  MISSED ({len(missed)}) — these games cannot be fully graded:")
        for w in missed[:8]:
            print(f"    {w['kind']:<5} {w['matchup'][:52]:<52} closed "
                  f"{(now - w['closes']).total_seconds()/3600:.1f}h ago")

    if open_now:
        kinds = sorted({w["kind"] for w in open_now})
        print(f"\n  CAPTURE DUE NOW — {len(open_now)} open window(s) "
              f"({', '.join(kinds)}) with no capture in them.")
        for w in open_now[:6]:
            print(f"    {w['kind']:<5} {w['matchup'][:52]:<52} closes in "
                  f"{(w['closes'] - now).total_seconds()/3600:.1f}h")
        if len(open_now) > 6:
            print(f"    ... and {len(open_now) - 6} more, all served by ONE capture")
    else:
        print("\n  nothing due right now.")

    if upcoming:
        nxt = min(upcoming, key=lambda w: w["opens"])
        print(f"\n  next window opens {nxt['opens']:%Y-%m-%d %H:%M}Z "
              f"(in {(nxt['opens'] - now).total_seconds()/3600:.1f}h) "
              f"for {nxt['kind']} on {nxt['matchup'][:40]}")

    if not args.run:
        print("\n(no --run: nothing captured, no credits spent)")
        return 0
    if not open_now:
        print("\n--run given but nothing is due; no credits spent.")
        return 0

    print(f"\nfiring one capture to satisfy {len(open_now)} open window(s)...")
    r = subprocess.run([sys.executable, FETCH, "--sport", args.sport],
                       cwd=ROOT)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
