#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1: event identity and the cross-source join.

TWO SOURCES, ONE FIXTURE. Game state comes from ESPN and quotes from The Odds
API, and each names the same game its own way. The pregame football pipeline
already solved team identity for both leagues; this module REUSES those
resolvers read-only and builds one CANONICAL EVENT ID from them:

    <sport>:<slate_week_et>:<AWAY_KEY>@<HOME_KEY>

WHY THE ID CARRIES THE SLATE WEEK AND NOT THE KICKOFF INSTANT. On 2026-09-13 the
results provider reported a kickoff at 03:59Z and the odds provider 04:00Z for
the same game, and the pregame grader needed a hand-written adjudication file
to join them (data/football/result_identity/). A live join cannot wait for a
human. Two teams meet at most once per slate week in both leagues, so the
week plus the two team keys identifies the game exactly, and a one-minute
disagreement about kickoff cannot split it. Kickoff agreement is checked
separately, with an hours-wide tolerance, and a disagreement REFUSES the join
rather than picking a side.

IDENTITY MODE PER SPORT (inherited from fetch_odds.py, unchanged):
  nfl    canonical  teams.canonical(espn abbreviation) / teams.from_name(odds name)
                    -> one of 32 franchise keys, or UnknownTeam. Never fuzzy.
  ncaaf  normalised espn_ncaaf.norm(display name) / espn_ncaaf.key_for(odds name)
                    -> deterministic lossy key, exact match required. Never fuzzy.

FALSE JOINS ARE THE FAILURE THAT MATTERS. A quote attached to the wrong game is
a silent corruption that later reads as an edge. So join_status() returns
"joined" only when EXACTLY ONE game-state record carries the canonical id and
its kickoff agrees with the quote's commence_time within KICKOFF_TOLERANCE_H.
Zero, several, or a kickoff mismatch are all named refusals, recorded on the
observation.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402

import teams                                                      # noqa: E402
import espn_ncaaf                                                 # noqa: E402

KICKOFF_TOLERANCE_H = 2.0

JOINED = "joined"
UNJOINED = "unjoined"                    # no game-state record carries the id
AMBIGUOUS = "ambiguous"                  # more than one does
KICKOFF_MISMATCH = "kickoff_mismatch"    # one does, but the clocks disagree by hours
UNRESOLVED = "unresolved"                # a team name could not be given an identity


class IdentityError(ValueError):
    """A team or event could not be identified without guessing."""


def team_key(sport, value, source):
    """Resolve a team reference to this sport's identity key.

    source is one of:
      "espn_abbr"  ESPN team abbreviation (NFL only; college abbreviations are
                   not a stable identity and are refused)
      "espn_name"  ESPN displayName
      "odds_name"  The Odds API home_team / away_team string
    Raises IdentityError rather than returning anything approximate.
    """
    cfg = common.sport_cfg(sport)
    if value is None or not str(value).strip():
        raise IdentityError(f"empty team reference ({source}) for {sport}")
    try:
        if cfg["identity"] == "canonical":
            if source == "espn_abbr":
                return teams.canonical(value, source="espn")
            if source == "odds_name":
                return teams.from_name(value, source="odds-api")
            if source == "espn_name":
                return teams.from_name(value, source="espn")
        elif cfg["identity"] == "normalised":
            if source == "espn_name":
                k = espn_ncaaf.norm(value)
            elif source == "odds_name":
                k = espn_ncaaf.key_for(value)
            elif source == "espn_abbr":
                raise IdentityError("college abbreviations are not an identity; "
                                    "use the display name")
            else:
                k = None
            if not k:
                raise IdentityError(f"name {value!r} normalises to nothing")
            return k
    except teams.UnknownTeam as e:
        raise IdentityError(str(e)) from e
    raise IdentityError(f"unknown identity source {source!r}")


def canonical_event_id(sport, kickoff_utc, away_key, home_key):
    """<sport>:<slate_week_et>:<AWAY>@<HOME>. Every part is required."""
    if kickoff_utc is None:
        raise IdentityError("canonical id needs a kickoff to place the slate week")
    if not away_key or not home_key:
        raise IdentityError("canonical id needs both team keys")
    if away_key == home_key:
        raise IdentityError(f"a team cannot play itself ({away_key})")
    return f"{sport}:{common.slate_week_et(kickoff_utc)}:{away_key}@{home_key}"


def join_status(canonical_id, state_index, commence_time_utc):
    """(status, state_record_or_None) for one quote event against one tick's
    game-state records.

    state_index: {canonical_event_id: [game_state records]} for THE SAME tick.
    Joining across ticks is never done: a quote is joined to the game state
    observed in the same capture execution, or not at all.
    """
    if canonical_id is None:
        return UNRESOLVED, None
    hits = state_index.get(canonical_id) or []
    if not hits:
        return UNJOINED, None
    if len(hits) > 1:
        return AMBIGUOUS, None
    st = hits[0]
    k = common.parse_utc(st.get("scheduled_start"))
    if k is None or commence_time_utc is None:
        # Cannot check agreement; the id matched but a REQUIRED clock is missing
        # on one side. Refuse: an unverifiable join is not a join.
        return KICKOFF_MISMATCH, None
    if abs((k - commence_time_utc).total_seconds()) > KICKOFF_TOLERANCE_H * 3600:
        return KICKOFF_MISMATCH, None
    return JOINED, st


def index_by_canonical(state_records):
    """{canonical_event_id: [records]} — lists, so ambiguity is visible."""
    idx = {}
    for r in state_records:
        cid = r.get("canonical_event_id")
        if cid:
            idx.setdefault(cid, []).append(r)
    return idx
