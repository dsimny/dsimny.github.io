#!/usr/bin/env python3
"""
D.J. Mercer Developmental Cohorts - hermetic self-test.

    python scripts/mercer_dev/selftest_mercer_dev.py

No network, no secrets, no Discord, temp directory only. The real
data/mercer_dev tree is hashed before and after and must be byte-identical.
Each group is numbered to the requirement it proves.
"""
import copy
import glob
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
os.chdir(ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))
for k in ("MERCER_DEV_PUBLICATION", "MERCER_DEV_APPROVERS", "DISCORD_WEBHOOK_URL_MERCER_PLAYS",
          "DISCORD_WEBHOOK_URL_MERCER_REVIEW", "ODDS_API_KEY", "GITHUB_ACTIONS"):
    os.environ.pop(k, None)

import mdcore      # noqa: E402
import mdmarket    # noqa: E402
import mdrelease   # noqa: E402

fails, n_checks = [], 0


def check(cond, msg):
    global n_checks
    n_checks += 1
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(path, "**", "*"), recursive=True)):
        if os.path.isfile(p):
            h.update(p.replace(ROOT, "").encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()


REAL_BEFORE = tree_hash(os.path.join(ROOT, "data", "mercer_dev"))
P = mdcore.parse_utc
T_REG = P("2026-09-20T12:00:00Z")
EFFECTIVE = "2026-09-21T00:00:00Z"
KICK = "2026-09-27T17:00:00Z"
T_BUILD = P("2026-09-27T12:00:00Z")
T_PUB = P("2026-09-27T12:10:00Z")
HOME, AWAY = "Buffalo Bills", "Baltimore Ravens"
TIER1 = ["betmgm", "betrivers", "draftkings", "fanatics", "fanduel", "williamhill_us"]
HOOK = "https://discord.com/api/webhooks/123/abc"
ENV_ON = {"MERCER_DEV_PUBLICATION": "enabled", "MERCER_DEV_APPROVERS": "dsimny",
          "DISCORD_WEBHOOK_URL_MERCER_PLAYS": HOOK}


def odds_payload(captured, home_ml=-150, away_ml=130, spread=-2.5, sp_price=-110,
                 total=47.5, stamp_offset_min=2, market_stamps=True, books=TIER1):
    stamp = mdcore.iso(P(captured) - timedelta(minutes=stamp_offset_min))
    out = []
    for i, b in enumerate(books):
        mk = [{"key": "h2h", "outcomes": [{"name": HOME, "price": home_ml},
                                          {"name": AWAY, "price": away_ml}]},
              {"key": "spreads", "outcomes": [{"name": HOME, "price": sp_price, "point": spread},
                                              {"name": AWAY, "price": -110, "point": -spread}]},
              {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": total},
                                             {"name": "Under", "price": -110, "point": total}]}]
        if market_stamps:
            for m in mk:
                m["last_update"] = stamp
        out.append({"key": b, "last_update": stamp, "markets": mk})
    return {"id": "oddsev1", "home_team": HOME, "away_team": AWAY,
            "commence_time": KICK, "bookmakers": out}


def espn_event(status="STATUS_SCHEDULED", final=False, hs=None, as_=None, season_type=2,
               kick=KICK, eid="401999001"):
    return {"espn_event_id": eid, "kickoff_utc": kick, "status": status, "final": final,
            "season_type": season_type, "home": "BUF", "away": "BAL",
            "home_name": HOME, "away_name": AWAY, "home_score": hs, "away_score": as_}


def draft(**kw):
    d = {"strategy_id": "mercer-nfl-dev", "version": 1, "sport": "nfl",
         "espn_event_id": "401999001", "odds_event_id": "oddsev1", "market": "moneyline",
         "selection": HOME, "line": None, "odds": -150, "book": "draftkings",
         "recommended_units": 0.25, "confidence": "standard",
         "reasoning": "Buffalo's pass rush against a patched Baltimore line.",
         "key_risks": "Baltimore's run game travels; the market priced it close.",
         "walk_away": {"min_odds": -160}}
    d.update(kw)
    return d


