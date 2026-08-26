#!/usr/bin/env python3
"""
Open Ledger Sports — NCAA FBS results source (ESPN public scoreboard, no key).

WHY THIS EXISTS. `fetch_odds.py --sport ncaaf` captures prices in "verbatim"
identity mode: it stores the Odds API's own team strings and claims NO canonical
key, because `teams.py` knows 32 NFL franchises and nothing else. That is
sufficient for price capture and for the selection rule in
`docs/FOOTBALL_PIPELINE.md`, which works entirely off the odds payload. It is
NOT sufficient for GRADING, which has to join a price to an outcome. This module
is that join.

THE JOIN, and why it is exact rather than fuzzy. Measured 2026-08-25 against the
2026-08-29 slate: 14 of 16 Odds API team names matched ESPN `displayName`
byte-for-byte. The two failures were pure orthography —

    'Hawaii Rainbow Warriors'   vs  "Hawai'i Rainbow Warriors"
    'San Jose State Spartans'   vs  'San José State Spartans'

— so the fix is a deterministic normaliser (NFKD decompose, drop combining
marks, drop everything that is not a letter or digit, casefold), after which
16 of 16 matched. NO fuzzy matching, no edit distance, no "closest" name. A
price attached to the wrong game is a silent corruption that later reads as an
edge, and an approximate matcher is how that happens. Normalise, then require
an exact hit, then FAIL LOUD on anything left over.

SCORES ONLY WHEN FINAL. ESPN reports "0" for an unplayed game. A game that has
not been played has NO score, not a score of zero — the same trap
`espn_slate.py` documents for the NFL. Anything not STATUS_FINAL is stored with
scores of None and is not gradeable.

WHAT THIS FILE MAY NOT DO. It writes `data/football/ncaaf_results.json` and
nowhere else. It never touches `ledger.json`, `daily_ledger.json`,
`totals_ledger.json`, `watchlist.json`, or any football ledger. Grading is a
separate step that reads this; House Rule 1 makes a ledger permanent, so nothing
uninformative may enter one.

Run:
  python scripts/football/espn_ncaaf.py --dates 20260829
  python scripts/football/espn_ncaaf.py --dates 20260829-20260901
  python scripts/football/espn_ncaaf.py --dates 20260829 --join     # report the
                                          # join rate against the latest odds snapshot
"""
import argparse, glob, io, json, os, re, sys, unicodedata
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
STORE = os.path.join(FB, "ncaaf_results.json")
ODDS_DIR = os.path.join(FB, "odds")
SCOREBOARD = ("http://site.api.espn.com/apis/site/v2/sports/football/"
              "college-football/scoreboard")
FBS_GROUP = "80"          # ESPN's group id for FBS
PAGE_LIMIT = 400          # a full Saturday is ~60 FBS games; 400 is headroom

# THE SEASON-TYPE ALLOWLIST (docs/FOOTBALL_PIPELINE.md section 3a). College has
# no preseason, so this looked like an NFL-only concern and was not implemented
# here. IT IS NOT NFL-ONLY: college has a POSTSEASON, and bowls are type 3.
#
# VERIFIED AGAINST THE LIVE ENDPOINT 2026-08-26:
#     2025-08-30  -> 62 events, all {'type': 2, 'slug': 'regular-season'}
#     2025-12-27  ->  8 events, all {'type': 3, 'slug': 'post-season'}
#
# Left unimplemented, the first December run would have booked eight bowls into
# an append-only ledger under House Rule 1 with no decision ever taken about
# whether a neutral-site, month-of-layoff market is the same product. That is
# the same unrecoverable failure preseason poses for the NFL, arriving four
# months later. ALLOWLIST, NOT BLOCKLIST: type 2 is named positively and
# everything else is refused, including types ESPN has not invented yet.
GRADEABLE_SEASON_TYPES = frozenset({2})
SEASON_TYPE_NAMES = {1: "preseason", 2: "regular-season", 3: "post-season",
                     4: "off-season/all-star"}


