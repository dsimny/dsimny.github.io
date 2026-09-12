#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the D.J. Mercer Spotlight.

    python scripts/football/selftest_mercer.py
    MERCER_KEEP=1 python scripts/football/selftest_mercer.py   # keep the pages

Runs the whole lifecycle against a FIXTURE results store in a temp directory -
nothing under data/ or football/ is touched - and asserts, point by point, the
guarantees the record makes on its own pages. Each section is numbered to the
requirement it proves:

  1  ledger separation        Mercer writes to its own file and nothing else
  2  pregame commitment       stamped before kickoff, never silently re-stamped
  3  immutable grading        a graded entry is never recomputed or edited
  4  pick taxonomy            OFFICIAL / LEAN / PASS enforced; only OFFICIAL counts
  5  units and ROI            American payouts, pushes, voids, multi-unit stakes
  6  split records            NFL, college and combined reported separately
  7  week and date handling   ET anchor, Mon night, Tue/Wed, postseason, cross-year
  8  late pick protection     commit REFUSES a selection after kickoff
  9  line provenance          the number and book taken are stored, never replaced
 10  loss visibility          losses render with the same prominence as wins
 11  legal language           21+, 1-800-GAMBLER, not-a-sportsbook, no guarantee
 12  generated-file safety    writes only under football/mercer/
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import mercer   # noqa: E402
import market   # noqa: E402
import settle   # noqa: E402
import crypto_box   # noqa: E402

# ---- HERMETIC TEST KEY ---------------------------------------------------
#
# WHY THIS EXISTS. Sealing the Mercer card (14f128e) routed save_week() through
# crypto_box.refuse_plaintext_in_ci(), which - correctly - aborts when
# GITHUB_ACTIONS=true and no key is set. This suite writes a card, so it began
# failing the hermetic gate the moment that landed, and the gate has been red
# since 2026-09-12 03:16.
#
# The gate must stay secret-free: "a red run here means a code regression" is
# only true while nothing in it depends on repository configuration. So the suite
# supplies its OWN key rather than the workflow supplying the real one.
#
# DETERMINISTIC, derived from a literal string, so a failure reproduces exactly
# and a reviewer can see at a glance that it is not a production key. It is never
# printed, never written outside the temp directory, and never committed - the
# only artifacts it encrypts live under tempfile.mkdtemp() and are deleted in
# finally. crypto_box is NOT modified: the production guard still refuses a
# plaintext CI write without a key, and section [13] proves it still does.
TEST_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(b"ols-selftest-mercer-fixture-key").digest()).decode()

# None means "was unset", which must be restored as UNSET rather than as "".
_REAL_KEY = os.environ.get(crypto_box.ENV_KEY)


def _restore_key():
    """Put the environment back exactly as it was found."""
    if _REAL_KEY is None:
        os.environ.pop(crypto_box.ENV_KEY, None)
    else:
        os.environ[crypto_box.ENV_KEY] = _REAL_KEY


def _prod_digest():
    """Fingerprint of the REAL data/mercer tree, to prove the suite never touches it.

    The suite redirects mercer.DATA into a temp directory, so this should be
    identical before and after. Handing the suite an encryption key is exactly
    the kind of change that could turn "writes nothing" into "writes something
    encrypted somewhere", so it is measured rather than assumed.
    """
    real = os.path.join(ROOT, "data", "mercer")
    out = {}
    for base, dirs, files in os.walk(real):
        for f in sorted(files):
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, ROOT).replace("\\", "/")] = \
                    hashlib.sha256(fh.read()).hexdigest()
    return out


WEEK = "2026-09-08"


def ev(eid, sport, away, home, kick, a=None, h=None, stype=2, status=None):
    final = a is not None
    if status is None:
        status = "STATUS_FINAL" if final else "STATUS_SCHEDULED"
    row = {"espn_event_id": eid, "kickoff_utc": kick, "final": final, "status": status,
           "season_type": stype, "season_year": 2026,
           "season_slug": {1: "preseason", 2: "regular-season", 3: "postseason"}[stype],
           "away_score": a, "home_score": h,
           "margin": (h - a) if final else None, "total": (h + a) if final else None}
    if sport == "nfl":
        row.update({"away": away[0], "away_abbr": away[0], "away_name": away[1],
                    "home": home[0], "home_abbr": home[0], "home_name": home[1]})
    else:
        row.update({"away": away[1], "away_abbr": away[0],
                    "away_key": mercer.espn_ncaaf.key_for(away[1]),
                    "home": home[1], "home_abbr": home[0],
                    "home_key": mercer.espn_ncaaf.key_for(home[1])})
    return row


