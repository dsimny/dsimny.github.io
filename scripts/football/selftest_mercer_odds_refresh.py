#!/usr/bin/env python3
"""Self-test for the Mercer-only Odds API refresh plumbing."""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_odds  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS", message)


def read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    root = tempfile.mkdtemp(prefix="ols_mercer_odds_")
    try:
        default_log = os.path.join(root, "data", "odds_credits.json")
        custom_log = os.path.join(root, "data", "mercer", "odds", "credits.json")
        reading = {
            "remaining": 999,
            "used": 1,
            "last_call_cost": 1,
            "markets": "h2h",
            "regions": "us",
            "http_status": 200,
            "source": "selftest",
            "read_utc": "2026-09-18T00:00:00Z",
        }

        original_credit_log = fetch_odds.CREDIT_LOG
        fetch_odds.CREDIT_LOG = default_log
        try:
            fetch_odds.record_credits(reading, path=custom_log)
            check(os.path.isfile(custom_log),
                  "explicit credit-log path is created")
            check(read_json(custom_log) == {"readings": [reading]},
                  "explicit credit-log path contains the credit reading")
            check(not os.path.exists(default_log),
                  "explicit credit-log path does not write the default ledger")

            fetch_odds.record_credits(reading)
            check(os.path.isfile(default_log),
                  "omitted credit-log path preserves the default ledger location")
            check(read_json(default_log) == {"readings": [reading]},
                  "omitted credit-log path preserves the existing ledger format")
        finally:
            fetch_odds.CREDIT_LOG = original_credit_log
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("All Mercer odds refresh checks passed")


if __name__ == "__main__":
    main()
