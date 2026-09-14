#!/usr/bin/env python3
"""
D.J. Mercer Developmental Cohorts - command line and real I/O.

    python scripts/mercer_dev/mercer_dev.py status
    python scripts/mercer_dev/mercer_dev.py candidate --draft drafts/x.json               (local)
    python scripts/mercer_dev/mercer_dev.py candidate --spotlight-week W --pick-id P      (local)
    python scripts/mercer_dev/mercer_dev.py preview   --play-id ID                        (review / console)
    python scripts/mercer_dev/mercer_dev.py commission --play-id ID --sha HASH            (offline double)
    python scripts/mercer_dev/mercer_dev.py publish   --play-id ID --sha HASH [--dry-run]
    python scripts/mercer_dev/mercer_dev.py grade
    python scripts/mercer_dev/mercer_dev.py render
    python scripts/mercer_dev/mercer_dev.py register  --strategy mercer-nfl-dev --version 1 --approved-by NAME

PUBLIC LOGS. GitHub Actions logs of this repository are public. Nothing
actionable (side, line, price, book, reasoning) is ever printed in CI before
the play is graded. Previews in CI go only to DISCORD_WEBHOOK_URL_MERCER_REVIEW.

NOTHING PUBLISHES BY DEFAULT. Publication needs, all at once: a REGISTERED and
effective strategy, data/mercer_dev/control.json enabled for the sport, the
repository variable MERCER_DEV_PUBLICATION=enabled, the sealed artifact and its
commitment already on origin/main, an approver on MERCER_DEV_APPROVERS supplying
the exact artifact hash, and every release check.

COMMISSIONING never touches Discord: it runs the full chain against an offline
double that records the payload and reads it back, and it writes nothing to the
repository.
"""
import argparse
import base64
import glob
import json
import os
import re
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
CAPTURE_RE = re.compile(r"^(nfl|ncaaf)_(\d{8}T\d{6}Z)\.json$")
# Spotlight cards name the book for readers; the release checks need the Odds API
# key. Explicit, never fuzzy: an unmapped book is refused.
BOOK_ALIASES = {"caesars": "williamhill_us", "williamhill": "williamhill_us",
                "circa": "circasports", "pointsbet": "pointsbetus", "unibet": "unibet_us"}


def book_key(name):
    from price_test import TIER1
    k = re.sub(r"[^a-z0-9_]", "", str(name or "").lower())
    k = BOOK_ALIASES.get(k, k)
    if k not in TIER1:
        raise mdrelease.ReleaseRefused(f"book {name!r} is not a recognised Tier-1 book")
    return k


def _git(*args):
    return subprocess.run(["git", *args], cwd=mdcore.ROOT, capture_output=True, text=True)


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

    # artifacts and public commitment
    def load_artifact(self, play_id):
        import crypto_box
        path = os.path.join(mdrelease.ARTIFACTS, f"{play_id}.enc")
        if not os.path.exists(path) or not crypto_box.have_key():
            return None
        return crypto_box.decrypt_from(path)

    def commitments(self):
        """The commitments as PUSHED to origin/main, never a local, unpushed copy."""
        r = _git("show", "origin/main:data/mercer_dev/commitments.json")
        if r.returncode:
            return []
        return json.loads(r.stdout).get("commitments", [])

    def artifact_committed(self, play_id):
        """The sealed artifact on origin/main is byte-identical to the one being sent."""
        rel = f"data/mercer_dev/artifacts/{play_id}.enc"
        remote = _git("rev-parse", f"origin/main:{rel}")
        local = _git("hash-object", os.path.join(mdcore.ROOT, rel))
        return remote.returncode == 0 and local.returncode == 0 and \
            remote.stdout.strip() == local.stdout.strip()

    def delivered(self):
        out = []
        for path in sorted(glob.glob(os.path.join(mdcore.DATA, "deliveries", "*.delivered.json"))):
            out.append(mdrelease.normalized_receipt(mdcore.load_json(path)))
        return out

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


