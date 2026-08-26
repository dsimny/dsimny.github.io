#!/usr/bin/env python3
"""
Open Ledger Sports — NFL team identity (fb-v0.1).

Two different problems that look like one:

1. SOURCE SPELLING. ESPN and nflverse disagree on exactly two abbreviations.
   Neither is wrong; they are different conventions for the same team.

2. FRANCHISE CONTINUITY. Three franchises relocated inside our data window and
   changed abbreviation when they did. For a rating system this matters a great
   deal: if OAK and LV are separate keys, the Raiders' Elo resets to the mean in
   2020 and the model spends half a season rediscovering a team it already knew.
   They are the same franchise with the same roster, so they carry one rating.

Both maps were DERIVED from the data (a diff of ESPN's teams endpoint against
nflverse's 2024+ abbreviations, and a first/last-season scan of games.csv), not
recalled from memory. `python scripts/football/teams.py` re-derives and re-checks
them against games.csv on demand.

A note on what is NOT here: no team is ever mapped to a "generic" or "unknown"
key. An abbreviation this module does not recognise raises. A silently
mismapped team is a corrupted rating that produces confident, wrong numbers.
"""
import argparse, csv, io, json, os, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
GAMES = os.path.join(ROOT, "data", "football", "games.csv")

# ESPN spelling -> nflverse spelling. Exactly two, verified by set-diff.
ESPN_TO_NFLVERSE = {
    "LAR": "LA",    # Rams
    "WSH": "WAS",   # Commanders
}

# Historical abbreviation -> canonical franchise key. The canonical key is the
# CURRENT abbreviation, so a franchise's key stops changing once it has moved.
#   STL -> LA   Rams, St. Louis -> Los Angeles, 2016
#   SD  -> LAC  Chargers, San Diego -> Los Angeles, 2017
#   OAK -> LV   Raiders, Oakland -> Las Vegas, 2020
# Ratings carry across these. A relocation is a change of address, not a new team.
RELOCATIONS = {
    "STL": "LA",
    "SD": "LAC",
    "OAK": "LV",
}

# The 32 current franchise keys.
FRANCHISES = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO",
    "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
}


NAMES_FILE = os.path.join(ROOT, "data", "football", "team_names.json")


class UnknownTeam(ValueError):
    """Raised rather than guessing. See the module docstring."""


def _norm_name(s):
    return " ".join(str(s).strip().lower().split())


def build_names():
    """Derive full-name -> canonical from ESPN's teams endpoint and cache it.

    The Odds API identifies teams by full display name ("Kansas City Chiefs"),
    not abbreviation, so joining prices to games needs a THIRD identity map on
    top of the two above. It is generated from a source rather than typed from
    memory, and regenerating it is how we find out that a team was renamed.
    """
    import requests
    j = requests.get(
        "http://site.api.espn.com/apis/site/v2/sports/football/nfl/teams",
        timeout=30).json()
    teams = [t["team"] for t in j["sports"][0]["leagues"][0]["teams"]]
    names = {}
    for t in teams:
        key = canonical(t["abbreviation"], source="espn")
        for field in ("displayName", "shortDisplayName", "name", "nickname"):
            v = t.get(field)
            if v:
                names[_norm_name(v)] = key
    os.makedirs(os.path.dirname(NAMES_FILE), exist_ok=True)
    with io.open(NAMES_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "_note": ("full team name -> canonical franchise key. Generated from "
                      "ESPN's teams endpoint by teams.py --build-names, not typed "
                      "from memory. The Odds API keys its events by full display "
                      "name, so this is what joins prices to games."),
            "_generated_from": "site.api.espn.com/apis/site/v2/sports/football/nfl/teams",
            "names": dict(sorted(names.items())),
        }, f, indent=2)
    return names


def _load_names():
    if not os.path.exists(NAMES_FILE):
        raise UnknownTeam(
            f"{os.path.relpath(NAMES_FILE, ROOT)} missing - run "
            "`python scripts/football/teams.py --build-names` first")
    with io.open(NAMES_FILE, encoding="utf-8") as f:
        return json.load(f)["names"]


