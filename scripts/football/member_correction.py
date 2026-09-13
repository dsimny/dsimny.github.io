"""Send the authorized, fixed incident correction once; never retry an uncertain send.

A remote reservation is created BEFORE Discord is contacted. Any later attempt
refuses that reservation, including if delivery or receipt persistence failed.
An uncertain outcome requires checking Discord, never deleting the reservation.
Secrets are consumed only in Actions and are never included in errors/receipts.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

import delivery_policy
import record_policy

ROOT = Path(__file__).resolve().parents[2]
NOTICE = "football-correction-2026-09-13"


def create_evidence(suffix, evidence):
    # GitHub's create-file endpoint fails if the path already exists without a
    # matching SHA. That is the atomic cross-run duplicate-send gate.
    raw = json.dumps(evidence, sort_keys=True, indent=2).encode()
    body = {"message": f"Record {NOTICE} {suffix}", "branch": "main",
            "content": base64.b64encode(raw).decode()}
    result = subprocess.run(
        ["gh", "api", "--method", "PUT",
         f"repos/{os.environ['GITHUB_REPOSITORY']}/contents/data/football/notices/{NOTICE}.{suffix}.json",
         "--input", "-"], input=json.dumps(body), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Evidence creation refused; inspect existing reservation/receipt. Do not retry delivery.")


def main():
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1" or not delivery_policy.PAUSED:
        raise RuntimeError("Only a first manual run with football paused is permitted")
    message = (ROOT / "docs/FOOTBALL_MEMBER_CORRECTION_2026-09-13.txt").read_text(encoding="utf-8").strip()
    if not 0 < len(message) <= 2000:
        raise RuntimeError("Message length invalid")
    rows = json.loads((ROOT / "data/football/football_ledger.json").read_text(encoding="utf-8"))["entries"]
    incident = [r for r in rows if r.get("board_sha256") in record_policy.INVALIDATED_BOARD_SHA256]
    if len(incident) != 2 or any(record_policy.is_official(r) for r in incident):
        raise RuntimeError("Incident classification is not ready")
    hook = os.environ.get("DISCORD_WEBHOOK_URL_MEMBERS", "")
    if not hook.startswith("https://discord.com/api/webhooks/") or "?" in hook:
        raise RuntimeError("Members webhook is unavailable or unexpected")
    evidence = {"notice": NOTICE, "content_sha256": hashlib.sha256(message.encode()).hexdigest(),
                "run_id": os.environ["GITHUB_RUN_ID"], "code_sha": os.environ["GITHUB_SHA"]}
    create_evidence("reserved", evidence)
    payload = json.dumps({"content": message, "allowed_mentions": {"parse": []}}).encode()
    request = urllib.request.Request(hook + "?wait=true", data=payload,
                                    headers={"Content-Type": "application/json", "User-Agent": "OpenLedgerSports/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            sent = json.load(response)
        message_id = sent["id"]
        if not message_id.isdigit() or sent.get("content") != message:
            raise ValueError("Unexpected acknowledgement")
        with urllib.request.urlopen(urllib.request.Request(hook + "/messages/" + message_id,
                headers={"User-Agent": "OpenLedgerSports/1.0"}), timeout=30) as response:
            verified = json.load(response)
        if verified.get("content") != message:
            raise ValueError("Verification mismatch")
    except Exception:
        raise RuntimeError("Delivery acknowledgement uncertain; reservation retained. Check Discord before any further action.") from None
    create_evidence("delivered", dict(evidence, message_id=message_id, channel_id=verified["channel_id"], verified=True))
    print("Member correction delivered and verified; durable receipt recorded.")


if __name__ == "__main__":
    main()
