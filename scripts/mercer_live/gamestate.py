#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1, live game-state observations.

SOURCE: ESPN's public scoreboard (site.api.espn.com), no key, no credits — the
same endpoint scripts/football/espn_nfl.py, espn_ncaaf.py and
capture_schedule.py already read for kickoffs and finals. ML-1 reads the SAME
events and keeps the in-play fields those modules discard: period, clock,
score while in progress, and the `situation` block (possession, down,
distance, field position, timeouts, last play).

IDENTITY IS BORROWED, NOT REINVENTED. NFL teams resolve through
scripts/football/teams.canonical() to the franchise key every football file
uses; NCAA FBS teams get espn_ncaaf.norm() of the display name, the same
normalised key grade_football.py joins prices to results with. So a Mercer Live
event is addressable by exactly the identity the pregame system already trusts,
and `event_id` is the league plus ESPN's own event id — the key both results
stores are already indexed by.

FIELD CLASSIFICATION (pre-registration section 7). Each field below is tagged:

  REQUIRED  — an observation missing it is still written, but is flagged in
              `missing_required`, so completeness is measurable per tick.
  OPTIONAL  — recorded when present, null when not; never inferred.
  FUTURE    — named so the schema has a place for it; NOT read in ML-1
              because this endpoint has not been shown to supply it reliably.

  REQUIRED: league, event_id, provider_event_id, home, away, scheduled_start,
            status.state, status.name, score (null pregame), period, clock,
            game_complete, observed_at
  OPTIONAL: situation.* (possession, down, distance, yard_line, is_red_zone,
            timeouts, down_distance_text, possession_text, last_play), season,
            neutral_site, venue
  FUTURE:   play_state / drive_state as structured objects, most-recent-play
            wall-clock timestamp (the scoreboard's lastPlay carries none;
            ESPN's play-by-play endpoint does), win probability (ESPN's own —
            an external model output, deliberately NOT captured in ML-1 so the
            shadow ledger cannot lean on it by accident)

"0 IS NOT A SCORE." ESPN reports "0" for an unplayed game. A game in state
`pre` has score null, mirroring espn_nfl.py; in `in` and `post` the score is
what ESPN reports, as an int.

NOTHING IS CORRECTED. An impossible clock, a period of 0 while in progress, a
score that went down between ticks — all are recorded exactly as received and
named in `anomalies`. A later package decides what to do with a suspect
observation; ML-1's job is to have written down what was actually served.

VERIFICATION STATUS: the field paths below follow ESPN's long-stable scoreboard
schema (the same shape the four existing football modules read for their
subset). The session that wrote this could not reach site.api.espn.com, so the
`situation` sub-schema is asserted from documentation and existing code, not
from a payload fetched today. `capture.py probe` saves a real payload so that
can be checked in one command; see docs/MERCER_LIVE_ML1_ARCHITECTURE.md.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "football"))

import mlcommon as C                                  # noqa: E402
import teams                                          # noqa: E402
import espn_ncaaf                                     # noqa: E402

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/{path}/scoreboard"

LEAGUES = {
    "nfl":   {"espn_path": "nfl", "groups": None, "identity": "canonical",
              "quarter_seconds": 900, "ot_seconds": 600},
    "ncaaf": {"espn_path": "college-football", "groups": "80", "identity": "verbatim",
              "quarter_seconds": 900, "ot_seconds": None},   # college OT is untimed
}
PAGE_LIMIT = 400

REQUIRED_FIELDS = ("league", "event_id", "provider_event_id", "home", "away",
                   "scheduled_start", "state", "status_name", "period", "clock_seconds",
                   "game_complete")


def scoreboard_url(league):
    return SCOREBOARD.format(path=LEAGUES[league]["espn_path"])


def scoreboard_params(league, dates=None):
    p = {"limit": PAGE_LIMIT}
    if LEAGUES[league]["groups"]:
        p["groups"] = LEAGUES[league]["groups"]
    if dates:
        p["dates"] = dates
    return p


def fetch(league, dates=None, timeout=20):
    """One GET. Returns (body_bytes, meta). Raises requests exceptions to the
    caller, which records the failure and writes nothing."""
    import requests
    sent = C.now_utc()
    r = requests.get(scoreboard_url(league), params=scoreboard_params(league, dates),
                     timeout=timeout)
    received = C.now_utc()
    meta = {"provider": "espn", "url": r.url, "http_status": r.status_code,
            "provider_http_date": r.headers.get("Date"),
            "request_sent_at": C.iso_ms(sent), "response_received_at": C.iso_ms(received)}
    r.raise_for_status()
    return r.content, meta


# ------------------------------------------------------------- parsing --

