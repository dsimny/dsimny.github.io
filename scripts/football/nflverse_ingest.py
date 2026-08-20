#!/usr/bin/env python3
"""
Open Ledger Sports — NFL data foundation (fb-v0.1).

Pulls the nflverse games file (1999-present, refreshed continuously) and lays it
down as the football model's historical foundation. Regular season and playoffs
only: nflverse carries NO preseason rows, which is why the preseason plumbing
test runs off ESPN instead (see espn_slate.py).

THE ONE THING THIS SCRIPT EXISTS TO ENFORCE. The nflverse file ships market data
in the same table as football data - spread_line, total_line, moneylines, and
the juice on each side. Fitting ratings on a table that contains the closing
spread is the single easiest way to leak the market into a "market-blind" model,
and it would not look like a bug: it would look like a model that works. So the
market columns are physically SPLIT OUT here, into a separate file that the
model side never opens, tagged Tier C because they carry no timestamp and are
therefore structurally incapable of producing closing-line value.

Outputs, all under data/football/:
  raw/games_<sha16>.csv.gz         content-addressed copy of exactly what we got
  manifest.json                    sha256 / url / bytes / rows / fetched_utc
  games.csv                        football facts only. No market columns. Ever.
  market_reference_tierC.csv       the quarantined market columns
  column_availability.json         which clock each column resolves on

Run: python scripts/football/nflverse_ingest.py [--min-season YYYY]
"""
import argparse, csv, gzip, hashlib, io, json, os, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUT = os.path.join(ROOT, "data", "football")
ET = ZoneInfo("America/New_York")

# Elo needs settled ratings before the development window opens, so we ingest a
# burn-in run-up rather than starting cold in 2015. The burn-in is NOT part of
# development, validation or holdout - it only initialises the ratings.
DEFAULT_MIN_SEASON = 2010
BURN_IN_THROUGH = 2014
DEV_SEASONS = (2015, 2024)
HOLDOUT_SEASON = 2025
LIVE_SEASON = 2026

# --- the quarantine list -----------------------------------------------------
# Every one of these is a sportsbook number. None of them may appear in
# games.csv, and no model-fitting code may read the file they go to.
MARKET_COLUMNS = [
    "away_moneyline", "home_moneyline",
    "spread_line", "away_spread_odds", "home_spread_odds",
    "total_line", "under_odds", "over_odds",
]

# --- availability clocks (prereg section 6) ----------------------------------
# Which moment each column can first be honestly known at. "schedule" means it
# is known when the schedule is published, i.e. available at T-24 and earlier.
# "result" means kickoff+4h. "stats" means kickoff+36h. Anything marked result
# or stats is a LEAKAGE HAZARD if a T-24 feature touches it, and the as-of guard
# reads this file rather than a hardcoded list so the two cannot drift apart.
AVAILABILITY = {
    "schedule": [
        "game_id", "season", "game_type", "week", "gameday", "weekday", "gametime",
        "kickoff_utc", "away_team", "home_team", "location", "away_rest", "home_rest",
        "div_game", "roof", "surface", "stadium_id", "stadium",
        "espn", "pfr", "gsis", "old_game_id", "nfl_detail_id", "pff", "ftn",
        "away_coach", "home_coach",
    ],
    # QBs and the referee are announced during the week but nflverse backfills
    # them to who ACTUALLY played/officiated, not who was announced. Treating
    # them as schedule-time would import hindsight (the QB who got hurt in
    # warmups is never the one in this column). Result-time until we ingest the
    # real practice/inactive reports.
    "result": [
        "away_score", "home_score", "result", "total", "overtime",
        "away_qb_id", "home_qb_id", "away_qb_name", "home_qb_name", "referee",
        # temp/wind are OBSERVED game-time conditions, not the forecast that was
        # available at T-24. A model that reads actual kickoff wind is reading
        # the future.
        "temp", "wind",
    ],
    "stats": [],
}


def fetch(url):
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def clock_of(col):
    for clock, cols in AVAILABILITY.items():
        if col in cols:
            return clock
    return "UNCLASSIFIED"


