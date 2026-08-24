#!/usr/bin/env python3
"""
Open Ledger Sports — historical T-24 odds pull (fb-v0.1).

The first code in this project that reads a price. Everything upstream of it -
ingest, as-of engine, Elo, ridge, game model - was fitted and frozen without one.

WHAT IT FETCHES. For every distinct hour in which some game sat exactly 24 hours
from kickoff, one historical snapshot of the whole NFL board. Snapshots return
the entire slate, so cost scales with TIMESTAMPS, not games - and coarsening the
slots barely helps (4-hour buckets save 2% of them and cost up to 3.7h of drift
from the real decision moment), so the slots stay hourly.

DEFAULT RANGE 2022-2024: 357 slots, 854 games, 10,710 credits. See the note on
DEFAULT_SEASONS for why 2020-2021 are excluded rather than merely cheap to skip.

COST. Historical requests bill 10x: 10 x markets x regions. At h2h,spreads,totals
across us that is 30 credits a snapshot.

RESUMABLE, BECAUSE A LONG JOB WILL FAIL PARTWAY. Every snapshot is written
to its own file and an existing file is skipped without a call, so a re-run
costs nothing for work already done. Interrupt it freely.

SAFETY RAIL. The script stops if the remaining credit balance falls below
--floor (default 5,000), so a bug in a loop cannot drain the account. It also
stops on the first HTTP error rather than hammering a dead key 500 times.

TIER A. These snapshots carry the timestamp WE requested and the timestamp the
API says it returned, both recorded. That is what makes them eligible for a
T-24 comparison at all; the same prices without those stamps would be Tier C.

Run:
  python scripts/football/fetch_historical_odds.py --plan      # 0 calls, shows the job
  python scripts/football/fetch_historical_odds.py --probe     # 1 call, 30 credits
  python scripts/football/fetch_historical_odds.py             # 2022-2024, 10,710
  python scripts/football/fetch_historical_odds.py --limit 20  # first 20 remaining
"""
import argparse, io, json, os, sys, time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asof                                        # noqa: E402
import localenv                                    # noqa: E402
from teams import from_name, UnknownTeam           # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
SNAPS = os.path.join(FB, "odds", "hist")
CREDIT_LOG = os.path.join(ROOT, "data", "odds_credits.json")
INDEX = os.path.join(FB, "odds", "hist_index.json")
API = "https://api.the-odds-api.com/v4/historical/sports/americanfootball_nfl/odds"

# 2022-2024 by default, NOT 2020-2024. 2020 and 2021 sit inside the model's TUNE
# window, so the model was fitted on them: scoring it against the market there
# flatters the model in exactly the direction that would turn a null result into
# an apparent signal. Those two seasons are also 6,540 credits. They can still be
# pulled deliberately with --seasons 2020-2021, and market_compare.py labels any
# TUNE season in-sample and keeps it out of the headline.
DEFAULT_SEASONS = "2022-2024"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def slots(seasons):
    """Distinct hours in which some game sat at T-24, ascending."""
    # schedule_only: this job needs kickoff hours and game ids, nothing else.
    # It is also what lets the HOLDOUT season be priced without burning the
    # one-shot evaluation on a calendar lookup. See asof.assert_season_allowed.
    games = asof.load_games(seasons=seasons, purpose="historical odds pull",
                            schedule_only=True)
    out = {}
    for g in games:
        t = g["_t24"].replace(minute=0, second=0, microsecond=0)
        out.setdefault(iso(t), []).append(g["game_id"])
    return dict(sorted(out.items())), games


def snap_path(stamp):
    return os.path.join(SNAPS, f"nfl_{stamp.replace(':', '').replace('-', '')}.json")


