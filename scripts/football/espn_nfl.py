#!/usr/bin/env python3
"""
Open Ledger Sports — NFL results source (ESPN public scoreboard, no key).

The NFL twin of espn_ncaaf.py, and it exists for the same reason: prices are
captured from The Odds API and outcomes come from ESPN, so something has to join
them. Grading cannot happen without this file.

IDENTITY IS CANONICAL HERE, NOT VERBATIM, and that is the whole difference from
the college module. `fetch_odds.py --sport nfl` resolves every team through
teams.py to a franchise key and ABORTS on anything unresolvable, so an NFL
capture stores "NE" in `away` and "New England Patriots" in `away_raw`. This
module resolves ESPN's abbreviation through the same teams.canonical(), so the
two sides meet at the franchise key rather than at a display string. 32
franchises with stable abbreviations make that exact and cheap; 134 FBS
programmes plus their FCS opponents are why college does it the other way.

SEASON TYPE IS RECORDED ON EVERY ROW, and this is the point of the module.
docs/FOOTBALL_PIPELINE.md section 3a: ESPN returns preseason games alongside
regular-season ones and NOTHING in the capture path distinguishes them. Wire
grading without this and preseason entries book into a permanent, append-only
ledger looking exactly like real ones. House Rule 1 is what makes that
unrecoverable.

ALLOWLIST, NOT BLOCKLIST. `GRADEABLE_SEASON_TYPES` names what may be graded
POSITIVELY - type 2, regular-season - and everything else is refused, including
values ESPN has not invented yet. A blocklist on type 1 would silently grade any
new type, and the direction of that error is a corrupted permanent ledger.
Postseason (type 3) is deliberately absent: a neutral-site, layoff-affected
market may not be the same product, and that decision has not been made.

VERIFIED AGAINST THE LIVE ENDPOINT 2026-08-26:
    2026-09-10 (opener)   -> {'year': 2026, 'type': 2, 'slug': 'regular-season'}
    2026-08-29 (preseason)-> {'year': 2026, 'type': 1, 'slug': 'preseason'}

SCORES ONLY WHEN FINAL. ESPN reports "0" for an unplayed game. A game that has
not been played has NO score, not a score of zero.

WHAT THIS FILE MAY NOT DO. It writes data/football/nfl_results.json and nowhere
else. It never touches any ledger.

Run:
  python scripts/football/espn_nfl.py --dates 20260910
  python scripts/football/espn_nfl.py --dates 20260910-20260915
  python scripts/football/espn_nfl.py --dates 20260829        # preseason: stored,
                                                              # flagged, never gradeable
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teams                                          # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
STORE = os.path.join(FB, "nfl_results.json")
SCOREBOARD = ("http://site.api.espn.com/apis/site/v2/sports/football/"
              "nfl/scoreboard")
PAGE_LIMIT = 400

# The allowlist. See the module docstring before touching it.
GRADEABLE_SEASON_TYPES = frozenset({2})
SEASON_TYPE_NAMES = {1: "preseason", 2: "regular-season", 3: "postseason",
                     4: "off-season/all-star"}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_espn_dt(s):
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
                     params={"dates": datestr, "limit": PAGE_LIMIT})
    r.raise_for_status()
    return r.json().get("events", [])


def gradeable(row):
    """The allowlist, in one place so nothing re-derives it."""
    return row.get("season_type") in GRADEABLE_SEASON_TYPES


def extract(ev):
    """One ESPN event -> our row. Raises rather than guessing."""
    comp = ev["competitions"][0]
    sides = {}
    for c in comp["competitors"]:
        t = c["team"]
        # teams.canonical raises UnknownTeam on anything unrecognised. Let it:
        # a franchise quietly mapped to the wrong key attaches a price to the
        # wrong game, and the crash is visible where the bad join is not.
        sides[c["homeAway"]] = {
            "key": teams.canonical(t.get("abbreviation"), source="espn"),
            "name": t.get("displayName"),
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
        # `home`/`away` are CANONICAL FRANCHISE KEYS, matching what an NFL
        # capture stores in its own home/away fields. The display names are kept
        # alongside for the writeup, never for the join.
        "home": sides["home"]["key"], "away": sides["away"]["key"],
        "home_name": sides["home"]["name"], "away_name": sides["away"]["name"],
        "home_abbr": sides["home"]["abbr"], "away_abbr": sides["away"]["abbr"],
        "home_score": hs, "away_score": as_,
        "margin": (hs - as_) if final else None,      # home perspective
        "total": (hs + as_) if final else None,
    }


def load_store():
    try:
        with io.open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"_note": ("NFL results from ESPN's public scoreboard, keyed by "
                          "ESPN event id. home/away are CANONICAL franchise keys "
                          "(teams.py), matching what an NFL odds capture stores. "
                          "Scores are None until STATUS_FINAL - an unplayed game "
                          "has no score, not a score of zero. season_type is "
                          "recorded on every row; ONLY type 2 (regular-season) is "
                          "gradeable, per docs/FOOTBALL_PIPELINE.md section 3a. "
                          "Grading reads this; nothing here writes to a ledger."),
                "events": {}}


def save_store(store):
    store["updated_utc"] = iso(datetime.now(timezone.utc))
    # newline LF like every other writer in this tree: without it Windows
    # writes CRLF and the whole store re-diffs on a machine that did not.
    with io.open(STORE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(store, f, indent=1, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True,
                    help="YYYYMMDD or YYYYMMDD-YYYYMMDD (inclusive)")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    store = load_store()
    rows, failures = [], []
    for d in daterange(args.dates):
        # Degrade, never die (the shape espn_ncaaf.py already uses): a flaky
        # scoreboard must not leave a half-written store behind, so a fetch
        # failure aborts before anything is saved rather than partway through.
        try:
            events = fetch_day(d)
        except requests.RequestException as exc:
            print(f"ESPN FETCH FAILED for {d}: {exc}")
            print("nothing written. Degrade, never die.")
            return 1
        for ev in events:
            try:
                rows.append(extract(ev))
            # Narrow, so a genuine bug surfaces as a crash instead of being
            # filed as one more unresolvable team.
            except (teams.UnknownTeam, ValueError, KeyError) as exc:
                failures.append(f"{d} event {ev.get('id')}: {exc}")

    by_type = {}
    for r in rows:
        by_type[r["season_slug"]] = by_type.get(r["season_slug"], 0) + 1
    n_grade = sum(1 for r in rows if gradeable(r))

    print(f"{len(rows)} events over {len(daterange(args.dates))} day(s)")
    for slug, n in sorted(by_type.items()):
        mark = "GRADEABLE" if any(gradeable(r) for r in rows
                                  if r["season_slug"] == slug) else "NOT gradeable"
        print(f"   {slug:<16} {n:>3}   {mark}")
    print(f"{n_grade} gradeable, {sum(1 for r in rows if r['final'])} final")

    if failures:
        # Loud, and never silently skipped: an unresolvable franchise means the
        # join would be wrong, not merely incomplete.
        print(f"\n{len(failures)} EXTRACTION FAILURE(S):")
        for f in failures:
            print("  ", f)

    if args.dry_run:
        print("\n(--dry-run: nothing written)")
        return 1 if failures else 0

    for r in rows:
        store["events"][r["espn_event_id"]] = r
    save_store(store)
    print(f"\nwrote {os.path.relpath(STORE, ROOT)} ({len(store['events'])} events)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