STORES = {
    "nfl": {
        # Sunday night. Ravens win by 4; total 44 (a total ON the number).
        "1": ev("1", "nfl", ("BAL", "Baltimore Ravens"), ("BUF", "Buffalo Bills"),
                "2026-09-14T00:20:00Z", 24, 20),
        # A tie: the moneyline pushes.
        "2": ev("2", "nfl", ("GB", "Green Bay Packers"), ("DAL", "Dallas Cowboys"),
                "2026-09-13T20:25:00Z", 40, 40),
        # Preseason, final: never gradeable.
        "3": ev("3", "nfl", ("DET", "Detroit Lions"), ("CIN", "Cincinnati Bengals"),
                "2026-09-11T23:00:00Z", 14, 16, stype=1),
        # Not played yet.
        "4": ev("4", "nfl", ("SF", "San Francisco 49ers"), ("LA", "Los Angeles Rams"),
                "2026-09-11T00:35:00Z"),
        # MONDAY NIGHT, 20:15 ET = Tue 00:15 UTC. The week-anchor case.
        "5": ev("5", "nfl", ("NYJ", "New York Jets"), ("MIN", "Minnesota Vikings"),
                "2026-09-15T00:15:00Z", 17, 31),
        # A LOSS at a plus price, 2 units.
        "6": ev("6", "nfl", ("CAR", "Carolina Panthers"), ("NE", "New England Patriots"),
                "2026-09-13T17:00:00Z", 10, 27),
        # Postseason, final: not on the allowlist.
        "7": ev("7", "nfl", ("KC", "Kansas City Chiefs"), ("PHI", "Philadelphia Eagles"),
                "2026-09-13T21:00:00Z", 21, 24, stype=3),
    },
    "ncaaf": {
        "10": ev("10", "ncaaf", ("UGA", "Georgia Bulldogs"), ("ALA", "Alabama Crimson Tide"),
                 "2026-09-12T23:30:00Z", 24, 27),
        "11": ev("11", "ncaaf", ("GSU", "Georgia State Panthers"),
                 ("ALST", "Alabama State Hornets"), "2026-09-12T20:00:00Z", 10, 13),
        # WEDNESDAY midweek, 19:00 ET -> its own Tuesday week.
        "12": ev("12", "ncaaf", ("TOL", "Toledo Rockets"), ("OHIO", "Ohio Bobcats"),
                 "2026-09-16T23:00:00Z", 28, 21),
    },
}


def pick(pid, sport, away, home, kick, market_, price=-110, units=1.0, **kw):
    p = {"id": pid, "sport": sport, "away": away, "home": home, "kickoff_utc": kick,
         "market": market_, "price": price, "units": units,
         "why": "The number asks more of the favourite than the matchup supports.",
         "case_against": "A fast start by the home side and this looks silly by halftime.",
         "changes_my_mind": "Friday injury report."}
    p.update(kw)
    return p


P1 = pick("w1-nfl-01", "nfl", "Baltimore Ravens", "Buffalo Bills", "2026-09-14T00:20:00Z",
          "spread", team="Baltimore Ravens", line=3, conviction="strong",
          mercer_number="BAL +1.5", book="DraftKings")
P2 = pick("w1-nfl-02", "nfl", "Baltimore Ravens", "Buffalo Bills", "2026-09-14T00:20:00Z",
          "total", side="over", line=44)
P3 = pick("w1-nfl-03", "nfl", "Green Bay Packers", "Dallas Cowboys", "2026-09-13T20:25:00Z",
          "moneyline", team="Dallas Cowboys", price=-150)
P4 = pick("w1-nfl-04", "nfl", "Detroit Lions", "Cincinnati Bengals", "2026-09-11T23:00:00Z",
          "spread", team="Cincinnati Bengals", line=-1)
P5 = pick("w1-nfl-05", "nfl", "San Francisco 49ers", "Los Angeles Rams", "2026-09-11T00:35:00Z",
          "spread", team="San Francisco 49ers", line=2.5)
P6 = pick("w1-cfb-01", "ncaaf", "Georgia", "Alabama", "2026-09-12T23:30:00Z",
          "spread", team="Alabama", line=-2.5, units=0.5)
P7 = pick("w1-nfl-07", "nfl", "Buffalo Bills", "Baltimore Ravens", "2026-09-14T00:20:00Z",
          "spread", team="Buffalo Bills", line=-3)
P9 = pick("w1-nfl-09", "nfl", "Baltimore Ravens", "Buffalo Bills", "2026-09-14T00:20:00Z",
          "spread", team="Buffalo Bills", line=-3)
