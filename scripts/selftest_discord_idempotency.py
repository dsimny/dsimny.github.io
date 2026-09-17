#!/usr/bin/env python3
"""
Offline self-test for the post_discord.py idempotency guard (B-6).

Confirms that:
  a) already_posted() correctly reads 'posted' records as a block;
  b) only 'posted' blocks — 'failed', 'no_webhook', 'nothing_to_post' allow retry;
  c) a guarded second call to main() is silently skipped without re-posting
     and without overwriting the 'posted' record;
  d) --dry-run bypasses the guard and always shows the payload;
  e) alert mode is never touched by the guard (dispatched before routing);
  f) blog is not in GUARDED_MODES;
  g) mode isolation: a block on (mode=pick, date=X) does not block (board, X)
     or (recap, X);
  h) channel_id is preserved in the 'posted' record (wrong-channel detectability
     unchanged);
  i) already_posted() fails open on a missing file and on corrupt JSON;
  j) record() schema is unchanged;
  k) the routing table strings checked by selftest_instagram.py group 22 are
     still present verbatim.

Follows the selftest_*.py convention: plain assertions, numbered checks, no
test framework, fully offline, no network, no credentials, no Discord call.
All file writes go into a TemporaryDirectory that is cleaned up at exit.

  python scripts/selftest_discord_idempotency.py
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import post_discord as pd   # noqa: E402

# Total checks this file is expected to run. Bump deliberately when adding or
# removing a check; an unexpected drift means a check stopped executing.
EXPECTED_CHECKS = 53

FAILURES = []
CHECKS = [0]


def check(n, label, cond):
    CHECKS[0] += 1
    print(("  ok  " if cond else "  FAIL") + f"  {n!s:>6}  {label}")
    if not cond:
        FAILURES.append(f"{n}: {label}")


def set_status(posts):
    """Write posts to pd.STATUS_PATH (wherever it currently points)."""
    os.makedirs(os.path.dirname(pd.STATUS_PATH), exist_ok=True)
    with open(pd.STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump({"posts": posts}, f)


def get_status():
    """Read pd.STATUS_PATH, or return empty if missing."""
    if not os.path.exists(pd.STATUS_PATH):
        return {"posts": []}
    with open(pd.STATUS_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_main(*argv):
    """Call pd.main() with the given argv, capture stdout, restore argv."""
    real = sys.argv[:]
    sys.argv = list(argv)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            pd.main()
    finally:
        sys.argv = real
    return buf.getvalue()


def main():
    tmp_dir = tempfile.TemporaryDirectory()
    tmp = tmp_dir.name
    real_status = pd.STATUS_PATH

    # ----------------------------------------------------------------
    # 1. GUARDED_MODES constant
    # ----------------------------------------------------------------
    print("\n-- 1. GUARDED_MODES contains exactly pick, board, recap --")
    check(1.1, "pick is guarded",          "pick"  in pd.GUARDED_MODES)
    check(1.2, "board is guarded",         "board" in pd.GUARDED_MODES)
    check(1.3, "recap is guarded",         "recap" in pd.GUARDED_MODES)
    check(1.4, "blog is NOT guarded",      "blog"  not in pd.GUARDED_MODES)
    check(1.5, "alert is NOT guarded",     "alert" not in pd.GUARDED_MODES)
    check(1.6, "no unknown mode is guarded",
          pd.GUARDED_MODES == frozenset({"pick", "board", "recap"}))

    # ----------------------------------------------------------------
    # 2. already_posted() — core read logic
    # ----------------------------------------------------------------
    print("\n-- 2. already_posted(): reading the status file --")
    pd.STATUS_PATH = os.path.join(tmp, "ap_test.json")

    # Missing file → False (fails open)
    check(2.1, "missing file → False (fails open)",
          pd.already_posted("pick", "2026-09-16") is False)

    # Empty posts list → False
    set_status([])
    check(2.2, "empty posts list → False",
          pd.already_posted("pick", "2026-09-16") is False)

    # 'posted' result → True
    set_status([{"date": "2026-09-16", "mode": "pick", "result": "posted",
                 "http_status": 200, "channel_id": "111", "detail": "",
                 "at_utc": "2026-09-16T15:10:00Z"}])
    check(2.3, "'posted' result → True",
          pd.already_posted("pick", "2026-09-16") is True)

    # 'failed' result → False
    set_status([{"date": "2026-09-16", "mode": "pick", "result": "failed",
                 "http_status": 500, "channel_id": "", "detail": "err",
                 "at_utc": "2026-09-16T15:10:00Z"}])
    check(2.4, "'failed' result → False (retry allowed)",
          pd.already_posted("pick", "2026-09-16") is False)

    # 'nothing_to_post' → False
    set_status([{"date": "2026-09-16", "mode": "pick", "result": "nothing_to_post",
                 "http_status": None, "channel_id": "", "detail": "",
                 "at_utc": "2026-09-16T15:10:00Z"}])
    check(2.5, "'nothing_to_post' → False (retry allowed)",
          pd.already_posted("pick", "2026-09-16") is False)

    # 'no_webhook' → False
    set_status([{"date": "2026-09-16", "mode": "pick", "result": "no_webhook",
                 "http_status": None, "channel_id": "", "detail": "",
                 "at_utc": "2026-09-16T15:10:00Z"}])
    check(2.6, "'no_webhook' → False (retry allowed)",
          pd.already_posted("pick", "2026-09-16") is False)

    # Corrupt JSON → False (fails open)
    pd.STATUS_PATH = os.path.join(tmp, "corrupt.json")
    with open(pd.STATUS_PATH, "w") as f:
        f.write("{not valid json")
    check(2.7, "corrupt JSON → False (fails open)",
          pd.already_posted("pick", "2026-09-16") is False)

    # ----------------------------------------------------------------
    # 3. Mode isolation
    # ----------------------------------------------------------------
    print("\n-- 3. Mode isolation: one mode's 'posted' does not block others --")
    pd.STATUS_PATH = os.path.join(tmp, "iso_test.json")
    set_status([{"date": "2026-09-16", "mode": "pick", "result": "posted",
                 "http_status": 200, "channel_id": "111", "detail": "",
                 "at_utc": "2026-09-16T15:10:00Z"}])
    check(3.1, "pick posted → pick blocked",
          pd.already_posted("pick", "2026-09-16") is True)
    check(3.2, "pick posted → board NOT blocked",
          pd.already_posted("board", "2026-09-16") is False)
    check(3.3, "pick posted → recap NOT blocked",
          pd.already_posted("recap", "2026-09-16") is False)
    check(3.4, "pick posted → blog NOT blocked",
          pd.already_posted("blog", "2026-09-16") is False)
    check(3.5, "pick/2026-09-16 posted → pick/2026-09-15 NOT blocked",
          pd.already_posted("pick", "2026-09-15") is False)

    # ----------------------------------------------------------------
    # 4. record() schema unchanged
    # ----------------------------------------------------------------
    print("\n-- 4. record() schema: all fields preserved --")
    pd.STATUS_PATH = os.path.join(tmp, "schema_test.json")
    pd.record("pick", "2026-09-16", "posted", status=200,
              detail="ok", channel_id="999")
    entry = get_status()["posts"][0]
    check(4.1,  "date field present",        "date"       in entry)
    check(4.2,  "mode field present",        "mode"       in entry)
    check(4.3,  "result field present",      "result"     in entry)
    check(4.4,  "http_status field present", "http_status" in entry)
    check(4.5,  "channel_id field present",  "channel_id" in entry)
    check(4.6,  "detail field present",      "detail"     in entry)
    check(4.7,  "at_utc field present",      "at_utc"     in entry)
    check(4.8,  "channel_id value preserved", entry["channel_id"] == "999")
    check(4.9,  "result value correct",       entry["result"] == "posted")

    # record() is replace-on-write: a second call for same (mode, date) replaces
    # the first entry. This is exactly why the guard must not call record() on
    # skip — doing so would overwrite 'posted' with 'skipped' and unblock the
    # next run.
    pd.record("pick", "2026-09-16", "failed", status=500)
    pick_entries = [p for p in get_status()["posts"]
                    if p["date"] == "2026-09-16" and p["mode"] == "pick"]
    check(4.10, "record() replaces same (mode,date): exactly one entry",
          len(pick_entries) == 1)
    check(4.11, "the replacement is the 'failed' entry (replace-on-write confirmed)",
          pick_entries[0]["result"] == "failed")
    check(4.12, "already_posted() after record('failed') returns False again",
          pd.already_posted("pick", "2026-09-16") is False)

    # ----------------------------------------------------------------
    # 5. Guard behavior via main() — exercises the actual guard branch
    # ----------------------------------------------------------------
    print("\n-- 5. Guard in main(): guarded modes skip on re-run --")
    # We use modes where the payload builder does NOT need a board file or key:
    #   - recap reads data/ledger.json; for a date with no entries returns None
    #   - blog reads data/blog_items.json; for a date with no entry returns None
    # For pick/board we can still test the guard branch (it fires before build()),
    # but we capture the output without reaching build_pick_payload.

    pd.STATUS_PATH = os.path.join(tmp, "guard_test.json")

    # pick — guard fires before build(), so no board file or key needed
    pd.record("pick", "2026-09-16", "posted", status=200, channel_id="111")
    before = get_status()
    out = run_main("post_discord.py", "pick", "2026-09-16")
    after = get_status()
    check(5.1, "pick guard: prints skip note",
          "already posted" in out and "skipping" in out)
    check(5.2, "pick guard: no new status entry added",
          len(after["posts"]) == len(before["posts"]))
    check(5.3, "pick guard: 'posted' record still present",
          any(p["result"] == "posted" and p["mode"] == "pick"
              and p["date"] == "2026-09-16" for p in after["posts"]))
    check(5.4, "pick guard: channel_id preserved (wrong-channel detectability intact)",
          any(p["channel_id"] == "111" for p in after["posts"]))

    # board — guard fires before build()
    pd.record("board", "2026-09-16", "posted", status=200, channel_id="222")
    before_b = get_status()
    out_b = run_main("post_discord.py", "board", "2026-09-16")
    after_b = get_status()
    check(5.5, "board guard: prints skip note",
          "already posted" in out_b)
    check(5.6, "board guard: 'posted' record still present",
          any(p["result"] == "posted" and p["mode"] == "board"
              and p["date"] == "2026-09-16" for p in after_b["posts"]))

    # recap — guard fires before build()
    pd.record("recap", "2026-09-16", "posted", status=200, channel_id="333")
    before_r = get_status()
    out_r = run_main("post_discord.py", "recap", "2026-09-16")
    after_r = get_status()
    check(5.7, "recap guard: prints skip note",
          "already posted" in out_r)
    check(5.8, "recap guard: 'posted' record still present",
          any(p["result"] == "posted" and p["mode"] == "recap"
              and p["date"] == "2026-09-16" for p in after_r["posts"]))

    # ----------------------------------------------------------------
    # 6. --dry-run bypasses the guard
    # ----------------------------------------------------------------
    print("\n-- 6. --dry-run bypasses the guard entirely --")
    # Use recap with a date that definitely has no ledger entries (no board
    # decrypt needed). build_recap_payload prints "No graded entries..." then
    # returns None; dry-run skips the record() call. The key point: the guard's
    # "already posted" message must NOT appear in the output.
    pd.STATUS_PATH = os.path.join(tmp, "dryrun_test.json")
    pd.record("recap", "1900-01-01", "posted", status=200, channel_id="111")
    out_dry = run_main("post_discord.py", "recap", "1900-01-01", "--dry-run")
    check(6.1, "--dry-run: guard skip note is NOT printed",
          "already posted" not in out_dry)
    # dry-run does not write a record; the file still has exactly one entry
    entries_after_dry = [p for p in get_status()["posts"]
                         if p["mode"] == "recap" and p["date"] == "1900-01-01"]
    check(6.2, "--dry-run: status file not written by guard skip",
          len(entries_after_dry) == 1 and entries_after_dry[0]["result"] == "posted")

    # ----------------------------------------------------------------
    # 7. alert mode is never reached by the guard
    # ----------------------------------------------------------------
    print("\n-- 7. alert mode: guard is never reached --")
    pd.STATUS_PATH = os.path.join(tmp, "alert_test.json")
    real_alert = os.environ.get("DISCORD_WEBHOOK_URL_ALERTS", "")
    os.environ["DISCORD_WEBHOOK_URL_ALERTS"] = ""
    out_alert = run_main("post_discord.py", "alert", "something broke")
    os.environ["DISCORD_WEBHOOK_URL_ALERTS"] = real_alert
    check(7.1, "alert: 'already posted' NOT in output (guard not reached)",
          "already posted" not in out_alert)
    check(7.2, "alert: prints the 'not configured' note",
          "not configured" in out_alert or "DISCORD_WEBHOOK_URL_ALERTS" in out_alert)
    check(7.3, "alert: no status file written",
          not os.path.exists(pd.STATUS_PATH))

    # ----------------------------------------------------------------
    # 8. blog is not guarded
    # ----------------------------------------------------------------
    print("\n-- 8. blog is not guarded --")
    pd.STATUS_PATH = os.path.join(tmp, "blog_test.json")
    pd.record("blog", "2026-09-16", "posted", status=200, channel_id="555")
    out_blog = run_main("post_discord.py", "blog", "2026-09-16")
    check(8.1, "blog: 'already posted' NOT in output (not guarded)",
          "already posted" not in out_blog)
    # build_blog_payload returns None for a date with no blog_items.json, so
    # main() reaches the nothing_to_post branch — proving it passed the guard.
    blog_entries = [p for p in get_status()["posts"] if p["mode"] == "blog"]
    # blog reached the payload stage: it recorded 'nothing_to_post' (no blog
    # items file) or 'no_webhook' (no webhook configured in the test env).
    # Either result proves the guard was bypassed; what matters is the guard's
    # own skip message is absent.
    check(8.2, "blog: reached payload stage (no_webhook or nothing_to_post, not skipped)",
          len(blog_entries) >= 1
          and blog_entries[-1]["result"] in ("nothing_to_post", "no_webhook"))

    # ----------------------------------------------------------------
    # 9. selftest_instagram.py group 22 routing assertions still pass
    # ----------------------------------------------------------------
    print("\n-- 9. Routing table strings (selftest_instagram group 22) unchanged --")
    pd_src = open(os.path.join(ROOT, "scripts", "post_discord.py"),
                  encoding="utf-8").read()
    check(9.1, 'pick routes to WEBHOOK',
          '"pick":  (build_pick_payload,  WEBHOOK,' in pd_src)
    check(9.2, 'board routes to MEMBERS_WEBHOOK',
          '"board": (build_board_payload, MEMBERS_WEBHOOK,' in pd_src)
    check(9.3, 'recap falls back to free channel',
          '"recap": (build_recap_payload, LEDGER_WEBHOOK or WEBHOOK,' in pd_src)
    check(9.4, 'MEMBERS_WEBHOOK is defined for board mode',
          "MEMBERS_WEBHOOK = os.environ.get" in pd_src)
    check(9.5, 'NO FALLBACK policy present (alert no-fallback)',
          "NO FALLBACK" in pd_src)
    # post_alert() body: the only webhook reference must be ALERT_WEBHOOK as a
    # real assignment (not a comment). MEMBERS_WEBHOOK and the free-pick WEBHOOK
    # appear only in comments documenting what was removed; they must never
    # be assigned to the `webhook` local variable.
    alert_body = pd_src[pd_src.index("def post_alert"):pd_src.index("def routes")]
    alert_lines = [l for l in alert_body.splitlines()
                   if not l.lstrip().startswith("#")]
    check(9.6, 'post_alert only assigns ALERT_WEBHOOK, not members/free webhook',
          any("webhook = ALERT_WEBHOOK" in l for l in alert_lines) and
          not any("webhook = MEMBERS_WEBHOOK" in l or "webhook = WEBHOOK" in l
                  for l in alert_lines))

    # ----------------------------------------------------------------
    # 10. Public API shape
    # ----------------------------------------------------------------
    print("\n-- 10. already_posted() and GUARDED_MODES are exported --")
    check(10.1, "already_posted is callable",
          callable(getattr(pd, "already_posted", None)))
    check(10.2, "GUARDED_MODES is a frozenset",
          isinstance(getattr(pd, "GUARDED_MODES", None), frozenset))

    # ----------------------------------------------------------------
    # Restore
    # ----------------------------------------------------------------
    pd.STATUS_PATH = real_status
    tmp_dir.cleanup()

    if CHECKS[0] != EXPECTED_CHECKS:
        FAILURES.append(
            f"check count drifted: ran {CHECKS[0]}, expected {EXPECTED_CHECKS} "
            f"(update EXPECTED_CHECKS deliberately, or find the stopped check)"
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