def kickoff_utc(row):
    """ET gameday+gametime -> aware UTC. None when nflverse has no time (1999)."""
    day, tm = row.get("gameday", ""), row.get("gametime", "")
    if not day or not tm:
        return None
    try:
        naive = datetime.strptime(f"{day} {tm}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=ET).astimezone(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-season", type=int, default=DEFAULT_MIN_SEASON)
    args = ap.parse_args()

    os.makedirs(os.path.join(OUT, "raw"), exist_ok=True)

    print(f"fetching {GAMES_URL}")
    blob = fetch(GAMES_URL)
    sha = hashlib.sha256(blob).hexdigest()
    # Gzipped, because nflverse rewrites this file every week as scores land and
    # an ingest per week would otherwise put ~40MB of near-identical CSV into git
    # over one season. The sha256 is of the UNCOMPRESSED bytes, so it stays
    # comparable against the source and across compression settings. Keeping the
    # raw copy at all is non-negotiable: nflverse mutates in place, so the exact
    # bytes we fitted on are not re-fetchable later from the URL alone.
    raw_path = os.path.join(OUT, "raw", f"games_{sha[:16]}.csv.gz")
    if not os.path.exists(raw_path):
        with gzip.open(raw_path, "wb", compresslevel=9) as f:
            f.write(blob)
        print(f"  stored raw -> {os.path.relpath(raw_path, ROOT)} "
              f"({len(blob):,} bytes -> {os.path.getsize(raw_path):,} gzipped)")
    else:
        print(f"  raw already on disk (identical sha) -> {os.path.basename(raw_path)}")

    rows = list(csv.DictReader(io.StringIO(blob.decode("utf-8"))))
    source_cols = list(rows[0].keys())

    # Fail closed on schema drift. If nflverse adds a column we have never
    # classified, we do NOT guess which clock it belongs to - an unclassified
    # column silently treated as schedule-time is exactly the leak this file
    # exists to prevent.
    unknown = [c for c in source_cols if clock_of(c) == "UNCLASSIFIED" and c not in MARKET_COLUMNS]
    if unknown:
        sys.exit(
            "REFUSING TO WRITE: nflverse added column(s) this ingest has never "
            f"classified: {unknown}\nAdd each one to AVAILABILITY (or to "
            "MARKET_COLUMNS if it is a sportsbook number) and re-run. Guessing "
            "the availability clock is how look-ahead bias gets in."
        )

    kept = [c for c in source_cols if c not in MARKET_COLUMNS]
    sel = [r for r in rows if r["season"].isdigit() and int(r["season"]) >= args.min_season]
    sel.sort(key=lambda r: (int(r["season"]), r["gameday"] or "", r["gametime"] or "", r["game_id"]))

    derived = ["kickoff_utc", "t_minus_24_utc", "result_available_at_utc", "stats_available_at_utc"]
    no_time = 0
    for r in sel:
        ko = kickoff_utc(r)
        if ko is None:
            no_time += 1
        r["kickoff_utc"] = iso(ko)
        r["t_minus_24_utc"] = iso(ko - timedelta(hours=24) if ko else None)
        r["result_available_at_utc"] = iso(ko + timedelta(hours=4) if ko else None)
        r["stats_available_at_utc"] = iso(ko + timedelta(hours=36) if ko else None)

    # --- games.csv: football facts only --------------------------------------
    games_path = os.path.join(OUT, "games.csv")
    game_cols = kept + derived
    with open(games_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=game_cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sel)

    # --- the quarantine ------------------------------------------------------
    mkt_path = os.path.join(OUT, "market_reference_tierC.csv")
    with open(mkt_path, "w", newline="", encoding="utf-8") as f:
        f.write("# TIER C - REFERENCE ONLY. NOT ELIGIBLE FOR CLV, EVER.\n")
        f.write("# These are sportsbook numbers with no capture timestamp: there is no way\n")
        f.write("# to know whether a given spread_line was the open, the close, or a\n")
        f.write("# midweek number, so any 'closing-line value' computed from them would be\n")
        f.write("# a number that looks fine and means nothing.\n")
        f.write("# NO MODEL-FITTING CODE MAY READ THIS FILE. It exists so that games.csv\n")
        f.write("# can be honestly market-blind, and for market-structure diagnostics only.\n")
        w = csv.DictWriter(f, fieldnames=["game_id", "season", "week"] + MARKET_COLUMNS,
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(sel)

    # --- column availability manifest ----------------------------------------
    avail = {c: clock_of(c) for c in kept}
    avail.update({
        "kickoff_utc": "schedule", "t_minus_24_utc": "schedule",
        "result_available_at_utc": "schedule", "stats_available_at_utc": "schedule",
    })
    with open(os.path.join(OUT, "column_availability.json"), "w", encoding="utf-8") as f:
        json.dump({
            "_note": ("Which clock each column of games.csv resolves on. The as-of "
                      "feature engine reads THIS, not a hardcoded list, so the guard "
                      "and the data cannot drift apart. 'schedule' is safe at T-24; "
                      "'result' (kickoff+4h) and 'stats' (kickoff+36h) are leakage "
                      "hazards for any T-24 feature."),
            "clocks": {"schedule": "known when the schedule is published",
                       "result": "kickoff + 4h",
                       "stats": "kickoff + 36h"},
            "columns": avail,
            "quarantined_market_columns": MARKET_COLUMNS,
        }, f, indent=2, sort_keys=True)

    # --- manifest ------------------------------------------------------------
    manifest_path = os.path.join(OUT, "manifest.json")
    manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else {"ingests": []}
    manifest["ingests"].append({
        "source": GAMES_URL,
        "sha256": sha,
        "bytes": len(blob),
        "source_rows": len(rows),
        "kept_rows": len(sel),
        "min_season": args.min_season,
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # --- integrity report ----------------------------------------------------
    by_season = {}
    for r in sel:
        s = int(r["season"])
        d = by_season.setdefault(s, {"games": 0, "scored": 0, "no_kickoff": 0})
        d["games"] += 1
        if r.get("home_score"):
            d["scored"] += 1
        if not r["kickoff_utc"]:
            d["no_kickoff"] += 1

    print(f"\nwrote {os.path.relpath(games_path, ROOT)}  "
          f"{len(sel):,} games, {len(game_cols)} cols (market columns removed)")
    print(f"wrote {os.path.relpath(mkt_path, ROOT)}  quarantined: {', '.join(MARKET_COLUMNS)}")
    print(f"\n{'season':>7} {'games':>6} {'scored':>7} {'no kickoff':>11}  role")
    for s in sorted(by_season):
        d = by_season[s]
        if s <= BURN_IN_THROUGH:
            role = "burn-in (ratings init only)"
        elif DEV_SEASONS[0] <= s <= DEV_SEASONS[1]:
            role = "development"
        elif s == HOLDOUT_SEASON:
            role = "HOLDOUT - locked until freeze"
        elif s == LIVE_SEASON:
            role = "live forward test (0u)"
        else:
            role = ""
        print(f"{s:>7} {d['games']:>6} {d['scored']:>7} {d['no_kickoff']:>11}  {role}")

    if no_time:
        print(f"\nNOTE: {no_time} rows have no kickoff time and therefore no T-24. "
              "They are kept but any as-of feature must skip them, not assume a time.")

    # A missing score in a past season is a real data problem; a missing score in
    # the live season is just a game that has not been played.
    for s, d in sorted(by_season.items()):
        if s < LIVE_SEASON and d["scored"] != d["games"]:
            print(f"WARNING: season {s} has {d['games'] - d['scored']} unscored games")

    leaked = [c for c in game_cols if c in MARKET_COLUMNS]
    print(f"\nmarket-blindness check: {'FAIL ' + str(leaked) if leaked else 'PASS (0 market columns in games.csv)'}")


if __name__ == "__main__":
    main()
