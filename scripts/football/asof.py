#!/usr/bin/env python3
"""
Open Ledger Sports — as-of feature engine and leakage guard (fb-v0.1).

Every football feature is computed as of a specific instant, using information
AVAILABILITY, not event time. The distinction is the whole point:

    event_time     when the thing happened
    available_at   when we could first honestly have known it
    ingested_at    when we actually stored it

THE SUBTLE ONE, which a naive implementation gets wrong. A rating system that
sorts games by date and updates sequentially will let a Sunday 1pm result inform
a Sunday 4:25pm prediction. It will not look wrong - the games are "in order" -
but the 4:25 game's decision moment is T-24, i.e. SATURDAY 4:25pm, and the 1pm
result does not exist until Sunday 5pm. The model would be reading four hours
into its own future, every week, for the whole sample.

So the rule here is not "earlier games first". It is:

    the rating used for game G may incorporate exactly those games whose
    result_available_at <= G.t_minus_24

which is a merge of two differently-sorted streams, and which correctly DOES let
Thursday night inform Sunday (result Thu ~23:30, Sunday's T-24 is Saturday) while
correctly refusing to let Sunday early inform Sunday late.

HOLDOUT LOCK. 2025 is the one-shot holdout. This module refuses to hand it out
while docs/FOOTBALL_PREREG.md still says `frozen: NOT YET`. The lock lives in
code rather than in discipline because the whole value of a holdout is that it
cannot be peeked at, and "I'll remember not to" is not a mechanism.

Run: python scripts/football/asof.py     (self-test on the real data)
"""
import csv, io, json, os, re, sys
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
GAMES = os.path.join(FB, "games.csv")
AVAIL = os.path.join(FB, "column_availability.json")
PREREG = os.path.join(ROOT, "docs", "FOOTBALL_PREREG.md")

BURN_IN = (2010, 2014)
DEV = (2015, 2024)
HOLDOUT = 2025
LIVE = 2026


class LeakageError(Exception):
    """A feature tried to read something that did not exist yet."""


class HoldoutLocked(Exception):
    """The holdout season was requested before the methodology was frozen."""


def parse_utc(s):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# --- the holdout lock -------------------------------------------------------

