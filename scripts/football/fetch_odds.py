#!/usr/bin/env python3
"""
Open Ledger Sports — football odds capture, Tier A (fb-v0.1).

Captures NFL prices from The Odds API and stores them as TIER A snapshots: our
own first-party pull, stamped with the exact UTC moment we received it. That
timestamp is the whole point. Prices without a capture time are Tier C - they
may be the open, the close or a midweek number, and any "closing-line value"
computed from them is a number that looks fine and means nothing.

WHAT THIS IS FOR IN THE PRESEASON WINDOW: measuring the things an estimate
cannot settle - the real credit cost per call, whether the Odds API's team
naming joins cleanly to our franchise keys, and whether a T-24 that lands
mid-week behaves. It captures prices only. It produces no pick, no edge, no
model output; per pre-registration section 4, preseason tests plumbing only.

CREDIT COST: markets x regions per call. h2h,spreads,totals across us = 3
credits. Every call's reading is appended to data/odds_credits.json in the
clear (a counter and a timestamp leak nothing) so the budget stays visible.

DEGRADE, NEVER DIE: any odds failure - quota, 401, 429, timeout, garbage - is
caught and logged, and the script exits non-zero without writing a partial
snapshot. It never invents a price.

FAIL LOUD ON IDENTITY: an unmatched team name ABORTS. Attaching a price to the
wrong game is a silent corruption, and it is exactly the kind of error that
shows up later as an edge.

Run:
  python scripts/football/fetch_odds.py --sport preseason --dry-run
  python scripts/football/fetch_odds.py --sport preseason
  python scripts/football/fetch_odds.py --sport nfl
"""
import argparse, json, os, sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import localenv                                    # noqa: E402
from teams import from_name, UnknownTeam           # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
ODDS_DIR = os.path.join(ROOT, "data", "football", "odds")
CREDIT_LOG = os.path.join(ROOT, "data", "odds_credits.json")
CREDIT_LOG_KEEP = 60
API = "https://api.the-odds-api.com/v4/sports/{sport}/odds"

