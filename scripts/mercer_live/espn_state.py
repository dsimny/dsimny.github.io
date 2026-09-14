#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1: live game state from ESPN's scoreboard.

ONE RECORD PER EVENT PER TICK, of kind "game_state". The scoreboard is the
same public, keyless endpoint the pregame pipeline already reads for results
(espn_nfl.py, espn_ncaaf.py) and kickoffs (capture_schedule.py). Those modules
never read the clock, the period or the `situation` block; this one does.

FIELD CLASSES (docs/MERCER_LIVE_V0.1_PREREGISTRATION.md section 7):
  REQUIRED  the record is `complete` only if every one is present. Absence is
            recorded by name in `missing_required`; the record is still stored.
  OPTIONAL  stored as null when absent, named in `missing_optional`.
  FUTURE    not read.
Nothing is defaulted to a plausible value. ESPN reports "0" for a game that has
not been played, so a score is read ONLY when the state is in-progress or
final; before kickoff a score is null, not zero (the same rule espn_slate.py and
espn_nfl.py already apply).

PROVIDER TIMESTAMP. The scoreboard payload carries no "last updated" instant
that this repository has ever read, and none is invented here:
provider_timestamp is null on every game_state record. observed_at is the
moment Open Ledger received the response and is the only clock these records
have.

