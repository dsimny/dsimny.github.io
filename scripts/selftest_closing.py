#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for fetch_closing.py's credit provenance.

    python scripts/selftest_closing.py

RUNS FULLY OFFLINE. requests.get is replaced by a scripted fake that answers the
MLB schedule, the MLB teams list and the Odds API, so no network call is made and
no credit is spent. Every file is written under a temp directory; ROOT is
redirected and restored.

WHY THIS EXISTS. fetch_closing.py is the largest spender in the Odds API account
- three runs a day at two credits each, 6 of the 8 daily credits - and it was the
only capture caller with no DURABLE per-call record. It built a full credit
reading on every call and then only appended it to data/odds_credits.json, a
rolling log capped at CREDIT_LOG_KEEP=60 readings, about six days of history. After
that, what any closing capture cost was gone. fetch_odds.py and fetch_data.py both
embed the reading in the artifact they write; this caller did not.

WHY PER GAME AND NOT TOP-LEVEL. fetch_odds.py writes one file per call, so a
top-level "credits" key is natural there. fetch_closing.py writes a per-DAY
accumulator whose every top-level key is a gamePk, and odds_page.py iterates
store.items() as games. A top-level "credits" dict crashes that page with
TypeError; a top-level list crashes it with AttributeError - both verified before
this patch. So the reading rides inside each game record, where every reader
already uses .get(specific_key) and never iterates a record's own keys.

WHAT IS ASSERTED:

  1. a new capture embeds the reading in every game record;
  2. it is exactly the reading of the call that produced the line - the same
     values the Odds API headers returned, not a recomputation;
  3. the price fields are unchanged, so odds, CLV and grading see what they saw;
  4. the reading does NOT leak into history, and a re-run with unchanged prices
     appends no spurious history point even though read_utc moved;
  5. NO DOUBLE BOOKING: odds_credits.json gains exactly one reading per call,
     not one per game;
  6. the rolling ledger keeps its CREDIT_LOG_KEEP cap and oldest-first eviction;
  7. a legacy closing file with no credits field is still read correctly by
     grade.py, game_pages.py's access pattern, and odds_page.py;
  8. a frozen (already started) game keeps the reading of the call that captured
     its line, rather than being stamped with a later call it did not come from.
