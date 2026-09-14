#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live, package ML-1: shared constants and time.

Mercer Live is the live-football observation system whose rules are frozen in
docs/MERCER_LIVE_V0.1_PREREGISTRATION.md. ML-1 is its DATA step and nothing
else: it captures time-stamped game state and market quotes and preserves them
append-only. It computes no edge, generates no pick, allocates no unit, posts
nothing anywhere, and uses no LLM. See docs/MERCER_LIVE_ML1_ARCHITECTURE.md.

ISOLATION IS THE DESIGN. This package lives beside scripts/football/, imports
three of its identity helpers READ-ONLY (teams, espn_ncaaf, fetch_odds constants)
and writes under data/mercer_live/ and nowhere else. Nothing in scripts/football
imports from here, globs data/mercer_live, or changes because this exists.

TIME. Every stored timestamp is UTC, ISO-8601 with a trailing Z, produced by
iso() from an AWARE datetime. Naive datetimes are refused at the boundary
(parse_utc returns aware or None; iso() raises on naive). Eastern time is used
for exactly two things: the ESPN `dates=` parameter (ESPN's scoreboard days are
US days) and the slate-week / slate-day labels in canonical ids, because a
Monday-night kickoff at 00:15Z Tuesday belongs to Monday's slate.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FOOTBALL = os.path.join(ROOT, "scripts", "football")
SCRIPTS = os.path.join(ROOT, "scripts")

# Read-only imports from the football package. APPENDED to sys.path so nothing
# here can shadow a module in scripts/football, and nothing there can be
# shadowed by a module here.
for _p in (FOOTBALL, SCRIPTS):
    if _p not in sys.path:
        sys.path.append(_p)

SCHEMA_VERSION = "ml1-v1"
PACKAGE = "mercer_live"

DATA_DIR = os.path.join(ROOT, "data", "mercer_live")
RAW_SUBDIR = "raw"            # gitignored: the per-minute observation stream
DIGEST_SUBDIR = "digest"      # committed: per-day fingerprints of the stream

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# Per-sport configuration. Everything that differs between the two leagues is a
# row here; no function below branches on a sport name. The ESPN path/group
# values and the Odds API sport keys are RESTATED rather than imported so that
# this package has no import-time dependency on scripts/football; the self-test
# asserts they still equal capture_schedule.ESPN_PATH / ESPN_GROUPS and
# fetch_odds.SPORT_KEYS, so a drift is a red test rather than a silent fork.
SPORTS = {
    "nfl": {
        "league": "NFL",
        "odds_sport_key": "americanfootball_nfl",
        "espn_path": "nfl",
        "espn_groups": None,
        # canonical: teams.py franchise keys on both sides of the join.
        "identity": "canonical",
        "periods": 4,
        "period_seconds": 900,
    },
    "ncaaf": {
        "league": "NCAA FBS",
        "odds_sport_key": "americanfootball_ncaaf",
        "espn_path": "college-football",
        "espn_groups": "80",
        # normalised: espn_ncaaf.norm() keys on both sides of the join. No
        # canonical registry exists for ~134 FBS programmes; the normalised
        # display name is the proven join (16/16 on 2026-08-29).
        "identity": "normalised",
        "periods": 4,
        "period_seconds": 900,
    },
}

GAME_STATE_SOURCE = "espn-scoreboard"
MARKET_SOURCE = "the-odds-api"

# Record kinds. Three, not two: an event that came back with NO books is
# evidence about market availability and has no quote row to carry it.
KIND_GAME_STATE = "game_state"
KIND_MARKET_QUOTE = "market_quote"
KIND_MARKET_EVENT = "market_event"
KINDS = (KIND_GAME_STATE, KIND_MARKET_QUOTE, KIND_MARKET_EVENT)

MARKETS = ("h2h", "spreads", "totals")


def now_utc():
    return datetime.now(UTC)


def iso(dt):
    """Aware datetime -> 'YYYY-MM-DDTHH:MM:SSZ'. Refuses naive input."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("iso() refuses a naive datetime; timestamps must be aware")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    """ISO-8601 string -> aware UTC datetime, or None when unparseable/naive.

    Accepts a trailing Z or an explicit offset, with or without seconds (ESPN
    emits '2026-08-21T23:00Z'). A string with no zone information is NOT
    assumed to be UTC: it returns None, and the caller records the field as
    missing rather than guessing.
    """
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if t.endswith("Z") or t.endswith("z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def et_date(dt):
    """The US/Eastern calendar date of an aware datetime, as YYYY-MM-DD."""
    if dt.tzinfo is None:
        raise ValueError("et_date() refuses a naive datetime")
    return dt.astimezone(ET).date().isoformat()


def et_hour(dt):
    if dt.tzinfo is None:
        raise ValueError("et_hour() refuses a naive datetime")
    return dt.astimezone(ET).hour


def slate_week_et(kickoff_utc):
    """The Tuesday on or before kickoff, anchored in US/Eastern, YYYY-MM-DD.

    Same semantics as mercer.slate_week (the editorial record) and deliberately
    NOT market.slate_week (UTC-anchored, frozen for the pregame ledger): a
    Monday 20:15 ET kickoff is Tuesday 00:15 UTC and must stay with the week
    that began on Thursday. No football kicks off around Tuesday 00:00 ET, so
    the boundary sits where neither provider's clock can straddle it.
    """
    if kickoff_utc.tzinfo is None:
        raise ValueError("slate_week_et() refuses a naive datetime")
    d = kickoff_utc.astimezone(ET).date()
    return (d - timedelta(days=(d.weekday() - 1) % 7)).isoformat()


def sha256_hex(*parts):
    """Deterministic identity from ordered string parts. None -> 'null'."""
    h = hashlib.sha256()
    for p in parts:
        h.update(("null" if p is None else str(p)).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def canonical_json(obj):
    """Stable serialisation: sorted keys, no whitespace, ASCII-safe."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sport_cfg(sport):
    if sport not in SPORTS:
        raise KeyError(f"unknown sport {sport!r}; known: {sorted(SPORTS)}")
    return SPORTS[sport]
