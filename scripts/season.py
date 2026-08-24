#!/usr/bin/env python3
"""
Open Ledger Sports — season state (the offseason guard).

WHY THIS EXISTS. fetch_data.py filters the slate to `gameType == "R"`, so the
board goes empty the day after the regular season ends — including right through
the postseason. Before this module the pipeline had no idea that had happened:
it would publish an empty board every morning, the blog would post daily into
nothing, heartbeat.yml would alert every single day for five months, and the
site's staleness banner would tell visitors the board "failed to publish" when
in fact baseball was simply over. That last one is a House Rule 8 violation on a
five-month timer — site copy describing something the code is not doing.

NO HARDCODED DATES. The state is derived from the schedule itself, never from a
calendar constant. docs/FOOTBALL_PREREG.md section 11 makes the same point about
football capture windows: "Any capture window derived from 'it's Sunday' is a
bug." A season-end date hardcoded here would be that bug with a longer fuse — it
would be wrong the first time MLB moved a schedule, and nothing would notice.

THREE STATES, and the distinction between the last two is the whole point:

  active     — regular-season games on today's slate. The happy path. Every
               caller behaves exactly as it did before this module existed.
  break      — no games today, but games within LOOKAHEAD_DAYS. The All-Star
               break, or a schedule quirk. The site says "no games today"; the
               blog runs its evergreen off-day piece, which is what blog.py
               already did. This is a pause, not an ending.
  offseason  — no games today and none on the horizon. The season is over.
               The site switches to a season-complete state, the daily posts
               stop, and the watchdog stops crying wolf.

A break longer than LOOKAHEAD_DAYS reads as offseason, and that is the correct
failure direction: the site says "season complete" a little early rather than
telling visitors a board failed for three weeks. The state is recomputed every
morning, so it corrects itself the moment games reappear.

FRESHNESS IS PART OF THE CONTRACT. data/season_state.json is written on every
morning run, in the clear (it leaks nothing — a date and a word). Consumers must
check `checked_date` before trusting it, because a stale "offseason" would mask
exactly the dead-pipeline failure heartbeat.yml exists to catch. is_stale() is
that check, and heartbeat.yml treats a stale state file as "alert anyway".
"""
import json, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MLB = "https://statsapi.mlb.com/api/v1"
ET = ZoneInfo("America/New_York")

# How far ahead to look before calling it an offseason rather than a break.
# The All-Star break is ~4 days; 21 clears it with room to spare and still
# resolves the end of the regular season within three weeks of the last game.
LOOKAHEAD_DAYS = 21

# How far back to look for the last completed game, for the season-complete copy.
LOOKBACK_DAYS = 14

# A state file older than this is not trusted by the watchdog. Three days
# tolerates a couple of missed morning runs without going blind to a pipeline
# that has actually died.
STATE_MAX_AGE_DAYS = 3

ACTIVE, BREAK, OFFSEASON = "active", "break", "offseason"


def today_et():
    return datetime.now(ET).strftime("%Y-%m-%d")


def _regular_season_dates(get, start, end):
    """Dates in [start, end] carrying at least one regular-season game.

    One API call per window. gameType=R is applied server-side AND re-checked
    here: the parameter is the fast path, the comprehension is the guarantee, so
    a silent API change cannot turn a spring-training slate into a live season.
    """
    resp = get(f"{MLB}/schedule", sportId=1, gameTypes="R",
               startDate=start, endDate=end,
               fields="dates,date,games,gamePk,gameType")
    out = []
    for d in resp.get("dates", []):
        if any(g.get("gameType") == "R" for g in d.get("games", [])):
            out.append(d["date"])
    return sorted(out)


def _regular_season_games_on(get, date):
    """The regular-season games on one date. Used only when the caller did not
    already have the slate; fetch_data.py always does, so this is the offseason
    path and the standalone-tool path, never the daily hot path."""
    resp = get(f"{MLB}/schedule", sportId=1, gameTypes="R", date=date,
               fields="dates,date,games,gamePk,gameType")
    out = []
    for d in resp.get("dates", []):
        out.extend(g for g in d.get("games", []) if g.get("gameType") == "R")
    return out


def classify(date, get, games_today=None):
    """Return the season state for `date` (YYYY-MM-DD, Eastern).

    `games_today` lets fetch_data.py hand over the slate it has already fetched
    so we do not pay for a second identical call on the happy path — which is
    every day of the season. When it is non-empty we return immediately without
    touching the network at all.

    None and [] mean DIFFERENT things and the distinction is load-bearing:
      []   — the caller looked and the slate is empty. Trusted.
      None — the caller has not looked. We look, because assuming "empty" here
             would classify the last day of the regular season as an offseason
             (it has games; the lookahead window after it does not). That is
             exactly what an early version of this function did.
    """
    state = {"checked_date": date,
             "checked_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}

    if games_today is None:
        games_today = _regular_season_games_on(get, date)

    if games_today:
        return {**state, "state": ACTIVE, "games_today": len(games_today)}

    d = datetime.strptime(date, "%Y-%m-%d")
    ahead = _regular_season_dates(
        get, (d + timedelta(days=1)).strftime("%Y-%m-%d"),
        (d + timedelta(days=LOOKAHEAD_DAYS)).strftime("%Y-%m-%d"))
    if ahead:
        return {**state, "state": BREAK, "games_today": 0, "resumes": ahead[0]}

    behind = _regular_season_dates(
        get, (d - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d"),
        (d - timedelta(days=1)).strftime("%Y-%m-%d"))
    return {**state, "state": OFFSEASON, "games_today": 0,
            "last_game_date": behind[-1] if behind else None,
            # Deliberately absent rather than guessed. Next season's schedule is
            # not published for months after the last out, and House Rule 4 says
            # a thing we do not know is recorded as unknown, never inferred. The
            # site copy is written to read correctly with this as None.
            "resumes": None}


# ---------------- store ----------------

def path(root):
    return os.path.join(root, "data", "season_state.json")


def write(root, state):
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(path(root), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
        f.write("\n")
    return state


def read(root):
    """The stored state, or None. Never raises: every consumer has a safe
    default (treat it as active / alert anyway), and a watchdog that dies on a
    malformed file is worse than one that assumes the worst."""
    try:
        with open(path(root), encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) and s.get("state") else None
    except (OSError, ValueError):
        return None


def is_stale(state, today=None, max_age_days=STATE_MAX_AGE_DAYS):
    """True when the state is missing or too old to act on."""
    if not state or not state.get("checked_date"):
        return True
    try:
        checked = datetime.strptime(state["checked_date"], "%Y-%m-%d")
        now = datetime.strptime(today or today_et(), "%Y-%m-%d")
    except ValueError:
        return True
    return (now - checked).days > max_age_days


def is_offseason(state, today=None):
    """True only for a FRESH offseason reading. A stale file is not an
    offseason; it is a pipeline that stopped, and it must not silence
    anything."""
    return bool(state) and state.get("state") == OFFSEASON and not is_stale(state, today)


def is_playing(state):
    """True when there are games today. Unknown state reads as playing, so the
    normal pipeline runs and the existing staleness machinery stays in charge."""
    return not state or state.get("state") == ACTIVE
