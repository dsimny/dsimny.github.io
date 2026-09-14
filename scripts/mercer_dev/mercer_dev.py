#!/usr/bin/env python3
"""
D.J. Mercer Developmental Cohorts - command line and real I/O.

    python scripts/mercer_dev/mercer_dev.py status
    python scripts/mercer_dev/mercer_dev.py candidate --draft drafts/x.json     (local; seals + commits hash)
    python scripts/mercer_dev/mercer_dev.py preview   --play-id ID              (review channel / console)
    python scripts/mercer_dev/mercer_dev.py publish   --play-id ID --sha HASH [--dry-run]
    python scripts/mercer_dev/mercer_dev.py grade
    python scripts/mercer_dev/mercer_dev.py register  --strategy mercer-nfl-dev --version 1 --approved-by NAME

PUBLIC LOGS. GitHub Actions logs of this repository are public. Nothing
actionable (side, line, price, book, reasoning) is ever printed in CI before
the play is graded. Previews in CI go only to DISCORD_WEBHOOK_URL_MERCER_REVIEW.

NOTHING PUBLISHES BY DEFAULT. Publication needs, all at once: a REGISTERED and
effective strategy, data/mercer_dev/control.json enabled for the sport, the
repository variable MERCER_DEV_PUBLICATION=enabled, an approver on
MERCER_DEV_APPROVERS supplying the exact artifact hash, and every release check.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "football"))

import mdcore                                        # noqa: E402
import mdmarket                                      # noqa: E402
import mdrelease                                     # noqa: E402

ODDS_SPORT = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}
CREDIT_LOG = os.path.join(mdcore.ROOT, "data", "odds_credits.json")
IN_CI = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
UA = {"User-Agent": "OpenLedgerSports/1.0 (mercer-dev)"}


# --- real I/O ---------------------------------------------------------------------

class RealIO(mdrelease.IO):
    """Local filesystem for create-only records, or the GitHub contents API in CI.

    The contents API's create call refuses a path that already exists, which is
    what makes a reservation atomic across concurrent or repeated workflow runs.
    """

    def __init__(self, remote=IN_CI):
        self.remote = remote

    # records
    def create_only(self, relpath, obj):
        raw = (json.dumps(obj, sort_keys=True, indent=1) + "\n").encode("utf-8")
        if self.remote:
            body = {"message": f"mercer-dev: record {os.path.basename(relpath)}",
                    "branch": "main", "content": base64.b64encode(raw).decode()}
            r = subprocess.run(["gh", "api", "--method", "PUT",
                                f"repos/{os.environ['GITHUB_REPOSITORY']}/contents/{relpath}",
                                "--input", "-"], input=json.dumps(body), text=True,
                               capture_output=True)
            if r.returncode:
                raise mdrelease.ReleaseRefused(f"create-only write refused for {relpath}")
            return
        path = os.path.join(mdcore.ROOT, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)

    def exists(self, relpath):
        if self.remote:
            r = subprocess.run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}"
                                f"/contents/{relpath}?ref=main", "--silent"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return True
            if "404" in (r.stderr or "") or "Not Found" in (r.stderr or ""):
                return False
            raise mdrelease.ReleaseRefused(f"could not determine whether {relpath} exists")
        return os.path.exists(os.path.join(mdcore.ROOT, relpath))

    def read(self, relpath):
        if self.remote:
            r = subprocess.run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}"
                                f"/contents/{relpath}?ref=main", "--jq", ".content"],
                               capture_output=True, text=True)
            if r.returncode:
                raise mdrelease.ReleaseRefused(f"could not read {relpath}")
            return json.loads(base64.b64decode(r.stdout.strip()))
        return mdcore.load_json(os.path.join(mdcore.ROOT, relpath))

    # artifacts
    def load_artifact(self, play_id):
        import crypto_box
        path = os.path.join(mdrelease.ARTIFACTS, f"{play_id}.enc")
        if not os.path.exists(path) or not crypto_box.have_key():
            return None
        return crypto_box.decrypt_from(path)

    def commitments(self):
        return mdcore.load_json(mdrelease.COMMITMENTS, {"commitments": []})["commitments"]

    # network
    def fetch_quote(self, sport, odds_event_id):
        import requests
        import odds_credits
        key = os.environ.get("ODDS_API_KEY", "").strip()
        if not key:
            raise mdrelease.ReleaseRefused("ODDS_API_KEY is not set")
        stamp = mdcore.iso(mdcore.now_utc())
        r = requests.get(f"https://api.the-odds-api.com/v4/sports/{ODDS_SPORT[sport]}/events/"
                         f"{odds_event_id}/odds", timeout=30,
                         params={"apiKey": key, "regions": "us", "markets": "h2h,spreads,totals",
                                 "oddsFormat": "american", "dateFormat": "iso"})
        odds_credits.record({"read_utc": stamp, "source": f"mercer_dev:{sport}",
                             "http_status": r.status_code, "markets": "h2h,spreads,totals",
                             "regions": "us",
                             "remaining": _int(r.headers.get("x-requests-remaining")),
                             "used": _int(r.headers.get("x-requests-used")),
                             "last_call_cost": _int(r.headers.get("x-requests-last"))},
                            path=CREDIT_LOG)
        if r.status_code != 200:
            raise mdrelease.ReleaseRefused(f"odds request failed: HTTP {r.status_code}")
        return mdmarket.normalize_event_odds(r.json(), sport, mdcore.iso(mdcore.now_utc()))

    def fetch_event(self, sport, espn_event_id):
        import espn_nfl
        import espn_ncaaf
        mod = espn_nfl if sport == "nfl" else espn_ncaaf
        today = mdcore.now_utc().astimezone(mdcore.ET).date()
        for delta in range(-1, 8):
            day = (today + timedelta(days=delta)).strftime("%Y%m%d")
            for ev in mod.fetch_day(day):
                if str(ev.get("id")) == str(espn_event_id):
                    return mod.extract(ev)
        raise mdrelease.ReleaseRefused("ESPN event not found within the next week")

    def send(self, webhook, payload):
        import requests
        try:
            r = requests.post(webhook + "?wait=true", json=payload, headers=UA, timeout=30)
        except Exception:
            raise mdrelease.Uncertain("transport error") from None
        if 400 <= r.status_code < 500:
            raise mdrelease.Definitive(r.status_code)
        if r.status_code != 200:
            raise mdrelease.Uncertain(f"HTTP {r.status_code}")
        return r.json()

    def fetch_message(self, webhook, message_id):
        import requests
        r = requests.get(f"{webhook}/messages/{message_id}", headers=UA, timeout=30)
        if r.status_code != 200:
            raise mdrelease.Uncertain(f"verification HTTP {r.status_code}")
        return r.json()


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# --- commands ---------------------------------------------------------------------

def cmd_status():
    for sport, meta in mdcore.SPORTS.items():
        sid = meta["strategy_id"]
        reg, entry = mdcore.active_registration(sport, mdcore.now_utc())
        agg = mdcore.aggregates(mdcore.load_ledger(sid))
        state = f"v{reg['version']} REGISTERED" if reg else "not registered (nothing may publish)"
        print(f"{meta['name']}: {state}")
        print(f"  record {agg['record']}  published {agg['published']}  pending {agg['pending']}  "
              f"units {agg['units']}  CLV {agg['clv']['mean_pts']}")
    ctl, env_on = mdrelease.control_state(os.environ)
    print(f"control.json publication_enabled={ctl.get('publication_enabled')} "
          f"sports={ctl.get('sports')}  {mdrelease.PUBLICATION_ENV}={'enabled' if env_on else 'off'}")
    return 0


def cmd_candidate(draft_path):
    import crypto_box
    if IN_CI:
        print("candidate runs locally only: the draft is actionable and must not touch CI.")
        return 2
    if not crypto_box.have_key():
        print("BOARD_ENCRYPTION_KEY is required: an artifact is never written in plaintext.")
        return 2
    d = mdcore.load_json(draft_path)
    now = mdcore.now_utc()
    reg = mdcore.active_registration(d["sport"], now)
    if reg == (None, None):
        print(f"{d['sport']}: no registered, effective strategy; nothing can be built for release.")
        return 1
    io = RealIO(remote=False)
    quote = io.fetch_quote(d["sport"], d["odds_event_id"])
    event = io.fetch_event(d["sport"], d["espn_event_id"])
    a, sha, failed = mdrelease.build_artifact(d, reg, quote, event, now)
    os.makedirs(mdrelease.ARTIFACTS, exist_ok=True)
    crypto_box.encrypt_to(os.path.join(mdrelease.ARTIFACTS, f"{a['play_id']}.enc"), a)
    log = mdcore.load_json(mdrelease.COMMITMENTS, {
        "_note": ("Append-only. SHA-256 of each sealed Mercer developmental artifact, "
                  "committed before release. Proof only: no side, line, price, book "
                  "or reasoning. A later entry for the same play supersedes an earlier "
                  "one before release; both stay."), "commitments": []})
    log["commitments"].append({"play_id": a["play_id"], "strategy_id": a["strategy_id"],
                               "version": a["version"], "sport": a["sport"],
                               "espn_event_id": a["event"]["espn_event_id"],
                               "matchup": a["matchup"], "market": a["market"],
                               "scheduled_start_utc": a["event"]["scheduled_start_utc"],
                               "artifact_sha256": sha, "committed_utc": mdcore.iso(now)})
    mdcore.save_json(mdrelease.COMMITMENTS, log)
    print(f"sealed {a['play_id']}\n  artifact sha256 {sha}")
    print(f"  circuit breakers failed: {failed or 'none'}")
    print("  Next: commit data/mercer_dev/artifacts/ and commitments.json, push, then preview.")
    return 0 if not failed else 1


def cmd_preview(play_id):
    io = RealIO(remote=False)
    a = io.load_artifact(play_id)
    if a is None:
        print("artifact unreadable (missing, or sealed with no key)")
        return 1
    sha = mdcore.sha256_of(a)
    if IN_CI:
        hook = os.environ.get(mdrelease.REVIEW_WEBHOOK_ENV, "").strip()
        if not hook.startswith("https://discord.com/api/webhooks/"):
            print(f"{mdrelease.REVIEW_WEBHOOK_ENV} not set; preview NOT printed (public log).")
            return 1
        import requests
        body = {"content": f"PREVIEW ONLY — not sent to members. Approve with sha256 `{sha}`",
                "embeds": a["discord_payload"]["embeds"], "allowed_mentions": {"parse": []}}
        r = requests.post(hook, json=body, headers=UA, timeout=30)
        print(f"preview for {play_id}: review channel HTTP {r.status_code}")
        return 0 if r.status_code < 300 else 1
    print(f"PREVIEW ONLY — nothing is sent. artifact sha256 {sha}\n")
    print(json.dumps(a["discord_payload"], indent=1, ensure_ascii=False))
    for b in a["circuit_breakers"]:
        print(f"  {'PASS' if b['passed'] else 'FAIL'}  {b['name']}: {b['detail']}")
    return 0


def cmd_publish(play_id, sha, dry_run, retry):
    now = mdcore.now_utc()
    approver = os.environ.get("GITHUB_ACTOR", "local")
    status, checks = mdrelease.publish(
        play_id, sha, approver, now, RealIO(), os.environ,
        run_id=os.environ.get("GITHUB_RUN_ID", "local"),
        run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        dry_run=dry_run, retry_after_definitive_failure=retry)
    for name, ok, detail in checks:
        # Check names and pass/fail only: safe for a public log.
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"{play_id}: {status.upper()}")
    if status == "uncertain":
        print("  Delivery outcome UNCERTAIN. The reservation stays. Check the channel; "
              "do not re-run.")
    return 0 if status in ("published", "dry_run") else 1


def cmd_grade(results=None, closing=None):
    """Enter released plays into their ledgers after kickoff; settle finals."""
    now = mdcore.now_utc()
    io = RealIO(remote=False)
    results = results or {
        "nfl": mdcore.load_json(os.path.join(mdcore.ROOT, "data", "football", "nfl_results.json"),
                                {"events": {}})["events"],
        "ncaaf": mdcore.load_json(os.path.join(mdcore.ROOT, "data", "football", "ncaaf_results.json"),
                                  {"events": {}})["events"]}
    deliveries = os.path.join(mdcore.DATA, "deliveries")
    changed = 0
    for name in sorted(os.listdir(deliveries)) if os.path.isdir(deliveries) else []:
        if not name.endswith(".delivered.json"):
            continue
        receipt = mdcore.load_json(os.path.join(deliveries, name))
        a = io.load_artifact(receipt["play_id"])
        if a is None:
            print(f"{receipt['play_id']}: artifact unreadable here; not graded")
            continue
        if mdcore.sha256_of(a) != receipt["artifact_sha256"]:
            raise mdcore.IntegrityError(f"{receipt['play_id']}: artifact differs from its receipt")
        sid = a["strategy_id"]
        led = mdcore.load_ledger(sid)
        start = mdcore.parse_utc(a["event"]["scheduled_start_utc"])
        if now < start:
            continue                     # still withheld from the public record
        if receipt["play_id"] not in {p["play_id"] for p in mdcore.publications(led)}:
            reg = mdcore.verified_registration(sid, a["version"])
            mdcore.append_row(sid, mdrelease.publication_row(a, receipt), registration=reg)
            led = mdcore.load_ledger(sid)
            changed += 1
        if receipt["play_id"] in mdcore.effective_settlements(led):
            continue
        pub = [p for p in mdcore.publications(led) if p["play_id"] == receipt["play_id"]][0]
        ev = results[a["sport"]].get(a["event"]["espn_event_id"])
        close = (closing or {}).get(receipt["play_id"]) or mdcore.load_json(
            os.path.join(mdcore.DATA, "closing", f"{receipt['play_id']}.json"))
        row = mdrelease.settle_row(pub, ev, close, now)
        if row:
            mdcore.append_row(sid, row)
            changed += 1
            print(f"{receipt['play_id']}: {row['result']}")
    print(f"grade: {changed} row(s) appended")
    return 0


def cmd_register(strategy, version, approved_by):
    entry = mdcore.register(strategy, version, mdcore.now_utc(), approved_by)
    print(f"registered {strategy} v{version}\n  sha256 {entry['sha256']}\n"
          f"  effective {entry['effective_utc']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    c = sub.add_parser("candidate"); c.add_argument("--draft", required=True)
    c = sub.add_parser("preview"); c.add_argument("--play-id", required=True)
    c = sub.add_parser("publish")
    c.add_argument("--play-id", required=True); c.add_argument("--sha", required=True)
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--retry-after-definitive-failure", action="store_true")
    sub.add_parser("grade")
    c = sub.add_parser("register")
    c.add_argument("--strategy", required=True); c.add_argument("--version", type=int, required=True)
    c.add_argument("--approved-by", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "candidate":
        return cmd_candidate(args.draft)
    if args.cmd == "preview":
        return cmd_preview(args.play_id)
    if args.cmd == "publish":
        return cmd_publish(args.play_id, args.sha, args.dry_run, args.retry_after_definitive_failure)
    if args.cmd == "grade":
        return cmd_grade()
    if args.cmd == "register":
        return cmd_register(args.strategy, args.version, args.approved_by)
    return 2


if __name__ == "__main__":
    sys.exit(main())
