#!/usr/bin/env python3
"""
Open Ledger Sports — daily data fetch.
Pulls today's MLB slate, standings, and probable-pitcher season stats from the
MLB Stats API, plus (optionally) live market odds from The Odds API when an
ODDS_API_KEY env var / repo secret is present. Writes data/snapshot_<date>.json
in the exact shape engine.py consumes.

Run: python scripts/fetch_data.py [YYYY-MM-DD]
"""
import json, os, statistics, sys
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

import crypto_box

DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SEASON = int(DATE[:4])
MLB = "https://statsapi.mlb.com/api/v1"

# Static season-level approximations — refresh from Baseball Savant seasonally.
PARK_FACTORS = {
    "Coors Field": 1.24, "Fenway Park": 1.06, "Chase Field": 1.05, "Kauffman Stadium": 1.03,
    "Yankee Stadium": 1.03, "Wrigley Field": 1.02, "Great American Ball Park": 1.04,
    "Citizens Bank Park": 1.01, "Angel Stadium": 1.00, "Truist Park": 1.00, "Rogers Centre": 1.00,
    "Dodger Stadium": 0.98, "American Family Field": 0.99, "Globe Life Field": 0.98,
    "Progressive Field": 0.97, "Daikin Park": 0.97, "Busch Stadium": 0.97, "Nationals Park": 0.99,
    "PNC Park": 0.97, "Oracle Park": 0.94, "Petco Park": 0.95, "T-Mobile Park": 0.92,
    "Citi Field": 0.96, "loanDepot park": 0.95, "Camden Yards": 1.00, "Target Field": 0.99,
    "Comerica Park": 0.97, "Guaranteed Rate Field": 1.03, "Rate Field": 1.03,
    "George M. Steinbrenner Field": 1.06, "Sutter Health Park": 1.02,
}

