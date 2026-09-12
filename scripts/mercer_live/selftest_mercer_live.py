#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for Mercer Live ML-1, the live observation boundary.

    python scripts/mercer_live/selftest_mercer_live.py

HERMETIC. No network, no credentials, no credits. Every payload is a fixture
built in this file to the provider schemas the package reads; every write
lands in a temp directory that is deleted afterwards. The real repository is
READ (source files, data/football/, data/mercer/, index.html, feed.xml) only
to prove it was not written to.

Numbered to the ML-1 test list in the package brief:

   1  canonical event join           market rows join to game state by the pregame identity
   2  home/away consistency          a swapped pair is refused, never joined
   3  NFL identity                   franchise keys via teams.py; unknown name recorded, not aborted
   4  NCAAF identity                 normalised keys via espn_ncaaf; Hawai'i and the alias join
   5  pregame vs live isolation      phase from OBSERVED game state; nothing reaches data/football
   6  observation timestamps         observed_at is ours, ms precision, Z, never naive
   7  provider timestamps            last_update / Date kept verbatim; quote_age derived
   8  duplicate execution            same run_id twice -> refused before any append
   9  sequential same-value samples  next slot, identical state -> distinct observations
  10  market normalisation           h2h / spreads / totals rows, tiers, unknown markets ignored
  11  spread identity                side and signed point per outcome
  12  total identity                 over / under and the line
  13  moneyline identity             side by the feed's own strings, point null
  14  suspended / missing market     absence is recorded per book, never a fabricated row
  15  missing game-state fields      pregame score null; absent situation; missing_required
  16  stale provider response        old last_update -> large quote_age; observed_at untouched
  17  ended game                     post state kept; odds not called when nothing is live
  18  clock / state parsing          displayClock, float clock, OT, anomalies named not fixed
  19  malformed payload              fail closed: nothing written for that source, exit 1
  20  no Discord side effects        no webhook, no post_discord, no requests.post in the package
  21  no ledger side effects         no ledger path in the package; store refuses their dirs
  22  no pregame board changes       football odds/board/commitments byte-identical after a tick
  23  no generated site artifacts    index.html / feed.xml untouched; raw dir gitignored
  24  the code gate is untouched     package lives outside scripts/football/; its own gate is read-only
  25  budget                         daily cap and reserve refuse a call before it is made
  26  manifest                       seal is append-only and names a hash disagreement
  27  pre-registration               the spec exists, carries a frozen: line, reads NOT YET