class CommissionIO(RealIO):
    """Real artifact, real commitments, real fresh ESPN and odds - and nothing else.

    Records live in memory (existing repository records are still visible, so a
    play that was already released still refuses). The "Discord" is an echo that
    returns what it was given. There is no network send path in this class.
    """
    offline_double = True

    def __init__(self):
        super().__init__(remote=False)
        self.memory, self.sent = {}, []

    def create_only(self, relpath, obj):
        if relpath in self.memory or super().exists(relpath):
            raise mdrelease.ReleaseRefused(f"{relpath} exists")
        self.memory[relpath] = obj

    def exists(self, relpath):
        return relpath in self.memory or super().exists(relpath)

    def read(self, relpath):
        return self.memory[relpath] if relpath in self.memory else super().read(relpath)

    def send(self, webhook, payload):
        if webhook != mdrelease.COMMISSIONING_HOOK:
            raise mdrelease.ReleaseRefused("commissioning double received a real webhook; refusing")
        self.sent.append(json.loads(json.dumps(payload)))
        return dict(payload, id="100000000000000001", channel_id="0",
                    timestamp=mdcore.iso(mdcore.now_utc()).replace("Z", "+00:00"))

    def fetch_message(self, webhook, message_id):
        return dict(self.sent[-1], id=message_id, channel_id="0",
                    timestamp=mdcore.iso(mdcore.now_utc()).replace("Z", "+00:00"))


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# --- Spotlight -> cohort draft ------------------------------------------------------

def spotlight_draft(week, pick_id, quote, spotlight_doc, spotlight_commits):
    """(draft, spotlight link) for a cohort-owned Spotlight pick. Pure; raises on any gap."""
    import mercer
    pick = next((p for p in spotlight_doc.get("picks", []) if p.get("id") == pick_id), None)
    if pick is None:
        raise mdrelease.ReleaseRefused(f"{pick_id} is not an official pick in week {week}")
    if not mercer.cohort_owned(pick):
        raise mdrelease.ReleaseRefused(f"{pick_id} has no cohort_play_id: it belongs to the Spotlight ledger")
    first = min((c for c in spotlight_commits if c["pick_id"] == pick_id),
                key=lambda c: c["committed_utc"], default=None)
    if first is None or first["sha256"] != mercer.fingerprint(pick):
        raise mdrelease.ReleaseRefused(f"{pick_id} is not fingerprinted as it stands; run mercer.py commit first")
    for k in ("odds_event_id", "walk_away"):
        if not pick.get(k):
            raise mdrelease.ReleaseRefused(f"{pick_id} needs {k!r} to be released as a cohort play")
    sport = pick["sport"]
    reg_version = int(pick["cohort_play_id"].split("-v")[1].split("-")[0])
    if pick["market"] == "total":
        selection = str(pick["side"]).lower()
    else:
        want = mdmarket.team_key(sport, pick["team"])
        names = [n for n in (quote["home"], quote["away"]) if mdmarket.team_key(sport, n) == want]
        if len(names) != 1:
            raise mdrelease.ReleaseRefused(f"{pick_id}: team does not map to exactly one side of the quote")
        selection = names[0]
    draft = {"strategy_id": mercer.COHORT_STRATEGY[sport], "version": reg_version, "sport": sport,
             "espn_event_id": str(first["espn_event_id"]), "odds_event_id": pick["odds_event_id"],
             "market": pick["market"], "selection": selection,
             "line": pick.get("line") if pick["market"] != "moneyline" else None,
             "odds": pick["price"], "book": book_key(pick.get("book")),
             "recommended_units": float(pick.get("units", 1)),
             "confidence": str(pick.get("conviction") or "standard").lower(),
             "reasoning": pick["why"], "key_risks": pick["case_against"],
             "walk_away": pick["walk_away"]}
    return draft, {"week": week, "pick_id": pick_id, "pick_sha256": first["sha256"]}


# --- commands ---------------------------------------------------------------------

def cmd_status():
    for sport, meta in mdcore.SPORTS.items():
        sid = meta["strategy_id"]
        reg, entry = mdcore.active_registration(sport, mdcore.now_utc())
        agg = mdcore.aggregates(mdcore.load_ledger(sid))
        state = f"v{reg['version']} REGISTERED" if reg else "not registered or not effective (nothing may publish)"
        print(f"{meta['name']}: {state}")
        print(f"  record {agg['record']}  published {agg['published']}  pending {agg['pending']}  "
              f"units {agg['units']}  CLV {agg['clv']['mean_pts']}")
    ctl, env_on = mdrelease.control_state(os.environ)
    print(f"control.json publication_enabled={ctl.get('publication_enabled')} "
          f"sports={ctl.get('sports')}  {mdrelease.PUBLICATION_ENV}={'enabled' if env_on else 'off'}")
    return 0


