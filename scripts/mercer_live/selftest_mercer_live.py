#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1 self-test.

    python scripts/mercer_live/selftest_mercer_live.py

HERMETIC. No network (requests.get/post are replaced with functions that FAIL
the suite if anything calls them), no credentials, no write outside a temporary
directory. Reads the repository only for contract checks: the two Mercer Live
workflows, the gitignore rule, and the football constants this package must
not drift from.

GROUPS ARE NUMBERED TO THE 24 TESTS docs/MERCER_LIVE_ML1_ARCHITECTURE.md
REQUIRES, in the brief's order, then the repository contracts (25+). Each
group has at least one NEGATIVE control where a guard exists, because a guard
that has never been seen to bite is a guard nobody can trust.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402
import credits                                                    # noqa: E402
import capture                                                    # noqa: E402
import espn_state                                                 # noqa: E402
import identity                                                   # noqa: E402
import odds_quotes                                                # noqa: E402
from store import Store, StoreError                               # noqa: E402

fails, n_checks = [], 0


def check(cond, msg):
    global n_checks
    n_checks += 1
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def raises(fn, exc, msg):
    try:
        fn()
    except exc:
        check(True, msg)
    except Exception as e:                                          # noqa: BLE001
        check(False, f"{msg} (raised {type(e).__name__}: {e})")
    else:
        check(False, f"{msg} (did not raise)")


# --- network guard -----------------------------------------------------------
NET = []


def _no_net(*a, **k):
    NET.append((a, k))
    raise AssertionError("network call attempted during the hermetic suite")


requests.get = _no_net
requests.post = _no_net

# --- fixtures -------------------------------------------------------------------
UTC = timezone.utc
NOW = datetime(2026, 9, 13, 18, 37, 23, tzinfo=UTC)          # Sunday 14:37 ET


def team(abbr, name, tid):
    return {"id": tid, "abbreviation": abbr, "displayName": name}


def espn_event(eid, date, home, away, state, hs="0", as_="0", situation=None,
               period=None, clock=None, season_type=2, completed=None):
    st = {"type": {"name": {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS",
                            "post": "STATUS_FINAL"}.get(state, "STATUS_WEIRD"),
                   "state": state,
                   "completed": (state == "post") if completed is None else completed,
                   "shortDetail": "x"}}
    if period is not None:
        st["period"] = period
    if clock is not None:
        st["clock"], st["displayClock"] = clock, f"{int(clock // 60)}:{int(clock % 60):02d}"
    comp = {"competitors": [{"homeAway": "home", "team": home, "score": hs},
                            {"homeAway": "away", "team": away, "score": as_}],
            "status": st}
    if situation is not None:
        comp["situation"] = situation
    return {"id": eid, "date": date, "season": {"year": 2026, "type": season_type,
                                                 "slug": "regular-season"},
            "competitions": [comp]}


JAX, CLE = team("JAX", "Jacksonville Jaguars", "30"), team("CLE", "Cleveland Browns", "5")
LAC, ARI = team("LAC", "Los Angeles Chargers", "24"), team("ARI", "Arizona Cardinals", "22")
DEN, KC = team("DEN", "Denver Broncos", "7"), team("KC", "Kansas City Chiefs", "12")
LAR, WSH = team("LAR", "Los Angeles Rams", "14"), team("WSH", "Washington Commanders", "28")

SIT = {"down": 2, "distance": 7, "yardLine": 45, "possession": "5", "isRedZone": False,
       "homeTimeouts": 3, "awayTimeouts": 2, "downDistanceText": "2nd & 7 at CLE 45",
       "possessionText": "CLE", "lastPlay": {"id": "9", "type": {"text": "Rush"},
                                              "text": "run for 3", "scoreValue": 0,
                                              "probability": {"homeWinPercentage": 0.61}}}


def nfl_espn(sport, d):
    return {"events": [
        espn_event("401", "2026-09-13T17:03Z", JAX, CLE, "in", "10", "7", SIT, 2, 420.0),
        espn_event("402", "2026-09-13T20:25Z", LAC, ARI, "pre"),
        espn_event("403", "2026-09-13T17:00Z", LAR, WSH, "post", "24", "20", None, 4, 0.0),
        espn_event("404", "2026-09-15T00:15Z", KC, DEN, "pre"),
    ]}


def book(key, lu, markets):
    return {"key": key, "last_update": lu, "markets": markets}


def h2h(home, away, hp, ap, lu=None):
    m = {"key": "h2h", "outcomes": [{"name": home, "price": hp}, {"name": away, "price": ap}]}
    if lu:
        m["last_update"] = lu
    return m


def spreads(home, away, pt, lu=None):
    m = {"key": "spreads", "outcomes": [{"name": home, "price": -110, "point": pt},
                                        {"name": away, "price": -110, "point": -pt}]}
    if lu:
        m["last_update"] = lu
    return m


def totals(pt, lu=None, op=-110, up=-110):
    m = {"key": "totals", "outcomes": [{"name": "Over", "price": op, "point": pt},
                                       {"name": "Under", "price": up, "point": pt}]}
    if lu:
        m["last_update"] = lu
    return m


LU = "2026-09-13T18:36:50Z"          # 33 s before NOW


def nfl_odds_body():
    j, c = "Jacksonville Jaguars", "Cleveland Browns"
    return [
        {"id": "e1", "commence_time": "2026-09-13T17:03:55Z", "home_team": j, "away_team": c,
         "bookmakers": [book("draftkings", LU, [h2h(j, c, -150, 130, LU), spreads(j, c, -3.0, LU),
                                                totals(44.5, "2026-09-13T18:36:40Z")]),
                        book("fanduel", "2026-09-13T18:20:00Z", [h2h(j, c, -145, 125)]),
                        book("betmgm", LU, [totals(45.0, LU)])]},
        # in the lead window, pregame: kept
        {"id": "e2", "commence_time": "2026-09-13T18:50:00Z", "home_team": "Los Angeles Chargers",
         "away_team": "Arizona Cardinals", "bookmakers": []},
        # next week, pregame: dropped from the store
        {"id": "e3", "commence_time": "2026-09-20T17:00:00Z", "home_team": "Denver Broncos",
         "away_team": "Kansas City Chiefs",
         "bookmakers": [book("fanduel", LU, [h2h("Denver Broncos", "Kansas City Chiefs", -120, 100)])]},
        # commenced, unresolvable team: event row only
        {"id": "e4", "commence_time": "2026-09-13T17:00:00Z", "home_team": "Mars Rovers",
         "away_team": "Cleveland Browns", "bookmakers": []},
    ]