def record_credits(entry):
    try:
        log = {"readings": []}
        if os.path.exists(CREDIT_LOG):
            with io.open(CREDIT_LOG, encoding="utf-8") as f:
                log = json.load(f)
        log["readings"] = (log.get("readings", []) + [entry])[-60:]
        with io.open(CREDIT_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:
        print(f"NOTE: could not record odds credits: {exc}")


def normalise(payload, requested, markets, regions):
    """API payload -> our shape. Unmatched team names are collected, not guessed."""
    data = payload.get("data", payload)
    events, unmatched = [], set()
    for ev in data or []:
        try:
            away = from_name(ev["away_team"], source="odds-api-historical")
            home = from_name(ev["home_team"], source="odds-api-historical")
        except UnknownTeam as e:
            unmatched.add(str(e))
            continue
        books = []
        for bk in ev.get("bookmakers", []):
            m = {}
            for mk in bk.get("markets", []):
                m[mk["key"]] = [{"name": o.get("name"), "price": o.get("price"),
                                 "point": o.get("point")} for o in mk.get("outcomes", [])]
            books.append({"book": bk.get("key"), "last_update": bk.get("last_update"),
                          "markets": m})
        events.append({"odds_api_event_id": ev.get("id"),
                       "commence_time": ev.get("commence_time"),
                       "away": away, "home": home, "books": books,
                       "n_books": len(books)})
    return {
        "tier": "A",
        "_tier_note": ("Historical snapshot. Carries BOTH the timestamp we asked "
                       "for and the timestamp the API says it returned, which is "
                       "what makes a T-24 comparison possible at all."),
        "requested_utc": requested,
        "snapshot_utc": payload.get("timestamp"),
        "previous_snapshot_utc": payload.get("previous_timestamp"),
        "next_snapshot_utc": payload.get("next_timestamp"),
        "markets": markets, "regions": regions,
        "n_events": len(events), "events": events,
        "unmatched_team_names": sorted(unmatched),
    }


NET_RETRIES = 4
NET_BACKOFF = 3.0          # seconds, doubling


def fetch_one(stamp, key, markets, regions, floor):
    # Transient network failure must not end a 357-call job. The first run of
    # this pull died on a ConnectionResetError at snapshot 111 - no fault of the
    # API, just a dropped socket - and resuming worked, but a job that needs
    # babysitting through a blip is a job that will be abandoned half-done.
    # HTTP errors are deliberately NOT retried here: a 401 or 429 is a real
    # answer about the key or the quota, and hammering it is the wrong response.
    last = None
    for attempt in range(NET_RETRIES):
        try:
            r = requests.get(API, timeout=60, params={
                "apiKey": key, "regions": regions, "markets": markets,
                "oddsFormat": "american", "date": stamp})
            break
        except requests.RequestException as e:
            last = e
            if attempt == NET_RETRIES - 1:
                raise SystemExit(
                    f"network failure at {stamp} after {NET_RETRIES} attempts: {e}\n"
                    "Snapshots already fetched are on disk; re-running resumes "
                    "from where this stopped and costs nothing for them.")
            wait = NET_BACKOFF * (2 ** attempt)
            print(f"    network hiccup at {stamp} ({type(e).__name__}), "
                  f"retry {attempt + 1}/{NET_RETRIES - 1} in {wait:.0f}s")
            time.sleep(wait)

    def _int(n):
        try:
            return int(r.headers.get(n))
        except (TypeError, ValueError):
            return None

    rem, used, cost = _int("x-requests-remaining"), _int("x-requests-used"), _int("x-requests-last")
    if r.status_code != 200:
        record_credits({"remaining": rem, "used": used, "last_call_cost": cost,
                        "markets": markets, "regions": regions,
                        "http_status": r.status_code,
                        "source": "football_historical", "read_utc": iso(datetime.now(timezone.utc))})
        raise SystemExit(f"HTTP {r.status_code} at {stamp}: {r.text[:300]}\n"
                         "Stopping rather than repeating a failing call. Already-"
                         "fetched snapshots are on disk; re-running resumes.")
    if rem is not None and rem < floor:
        raise SystemExit(f"credit floor reached: {rem} remaining, floor {floor}. "
                         "Stopping. Re-run with a lower --floor to continue.")
    return r.json(), rem, used, cost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default=DEFAULT_SEASONS,
                    help="inclusive range, e.g. 2022-2024 (default) or 2020-2021")
    ap.add_argument("--markets", default="h2h,spreads,totals")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new snapshots")
    ap.add_argument("--floor", type=int, default=5000,
                    help="stop if remaining credits fall below this")
    ap.add_argument("--probe", action="store_true", help="one snapshot, then stop")
    ap.add_argument("--plan", action="store_true", help="no calls; show the job")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    a, _, b = args.seasons.partition("-")
    seasons = list(range(int(a), int(b or a) + 1))
    by_slot, games = slots(seasons)
    os.makedirs(SNAPS, exist_ok=True)
    done = [s for s in by_slot if os.path.exists(snap_path(s))]
    todo = [s for s in by_slot if not os.path.exists(snap_path(s))]
    per_call = 10 * len([m for m in args.markets.split(",") if m]) * \
        len([r for r in args.regions.split(",") if r])

    print(f"seasons {seasons[0]}-{seasons[-1]}   {len(games):,} games   "
          f"{len(by_slot)} distinct T-24 hours")
    print(f"markets {args.markets}  regions {args.regions}  "
          f"-> {per_call} credits per snapshot (historical bills 10x)")
    print(f"already on disk {len(done)}   remaining {len(todo)}   "
          f"projected cost {len(todo) * per_call:,} credits")
    if args.plan:
        print("\n--plan: no calls made. First 5 slots: " + ", ".join(todo[:5]))
        return 0
    if not todo:
        print("\nnothing to fetch - every slot already on disk.")
        return 0

    key = localenv.require("ODDS_API_KEY")
    print(f"key fingerprint {localenv.fingerprint(key)} (not the key itself)\n")

    if args.probe:
        todo = todo[:1]
    elif args.limit:
        todo = todo[:args.limit]

    t0, spent, unmatched_total = time.time(), 0, set()
    for i, stamp in enumerate(todo, 1):
        payload, rem, used, cost = fetch_one(stamp, key, args.markets,
                                             args.regions, args.floor)
        snap = normalise(payload, stamp, args.markets, args.regions)
        with io.open(snap_path(stamp), "w", encoding="utf-8", newline="\n") as f:
            json.dump(snap, f, indent=1, sort_keys=True)
        spent += cost or per_call
        unmatched_total.update(snap["unmatched_team_names"])
        if i == 1 or i % 25 == 0 or i == len(todo):
            rate = i / max(time.time() - t0, 1e-9)
            print(f"  {i}/{len(todo)}  {stamp}  {snap['n_events']:>2} events  "
                  f"snapshot={snap['snapshot_utc']}  credits left {rem}  "
                  f"({rate*60:.0f}/min)")
        time.sleep(args.sleep)

    record_credits({"remaining": rem, "used": used, "last_call_cost": cost,
                    "markets": args.markets, "regions": args.regions,
                    "http_status": 200, "source": "football_historical",
                    "read_utc": iso(datetime.now(timezone.utc))})

    idx = {"_note": ("Which T-24 hour each game maps to. Built from games.csv, "
                     "not from the odds payloads, so a missing snapshot shows up "
                     "as a missing match rather than silently dropping a game."),
           "seasons": seasons, "markets": args.markets, "regions": args.regions,
           "slots": {s: by_slot[s] for s in by_slot}}
    with io.open(INDEX, "w", encoding="utf-8", newline="\n") as f:
        json.dump(idx, f, indent=1, sort_keys=True)

    print(f"\nfetched {len(todo)} snapshots, ~{spent:,} credits, "
          f"{rem} remaining")
    print(f"wrote {os.path.relpath(INDEX, ROOT)}")
    if unmatched_total:
        print(f"\n{len(unmatched_total)} UNMATCHED team name(s) - these games will "
              "not join, and are listed rather than guessed:")
        for u in sorted(unmatched_total):
            print("  -", u)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