def seal(a, sha, now):
    import crypto_box
    os.makedirs(mdrelease.ARTIFACTS, exist_ok=True)
    crypto_box.encrypt_to(os.path.join(mdrelease.ARTIFACTS, f"{a['play_id']}.enc"), a)
    log = mdcore.load_json(mdrelease.COMMITMENTS, {
        "_note": ("Append-only. SHA-256 of each sealed Mercer developmental artifact, "
                  "committed and pushed before release. Proof only: no side, line, price, "
                  "book or reasoning. A later entry for the same play supersedes an earlier "
                  "one before release; both stay."), "commitments": []})
    log["commitments"].append({"play_id": a["play_id"], "strategy_id": a["strategy_id"],
                               "version": a["version"], "sport": a["sport"],
                               "espn_event_id": a["event"]["espn_event_id"],
                               "matchup": a["matchup"], "market": a["market"],
                               "scheduled_start_utc": a["event"]["scheduled_start_utc"],
                               "featured": a["featured"], "artifact_sha256": sha,
                               "committed_utc": mdcore.iso(now)})
    mdcore.save_json(mdrelease.COMMITMENTS, log)


def cmd_candidate(draft_path=None, spotlight_week=None, pick_id=None):
    import crypto_box
    if IN_CI:
        print("candidate runs locally only: the draft is actionable and must not touch CI.")
        return 2
    if not crypto_box.have_key():
        print("BOARD_ENCRYPTION_KEY is required: an artifact is never written in plaintext.")
        return 2
    now = mdcore.now_utc()
    io = RealIO(remote=False)
    spotlight = None
    if spotlight_week:
        import mercer
        doc = mercer.load_week(spotlight_week)
        if doc is None:
            print(f"{spotlight_week}: Spotlight card unreadable")
            return 1
        pick = next((p for p in doc.get("picks", []) if p.get("id") == pick_id), {})
        quote = io.fetch_quote(pick.get("sport"), pick.get("odds_event_id"))
        draft, spotlight = spotlight_draft(spotlight_week, pick_id, quote, doc,
                                           mercer.load_commitments()["commitments"])
    else:
        draft = mdcore.load_json(draft_path)
        quote = io.fetch_quote(draft["sport"], draft["odds_event_id"])
    reg = mdcore.active_registration(draft["sport"], now)
    if reg == (None, None):
        print(f"{draft['sport']}: no registered, effective cohort; nothing can be built for release.")
        return 1
    event = io.fetch_event(draft["sport"], draft["espn_event_id"])
    a, sha, failed = mdrelease.build_artifact(draft, reg, quote, event, now, spotlight=spotlight)
    if spotlight and a["play_id"] != pick["cohort_play_id"]:
        print("the built play id does not match the Spotlight pick's cohort_play_id; not sealed")
        return 1
    seal(a, sha, now)
    print(f"sealed {a['play_id']}\n  artifact sha256 {sha}")
    print(f"  circuit breakers failed: {failed or 'none'}")
    print("  Next: commit and PUSH data/mercer_dev/artifacts/ and commitments.json, then preview.")
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


def _print_checks(play_id, status, checks, commissioning=False):
    for name, ok, _detail in checks:
        # Check names and pass/fail only: safe for a public log.
        note = " (expected OFF during commissioning)" if commissioning and name == "kill_switch_off" and not ok else ""
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{note}")
    print(f"{play_id}: {status.upper()}")


def cmd_publish(play_id, sha, dry_run, retry):
    now = mdcore.now_utc()
    approver = os.environ.get("GITHUB_ACTOR", "local")
    status, checks = mdrelease.publish(
        play_id, sha, approver, now, RealIO(), os.environ,
        run_id=os.environ.get("GITHUB_RUN_ID", "local"),
        run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        dry_run=dry_run, retry_after_definitive_failure=retry)
    _print_checks(play_id, status, checks)
    if status == "uncertain":
        print("  Delivery outcome UNCERTAIN. The reservation stays. Check the channel; do not re-run.")
    return 0 if status in ("published", "dry_run") else 1