class FakeOdds:
    def __init__(self, body=None, status=200, remaining=99000, cost=3, raise_exc=None):
        self.body = nfl_odds_body() if body is None else body
        self.status, self.remaining, self.cost, self.raise_exc = status, remaining, cost, raise_exc
        self.calls = []

    def __call__(self, sport, key, markets, regions, commence_from=None, commence_to=None):
        self.calls.append({"sport": sport, "markets": markets, "regions": regions,
                           "from": commence_from, "to": commence_to, "key": key})
        if self.raise_exc:
            raise self.raise_exc
        hdr = {"x-requests-remaining": str(self.remaining), "x-requests-used": "1000",
               "x-requests-last": str(self.cost)}
        return odds_quotes.OddsResponse(self.status, hdr, self.body, NOW, NOW)


def fresh_store():
    tmp = tempfile.mkdtemp(prefix="mlive")
    clog = os.path.join(tmp, "odds_credits.json")
    with io.open(clog, "w", encoding="utf-8") as f:
        json.dump({"readings": [{"remaining": 99000, "used": 1000, "last_call_cost": 1,
                                 "source": "fetch_closing", "read_utc": "2026-09-13T16:30:00Z",
                                 "http_status": 200}]}, f)
    return tmp, Store(tmp), clog


BOOKED = []


def fake_book(reading, sport, path, now):
    BOOKED.append((sport, reading))
    return True, "fake"


def tick(store, clog, run_id=None, odds=None, espn=nfl_espn, now=NOW, sports=("nfl",),
         api_key="k", **kw):
    return capture.run_tick(list(sports), store=store, now_fn=lambda: now, run_id=run_id,
                            api_key=api_key, credit_log=clog, espn_fetch=espn,
                            odds_fetch=odds or FakeOdds(), book_fn=fake_book, **kw)


def read_shard(tmp, rel):
    p = os.path.join(tmp, rel)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]