def parse_clock(display):
    """'12:34' -> 754.0 ; '0:07' -> 7.0 ; garbage -> None. Never guesses."""
    if display is None:
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?:\.(\d+))?\s*", str(display))
    if not m:
        return None
    mm, ss, frac = int(m.group(1)), int(m.group(2)), m.group(3)
    if ss >= 60:
        return None
    return float(mm * 60 + ss) + (float("0." + frac) if frac else 0.0)


def team_identity(league, team):
    """ESPN team object -> {key, name, abbr, espn_team_id, identity_mode}.

    NFL: teams.canonical raises UnknownTeam on anything unrecognised and that
    is allowed to propagate — a franchise mapped to the wrong key is exactly
    the silent corruption the football package refuses everywhere.
    NCAA FBS: no canonical registry exists (see fetch_odds.IDENTITY), so the
    key is the deterministic normalised display name and the record says so.
    """
    mode = LEAGUES[league]["identity"]
    name = team.get("displayName")
    abbr = team.get("abbreviation")
    if mode == "canonical":
        key = teams.canonical(abbr, source="espn")
    else:
        if not name:
            raise C.MalformedPayload("college team with no displayName")
        key = espn_ncaaf.norm(name)
    return {"key": key, "name": name, "abbr": abbr,
            "espn_team_id": str(team.get("id")) if team.get("id") is not None else None,
            "identity_mode": mode}


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _situation(comp, sides):
    """The in-play block, or None when ESPN did not send one. OPTIONAL."""
    sit = comp.get("situation")
    if not isinstance(sit, dict):
        return None
    poss = sit.get("possession")
    poss_key = None
    if poss is not None:
        for s in ("home", "away"):
            if sides[s]["espn_team_id"] == str(poss):
                poss_key = sides[s]["key"]
    lp = sit.get("lastPlay") if isinstance(sit.get("lastPlay"), dict) else None
    last_play = None
    if lp:
        last_play = {
            "id": str(lp.get("id")) if lp.get("id") is not None else None,
            "type": ((lp.get("type") or {}).get("text") if isinstance(lp.get("type"), dict)
                     else lp.get("type")),
            "text": lp.get("text"),
            "score_value": _int_or_none(lp.get("scoreValue")),
            "team_espn_id": (str((lp.get("team") or {}).get("id"))
                             if isinstance(lp.get("team"), dict) and (lp.get("team") or {}).get("id") is not None
                             else None),
            # FUTURE: the scoreboard's lastPlay has no wall-clock stamp. Recorded
            # as null rather than filled with observed_at, which would claim a
            # precision this source does not have.
            "wallclock": None,
        }
    return {
        "possession_espn_team_id": str(poss) if poss is not None else None,
        "possession_key": poss_key,
        "down": _int_or_none(sit.get("down")),
        "distance": _int_or_none(sit.get("distance")),
        "yard_line": _int_or_none(sit.get("yardLine")),
        "is_red_zone": sit.get("isRedZone") if isinstance(sit.get("isRedZone"), bool) else None,
        "home_timeouts": _int_or_none(sit.get("homeTimeouts")),
        "away_timeouts": _int_or_none(sit.get("awayTimeouts")),
        "down_distance_text": sit.get("downDistanceText"),
        "possession_text": sit.get("possessionText"),
        "last_play": last_play,
    }


def anomalies_for(league, rec):
    """Impossible or suspicious states, NAMED, never repaired."""
    out = []
    cfg = LEAGUES[league]
    st, per, clk = rec["state"], rec["period"], rec["clock_seconds"]
    if st == "in" and (per is None or per < 1):
        out.append("in_progress_without_period")
    if clk is not None:
        if clk < 0:
            out.append("negative_clock")
        limit = cfg["quarter_seconds"]
        if per is not None and per > 4 and cfg["ot_seconds"]:
            limit = cfg["ot_seconds"]
        if clk > limit:
            out.append("clock_exceeds_period_length")
    if st == "in" and clk is None and rec["clock_display"] is None:
        out.append("in_progress_without_clock")
    if st == "post" and not rec["game_complete"]:
        out.append("post_state_not_completed")
    if st in ("in", "post") and (rec["score"]["home"] is None or rec["score"]["away"] is None):
        out.append("started_without_score")
    if st == "pre" and rec["situation"] is not None:
        out.append("situation_block_before_kickoff")
    return out