class FakeIO(mdrelease.IO):
    def __init__(self):
        self.files, self.sent, self.artifacts, self.commits = {}, [], {}, []
        self.quote = None
        self.event = None
        self.send_mode = "ok"
        self.verify_mode = "ok"

    def load_artifact(self, pid): return copy.deepcopy(self.artifacts.get(pid))
    def commitments(self): return self.commits
    def fetch_quote(self, sport, oid): return self.quote
    def fetch_event(self, sport, eid): return self.event
    def exists(self, rel): return rel in self.files
    def read(self, rel): return self.files[rel]

    def create_only(self, rel, obj):
        if rel in self.files:
            raise mdrelease.ReleaseRefused("exists")
        self.files[rel] = copy.deepcopy(obj)

    def send(self, hook, payload):
        if self.send_mode == "timeout":
            raise mdrelease.Uncertain("timeout")
        if self.send_mode == "400":
            raise mdrelease.Definitive(400)
        self.sent.append(copy.deepcopy(payload))
        msg = dict(copy.deepcopy(payload), id="555", channel_id="777",
                   timestamp="2026-09-27T12:10:03.000000+00:00")
        if self.send_mode == "altered":
            msg["embeds"][0]["description"] += " tampered"
        return msg

    def fetch_message(self, hook, mid):
        if self.verify_mode == "fail":
            raise mdrelease.Uncertain("verify")
        return dict(copy.deepcopy(self.sent[-1]), id=mid, channel_id="777",
                    timestamp="2026-09-27T12:10:03.000000+00:00")