def frozen_date():
    """The `frozen:` value from the pre-registration, or None if not frozen."""
    if not os.path.exists(PREREG):
        return None
    with io.open(PREREG, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^frozen:\s*(.+?)\s*$", line)
            if m:
                v = m.group(1).strip()
                return None if v.upper().startswith("NOT") else v
    return None


def assert_season_allowed(season, purpose="fit"):
    """Refuse the holdout until the methodology is frozen and committed."""
    season = int(season)
    if season <= DEV[1]:
        return
    if season >= LIVE:
        return                                  # live season is forward-only
    if season == HOLDOUT and frozen_date() is None:
        raise HoldoutLocked(
            f"season {HOLDOUT} is the one-shot holdout and the pre-registration "
            f"still reads `frozen: NOT YET` (purpose: {purpose}).\n"
            "Freeze the methodology first: set `frozen:` to a date in "
            "docs/FOOTBALL_PREREG.md, commit it, THEN evaluate - exactly once.\n"
            "This lock is deliberate. A holdout you can peek at is not a holdout."
        )


# --- loading ----------------------------------------------------------------

def load_availability():
    with io.open(AVAIL, encoding="utf-8") as f:
        return json.load(f)["columns"]


def load_games(seasons=None, played_only=True, purpose="fit"):
    """Games as dicts, kickoff/T-24 parsed. Enforces the holdout lock."""
    if seasons is not None:
        for s in seasons:
            assert_season_allowed(s, purpose=purpose)
    rows = []
    with io.open(GAMES, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            if seasons is not None and s not in seasons:
                continue
            if seasons is None:
                assert_season_allowed(s, purpose=purpose)
            if played_only and not r["home_score"]:
                continue
            if not r["kickoff_utc"]:
                # No kickoff time means no T-24, so there is no honest decision
                # moment. Skipped rather than assumed (1999 only; none in range).
                continue
            r["_season"] = s
            r["_t24"] = parse_utc(r["t_minus_24_utc"])
            r["_result_at"] = parse_utc(r["result_available_at_utc"])
            r["_kickoff"] = parse_utc(r["kickoff_utc"])
            if r["home_score"]:
                r["_home_score"] = int(r["home_score"])
                r["_away_score"] = int(r["away_score"])
                r["_margin"] = r["_home_score"] - r["_away_score"]   # home perspective
                r["_total"] = r["_home_score"] + r["_away_score"]
            rows.append(r)
    return rows


# --- the guard --------------------------------------------------------------

def assert_visible(column, asof, row, availability=None):
    """Raise unless `column` of `row` was knowable at `asof`.

    Fail-closed: an unclassified column raises rather than being assumed safe.
    """
    availability = availability or load_availability()
    clock = availability.get(column)
    if clock is None:
        raise LeakageError(
            f"column {column!r} has no availability classification. Refusing to "
            "assume it is safe - add it to AVAILABILITY in nflverse_ingest.py "
            "and re-run the ingest.")
    if clock == "schedule":
        return
    stamp = row.get("result_available_at_utc" if clock == "result"
                    else "stats_available_at_utc")
    when = parse_utc(stamp)
    if when is None:
        raise LeakageError(f"{row.get('game_id')}: no {clock} timestamp to check "
                           f"{column!r} against")
    if when > asof:
        raise LeakageError(
            f"LEAKAGE: {column!r} of {row.get('game_id')} is {clock}-time "
            f"(available {stamp}) but was read as of {asof:%Y-%m-%dT%H:%M:%SZ} - "
            f"{(when - asof).total_seconds() / 3600:.1f}h into the future.")


# --- the timeline -----------------------------------------------------------

def walk(games, update, predict=None):
    """Walk games in DECISION order, applying results only once available.

    For each game G, in ascending T-24 order:
      1. apply `update(g)` for every prior game whose result became available
         at or before G's T-24;
      2. call `predict(G)` - at which moment the state contains exactly the
         information that existed at G's decision moment, and nothing else.

    Returns the list of predict() returns (skipping None).

    The two orderings are genuinely different. Sorting by kickoff and updating
    as you go is the bug this function exists to make impossible.
    """
    by_decision = sorted(games, key=lambda g: (g["_t24"], g["game_id"]))
    by_result = sorted((g for g in games if g.get("_result_at")),
                       key=lambda g: (g["_result_at"], g["game_id"]))

    out, i = [], 0
    for g in by_decision:
        while i < len(by_result) and by_result[i]["_result_at"] <= g["_t24"]:
            update(by_result[i])
            i += 1
        if predict is not None:
            p = predict(g)
            if p is not None:
                out.append(p)
    # Drain the remainder so the caller's state is complete after the walk.
    while i < len(by_result):
        update(by_result[i])
        i += 1
    return out


# --- self-test --------------------------------------------------------------

def _selftest():
    fails = []

    # 1. The holdout is locked while the prereg is unfrozen.
    print(f"pre-registration frozen: {frozen_date() or 'NOT YET'}")
    try:
        load_games(seasons=[HOLDOUT], purpose="selftest")
        fails.append(f"holdout season {HOLDOUT} was handed out while unfrozen")
    except HoldoutLocked:
        print(f"  holdout lock: season {HOLDOUT} correctly REFUSED")

    dev = list(range(DEV[0], DEV[1] + 1))
    games = load_games(seasons=list(range(BURN_IN[0], BURN_IN[1] + 1)) + dev,
                       purpose="selftest")
    print(f"  loaded {len(games):,} played games, {BURN_IN[0]}-{DEV[1]}")

    # 2. The guard catches a result-time read at T-24, and allows it after.
    g = games[len(games) // 2]
    av = load_availability()
    try:
        assert_visible("home_score", g["_t24"], g, av)
        fails.append("guard allowed a result-time read at T-24")
    except LeakageError:
        pass
    try:
        assert_visible("home_score", g["_result_at"], g, av)
    except LeakageError:
        fails.append("guard blocked a result-time read AFTER the result landed")
    try:
        assert_visible("roof", g["_t24"], g, av)
    except LeakageError:
        fails.append("guard blocked a schedule-time column at T-24")
    try:
        assert_visible("spread_line", g["_t24"], g, av)
        fails.append("guard allowed an UNCLASSIFIED column instead of raising")
    except LeakageError:
        pass
    print("  leakage guard: blocks result-time at T-24, allows schedule-time, "
          "refuses unclassified")

    # 3. The walk never applies a result that was not yet available. This is the
    #    Sunday-early-informs-Sunday-late bug, checked directly.
    applied, violations, sunday_pairs = [], [], 0

    def update(g):
        applied.append(g)

    def predict(g):
        for a in applied:
            if a["_result_at"] > g["_t24"]:
                violations.append((a["game_id"], g["game_id"]))
        return None

    walk(games, update, predict)
    if violations:
        fails.append(f"{len(violations)} results applied before they existed, "
                     f"e.g. {violations[:2]}")
    print(f"  timeline: {len(applied):,} results applied, {len(violations)} "
          "applied before they were available")

    # 4. Show that the ordering actually BITES - count same-day pairs where a
    #    naive kickoff-ordered walk would have leaked.
    by_kick = sorted(games, key=lambda g: g["_kickoff"])
    for idx, later in enumerate(by_kick):
        for earlier in by_kick[max(0, idx - 16):idx]:
            if earlier["_result_at"] > later["_t24"]:
                sunday_pairs += 1
    print(f"  a kickoff-ordered walk would have leaked in ~{sunday_pairs:,} "
          "same-window game pairs (this is what walk() prevents)")

    if fails:
        print("\nSELFTEST FAILED")
        for f in fails:
            print("  -", f)
        return 1
    print("\nasof selftest PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