SPORT_KEYS = {
    "preseason": "americanfootball_nfl_preseason",
    "nfl": "americanfootball_nfl",
}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def record_credits(credits):
    """Append a plaintext credit reading. Never raises - mirrors fetch_closing."""
    try:
        log = {"readings": []}
        if os.path.exists(CREDIT_LOG):
            with open(CREDIT_LOG, encoding="utf-8") as f:
                log = json.load(f)
        log["readings"] = (log.get("readings", []) + [credits])[-CREDIT_LOG_KEEP:]
        os.makedirs(os.path.dirname(CREDIT_LOG), exist_ok=True)
        with open(CREDIT_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:
        print(f"NOTE: could not record odds credits: {exc}")


def normalise(events, captured_utc, sport_key):
    """Odds API events -> our shape, with every book's price kept.

    We keep EVERY book rather than collapsing to a consensus here. De-vigging
    and consensus-building happen later, against a stored record of what was
    actually offered - so that a consensus can be rebuilt or corrected without
    re-spending credits on a moment that has already passed.
    """
    out, unmatched = [], set()
    for ev in events:
        try:
            away = from_name(ev["away_team"], source="odds-api")
            home = from_name(ev["home_team"], source="odds-api")
        except UnknownTeam as e:
            unmatched.add(str(e))
            continue
        books = []
        for bk in ev.get("bookmakers", []):
            markets = {}
            for m in bk.get("markets", []):
                markets[m["key"]] = [
                    {"name": o.get("name"), "price": o.get("price"),
                     "point": o.get("point")}
                    for o in m.get("outcomes", [])
                ]
            books.append({
                "book": bk.get("key"),
                "last_update": bk.get("last_update"),
                "markets": markets,
            })
        out.append({
            "odds_api_event_id": ev.get("id"),
            "commence_time": ev.get("commence_time"),
            "away": away,
            "home": home,
            "books": books,
            "n_books": len(books),
        })
    return out, unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="preseason", choices=sorted(SPORT_KEYS))
    ap.add_argument("--markets", default="h2h,spreads,totals")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the call and its credit cost; spend nothing")
    args = ap.parse_args()

    sport_key = SPORT_KEYS[args.sport]
    n_markets = len([m for m in args.markets.split(",") if m.strip()])
    n_regions = len([r for r in args.regions.split(",") if r.strip()])
    cost = n_markets * n_regions

    print(f"sport   {sport_key}")
    print(f"markets {args.markets}  regions {args.regions}")
    print(f"cost    {cost} credits ({n_markets} markets x {n_regions} regions)")

    if args.dry_run:
        print("\n--dry-run: no call made, no credits spent")
        return 0

    key = localenv.require("ODDS_API_KEY")
    print(f"key     fingerprint {localenv.fingerprint(key)} (not the key itself)")

    stamp = datetime.now(timezone.utc)
    try:
        r = requests.get(API.format(sport=sport_key), timeout=30, params={
            "apiKey": key, "regions": args.regions, "markets": args.markets,
            "oddsFormat": "american"})
    except requests.RequestException as e:
        print(f"ODDS FETCH FAILED (network): {e}")
        print("nothing written. Degrade, never die - a missing price feed costs "
              "a capture, never a fabricated number.")
        return 1

    def _int(name):
        try:
            return int(r.headers.get(name))
        except (TypeError, ValueError):
            return None

    credits = {"remaining": _int("x-requests-remaining"), "used": _int("x-requests-used"),
               "last_call_cost": _int("x-requests-last"), "markets": args.markets,
               "regions": args.regions, "http_status": r.status_code,
               "source": f"football_fetch_odds:{args.sport}",
               "read_utc": iso(stamp)}
    # Read BEFORE raise_for_status, so a 401 (dead key) or 429 (month spent) -
    # the readings that matter most - survive the failure.
    print(f"credits {credits['remaining']} remaining, {credits['used']} used, "
          f"this call cost {credits['last_call_cost']}")
    record_credits(credits)

    if r.status_code != 200:
        print(f"ODDS FETCH FAILED (HTTP {r.status_code}): {r.text[:300]}")
        print("nothing written.")
        return 1

    try:
        events = r.json()
    except ValueError:
        print("ODDS FETCH FAILED: response was not JSON. Nothing written.")
        return 1

    rows, unmatched = normalise(events, iso(stamp), sport_key)

    if unmatched:
        print(f"\nABORTING: {len(unmatched)} unmatched team name(s).")
        for u in sorted(unmatched):
            print("  -", u)
        print("No snapshot written. A price attached to the wrong game is a "
              "silent corruption that later reads as an edge.")
        return 1

    if not rows:
        print("\nno events returned (out of season, or no books posted yet). "
              "Nothing written.")
        return 0

    snap = {
        "tier": "A",
        "_tier_note": ("Tier A: first-party pull, stamped with the UTC moment WE "
                       "received it. Eligible for CLV precisely because that "
                       "timestamp exists."),
        "captured_utc": iso(stamp),
        "sport_key": sport_key,
        "markets": args.markets,
        "regions": args.regions,
        "credits": credits,
        "n_events": len(rows),
        "events": rows,
    }
    os.makedirs(ODDS_DIR, exist_ok=True)
    path = os.path.join(ODDS_DIR,
                        f"{args.sport}_{stamp.strftime('%Y%m%dT%H%M%SZ')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1, sort_keys=True)

    print(f"\n{'matchup':<12} {'books':>5}  commence (UTC)")
    for e in sorted(rows, key=lambda x: x["commence_time"] or ""):
        print(f"{e['away']+' @ '+e['home']:<12} {e['n_books']:>5}  {e['commence_time']}")
    print(f"\nwrote {os.path.relpath(path, ROOT)}  "
          f"({len(rows)} events, Tier A)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