"""
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fetch_closing as fc   # noqa: E402
import grade                 # noqa: E402
import odds_page             # noqa: E402

EXPECTED_CHECKS = 30
fails = []
n = [0]


def check(cond, msg):
    n[0] += 1
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


DATE = "2026-09-13"
FUTURE = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
PAST = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Resp:
    def __init__(self, body, headers=None, status=200):
        self._body, self.headers, self.status_code = body, headers or {}, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    """Answers the three endpoints fetch_closing.py calls; records every call."""

    def __init__(self, games, headers, away_price=-130, home_price=+110):
        self.games, self.headers = games, headers
        self.away_price, self.home_price = away_price, home_price
        self.odds_calls = 0

    def get(self, url, params=None, timeout=None):
        if url.endswith("/schedule"):
            return Resp({"dates": [{"games": [
                {"gamePk": pk, "gameType": "R", "gameDate": utc,
                 "status": {"abstractGameState": state},
                 "teams": {"away": {"team": {"id": 1}}, "home": {"team": {"id": 2}}}}
                for pk, utc, state in self.games]}]})
        if url.endswith("/teams"):
            return Resp({"teams": [{"id": 1, "name": "Away Club"},
                                   {"id": 2, "name": "Home Club"}]})
        if "the-odds-api.com" in url:
            self.odds_calls += 1
            return Resp([{"away_team": "Away Club", "home_team": "Home Club",
                          "bookmakers": [{"markets": [
                              {"key": "h2h", "outcomes": [
                                  {"name": "Away Club", "price": self.away_price},
                                  {"name": "Home Club", "price": self.home_price}]},
                              {"key": "totals", "outcomes": [
                                  {"name": "Over", "point": 8.5, "price": -110},
                                  {"name": "Under", "point": 8.5, "price": -110}]}]}]}],
                        headers=self.headers)
        raise AssertionError(f"unexpected network call: {url}")


def run(tmp, fake):
    """One fetch_closing run against the fake, returning (store, ledger)."""
    fc.requests.get = fake.get
    fc.main()
    store = json.load(io.open(os.path.join(tmp, "data", f"closing_{DATE}.json"),
                              encoding="utf-8"))
    ledger = json.load(io.open(os.path.join(tmp, "data", "odds_credits.json"),
                               encoding="utf-8"))
    return store, ledger


real = {"ROOT": fc.ROOT, "DATE": fc.DATE, "SEASON": fc.SEASON, "get": fc.requests.get,
        "key": os.environ.get("ODDS_API_KEY")}
tmp = tempfile.mkdtemp(prefix="olsclose")
try:
    os.makedirs(os.path.join(tmp, "data"))
    fc.ROOT, fc.DATE, fc.SEASON = tmp, DATE, int(DATE[:4])
    os.environ["ODDS_API_KEY"] = "offline-test-key-never-sent"

    H1 = {"x-requests-remaining": "99836", "x-requests-used": "164", "x-requests-last": "2"}
    H2 = {"x-requests-remaining": "99834", "x-requests-used": "166", "x-requests-last": "2"}

    # ---- 1/2/3. a new capture embeds exactly the call's reading ----------
    print("[1] a new closing capture carries its credit provenance")
    fake = FakeRequests([(900001, FUTURE, "Preview")], H1)
    store, ledger = run(tmp, fake)
    g = store.get("900001", {})
    check("credits" in g, "the game record carries a credits field")
    c = g.get("credits") or {}
    print("\n[2] it is the reading of the call that produced the line")
    check(c.get("remaining") == 99836, f"remaining == header value ({c.get('remaining')})")
    check(c.get("used") == 164, f"used == header value ({c.get('used')})")
    check(c.get("last_call_cost") == 2,
          f"last_call_cost == x-requests-last, the per-call cost ({c.get('last_call_cost')})")
    check(c.get("source") == "fetch_closing", "source identifies the caller")
    check(c.get("http_status") == 200, "http_status recorded")
    check(c == ledger["readings"][-1],
          "the embedded reading is IDENTICAL to what record_credits() booked - "
          "the same object, not a recomputation")
    check(set(c) == {"remaining", "used", "last_call_cost", "markets", "regions",
                     "http_status", "source", "read_utc"},
          "same field set fetch_odds.py embeds, so the two stay comparable")

    print("\n[3] the prices are untouched")
    check((g.get("away_ml"), g.get("home_ml"), g.get("total")) == (-130, 110, 8.5),
          f"away_ml/home_ml/total unchanged ({g.get('away_ml')}, {g.get('home_ml')}, "
          f"{g.get('total')})")
    check((g.get("over_price"), g.get("under_price")) == (-110, -110),
          "over/under prices unchanged")

    # ---- 4. no leak into history, no spurious history point ---------------
    print("\n[4] provenance stays out of the movement history")
    check(all("credits" not in p for p in g.get("history", [])),
          "no history point carries the credits field")
    check(len(g.get("history", [])) == 1, "one history point after the first capture")

    # ---- 5. no double booking --------------------------------------------
    print("\n[5] NO DOUBLE BOOKING")
    booked_before = len(ledger["readings"])
    check(booked_before == 1 and fake.odds_calls == 1,
          f"one odds call booked exactly one reading ({booked_before} for "
          f"{fake.odds_calls} call)")

    # A second run, SAME prices, new headers: the line did not move.
    fake2 = FakeRequests([(900001, FUTURE, "Preview")], H2)
    store2, ledger2 = run(tmp, fake2)
    g2 = store2["900001"]
    check(len(ledger2["readings"]) == 2 and fake2.odds_calls == 1,
          "a second call books exactly one more reading, not one per game")
    check(len(g2.get("history", [])) == 1,
          "unchanged prices append NO history point, even though read_utc moved - "
          "the comparison ignores credits")
    check(g2["credits"]["used"] == 166,
          "the game now carries the reading of the call that last produced its line")

    # Multiple games from one call must not multiply the booking.
    tmp_multi = tempfile.mkdtemp(prefix="olsclose_multi")
    os.makedirs(os.path.join(tmp_multi, "data"))
    fc.ROOT = tmp_multi
    try:
        fake3 = FakeRequests([(900011, FUTURE, "Preview"), (900012, FUTURE, "Preview"),
                              (900013, FUTURE, "Preview")], H1)
        store3, ledger3 = run(tmp_multi, fake3)
        check(len(store3) == 3, f"three games captured ({len(store3)})")
        check(len(ledger3["readings"]) == 1,
              f"THREE games from ONE call book exactly ONE reading "
              f"({len(ledger3['readings'])}) - the per-game copies are provenance, "
              f"not bookings")
        embedded_sum = sum(v["credits"]["last_call_cost"] for v in store3.values())
        check(embedded_sum == 3 * ledger3["readings"][0]["last_call_cost"],
              f"summing the per-game copies overstates spend by the game count "
              f"({embedded_sum} vs a true cost of "
              f"{ledger3['readings'][0]['last_call_cost']}) - which is exactly why "
              f"the field is documented as never-sum provenance")
    finally:
        fc.ROOT = tmp
        shutil.rmtree(tmp_multi, ignore_errors=True)

    # ---- 6. rolling ledger behaviour unchanged ----------------------------
    print("\n[6] the rolling account ledger is unchanged")
    check(fc.CREDIT_LOG_KEEP == 60, f"CREDIT_LOG_KEEP is still 60 ({fc.CREDIT_LOG_KEEP})")
    log_path = os.path.join(tmp, "data", "odds_credits.json")
    json.dump({"readings": [{"used": i, "source": "seed"} for i in range(60)]},
              io.open(log_path, "w", encoding="utf-8"))
    fc.record_credits({"used": 999, "source": "fetch_closing"})
    capped = json.load(io.open(log_path, encoding="utf-8"))["readings"]
    check(len(capped) == 60, f"the ledger stays capped at 60 ({len(capped)})")
    check(capped[0]["used"] == 1 and capped[-1]["used"] == 999,
          "oldest evicted first, newest appended last")

    # ---- 8. a frozen game keeps its own call's reading --------------------
    print("\n[8] a game already under way keeps the reading of the call that captured it")
    frozen_before = json.load(io.open(os.path.join(tmp, "data", f"closing_{DATE}.json"),
                                      encoding="utf-8"))
    frozen_before["900001"]["utc"] = PAST
    json.dump(frozen_before, io.open(os.path.join(tmp, "data", f"closing_{DATE}.json"),
                                     "w", encoding="utf-8"))
    H3 = {"x-requests-remaining": "99000", "x-requests-used": "1000", "x-requests-last": "2"}
    fake4 = FakeRequests([(900001, PAST, "Live")], H3, away_price=-400, home_price=+300)
    store4, _ = run(tmp, fake4)
    g4 = store4["900001"]
    check(g4["credits"]["used"] == 166,
          "the frozen game still carries the reading of the call that produced its "
          "closing line, not the in-play call that was refused")
    check(g4["away_ml"] == -130, "and its pre-pitch price was not overwritten in play")

    # ---- 7. legacy files without the field stay readable -----------------
    print("\n[7] legacy closing files with no credits field are still read")
    legacy = {"900021": {"away_ml": -120, "home_ml": 100, "total": 9.0,
                         "over_price": -105, "under_price": -115,
                         "away_name": "Away Club", "home_name": "Home Club",
                         "utc": FUTURE, "captured_utc": FUTURE,
                         "history": [{"away_ml": -120, "home_ml": 100, "total": 9.0,
                                      "over_price": -105, "under_price": -115,
                                      "captured_utc": FUTURE}]}}
    ltmp = tempfile.mkdtemp(prefix="olsclose_legacy")
    os.makedirs(os.path.join(ltmp, "data"))
    json.dump(legacy, io.open(os.path.join(ltmp, "data", f"closing_{DATE}.json"), "w",
                              encoding="utf-8"))
    try:
        loaded = grade.load_closing(ltmp, DATE)
        cl = loaded.get("900021")
        check(cl is not None and cl.get("away_ml") == -120 and "credits" not in cl,
              "grade.load_closing reads a legacy record and its price fields")
        # game_pages.py's exact access pattern: closing.get(str(pk)) then .get(key)
        gp = loaded.get(str(900021)) if loaded else None
        check(gp is not None and gp.get("home_ml") == 100,
              "game_pages.py's closing.get(str(pk)).get(key) pattern works on legacy")
        old_root = odds_page.ROOT
        odds_page.ROOT = ltmp
        try:
            odds_page.render(DATE)
            html = io.open(os.path.join(ltmp, "odds", "index.html"), encoding="utf-8").read()
            check("Away Club" in html and "Home Club" in html,
                  "odds_page.py renders a legacy file with no credits field")
        finally:
            odds_page.ROOT = old_root
        # and the NEW shape renders too, with no phantom game card
        new_shape = {k: dict(v, credits=c) for k, v in legacy.items()}
        json.dump(new_shape, io.open(os.path.join(ltmp, "data", f"closing_{DATE}.json"),
                                     "w", encoding="utf-8"))
        odds_page.ROOT = ltmp
        try:
            odds_page.render(DATE)
            html2 = io.open(os.path.join(ltmp, "odds", "index.html"), encoding="utf-8").read()
            check("Game credits" not in html2 and "Away Club" in html2,
                  "odds_page.py renders the NEW shape with no phantom 'credits' game card")
        finally:
            odds_page.ROOT = old_root
    finally:
        shutil.rmtree(ltmp, ignore_errors=True)

    check(fake.odds_calls + fake2.odds_calls + fake4.odds_calls == 3,
          "every odds call in this suite went to the fake - no network was touched")
finally:
    fc.ROOT, fc.DATE, fc.SEASON = real["ROOT"], real["DATE"], real["SEASON"]
    fc.requests.get = real["get"]
    if real["key"] is None:
        os.environ.pop("ODDS_API_KEY", None)
    else:
        os.environ["ODDS_API_KEY"] = real["key"]
    shutil.rmtree(tmp, ignore_errors=True)

check(n[0] + 1 == EXPECTED_CHECKS, f"all {EXPECTED_CHECKS} checks ran (got {n[0] + 1})")
print()
if fails:
    print(f"FAIL — {len(fails)} of {n[0]} checks failed:")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print(f"PASS — {n[0]} checks, 0 failures.")