def gradeable(row):
    """The allowlist, in one place so nothing re-derives it."""
    return row.get("season_type") in GRADEABLE_SEASON_TYPES


def norm(s):
    """Team name -> comparison key. Deterministic, lossy, never approximate."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ALIASES: Odds API key -> ESPN key. Applied ONLY after an exact match on the
# normalised key has already failed, and every entry is a real, explained
# discrepancy observed in the data — never a fuzzy fallback and never a guess.
# Same discipline as teams.ESPN_TO_NFLVERSE, which holds exactly two entries
# verified by set-diff.
#
# Add to this map only with the reason written down. An entry that cannot be
# explained is a mismatch someone should look at, not paper over: attaching a
# price to the wrong game is a silent corruption that later reads as an edge.
ALIASES = {
    # The school rebranded from "Sam Houston State" to "Sam Houston" in 2023.
    # ESPN carries the current name; The Odds API still carries the old one.
    # Observed 2026-08-25 on the 2026-09-05 slate (Sam Houston @ Troy).
    "samhoustonstatebearkats": "samhoustonbearkats",
}


def key_for(name):
    """Normalised key for an Odds API team name, alias-corrected."""
    k = norm(name)
    return ALIASES.get(k, k)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_espn_dt(s):
    """ESPN returns e.g. '2026-08-29T16:00Z'."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def daterange(spec):
    a, _, b = spec.partition("-")
    start = datetime.strptime(a, "%Y%m%d")
    end = datetime.strptime(b, "%Y%m%d") if b else start
    out, d = [], start
    while d <= end:
        out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def fetch_day(datestr):
    r = requests.get(SCOREBOARD, timeout=30,
                     params={"dates": datestr, "groups": FBS_GROUP,
                             "limit": PAGE_LIMIT})
    r.raise_for_status()
    return r.json().get("events", [])


def extract(ev):
    """One ESPN event -> our row. Raises rather than guessing."""
    comp = ev["competitions"][0]
    sides = {}
    for c in comp["competitors"]:
        t = c["team"]
        sides[c["homeAway"]] = {
            "espn_id": t.get("id"),
            "name": t.get("displayName"),
            "key": norm(t.get("displayName")),
            "abbr": t.get("abbreviation"),
            "score": c.get("score"),
        }
    if set(sides) != {"home", "away"}:
        raise ValueError(f"event {ev.get('id')} has competitors {sorted(sides)}")

    season = ev.get("season") or {}
    stype = season.get("type")
    state = ev["status"]["type"]["name"]
    final = state == "STATUS_FINAL"
    hs = as_ = None
    if final:
        try:
            hs, as_ = int(sides["home"]["score"]), int(sides["away"]["score"])
        except (TypeError, ValueError):
            # Final with no parseable score is a data fault, not a 0-0 game.
            raise ValueError(f"event {ev.get('id')} is FINAL but has no usable "
                             f"score: home={sides['home']['score']!r} "
                             f"away={sides['away']['score']!r}")
    return {
        "espn_event_id": ev.get("id"),
        "kickoff_utc": iso(parse_espn_dt(ev.get("date"))) if ev.get("date") else None,
        "status": state,
        "final": final,
        # Section 3a. Stored on every row, including the ones that will never be
        # graded, so the refusal is auditable rather than invisible.
        "season_year": season.get("year"),
        "season_type": stype,
        "season_slug": season.get("slug") or SEASON_TYPE_NAMES.get(stype, "unknown"),
        "home": sides["home"]["name"], "home_key": sides["home"]["key"],
        "away": sides["away"]["name"], "away_key": sides["away"]["key"],
        "home_abbr": sides["home"]["abbr"], "away_abbr": sides["away"]["abbr"],
        # None, never 0, until the game is actually final.
        "home_score": hs, "away_score": as_,
        "margin": (hs - as_) if final else None,      # home perspective
        "total": (hs + as_) if final else None,
    }