"""
import copy
import glob
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HERE = os.path.join(ROOT, "scripts", "mercer_live")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))

import mlcommon as C            # noqa: E402
import gamestate                # noqa: E402
import odds                     # noqa: E402
import store as storemod        # noqa: E402
import budget as budgetmod      # noqa: E402
import capture                  # noqa: E402
import market                   # noqa: E402  (the PREGAME loader, read to prove isolation)
import teams                    # noqa: E402

fails = []
n_checks = 0


def check(cond, msg):
    global n_checks
    n_checks += 1
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def section(title):
    print(f"\n{title}")


# ------------------------------------------------------------ fixtures --

T0 = datetime(2026, 9, 13, 17, 31, 3, 412000, tzinfo=timezone.utc)   # a Sunday, 1:31pm ET
KICK_1PM = "2026-09-13T17:00Z"
KICK_4PM = "2026-09-13T20:25Z"


def espn_event(eid, date, away, home, state="pre", score=(None, None), period=None,
               clock=None, display=None, situation=None, completed=False, stype=2,
               neutral=False, detail=None):
    """(abbr, displayName, espn_team_id) tuples for away/home."""
    def comp(ha, t, sc):
        return {"id": t[2], "homeAway": ha, "score": "0" if sc is None else str(sc),
                "team": {"id": t[2], "abbreviation": t[0], "displayName": t[1]}}
    names = {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL"}
    status = {"clock": clock, "displayClock": display, "period": period,
              "type": {"id": "1", "name": names[state], "state": state,
                       "completed": completed, "description": names[state],
                       "detail": detail or names[state]}}
    c = {"id": eid, "date": date, "neutralSite": neutral,
         "venue": {"fullName": "Some Stadium"},
         "competitors": [comp("home", home, score[1]), comp("away", away, score[0])],
         "status": status}
    if situation is not None:
        c["situation"] = situation
    return {"id": eid, "date": date, "season": {"year": 2026, "type": stype,
                                                "slug": "regular-season" if stype == 2 else "other"},
            "competitions": [c], "status": status}


SIT = {"possession": "23", "down": 2, "distance": 7, "yardLine": 45, "isRedZone": False,
       "homeTimeouts": 3, "awayTimeouts": 2, "downDistanceText": "2nd & 7 at ATL 45",
       "possessionText": "PIT", "lastPlay": {"id": "4016112233", "type": {"text": "Rush"},
                                              "text": "J. Warren rush for 4 yards",
                                              "scoreValue": 0, "team": {"id": "23"}}}

PIT = ("PIT", "Pittsburgh Steelers", "23")
ATL = ("ATL", "Atlanta Falcons", "1")
KC = ("KC", "Kansas City Chiefs", "12")
DEN = ("DEN", "Denver Broncos", "7")
WSH = ("WSH", "Washington Commanders", "28")
NYG = ("NYG", "New York Giants", "19")
LAR = ("LAR", "Los Angeles Rams", "14")
SF = ("SF", "San Francisco 49ers", "25")


def nfl_scoreboard():
    return {"events": [
        # live: ATL @ PIT, 2nd quarter, PIT ball
        espn_event("401", KICK_1PM, ATL, PIT, "in", (7, 14), 2, 754.0, "12:34", SIT),
        # pregame 4:25 kickoff
        espn_event("402", KICK_4PM, DEN, KC, "pre"),
        # final from earlier (a hypothetical early window)
        espn_event("403", "2026-09-13T13:30Z", NYG, WSH, "post", (17, 24), 4, 0.0, "0:00",
                   completed=True),
        # live, LAR @ SF, no situation block sent, clock as display only
        espn_event("404", KICK_1PM, LAR, SF, "in", (3, 0), 1, None, "08:15"),
    ]}


def odds_event(oid, away, home, commence, books):
    return {"id": oid, "sport_key": "americanfootball_nfl", "commence_time": commence,
            "home_team": home, "away_team": away, "bookmakers": books}


def book(key, lu, away, home, h2h=(+215, -265), spread=(6.0, -110, -6.0, -110),
         total=(41.5, -105, -115), markets=("h2h", "spreads", "totals"), extra=None):
    ms = []
    if "h2h" in markets:
        ms.append({"key": "h2h", "last_update": lu,
                   "outcomes": [{"name": away, "price": h2h[0]}, {"name": home, "price": h2h[1]}]})
    if "spreads" in markets:
        ms.append({"key": "spreads", "last_update": lu,
                   "outcomes": [{"name": away, "price": spread[1], "point": spread[0]},
                                {"name": home, "price": spread[3], "point": spread[2]}]})
    if "totals" in markets:
        ms.append({"key": "totals", "last_update": lu,
                   "outcomes": [{"name": "Over", "price": total[1], "point": total[0]},
                                {"name": "Under", "price": total[2], "point": total[0]}]})
    if extra:
        ms.extend(extra)
    return {"key": key, "title": key, "last_update": lu, "markets": ms}


LU = "2026-09-13T17:30:41Z"          # 22 s before T0
OLD = "2026-09-13T17:10:00Z"         # 21 min before T0


def nfl_odds():
    return [
        odds_event("aaa1", "Atlanta Falcons", "Pittsburgh Steelers", "2026-09-13T17:00:00Z", [
            book("draftkings", LU, "Atlanta Falcons", "Pittsburgh Steelers"),
            book("fanduel", LU, "Atlanta Falcons", "Pittsburgh Steelers", h2h=(210, -258)),
            book("bovada", LU, "Atlanta Falcons", "Pittsburgh Steelers"),
            # suspended everything but the moneyline
            book("betmgm", LU, "Atlanta Falcons", "Pittsburgh Steelers", markets=("h2h",)),
            # a book this repo has never classified
            book("newbook_xyz", LU, "Atlanta Falcons", "Pittsburgh Steelers"),
        ]),
        odds_event("aaa2", "Denver Broncos", "Kansas City Chiefs", "2026-09-13T20:25:00Z", [
            book("draftkings", OLD, "Denver Broncos", "Kansas City Chiefs"),
        ]),
        # home/away SWAPPED relative to ESPN (a neutral-site style disagreement)
        odds_event("aaa3", "Washington Commanders", "New York Giants", "2026-09-13T13:30:00Z", [
            book("draftkings", LU, "Washington Commanders", "New York Giants"),
        ]),
        # a team name this repository cannot resolve
        odds_event("aaa4", "Oakland Raiders", "Kansas City Chiefs", "2026-09-13T20:25:00Z", [
            book("draftkings", LU, "Oakland Raiders", "Kansas City Chiefs"),
        ]),
        # the second live game, plus a market family ML-1 does not capture
        odds_event("aaa5", "Los Angeles Rams", "San Francisco 49ers", "2026-09-13T17:00:00Z", [
            book("draftkings", LU, "Los Angeles Rams", "San Francisco 49ers",
                 extra=[{"key": "player_pass_tds", "last_update": LU,
                         "outcomes": [{"name": "Somebody", "price": 150, "point": 1.5}]}]),
        ]),
        # commence in the past and NO game state for it this tick (not on the scoreboard)
        odds_event("aaa6", "Chicago Bears", "Green Bay Packers", "2026-09-13T17:00:00Z", [
            book("draftkings", LU, "Chicago Bears", "Green Bay Packers"),
        ]),
    ]


def opts(**kw):
    base = dict(run_id=None, interval=60, markets="h2h,spreads,totals", regions="us",
                lookahead_min=30, timeout=5, no_odds=False, always_odds=False, dry_run=False)
    base.update(kw)
    return SimpleNamespace(**base)


def write_fixture(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return p


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def run_tick(tmp, league, espn, oddsp, now, **kw):
    os.makedirs(tmp, exist_ok=True)
    st = storemod.ObservationStore(os.path.join(tmp, "raw"))
    bud = budgetmod.Budget(os.path.join(tmp, "raw", "budget.json"))
    fx = {}
    if espn is not None:
        fx["espn"] = write_fixture(tmp, f"espn_{now.strftime('%H%M%S')}_{league}.json", espn) \
            if not isinstance(espn, str) else espn
    if oddsp is not None:
        fx["odds"] = write_fixture(tmp, f"odds_{now.strftime('%H%M%S')}_{league}.json", oddsp) \
            if not isinstance(oddsp, str) else oddsp
    rec = capture.tick(league, opts(**kw), st, bud, now=now, fixtures=fx)
    return rec, st


def rows(st, league, day, kind):
    return list(st.read(league, day, kind))


# Snapshot of everything the package must not touch, taken BEFORE any tick.
PROTECTED = sorted(glob.glob(os.path.join(ROOT, "data", "football", "odds", "*.json"))
                   + glob.glob(os.path.join(ROOT, "data", "football", "*.json"))
                   + glob.glob(os.path.join(ROOT, "data", "mercer", "*.json"))
                   + glob.glob(os.path.join(ROOT, "data", "mercer", "**", "*.json"), recursive=True)
                   + glob.glob(os.path.join(ROOT, "data", "*ledger*.json"))
                   + glob.glob(os.path.join(ROOT, "data", "watchlist.json"))
                   + glob.glob(os.path.join(ROOT, "data", "commitments.json"))
                   + glob.glob(os.path.join(ROOT, "data", "post_status.json"))
                   + [os.path.join(ROOT, "index.html"), os.path.join(ROOT, "feed.xml")])
PROTECTED = [p for p in PROTECTED if os.path.isfile(p)]
BEFORE = {p: (sha_file(p), os.path.getmtime(p)) for p in PROTECTED}
PREGAME_SNAPS_BEFORE = {s: len(market.load_snapshots(s, os.path.join(ROOT, "data", "football", "odds")))
                        for s in ("nfl", "ncaaf")}

TMP = tempfile.mkdtemp(prefix="mercer_live_selftest_")
DAY = C.utc_date(T0)

try:
    # ================================================================== 1
    section("[1] canonical event join")
    rec, st = run_tick(TMP, "nfl", nfl_scoreboard(), nfl_odds(), T0)
    games = rows(st, "nfl", DAY, "game_observations")
    mrows = rows(st, "nfl", DAY, "market_observations")
    cov = rows(st, "nfl", DAY, "market_coverage")
    check(rec["espn"]["ok"] and rec["odds"]["ok"], "a full tick ran from fixtures")
    check(len(games) == 4, f"four game observations written ({len(games)})")
    by_pid = {g["provider_event_id"]: g for g in games}
    atl_pit = [r for r in mrows if r["provider_event_id"] == "aaa1"]
    check(all(r["join_status"] == "joined" for r in atl_pit), "ATL @ PIT market rows joined")
    check(all(r["event_id"] == "nfl:401" for r in atl_pit),
          "event_id is league:espn_event_id, the key both results stores already use")
    check(all(r["gamestate_obs_id"] == by_pid["401"]["obs_id"] for r in atl_pit),
          "every market row points at the game-state observation it was classified against")
    check(by_pid["401"]["home"]["key"] == "PIT" and by_pid["401"]["away"]["key"] == "ATL",
          "game observation carries franchise keys")
    check(atl_pit and atl_pit[0]["away_key"] == "ATL" and atl_pit[0]["home_key"] == "PIT",
          "market row resolved the feed's full names to the same franchise keys")
    check(rec["odds"]["joins"] == {"joined": 3, "home_away_conflict": 1, "identity_error": 1, "unjoined": 1},
          f"the tick summary counts every join outcome ({rec['odds']['joins']})")

    # ================================================================== 2
    section("[2] home/away consistency")
    nyg = [r for r in cov if r["provider_event_id"] == "aaa3"]
    check(nyg and nyg[0]["join_status"] == "home_away_conflict",
          "a pair that matches only with home and away swapped is home_away_conflict")
    check(nyg and nyg[0]["event_id"] is None, "and it is NOT joined to the game")
    j = odds.join("nfl", "NYG", "WAS", C.parse_utc("2026-09-13T13:30:00Z"),
                  gamestate.index(games))
    check(j[2] == "joined" and j[0] == "nfl:403", "the correctly-oriented pair (canonical keys) joins")
    far = odds.join("nfl", "ATL", "PIT", C.parse_utc("2026-09-20T17:00:00Z"), gamestate.index(games))
    check(far[2] == "unjoined", "same teams a week later do not join (kickoff window)")
    dup = gamestate.index(games + [dict(by_pid["401"], obs_id="x", provider_event_id="9",
                                        event_id="nfl:9")])
    amb = odds.join("nfl", "ATL", "PIT", C.parse_utc("2026-09-13T17:00:00Z"), dup)
    check(amb[2] == "ambiguous" and amb[0] is None,
          "two game-state matches -> ambiguous, refused rather than chosen")

    # ================================================================== 3
    section("[3] NFL identity")
    check(by_pid["403"]["home"]["key"] == "WAS" and by_pid["403"]["home"]["abbr"] == "WSH",
          "ESPN's WSH resolves to the canonical WAS, abbr kept verbatim")
    check(by_pid["404"]["away"]["key"] == "LA", "ESPN's LAR resolves to the canonical LA")
    check(all(g["identity_mode"] == "canonical" for g in games), "NFL rows say identity_mode=canonical")
    oak = [r for r in cov if r["provider_event_id"] == "aaa4"]
    check(oak and oak[0]["join_status"] == "identity_error",
          "an unresolvable name is recorded as identity_error")
    oak_rows = [r for r in mrows if r["provider_event_id"] == "aaa4"]
    check(oak_rows and oak_rows[0]["away_key"] is None and "unrecognised" in oak_rows[0]["identity_error"],
          "its market rows are still WRITTEN, with the error text, never dropped and never aborting the tick")
    try:
        gamestate.team_identity("nfl", {"abbreviation": "XXX", "displayName": "Nobody", "id": "0"})
        check(False, "an unknown NFL abbreviation raises")
    except teams.UnknownTeam:
        check(True, "an unknown NFL abbreviation raises UnknownTeam (never a guess)")

    # ================================================================== 4
    section("[4] NCAAF identity")
    HAW = ("HAW", "Hawai'i Rainbow Warriors", "62")
    SJS = ("SJSU", "San José State Spartans", "23")
    SHSU = ("SHSU", "Sam Houston Bearkats", "2534")
    TROY = ("TROY", "Troy Trojans", "2653")
    cfb = {"events": [espn_event("501", KICK_1PM, HAW, SJS, "in", (10, 3), 1, 600.0, "10:00"),
                      espn_event("502", KICK_4PM, SHSU, TROY, "pre")]}
    cfb_odds = [
        odds_event("ccc1", "Hawaii Rainbow Warriors", "San Jose State Spartans", "2026-09-13T17:00:00Z",
                   [book("draftkings", LU, "Hawaii Rainbow Warriors", "San Jose State Spartans")]),
        odds_event("ccc2", "Sam Houston State Bearkats", "Troy Trojans", "2026-09-13T20:25:00Z",
                   [book("draftkings", LU, "Sam Houston State Bearkats", "Troy Trojans")]),
    ]
    rec4, st4 = run_tick(TMP, "ncaaf", cfb, cfb_odds, T0)
    g4 = {g["provider_event_id"]: g for g in rows(st4, "ncaaf", DAY, "game_observations")}
    c4 = {c["provider_event_id"]: c for c in rows(st4, "ncaaf", DAY, "market_coverage")}
    check(g4["501"]["identity_mode"] == "verbatim", "college rows say identity_mode=verbatim")
    check(g4["501"]["home"]["key"] == "sanjosestatespartans",
          "college key is espn_ncaaf.norm of the display name (accent stripped)")
    check(c4["ccc1"]["join_status"] == "joined", "Hawai'i / San José orthography joins through the normaliser")
    check(c4["ccc2"]["join_status"] == "joined", "Sam Houston State -> Sam Houston joins through the alias table")
    check(c4["ccc1"]["phase"] == "live" and c4["ccc2"]["phase"] == "pregame", "phases follow the college game state")

    # ================================================================== 5
    section("[5] pregame vs live quote isolation")
    ph = {c["provider_event_id"]: (c["phase"], c["join_status"]) for c in cov}
    check(ph["aaa1"] == ("live", "joined"), "quote on an in-progress game is phase=live")
    check(ph["aaa2"] == ("pregame", "joined"), "quote on a scheduled game is phase=pregame")
    check(ph["aaa3"][0] in ("post", "unknown"),
          f"quote on a conflicted finished game is never live ({ph['aaa3'][0]})")
    check(ph["aaa5"] == ("live", "joined"), "the second in-progress game's quotes are live too")
    check(ph["aaa6"] == ("unknown", "unjoined"),
          "commence in the past with NO game state this tick is phase=unknown, not live")
    check(all(r["is_live"] == (r["phase"] == "live") for r in mrows), "is_live is true only for phase=live")
    check(all(r["phase_basis"] == "gamestate" for r in mrows if r["join_status"] == "joined"),
          "joined rows derive phase from game state")
    check(all(r["phase_basis"] == "commence_time" for r in mrows if r["join_status"] != "joined"),
          "unjoined rows say their phase came from commence_time only")
    g_pre = odds.phase_for(dict(state="pre"), C.parse_utc("2026-09-13T16:00:00Z"), T0)
    check(g_pre == ("pregame", "gamestate"),
          "game state wins over commence_time: a delayed kickoff is pregame even after its scheduled start")
    check(not any("mercer_live" in f for _t, f, _s in
                  market.load_snapshots("nfl", os.path.join(ROOT, "data", "football", "odds"))),
          "the pregame loader (market.load_snapshots) cannot see anything Mercer Live wrote")
    for bad in ("data/football/odds", "data/football", "data/mercer/odds", "football", "picks"):
        try:
            storemod.ObservationStore(os.path.join(ROOT, bad))
            check(False, f"store refuses {bad}")
        except storemod.RefusedDirectory:
            check(True, f"store refuses to open under {bad}")
    try:
        storemod.ObservationStore(ROOT)
        check(False, "store refuses the repo root")
    except storemod.RefusedDirectory:
        check(True, "store refuses the repository root")
    check(storemod.ObservationStore(os.path.join(TMP, "elsewhere")).raw_dir.endswith("elsewhere"),
          "and accepts an unrelated directory")

    # ================================================================== 6
    section("[6] observation timestamp semantics")
    g = by_pid["401"]
    check(g["observed_at"] == C.iso_ms(T0), "observed_at is the moment WE received the payload")
    check(g["observed_at"].endswith("Z") and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", g["observed_at"]),
          "observed_at is UTC, millisecond precision, Z-suffixed")
    check(all(r["observed_at"] == C.iso_ms(T0) for r in mrows),
          "market rows carry the same Open Ledger observed_at, not a bookmaker time")
    check(atl_pit[0]["observed_at"] != atl_pit[0]["book_last_update"],
          "observed_at was not substituted with last_update")
    try:
        C.iso_ms(datetime(2026, 9, 13, 17, 0))
        check(False, "iso_ms refuses naive datetimes")
    except ValueError:
        check(True, "iso_ms refuses a naive datetime")
    check(C.parse_utc("2026-09-13T17:00:00") is None, "parse_utc refuses a string with no zone")
    check(C.parse_utc("2026-09-13T17:00Z") == datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc),
          "parse_utc reads ESPN's minute-precision form")
    check(C.parse_utc("2026-09-13T13:00:00-04:00") == datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc),
          "parse_utc normalises an offset to UTC")
    check(C.utc_date(datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)) == "2026-09-14",
          "partitions are keyed by UTC date (a Sunday-night game files under Monday UTC)")
    check(rec["run_id"] == "nfl:20260913T173100Z", f"slot run_id floors to the interval ({rec['run_id']})")

    # ================================================================== 7
    section("[7] provider timestamp preservation")
    dk = [r for r in atl_pit if r["book"] == "draftkings" and r["market"] == "h2h"][0]
    check(dk["book_last_update"] == LU and dk["market_last_update"] == LU,
          "bookmaker and market last_update kept verbatim")
    check(abs(dk["quote_age_s"] - 22.412) < 0.001, f"quote_age_s = observed_at - last_update ({dk['quote_age_s']})")
    check(g["provider_ts"] is None, "ESPN sends no server timestamp, so provider_ts is null - not observed_at")
    check("provider_http_date" in g and "provider_http_date" in dk, "the HTTP Date header has a slot on both record kinds")
    fx_bytes = open(os.path.join(TMP, f"espn_{T0.strftime('%H%M%S')}_nfl.json"), "rb").read()
    check(g["raw_sha256"] == hashlib.sha256(fx_bytes).hexdigest(),
          "raw_sha256 is the hash of the exact bytes received")
    pl = os.path.join(st.raw_dir, g["raw_ref"])
    check(os.path.exists(pl) and hashlib.sha256(gzip.open(pl, "rb").read()).hexdigest() == g["raw_sha256"],
          "the raw payload is kept gzipped and hashes to raw_sha256")

    # ================================================================== 8
    section("[8] duplicate execution idempotency")
    n_before = (len(games), len(mrows), len(cov))
    rec8, _ = run_tick(TMP, "nfl", nfl_scoreboard(), nfl_odds(), T0 + timedelta(seconds=20))
    check(rec8.get("skipped") == "duplicate_run", "a re-execution inside the same slot is refused")
    n_after = (len(rows(st, "nfl", DAY, "game_observations")), len(rows(st, "nfl", DAY, "market_observations")),
               len(rows(st, "nfl", DAY, "market_coverage")))
    check(n_after == n_before, f"and nothing was appended ({n_after} == {n_before})")
    check(len(rows(st, "nfl", DAY, "ticks")) == 1, "no tick record for the refused run either")
    rec8b, _ = run_tick(TMP, "nfl", nfl_scoreboard(), nfl_odds(), T0 + timedelta(seconds=20), run_id=rec["run_id"])
    check(rec8b.get("skipped") == "duplicate_run", "an explicit --run-id already written is refused")
    ids = [g["obs_id"] for g in games] + [r["obs_id"] for r in mrows] + [c["obs_id"] for c in cov]
    check(len(ids) == len(set(ids)), "every obs_id in the partition is unique")
    check(C.obs_id("r", "espn", "401") == C.obs_id("r", "espn", "401"), "obs_id is deterministic")

    # ================================================================== 9
    section("[9] sequential same-value observations are distinct observations")
    T1 = T0 + timedelta(seconds=60)
    rec9, _ = run_tick(TMP, "nfl", nfl_scoreboard(), nfl_odds(), T1)
    check(not rec9.get("skipped") and rec9["run_id"] == "nfl:20260913T173200Z", "the next slot is a new run")
    g2 = rows(st, "nfl", DAY, "game_observations")
    check(len(g2) == 8, f"identical game state one minute later is four MORE observations ({len(g2)})")
    a, b = [x for x in g2 if x["provider_event_id"] == "401"]
    check(a["obs_id"] != b["obs_id"] and a["observed_at"] != b["observed_at"],
          "same values, different observation identity and time")
    skip = ("obs_id", "run_id", "observed_at", "raw_ref", "raw_sha256")
    check({k: v for k, v in a.items() if k not in skip} == {k: v for k, v in b.items() if k not in skip},
          "and every observed field is identical - nothing was deduplicated away")
    check(len(rows(st, "nfl", DAY, "ticks")) == 2, "two tick records")
    check(st.runs_seen("nfl", DAY) == {rec["run_id"], rec9["run_id"]}, "runs.txt lists both runs")

    # ================================================================== 10
    section("[10] odds market normalisation")
    kinds = {r["market"] for r in atl_pit}
    check(kinds == {"h2h", "spreads", "totals"}, f"three market families ({sorted(kinds)})")
    check(all(r["record"] == "live_market_observation" and r["schema_version"] == C.SCHEMA_VERSION for r in mrows),
          "every market row is typed and versioned")
    check(not any(r["market"] == "player_pass_tds" for r in mrows),
          "a market outside h2h/spreads/totals is ignored, not stored as a live market")
    tiers = {r["book"]: r["book_tier"] for r in atl_pit}
    check(tiers["draftkings"] == "tier1" and tiers["bovada"] == "tier2" and tiers["newbook_xyz"] == "unclassified",
          f"book tiers come from price_test.TIER1/TIER2 and an unknown book is 'unclassified' ({tiers})")
    check(all(len(r["outcomes"]) == 2 for r in atl_pit), "each row carries both outcomes of its market")
    check(all(r["n_outcomes"] == len(r["outcomes"]) for r in mrows), "n_outcomes agrees")

    # ================================================================== 11
    section("[11] spread side/point identity")
    sp = [r for r in atl_pit if r["book"] == "draftkings" and r["market"] == "spreads"][0]
    sides = {o["side"]: o for o in sp["outcomes"]}
    check(set(sides) == {"home", "away"}, "spread outcomes resolve to home and away")
    check(sides["away"]["point"] == 6.0 and sides["home"]["point"] == -6.0,
          "signed points preserved per side (away +6, home -6)")
    check(sides["away"]["name"] == "Atlanta Falcons", "the feed's own outcome name is kept beside the side")
    check(sides["home"]["price_american"] == -110, "spread price kept")

    # ================================================================== 12
    section("[12] total over/under identity")
    tt = [r for r in atl_pit if r["book"] == "draftkings" and r["market"] == "totals"][0]
    tsides = {o["side"]: o for o in tt["outcomes"]}
    check(set(tsides) == {"over", "under"}, "total outcomes resolve to over and under")
    check(tsides["over"]["point"] == 41.5 == tsides["under"]["point"], "the line is on both outcomes")
    check(tsides["over"]["price_american"] == -105 and tsides["under"]["price_american"] == -115,
          "over and under prices kept separately")
    check(odds.side_for("totals", "Middle", "A", "B") is None, "a nonsense total outcome has no side")

    # ================================================================== 13
    section("[13] moneyline identity")
    ml = [r for r in atl_pit if r["book"] == "draftkings" and r["market"] == "h2h"][0]
    msides = {o["side"]: o for o in ml["outcomes"]}
    check(msides["away"]["price_american"] == 215 and msides["home"]["price_american"] == -265,
          "moneyline prices attach to the right side")
    check(all(o["point"] is None for o in ml["outcomes"]), "moneyline outcomes carry no point")
    check(odds.side_for("h2h", "Someone Else", "Atlanta Falcons", "Pittsburgh Steelers") is None,
          "an outcome name matching neither team has no side (never guessed)")

    # ================================================================== 14
    section("[14] suspended / missing market handling")
    c1 = [c for c in cov if c["provider_event_id"] == "aaa1"][0]
    check("betmgm" in c1["books_quoting"]["h2h"] and "betmgm" not in c1["books_quoting"]["spreads"],
          "a book quoting only the moneyline is listed under h2h only")
    check("betmgm" in c1["books_not_quoting"]["spreads"] and "betmgm" in c1["books_not_quoting"]["totals"],
          "and named under books_not_quoting for the markets it withheld")
    check(not any(r["book"] == "betmgm" and r["market"] in ("spreads", "totals") for r in atl_pit),
          "no spread/total row was fabricated for it")
    check(c1["n_books"] == 5 and len(c1["books_present"]) == 5, "coverage counts every book present")
    empty = odds.observe("nfl", [odds_event("z", "Atlanta Falcons", "Pittsburgh Steelers",
                                            "2026-09-13T17:00:00Z", [])],
                         {"run_id": "r", "observed_at": C.iso_ms(T0)}, {})
    check(empty[0] == [] and empty[1][0]["n_books"] == 0,
          "an event with no bookmakers yields no rows and a zero-book coverage row")

    # ================================================================== 15
    section("[15] missing game-state fields")
    pre = by_pid["402"]
    check(pre["score"] == {"home": None, "away": None}, "pregame score is null, not 0 ('0 is not a score')")
    check(pre["situation"] is None and pre["situation_present"] is False, "no situation block -> null, flagged")
    check(pre["period"] is None and pre["missing_required"] == [], "pregame needs no period or clock to be complete")
    live_nosit = by_pid["404"]
    check(live_nosit["situation"] is None and live_nosit["state"] == "in",
          "an in-progress game ESPN sent no situation for is recorded without one")
    check(live_nosit["clock_seconds"] == 495.0, "clock parsed from displayClock when the float is absent")
    sit = by_pid["401"]["situation"]
    check(sit["possession_key"] == "PIT" and sit["possession_espn_team_id"] == "23",
          "possession resolved from the ESPN team id to the franchise key")
    check(sit["down"] == 2 and sit["distance"] == 7 and sit["yard_line"] == 45 and
          sit["home_timeouts"] == 3 and sit["away_timeouts"] == 2, "down/distance/yard line/timeouts kept")
    check(sit["last_play"]["text"].startswith("J. Warren") and sit["last_play"]["wallclock"] is None,
          "last play text kept; its wall-clock is null because this source has none")
    ev_noclock = espn_event("777", KICK_1PM, ATL, PIT, "in", (0, 0), 1, None, None)
    r15 = gamestate.extract("nfl", ev_noclock, {"run_id": "r", "observed_at": C.iso_ms(T0)})
    check("clock_seconds" in r15["missing_required"] and "in_progress_without_clock" in r15["anomalies"],
          "an in-progress game with no clock is flagged in missing_required and anomalies")

    # ================================================================== 16
    section("[16] stale provider response handling")
    old = [r for r in mrows if r["provider_event_id"] == "aaa2"][0]
    check(abs(old["quote_age_s"] - (21 * 60 + 3.412)) < 0.01,
          f"a 21-minute-old quote reports its age ({old['quote_age_s']})")
    check(old["observed_at"] == C.iso_ms(T0), "and observed_at is still ours, not the stale provider time")
    check(rec["odds"]["quote_age"]["max_s"] > 1200 and rec["odds"]["quote_age"]["median_s"] < 60,
          "the tick summary carries the quote-age distribution")
    check(old["availability"] == "quoted",
          "a stale quote is still recorded as quoted - freshness is a later gate, not a capture filter")

    # ================================================================== 17
    section("[17] ended-game handling")
    fin = by_pid["403"]
    check(fin["state"] == "post" and fin["game_complete"] is True and fin["score"] == {"home": 24, "away": 17},
          "a final keeps its score and completion flag")
    act = rec["espn"]["active"]
    check(act == {"live": 2, "starting_soon": 0, "later": 1, "final": 1, "live_event_ids": ["nfl:401", "nfl:404"]},
          f"active summary counts live/soon/later/final ({act})")
    all_final = {"events": [espn_event("403", "2026-09-13T13:30Z", NYG, WSH, "post", (17, 24), 4, 0.0, "0:00",
                                       completed=True)]}
    rec17, st17 = run_tick(TMP, "nfl", all_final, nfl_odds(), T0 + timedelta(hours=1))
    check(rec17["odds"]["called"] is False and rec17["odds"]["decision"].startswith("no live game"),
          "with every game final the odds endpoint is not called")
    check(rows(st17, "nfl", DAY, "game_observations")[-1]["state"] == "post", "the final observation is still written")
    soon = {"events": [espn_event("402", "2026-09-13T20:25Z", DEN, KC, "pre")]}
    rec17b, _ = run_tick(TMP, "nfl", soon, nfl_odds(), C.parse_utc("2026-09-13T20:10:00Z"))
    check(rec17b["odds"]["called"] is False and "starting within" in rec17b["odds"]["decision"],
          "a game starting in 15 minutes is reported as starting soon but does not trigger a call by itself")

    # ================================================================== 18
    section("[18] clock / state parsing")
    check(gamestate.parse_clock("12:34") == 754.0 and gamestate.parse_clock("0:07") == 7.0, "mm:ss parses")
    check(gamestate.parse_clock("12:60") is None and gamestate.parse_clock("garbage") is None
          and gamestate.parse_clock(None) is None, "invalid clocks parse to None, never to a number")
    check(by_pid["401"]["clock_seconds"] == 754.0 and by_pid["401"]["clock_display"] == "12:34",
          "the float clock is preferred and the display kept")
    bad = espn_event("778", KICK_1PM, ATL, PIT, "in", (0, 0), 0, -5.0, "-0:05")
    r18 = gamestate.extract("nfl", bad, {"run_id": "r", "observed_at": C.iso_ms(T0)})
    check(r18["clock_seconds"] == -5.0 and r18["period"] == 0, "an impossible clock and period are recorded AS RECEIVED")
    check({"negative_clock", "in_progress_without_period"} <= set(r18["anomalies"]),
          f"and named as anomalies ({r18['anomalies']})")
    ot = espn_event("779", KICK_1PM, ATL, PIT, "in", (20, 20), 5, 540.0, "9:00")
    r18b = gamestate.extract("nfl", ot, {"run_id": "r", "observed_at": C.iso_ms(T0)})
    check(r18b["anomalies"] == [], "NFL overtime at 9:00 is within the 10-minute OT period")
    ot2 = espn_event("780", KICK_1PM, ATL, PIT, "in", (20, 20), 5, 840.0, "14:00")
    check("clock_exceeds_period_length" in gamestate.extract("nfl", ot2, {"run_id": "r", "observed_at": C.iso_ms(T0)})["anomalies"],
          "14:00 in NFL overtime is flagged")
    ot3 = espn_event("781", KICK_1PM, HAW, SJS, "in", (20, 20), 5, 840.0, "14:00")
    check("clock_exceeds_period_length" not in gamestate.extract("ncaaf", ot3, {"run_id": "r", "observed_at": C.iso_ms(T0)})["anomalies"],
          "college overtime is untimed, so the same clock is not flagged there")
    weird = copy.deepcopy(espn_event("782", KICK_1PM, ATL, PIT, "in"))
    weird["competitions"][0]["status"]["type"]["state"] = "halftime?"
    try:
        gamestate.extract("nfl", weird, {"run_id": "r", "observed_at": C.iso_ms(T0)})
        check(False, "an unknown status state is refused")
    except C.MalformedPayload:
        check(True, "an unknown status state is refused (MalformedPayload), not mapped to something")

    # ================================================================== 19
    section("[19] malformed payload fail-closed behaviour")
    tmp19 = os.path.join(TMP, "t19")
    os.makedirs(tmp19)
    junk = os.path.join(tmp19, "junk.json")
    open(junk, "wb").write(b"<html>rate limited</html>")
    rec19, st19 = run_tick(tmp19, "nfl", junk, nfl_odds(), T0)
    check(rec19["espn"]["ok"] is False and "not JSON" in rec19["espn"]["error"], "a non-JSON scoreboard fails the source")
    check(not os.path.exists(st19.path_for("nfl", DAY, "game_observations")), "and NO game observations were written")
    check(rec19["odds"]["called"] is False and "cannot classify" in rec19["odds"]["decision"],
          "with no game state the odds endpoint is not called - a quote that cannot be classified is not captured")
    check(os.path.exists(st19.path_for("nfl", DAY, "ticks")) and rows(st19, "nfl", DAY, "ticks")[0]["errors"],
          "the failure itself is recorded in the tick record")
    check(st19.runs_seen("nfl", DAY) == set(), "a tick that wrote no observation does not consume its run slot")
    rec19r, _ = run_tick(tmp19, "nfl", nfl_scoreboard(), nfl_odds(), T0 + timedelta(seconds=15))
    check(not rec19r.get("skipped") and rec19r["run_id"] == rec19["run_id"] and rec19r["espn"]["ok"],
          "so a retry inside the same slot is allowed and succeeds")
    check(len(rows(st19, "nfl", DAY, "ticks")) == 2 and st19.runs_seen("nfl", DAY) == {rec19["run_id"]},
          "both attempts are in the tick log; the run is marked seen only by the one that wrote")
    rec19b, _ = run_tick(os.path.join(TMP, "t19b"), "nfl", {"events": "nope"}, nfl_odds(), T0)
    check(rec19b["espn"]["ok"] is False and "events" in rec19b["espn"]["error"],
          "a scoreboard without an events list fails closed")
    broken = nfl_scoreboard()
    del broken["events"][1]["competitions"][0]["competitors"][0]
    rec19c, _ = run_tick(os.path.join(TMP, "t19c"), "nfl", broken, nfl_odds(), T0)
    check(rec19c["espn"]["ok"] and rec19c["espn"]["n_games"] == 3 and rec19c["espn"]["n_errors"] == 1,
          "one unreadable event is recorded as an error and the other three are kept")
    check(any(e["source"] == "espn-event" and e["provider_event_id"] == "402" for e in rec19c["errors"]),
          "the error names the event")
    rec19d, st19d = run_tick(os.path.join(TMP, "t19d"), "nfl", nfl_scoreboard(), {"not": "a list"}, T0)
    check(rec19d["espn"]["ok"] and rec19d["odds"]["ok"] is False and "not a list" in rec19d["odds"]["error"],
          "a malformed odds payload fails only the odds source")
    check(os.path.exists(st19d.path_for("nfl", DAY, "game_observations")) and
          not os.path.exists(st19d.path_for("nfl", DAY, "market_observations")),
          "game observations were written, market observations were not")
    check(not os.path.exists(os.path.join(st19d.partition("nfl", DAY), "payloads", "nfl_20260913T173100Z.odds.json.gz")),
          "and the bad odds payload was not archived as if it were evidence")
    o = opts(dry_run=True)
    stD = storemod.ObservationStore(os.path.join(TMP, "dry"))
    bD = budgetmod.Budget(os.path.join(TMP, "dry", "budget.json"))
    fx = {"espn": write_fixture(TMP, "dry_espn.json", nfl_scoreboard()),
          "odds": write_fixture(TMP, "dry_odds.json", nfl_odds())}
    recD = capture.tick("nfl", o, stD, bD, now=T0, fixtures=fx)
    check(recD["dry_run"] and not os.path.exists(os.path.join(TMP, "dry", "nfl")) and recD["odds"]["called"] is False,
          "--dry-run writes nothing and calls no odds endpoint")

    # ================================================================== 20
    section("[20] no Discord side effects")
    PKG = sorted(f for f in glob.glob(os.path.join(HERE, "*.py")) if not f.endswith("selftest_mercer_live.py"))
    src = {os.path.basename(f): "\n".join(l for l in io.open(f, encoding="utf-8").read().splitlines()
                                          if not l.lstrip().startswith("#")) for f in PKG}
    joined = "\n".join(src.values())
    code_only = re.sub(r'"""[\s\S]*?"""', "", joined)     # docstrings are not code either
    for banned in ("post_discord", "DISCORD_WEBHOOK", "discord.com", "webhook", "requests.post(",
                   "anthropic", "openai", "send_email", "post_social", "post_instagram"):
        check(banned.lower() not in code_only.lower(), f"package code never mentions {banned}")
    check(len(PKG) == 6, f"the package is six modules ({[os.path.basename(p) for p in PKG]})")

    # ================================================================== 21
    section("[21] no production ledger side effects")
    for banned in ("ledger.json", "football_ledger", "mercer_ledger", "commitments.json", "watchlist.json",
                   "daily_ledger", "totals_ledger", "game_commitments", "board_*", "/board_",
                   "board.py", "post_status"):
        check(banned not in code_only, f"package code never names {banned}")
    check("crypto_box" not in code_only, "the package never touches the encryption layer (it holds no picks)")
    for mod in ("gamestate.py", "odds.py", "mlcommon.py"):
        body = re.sub(r'"""[\s\S]*?"""', "", src[mod])
        check(not re.search(r'open\([^)]*["\'](?:w|a|wb|ab)', body),
              f"{mod} (a parser) never opens a file for writing")
    after = {p: (sha_file(p), os.path.getmtime(p)) for p in PROTECTED}
    changed = [os.path.relpath(p, ROOT) for p in PROTECTED if BEFORE[p] != after[p]]
    check(not changed, f"none of the {len(PROTECTED)} protected ledger/commitment/board files changed ({changed})")

    # ================================================================== 22
    section("[22] no pregame board changes")
    check(PREGAME_SNAPS_BEFORE == {s: len(market.load_snapshots(s, os.path.join(ROOT, "data", "football", "odds")))
                                   for s in ("nfl", "ncaaf")},
          f"the pregame capture count is unchanged ({PREGAME_SNAPS_BEFORE})")
    check(not glob.glob(os.path.join(ROOT, "data", "football", "odds", "*mercer*")),
          "no Mercer Live file sits in data/football/odds")
    check("data/football" not in code_only.replace("FORBIDDEN_WRITE_DIRS", ""),
          "the package never composes a path under data/football (except to forbid it)")
    for banned_import in ("import board", "import grade_football", "import fetch_data", "import fetch_closing",
                          "import capture_schedule", "import mercer\n", "import page"):
        check(banned_import not in code_only, f"package never imports {banned_import.strip()}")

    # ================================================================== 23
    section("[23] no locally generated index.html / feed.xml")
    for name in ("index.html", "feed.xml"):
        p = os.path.join(ROOT, name)
        check(BEFORE[p] == after[p], f"{name} byte-identical and untouched")
        check(name not in code_only, f"package code never names {name}")
    gi = io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().splitlines()
    check("data/mercer_live/raw/" in gi, "data/mercer_live/raw/ is gitignored (raw observations never enter the repo)")
    check(os.path.exists(os.path.join(ROOT, "data", "mercer_live", "manifest.json")),
          "the committed manifest exists at data/mercer_live/manifest.json")
    man = json.load(io.open(os.path.join(ROOT, "data", "mercer_live", "manifest.json"), encoding="utf-8"))
    check("files" in man and "discrepancies" in man, "manifest has its append-only sections")

    # ================================================================== 24
    section("[24] the existing code gate is untouched")
    check(not glob.glob(os.path.join(ROOT, "scripts", "football", "*mercer_live*")),
          "nothing of this package lives under scripts/football/, so football-selftest.yml's trigger set is unchanged")
    gate = io.open(os.path.join(ROOT, ".github", "workflows", "football-selftest.yml"), encoding="utf-8").read()
    check("mercer_live" not in gate,
          "football-selftest.yml does not run this suite (a red run there still means a football code regression)")
    wf_path = os.path.join(ROOT, ".github", "workflows", "mercer-live-selftest.yml")
    check(os.path.exists(wf_path), "this suite has its own read-only gate workflow")
    if os.path.exists(wf_path):
        import yaml
        wf = io.open(wf_path, encoding="utf-8").read()
        doc = yaml.safe_load(wf)
        code = "\n".join(l for l in wf.splitlines() if not l.lstrip().startswith("#"))
        check(doc.get("permissions") == {"contents": "read"}, "gate workflow is contents: read")
        check("secrets." not in code, "gate workflow names no secret")
        for banned in ("git push", "git commit", "git add", "ODDS_API_KEY", "DISCORD", "schedule:"):
            check(banned not in code, f"gate workflow never contains {banned}")
        check("selftest_mercer_live.py" in code, "gate workflow runs this suite")
    fc = io.open(os.path.join(ROOT, ".github", "workflows", "football-capture.yml"), encoding="utf-8").read()
    fg = io.open(os.path.join(ROOT, ".github", "workflows", "football-grade.yml"), encoding="utf-8").read()
    check("mercer_live" not in fc and "mercer_live" not in fg,
          "neither football pipeline workflow invokes Mercer Live - ML-1 is not scheduled on GitHub Actions")

    # ================================================================== 25
    section("[25] budget")
    bp = os.path.join(TMP, "b", "budget.json")
    b = budgetmod.Budget(bp, daily_cap=7, reserve=100)
    check(b.allow(3, T0) == (True, "ok (0+3 <= 7)"), "first call fits under the cap")
    b.state["days"][DAY] = {"credits": 6, "calls": 2}
    ok, why = b.allow(3, T0)
    check(not ok and why.startswith("daily_cap"), f"the daily cap refuses before the call ({why})")
    b2 = budgetmod.Budget(os.path.join(TMP, "b2", "budget.json"), daily_cap=10000, reserve=5000)
    b2.state["last_remaining"] = 5002
    ok, why = b2.allow(3, T0)
    check(not ok and why.startswith("reserve"), f"the reserve floor protects the pregame callers ({why})")
    tmp25 = os.path.join(TMP, "t25")
    os.makedirs(tmp25)
    st25 = storemod.ObservationStore(os.path.join(tmp25, "raw"))
    b25 = budgetmod.Budget(os.path.join(tmp25, "raw", "budget.json"), daily_cap=2)
    fx = {"espn": write_fixture(tmp25, "e.json", nfl_scoreboard()), "odds": write_fixture(tmp25, "o.json", nfl_odds())}
    rec25 = capture.tick("nfl", opts(), st25, b25, now=T0, fixtures=fx)
    check(rec25["odds"]["called"] is False and rec25["odds"]["decision"].startswith("budget: daily_cap"),
          "through the real tick path, a cap below one call's cost stops the call and says why")
    check(rows(st25, "nfl", DAY, "ticks")[0]["odds"]["decision"].startswith("budget"), "the refusal is in the tick record")
    check(odds.call_cost("h2h,spreads,totals", "us") == 3 and odds.call_cost("h2h", "us,uk") == 2, "cost = markets x regions")
    bt = budgetmod.burn_table(60, "h2h,spreads,totals")
    check(bt["per_live_hour"] == 180 and bt["nfl_sunday_10.5h"] == 1890, f"burn table arithmetic ({bt})")

    # ================================================================== 26
    section("[26] manifest seal is append-only")
    mp = os.path.join(TMP, "man", "manifest.json")
    s1 = storemod.seal(st, mp, now=T0 + timedelta(days=1))
    check(len(s1["added"]) >= 6 and s1["disputed"] == [],
          f"first seal records every file of the closed partition ({len(s1['added'])})")
    man1 = json.load(io.open(mp, encoding="utf-8"))
    gk = f"nfl/{DAY}/game_observations.ndjson.gz"
    n_rows_now = len(rows(st, "nfl", DAY, "game_observations"))
    check(man1["files"][gk]["rows"] == n_rows_now and
          man1["files"][gk]["sha256"] == sha_file(st.path_for("nfl", DAY, "game_observations")),
          f"row count ({n_rows_now}) and sha256 recorded per file")
    s2 = storemod.seal(st, mp, now=T0 + timedelta(days=1))
    check(s2["added"] == [] and len(s2["unchanged"]) == len(s1["added"]), "a second seal changes nothing")
    s_today = storemod.seal(st, mp, now=T0)
    check(s_today["added"] == [] and s_today["unchanged"] == [], "a partition still being written today is not sealed")
    with open(st.path_for("nfl", DAY, "game_observations"), "ab") as f:
        f.write(gzip.compress(b'{"record":"forged"}\n'))
    s3 = storemod.seal(st, mp, now=T0 + timedelta(days=2))
    man3 = json.load(io.open(mp, encoding="utf-8"))
    check(s3["disputed"] == [gk], "a sealed file whose bytes changed is reported as disputed")
    check(man3["files"][gk]["sha256"] == man1["files"][gk]["sha256"], "the original entry is NOT rewritten")
    check(man3["discrepancies"] and man3["discrepancies"][0]["file"] == gk, "the discrepancy is appended with both hashes")
    with tempfile.TemporaryDirectory() as clean:
        check(list(storemod.ObservationStore(clean).partitions()) == [], "an empty store has no partitions")

    # ================================================================== 27
    section("[27] pre-registration")
    check(os.path.exists(C.PREREG), "docs/MERCER_LIVE_V0.1_PREREGISTRATION.md exists")
    if os.path.exists(C.PREREG):
        txt = io.open(C.PREREG, encoding="utf-8").read()
        check(re.search(r"^frozen:", txt, re.M) is not None, "it carries a frozen: line, the repo's freeze convention")
        check(C.prereg_frozen() is None,
              "it reads frozen: NOT YET (ML-1 produces observations, not positions; the freeze precedes ML-5)")
        for must in ("100", "shadow", "observed_at", "1-800-GAMBLER", "martingale", "Mode 1", "3.0", "4.0"):
            check(must in txt, f"the spec states its commitment on {must!r}")
        check("model" in txt.lower() and "circuit breaker" in txt.lower(), "the spec names the model and circuit-breaker layers")
    arch = os.path.join(ROOT, "docs", "MERCER_LIVE_ML1_ARCHITECTURE.md")
    check(os.path.exists(arch), "docs/MERCER_LIVE_ML1_ARCHITECTURE.md exists")
    if os.path.exists(arch):
        a = io.open(arch, encoding="utf-8").read()
        for must in ("ML-2", "ML-8", "scheduler", "credit", "observed_at", "gitignored"):
            check(must in a, f"the architecture doc covers {must!r}")

finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nmercer-live selftest: {n_checks} checks, "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