MALFORMED INPUT FAILS CLOSED PER EVENT. An event that cannot be read without
guessing (no home/away split, unresolvable team, unparseable kickoff) is
skipped and recorded in the tick's error list with its provider id and the
reason. It never becomes a partial record with guessed fields.
"""
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402
import identity                                                   # noqa: E402

SCOREBOARD = ("http://site.api.espn.com/apis/site/v2/sports/football/"
              "{path}/scoreboard")
PAGE_LIMIT = 400

REQUIRED_FIELDS = ("provider_event_id", "home", "away", "scheduled_start",
                   "status_name", "status_state", "completed", "season_year",
                   "season_type")
# Score is REQUIRED only once the game has started; enforced in extract_event.
OPTIONAL_FIELDS = ("period", "clock_seconds", "display_clock", "possession",
                   "down", "distance", "yard_line", "is_red_zone",
                   "home_timeouts", "away_timeouts", "down_distance_text",
                   "possession_text", "last_play")

STATE_PRE, STATE_IN, STATE_POST = "pre", "in", "post"


def scoreboard_url(sport):
    return SCOREBOARD.format(path=common.sport_cfg(sport)["espn_path"])


def fetch_scoreboard(sport, et_date_yyyymmdd, timeout=30, session=None):
    """GET the scoreboard for one ESPN calendar day. Raises on transport or
    HTTP failure; the caller records the failure. Returns the parsed JSON."""
    cfg = common.sport_cfg(sport)
    params = {"dates": et_date_yyyymmdd, "limit": PAGE_LIMIT}
    if cfg["espn_groups"]:
        params["groups"] = cfg["espn_groups"]
    s = session or requests
    r = s.get(scoreboard_url(sport), params=params, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict):
        raise ValueError("scoreboard payload is not a JSON object")
    return body


def _int_or_none(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool_or_none(v):
    return v if isinstance(v, bool) else None


def extract_event(ev, sport, observed_at, run_id):
    """One ESPN event -> one game_state record. Raises ValueError /
    identity.IdentityError rather than guessing."""
    if not isinstance(ev, dict):
        raise ValueError("event is not an object")
    pid = ev.get("id")
    if pid in (None, ""):
        raise ValueError("event has no id")
    pid = str(pid)
    comps = ev.get("competitions") or []
    if not comps or not isinstance(comps[0], dict):
        raise ValueError(f"event {pid} has no competition")
    comp = comps[0]

    sides = {}
    for c in comp.get("competitors") or []:
        ha = c.get("homeAway")
        if ha in ("home", "away"):
            sides[ha] = c
    if set(sides) != {"home", "away"}:
        raise ValueError(f"event {pid} has competitors {sorted(sides)}")

    cfg = common.sport_cfg(sport)

    def key_of(c):
        t = c.get("team") or {}
        if cfg["identity"] == "canonical":
            return identity.team_key(sport, t.get("abbreviation"), "espn_abbr")
        return identity.team_key(sport, t.get("displayName"), "espn_name")

    home_key, away_key = key_of(sides["home"]), key_of(sides["away"])
    kickoff = common.parse_utc(ev.get("date") or comp.get("date"))
    if kickoff is None:
        raise ValueError(f"event {pid} has no parseable kickoff ({ev.get('date')!r})")

    status = comp.get("status") or ev.get("status") or {}
    stype = status.get("type") or {}
    state = stype.get("state")
    if state not in (STATE_PRE, STATE_IN, STATE_POST):
        state = None
    completed = _bool_or_none(stype.get("completed"))
    season = ev.get("season") or {}

    started = state in (STATE_IN, STATE_POST)
    home_score = _int_or_none(sides["home"].get("score")) if started else None
    away_score = _int_or_none(sides["away"].get("score")) if started else None

    sit = comp.get("situation") if isinstance(comp.get("situation"), dict) else {}
    home_tid = str((sides["home"].get("team") or {}).get("id") or "")
    away_tid = str((sides["away"].get("team") or {}).get("id") or "")
    poss_raw = sit.get("possession")
    possession = None
    if poss_raw not in (None, ""):
        poss_raw = str(poss_raw)
        possession = ("home" if poss_raw == home_tid and home_tid
                      else "away" if poss_raw == away_tid and away_tid
                      else None)
    lp = sit.get("lastPlay") if isinstance(sit.get("lastPlay"), dict) else None
    last_play = None
    espn_wp_home = None
    if lp:
        last_play = {
            "id": str(lp.get("id")) if lp.get("id") is not None else None,
            "type": (lp.get("type") or {}).get("text") if isinstance(lp.get("type"), dict) else None,
            "text": lp.get("text"),
            "score_value": _int_or_none(lp.get("scoreValue")),
        }
        prob = lp.get("probability") if isinstance(lp.get("probability"), dict) else {}
        espn_wp_home = _float_or_none(prob.get("homeWinPercentage"))

    rec = {
        "kind": common.KIND_GAME_STATE,
        "schema_version": common.SCHEMA_VERSION,
        "run_id": run_id,
        "observed_at": common.iso(observed_at),
        "source": common.GAME_STATE_SOURCE,
        # ESPN's scoreboard carries no payload/event "last updated" instant
        # that this repository reads; recorded as null, never substituted.
        "provider_timestamp": None,
        "sport": sport,
        "league": cfg["league"],
        "provider_event_id": pid,
        "canonical_event_id": identity.canonical_event_id(sport, kickoff, away_key, home_key),
        "home": home_key, "away": away_key,
        "home_name": (sides["home"].get("team") or {}).get("displayName"),
        "away_name": (sides["away"].get("team") or {}).get("displayName"),
        "home_espn_team_id": home_tid or None,
        "away_espn_team_id": away_tid or None,
        "scheduled_start": common.iso(kickoff),
        "season_year": _int_or_none(season.get("year")),
        "season_type": _int_or_none(season.get("type")),
        "season_slug": season.get("slug"),
        "status_name": stype.get("name"),
        "status_state": state,
        "status_detail": stype.get("shortDetail") or stype.get("detail"),
        "completed": completed,
        "home_score": home_score,
        "away_score": away_score,
        "period": _int_or_none(status.get("period")),
        "clock_seconds": _float_or_none(status.get("clock")),
        "display_clock": status.get("displayClock"),
        "possession": possession,
        "down": _int_or_none(sit.get("down")),
        "distance": _int_or_none(sit.get("distance")),
        "yard_line": _int_or_none(sit.get("yardLine")),
        "is_red_zone": _bool_or_none(sit.get("isRedZone")),
        "home_timeouts": _int_or_none(sit.get("homeTimeouts")),
        "away_timeouts": _int_or_none(sit.get("awayTimeouts")),
        "down_distance_text": sit.get("downDistanceText"),
        "possession_text": sit.get("possessionText"),
        "last_play": last_play,
        # RECORDED, NEVER AN INPUT (preregistration section 7): a third party's
        # model output, kept only so a future evaluation can benchmark against
        # it. Nothing in Mercer Live may read it as a feature.
        "espn_win_probability_home": espn_wp_home,
    }

    missing_required = [f for f in REQUIRED_FIELDS if rec.get(f) is None]
    if started:
        for f in ("home_score", "away_score"):
            if rec.get(f) is None:
                missing_required.append(f)
    missing_optional = [f for f in OPTIONAL_FIELDS if rec.get(f) is None]
    rec["missing_required"] = missing_required
    rec["missing_optional"] = missing_optional
    rec["complete"] = not missing_required
    rec["observation_id"] = common.sha256_hex(
        common.KIND_GAME_STATE, run_id, common.GAME_STATE_SOURCE, sport, pid)
    return rec


def parse_scoreboard(payload, sport, observed_at, run_id):
    """(records, errors). Every event is either a record or a named error."""
    records, errors = [], []
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return [], [{"provider_event_id": None,
                     "reason": "payload has no events list"}]
    for ev in events:
        try:
            records.append(extract_event(ev, sport, observed_at, run_id))
        except (ValueError, KeyError, TypeError, identity.IdentityError) as e:
            errors.append({"provider_event_id": str((ev or {}).get("id")) if isinstance(ev, dict) else None,
                           "reason": str(e)[:300]})
    return records, errors


def classify(records):
    """{state: [records]} for the active-game filter. Unknown state is its own
    bucket so it can never be mistaken for pregame."""
    out = {STATE_PRE: [], STATE_IN: [], STATE_POST: [], "unknown": []}
    for r in records:
        out.get(r.get("status_state") or "unknown", out["unknown"]).append(r)
    return out
