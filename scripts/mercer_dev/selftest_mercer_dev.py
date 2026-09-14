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
        self.committed = True
        self.receipts = []
        self.hooks = []

    def artifact_committed(self, pid): return self.committed
    def delivered(self): return list(self.receipts)

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
        self.hooks.append(hook)
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
    npath = mdcore.registration_path("mercer-ncaaf-dev", 1)
    nreg = mdcore.load_json(npath)
    nreg.update(status="REGISTERED", registration_timestamp_utc="2026-09-20T11:59:00Z",
                effective_utc=EFFECTIVE, code_commit="abcdef1")
    mdcore.save_json(npath, nreg)
    mdcore.register("mercer-ncaaf-dev", 1, T_REG, "dsimny")
    NREG = mdcore.verified_registration("mercer-ncaaf-dev", 1)
    check(NREG[0]["sport"] == "ncaaf" and NREG[1]["sha256"] != REG[1]["sha256"],
          "NCAA registers as its own strategy with its own hash")
    bad = dict(reg, daily_exposure_scope="per_cohort")
    check(raises(lambda: mdcore.validate_registration(bad), mdcore.IntegrityError),
          "a registration capping exposure per cohort instead of across both is refused")
    bad = dict(reg, max_play_units=0.5)
    check(raises(lambda: mdcore.validate_registration(bad), mdcore.IntegrityError),
          "variable sizing above the flat 0.25u is refused")

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
    for name in ("mdcore.py", "mdmarket.py", "mdrelease.py", "mercer_dev.py", "render.py"):
        code = code_only(open(os.path.join(HERE, name), encoding="utf-8").read())
        hits = [bad for bad in FORBIDDEN if bad in code]
        check(not hits, f"{name} code never references another record or the members webhook ({hits or 'none'})")
    # Code only: build_site.py's copy comment names this suite, which is policy
    # text, not an import. The control below proves a real import still trips it.
    MLB_IMPORT = re.compile(r"\b(mdcore|mdmarket|mdrelease|mercer_dev)\b")
    check(bool(MLB_IMPORT.search(code_only("import os\nsys.path.insert(0, 'scripts/mercer_dev')\n"))),
          "negative control: a real mercer_dev path in code is still detected")
    mlb = [p for p in glob.glob(os.path.join(ROOT, "scripts", "*.py"))
           if MLB_IMPORT.search(code_only(open(p, encoding="utf-8").read()))]
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
                        ("Spotlight is the weekly premium featured selection", "Spotlight stays the featured selection"),
                        ("each have a separate developmental record", "NFL and NCAA are separate records"),
                        ("human-researched selections supported by market and analytical data",
                         "human-researched, not a model"),
                        ("counts on that record only", "a Spotlight pick counts once"),
                        ("Developmental releases have not started yet", "cohorts are not described as live"),
                        ("flat-staked at 0.25 units", "flat staking stated"),
                        ("no guaranteed number of picks", "no volume promise"),
                        ("no model edge is claimed", "no model edge claim"),
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

    # ------------------------------------------------------------------ owner decisions 2026-09-14
    def make(eid, market="moneyline", kick=KICK, now=T_BUILD, spotlight=None, **kw):
        ev_ = espn_event(eid=eid, kick=kick)
        q_ = mdmarket.normalize_event_odds(odds_payload(mdcore.iso(now - timedelta(minutes=2))),
                                           "nfl", mdcore.iso(now - timedelta(minutes=2)))
        d_ = draft(espn_event_id=eid, market=market, **kw)
        art, h, fl = mdrelease.build_artifact(d_, REG, q_, ev_, now, spotlight=spotlight)
        return art, h, fl, q_, ev_

    def state_for(art, h, eid, kick=KICK, qnow=None):
        io_ = FakeIO()
        io_.artifacts[art["play_id"]] = copy.deepcopy(art)
        io_.commits = [{"play_id": art["play_id"], "artifact_sha256": h}]
        cap = mdcore.iso((qnow or T_PUB) - timedelta(minutes=1))
        io_.quote = mdmarket.normalize_event_odds(odds_payload(cap), "nfl", cap)
        io_.event = espn_event(eid=eid, kick=kick)
        return io_

    print("\n[X] daily exposure is capped across BOTH cohorts, released plays included")
    ax, hx, fx, _, _ = make("401999101")
    check(not fx, "a second NFL play on the same day builds cleanly")
    same_day = "2026-09-27T20:00:00Z"
    receipts = [{"play_id": f"r{i}", "sport": "ncaaf" if i % 2 else "nfl", "espn_event_id": f"77{i}",
                 "market": "moneyline", "scheduled_start_utc": same_day, "recommended_units": 0.25}
                for i in range(3)]
    led_nfl = mdcore.load_ledger("mercer-nfl-dev")
    led_ncaa = mdcore.load_ledger("mercer-ncaaf-dev")
    appr = {"play_id": ax["play_id"], "artifact_sha256": hx, "approver": "dsimny"}
    commits = [{"play_id": ax["play_id"], "artifact_sha256": hx}]
    q_now = state_for(ax, hx, "401999101").quote
    ev_x = espn_event(eid="401999101")

    def ck(delivered, committed=True):
        return dict((n, o) for n, o, _ in mdrelease.check_release(
            ax, hx, appr, commits, led_nfl, q_now, ev_x, T_PUB, ENV_ON, REG,
            delivered=delivered, artifact_committed=committed, other_ledgers=[led_ncaa]))
    check(ck(receipts[:2])["exposure_limits"],
          "1 ledger play + 2 released (NFL and NCAA) + this one = 1.00u: allowed")
    check(not ck(receipts)["exposure_limits"],
          "1 ledger play + 3 released across both cohorts + this one = 1.25u: refused")
    check(not ck([dict(receipts[0], espn_event_id="401999101")])["no_conflicting_play"],
          "a RELEASED but not-yet-started play on the same event and market is a conflict")
    check(not ck([dict(receipts[0], play_id=ax["play_id"])])["no_duplicate"],
          "a released receipt with the same play id is a duplicate before any ledger row exists")
    check(not ck([], committed=False)["artifact_committed_publicly"],
          "an artifact that is not on origin/main fails its check")
    io_nc = state_for(ax, hx, "401999101")
    io_nc.committed = False
    enable()
    st, _ = mdrelease.publish(ax["play_id"], hx, "dsimny", T_PUB, io_nc, ENV_ON)
    check(st == "refused" and not io_nc.files and not io_nc.sent,
          "publish refuses before any reservation when the artifact was not pushed first")

    print("\n[C] commissioning dry run cannot reach a customer channel")
    mdcore.save_json(mdrelease.CONTROL, {"publication_enabled": False, "sports": {}})
    io_c = state_for(ax, hx, "401999101")
    check(raises(lambda: mdrelease.publish(ax["play_id"], hx, "dsimny", T_PUB, io_c, ENV_ON,
                                           commissioning=True), mdrelease.ReleaseRefused),
          "commissioning refuses to run on anything but the offline double")
    io_c.offline_double = True
    env_c = dict(ENV_ON, MERCER_DEV_PUBLICATION="off")
    st, cks = mdrelease.publish(ax["play_id"], hx, "dsimny", T_PUB, io_c, env_c, commissioning=True)
    cmap = dict((n, o) for n, o, _ in cks)
    check(st == "commissioned" and cmap["kill_switch_off"] is False,
          "with publication OFF the full chain completes as COMMISSIONED")
    check(io_c.hooks == [mdrelease.COMMISSIONING_HOOK] and HOOK not in io_c.hooks
          and not any(h.lower().startswith(("http", "https")) for h in io_c.hooks)
          and not mdrelease.COMMISSIONING_HOOK.lower().startswith("http"),
          "the only 'webhook' used is a non-URL offline marker, even with a real one in the environment")
    check(io_c.sent == [ax["discord_payload"]], "the stored payload bytes went through the chain unchanged")
    io_c2 = state_for(ax, hx, "401999101")
    io_c2.offline_double, io_c2.committed = True, False
    st, _ = mdrelease.publish(ax["play_id"], hx, "dsimny", T_PUB, io_c2, env_c, commissioning=True)
    check(st == "refused", "commissioning still fails on any real check other than the kill switch")
    import mercer_dev as cli
    cio = cli.CommissionIO()
    check(raises(lambda: cio.send(HOOK, ax["discord_payload"]), mdrelease.ReleaseRefused),
          "CommissionIO refuses a real webhook URL outright")
    import ast
    csrc = open(os.path.join(HERE, "mercer_dev.py"), encoding="utf-8").read()
    ctree = ast.parse(csrc)
    cls = next(n for n in ctree.body if isinstance(n, ast.ClassDef) and n.name == "CommissionIO")
    check("requests" not in ast.unparse(cls), "CommissionIO contains no HTTP client at all")
    wf = open(os.path.join(ROOT, ".github", "workflows", "mercer-dev-release.yml"), encoding="utf-8").read()
    step = wf.split("- name: Commission the release chain offline")[1].split("- name:")[0]
    check("DISCORD_WEBHOOK" not in step and "GH_TOKEN" not in step,
          "the commissioning workflow step receives no webhook secret and no write token")

    print("\n[S] a Spotlight pick lands on exactly one record")
    import mercer
    MS = os.path.join(tmp, "mercer")
    mercer.DATA, mercer.WEEKS = MS, os.path.join(MS, "weeks")
    mercer.LEDGER, mercer.COMMITMENTS = os.path.join(MS, "mercer_ledger.json"), os.path.join(MS, "commitments.json")
    mercer.DELIVERIES, mercer.STATUS_PATH = os.path.join(MS, "deliveries.json"), os.path.join(MS, "post_status.json")
    mercer.OUT, mercer.COHORT_DIR = os.path.join(tmp, "football", "mercer"), D
    os.makedirs(mercer.WEEKS)
    EID, K2 = "401999555", "2026-09-27T20:25:00Z"
    SPOT_NOW = P("2026-09-26T12:00:00Z")
    week = mercer.slate_week(P(K2))
    pick = {"id": "w3-nfl-01", "sport": "nfl", "status": "OFFICIAL", "away": AWAY, "home": HOME,
            "kickoff_utc": K2, "market": "moneyline", "team": HOME, "price": -150, "units": 0.25,
            "book": "DraftKings", "conviction": "strong", "espn_event_id": EID,
            "odds_event_id": "oddsev1", "walk_away": {"min_odds": -160},
            "why": "Buffalo's pass rush against a patched Baltimore line.",
            "case_against": "Baltimore's run game travels; the market priced it close.",
            "cohort_play_id": f"mercer-nfl-dev-v1-{EID}-moneyline"}
    ev_s = espn_event(eid=EID, kick=K2)
    check("no backfill" in (mercer.cohort_gate(pick, ev_s, T_REG) or ""),
          "before the cohort is effective, a pick cannot claim a cohort play id")
    check("is now a cohort play" in (mercer.cohort_gate(dict(pick, cohort_play_id=None), ev_s, SPOT_NOW) or ""),
          "after the cohort is effective, an official Spotlight pick without a cohort play id is refused")
    check("must be" in (mercer.cohort_gate(dict(pick, cohort_play_id="mercer-nfl-dev-v1-1-spread"), ev_s, SPOT_NOW) or ""),
          "a cohort play id that does not name this event and market is refused")
    check("0.25u" in (mercer.cohort_gate(dict(pick, units=1), ev_s, SPOT_NOW) or ""),
          "a cohort-owned Spotlight pick must be staked at exactly 0.25u")
    check(mercer.cohort_gate(pick, ev_s, SPOT_NOW) is None, "a correctly linked Spotlight pick passes")
    mercer.save_json(os.path.join(mercer.WEEKS, f"{week}.json"),
                     {"slate_week": week, "title": "t", "intro": "i", "picks": [pick], "leans": [], "passes": []})
    stores_pre = {"nfl": {EID: ev_s}, "ncaaf": {}}
    check(mercer.cmd_commit(week, now=SPOT_NOW, stores=stores_pre) == 0, "the Spotlight card commits")
    scommits = mercer.load_commitments()["commitments"]
    doc = mercer.load_week(week)
    q_s = mdmarket.normalize_event_odds(odds_payload("2026-09-26T11:58:00Z"), "nfl", "2026-09-26T11:58:00Z")
    sdraft, slink = cli.spotlight_draft(week, pick["id"], q_s, doc, scommits)
    a_s, h_s, f_s = mdrelease.build_artifact(sdraft, REG, q_s, ev_s, SPOT_NOW, spotlight=slink)
    check(not f_s and a_s["play_id"] == pick["cohort_play_id"] and a_s["featured"] is True,
          "the Spotlight pick becomes a featured cohort artifact with the matching play id")
    check("SPOTLIGHT" in a_s["discord_payload"]["embeds"][0]["description"],
          "the member message is labelled as the Spotlight featured selection")
    check(sdraft["book"] == "draftkings" and raises(lambda: cli.book_key("Some Offshore Book"), mdrelease.ReleaseRefused),
          "Spotlight book names map explicitly to Tier-1 keys; an unknown book is refused")
    check(raises(lambda: cli.spotlight_draft(week, pick["id"], q_s,
                                             {"picks": [dict(pick, why="edited after commit")]}, scommits),
                 mdrelease.ReleaseRefused), "a Spotlight pick edited after its fingerprint cannot be released")

    sent_hooks = []
    import types
    fake_req = types.ModuleType("requests")
    fake_req.post = lambda *a_, **k_: sent_hooks.append(a_) or (_ for _ in ()).throw(AssertionError("posted"))
    sys.modules["requests"] = fake_req
    os.environ["DISCORD_WEBHOOK_URL_MEMBERS"] = HOOK
    try:
        rc = mercer.cmd_deliver(week, now=SPOT_NOW)
    finally:
        sys.modules.pop("requests", None)
        os.environ.pop("DISCORD_WEBHOOK_URL_MEMBERS", None)
    check(rc == 0 and not sent_hooks, "the hourly Spotlight delivery never sends a cohort-owned pick")

    after = P("2026-09-28T03:00:00Z")
    stores_fin = {"nfl": {EID: espn_event(eid=EID, kick=K2, status="STATUS_FINAL", final=True, hs=27, as_=20)},
                  "ncaaf": {}}
    mercer.cmd_grade(stores=stores_fin, now=after)
    check(mercer.load_ledger()["entries"] == [], "the Spotlight ledger never books the cohort-owned pick")
    check(not mercer.load_commitments()["commitments"][0].get("revealed"),
          "the Spotlight card stays sealed until its cohort ledger has settled the play")
    srec = {"play_id": a_s["play_id"], "artifact_sha256": h_s, "message_id": "9", "published_utc": "2026-09-26T12:30:00Z",
            "approver": "dsimny", "approved_utc": "2026-09-26T12:29:00Z"}
    spub = mdrelease.publication_row(a_s, srec)
    mdcore.append_row("mercer-nfl-dev", spub, registration=REG)
    mdcore.append_row("mercer-nfl-dev", mdrelease.settle_row(spub, stores_fin["nfl"][EID], None, after))
    mercer.cmd_grade(stores=stores_fin, now=after)
    check(mercer.load_ledger()["entries"] == [] and mercer.load_commitments()["commitments"][0].get("revealed") is True,
          "once the cohort settles it, the Spotlight card is revealed - still with nothing booked there")
    on_cohorts = sum(1 for sid in mdcore.STRATEGY_SPORT for r in mdcore.publications(mdcore.load_ledger(sid))
                     if r.get("spotlight_pick_id") == pick["id"])
    on_spot = sum(1 for e in mercer.load_ledger()["entries"] if e["pick_id"] == pick["id"])
    check(on_cohorts + on_spot == 1, f"counted exactly once across every record ({on_cohorts} cohort, {on_spot} Spotlight)")
    check(mdcore.aggregates(mdcore.load_ledger("mercer-ncaaf-dev"))["published"] == 0,
          "and never on the other sport's cohort")
    mercer.cmd_render(stores=stores_fin)
    wkpage = open(os.path.join(mercer.OUT, week, "index.html"), encoding="utf-8").read()
    check("Developmental Cohort</a> record as play" in wkpage, "the Spotlight page says which record holds the pick")

    print("\n[G] prospective grading, closing lines, reveal")
    mdcore.save_json(mdcore.ledger_path("mercer-nfl-dev"), mdcore.empty_ledger("mercer-nfl-dev"))
    ag, hg, _, _, _ = make("401999201")
    asp, hsp, fsp, _, _ = make("401999202", market="spread", selection=HOME, line=-2.5, odds=-110,
                               walk_away={"line_at_least": -3.0, "min_odds": -115})
    arts = {ag["play_id"]: ag, asp["play_id"]: asp}
    rcpts = [{"play_id": x["play_id"], "artifact_sha256": mdcore.sha256_of(x), "message_id": "1",
              "published_utc": "2026-09-27T12:30:00Z", "approver": "dsimny",
              "approved_utc": "2026-09-27T12:29:00Z"} for x in (ag, asp)]
    revealed = []
    res_fin = {"nfl": {e: espn_event(eid=e, status="STATUS_FINAL", final=True, hs=24, as_=20)
                       for e in ("401999201", "401999202")}}
    close_ml = {"reference_probability": 0.63, "line": None, "odds": None}
    rows = mdrelease.grade_all(P("2026-09-27T16:00:00Z"), rcpts, arts.get, res_fin, lambda pub: close_ml, revealed.append)
    check(rows == [] and not revealed, "before kickoff nothing enters the ledger and nothing is revealed")
    rows = mdrelease.grade_all(P("2026-09-27T18:00:00Z"), rcpts, arts.get, {"nfl": {}}, lambda pub: close_ml, revealed.append)
    check([r["row_type"] for r in rows] == ["publication", "publication"] and not revealed,
          "after kickoff, publication rows only; no final means no settlement and no reveal")
    rows = mdrelease.grade_all(P("2026-09-28T02:00:00Z"), rcpts, arts.get, res_fin, lambda pub: close_ml, revealed.append)
    sets_ = {r["play_id"]: r for r in rows if r["row_type"] == "settlement"}
    check(len(sets_) == 2 and len(revealed) == 2, "finals settle both plays and reveal both artifacts")
    check(sets_[ag["play_id"]]["clv_pts"] == round((0.63 - ag["market_implied_probability"]) * 100, 3),
          "moneyline CLV is computed where a closing line exists")
    check(sets_[asp["play_id"]]["clv_pts"] is None and "spreads and totals" in sets_[asp["play_id"]]["clv_unavailable_reason"],
          "spread CLV is marked unavailable with its reason, never estimated")
    again = mdrelease.grade_all(P("2026-09-28T03:00:00Z"), rcpts, arts.get, res_fin, lambda pub: close_ml, lambda x: None)
    check(again == [], "a second grading run appends nothing")
    bad_r = [dict(rcpts[0], artifact_sha256="0" * 64)]
    check(raises(lambda: mdrelease.grade_all(P("2026-09-28T03:00:00Z"), bad_r, arts.get, res_fin,
                                             lambda pub: None, lambda x: None), mdcore.IntegrityError),
          "an artifact that does not match its receipt is refused, not graded")
    agg = mdcore.aggregates(mdcore.load_ledger("mercer-nfl-dev"))
    check(agg["clv"]["n"] == 1 and agg["clv"]["of_graded"] == 2 and len(agg["clv"]["unavailable"]) == 1,
          "aggregates report CLV for 1 of 2 plays and name why the other has none")

    kick_dt = P(KICK)

    def cap(minutes_before, n_books=4, stale=False, home=-160, away=140):
        t = kick_dt - timedelta(minutes=minutes_before)
        lu = t - timedelta(minutes=30 if stale else 2)
        return (t, {"events": [{"odds_api_event_id": "oddsev1", "books": [
            {"book": b, "last_update": mdcore.iso(lu),
             "markets": {"h2h": [{"name": HOME, "price": home}, {"name": AWAY, "price": away}]}}
            for b in TIER1[:n_books]]}]})
    got = mdrelease.closing_from_captures([cap(180, home=-120, away=100), cap(60)], "oddsev1", HOME, AWAY, kick_dt)
    check(got.get("n_books") == 4 and abs(got["reference_probability"] - (0.61538 / (0.61538 + 0.41667))) < 1e-3,
          "closing moneyline uses the LATEST pregame capture")
    check("unavailable" in mdrelease.closing_from_captures([(kick_dt + timedelta(minutes=5), cap(0)[1])],
                                                           "oddsev1", HOME, AWAY, kick_dt),
          "a capture taken after kickoff is never a close")
    check("need 3" in mdrelease.closing_from_captures([cap(30, n_books=2)], "oddsev1", HOME, AWAY, kick_dt)["unavailable"],
          "fewer than 3 fresh Tier-1 books: unavailable, with the count")
    check("unavailable" in mdrelease.closing_from_captures([cap(30, stale=True)], "oddsev1", HOME, AWAY, kick_dt),
          "stale book quotes at the capture are not used")
    check("within 6 h" in mdrelease.closing_from_captures([cap(60 * 8)], "oddsev1", HOME, AWAY, kick_dt)["unavailable"],
          "no capture within 6 hours of kickoff: unavailable")

    print("\n[R] public cohort record")
    import render as rmod
    os.makedirs(os.path.join(tmp, "data", "mercer_dev", "research"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "data", "mercer_dev", "research", "nfl_v1_dev_eval.json"),
                os.path.join(tmp, "data", "mercer_dev", "research", "nfl_v1_dev_eval.json"))
    ap_, hp_, _, _, _ = make("401999301", kick="2026-10-04T17:00:00Z", now=P("2026-10-04T12:00:00Z"))
    mdcore.save_json(os.path.join(D, "deliveries", f"{ap_['play_id']}.delivered.json"),
                     {"play_id": ap_["play_id"], "sport": "nfl", "espn_event_id": "401999301",
                      "market": "moneyline", "scheduled_start_utc": "2026-10-04T17:00:00Z",
                      "recommended_units": 0.25, "featured": True, "artifact_sha256": hp_,
                      "published_utc": "2026-10-04T12:30:00Z"})
    mdcore.save_json(mdrelease.COMMITMENTS, {"commitments": [
        {"play_id": ap_["play_id"], "matchup": "Pending Visitors @ Pending Hosts", "artifact_sha256": hp_}]})
    out = rmod.render(now=P("2026-10-04T13:00:00Z"))
    page = open(out, encoding="utf-8").read()
    check(out.replace("\\", "/").endswith("football/mercer/developmental/index.html"), "writes only the cohort page")
    check("DJ Mercer NFL — Developmental Cohort" in page and "DJ Mercer NCAA — Developmental Cohort" in page,
          "NFL and NCAA each get their own section")
    check("ombined" not in page, "there is no combined NFL+NCAA figure anywhere")
    pend = page.split("Released, game not started")[1].split("</table>")[0]
    check("Pending Visitors @ Pending Hosts" in pend and "-150" not in pend and "draftkings" not in pend.lower(),
          "a released play before kickoff shows proof only - matchup, times, hash - no selection or price")
    check("unavailable — no closing capture exists for spreads and totals" in page,
          "unavailable CLV is printed as unavailable, with the reason")
    check("Publication is off" in page, "the page states publication is off")
    check("no independent edge" in page and "not an untouched test" in page,
          "NFL historical research is shown separately and labelled")
    check("no NCAA model exists" in page, "NCAA states that no model exists")
    check("proven model" in page and "no model edge is claimed" in page and "1-800-GAMBLER" in page,
          "the page disclaims a proven model and carries the gambling-help line")

    print("\n[P] decisions that must not drift")
    import delivery_policy
    check(delivery_policy.PAUSED is True, "the automated football pipeline stays paused")
    ctl_real = json.load(open(os.path.join(ROOT, "data", "mercer_dev", "control.json"), encoding="utf-8"))
    check(ctl_real["publication_enabled"] is False and not any(ctl_real["sports"].values()),
          "committed control.json keeps developmental publication off")
    eng = open(os.path.join(ROOT, "scripts", "engine.py"), encoding="utf-8").read()
    check(re.search(r"^DAILY_PICK_UNITS\s*=\s*0\.0\b", eng, re.M) is not None,
          "the Daily Pick stays at zero units")
    import yaml
    for wfn in sorted(os.listdir(os.path.join(ROOT, ".github", "workflows"))):
        doc_ = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", wfn), encoding="utf-8"))
        trig = doc_.get("on") or doc_.get(True) or {}
        text = json.dumps(doc_)
        if "mercer_live/capture.py" in text:
            check("schedule" not in trig, f"{wfn}: Mercer Live capture is not scheduled")
    check(not os.path.exists(os.path.join(ROOT, "data", "football", "holdout_evaluations.json")),
          "the 2025 NFL holdout has never been claimed")
    for sid in ("mercer-nfl-dev", "mercer-ncaaf-dev"):
        r_ = json.load(open(os.path.join(ROOT, "data", "mercer_dev", "registrations", f"{sid}-v1.json"), encoding="utf-8"))
        check(r_["model_type"].startswith("none.") and "Human-researched" in r_["selection_source"],
              f"{sid}: human-researched, no model")
        check(r_["unit_sizing"]["flat_units"] == 0.25 and r_["max_daily_units"] == 1.0
              and r_["daily_exposure_scope"] == "all_developmental_cohorts",
              f"{sid}: 0.25u flat, 1u per day across all developmental plays")
        check(r_["manual_review"]["required"] is True and "every play" in r_["manual_review"]["period"],
              f"{sid}: Daniel approves every play")
        check("2025 NFL holdout remains unspent" in r_["untouched_test_window"], f"{sid}: holdout unspent")
        check(r_["live_plays_permitted"] is False, f"{sid}: no live plays")
    nfl_r = json.load(open(os.path.join(ROOT, "data", "mercer_dev", "registrations", "mercer-nfl-dev-v1.json"), encoding="utf-8"))
    ncaa_r = json.load(open(os.path.join(ROOT, "data", "mercer_dev", "registrations", "mercer-ncaaf-dev-v1.json"), encoding="utf-8"))
    check("no independent edge" in nfl_r["historical_research"], "NFL registration records the no-edge finding")
    check("No NCAA model exists" in ncaa_r["historical_research"], "NCAA registration says no model exists")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

check(tree_hash(os.path.join(ROOT, "data", "mercer_dev")) == REAL_BEFORE,
      "the real data/mercer_dev tree is byte-identical after the run")
print(f"\nmercer-dev selftest: {n_checks} checks, "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
