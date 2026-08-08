#!/usr/bin/env python3
"""
Open Ledger Sports — closing-line capture (CLV support, roadmap item 2).

Run repeatedly through the day (its own cron-job.org trigger). Each run fetches
the CURRENT market odds for every game on <date>'s slate and records them in
data/closing_<date>.json, but ONLY for games that have NOT started yet. Because
a game already under way is skipped, the value that survives for each game is
the LAST line seen before its first pitch — i.e. the closing line. The nightly
grader (grade.py) reads this file and books CLV per pick: did the morning price
we published beat the closing price?

Why a separate script instead of reusing the morning snapshot:
  - The snapshot is committed ENCRYPTED, so reading game times out of it would
    need BOARD_ENCRYPTION_KEY. This script re-fetches the schedule from the MLB
    Stats API instead, so it needs only ODDS_API_KEY. If that key is unset it
    no-ops cleanly (exit 0) and CLV simply stays blank on the ledger, exactly as
    it is today.
  - The slate spans many hours (1pm day games, 10pm night games). One capture
    can't be every game's close, so we accumulate: run this a few times a day
    and each game keeps the odds captured closest to (but before) its first
    pitch. An already-started game is never overwritten with an in-play line.

Run: python scripts/fetch_closing.py [YYYY-MM-DD]
The odds parse mirrors fetch_data.py's (median h2h + totals across US books);
kept standalone on purpose so this script never imports the committed morning
pipeline. If you change one, eyeball the other.
"""
import json, os, statistics, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MLB = "https://statsapi.mlb.com/api/v1"
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
SEASON = int(DATE[:4])


def get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_utc(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def fetch_market_odds(games, team_names, key):
    """Median h2h + totals across US books, keyed by gamePk. Mirrors fetch_data.py."""
    events = get("https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
                 apiKey=key, regions="us", markets="h2h,totals", oddsFormat="american")
    by_name = {(ev["away_team"], ev["home_team"]): ev for ev in events}
    odds = {}
    for g in games:
        ev = by_name.get((team_names.get(g["away"]), team_names.get(g["home"])))
        if not ev:
            continue
        a_mls, h_mls, tots, ovr, und = [], [], [], [], []
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        (a_mls if o["name"] == team_names.get(g["away"]) else h_mls).append(o["price"])
                elif m["key"] == "totals":
                    for o in m.get("outcomes", []):
                        if o.get("point") is not None:
                            tots.append(o["point"])
                        if o.get("name") == "Over" and o.get("price") is not None:
                            ovr.append(o["price"])
                        elif o.get("name") == "Under" and o.get("price") is not None:
                            und.append(o["price"])
        if a_mls and h_mls:
            rec = {
                "away_ml": int(statistics.median(a_mls)),
                "home_ml": int(statistics.median(h_mls)),
                "total": float(statistics.median(tots)) if tots else None,
            }
            if ovr and und:
                rec["over_price"] = int(statistics.median(ovr))
                rec["under_price"] = int(statistics.median(und))
            odds[str(g["gamePk"])] = rec
    return odds


def main():
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        print("NOTE: no ODDS_API_KEY set — closing lines not captured (CLV stays blank).")
        return

    sched = get(f"{MLB}/schedule", sportId=1, date=DATE)
    games_raw = [g for g in (sched["dates"][0]["games"] if sched.get("dates") else [])
                 if g.get("gameType") == "R"]
    if not games_raw:
        print(f"[{DATE}] no regular-season games on the slate; nothing to capture.")
        return
    team_names = {t["id"]: t["name"] for t in get(f"{MLB}/teams", sportId=1, season=SEASON)["teams"]}

    games = [{"gamePk": g["gamePk"],
              "away": g["teams"]["away"]["team"]["id"],
              "home": g["teams"]["home"]["team"]["id"],
              "utc": g["gameDate"],
              "state": g.get("status", {}).get("abstractGameState")} for g in games_raw]

    try:
        live = fetch_market_odds(games, team_names, key)
    except Exception as e:  # never let a bad odds fetch matter — just capture nothing this run
        print(f"WARNING: odds fetch failed ({e}); nothing captured this run.")
        return

    path = os.path.join(ROOT, "data", f"closing_{DATE}.json")
    store = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            store = json.load(f)

    now = datetime.now(timezone.utc)
    updated = frozen = 0
    for g in games:
        pk = str(g["gamePk"])
        o = live.get(pk)
        if not o:
            continue
        start = parse_utc(g["utc"])
        started = (start is not None and start <= now) or g["state"] in ("Live", "Final")
        if started and pk in store:
            frozen += 1                 # keep the last pre-game capture; never overwrite with an in-play line
            continue
        if started and pk not in store:
            o = dict(o, note="first capture was after first pitch — weak close")
        mins = round((start - now).total_seconds() / 60) if start else None
        # Capture HISTORY for the odds-movement page: every distinct pre-pitch
        # capture, oldest first. Appended only when the numbers actually moved,
        # so 4 identical captures stay one point. The top-level fields keep
        # their original meaning (the LAST pre-pitch capture) — grade.py's CLV
        # read is untouched. Names and first pitch ride along so the movement
        # page never needs the encrypted board (this job has no key by design).
        hist = store.get(pk, {}).get("history", [])
        point = {k: o.get(k) for k in ("away_ml", "home_ml", "total", "over_price", "under_price")}
        if not hist or any(hist[-1].get(k) != point[k] for k in point):
            hist = (hist + [{**point, "captured_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}])[-24:]
        store[pk] = {**o,
                     "away_name": team_names.get(g["away"]),
                     "home_name": team_names.get(g["home"]),
                     "utc": g["utc"],
                     "history": hist,
                     "captured_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "mins_to_first_pitch": mins}
        updated += 1

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1)
    print(f"[{DATE}] closing capture: {updated} game(s) updated, {frozen} already-started left frozen; "
          f"{len(store)} total in {os.path.basename(path)}")


if __name__ == "__main__":
    main()