def snapshot_tree(path):
    out = {}
    for r, _d, fs in os.walk(path):
        for f in fs:
            p = os.path.join(r, f)
            try:
                st = os.stat(p)
                out[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return out


# =============================================================================
print("[1] canonical event join")
gs_recs, gs_errs = espn_state.parse_scoreboard(nfl_espn("nfl", "20260913"), "nfl", NOW, "r1")
idx = identity.index_by_canonical(gs_recs)
cid = identity.canonical_event_id("nfl", common.parse_utc("2026-09-13T17:03:55Z"), "CLE", "JAX")
check(cid == "nfl:2026-09-08:CLE@JAX", f"canonical id is sport:slate_week_et:AWAY@HOME ({cid})")
st, rec = identity.join_status(cid, idx, common.parse_utc("2026-09-13T17:03:55Z"))
check(st == identity.JOINED and rec["provider_event_id"] == "401",
      "a quote event joins exactly one game-state record by canonical id")
check(identity.canonical_event_id("nfl", common.parse_utc("2026-09-13T03:59:00Z"), "A", "B")
      == identity.canonical_event_id("nfl", common.parse_utc("2026-09-13T04:00:00Z"), "A", "B"),
      "the 03:59Z / 04:00Z kickoff disagreement (2026-09-13 incident) yields ONE id")
check(identity.join_status("nfl:2026-09-08:NOPE@JAX", idx, NOW)[0] == identity.UNJOINED,
      "an id no game-state record carries is unjoined, not guessed")
dup_idx = {cid: [gs_recs[0], dict(gs_recs[0], provider_event_id="999")]}
check(identity.join_status(cid, dup_idx, common.parse_utc("2026-09-13T17:03:55Z"))[0]
      == identity.AMBIGUOUS, "two game-state records under one id is AMBIGUOUS, never a pick")
check(identity.join_status(cid, idx, common.parse_utc("2026-09-13T22:00:00Z"))[0]
      == identity.KICKOFF_MISMATCH, "a 5-hour kickoff disagreement refuses the join")
check(identity.join_status(cid, idx, common.parse_utc("2026-09-13T17:00:00Z"))[0]
      == identity.JOINED, "a 4-minute disagreement inside the 2h tolerance joins")
check(identity.join_status(cid, idx, None)[0] == identity.KICKOFF_MISMATCH,
      "a quote with no commence_time cannot verify kickoff and is refused")
check(identity.join_status(None, idx, NOW)[0] == identity.UNRESOLVED,
      "an unresolved identity is its own refusal")

print("\n[2] home/away consistency")
q, ev, _ = odds_quotes.parse_odds(nfl_odds_body(), "nfl", NOW, "r1", idx, "h2h,spreads,totals")
e1 = [e for e in ev if e["provider_event_id"] == "e1"][0]
check(e1["home"] == "JAX" and e1["away"] == "CLE", "odds event resolves home/away to franchise keys")
check(rec["home"] == "JAX" and rec["away"] == "CLE", "ESPN event resolves the same keys")
check(e1["canonical_event_id"] == rec["canonical_event_id"], "both sides produce the identical id")
swapped = identity.canonical_event_id("nfl", NOW, "JAX", "CLE")
check(swapped != cid, "swapping home and away changes the id (AWAY@HOME is ordered)")
raises(lambda: identity.canonical_event_id("nfl", NOW, "JAX", "JAX"), identity.IdentityError,
       "a team cannot play itself")
h2h_q = [x for x in q if x["provider_event_id"] == "e1" and x["market"] == "h2h"
         and x["book"] == "draftkings"]
check({x["outcome"]: x["price"] for x in h2h_q} == {"JAX": -150, "CLE": 130},
      "h2h outcomes land on the right side by exact name match")

print("\n[3] NFL identity")
check(identity.team_key("nfl", "LAR", "espn_abbr") == "LA" and
      identity.team_key("nfl", "WSH", "espn_abbr") == "WAS", "ESPN spellings map to franchise keys")
check(identity.team_key("nfl", "Kansas City Chiefs", "odds_name") == "KC", "Odds API full name resolves")
raises(lambda: identity.team_key("nfl", "Kansas Cty Chiefs", "odds_name"), identity.IdentityError,
       "a one-letter typo is refused, never fuzzy-matched")
raises(lambda: identity.team_key("nfl", "XXX", "espn_abbr"), identity.IdentityError,
       "an unknown abbreviation is refused")
raises(lambda: identity.team_key("nfl", "", "espn_abbr"), identity.IdentityError, "empty is refused")
check(any(e["provider_event_id"] == "e4" and e["identity_status"] == "unresolved" for e in ev),
      "an unresolvable odds event is stored as an event row marked unresolved")
check(not any(x["provider_event_id"] == "e4" for x in q),
      "and carries NO quote rows (no identity, no quotes)")

print("\n[4] NCAAF identity")
check(identity.team_key("ncaaf", "Hawai'i Rainbow Warriors", "espn_name")
      == identity.team_key("ncaaf", "Hawaii Rainbow Warriors", "odds_name"),
      "orthography difference (Hawai'i) joins under the normalised key")
check(identity.team_key("ncaaf", "San José State Spartans", "espn_name")
      == identity.team_key("ncaaf", "San Jose State Spartans", "odds_name"), "accents join")
check(identity.team_key("ncaaf", "Sam Houston State Bearkats", "odds_name")
      == identity.team_key("ncaaf", "Sam Houston Bearkats", "espn_name"),
      "the one explained alias (Sam Houston) applies on the odds side only")
check(identity.team_key("ncaaf", "Ohio State Buckeyes", "odds_name")
      != identity.team_key("ncaaf", "Ohio Bobcats", "espn_name"), "distinct programmes stay distinct")
raises(lambda: identity.team_key("ncaaf", "OSU", "espn_abbr"), identity.IdentityError,
       "college abbreviations are refused as identity")
uh = team("HAW", "Hawai'i Rainbow Warriors", "62")
ucla = team("UCLA", "UCLA Bruins", "26")
nc_recs, nc_errs = espn_state.parse_scoreboard(
    {"events": [espn_event("501", "2026-09-13T02:00Z", uh, ucla, "in", "14", "10", None, 3, 100.0)]},
    "ncaaf", NOW, "r1")
check(len(nc_recs) == 1 and not nc_errs and nc_recs[0]["home"] == "hawaiirainbowwarriors",
      "NCAAF game state carries normalised keys")
nc_q, nc_ev, _ = odds_quotes.parse_odds(
    [{"id": "c1", "commence_time": "2026-09-13T02:00:00Z", "home_team": "Hawaii Rainbow Warriors",
      "away_team": "UCLA Bruins", "bookmakers": [book("draftkings", LU, [h2h("Hawaii Rainbow Warriors", "UCLA Bruins", -200, 170, LU)])]}],
    "ncaaf", NOW, "r1", identity.index_by_canonical(nc_recs), "h2h")
check(nc_ev[0]["join_status"] == identity.JOINED and nc_ev[0]["canonical_event_id"].startswith("ncaaf:2026-09-08:"),
      "an NCAAF quote joins its ESPN game across the orthography difference")

print("\n[5] pregame vs live quote isolation")
P = odds_quotes.classify_phase
pre_state = {"status_state": "pre"}
in_state = {"status_state": "in"}
post_state = {"status_state": "post"}
check(P(NOW + timedelta(hours=2), NOW, pre_state) == ("pregame", "game_state"), "state pre -> pregame")
check(P(NOW - timedelta(hours=1), NOW, in_state) == ("live", "game_state"), "state in -> live")
check(P(NOW - timedelta(hours=4), NOW, post_state) == ("post", "game_state"), "state post -> post")
check(P(NOW + timedelta(minutes=1), NOW, in_state) == ("live", "game_state"),
      "game state WINS over a provider clock that says the game has not started")
check(P(NOW + timedelta(hours=2), NOW, None) == ("pregame", "commence_time"),
      "unjoined + future commence_time -> pregame by clock")
check(P(NOW - timedelta(minutes=1), NOW, None) == ("commenced_unjoined", "commence_time"),
      "unjoined + past commence_time -> commenced_unjoined (never live, never pregame)")
check(P(None, NOW, None) == ("unknown", "none"), "no clock at all -> unknown")
check(P(NOW - timedelta(hours=1), NOW, {"status_state": None}) == ("commenced_unjoined", "commence_time"),
      "a joined record with no usable state falls back to the clock")
for ph in odds_quotes.PHASES:
    check(odds_quotes.is_pregame({"quote_phase": ph}) == (ph == "pregame"),
          f"is_pregame is true for exactly 'pregame' ({ph})")
live_q = [x for x in q if x["provider_event_id"] == "e1"]
check(live_q and all(x["quote_phase"] == "live" and x["phase_basis"] == "game_state" for x in live_q),
      "every quote on the in-progress event is labelled live via game state")
check(all(x["join_status"] == identity.JOINED and x["game_state_observation_id"] == rec["observation_id"]
          for x in live_q), "and points at the game_state observation it was joined to")
e4 = [e for e in ev if e["provider_event_id"] == "e4"][0]
check(e4["quote_phase"] == "commenced_unjoined", "the unresolved commenced event is commenced_unjoined")
e3 = [e for e in ev if e["provider_event_id"] == "e3"][0]
check(e3["quote_phase"] == "pregame" and e3["join_status"] == identity.UNJOINED,
      "next week's game is pregame by clock and unjoined (not on today's scoreboard)")

print("\n[6] observation timestamp semantics")
check(all(r["observed_at"] == common.iso(NOW) for r in gs_recs), "observed_at is the receipt instant we passed")
check(all(r["provider_timestamp"] is None for r in gs_recs), "ESPN game state has provider_timestamp null")
check(all(x["observed_at"] == common.iso(NOW) for x in q), "every quote carries the same observed_at as its response")
check(all(x["observed_at"] != x["provider_timestamp"] for x in live_q),
      "observed_at is never the provider stamp")
raises(lambda: common.iso(datetime(2026, 1, 1)), ValueError, "iso() refuses a naive datetime")
check(common.parse_utc("2026-09-13T17:03") is None, "parse_utc refuses a zone-less string")
check(common.parse_utc("2026-08-21T23:00Z") == datetime(2026, 8, 21, 23, 0, tzinfo=UTC),
      "ESPN's minute-precision Z format parses")
check(common.parse_utc("2026-09-13T13:03:55-04:00") == datetime(2026, 9, 13, 17, 3, 55, tzinfo=UTC),
      "an explicit offset is normalised to UTC")
raises(lambda: common.slate_week_et(datetime(2026, 9, 14)), ValueError, "slate_week_et refuses naive")
check(common.slate_week_et(datetime(2026, 9, 15, 0, 15, tzinfo=UTC)) == "2026-09-08",
      "Monday Night Football (Tue 00:15Z) stays in its week under the ET anchor")
check(common.slate_week_et(datetime(2026, 9, 15, 4, 1, tzinfo=UTC)) == "2026-09-15",
      "Tuesday 00:01 ET starts the next week")

print("\n[7] provider timestamp preservation")
dk_tot = [x for x in q if x["book"] == "draftkings" and x["market"] == "totals"][0]
check(dk_tot["provider_market_last_update"] == "2026-09-13T18:36:40Z"
      and dk_tot["provider_book_last_update"] == LU, "book and market stamps kept verbatim")
check(dk_tot["provider_timestamp"] == "2026-09-13T18:36:40Z" and dk_tot["age_basis"] == "market_last_update",
      "provider_timestamp prefers the market stamp")
check(abs(dk_tot["quote_age_seconds"] - 43.0) < 1e-6, f"age computed from observed_at ({dk_tot['quote_age_seconds']}s)")
fd = [x for x in q if x["book"] == "fanduel" and x["provider_event_id"] == "e1"][0]
check(fd["provider_market_last_update"] is None and fd["age_basis"] == "book_last_update"
      and abs(fd["quote_age_seconds"] - 1043.0) < 1e-6,
      "no market stamp -> book stamp is the basis, and the 17-minute age is RECORDED, not dropped")

print("\n[8] duplicate execution idempotency")
tmp, store, clog = fresh_store()
BOOKED.clear()
r1 = tick(store, clog, run_id="fixedrun")
n1 = r1["records_written"]
r2 = tick(store, clog, run_id="fixedrun")
check(r2.get("replayed") is True and r2["records_written"] == n1,
      "re-running the same run_id replays the stored run record")
check(len(BOOKED) == 1, "and makes no second odds call or booking")
before = snapshot_tree(os.path.join(tmp, "raw"))
tick(store, clog, run_id="fixedrun")
check(snapshot_tree(os.path.join(tmp, "raw")) == before, "a replay changes no byte on disk")
# store-level: the same records offered again are refused as duplicates
recs = read_shard(tmp, "raw/nfl/2026-09-13/market_quote_14.jsonl")
summ = store.append(recs)
check(summ["written"] == 0 and summ["duplicates_skipped"] == len(recs),
      f"append of {len(recs)} already-stored ids writes 0 and counts {len(recs)} duplicates")
raises(lambda: store.write_run(r1), StoreError, "a run record is never overwritten")

print("\n[9] sequential same-value observations remain distinct observations")
r3 = tick(store, clog, run_id="secondtick", now=NOW + timedelta(minutes=5))
check(r3["records_written"] == n1 and r3["duplicates_skipped"] == 0,
      "identical payload five minutes later stores every record again")
qs = read_shard(tmp, "raw/nfl/2026-09-13/market_quote_14.jsonl")
ids = {x["observation_id"] for x in qs}
check(len(ids) == len(qs) and len(qs) == 2 * len(recs), "distinct observation_ids per tick, both ticks present")
runs = {x["run_id"] for x in qs}
check(runs == {"fixedrun", "secondtick"}, "records name the run that observed them")
shutil.rmtree(tmp, ignore_errors=True)

print("\n[10] odds market normalisation")
check({x["market"] for x in q} == {"h2h", "spreads", "totals"}, "the three markets are captured")
check(e1["markets_offered"] == {"betmgm": ["totals"], "draftkings": ["h2h", "spreads", "totals"],
                                "fanduel": ["h2h"]}, "markets offered per book recorded")
check(e1["books_per_market"] == {"h2h": 2, "spreads": 1, "totals": 2}, "books per market counted")
odd_body = [dict(nfl_odds_body()[0], bookmakers=[book("dk", LU, [{"key": "player_pass_tds", "outcomes": [{"name": "x", "price": 100}]}])])]
q_u, ev_u, err_u = odds_quotes.parse_odds(odd_body, "nfl", NOW, "r1", idx, "h2h")
check(not q_u and ev_u[0]["markets_offered"] == {"dk": []} and not err_u,
      "an uncaptured market key is ignored silently (not an error, not a quote)")

print("\n[11] spread side/point identity")
sp = {x["outcome"]: x for x in q if x["market"] == "spreads"}
check(sp["JAX"]["point"] == -3.0 and sp["CLE"]["point"] == 3.0, "spread points carry the side's sign")
check(all(isinstance(x["point"], float) for x in sp.values()), "points are floats")
bad = [dict(nfl_odds_body()[0], bookmakers=[book("dk", LU, [{"key": "spreads", "outcomes": [
    {"name": "Jacksonville Jaguars", "price": -110}, {"name": "Cleveland Browns", "price": -110, "point": 3.0}]}])])]
q_b, _, err_b = odds_quotes.parse_odds(bad, "nfl", NOW, "r1", idx, "spreads")
check(len(q_b) == 1 and len(err_b) == 1 and "malformed" in err_b[0]["reason"],
      "a spread outcome with no point is refused and recorded; its sibling survives")

print("\n[12] total over/under identity")
tot = [x for x in q if x["market"] == "totals" and x["book"] == "draftkings"]
check({x["outcome"] for x in tot} == {"Over", "Under"} and all(x["point"] == 44.5 for x in tot),
      "totals map to Over/Under at the shared point")
check(odds_quotes.outcome_key("nfl", "totals", "under", None, None, None, None) == "Under"
      and odds_quotes.outcome_key("nfl", "totals", "OVER", None, None, None, None) == "Over",
      "case-insensitive Over/Under")
check(odds_quotes.outcome_key("nfl", "totals", "Jacksonville Jaguars", "CLE", "JAX", "Cleveland Browns", "Jacksonville Jaguars") is None,
      "a team name on a totals market is not an outcome")
bad = [dict(nfl_odds_body()[0], bookmakers=[book("dk", LU, [{"key": "totals", "outcomes": [
    {"name": "Over", "price": -110}, {"name": "Under", "price": -110, "point": 44.5}]}])])]
q_b, _, err_b = odds_quotes.parse_odds(bad, "nfl", NOW, "r1", idx, "totals")
check(len(q_b) == 1 and len(err_b) == 1, "a total without a point is refused")

print("\n[13] moneyline identity")
ml = {x["outcome"]: x for x in q if x["market"] == "h2h" and x["book"] == "draftkings"}
check(set(ml) == {"JAX", "CLE"} and all(x["point"] is None for x in ml.values()),
      "moneyline outcomes are team keys with no point")
bad = [dict(nfl_odds_body()[0], bookmakers=[book("dk", LU, [{"key": "h2h", "outcomes": [
    {"name": "Jacksonville Jaguars", "price": -150, "point": -3}, {"name": "Cleveland Browns", "price": 130}]}])])]
q_b, _, err_b = odds_quotes.parse_odds(bad, "nfl", NOW, "r1", idx, "h2h")
check(len(q_b) == 1 and len(err_b) == 1, "a moneyline outcome carrying a point is refused")
for p in (50, -99, 99, 0, "abc", None, True, 110.5):
    check(odds_quotes._price(p) is None, f"price {p!r} is not a usable American price")
check(odds_quotes._price(-100) == -100 and odds_quotes._price("130") == 130, "-100 and '130' are usable")

print("\n[14] suspended / missing market handling")
e2 = [e for e in ev if e["provider_event_id"] == "e2"][0]
check(e2["n_books"] == 0 and e2["no_quotes"] is True and e2["n_quotes"] == 0,
      "an event returned with zero books is stored as a market_event with no_quotes")
check(not any(x["provider_event_id"] == "e2" for x in q), "and has no quote rows to invent")
check(e1["books_per_market"]["spreads"] == 1, "a book missing a market is visible in books_per_market")
_, ev_n, err_n = odds_quotes.parse_odds([dict(nfl_odds_body()[0], bookmakers=[{"last_update": LU, "markets": []}])],
                                        "nfl", NOW, "r1", idx, "h2h")
check(ev_n[0]["n_books"] == 0 and err_n and "bookmaker without key" in err_n[0]["reason"],
      "a bookmaker with no key is an error, not an anonymous book")

print("\n[15] missing game-state fields")
pre = [r for r in gs_recs if r["provider_event_id"] == "402"][0]
check(pre["home_score"] is None and pre["away_score"] is None, "pregame score is null, not ESPN's '0'")
check(pre["complete"] is True and pre["period"] is None and "period" in pre["missing_optional"],
      "optional fields null and named; the record is still complete")
live = [r for r in gs_recs if r["provider_event_id"] == "401"][0]
check(live["possession"] == "away" and live["down"] == 2 and live["distance"] == 7 and live["yard_line"] == 45
      and live["home_timeouts"] == 3 and live["away_timeouts"] == 2 and live["is_red_zone"] is False,
      "situation fields parsed, possession resolved to the away side by team id")
check(live["last_play"] == {"id": "9", "type": "Rush", "text": "run for 3", "score_value": 0},
      "last play captured")
check(live["espn_win_probability_home"] == 0.61, "ESPN's win probability is RECORDED (never an input)")
no_score = {"events": [espn_event("410", "2026-09-13T17:03Z", JAX, CLE, "in", "", "7", None, 2, 420.0)]}
rr, ee = espn_state.parse_scoreboard(no_score, "nfl", NOW, "r1")
check(rr and rr[0]["complete"] is False and rr[0]["missing_required"] == ["home_score"],
      "an in-progress game with an unparseable score is stored INCOMPLETE naming the field")
no_season = {"events": [dict(espn_event("411", "2026-09-13T17:03Z", JAX, CLE, "pre"), season={})]}
rr, _ = espn_state.parse_scoreboard(no_season, "nfl", NOW, "r1")
check(set(rr[0]["missing_required"]) == {"season_year", "season_type"} and not rr[0]["complete"],
      "missing season fields are named and mark the record incomplete")
odd_poss = {"events": [espn_event("412", "2026-09-13T17:03Z", JAX, CLE, "in", "3", "0", {"possession": "77"}, 1, 100.0)]}
rr, _ = espn_state.parse_scoreboard(odd_poss, "nfl", NOW, "r1")
check(rr[0]["possession"] is None, "a possession id matching neither team is null, not guessed")

print("\n[16] stale provider response handling")
stale_body = [dict(nfl_odds_body()[0], bookmakers=[book("dk", "2026-09-13T12:00:00Z", [h2h("Jacksonville Jaguars", "Cleveland Browns", -150, 130)])])]
q_s, _, _ = odds_quotes.parse_odds(stale_body, "nfl", NOW, "r1", idx, "h2h")
check(len(q_s) == 2 and all(x["quote_age_seconds"] > 6 * 3600 for x in q_s),
      "a 6.6-hour-old quote is stored with its age; ML-1 records staleness, it does not judge it")
fut_body = [dict(nfl_odds_body()[0], bookmakers=[book("dk", "2026-09-13T19:00:00Z", [h2h("Jacksonville Jaguars", "Cleveland Browns", -150, 130)])])]
q_f, _, _ = odds_quotes.parse_odds(fut_body, "nfl", NOW, "r1", idx, "h2h")
check(all(x["quote_age_seconds"] < 0 for x in q_f), "a provider stamp from the future yields a negative age, kept")
nolu = [dict(nfl_odds_body()[0], bookmakers=[book("dk", None, [h2h("Jacksonville Jaguars", "Cleveland Browns", -150, 130)])])]
q_n, _, _ = odds_quotes.parse_odds(nolu, "nfl", NOW, "r1", idx, "h2h")
check(all(x["quote_age_seconds"] is None and x["age_basis"] is None and x["provider_timestamp"] is None for x in q_n),
      "no provider stamp at all -> null age, null basis; nothing substituted")

print("\n[17] ended-game handling")
post = [r for r in gs_recs if r["provider_event_id"] == "403"][0]
check(post["status_state"] == "post" and post["completed"] is True and post["home_score"] == 24
      and post["away_score"] == 20, "a final game carries its score and completed flag")
active, imminent = capture.live_window(gs_recs, NOW, 20)
check([r["provider_event_id"] for r in active] == ["401"], "only the in-progress game is active")
check(imminent == [], "a 20:25Z kickoff is not imminent at 18:37Z with a 20-minute lead")
active2, imminent2 = capture.live_window(gs_recs, datetime(2026, 9, 13, 20, 10, tzinfo=UTC), 20)
check([r["provider_event_id"] for r in imminent2] == ["402"], "it becomes imminent 15 minutes out")
only_post = lambda s, d: {"events": [espn_event("403", "2026-09-13T17:00Z", LAR, WSH, "post", "24", "20")]}  # noqa: E731
tmp, store, clog = fresh_store()
fo = FakeOdds()
r = tick(store, clog, odds=fo, espn=only_post)
check(fo.calls == [] and r["sports"]["nfl"]["odds"]["decision"] == "skipped",
      "a slate with only finished games makes NO odds call")
shutil.rmtree(tmp, ignore_errors=True)

print("\n[18] clock/state parsing")
check(live["period"] == 2 and live["clock_seconds"] == 420.0 and live["display_clock"] == "7:00",
      "period, clock seconds and display clock parsed")
weird = {"events": [espn_event("420", "2026-09-13T17:03Z", JAX, CLE, "halftime?", "3", "0")]}
rr, _ = espn_state.parse_scoreboard(weird, "nfl", NOW, "r1")
check(rr[0]["status_state"] is None and "status_state" in rr[0]["missing_required"],
      "an unrecognised state is null and REQUIRED-missing, never coerced")
check(espn_state.classify(rr)["unknown"] == rr, "and it lands in the 'unknown' bucket, not pre")
check(odds_quotes.classify_phase(NOW - timedelta(hours=1), NOW, rr[0]) == ("commenced_unjoined", "commence_time"),
      "a quote joined to an unknown-state record is not called live")

print("\n[19] malformed payload fail-closed")
_, _, err = odds_quotes.parse_odds({"not": "a list"}, "nfl", NOW, "r1", idx, "h2h")
check(err and "not a list" in err[0]["reason"], "an odds body that is not a list yields one error, zero records")
q_m, ev_m, err_m = odds_quotes.parse_odds([None, 5, {"id": "z"}], "nfl", NOW, "r1", idx, "h2h")
check(not q_m and not ev_m and len(err_m) == 3, "non-object events and an event without teams: errors only")
rr, ee = espn_state.parse_scoreboard({"events": "nope"}, "nfl", NOW, "r1")
check(not rr and ee and "no events list" in ee[0]["reason"], "an ESPN payload without an events list is an error")
rr, ee = espn_state.parse_scoreboard({"events": [{"id": "1"}, 7, {"id": "2", "date": "garbage", "competitions": [
    {"competitors": [{"homeAway": "home", "team": JAX}, {"homeAway": "away", "team": CLE}]}]}]}, "nfl", NOW, "r1")
check(not rr and len(ee) == 3, "events with no competition, a non-object, and an unparseable kickoff: 3 errors, 0 records")
check(all("guess" not in json.dumps(x) for x in rr), "no record was produced with guessed fields")
tmp, store, clog = fresh_store()
r = tick(store, clog, odds=FakeOdds(status=429, remaining=0, cost=3))
od = r["sports"]["nfl"]["odds"]
check(od["http_status"] == 429 and od["n_quotes"] == 0 and od["credits"]["remaining"] == 0,
      "HTTP 429: nothing parsed, nothing stored, and the reading is still captured")
check(BOOKED and BOOKED[-1][1]["http_status"] == 429, "a non-200 reading is handed to the booking policy")
r = tick(store, clog, odds=FakeOdds(raise_exc=requests.ConnectionError("boom")))
check(r["sports"]["nfl"]["odds"]["fetch_error"].startswith("ConnectionError")
      and any(e["source"] == common.MARKET_SOURCE for e in r["errors"]),
      "a transport failure is recorded on the run; the tick completes")
check(r["records_written"] > 0, "and the game-state records from the same tick were still stored")
r = tick(store, clog, espn=lambda s, d: (_ for _ in ()).throw(requests.Timeout("espn down")))
check(r["exit_code"] == capture.EXIT_NOTHING_OBSERVED and r["records_written"] == 0
      and any("Timeout" in e["reason"] for e in r["errors"]),
      "an ESPN failure records the reason, stores nothing, exits 1 - never fabricates")
shutil.rmtree(tmp, ignore_errors=True)

print("\n[20] no Discord side effects")
SRC = {}
for fn in sorted(os.listdir(HERE)):
    if fn.endswith(".py") and fn != os.path.basename(__file__):
        SRC[fn] = io.open(os.path.join(HERE, fn), encoding="utf-8").read()


def code_only(src):
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


BANNED = ("post_discord", "import discord", "DISCORD_WEBHOOK", "discord.com", "webhook",
          "requests.post", "anthropic", "openai", "ANTHROPIC_API_KEY")
for fn, src in SRC.items():
    hits = [b for b in BANNED if b.lower() in code_only(src).lower()]
    check(not hits, f"{fn}: no Discord/LLM/posting reference ({hits})")
check(any("DISCORD_WEBHOOK" in b for b in BANNED)
      and [b for b in BANNED if b.lower() in 'os.environ["DISCORD_WEBHOOK_URL"]'.lower()],
      "negative control: the scan would catch a webhook reference")
check(NET == [], "no network call was attempted anywhere in this suite")

print("\n[21] no production ledger side effects")
LEDGERS = ("ledger.json", "football_ledger.json", "mercer_ledger.json", "daily_ledger.json",
           "totals_ledger.json", "watchlist.json", "commitments.json", "game_commitments.json",
           "data/football/odds", "data/mercer/odds", "post_status.json")
for fn, src in SRC.items():
    hits = [b for b in LEDGERS if b in code_only(src)]
    check(not hits, f"{fn}: references no ledger, commitment store or pregame odds directory ({hits})")
data_before = snapshot_tree(os.path.join(ROOT, "data"))
tmp, store, clog = fresh_store()
tick(store, clog)
tick(store, clog, sports=("nfl", "ncaaf"), espn=lambda s, d: nfl_espn(s, d) if s == "nfl" else {"events": []})
check(snapshot_tree(os.path.join(ROOT, "data")) == data_before,
      "two ticks into a temp store changed no file under the repository's data/")
raises(lambda: Store(tmp).shard_path("game_state", "../../escape", common.iso(NOW)), StoreError,
       "a shard path escaping the data dir is refused")
raises(lambda: Store(tmp).append([{"kind": "ledger", "sport": "nfl", "observed_at": common.iso(NOW),
                                   "observation_id": "x"}]), StoreError, "an unknown record kind is refused")
shutil.rmtree(tmp, ignore_errors=True)

print("\n[22] no pregame board changes")
fb_src = ""
for fn in os.listdir(os.path.join(ROOT, "scripts", "football")):
    if fn.endswith(".py"):
        fb_src += io.open(os.path.join(ROOT, "scripts", "football", fn), encoding="utf-8").read()
check("mercer_live" not in fb_src, "nothing in scripts/football references mercer_live")
for fn, src in SRC.items():
    check(not re.search(r"(?<![a-z])board_", code_only(src)) and "board.py" not in code_only(src)
          and "crypto_box" not in code_only(src),
          f"{fn}: touches no board, no encryption, no reveal")
check(common.SPORTS["nfl"]["odds_sport_key"] == "americanfootball_nfl"
      and common.SPORTS["ncaaf"]["odds_sport_key"] == "americanfootball_ncaaf", "sport keys as declared")
import fetch_odds as _fo                                           # noqa: E402  read-only
import capture_schedule as _cs                                     # noqa: E402  read-only
check(all(common.SPORTS[s]["odds_sport_key"] == _fo.SPORT_KEYS[s] for s in common.SPORTS),
      "Odds API sport keys have not drifted from fetch_odds.SPORT_KEYS")
check(all(common.SPORTS[s]["espn_path"] == _cs.ESPN_PATH[s] for s in common.SPORTS)
      and all(common.SPORTS[s]["espn_groups"] == _cs.ESPN_GROUPS.get(s) for s in common.SPORTS),
      "ESPN paths/groups have not drifted from capture_schedule")
check(_fo.ODDS_DIR.replace("\\", "/").endswith("data/football/odds"), "the pregame odds dir is unchanged")

print("\n[23] no locally generated index.html / feed.xml touched")
site_before = {p: os.stat(os.path.join(ROOT, p)).st_mtime_ns for p in ("index.html", "feed.xml")
               if os.path.exists(os.path.join(ROOT, p))}
tmp, store, clog = fresh_store()
tick(store, clog)
store.digest("2026-09-13")
site_after = {p: os.stat(os.path.join(ROOT, p)).st_mtime_ns for p in site_before}
check(site_before == site_after, "a tick and a digest leave index.html and feed.xml untouched")
shutil.rmtree(tmp, ignore_errors=True)
for fn, src in SRC.items():
    check("index.html" not in code_only(src) and "feed.xml" not in code_only(src),
          f"{fn}: never names index.html or feed.xml")

print("\n[24] repository contracts: workflows, gitignore, credits, store")
WF = os.path.join(ROOT, ".github", "workflows")
for wf in ("mercer-live-selftest.yml", "mercer-live-smoke.yml"):
    p = os.path.join(WF, wf)
    check(os.path.exists(p), f"{wf} exists")
    if not os.path.exists(p):
        continue
    raw = io.open(p, encoding="utf-8").read()
    code = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    doc = yaml.safe_load(raw)
    check(doc.get("permissions") == {"contents": "read"}, f"{wf}: contents: read")
    for banned in ("git push", "git commit", "git add", "post_discord", "DISCORD_WEBHOOK",
                   "--autostash", "git pull"):
        check(banned not in code, f"{wf}: never runs {banned}")
    trig = doc.get(True) or doc.get("on") or {}
    check("schedule" not in trig, f"{wf}: no GitHub cron")
smoke = yaml.safe_load(io.open(os.path.join(WF, "mercer-live-smoke.yml"), encoding="utf-8").read())
strig = smoke.get(True) or smoke.get("on")
check(set(strig) == {"workflow_dispatch"}, "smoke is dispatch-only")
check(strig["workflow_dispatch"]["inputs"]["spend_credits"]["default"] is False,
      "smoke defaults to spending nothing")
run_body = smoke["jobs"]["smoke"]["steps"][3]["run"]
check("--data-dir" in run_body and "RUNNER_TEMP" in run_body, "smoke writes only to the runner temp dir")
check("--no-odds" in run_body, "smoke passes --no-odds unless told to spend")
gate = yaml.safe_load(io.open(os.path.join(WF, "mercer-live-selftest.yml"), encoding="utf-8").read())
gpaths = (gate.get(True) or gate.get("on"))["push"]["paths"]
check("scripts/mercer_live/**" in gpaths, "the code gate triggers on scripts/mercer_live/**")
fb_gate = io.open(os.path.join(WF, "football-selftest.yml"), encoding="utf-8").read()
check("mercer_live" not in fb_gate, "the football gate does NOT run this suite (a red X keeps its meaning)")
gi = "\n".join(l for l in io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().splitlines()
               if not l.lstrip().startswith("#"))      # comments are not code
check(re.search(r"^data/mercer_live/raw/\s*$", gi, re.M) is not None, ".gitignore ignores data/mercer_live/raw/")
check(not re.search(r"^data/mercer_live/?\s*$", gi, re.M) and "data/mercer_live/digest" not in gi,
      "the digest directory is NOT ignored (it is the committed record)")

# credits
ok, why = credits.budget_check(None, 0, 3)
check(not ok and "unknown" in why, "unknown balance refuses to spend")
check(not credits.budget_check(4999, 0, 3)[0] and credits.budget_check(5000, 0, 3)[0],
      "the floor is inclusive at 5,000 remaining")
check(not credits.budget_check(99000, 3998, 3)[0] and credits.budget_check(99000, 3997, 3)[0],
      "the daily cap counts the call about to be made")
tmp, store, clog = fresh_store()
rd = {"remaining": 98000, "used": 2000, "last_call_cost": 3, "markets": "h2h,spreads,totals",
      "regions": "us", "http_status": 200, "source": credits.source_tag("nfl"),
      "read_utc": common.iso(NOW)}
b1 = credits.book(rd, "nfl", path=clog, now=NOW)
b2 = credits.book(dict(rd, read_utc=common.iso(NOW + timedelta(minutes=5))), "nfl", path=clog,
                  now=NOW + timedelta(minutes=5))
b3 = credits.book(dict(rd, read_utc=common.iso(NOW + timedelta(minutes=61))), "nfl", path=clog,
                  now=NOW + timedelta(minutes=61))
check(b1[0] and not b2[0] and b3[0], f"success readings book at most hourly per sport ({b1[1]} / {b2[1]} / {b3[1]})")
b4 = credits.book(dict(rd, http_status=429, read_utc=common.iso(NOW + timedelta(minutes=62))), "nfl",
                  path=clog, now=NOW + timedelta(minutes=62))
check(b4[0], "a non-200 reading is booked immediately regardless of the throttle")
b5 = credits.book(dict(rd, source=credits.source_tag("ncaaf"), read_utc=common.iso(NOW + timedelta(minutes=63))),
                  "ncaaf", path=clog, now=NOW + timedelta(minutes=63))
check(b5[0], "the throttle is per sport")
led = json.load(io.open(clog, encoding="utf-8"))["readings"]
check(led[0]["source"] == "fetch_closing" and len(led) == 5,
      "the other caller's reading survives and the ledger grew by exactly the booked readings")
check(credits.latest_remaining(clog) == 98000, "latest_remaining reads the newest reading")
r = tick(store, clog, odds=FakeOdds(remaining=99000, cost=3))
check(credits.spent_today(store, "2026-09-13") == 3, "spent_today sums this package's calls from run records")
r = tick(store, clog, odds=FakeOdds(), floor=100000)
check(r["sports"]["nfl"]["odds"]["decision"] == "skipped" and "floor" in r["sports"]["nfl"]["odds"]["reason"],
      "below the floor: skipped, reason recorded")
r = tick(store, clog, odds=FakeOdds(), cap=3)
check(r["sports"]["nfl"]["odds"]["decision"] == "skipped" and "daily cap" in r["sports"]["nfl"]["odds"]["reason"],
      "over the cap: skipped, reason recorded")
r = tick(store, clog, odds=FakeOdds(), api_key=None)
check(r["sports"]["nfl"]["odds"]["decision"] == "skipped" and "ODDS_API_KEY" in r["sports"]["nfl"]["odds"]["reason"]
      and r["records_written"] > 0, "no key: odds skipped, game state still stored")
r = tick(store, clog, odds_enabled=False)
check(r["sports"]["nfl"]["odds"]["decision"] == "disabled" and not r["odds_calls"], "--no-odds makes no call")
check(r["api_key_fingerprint"] and len(r["api_key_fingerprint"]) == 12 and "k" != r["api_key_fingerprint"],
      "the run record carries a key fingerprint, never the key")
check(json.dumps(r).count('"k"') == 0, "the key value appears nowhere in the run record")
fo = FakeOdds()
r = tick(store, clog, odds=fo)
call = fo.calls[0]
check(call["to"] == NOW + timedelta(minutes=20) and call["from"] == NOW - timedelta(hours=8),
      "the odds call is windowed to [now-8h, now+lead]")
od = r["sports"]["nfl"]["odds"]
check(od["n_events_returned"] == 4 and od["n_events_stored"] == 3 and od["n_events_dropped_pregame_outside_window"] == 1,
      "next week's pregame event is dropped and counted; the imminent one is kept")
# digest
d = store.digest("2026-09-13")
check(d["odds_calls"] == 2 and d["credits_spent"] == 6 and d["runs"] == 6,
      f"digest counts runs, calls and credits from the run records ({d['runs']} runs, {d['odds_calls']} calls, {d['credits_spent']} credits)")
check(all(s["lines"] == s["parsed"] for s in d["shards"]), "every shard line parses")
# THE DIGEST NAMES EACH KIND IN FULL. The first live smoke run (2026-09-21)
# showed game_state_15.jsonl digested as kind "game": the label came from
# split("_")[0], which also collapsed market_event and market_quote to one
# word. The path disambiguated it, but the labelled field in the COMMITTED
# artifact was wrong. Asserted per kind so it cannot regress to a prefix.
kinds = {s["kind"] for s in d["shards"]}
check(kinds and kinds <= set(common.KINDS),
      f"every digested shard names a real record kind ({sorted(kinds)})")
for k in ("game_state", "market_event", "market_quote"):
    hits = [s for s in d["shards"] if s["path"].endswith(f"{k}_14.jsonl")]
    check(len(hits) == 1 and hits[0]["kind"] == k,
          f"{k} shard is digested as kind {k!r}, not a truncated prefix")
check(len({s["kind"] for s in d["shards"] if s["kind"].startswith("market")}) == 2,
      "market_event and market_quote stay DISTINCT kinds in the digest")
p = os.path.join(tmp, d["shards"][0]["path"])
h0 = d["shards"][0]["sha256"]
with io.open(p, "a", encoding="utf-8") as f:
    f.write("{}\n")
d2 = store.digest("2026-09-13")
check(d2["shards"][0]["sha256"] != h0 and d2["shards"][0]["lines"] == d["shards"][0]["lines"] + 1,
      "appending one line changes the shard's fingerprint (tampering is detectable)")
check(os.path.exists(os.path.join(tmp, "digest", "2026-09-13.json")), "the digest file is written under digest/")
# torn line does not crash a later append
with io.open(p, "a", encoding="utf-8") as f:
    f.write('{"observation_id": "torn')
first = json.loads(io.open(p, encoding="utf-8").readline())
s2 = store.append([dict(first, observation_id="brandnew")])
tail = io.open(p, encoding="utf-8").read().splitlines()
check(s2["written"] == 1 and tail[-2] == '{"observation_id": "torn' and json.loads(tail[-1])["observation_id"] == "brandnew",
      "a torn trailing line is left in place and the next append starts on a fresh line")
shutil.rmtree(tmp, ignore_errors=True)

# slate week drift vs the editorial record's ET anchor
try:
    import mercer as _mercer                                       # noqa: E402  read-only
    samples = [datetime(2026, 9, 10, 0, 20, tzinfo=UTC), datetime(2026, 9, 14, 0, 20, tzinfo=UTC),
               datetime(2026, 9, 15, 0, 15, tzinfo=UTC), datetime(2026, 9, 15, 4, 1, tzinfo=UTC),
               datetime(2026, 11, 3, 3, 59, tzinfo=UTC), datetime(2026, 11, 3, 4, 0, tzinfo=UTC)]
    check(all(common.slate_week_et(s) == _mercer.slate_week(s) for s in samples),
          "slate_week_et agrees with mercer.slate_week on Thu/Sun/Mon-night/Tue-boundary/DST samples")
except Exception as e:                                              # noqa: BLE001
    check(False, f"could not compare against mercer.slate_week ({type(e).__name__}: {e})")

# main() plumbing: --dry-run writes nothing, --loop respects --max-ticks
tmp = tempfile.mkdtemp(prefix="mlive")
orig = capture.run_tick
orig_now_utc = common.now_utc
calls = []
# ONE injected clock for both halves of main(). run_tick stamps runs and shards
# with now_fn, but main() names the digest from common.now_utc() after the
# ticks. Faking only the first made this block depend on the machine's real
# calendar: runs landed under NOW's ET date while the digest took today's, so it
# passed only while the real US/Eastern date happened to be 2026-09-13.
CLOCK = [NOW]


def fake_run_tick(sports, **kw):
    calls.append(kw.get("run_id"))
    kw.update(now_fn=lambda: CLOCK[0], espn_fetch=nfl_espn, odds_fetch=FakeOdds(),
              book_fn=fake_book, api_key=None)
    return orig(sports, **kw)


capture.run_tick = fake_run_tick
common.now_utc = lambda: CLOCK[0]
try:
    rc = capture.main(["--sport", "nfl", "--no-odds", "--data-dir", tmp, "--dry-run"])
    check(rc == 0 and not os.path.isdir(os.path.join(tmp, "raw")), "--dry-run exits 0 and writes nothing")
    rc = capture.main(["--sport", "nfl", "--no-odds", "--data-dir", tmp, "--loop", "--interval", "0",
                       "--max-ticks", "3", "--digest"])
    runs = Store(tmp).runs_for_date("2026-09-13")
    check(rc == 0 and len(runs) == 3 and len({r["run_id"] for r in runs}) == 3,
          "--loop --max-ticks 3 writes three runs with three distinct run_ids")
    check(os.path.exists(os.path.join(tmp, "digest", "2026-09-13.json")), "--digest writes the day's digest")
    # The digest is keyed by the US/Eastern date, like runs and shards - never
    # the UTC date and never the Tuesday slate week. Each instant sits on a
    # boundary where one of those would give a different answer.
    for instant, want, label in (
            ("2026-09-15T03:59:59Z", "2026-09-14", "Mon 23:59:59 ET (UTC already Tuesday)"),
            ("2026-09-15T04:00:00Z", "2026-09-15", "Tue 00:00:00 ET rollover"),
            ("2026-09-14T01:30:00Z", "2026-09-13", "Sun 21:30 ET (UTC already Monday)"),
            ("2026-11-02T04:30:00Z", "2026-11-01", "23:30 EST after DST ends (EDT would say Nov 2)"),
            ("2027-03-15T04:30:00Z", "2027-03-15", "00:30 EDT after DST starts (EST would say Mar 14)")):
        CLOCK[0] = common.parse_utc(instant)
        dtmp = tempfile.mkdtemp(prefix="mlived")
        try:
            rc = capture.main(["--sport", "nfl", "--no-odds", "--data-dir", dtmp,
                               "--max-ticks", "1", "--digest"])
            files = sorted(os.listdir(os.path.join(dtmp, "digest")))
            runs = Store(dtmp).runs_for_date(want)
            check(files == [f"{want}.json"] and len(runs) == 1,
                  f"--digest at {label}: digest and run both under ET date {want} ({files})")
        finally:
            shutil.rmtree(dtmp, ignore_errors=True)
    CLOCK[0] = NOW
    rc = capture.main(["--sport", "nfl", "--no-odds", "--data-dir", tmp, "--until", "not-a-time"])
except SystemExit as e:
    check(e.code == 2, "--until rejects an unparseable instant")
finally:
    capture.run_tick = orig
    common.now_utc = orig_now_utc
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nmercer-live selftest: {n_checks} checks, "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