P10 = pick("w1-nfl-10", "nfl", "Green Bay Packers", "Dallas Cowboys", "2026-09-13T20:25:00Z",
           "spread", team="Green Bay Packers", line=3.5)
P11 = pick("w1-nfl-11", "nfl", "New York Jets", "Minnesota Vikings", "2026-09-15T00:15:00Z",
           "spread", team="Minnesota Vikings", line=-6.5, book="Caesars")
P12 = pick("w1-nfl-12", "nfl", "Carolina Panthers", "New England Patriots",
           "2026-09-13T17:00:00Z", "moneyline", team="Carolina Panthers", price=240,
           units=2, book="BetMGM")
P13 = pick("w1-nfl-13", "nfl", "Kansas City Chiefs", "Philadelphia Eagles",
           "2026-09-13T21:00:00Z", "spread", team="Kansas City Chiefs", line=1.5)
P14 = pick("w1-nfl-14", "nfl", "Baltimore Ravens", "Buffalo Bills", "2026-09-14T00:20:00Z",
           "moneyline", team="Baltimore Ravens", price=140)

T_EARLY = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
T_MID = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
T_LATE = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
T_GRADE = datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)
T_VIEW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def write_week(picks, week=WEEK):
    doc = {"slate_week": week, "title": "Fixture week", "picks": picks,
           "leans": [{"status": "LEAN", "sport": "nfl", "away": "Green Bay Packers",
                      "home": "Dallas Cowboys", "kickoff_utc": "2026-09-13T20:25:00Z",
                      "lean": "Under 47", "note": "Not enough to make it official."}],
           "passes": [{"status": "PASS", "sport": "ncaaf", "away": "Georgia Bulldogs",
                       "home": "Alabama Crimson Tide", "kickoff_utc": "2026-09-12T23:30:00Z",
                       "temptation": "Georgia +2.5", "reason": "Public number, no view."}],
           "spotlight": [{"title": "Ravens at Bills", "matchup": "NFL",
                          "sections": {"The case for": "x", "What would make this wrong": "y"}}]}
    mercer.save_json(os.path.join(mercer.WEEKS, f"{week}.json"), doc)