def cmd_commission(play_id, sha):
    """The full release chain with an offline Discord double. Posts nothing, writes nothing."""
    for k in (mdrelease.PLAYS_WEBHOOK_ENV, mdrelease.REVIEW_WEBHOOK_ENV):
        os.environ.pop(k, None)
    io = CommissionIO()
    status, checks = mdrelease.publish(
        play_id, sha, os.environ.get("GITHUB_ACTOR", "local"), mdcore.now_utc(), io, os.environ,
        run_id=os.environ.get("GITHUB_RUN_ID", "local"), commissioning=True)
    _print_checks(play_id, status, checks, commissioning=True)
    print(f"  offline double recorded {len(io.sent)} message(s); nothing was sent to Discord "
          f"and nothing was written to the repository.")
    return 0 if status == "commissioned" else 1


def load_captures(sport, kickoff):
    out = []
    for path in glob.glob(os.path.join(mdcore.ROOT, "data", "football", "odds", f"{sport}_*.json")):
        m = CAPTURE_RE.match(os.path.basename(path))
        if not m:
            continue
        t = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if t < kickoff and kickoff - t <= mdrelease.CLOSE_WINDOW:
            out.append((t, mdcore.load_json(path)))
    return out


def closing_for(pub):
    kickoff = mdcore.parse_utc(pub["scheduled_start_utc"])
    away, home = pub["matchup"].split(" @ ")
    other = home if pub["selection"] == away else away
    return mdrelease.closing_from_captures(load_captures(pub["sport"], kickoff),
                                           pub["odds_event_id"], pub["selection"], other, kickoff)


def reveal(a):
    path = os.path.join(mdcore.DATA, "revealed", f"{a['play_id']}.json")
    if not os.path.exists(path):
        mdcore.save_json(path, a)


def cmd_grade():
    now = mdcore.now_utc()
    io = RealIO(remote=False)
    results = {s: mdcore.load_json(os.path.join(mdcore.ROOT, "data", "football", f"{s}_results.json"),
                                   {"events": {}})["events"] for s in ("nfl", "ncaaf")}
    receipts = [mdcore.load_json(p) for p in
                sorted(glob.glob(os.path.join(mdcore.DATA, "deliveries", "*.delivered.json")))]
    rows = mdrelease.grade_all(now, receipts, io.load_artifact, results, closing_for, reveal)
    for r in rows:
        print(f"  {r['play_id']}: {r['row_type']}" + (f" {r['result']}" if r["row_type"] == "settlement" else ""))
    print(f"grade: {len(rows)} row(s) appended across {len(receipts)} released play(s)")
    return 0


def cmd_render():
    import render
    print(f"wrote {os.path.relpath(render.render(), mdcore.ROOT)}")
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
    c = sub.add_parser("candidate")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--draft")
    g.add_argument("--spotlight-week")
    c.add_argument("--pick-id")
    c = sub.add_parser("preview"); c.add_argument("--play-id", required=True)
    c = sub.add_parser("commission")
    c.add_argument("--play-id", required=True); c.add_argument("--sha", required=True)
    c = sub.add_parser("publish")
    c.add_argument("--play-id", required=True); c.add_argument("--sha", required=True)
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--retry-after-definitive-failure", action="store_true")
    sub.add_parser("grade")
    sub.add_parser("render")
    c = sub.add_parser("register")
    c.add_argument("--strategy", required=True); c.add_argument("--version", type=int, required=True)
    c.add_argument("--approved-by", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "candidate":
        if args.spotlight_week and not args.pick_id:
            ap.error("--spotlight-week needs --pick-id")
        return cmd_candidate(args.draft, args.spotlight_week, args.pick_id)
    if args.cmd == "preview":
        return cmd_preview(args.play_id)
    if args.cmd == "commission":
        return cmd_commission(args.play_id, args.sha)
    if args.cmd == "publish":
        return cmd_publish(args.play_id, args.sha, args.dry_run, args.retry_after_definitive_failure)
    if args.cmd == "grade":
        return cmd_grade()
    if args.cmd == "render":
        return cmd_render()
    if args.cmd == "register":
        return cmd_register(args.strategy, args.version, args.approved_by)
    return 2


if __name__ == "__main__":
    sys.exit(main())