def load_store():
    try:
        with io.open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"_note": ("NCAA FBS results from ESPN's public scoreboard. Keyed "
                          "by ESPN event id. Scores are None until a game is "
                          "STATUS_FINAL - an unplayed game has no score, not a "
                          "score of zero. season_type is recorded on every row; "
                          "ONLY type 2 (regular-season) is gradeable - bowls are "
                          "type 3 and are refused, per docs/FOOTBALL_PIPELINE.md "
                          "section 3a. Grading reads this; nothing here writes "
                          "to any ledger."),
                "events": {}}


def latest_odds_snapshot():
    fs = sorted(glob.glob(os.path.join(ODDS_DIR, "ncaaf_*.json")))
    if not fs:
        return None
    with io.open(fs[-1], encoding="utf-8") as f:
        return json.load(f)


def report_join(rows):
    """How many priced games can actually be graded? Run this before a dark run."""
    snap = latest_odds_snapshot()
    if not snap:
        print("no ncaaf odds snapshot on disk; nothing to join.")
        return 0
    by_pair = {(r["away_key"], r["home_key"]): r for r in rows}
    covered = [e for e in snap["events"] if e["n_books"] >= 5]
    hit, miss = 0, []
    aliased = []
    for e in covered:
        ka, kh = key_for(e["away_raw"]), key_for(e["home_raw"])
        if (ka, kh) in by_pair:
            hit += 1
            if ka != norm(e["away_raw"]) or kh != norm(e["home_raw"]):
                aliased.append(f'{e["away_raw"]} @ {e["home_raw"]}')
        else:
            miss.append(f'{e["away_raw"]} @ {e["home_raw"]}')
    print(f"\njoin against {os.path.basename(sorted(glob.glob(os.path.join(ODDS_DIR,'ncaaf_*.json')))[-1])}:")
    print(f"  priced games with >=5 books : {len(covered)}")
    print(f"  joined to an ESPN event     : {hit}")
    if aliased:
        print(f"  joined VIA ALIAS ({len(aliased)}) - each one is an explained entry "
              f"in ALIASES, listed so the map stays auditable:")
        for a in aliased:
            print("    -", a)
    if miss:
        print(f"  UNJOINED ({len(miss)}) - these could be priced but not graded:")
        for m in miss[:15]:
            print("    -", m)
    return len(miss)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="YYYYMMDD or YYYYMMDD-YYYYMMDD")
    ap.add_argument("--join", action="store_true",
                    help="also report the join rate against the latest odds snapshot")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    rows, bad = [], []
    for d in daterange(args.dates):
        try:
            evs = fetch_day(d)
        except requests.RequestException as e:
            print(f"ESPN FETCH FAILED for {d}: {e}")
            print("nothing written. Degrade, never die.")
            return 1
        for ev in evs:
            try:
                rows.append(extract(ev))
            except (KeyError, ValueError) as e:
                bad.append(str(e))
        print(f"  {d}: {len(evs)} FBS events")

    if bad:
        print(f"\nABORTING: {len(bad)} event(s) could not be read without guessing.")
        for b in bad[:10]:
            print("  -", b)
        return 1

    finals = [r for r in rows if r["final"]]
    print(f"\n{len(rows)} events, {len(finals)} final")

    by_type = {}
    for r in rows:
        by_type[r["season_slug"]] = by_type.get(r["season_slug"], 0) + 1
    for slug, n in sorted(by_type.items()):
        mark = ("GRADEABLE" if any(gradeable(r) for r in rows
                                   if r["season_slug"] == slug)
                else "NOT gradeable")
        print(f"   {slug:<16} {n:>3}   {mark}")

    if args.join:
        report_join(rows)

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    store = load_store()
    for r in rows:
        store["events"][r["espn_event_id"]] = r
    store["updated_utc"] = iso(datetime.now(timezone.utc))
    os.makedirs(FB, exist_ok=True)
    with io.open(STORE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(store, f, indent=1, sort_keys=True)
    print(f"wrote {os.path.relpath(STORE, ROOT)} "
          f"({len(store['events'])} events held)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
