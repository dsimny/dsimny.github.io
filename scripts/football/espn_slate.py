#!/usr/bin/env python3
"""
Open Ledger Sports — ESPN slate + result capture (fb-v0.1).

Reads the NFL slate and live status from ESPN's public scoreboard (no key), and
writes it to data/football_pipeline_test.json.

WHY THIS EXISTS. nflverse carries no preseason rows at all - only REG, WC, DIV,
CON and SB. So the preseason plumbing test, which is the only chance to shake
this pipeline out on real games before Week 1 publishes live, cannot run off
nflverse. It runs off ESPN.

WHAT IT IS TESTING (per pre-registration section 4, preseason tests the PIPELINE
and never the model):
  - team-id mapping ESPN -> canonical franchise, via teams.canonical
  - the kickoff-relative as-of clock for a weekly sport, including the T-24 that
    lands mid-week rather than mid-morning
  - status transitions scheduled -> in-progress -> final, captured over time
  - score capture and the join back to nflverse

WHAT THIS FILE MAY NOT DO. It writes to data/football_pipeline_test.json and
nowhere else. It never touches ledger.json, daily_ledger.json, totals_ledger.json,
watchlist.json, or the football paper ledger. House Rule 1 makes the real ledger
permanent, which is exactly why nothing uninformative may enter it, and preseason
is uninformative about the model by construction.

Run:
  python scripts/football/espn_slate.py --dates 20260821            capture one day
  python scripts/football/espn_slate.py --dates 20260821-20260824   capture a range
  python scripts/football/espn_slate.py --dates 20260821 --seasontype 2   regular season
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from teams import canonical, UnknownTeam  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUTFILE = os.path.join(ROOT, "data", "football_pipeline_test.json")
SCOREBOARD = "http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

SEASONTYPE = {1: "preseason", 2: "regular", 3: "postseason"}

WARNING = (
    "PLUMBING TEST ONLY. This file records pipeline behaviour on real games - "
    "team mapping, the as-of clock, status transitions, score capture. It is NOT "
    "a betting record and contains no model output. Preseason results are "
    "uninformative about the model by construction (playing time is a coaching "
    "decision invisible to ratings fitted on regular-season football), so nothing "
    "here may ever be promoted into ledger.json or the football paper ledger. "
    "Archive or delete this file; never merge it."
)


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_espn_dt(s):
    """ESPN returns e.g. '2026-08-21T23:00Z'."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def daterange(spec):
    if "-" in spec:
        a, b = spec.split("-", 1)
    else:
        a = b = spec
    d0 = datetime.strptime(a, "%Y%m%d")
    d1 = datetime.strptime(b, "%Y%m%d")
    if d1 < d0:
        sys.exit(f"--dates range runs backwards: {spec}")
    out, d = [], d0
    while d <= d1:
        out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def fetch_day(datestr, seasontype):
    r = requests.get(SCOREBOARD, params={"dates": datestr, "seasontype": seasontype}, timeout=30)
    r.raise_for_status()
    return r.json().get("events", []) or []


def extract(ev, seasontype):
    """One ESPN event -> our row. Raises rather than guessing on a bad team."""
    comp = ev["competitions"][0]
    sides = {}
    for c in comp["competitors"]:
        sides[c["homeAway"]] = c
    if "home" not in sides or "away" not in sides:
        raise ValueError(f"event {ev.get('id')} has no home/away split")

    ko = parse_espn_dt(ev["date"])
    status = comp["status"]["type"]["name"]          # STATUS_SCHEDULED / _FINAL / ...
    final = bool(comp["status"]["type"].get("completed"))

    def score(c):
        # ESPN reports "0" for unplayed games. A not-yet-played game has NO
        # score, and recording a real 0 would make an unplayed game look like a
        # shutout to anything downstream.
        if not final:
            return None
        v = c.get("score")
        return int(v) if v not in (None, "") else None

    return {
        "espn_id": str(ev["id"]),
        "seasontype": SEASONTYPE.get(seasontype, str(seasontype)),
        "away": canonical(sides["away"]["team"]["abbreviation"], source="espn"),
        "home": canonical(sides["home"]["team"]["abbreviation"], source="espn"),
        "kickoff_utc": iso(ko),
        "t_minus_24_utc": iso(ko - timedelta(hours=24)),
        "result_available_at_utc": iso(ko + timedelta(hours=4)),
        "status": status,
        "completed": final,
        "away_score": score(sides["away"]),
        "home_score": score(sides["home"]),
    }


