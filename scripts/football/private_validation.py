"""Offline pregame readiness audit. No network, picks, ledger writes or sender.

Reads scheduled stored captures only. Outputs outside the repository, using
exclusive creation so repeated runs cannot silently overwrite earlier evidence.
This is an operational diagnostic, never a strategy validation result.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import market_input as mi

ROOT = Path(__file__).resolve().parents[2]
VERSION = "pregame-private-audit-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def audit_snapshot(snapshot, sport, asof):
    decision = mi.utc(asof)
    reasons, observations = Counter(), []
    captured = mi.utc(snapshot.get("captured_utc"))
    if snapshot.get("sport_key") != mi.SPORT_KEYS[sport]:
        raise mi.InvalidMarket("sport routing mismatch")
    if not 0 <= (decision - captured).total_seconds() <= 3600:
        raise mi.InvalidMarket("capture stale or after decision (60-minute diagnostic limit)")
    for ev in snapshot.get("events", []):
        identity = {"sport": sport, "event_id": ev.get("odds_api_event_id"),
                    "away": ev.get("away_raw"), "home": ev.get("home_raw"),
                    "kickoff_utc": ev.get("commence_time")}
        item = dict(identity, status="REJECTED", quote_rejections={})
        try:
            if mi.utc(identity["kickoff_utc"]) <= decision:
                raise mi.InvalidMarket("game already started at decision")
            event, stamp = mi.event_at(snapshot, sport, identity["event_id"],
                identity["away"], identity["home"], identity["kickoff_utc"], asof)
            q, rejected = mi.quotes(event, stamp)
            item["quote_rejections"] = rejected
            item["complete_books"] = len(q)
            item["observation"] = mi.consensus(q, identity["away"], identity["home"])
            item["status"] = "OBSERVATION_ONLY"
            item["reason"] = "no validated pregame strategy"
        except mi.InvalidMarket as exc:
            item["reason"] = str(exc)
        reasons[item["reason"]] += 1
        observations.append(item)
    return {"sport": sport, "captured_utc": snapshot["captured_utc"],
            "decision_utc": asof, "events": observations,
            "reason_counts": dict(reasons), "release_allowed": False,
            "strategy_status": "NOT_REGISTERED_OR_VALIDATED"}


def preview(report):
    # This representation deliberately has no team-side/price recommendation.
    return {"content": f"PRIVATE DIAGNOSTIC — {report['sport'].upper()} — {report['decision_utc']}\n"
            f"{len(report['events'])} events checked. PASS: no validated pregame strategy. "
            "Not a member recommendation; football delivery remains paused.",
            "allowed_mentions": {"parse": []}, "embeds": []}


def bundle(report):
    return {"report": report, "report_sha256": digest(report), "discord_preview": preview(report)}


def verify_bundle(value):
    if value["report_sha256"] != digest(value["report"]):
        raise ValueError("report fingerprint mismatch")
    if value["discord_preview"] != preview(value["report"]):
        raise ValueError("preview differs from fingerprinted report")
    if value["report"]["release_allowed"] is not False:
        raise ValueError("diagnostic cannot authorize delivery")


def latest_capture(directory, sport, asof):
    candidates = []
    for path in directory.glob(f"{sport}_*.json"):
        raw = path.read_bytes()
        snap = json.loads(raw)
        stamp = mi.utc(snap.get("captured_utc"))
        if stamp <= mi.utc(asof) and snap.get("capture_role") == "scheduled":
            candidates.append((stamp, path.name, path, raw, snap))
    if not candidates:
        raise mi.InvalidMarket("no scheduled capture at or before decision")
    _, _, path, raw, snap = max(candidates, key=lambda c: (c[0], c[1]))
    return path, raw, snap


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asof", required=True, help="Explicit UTC decision timestamp; historical runs are replays")
    parser.add_argument("--output", required=True, type=Path, help="New private JSON file outside repository")
    args = parser.parse_args()
    mi.utc(args.asof)
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error("private audit output must be outside the public repository")
    result = {"version": VERSION, "mode": "OFFLINE_REPLAY_NOT_VALIDATION", "asof": args.asof,
              "created_utc": datetime.now(timezone.utc).isoformat(), "sports": {}}
    for sport in mi.SPORT_KEYS:
        try:
            path, raw, snapshot = latest_capture(ROOT / "data/football/odds", sport, args.asof)
            evidence = {"source": path.relative_to(ROOT).as_posix(), "source_sha256": hashlib.sha256(raw).hexdigest()}
            try:
                report = audit_snapshot(snapshot, sport, args.asof)
            except mi.InvalidMarket as exc:
                report = {"sport": sport, "decision_utc": args.asof, "events": [],
                          "reason_counts": {str(exc): 1}, "release_allowed": False,
                          "strategy_status": "NOT_REGISTERED_OR_VALIDATED"}
            report.update(evidence)
            result["sports"][sport] = bundle(report)
            verify_bundle(result["sports"][sport])
        except mi.InvalidMarket as exc:
            result["sports"][sport] = {"error": str(exc), "release_allowed": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
    for sport, value in result["sports"].items():
        print(sport, json.dumps(value.get("report", {}).get("reason_counts", value.get("error"))))
    print("Private audit saved. No recommendation selected or sent.")


if __name__ == "__main__":
    main()