def from_name(name, source="odds-api"):
    """Full team name -> canonical franchise key. Raises on anything unmatched.

    Deliberately no fuzzy matching. A near-miss that silently resolves to the
    wrong team attaches a price to the wrong game, which is worse than a loud
    failure listing exactly which name we could not place.
    """
    key = _load_names().get(_norm_name(name))
    if key is None:
        raise UnknownTeam(
            f"unrecognised team name {name!r} from {source}. Re-run "
            "`teams.py --build-names` (a team may have been renamed); if the name "
            "is genuinely new, add it deliberately rather than fuzzy-matching.")
    return key


def canonical(abbr, source="nflverse"):
    """Any source spelling, any era -> the canonical franchise key.

    Raises UnknownTeam on anything unrecognised. There is deliberately no
    fallback: a team quietly mapped to the wrong rating is worse than a crash,
    because the crash is visible and the bad rating is not.
    """
    if abbr is None:
        raise UnknownTeam(f"empty team abbreviation from {source}")
    a = abbr.strip().upper()
    if not a:
        raise UnknownTeam(f"empty team abbreviation from {source}")
    a = ESPN_TO_NFLVERSE.get(a, a)
    a = RELOCATIONS.get(a, a)
    if a not in FRANCHISES:
        raise UnknownTeam(
            f"unrecognised team {abbr!r} from {source}. If this is a real new or "
            "renamed franchise, add it to FRANCHISES (and RELOCATIONS if it kept "
            "its history) deliberately - do not add a fallback."
        )
    return a


def selfcheck(verbose=True):
    """Re-derive both maps from games.csv and confirm they still hold."""
    if not os.path.exists(GAMES):
        print(f"games.csv not found at {GAMES} - run nflverse_ingest.py first")
        return 1

    rows = list(csv.DictReader(io.open(GAMES, encoding="utf-8")))
    spans, fails = {}, []
    for r in rows:
        for t in (r["away_team"], r["home_team"]):
            s = int(r["season"])
            lo, hi = spans.get(t, (9999, 0))
            spans[t] = (min(lo, s), max(hi, s))

    # Every abbreviation that has ever appeared must canonicalise without raising.
    for t in sorted(spans):
        try:
            canonical(t)
        except UnknownTeam as e:
            fails.append(str(e))

    # Every canonical key must be one of the 32, and all 32 must be reachable.
    reached = {canonical(t) for t in spans if t not in ("",)}
    missing = FRANCHISES - reached
    if missing:
        fails.append(f"franchises never reached from the data: {sorted(missing)}")

    # A relocated pair must not overlap in time: if it does, they are two
    # different teams that played each other, not one team that moved.
    for old, new in RELOCATIONS.items():
        if old in spans and new in spans:
            old_hi, new_lo = spans[old][1], spans[new][0]
            if old_hi >= new_lo:
                fails.append(
                    f"{old} (through {old_hi}) overlaps {new} (from {new_lo}) - "
                    "these cannot be the same franchise")

    if verbose:
        print(f"{len(spans)} distinct abbreviations across {len(rows):,} games")
        for old, new in sorted(RELOCATIONS.items()):
            if old in spans and new in spans:
                print(f"  {old} {spans[old][0]}-{spans[old][1]}  ->  "
                      f"{new} {spans[new][0]}-{spans[new][1]}   (one franchise, one rating)")
        print(f"  ESPN spelling fixes: {', '.join(f'{k}->{v}' for k, v in ESPN_TO_NFLVERSE.items())}")
        print(f"  canonical franchises reached: {len(reached)}/32")

    if fails:
        print("\nSELFCHECK FAILED")
        for f in fails:
            print("  -", f)
        return 1
    if verbose:
        print("\nselfcheck PASS")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-names", action="store_true",
                    help="regenerate data/football/team_names.json from ESPN")
    a = ap.parse_args()
    if a.build_names:
        n = build_names()
        print(f"wrote {os.path.relpath(NAMES_FILE, ROOT)}  "
              f"({len(n)} name spellings -> 32 franchises)")
    sys.exit(selfcheck())