def load_store():
    if os.path.exists(OUTFILE):
        with open(OUTFILE, encoding="utf-8") as f:
            return json.load(f)
    return {"_warning": WARNING, "created_utc": iso(now_utc()), "captures": [], "games": {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="YYYYMMDD or YYYYMMDD-YYYYMMDD")
    ap.add_argument("--seasontype", type=int, default=1, choices=[1, 2, 3],
                    help="1=preseason (default), 2=regular, 3=postseason")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    stamp = now_utc()
    days = daterange(args.dates)
    rows, errors = [], []
    for d in days:
        try:
            events = fetch_day(d, args.seasontype)
        except requests.RequestException as e:
            errors.append(f"{d}: fetch failed: {e}")
            continue
        for ev in events:
            try:
                rows.append(extract(ev, args.seasontype))
            except (UnknownTeam, ValueError, KeyError) as e:
                # Recorded as a failure, never dropped silently.
                errors.append(f"{d}: event {ev.get('id')}: {e}")

    store = load_store()
    store["_warning"] = WARNING
    new, updated, transitions = 0, 0, []
    for r in rows:
        gid = r["espn_id"]
        prev = store["games"].get(gid)
        if prev is None:
            r["first_seen_utc"] = iso(stamp)
            r["last_seen_utc"] = iso(stamp)
            r["status_history"] = [{"utc": iso(stamp), "status": r["status"]}]
            store["games"][gid] = r
            new += 1
        else:
            if prev.get("status") != r["status"]:
                prev.setdefault("status_history", []).append(
                    {"utc": iso(stamp), "status": r["status"]})
                transitions.append(f"{r['away']}@{r['home']}: "
                                   f"{prev.get('status')} -> {r['status']}")
            # Kickoff times move. Recording the change is the point of a
            # kickoff-relative clock; silently overwriting it hides a flex.
            if prev.get("kickoff_utc") != r["kickoff_utc"]:
                transitions.append(f"{r['away']}@{r['home']}: kickoff moved "
                                   f"{prev.get('kickoff_utc')} -> {r['kickoff_utc']}")
            prev.update({k: v for k, v in r.items() if k != "espn_id"})
            prev["last_seen_utc"] = iso(stamp)
            updated += 1

    store["captures"].append({
        "captured_utc": iso(stamp),
        "dates": args.dates,
        "seasontype": SEASONTYPE.get(args.seasontype),
        "n_events": len(rows),
        "n_new": new,
        "n_updated": updated,
        "n_errors": len(errors),
        "errors": errors,
    })

    # --- report --------------------------------------------------------------
    print(f"ESPN {SEASONTYPE.get(args.seasontype)} slate, dates {args.dates} "
          f"({len(days)} day{'s' if len(days) != 1 else ''})")
    print(f"captured {iso(stamp)}  ->  {len(rows)} events "
          f"({new} new, {updated} updated)")
    if rows:
        print(f"\n{'kickoff (UTC)':<21} {'T-24 (UTC)':<21} {'matchup':<12} "
              f"{'status':<18} score")
        for r in sorted(rows, key=lambda x: x["kickoff_utc"]):
            sc = ("-" if r["away_score"] is None
                  else f"{r['away_score']}-{r['home_score']}")
            t24 = r["t_minus_24_utc"]
            flag = "" if datetime.strptime(t24, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc) > stamp else "  (T-24 passed)"
            print(f"{r['kickoff_utc']:<21} {t24:<21} "
                  f"{r['away']+' @ '+r['home']:<12} "
                  f"{r['status'].replace('STATUS_',''):<18} {sc}{flag}")
    for t in transitions:
        print(f"  CHANGE {t}")
    if errors:
        print(f"\n{len(errors)} ERROR(S) - recorded, not dropped:")
        for e in errors:
            print("  -", e)

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    os.makedirs(os.path.dirname(OUTFILE), exist_ok=True)
    with open(OUTFILE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True)
    print(f"\nwrote {os.path.relpath(OUTFILE, ROOT)}  "
          f"({len(store['games'])} games tracked, {len(store['captures'])} captures)")
    print("this file is a plumbing test and is never promoted into any ledger")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
