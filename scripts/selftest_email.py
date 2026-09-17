#!/usr/bin/env python3
"""
Offline self-test for scripts/send_email.py — defect B-1 regression coverage.

Confirms that:
  a) the Resend broadcast "name" field never exceeds 70 characters regardless
     of title length (the cause of the 422 that blocked subscriber email);
  b) the "subject" field always carries the FULL title (recipient-visible, no
     limit enforced by Resend);
  c) short and exactly-70-char titles pass through unchanged;
  d) the existing idempotency mechanism (already_sent / record) is intact:
     a "sent" record blocks a re-send, a "failed" record does NOT;
  e) a "nothing_to_send" record does not block a later attempt.

Follows the selftest_*.py convention: plain assertions, no test framework
(requirements.txt pins none), each check NUMBERED. Runs fully offline — no
network, no credentials, no Resend call is ever made. All file writes go into
a TemporaryDirectory that is cleaned up at exit.

  python scripts/selftest_email.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import send_email as se  # noqa: E402

# Total checks this file is expected to run. Bump deliberately when adding or
# removing a check; an unexpected drift means a check stopped executing.
EXPECTED_CHECKS = 71

FAILURES = []
CHECKS = [0]

RESEND_NAME_LIMIT = 70  # Resend API hard limit on the "name" field


def check(n, label, cond):
    CHECKS[0] += 1
    print(("  ok  " if cond else "  FAIL") + f"  {n:>4}  {label}")
    if not cond:
        FAILURES.append(f"{n} {label}")


def fake_item(title, html="<p>body</p>"):
    return {"title": title, "html": html, "date": "2026-09-16",
            "pubDate": "Tue, 16 Sep 2026 15:10:00 +0000"}


def main():
    tmp_dir = tempfile.TemporaryDirectory()
    tmp = tmp_dir.name
    real_status = se.STATUS_PATH
    se.STATUS_PATH = os.path.join(tmp, "post_status.json")

    # ----------------------------------------------------------------
    # 1. Exact cause of B-1: title longer than 70 chars
    # ----------------------------------------------------------------
    print("\n-- 1. Name field truncation: titles longer than 70 chars --")
    titles_over_70 = [
        # Real examples from data/feed_items.json that caused the 422:
        "Daily Pick for Saturday, September 5: Tampa Bay Rays ML (-105, BetOnline.ag)",   # 76 chars
        "Daily Pick for Tuesday, September 8: Atlanta Braves ML (-108, LowVig.ag)",       # 72 chars
        "Daily Pick for Wednesday, September 9: Kansas City Royals ML (+108, Caesars)",   # 76 chars
        "Daily Pick for Saturday, September 12: Arizona Diamondbacks ML (-120, LowVig.ag)",  # 80 chars
        "Daily Pick for Wednesday, September 16: Milwaukee Brewers ML (-118, LowVig.ag)",    # 78 chars
        # Pathological: 200 chars
        "Free Pick for " + "X" * 186,
    ]
    for title in titles_over_70:
        p = se.build_payload(fake_item(title))
        check(1.1, f"name <= {RESEND_NAME_LIMIT} for {len(title)}-char title",
              len(p["name"]) <= RESEND_NAME_LIMIT)
        check(1.2, f"subject is the FULL {len(title)}-char title (not truncated)",
              p["subject"] == title)
        check(1.3, f"name is a prefix of the title ([:70] truncation only)",
              title.startswith(p["name"]))

    # ----------------------------------------------------------------
    # 2. Boundary: exactly 70 chars (no truncation needed)
    # ----------------------------------------------------------------
    print("\n-- 2. Boundary: 70-char title passes unchanged --")
    t70 = "A" * 70
    p70 = se.build_payload(fake_item(t70))
    check(2.1, "name == title when len(title) == 70", p70["name"] == t70)
    check(2.2, "name length is exactly 70", len(p70["name"]) == 70)
    check(2.3, "subject == title when len(title) == 70", p70["subject"] == t70)

    # ----------------------------------------------------------------
    # 3. Short titles: untouched in both fields
    # ----------------------------------------------------------------
    print("\n-- 3. Short titles: name and subject both carry the full string --")
    for short in [
        "Free Pick for Monday, September 7, 2026: no qualifying plays",  # 60 chars (real)
        "Daily Pick for Sunday, September 6: Atlanta Braves ML (+100, Caesars)",  # 69 chars (real)
        "Hi",
        "",
    ]:
        p = se.build_payload(fake_item(short))
        check(3.1, f"name == title for {len(short)}-char title ({short[:30]!r})",
              p["name"] == short)
        check(3.2, f"subject == title for {len(short)}-char title",
              p["subject"] == short)

    # ----------------------------------------------------------------
    # 4. Invariants that must always hold
    # ----------------------------------------------------------------
    print("\n-- 4. Payload invariants always hold --")
    for length in [0, 1, 69, 70, 71, 100, 200]:
        title = "T" * length
        p = se.build_payload(fake_item(title))
        check(4.1, f"name <= {RESEND_NAME_LIMIT} for len={length}",
              len(p["name"]) <= RESEND_NAME_LIMIT)
        check(4.2, f"subject == full title for len={length}",
              p["subject"] == title)
        check(4.3, f"name is a (possibly equal) prefix of subject for len={length}",
              p["subject"].startswith(p["name"]))

    # ----------------------------------------------------------------
    # 5. Idempotency: "sent" blocks re-send; "failed" does not
    # ----------------------------------------------------------------
    print("\n-- 5. Idempotency: already_sent() behavior unchanged --")
    se.STATUS_PATH = os.path.join(tmp, "idem.json")

    # Nothing written yet: not blocked
    check(5.1, "fresh status file: already_sent returns False",
          se.already_sent("2026-09-16", se.STATUS_MODE) is False)

    # Write a "failed" record — must NOT block
    se.record(se.STATUS_MODE, "2026-09-16", "failed", status=422,
              detail="Field name has a maximum of 70 items.")
    check(5.2, "after 'failed': already_sent still returns False (retry is allowed)",
          se.already_sent("2026-09-16", se.STATUS_MODE) is False)

    # Write a "sent" record — must block
    se.record(se.STATUS_MODE, "2026-09-16", "sent", status=200, detail="abc-broadcast-id")
    check(5.3, "after 'sent': already_sent returns True",
          se.already_sent("2026-09-16", se.STATUS_MODE) is True)

    # Different date: not blocked
    check(5.4, "different date is not blocked",
          se.already_sent("2026-09-15", se.STATUS_MODE) is False)

    # "nothing_to_send" does not block
    se.record(se.STATUS_MODE, "2026-09-15", "nothing_to_send")
    check(5.5, "after 'nothing_to_send': already_sent still returns False",
          se.already_sent("2026-09-15", se.STATUS_MODE) is False)

    # Record integrity: one row per (date, mode)
    with open(se.STATUS_PATH, encoding="utf-8") as f:
        log = json.load(f)
    modes = [(p["date"], p["mode"]) for p in log["posts"]]
    check(5.6, "one row per (date, mode) — no duplicates",
          len(modes) == len(set(modes)))

    # status_mode is "email", distinct from post_discord's "pick"
    check(5.7, "STATUS_MODE is 'email' (isolated from Discord's 'pick' key)",
          se.STATUS_MODE == "email")

    # ----------------------------------------------------------------
    # 6. The specific titles that were failing on production
    # ----------------------------------------------------------------
    print("\n-- 6. Production titles that caused the Resend 422 are now valid --")
    production_failures = [
        # From post_status.json (the exact strings that produced HTTP 422):
        "Daily Pick for Saturday, September 5: Tampa Bay Rays ML (-105, BetOnline.ag)",
        "Daily Pick for Tuesday, September 8: Atlanta Braves ML (-108, LowVig.ag)",
        "Daily Pick for Thursday, September 10: Chicago White Sox ML (-104, BetUS)",
        "Daily Pick for Saturday, September 12: Arizona Diamondbacks ML (-120, LowVig.ag)",
        "Daily Pick for Sunday, September 13: Washington Nationals ML (-121, BetRivers)",
        "Daily Pick for Monday, September 14: Los Angeles Angels ML (-102, BetMGM)",
        "Daily Pick for Wednesday, September 16: Milwaukee Brewers ML (-118, LowVig.ag)",
    ]
    for title in production_failures:
        assert len(title) > RESEND_NAME_LIMIT, f"Test setup error: {title!r} is not over 70"
        p = se.build_payload(fake_item(title))
        check(6.1, f"name <= 70 for production title ({len(title)} chars): {title[:40]!r}…",
              len(p["name"]) <= RESEND_NAME_LIMIT)
        check(6.2, f"subject is full title: {title[:40]!r}…",
              p["subject"] == title)

    # ----------------------------------------------------------------
    # Restore
    # ----------------------------------------------------------------
    se.STATUS_PATH = real_status
    tmp_dir.cleanup()

    # Count guard: a check that stops running reports nothing; this makes it loud.
    if CHECKS[0] != EXPECTED_CHECKS:
        FAILURES.append(
            f"check count drifted: ran {CHECKS[0]}, expected {EXPECTED_CHECKS} "
            f"(update EXPECTED_CHECKS deliberately, or find the check that stopped running)"
        )

    print(f"\n{'=' * 60}")
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} problem(s) across {CHECKS[0]} checks:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"PASS — {CHECKS[0]} checks, 0 failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