def get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    # The board runs on several cron windows because GitHub's scheduler is
    # unreliable. Whichever fires first wins; later ones must not re-fetch,
    # since overwriting the snapshot would break the hash already published in
    # that day's commitment.
    if crypto_box.already_published(ROOT, DATE) and "--force" not in sys.argv:
        print(f"Board for {DATE} is already published. Nothing to fetch.")
        return

    # ---- Schedule + probable pitchers ----
    sched = get(f"{MLB}/schedule", sportId=1, date=DATE, hydrate="probablePitcher")
    games_raw = sched["dates"][0]["games"] if sched.get("dates") else []
    games_raw = [g for g in games_raw if g.get("gameType") == "R"]  # regular season only

    # ---- Teams (abbreviations) ----
    teams_resp = get(f"{MLB}/teams", sportId=1, season=SEASON)
    abbr = {t["id"]: t.get("abbreviation", "???") for t in teams_resp["teams"]}
    names = {t["id"]: t["name"] for t in teams_resp["teams"]}

    # ---- Standings (W, L, runs scored/allowed) ----
    standings = get(f"{MLB}/standings", leagueId="103,104", season=SEASON, standingsTypes="regularSeason")
    teams = {}
    for div in standings["records"]:
        for rec in div["teamRecords"]:
            tid = rec["team"]["id"]
            teams[str(tid)] = {
                "name": names.get(tid, rec["team"].get("name", "?")),
                "abbr": abbr.get(tid, "???"),
                "w": rec["wins"], "l": rec["losses"],
                "rs": rec.get("runsScored"), "ra": rec.get("runsAllowed"),
            }
    missing = [t for t in teams.values() if t["rs"] is None or t["ra"] is None]
    if missing:
        raise SystemExit(f"Standings missing runs data for: {[t['abbr'] for t in missing]}")

    # ---- Probable pitcher season stats (one batched call) ----
    pids = sorted({g["teams"][side].get("probablePitcher", {}).get("id")
                   for g in games_raw for side in ("away", "home")
                   if g["teams"][side].get("probablePitcher")})
    pitchers = {}
    if pids:
        ppl = get(f"{MLB}/people", personIds=",".join(map(str, pids)),
                  hydrate=f"stats(group=[pitching],type=[season],season={SEASON})")
        for p in ppl["people"]:
            splits = (p.get("stats") or [{}])[0].get("splits") or []
            if not splits:
                continue
            s = splits[0]["stat"]
            try:
                pitchers[str(p["id"])] = {
                    "name": p["fullName"],
                    "era": float(s["era"]),
                    "ip": float(s["inningsPitched"]),
                    "whip": float(s["whip"]),
                    "k9": float(s["strikeoutsPer9Inn"]),
                }
            except (KeyError, ValueError):
                continue  # no usable season line -> treated as TBD by the engine

    games = []
    for g in games_raw:
        a, h = g["teams"]["away"], g["teams"]["home"]
        a_sp = a.get("probablePitcher", {}).get("id")
        h_sp = h.get("probablePitcher", {}).get("id")
        games.append({
            "gamePk": g["gamePk"],
            "away": a["team"]["id"], "home": h["team"]["id"],
            "utc": g["gameDate"],
            "venue": g.get("venue", {}).get("name", "Unknown"),
            "awaySP": a_sp if str(a_sp) in pitchers else None,
            "homeSP": h_sp if str(h_sp) in pitchers else None,
        })

    # ---- Odds (optional): The Odds API, consensus = median across books ----
    odds, odds_source = {}, None
    key = os.environ.get("ODDS_API_KEY")
    if key:
        try:
            events = get("https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
                         apiKey=key, regions="us", markets="h2h,totals", oddsFormat="american")
            by_name = {}
            for ev in events:
                by_name[(ev["away_team"], ev["home_team"])] = ev
            for g in games:
                a_name, h_name = teams[str(g["away"])]["name"], teams[str(g["home"])]["name"]
                ev = by_name.get((a_name, h_name))
                if not ev:
                    continue
                a_mls, h_mls, tots = [], [], []
                for bk in ev.get("bookmakers", []):
                    for m in bk.get("markets", []):
                        if m["key"] == "h2h":
                            for o in m["outcomes"]:
                                (a_mls if o["name"] == a_name else h_mls).append(o["price"])
                        elif m["key"] == "totals" and m["outcomes"]:
                            tots.append(m["outcomes"][0].get("point"))
                if a_mls and h_mls:
                    odds[str(g["gamePk"])] = {
                        "away_ml": int(statistics.median(a_mls)),
                        "home_ml": int(statistics.median(h_mls)),
                        "total": float(statistics.median([t for t in tots if t is not None])) if any(t is not None for t in tots) else None,
                    }
            odds_source = f"The Odds API, median across US books, fetched {datetime.utcnow().isoformat()}Z"
        except Exception as e:  # odds are optional — never sink the board over them
            print(f"WARNING: odds fetch failed ({e}); engine will run without market gates.")
            odds, odds_source = {}, None
    else:
        print("NOTE: no ODDS_API_KEY set — running without market odds (edge/Kelly/Rule 8 inactive).")

    snapshot = {
        "snapshot_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"MLB Stats API (statsapi.mlb.com), fetched {DATE}",
        "season": SEASON,
        "teams": teams,
        "pitchers": pitchers,
        "odds_source": odds_source,
        "odds": odds,
        "park_factors_note": "Approximate season-level run park factors; refresh from Baseball Savant.",
        "park_factors": PARK_FACTORS,
        "games": games,
    }
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    # Encrypted when a key is present. The snapshot is withheld alongside the
    # board because the engine is public and deterministic: publish the inputs
    # and anyone can re-derive the picks exactly.
    out, _sha, enc = crypto_box.save_dataset(ROOT, "snapshot", DATE, snapshot)
    print(f"Wrote {out}{' (encrypted)' if enc else ''}: {len(games)} games, "
          f"{len(pitchers)} pitchers, odds for {len(odds)} games")

if __name__ == "__main__":
    main()