def extract(league, ev, ctx):
    """One ESPN event -> one live_game_observation. Raises MalformedPayload or
    teams.UnknownTeam rather than guessing."""
    try:
        comp = ev["competitions"][0]
        competitors = comp["competitors"]
        status = comp.get("status") or ev["status"]
        stype = status["type"]
    except (KeyError, IndexError, TypeError) as e:
        raise C.MalformedPayload(f"event {ev.get('id') if isinstance(ev, dict) else '?'}: "
                                 f"missing {e!r}")
    sides = {}
    for c in competitors:
        ha = c.get("homeAway")
        if ha in ("home", "away") and isinstance(c.get("team"), dict):
            sides[ha] = dict(team_identity(league, c["team"]), score_raw=c.get("score"))
    if set(sides) != {"home", "away"}:
        raise C.MalformedPayload(f"event {ev.get('id')}: competitors are {sorted(sides)}, "
                                 f"need home and away")

    state = stype.get("state")           # pre | in | post
    if state not in ("pre", "in", "post"):
        raise C.MalformedPayload(f"event {ev.get('id')}: unknown status state {state!r}")
    completed = bool(stype.get("completed"))
    scheduled = C.parse_utc(ev.get("date") or comp.get("date"))
    if scheduled is None:
        raise C.MalformedPayload(f"event {ev.get('id')}: no parseable scheduled start")

    def score(side):
        if state == "pre":
            return None                 # "0 is not a score"
        return _int_or_none(sides[side]["score_raw"])

    period = _int_or_none(status.get("period"))
    clock = status.get("clock")
    clock_s = float(clock) if isinstance(clock, (int, float)) else parse_clock(status.get("displayClock"))
    season = ev.get("season") if isinstance(ev.get("season"), dict) else {}
    pid = str(ev.get("id"))
    rec = {
        "record": "live_game_observation",
        "schema_version": C.SCHEMA_VERSION,
        "obs_id": C.obs_id(ctx["run_id"], "espn", pid),
        "run_id": ctx["run_id"],
        "observed_at": ctx["observed_at"],
        "request_sent_at": ctx.get("request_sent_at"),
        "provider": "espn",
        "provider_http_date": ctx.get("provider_http_date"),
        "provider_ts": None,             # ESPN's scoreboard carries no server timestamp
        "raw_ref": ctx.get("raw_ref"),
        "raw_sha256": ctx.get("raw_sha256"),
        "sport": "football",
        "league": league,
        "event_id": f"{league}:{pid}",
        "provider_event_id": pid,
        "season": {"year": season.get("year"), "type": season.get("type"),
                   "slug": season.get("slug")},
        "home": {k: sides["home"][k] for k in ("key", "name", "abbr", "espn_team_id")},
        "away": {k: sides["away"][k] for k in ("key", "name", "abbr", "espn_team_id")},
        "identity_mode": LEAGUES[league]["identity"],
        "scheduled_start": C.iso_s(scheduled),
        "neutral_site": comp.get("neutralSite") if isinstance(comp.get("neutralSite"), bool) else None,
        "venue": ((comp.get("venue") or {}).get("fullName")
                  if isinstance(comp.get("venue"), dict) else None),
        "state": state,
        "status_name": stype.get("name"),
        "status_detail": stype.get("detail") or stype.get("shortDetail"),
        "game_complete": completed,
        "score": {"home": score("home"), "away": score("away")},
        "period": period,
        "clock_seconds": clock_s,
        "clock_display": status.get("displayClock"),
        "situation": _situation(comp, sides),
    }
    rec["situation_present"] = rec["situation"] is not None
    rec["missing_required"] = [f for f in REQUIRED_FIELDS if rec.get(f) is None
                               and not (f == "period" and state == "pre")
                               and not (f == "clock_seconds" and state != "in")]
    rec["anomalies"] = anomalies_for(league, rec)
    return rec


def observe(league, payload, ctx):
    """Whole scoreboard payload -> (records, errors). Structural failure of the
    payload itself raises MalformedPayload (nothing is written); a single
    unreadable event is recorded in `errors` and the rest are kept."""
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise C.MalformedPayload("scoreboard payload has no events list")
    out, errors = [], []
    for ev in payload["events"]:
        try:
            out.append(extract(league, ev, ctx))
        except (C.MalformedPayload, teams.UnknownTeam) as e:
            errors.append({"provider_event_id": str(ev.get("id")) if isinstance(ev, dict) else None,
                           "error": str(e)})
    return out, errors


def index(records):
    """{(away_key, home_key): [records]} for the join in odds.py."""
    idx = {}
    for r in records:
        idx.setdefault((r["away"]["key"], r["home"]["key"]), []).append(r)
    return idx


def active_summary(records, now, lookahead_s):
    """What the scheduler needs: how many games are live, how many kick off
    within `lookahead_s`, how many are done."""
    live = [r for r in records if r["state"] == "in"]
    soon, later, done = [], [], []
    for r in records:
        if r["state"] == "post":
            done.append(r)
        elif r["state"] == "pre":
            k = C.parse_utc(r["scheduled_start"])
            if k and 0 <= (k - now).total_seconds() <= lookahead_s:
                soon.append(r)
            else:
                later.append(r)
    return {"live": len(live), "starting_soon": len(soon), "later": len(later),
            "final": len(done), "live_event_ids": [r["event_id"] for r in live]}