def main():
    tmp = tempfile.mkdtemp(prefix="olsmercer")
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # Snapshot the real Mercer data directory so [13] can prove the suite
    # left production untouched, key or no key.
    prod_before = _prod_digest()
    try:
        os.environ[crypto_box.ENV_KEY] = TEST_KEY
        mercer.DATA = os.path.join(tmp, "data")
        mercer.WEEKS = os.path.join(mercer.DATA, "weeks")
        mercer.LEDGER = os.path.join(mercer.DATA, "mercer_ledger.json")
        mercer.COMMITMENTS = os.path.join(mercer.DATA, "commitments.json")
        mercer.OUT = os.path.join(tmp, "football", "mercer")
        os.makedirs(mercer.WEEKS)

        # ---- 7. week and date handling ----------------------------------
        print("\n[7] week and date handling")
        for label, iso, want in [
                ("Thu 2026-09-10 20:20 ET", "2026-09-11T00:20:00Z", "2026-09-08"),
                ("Sun 2026-09-13 13:00 ET", "2026-09-13T17:00:00Z", "2026-09-08"),
                ("Sun 2026-09-13 20:20 ET", "2026-09-14T00:20:00Z", "2026-09-08"),
                ("MON 2026-09-14 20:15 ET", "2026-09-15T00:15:00Z", "2026-09-08"),
                ("Sat 2026-09-12 12:00 ET", "2026-09-12T16:00:00Z", "2026-09-08"),
                ("TUE 2026-09-15 19:00 ET", "2026-09-15T23:00:00Z", "2026-09-15"),
                ("WED 2026-09-16 19:00 ET", "2026-09-16T23:00:00Z", "2026-09-15"),
                ("Thu 2027-01-07 20:15 ET", "2027-01-08T01:15:00Z", "2027-01-05")]:
            got = mercer.slate_week(market.parse_utc(iso))
            check(got == want, f"{label} -> {want} (got {got})")
        mon = market.parse_utc("2026-09-15T00:15:00Z")
        check(mercer.slate_week(mon) != market.slate_week(mon),
              "Monday night is exactly where the ET anchor and the model's UTC anchor differ")
        check(mercer.current_week(T_VIEW) == "2026-09-08", "current_week maps today to its Tuesday")

        # ---- 4. taxonomy and the required case against -------------------
        print("\n[4] taxonomy and the required 'case against'")
        write_week([P1, P2, P3, P4, P5, P6, P9, P11, P12, P13])
        errs = mercer.validate_week(WEEK, mercer.load_week(WEEK))
        check(errs == [], f"a well-formed card validates clean ({errs})")
        e = mercer.validate_pick({k: v for k, v in P1.items() if k != "case_against"}, "x")
        check(any("case_against" in m for m in e), "a pick with no case against is REFUSED")
        e = mercer.validate_pick({k: v for k, v in P1.items() if k != "why"}, "x")
        check(any("'why'" in m for m in e), "a pick with no affirmative case is refused")
        check(any("move a lean" in m for m in mercer.validate_pick(dict(P1, status="LEAN"), "x")),
              "status LEAN inside picks[] is refused")
        check(any("0-unit" in m for m in mercer.validate_pick(dict(P1, units=0), "x")),
              "a 0-unit official pick is refused and pointed at 'leans'")
        e = mercer.validate_week(WEEK, {"slate_week": WEEK, "picks": [],
                                        "leans": [{"status": "OFFICIAL", "sport": "nfl",
                                                   "away": "a", "home": "b", "lean": "x"}]})
        check(any("must be 'LEAN'" in m for m in e), "status OFFICIAL inside leans[] is refused")
        e = mercer.validate_week(WEEK, {"slate_week": WEEK, "picks": [],
                                        "leans": [{"sport": "nfl", "away": "a", "home": "b",
                                                   "lean": "x", "units": 1}]})
        check(any("no units" in m for m in e), "a lean carrying units is refused")

        print("\n[4b] conviction levels, not invented precision")
        check(any("conviction must be" in m
                  for m in mercer.validate_pick(dict(P1, conviction=4), "x")),
              "a numeric confidence is refused")
        check(mercer.validate_pick(dict(P1, conviction="spotlight"), "x") == [],
              "conviction 'spotlight' is accepted")
        check(any("renamed" in m for m in mercer.validate_pick(dict(P1, confidence=4), "x")),
              "the retired 'confidence' field fails loudly")
        check(any("case_against" in m for m in mercer.validate_pick(dict(P1, worries="x"), "x")),
              "the retired 'worries' field names its replacement")
        check(any("barred" in m for m in mercer.validate_pick(dict(P1, why="this has edge"), "x")),
              "the barred expectation word is still refused")

        # ---- resolution --------------------------------------------------
        print("\n[resolution]")
        _, why6 = mercer.resolve_event(P6, STORES["ncaaf"])
        check("ambiguous" in (why6 or ""), "an ambiguous college short name is refused")
        ev6b, _ = mercer.resolve_event(dict(P6, espn_event_id="10"), STORES["ncaaf"])
        check(ev6b is not None, "pinning espn_event_id resolves it")
        _, whyx = mercer.resolve_event(dict(P6, espn_event_id="11", away="Georgia Bulldogs"),
                                       STORES["ncaaf"])
        check("does not match" in (whyx or ""), "a pinned id whose teams disagree is refused")
        _, why7 = mercer.resolve_event(P7, STORES["nfl"])
        check("no NFL game" in (why7 or ""), "home and away swapped does not resolve")
        _, whyr = mercer.resolve_event(dict(P1, away="Baltimore Raven"), STORES["nfl"])
        check("unrecognised" in (whyr or ""), "an NFL typo is refused, not fuzzed")

        P6p = dict(P6, espn_event_id="10")
        write_week([P1, P2, P3, P4, P5, P6p, P9, P11, P12, P13])
        check(mercer.cmd_check(WEEK, stores=STORES, now=T_EARLY) == 1,
              "check exits 1 while a pick could never be fingerprinted")

        # ---- 2 and 8. the commitment gate --------------------------------
        print("\n[2][8] fingerprints and the late-pick gate")
        rc = mercer.cmd_commit(WEEK, now=T_EARLY, stores=STORES)
        ids = {c["pick_id"] for c in mercer.load_commitments()["commitments"]}
        check(rc == 1, "commit exits 1 because some picks were refused")
        check("w1-nfl-04" not in ids, "[8] a preseason pick is NOT stamped")
        check("w1-nfl-13" not in ids, "[8] a postseason pick is NOT stamped")
        check({"w1-nfl-01", "w1-nfl-11", "w1-nfl-12"} <= ids, "the gradeable picks are stamped")
        check(len(ids) == 8, f"8 of the 10 picks are stamped (got {len(ids)})")
        n_before = len(mercer.load_commitments()["commitments"])
        mercer.cmd_commit(WEEK, now=T_EARLY, stores=STORES)
        check(len(mercer.load_commitments()["commitments"]) == n_before,
              "[2] re-running commit stamps nothing twice")

        write_week([P1, P2, P3, P4, P5, P6p, P9, P11, P12, P13, P14])
        rc = mercer.cmd_commit(WEEK, now=T_LATE, stores=STORES)
        ids = {c["pick_id"] for c in mercer.load_commitments()["commitments"]}
        check("w1-nfl-14" not in ids,
              "[8] a selection added after kickoff is REFUSED, not stamped late")
        check(rc == 1, "[8] that refusal makes the run go red")
        gate = mercer.commit_gate(P14, STORES["nfl"]["1"], T_LATE)
        check(gate and "Nothing is backfilled" in gate,
              "[8] the refusal names backfilling as the reason")

        P9e = dict(P9, line=-2.5)
        first9 = min((c for c in mercer.load_commitments()["commitments"]
                      if c["pick_id"] == "w1-nfl-09"), key=lambda c: c["committed_utc"])
        write_week([P1, P2, P3, P4, P5, P6p, P9e, P11, P12, P13])
        rc = mercer.cmd_commit(WEEK, now=T_MID, stores=STORES)
        still9 = min((c for c in mercer.load_commitments()["commitments"]
                      if c["pick_id"] == "w1-nfl-09"), key=lambda c: c["committed_utc"])
        check(still9["sha256"] == first9["sha256"] and rc == 1,
              "[2] an edited pick gets NO new stamp and the run goes red")

        # A pick added to the card and never run through `commit` at all. This
        # is the forgotten-pick case, and it must not be quietly ignored: it
        # reaches grading and books VOID in public.
        write_week([P1, P2, P3, P4, P5, P6p, P9e, P10, P11, P12, P13])
        check("w1-nfl-10" not in {c["pick_id"] for c
                                  in mercer.load_commitments()["commitments"]},
              "[2] a pick never passed through `commit` carries no stamp")

        # ---- 5. grading, payouts, pushes, voids ---------------------------
        print("\n[5] grading, payouts, pushes and voids")
        rc = mercer.cmd_grade(stores=STORES, now=T_GRADE)
        by = {e["pick_id"]: e for e in mercer.load_ledger()["entries"]}
        check(rc == 1, "grade exits 1 because one pick was edited after stamping")
        check("w1-nfl-09" not in by, "the edited pick is NOT booked")
        check("w1-nfl-05" not in by, "the unplayed game is not booked")
        check(by["w1-nfl-01"]["result"] == "WIN" and abs(by["w1-nfl-01"]["pnl"] - 0.9091) < 1e-6,
              "[5] Ravens +3 wins by 4 -> WIN, +0.9091u at -110")
        check(by["w1-nfl-02"]["result"] == "PUSH" and by["w1-nfl-02"]["pnl"] == 0.0,
              "[5] Over 44 with a total of 44 -> PUSH, exactly 0")
        check(by["w1-nfl-03"]["result"] == "PUSH", "[5] moneyline on a tie -> PUSH")
        check(by["w1-cfb-01"]["units"] == 0.5 and abs(by["w1-cfb-01"]["pnl"] - 0.4545) < 1e-4,
              "[5] a 0.5-unit win returns half a unit's payout")
        check(by["w1-nfl-12"]["result"] == "LOSS" and by["w1-nfl-12"]["pnl"] == -2.0,
              "[5] a 2-unit loss at +240 risks exactly 2 units")
        check(by["w1-nfl-11"]["result"] == "WIN",
              "[5][7] the Monday-night pick grades inside its own week")
        check(by["w1-nfl-10"]["result"] == "VOID" and "never" in by["w1-nfl-10"]["void_reason"],
              "a pick that reached grading unstamped -> VOID")
        check(by["w1-nfl-04"]["result"] == "VOID"
              and "season type" in by["w1-nfl-04"]["void_reason"],
              "[7] a preseason game that slipped onto the card books VOID, not a result")
        check(by["w1-nfl-13"]["result"] == "VOID"
              and "season type" in by["w1-nfl-13"]["void_reason"],
              "[7] a postseason game books VOID rather than breaking the run")
        check(abs(settle.pnl("WIN", 2.0, 240) - 4.8) < 1e-9, "[5] 2u win at +240 returns 4.80")
        check(abs(settle.pnl("WIN", 1.5, -150) - 1.0) < 1e-9, "[5] 1.5u win at -150 returns 1.00")
        check(settle.pnl("PUSH", 3.0, 500) == 0.0, "[5] a push returns exactly 0 at any price")

        # ---- 3. immutable grading -----------------------------------------
        print("\n[3] immutable grading")
        led = mercer.load_ledger()["entries"]
        snapshot = json.dumps(sorted(led, key=lambda e: e["pick_id"]), sort_keys=True)
        mercer.cmd_grade(stores=STORES, now=T_GRADE + timedelta(days=1))
        led2 = mercer.load_ledger()["entries"]
        check(len(led2) == len(led), "grading again books nothing twice")
        check(json.dumps(sorted(led2, key=lambda e: e["pick_id"]), sort_keys=True) == snapshot,
              "[3] every booked entry is byte-identical after a second grading run")
        edited = dict(P1, line=7, price=-105, units=5, why="rewritten after the fact")
        write_week([edited, P2, P3, P4, P5, P6p, P9e, P10, P11, P12, P13])
        rc = mercer.cmd_grade(stores=STORES, now=T_GRADE + timedelta(days=2))
        g = {e["pick_id"]: e for e in mercer.load_ledger()["entries"]}["w1-nfl-01"]
        check(g["line"] == 3 and g["price"] == -110 and g["units"] == 1.0
              and g["result"] == "WIN" and abs(g["pnl"] - 0.9091) < 1e-6,
              "[3] editing a graded pick changes NO field of its ledger entry")
        check(rc == 1, "[3] the alteration makes the run go red")

        # ---- 5 and 6. split records and the ROI denominator ---------------
        print("\n[5][6] split records and ROI")
        entries = mercer.load_ledger()["entries"]
        r_nfl = mercer.record(entries, "nfl")
        r_cfb = mercer.record(entries, "ncaaf")
        r_all = mercer.record(entries)
        check((r_nfl["w"], r_nfl["l"], r_nfl["p"]) == (2, 1, 2), f"[6] NFL is 2-1-2 ({r_nfl})")
        check(r_nfl["v"] == 3, f"[6] the three voids are counted and shown ({r_nfl['v']})")
        check((r_cfb["w"], r_cfb["l"], r_cfb["p"]) == (1, 0, 0), f"[6] college is 1-0 ({r_cfb})")
        check(r_all["n"] == r_nfl["n"] + r_cfb["n"], "[6] combined is exactly the two sports")
        check(r_nfl["risked"] == 6.0, f"[5] NFL denominator is decided units only ({r_nfl['risked']})")
        check(abs(r_nfl["pnl"] - (0.9091 + 0.9091 - 2.0)) < 0.01, "[5] NFL P/L sums the payouts")
        check(abs(r_nfl["roi"] - (0.9091 + 0.9091 - 2.0) / 6) < 1e-4,
              "[5] ROI divides unrounded P/L by units risked")
        decided = sum(e["units"] for e in entries if e["result"] != "VOID")
        voided = sum(e["units"] for e in entries if e["result"] == "VOID")
        check(voided > 0 and r_nfl["risked"] + r_cfb["risked"] == decided,
              "[5] void stakes are excluded from the denominator")

        # ---- 1. ledger separation -----------------------------------------
        print("\n[1] ledger separation")
        check(os.path.abspath(mercer.LEDGER).startswith(os.path.abspath(mercer.DATA)),
              "[1] the Mercer ledger lives under data/mercer/")
        src = io.open(os.path.join(ROOT, "scripts", "football", "mercer.py"),
                      encoding="utf-8").read()
        for forbidden in ("football_ledger", "daily_ledger", "totals_ledger", "watchlist"):
            check(not re.search(r"save_json\([^)]*" + forbidden, src),
                  f"[1] mercer.py never writes {forbidden}.json")
        # The commitment log gained a SECOND legitimate writer when premium
        # withholding landed: cmd_commit stamps, cmd_grade flips `revealed`.
        # Both are controlled; a THIRD would still fail this.
        check(src.count("save_json(LEDGER") == 1, "[1] exactly one writer for the ledger")
        check(src.count("save_json(COMMITMENTS") == 2,
              "[1] exactly two controlled writers for the commitments (stamp, reveal)")

        # ---- rendering ------------------------------------------------------
        print("\n[9][10][11][12] rendering")
        write_week([P1, P2, P3, P4, P5, P6p, P9e, P10, P11, P12, P13])
        check(mercer.cmd_render(stores=STORES, now=T_VIEW) == 0, "render exits 0")
        wk = io.open(os.path.join(mercer.OUT, WEEK, "index.html"), encoding="utf-8").read()
        hub = io.open(os.path.join(mercer.OUT, "index.html"), encoding="utf-8").read()
        rec = io.open(os.path.join(mercer.OUT, "record", "index.html"), encoding="utf-8").read()
        about = io.open(os.path.join(mercer.OUT, "about", "index.html"), encoding="utf-8").read()
        body = wk.split("<body>", 1)[1]

        written = []
        for base, _d, files in os.walk(tmp):
            for f in files:
                if f.endswith((".html", ".xml")):
                    written.append(os.path.relpath(os.path.join(base, f), tmp).replace("\\", "/"))
        stray = [w for w in written if not w.startswith("football/mercer/")]
        check(not stray, f"[12] every rendered file is under football/mercer/ ({stray})")
        check(not any(w.endswith("feed.xml") for w in written), "[12] no feed.xml is written")

        check("The case against" in body, "[4] 'The case against' renders on the card")
        check(body.count("The case against") == body.count("Why Mercer likes it"),
              "[4] every card carries both halves of the argument")
        check("What would change my mind" in body, "[4] 'What would change my mind' renders")
        check("OFFICIAL PICK" in body.upper(), "[4] the OFFICIAL status is printed on cards")
        # PREMIUM WITHHOLDING (2026-09-12): leans, the pass list and the week's
        # written prose publish only once EVERY pick in the week is revealed,
        # because they are written as one piece and can allude to a live play.
        # This fixture week never fully reveals (P5's game does not finish), so
        # the correct assertion is that they are ABSENT. selftest_mercer_premium
        # covers the revealed case, where they must all appear.
        check(">LEAN<" not in body and ">PASS<" not in body,
              "[4] leans and passes are withheld while a pick in the week is live")
        check("publish with the picks once every selection" in body,
              "[4] the page explains why they are withheld")
        check("★" not in body, "[4b] no star ratings survive")
        check("Conviction" in body and "Strong" in body, "[4b] conviction renders as a word")

        check("Taken at" in body and "DraftKings" in body,
              "[9] the book the number was taken at is shown on the card")
        check("<th>Book</th>" in rec and "Caesars" in rec, "[9] the record table carries the book")
        check("closing number" in rec, "[9] the record states the number is never replaced")

        loss = body[body.find('id="w1-nfl-12"'):]
        loss = loss[:loss.find("</article>")]
        for token in ("The case against", "Why Mercer likes it", "Taken at", "Fingerprinted"):
            check(token in loss, f"[10] the LOSS card still carries '{token}'")
        check("LOSS -2.00u" in loss, "[10] the loss shows its full negative P/L")
        check('class="pick graded-LOSS"' in body, "[10] the loss renders as a full card")
        check('class="res-LOSS"' in rec, "[10] the loss is in the record table")

        for name, h in (("week", wk), ("hub", hub), ("record", rec), ("about", about)):
            check("1-800-GAMBLER" in h, f"[11] {name}: 1-800-GAMBLER present")
            check("not a sportsbook" in h, f"[11] {name}: not-a-sportsbook present")
            check("guarantee" in h.lower(), f"[11] {name}: no-guarantee language present")
            check("21+" in h, f"[11] {name}: 21+ present")
            text = re.sub(r"<[^>]+>", " ", h.split("<body>", 1)[1]).lower()
            m = mercer.BARRED_RE.search(text)
            check(m is None, f"[11] {name}: no barred expectation word"
                  + (f" (found {m.group(0)!r})" if m else ""))
        check("rgline" in wk and "rgline" in rec,
              "[11] the responsible-gambling line sits at the point of decision")

        write_week([edited, P2, P3, P4, P5, P6p, P9e, P10, P11, P12, P13])
        mercer.cmd_render(stores=STORES, now=T_VIEW)
        wk2 = io.open(os.path.join(mercer.OUT, WEEK, "index.html"), encoding="utf-8").read()
        card = wk2[wk2.find('id="w1-nfl-01"'):]
        card = card[:card.find("</article>")]
        check("edited after the pick was" in card,
              "[3] an edited graded card says so on the page")
        check("Baltimore Ravens +3" in card, "[3] the page shows the COMMITTED selection")
        check("-110" in card and "5 units" not in card,
              "[3] the page shows the committed price and stake, not the edited ones")

        check("2–1–2" in hub or "2-1-2" in hub, "[6] hub tiles show the split records")
        check("This record opened with its first committed pick" in rec,
              "no backfill: the record page states when it opened")

        # A DRAFT FOR A LATER WEEK. Its kickoff really is in that week, or the
        # validator would reject the card before the hub ever saw it.
        future = pick("w4-nfl-01", "nfl", "Baltimore Ravens", "Buffalo Bills",
                      "2026-09-25T00:15:00Z", "spread", team="Baltimore Ravens", line=1.5)
        write_week([future], week="2026-09-22")
        mercer.cmd_render(stores=STORES, now=T_VIEW)
        hub2 = io.open(os.path.join(mercer.OUT, "index.html"), encoding="utf-8").read()
        head = hub2[hub2.find("Mercer&#x27;s card") if "Mercer&#x27;s card" in hub2
                    else hub2.find("Mercer's card"):][:400]
        check("September 8, 2026" in head,
              "[7] a future draft does not displace the live week on the hub")
        check("2026-09-22" in hub2, "[7] the future week is still listed in the archive")

        mercer.save_json(os.path.join(mercer.WEEKS, "2026-09-29.json"),
                         {"slate_week": "2026-09-29", "picks": [{"id": "broken"}]})
        rc = mercer.cmd_render(stores=STORES, now=T_VIEW)
        hub3 = io.open(os.path.join(mercer.OUT, "index.html"), encoding="utf-8").read()
        # "Fixture week" is the card TITLE, which is now withheld until the week
        # fully reveals. Assert instead on content the hub shows either way.
        check(rc == 1 and "Mercer's card" in hub3,
              "a malformed card is skipped and the hub keeps rendering")
        check(not os.path.exists(os.path.join(mercer.OUT, "2026-09-29")),
              "the malformed week gets no page")

        # ---- 13. the suite is hermetic AND still exercises the sealed path --
        #
        # Supplying a key could "fix" the gate two wrong ways: by never reaching
        # the encryption branch at all, or by leaving a readable card behind. So
        # assert the branch was taken, the bytes are real ciphertext, and the
        # guard still bites when the key is removed.
        print("\n[13] sealed-card path, exercised rather than bypassed")
        sealed_week = "2026-10-06"
        doc = {"slate_week": sealed_week, "picks": [dict(P1, id="seal-01")],
               "leans": [], "passes": [], "spotlight": []}
        how = mercer.save_week(sealed_week, doc, revealed=False)
        plain, enc = mercer.week_paths(sealed_week)
        check(how == "encrypted",
              f"save_week took the ENCRYPTED branch, not the local plaintext "
              f"fallback (returned {how!r})")
        check(os.path.exists(enc), "the .enc card exists")
        check(not os.path.exists(plain),
              "and NO plaintext card was left beside it")
        raw = open(enc, "rb").read()
        check(raw.startswith(b"gAAAAA"),
              "the card is real Fernet ciphertext, not JSON in an .enc name")
        check(b"seal-01" not in raw and b"Baltimore" not in raw,
              "the selection is not readable in the sealed bytes")
        check(mercer.load_week(sealed_week)["picks"][0]["id"] == "seal-01",
              "and it decrypts back to exactly what went in")
        # WITHOUT the key the same call returns None rather than the card - the
        # property every public CI render depends on.
        os.environ.pop(crypto_box.ENV_KEY, None)
        try:
            check(mercer.load_week(sealed_week) is None,
                  "with no key the sealed card reads back as None, which is what "
                  "a keyless public render sees")
        finally:
            os.environ[crypto_box.ENV_KEY] = TEST_KEY

        # THE GUARD STILL HAS TEETH. Remove the key, claim to be CI, and the
        # production refusal must fire - this is the check that would have caught
        # the outage, and it must keep working after the suite starts supplying
        # a key of its own.
        _ga = os.environ.get("GITHUB_ACTIONS")
        os.environ.pop(crypto_box.ENV_KEY, None)
        os.environ["GITHUB_ACTIONS"] = "true"
        try:
            refused = False
            try:
                mercer.save_week("2026-10-13", doc, revealed=False)
            except SystemExit as exc:
                refused = "REFUSING" in str(exc) and crypto_box.ENV_KEY in str(exc)
            check(refused,
                  "with the key removed under CI, save_week REFUSES rather than "
                  "writing the card in the clear")
            p2, e2 = mercer.week_paths("2026-10-13")
            check(not os.path.exists(p2) and not os.path.exists(e2),
                  "and it wrote nothing at all when it refused")
        finally:
            os.environ[crypto_box.ENV_KEY] = TEST_KEY
            if _ga is None:
                os.environ.pop("GITHUB_ACTIONS", None)
            else:
                os.environ["GITHUB_ACTIONS"] = _ga
        check(crypto_box.have_key(), "the test key is restored for the rest of the run")

        # The key never escapes the process or the temp tree.
        check(_REAL_KEY != TEST_KEY, "the test key is not the production key")
        leaked = [p for p in (plain, enc) if not p.startswith(tmp)]
        check(not leaked, f"every sealed artifact lives under the temp dir ({leaked})")
        check(_prod_digest() == prod_before,
              "the real data/mercer tree is byte-identical to before the run")
    finally:
        _restore_key()
        if os.environ.get("MERCER_KEEP"):
            print(f"\nMERCER_KEEP set: fixture pages left in {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nmercer selftest: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