tmp = tempfile.mkdtemp(prefix="mercerdev")
try:
    D = os.path.join(tmp, "data", "mercer_dev")
    mdcore.DATA = mdrelease.DATA = D
    mdcore.REGISTRATIONS = os.path.join(D, "registrations")
    mdcore.AMENDMENTS = os.path.join(D, "amendments")
    mdcore.REGISTRY_LOG = os.path.join(D, "registry_log.json")
    mdcore.LEDGERS = os.path.join(D, "ledgers")
    mdrelease.CONTROL = os.path.join(D, "control.json")
    mdrelease.COMMITMENTS = os.path.join(D, "commitments.json")
    mdrelease.ARTIFACTS = os.path.join(D, "artifacts")
    mdcore.ROOT = tmp
    shutil.copytree(os.path.join(ROOT, "data", "mercer_dev", "registrations"), mdcore.REGISTRATIONS)

    print("[2] version registration")
    for sid in ("mercer-nfl-dev", "mercer-ncaaf-dev"):
        reg = mdcore.load_json(mdcore.registration_path(sid, 1))
        check(reg["status"] == "PROPOSED" and mdcore.validate_registration(reg),
              f"{sid} v1 ships PROPOSED and carries every required field")
    check(mdcore.active_registration("nfl", T_BUILD) == (None, None),
          "a PROPOSED registration is never active")
    check(raises(lambda: mdcore.register("mercer-nfl-dev", 1, T_REG, "dsimny"), mdcore.IntegrityError),
          "register refuses a file still marked PROPOSED")
    path = mdcore.registration_path("mercer-nfl-dev", 1)
    reg = mdcore.load_json(path)
    reg.update(status="REGISTERED", registration_timestamp_utc="2026-09-20T11:59:00Z",
               effective_utc="2026-09-19T00:00:00Z", code_commit="abcdef1")
    mdcore.save_json(path, reg)
    check(raises(lambda: mdcore.register("mercer-nfl-dev", 1, T_REG, "dsimny"), mdcore.IntegrityError),
          "a backdated effective date is refused (no retroactive version)")
    reg["effective_utc"] = EFFECTIVE
    mdcore.save_json(path, reg)
    entry = mdcore.register("mercer-nfl-dev", 1, T_REG, "dsimny")
    check(entry["sha256"] == mdcore.sha256_of(reg), "registration logs the file's SHA-256")
    check(mdcore.active_registration("nfl", T_REG) == (None, None),
          "not active before its effective time")
    check(mdcore.active_registration("nfl", T_BUILD)[0]["version"] == 1, "active after effective time")
    check(raises(lambda: mdcore.register("mercer-nfl-dev", 1, T_REG, "dsimny"), mdcore.IntegrityError),
          "the same version cannot be registered twice")
    check(raises(lambda: mdcore.amend("mercer-nfl-dev", 1, {"reason": "x",
                                                            "changes": {"odds_limits": {}}}, T_BUILD),
                 mdcore.IntegrityError), "an amendment touching a methodology field is refused")
    check(raises(lambda: mdcore.amend("mercer-nfl-dev", 1, {"changes": {"note": "y"}}, T_BUILD),
                 mdcore.IntegrityError), "an amendment without a reason is refused")
    tampered = dict(reg, max_daily_units=9.0)
    mdcore.save_json(path, tampered)
    check(raises(lambda: mdcore.verified_registration("mercer-nfl-dev", 1), mdcore.IntegrityError),
          "a registration edited after registration is refused everywhere")
    mdcore.save_json(path, reg)
    REG = mdcore.verified_registration("mercer-nfl-dev", 1)

    print("\n[7] market normalisation")
    check(raises(lambda: mdmarket.normalize_event_odds({"home_team": HOME}, "nfl", "x"),
                 mdmarket.QuoteError), "a payload with no event id is refused")
    bad = odds_payload("2026-09-27T11:58:00Z")
    bad["bookmakers"][0]["markets"][0]["outcomes"] = bad["bookmakers"][0]["markets"][0]["outcomes"][:1]
    q = mdmarket.normalize_event_odds(bad, "nfl", "2026-09-27T11:58:00Z")
    check("moneyline" not in q["books"]["betmgm"]["markets"],
          "a one-sided moneyline is dropped, never half-used")
    q = mdmarket.normalize_event_odds(odds_payload("2026-09-27T11:58:00Z"), "nfl", "2026-09-27T11:58:00Z")
    check(q["books"]["draftkings"]["markets"]["spread"][HOME] == [-2.5, -110],
          "spread stored as [point, price] per team")
    check(q["books"]["draftkings"]["markets"]["total"]["over"] == [47.5, -110], "totals keyed over/under")

    print("\n[8] odds freshness")
    fb, fallback = mdmarket.fresh_books(q, "moneyline", 15, T_BUILD)
    check(len(fb) == 6 and not fallback, "six fresh Tier-1 books with per-market stamps")
    stale = mdmarket.normalize_event_odds(odds_payload("2026-09-27T11:58:00Z", stamp_offset_min=40),
                                          "nfl", "2026-09-27T11:58:00Z")
    check(not mdmarket.fresh_books(stale, "moneyline", 15, T_BUILD)[0], "quotes older than 15 min are excluded")
    nostamp = mdmarket.normalize_event_odds(odds_payload("2026-09-27T11:58:00Z", market_stamps=False),
                                            "nfl", "2026-09-27T11:58:00Z")
    check(mdmarket.fresh_books(nostamp, "moneyline", 15, T_BUILD)[1],
          "a missing per-market timestamp is reported, not hidden")

    print("\n[6] event and team mapping")
    ev = espn_event()
    art_ev = {"espn_event_id": "401999001", "odds_event_id": "oddsev1", "home": HOME, "away": AWAY}
    check(not mdmarket.identity_problems("nfl", q, ev, art_ev), "matching quote, ESPN event and artifact")
    swapped = dict(ev, home="BAL", away="BUF")
    check(mdmarket.identity_problems("nfl", q, swapped, art_ev), "home/away swapped is caught")
    check(mdmarket.identity_problems("nfl", q, dict(ev, espn_event_id="1"), art_ev),
          "a different ESPN event id is caught")
    odd = copy.deepcopy(q); odd["home"] = "Buffalo Billz"
    check(mdmarket.identity_problems("nfl", odd, ev, art_ev), "an unknown team name is caught, not guessed")
    check(not mdmarket.identity_problems("nfl", q, dict(ev, kickoff_utc="2026-09-27T17:25:00Z"), art_ev),
          "identity never depends on kickoff time (the incident's duplicate cause)")

    print("\n[28] artifact build and barred claims")
    a, sha, failed = mdrelease.build_artifact(draft(), REG, q, ev, T_BUILD)
    check(not failed, f"clean draft passes every circuit breaker ({failed or 'none failed'})")
    check(sha == mdcore.sha256_of(a) and a["discord_payload"] == mdrelease.render_payload(a),
          "artifact hash covers the stored render")
    check(raises(lambda: mdrelease.build_artifact(draft(reasoning="This is a lock, proven system."),
                                                  REG, q, ev, T_BUILD), mdrelease.ReleaseRefused),
          "reasoning claiming a lock / proven system is refused")
    a2, _, f2 = mdrelease.build_artifact(draft(odds=-400, walk_away={"min_odds": -450}), REG, q, ev, T_BUILD)
    check("odds_window" in f2, "odds outside the registered window fails its breaker")
    a3, _, f3 = mdrelease.build_artifact(draft(market="spread", selection=HOME, line=-2.5, odds=-110,
                                               walk_away={"line_at_least": -3.0, "min_odds": -115}),
                                         REG, q, ev, T_BUILD)
    check(not f3 and a3["model_probability"] == 0.5, "spread consensus at the exact number")
    check(raises(lambda: mdrelease.build_artifact(draft(market="moneyline", line=-2.5), REG, q, ev, T_BUILD),
                 mdrelease.ReleaseRefused), "a moneyline carrying a line is refused")
    text = json.dumps(a["discord_payload"])
    for want in ("DJ MERCER", "NFL", "Developmental Cohort v1", "PREGAME", "Baltimore Ravens @ Buffalo Bills",
                 "-150", "draftkings", "0.25u", a["play_id"], "1-800-GAMBLER", "not proven"):
        check(want in text, f"payload shows {want!r}")
    check(not mdrelease.banned_phrases(a["discord_payload"]["embeds"][0]["description"]),
          "the rendered payload itself makes no barred claim")
    live = dict(a, designation="live")
    check(mdrelease.LIVE_WARNING in mdrelease.render_payload(live)["embeds"][0]["description"],
          "a live render carries the verify-the-market warning")

    def fresh_state(**qkw):
        io_ = FakeIO()
        io_.artifacts[a["play_id"]] = copy.deepcopy(a)
        io_.commits = [{"play_id": a["play_id"], "artifact_sha256": sha}]
        io_.quote = mdmarket.normalize_event_odds(odds_payload("2026-09-27T12:09:00Z", **qkw),
                                                  "nfl", "2026-09-27T12:09:00Z")
        io_.event = espn_event()
        return io_

    def enable(sports=("nfl",)):
        mdcore.save_json(mdrelease.CONTROL, {"publication_enabled": True,
                                             "sports": {s: True for s in sports}})

    print("\n[14] kill switch")
    io1 = fresh_state()
    st, checks = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io1, ENV_ON)
    check(st == "refused" and not io1.sent and not io1.files,
          "control.json absent (default OFF): refused, nothing reserved or sent")
    enable()
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io1, dict(ENV_ON, MERCER_DEV_PUBLICATION="off"))
    check(st == "refused" and not io1.sent, "repository variable not 'enabled': refused")
    enable(sports=("ncaaf",))
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io1, ENV_ON)
    check(st == "refused" and not io1.sent, "switch enabled for the other sport only: refused")
    enable()

    print("\n[13] manual approval")
    st, _ = mdrelease.publish(a["play_id"], sha, "someone_else", T_PUB, fresh_state(), ENV_ON)
    check(st == "refused", "an approver not on MERCER_DEV_APPROVERS is refused")
    st, _ = mdrelease.publish(a["play_id"], "0" * 64, "dsimny", T_PUB, fresh_state(), ENV_ON)
    check(st == "refused", "approving a different hash is refused")

    print("\n[dry] preview / dry-run cannot dispatch")
    io2 = fresh_state()
    st, checks = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io2, ENV_ON, dry_run=True)
    check(st == "dry_run" and all(ok for _, ok, _ in checks), "dry-run runs every check and they pass")
    check(not io2.sent and not io2.files, "dry-run sends nothing and writes nothing")

    print("\n[19] missing credentials")
    io3 = fresh_state()
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io3,
                              {k: v for k, v in ENV_ON.items() if k != "DISCORD_WEBHOOK_URL_MERCER_PLAYS"})
    check(st == "refused" and not io3.files, "no webhook: refused BEFORE any reservation")
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io3,
                              dict(ENV_ON, DISCORD_WEBHOOK_URL_MERCER_PLAYS="https://evil.example/x"))
    check(st == "refused" and not io3.sent, "a non-discord.com webhook is refused")

    print("\n[18] workflow retry")
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, fresh_state(), ENV_ON, run_attempt="2")
    check(st == "refused", "a workflow re-run attempt never publishes")

    print("\n[20] stale artifact rejection")
    io4 = fresh_state(home_ml=-170, away_ml=145)
    st, checks = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io4, ENV_ON)
    check(st == "refused" and ("book_still_offers_price", False, "never substitute a worse price or number")
          in checks, "the price moved worse: refused, never substituted")
    io5 = fresh_state()
    io5.event = espn_event(kick="2026-09-27T20:25:00Z")
    st, checks = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io5, ENV_ON)
    check(st == "refused" and not dict((n, o) for n, o, _ in checks)["start_time_unchanged"],
          "kickoff moved by more than 15 minutes: re-approval required")
    io6 = fresh_state()
    io6.commits.append({"play_id": a["play_id"], "artifact_sha256": "f" * 64})
    st, checks = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io6, ENV_ON)
    check(st == "refused", "a superseded commitment (a newer artifact exists) is refused")
    io7 = fresh_state()
    io7.quote["captured_utc"] = "2026-09-27T11:40:00Z"
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io7, ENV_ON)
    check(st == "refused", "a quote older than 15 minutes at publication is refused")
    io8 = fresh_state()
    tam = copy.deepcopy(a); tam["discord_payload"]["embeds"][0]["description"] += "\nextra"
    io8.artifacts[a["play_id"]] = tam
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io8, ENV_ON)
    check(st == "refused" and not io8.sent, "an artifact whose stored payload was edited is refused")

    print("\n[9] pregame versus live")
    io9 = fresh_state()
    io9.event = espn_event(status="STATUS_IN_PROGRESS")
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io9, ENV_ON)
    check(st == "refused", "a game already in progress is refused")
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", P("2026-09-27T16:45:00Z"), fresh_state(), ENV_ON)
    check(st == "refused", "inside the 30-minute pregame lead is refused")

    print("\n[15/16] payload integrity and idempotent posting")
    io10 = fresh_state()
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io10, ENV_ON)
    check(st == "published", "all checks pass: published")
    check(io10.sent == [a["discord_payload"]], "the exact stored payload was sent, once")
    paths = mdrelease.delivery_paths(a["play_id"])
    rec = io10.files[paths["delivered"]]
    check(rec["verified"] and rec["message_id"] == "555" and rec["artifact_sha256"] == sha,
          "receipt records verified message id and artifact hash")
    check(io10.files[paths["approval"]]["approver"] == "dsimny", "approval recorded with approver")
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io10, ENV_ON)
    check(st == "refused" and len(io10.sent) == 1, "a second publish of the same play is refused")
    receipt_str = json.dumps({k: v for k, v in io10.files.items()})
    check("-150" not in receipt_str and "draftkings" not in receipt_str,
          "reservation, approval and receipt carry no actionable fields")
    io11 = fresh_state(); io11.send_mode = "altered"
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io11, ENV_ON)
    check(st == "uncertain" and paths["delivered"] not in io11.files,
          "an acknowledgement that differs from the payload is UNCERTAIN, no receipt")

    print("\n[17] failed Discord response and uncertain sends")
    io12 = fresh_state(); io12.send_mode = "timeout"
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io12, ENV_ON)
    check(st == "uncertain", "a timeout is uncertain")
    io12.send_mode = "ok"
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io12, ENV_ON)
    check(st == "refused" and not io12.sent, "after an uncertain send, every retry is refused")
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io12, ENV_ON,
                              retry_after_definitive_failure=True)
    check(st == "refused" and not io12.sent, "the retry flag cannot override an UNCERTAIN attempt")
    io13 = fresh_state(); io13.send_mode = "400"
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io13, ENV_ON)
    check(st == "failed" and paths["failed"] in io13.files, "HTTP 400 is recorded as a definitive failure")
    io13.send_mode = "ok"
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io13, ENV_ON)
    check(st == "refused", "a definitive failure still needs the explicit retry flag")
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io13, ENV_ON,
                              retry_after_definitive_failure=True)
    check(st == "published" and mdrelease.delivery_paths(a["play_id"], 2)["reserved"] in io13.files,
          "with the flag, attempt 2 reserves separately and publishes")
    io14 = fresh_state(); io14.verify_mode = "fail"
    st, _ = mdrelease.publish(a["play_id"], sha, "dsimny", T_PUB, io14, ENV_ON)
    check(st == "uncertain", "sent but not verifiable: uncertain, never retried")

    print("\n[1/4] ledgers: separation, no backfill, append-only")
    pub = mdrelease.publication_row(a, rec)
    check(raises(lambda: mdcore.append_row("mercer-ncaaf-dev", pub, registration=REG), mdcore.IntegrityError),
          "an NFL play cannot enter the NCAA ledger")
    early = dict(pub, published_utc="2026-09-20T13:00:00Z")
    check(raises(lambda: mdcore.append_row("mercer-nfl-dev", early, registration=REG), mdcore.IntegrityError),
          "a play published before the effective date is refused (no backfill)")
    check(raises(lambda: mdcore.append_row("mercer-nfl-dev", pub, registration=None), mdcore.IntegrityError),
          "no registration, no publication row")
    livepub = dict(pub, designation="live")
    check(raises(lambda: mdcore.append_row("mercer-nfl-dev", livepub, registration=REG), mdcore.IntegrityError),
          "a live play is refused when the registration forbids live plays")
    late = dict(pub, published_utc="2026-09-27T17:01:00Z")
    check(raises(lambda: mdcore.append_row("mercer-nfl-dev", late, registration=REG), mdcore.IntegrityError),
          "a pregame play published after its start is refused")
    mdcore.append_row("mercer-nfl-dev", pub, registration=REG)
    check(raises(lambda: mdcore.append_row("mercer-nfl-dev", pub, registration=REG), mdcore.IntegrityError),
          "a duplicate play id is refused in the ledger too")
    check(mdcore.aggregates(mdcore.load_ledger("mercer-ncaaf-dev"))["published"] == 0,
          "the NCAA record is untouched by NFL activity")
    rows = mdcore.load_ledger("mercer-nfl-dev")["rows"]
    edited = copy.deepcopy(rows); edited[0]["odds"] = -110
    check(raises(lambda: mdcore.assert_append_only(rows, edited), mdcore.IntegrityError),
          "editing an existing row is detected")
    check(raises(lambda: mdcore.assert_append_only(rows, []), mdcore.IntegrityError),
          "removing a row is detected")

    print("\n[11/12] conflicting plays and exposure")
    led = mdcore.load_ledger("mercer-nfl-dev")
    other = copy.deepcopy(a); other["play_id"] = "x-other"; other["selection"] = AWAY
    other["discord_payload"] = mdrelease.render_payload(other)
    ck = dict((n, o) for n, o, _ in mdrelease.check_release(
        other, mdcore.sha256_of(other), {"play_id": "x-other", "artifact_sha256": mdcore.sha256_of(other),
                                         "approver": "dsimny"},
        [{"play_id": "x-other", "artifact_sha256": mdcore.sha256_of(other)}], led,
        fresh_state().quote, espn_event(), T_PUB, ENV_ON, REG))
    check(not ck["no_conflicting_play"], "the opposite side of an already-published event+market is refused")
    big = copy.deepcopy(a); big["play_id"] = "x-big"; big["recommended_units"] = 0.9
    big["event"] = dict(big["event"], espn_event_id="401999777")
    big["discord_payload"] = mdrelease.render_payload(big)
    ck = dict((n, o) for n, o, _ in mdrelease.check_release(
        big, mdcore.sha256_of(big), {"play_id": "x-big", "artifact_sha256": mdcore.sha256_of(big),
                                     "approver": "dsimny"},
        [{"play_id": "x-big", "artifact_sha256": mdcore.sha256_of(big)}], led,
        fresh_state().quote, espn_event(), T_PUB, ENV_ON, REG))
    check(not ck["exposure_limits"], "a play above max_play_units / max_daily_units is refused")

    print("\n[21/22/23] grading, voids, closing-line value")
    win = mdrelease.settle_row(pub, espn_event(status="STATUS_FINAL", final=True, hs=27, as_=20),
                               {"reference_probability": 0.62, "odds": -165}, P("2026-09-28T01:00:00Z"))
    check(win["result"] == "WIN" and abs(win["profit_units"] - 0.1667) < 1e-4,
          "moneyline win at -150 on 0.25u pays 0.1667u")
    check(win["clv_pts"] == round((0.62 - a["market_implied_probability"]) * 100, 3),
          "CLV = closing consensus minus implied probability of the price taken")
    nocl = mdrelease.settle_row(pub, espn_event(status="STATUS_FINAL", final=True, hs=17, as_=20),
                                None, P("2026-09-28T01:00:00Z"))
    check(nocl["result"] == "LOSS" and nocl["profit_units"] == -0.25 and nocl["clv_pts"] is None,
          "a loss books -0.25u and a missing close leaves CLV empty, not zero")
    sp = dict(pub, market="spread", line=-2.5, odds=-110)
    check(mdrelease.settle_row(sp, espn_event(status="STATUS_FINAL", final=True, hs=22, as_=20),
                               None, P("2026-09-28T01:00:00Z"))["result"] == "LOSS",
          "spread -2.5 winning by 2 loses")
    tot = dict(pub, market="total", selection="under", line=42.0, odds=-110)
    check(mdrelease.settle_row(tot, espn_event(status="STATUS_FINAL", final=True, hs=22, as_=20),
                               None, P("2026-09-28T01:00:00Z"))["result"] == "PUSH",
          "total 42 landing on 42 is a push")
    check(mdrelease.settle_row(pub, espn_event(status="STATUS_POSTPONED"), None,
                               P("2026-09-28T01:00:00Z"))["void_reason"] == "game postponed",
          "a postponed game voids with its reason")
    check(mdrelease.settle_row(pub, espn_event(season_type=3, status="STATUS_FINAL", final=True, hs=1, as_=0),
                               None, P("2026-09-28T01:00:00Z"))["result"] == "VOID",
          "a non-regular-season game voids")
    check(mdrelease.settle_row(pub, espn_event(), None, P("2026-09-28T01:00:00Z")) is None,
          "not final yet and inside 7 days: stays pending")
    check(mdrelease.settle_row(pub, espn_event(), None, P("2026-10-05T00:00:00Z"))["result"] == "VOID",
          "not final 7 days after start: voids with a reason")
    mdcore.append_row("mercer-nfl-dev", nocl)
    check(raises(lambda: mdcore.append_row("mercer-nfl-dev", win), mdcore.IntegrityError),
          "a second settlement without an explicit correction is refused")
    check(raises(lambda: mdcore.append_row("mercer-nfl-dev", dict(win, result="VOID", void_reason=None,
                                                                   corrects="x")), mdcore.IntegrityError),
          "a VOID without a reason is refused")
    mdcore.append_row("mercer-nfl-dev", dict(win, corrects=nocl["graded_utc"]))
    agg = mdcore.aggregates(mdcore.load_ledger("mercer-nfl-dev"))
    check(agg["record"] == "1-0" and len(mdcore.load_ledger("mercer-nfl-dev")["rows"]) == 3,
          "a correction supersedes in aggregates while the original row stays")

    print("\n[5] data-leakage guards")
    import asof
    check(raises(lambda: asof.assert_season_allowed(2025, purpose="test")),
          "asof refuses the restricted 2025 season")
    import nfl_v1_eval as ev_mod
    fake_hist = os.path.join(tmp, "hist"); os.makedirs(fake_hist)
    for req in ("2024-12-01T17:00:00Z", "2025-09-07T17:00:00Z"):
        with open(os.path.join(fake_hist, req.replace(":", "") + ".json"), "w") as f:
            json.dump({"requested_utc": req, "events": []}, f)
    ev_mod.HIST = fake_hist
    check(list(ev_mod.load_snapshots()) == ["2024-12-01T17:00:00Z"],
          "the evaluation never loads an odds snapshot from the restricted season")
    src = open(os.path.join(HERE, "nfl_v1_eval.py"), encoding="utf-8").read()
    feat = src.split("def build_rows")[1].split("def _int")[0]
    check("market_reference_tierC" not in src, "the evaluation never reads untimestamped Tier C lines")
    check(re.search(r'"p_close"', feat) and "p_close" not in src.split("def with_model")[1].split("def select")[0],
          "the closing price is carried for CLV only, never into the model blend")

    print("\n[1/25/26] source-level separation and preservation")
    import ast

    def code_only(source):
        """Executable code only: comments and docstrings are policy text, not code."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0] = ast.Pass()
        return ast.unparse(tree)

    FORBIDDEN = ("football_ledger.json", "mercer_ledger.json", "daily_ledger.json",
                 "totals_ledger.json", "watchlist.json", "'ledger.json'", "DISCORD_WEBHOOK_URL_MEMBERS")
    control = code_only('"""never touches football_ledger.json"""\nP = "football_ledger.json"\n')
    check("football_ledger.json" in control and "never touches" not in control,
          "negative control: the scan still sees a real reference and ignores the docstring")
    for name in ("mdcore.py", "mdmarket.py", "mdrelease.py", "mercer_dev.py"):
        code = code_only(open(os.path.join(HERE, name), encoding="utf-8").read())
        hits = [bad for bad in FORBIDDEN if bad in code]
        check(not hits, f"{name} code never references another record or the members webhook ({hits or 'none'})")
    mlb = [p for p in glob.glob(os.path.join(ROOT, "scripts", "*.py"))
           if "mercer_dev" in open(p, encoding="utf-8").read()]
    check(not mlb, f"no MLB script imports the Mercer developmental code ({mlb or 'none'})")
    import record_policy
    inc = "da33da68ad7e9f444c18dc1337c81af4fc172a22a361bc83d6b65f6f92e55a34"
    check(inc in record_policy.INVALIDATED_BOARD_SHA256, "the Sept 8 incident board stays classified")
    fl = json.load(open(os.path.join(ROOT, "data", "football", "football_ledger.json"), encoding="utf-8"))
    check(sum(1 for r in fl["entries"] if r.get("board_sha256") == inc) == 2,
          "both incident rows are still present in the football ledger")
    print("\n[24] website and member-facing disclosures (scoped to the copy blocks)")
    bs = open(os.path.join(ROOT, "scripts", "build_site.py"), encoding="utf-8").read()
    up = bs.split("upgrade_block = f'''")[1].split("''' if PREMIUM_URL")[0]
    up_text = re.sub(r"<[^>]+>", "", up).replace("\n", " ")
    up_text = re.sub(r"\s+", " ", up_text)
    for want, label in (("paused following the September 8 incident", "the automated pipeline is paused"),
                        ("D.J. Mercer Spotlight", "what football members actually receive"),
                        ("developmental strategies are in preparation and are not publishing yet",
                         "cohorts are not described as live"),
                        ("no guaranteed number of picks", "no volume promise"),
                        ("No football selection is presented as proven", "an explicit no-proven-claim sentence")):
        check(want in up_text, f"homepage premium block: {label}")
    check("every covered game" not in up_text and "the one play we would act on" not in up_text,
          "homepage no longer sells the paused automated slate")
    check(not re.search(r"(?<!as )\bproven\b(?! or)", up_text),
          "homepage premium block never calls football proven")
    pg = open(os.path.join(ROOT, "scripts", "football", "page.py"), encoding="utf-8").read()
    check("schedule remains in force" not in pg, "football hub no longer says the paused schedule is in force")
    check("It is sold as process and receipts" not in pg, "football hub no longer sells the paused product")
    stale = [f for f in ("build_site.py", "feed.py", "blog.py", "post_discord.py", "post_pickem.py")
             if re.search(r"ends September 8|0u PROVING|0u proving|proving window\*\*",
                          open(os.path.join(ROOT, "scripts", f), encoding="utf-8").read())]
    check(not stale, f"no surface still says the Daily Pick proving window ends September 8 ({stale or 'none'})")
    mp = open(os.path.join(ROOT, "scripts", "football", "mercer.py"), encoding="utf-8").read()
    check("1-800-GAMBLER" in mp.split("MEMBER_FOOTER = (")[1].split(")")[0],
          "Mercer member posts carry the responsible-gambling footer")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

check(tree_hash(os.path.join(ROOT, "data", "mercer_dev")) == REAL_BEFORE,
      "the real data/mercer_dev tree is byte-identical after the run")
print(f"\nmercer-dev selftest: {n_checks} checks, "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
