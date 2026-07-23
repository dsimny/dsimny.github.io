#!/usr/bin/env python3
"""
Open Ledger Sports — Resend broadcast of the free pick.

One mode today, wired into the morning board workflow after the site is built:

  pick   (after the morning board) — emails the Free Pick of the Day to the
         all-subscribers segment, from picks@send.openledgersports.com. The body
         is the exact HTML already published in feed.xml and #free-pick, so the
         email can never contain a premium play (house rule 2) and never drifts
         from the free pick shown on the site.

Content source is data/feed_items.json (built by build_site.py -> feed.py in the
same run), NOT the encrypted board: the item there is the finished, world-visible
free pick, so there is nothing to decrypt and nothing premium to leak.

IDEMPOTENCY IS THE WHOLE POINT HERE. post_discord.py is deliberately not
idempotent and that spammed the channels when a run repeated; an email that
double-sends is worse — subscribers see two identical picks and unsubscribe. So
this records "sent" in data/post_status.json (which the workflow commits) and a
second run for the same date refuses to send again. Only a failed or missing send
is retried.

No RESEND_API_KEY or no RESEND_SEGMENT_ID? The script exits 0 with a note — like
the Discord poster, it never fails the board run.

Run:  python scripts/send_email.py pick [YYYY-MM-DD] [--dry-run]
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

API_KEY = os.environ.get("RESEND_API_KEY", "")
SEGMENT_ID = os.environ.get("RESEND_SEGMENT_ID", "")
# Verified sending domain is send.openledgersports.com; overridable but the
# default is the address the DKIM/SPF records were set up for.
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Open Ledger Sports <picks@send.openledgersports.com>")

BROADCASTS_URL = "https://api.resend.com/broadcasts"

STATUS_PATH = os.path.join(ROOT, "data", "post_status.json")
STATUS_KEEP = 30   # matches post_discord.py: about a fortnight of daily posts


def load_status():
    if os.path.exists(STATUS_PATH):
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"posts": []}


def already_sent(date, mode):
    """True if this date/mode already went out. The committed status log is the
    cross-run memory: the next scheduled run checks out the file this one wrote."""
    for p in load_status().get("posts", []):
        if p.get("date") == date and p.get("mode") == mode and p.get("result") == "sent":
            return True
    return False


def record(mode, date, result, status=None, detail=""):
    """Append the outcome to data/post_status.json. Same schema and file as the
    Discord poster so the two share one status log. Never records the API key.
    Telemetry must never break a run."""
    try:
        log = load_status()
        log["posts"] = [p for p in log.get("posts", [])
                        if not (p.get("date") == date and p.get("mode") == mode)]
        log["posts"].append({
            "date": date, "mode": mode, "result": result,
            "http_status": status, "detail": str(detail)[:200],
            "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        log["posts"] = sorted(log["posts"], key=lambda p: p["at_utc"])[-STATUS_KEEP:]
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:
        print(f"NOTE: could not record email status: {exc}")


def load_item(date):
    """The feed item for `date`: {date, title, html, pubDate}, or None."""
    path = os.path.join(ROOT, "data", "feed_items.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        store = json.load(f)
    return next((it for it in store.get("items", []) if it.get("date") == date), None)


def html_to_text(html_str):
    """A plain-text fallback for clients that prefer it. Not pretty, just legible:
    block tags become newlines, links keep their href, entities are unescaped."""
    import html as _html
    s = re.sub(r'(?i)<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"\2 (\1)", html_str)
    s = re.sub(r"(?i)</(p|div|br|li|h[1-6])\s*>", "\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def build_payload(item):
    return {
        "segment_id": SEGMENT_ID,
        "from": EMAIL_FROM,
        "subject": item["title"],
        "name": item["title"],           # broadcast name in the Resend dashboard
        "html": item["html"],
        "text": html_to_text(item["html"]),
        "send": True,                    # create + send in one call (Resend supports this)
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "pick"
    if mode != "pick":
        print(f"Unknown mode {mode!r}. Only 'pick' is supported today.")
        return
    date = args[1] if len(args) > 1 else datetime.now(ET).strftime("%Y-%m-%d")
    dry = "--dry-run" in sys.argv

    item = load_item(date)
    if item is None:
        # No feed item means build_site did not run, or ran for another date.
        # The board is unaffected; just note it and move on.
        print(f"No feed item for {date} — nothing to email.")
        if not dry:
            record(mode, date, "nothing_to_send")
        return

    payload = build_payload(item)
    if dry:
        # Show everything except that html/text are long — print their lengths.
        preview = dict(payload)
        preview["html"] = f"<{len(payload['html'])} chars>"
        preview["text"] = f"<{len(payload['text'])} chars>"
        print(json.dumps(preview, indent=2))
        print("\n--- text body ---\n" + payload["text"])
        return

    if not API_KEY or not SEGMENT_ID:
        missing = " and ".join(n for n, v in
                               [("RESEND_API_KEY", API_KEY), ("RESEND_SEGMENT_ID", SEGMENT_ID)] if not v)
        print(f"NOTE: {missing} not set — skipping email (board is unaffected).")
        record(mode, date, "no_config", detail=f"{missing} not set")
        return

    if already_sent(date, mode):
        print(f"Email for {date} already sent — refusing to re-send.")
        return

    import requests
    try:
        r = requests.post(BROADCASTS_URL, json=payload, timeout=30,
                          headers={"Authorization": f"Bearer {API_KEY}"})
    except Exception as exc:
        print(f"WARNING: Resend broadcast failed to send: {exc}")
        record(mode, date, "failed", detail=str(exc))
        return
    if r.status_code >= 300:
        # Never fail the pipeline over an email: log, record, move on. A "failed"
        # record does not block a later retry; only "sent" does.
        print(f"WARNING: Resend broadcast failed ({r.status_code}): {r.text[:300]}")
        record(mode, date, "failed", status=r.status_code, detail=r.text)
        return
    bid = ""
    try:
        bid = str(r.json().get("id", ""))
    except Exception:
        pass
    print(f"Sent email for {date} via Resend" + (f" (broadcast {bid})." if bid else "."))
    record(mode, date, "sent", status=r.status_code, detail=bid)


if __name__ == "__main__":
    main()
