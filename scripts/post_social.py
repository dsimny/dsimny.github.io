#!/usr/bin/env python3
"""
Open Ledger Sports — post the daily record to social media.

Runs after nightly grading (grade-ledger.yml), alongside the Discord recap, and
posts YESTERDAY's graded results plus the running ledger. Reads the same
data/ledger.json the site and the Discord recap read, so the three never
disagree about the record.

  x         short post (<=280 chars) to X / Twitter.
  facebook  longer post to a Facebook Page.

Two brand rules are baked in and must stay that way (house rules 1 and 5):
  - Losing days post too. The text is generated from the ledger, wins and losses
    alike; there is no path here that skips a bad day.
  - Every post carries responsible-gambling language (21+, 1-800-GAMBLER, not
    betting advice).

IDEMPOTENT, like the email sender and unlike post_discord.py: it records
"posted" per (date, platform) in data/post_status.json (which the workflow
commits) and refuses to re-post that date, so a repeat grading run never
double-posts. Only a failed or missing post is retried.

Missing credentials for a platform? It skips that platform (exit 0) and never
fails the grading run — the ledger and site are unaffected either way.

Run:  python scripts/post_social.py x        [YYYY-MM-DD] [--dry-run]
      python scripts/post_social.py facebook [YYYY-MM-DD] [--dry-run]
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SITE = (os.environ.get("SITE_URL", "").rstrip("/") or "https://openledgersports.com")

CHIP = {"WIN": "✅", "LOSS": "❌", "VOID": "⚪"}
RG = "21+ · 1-800-GAMBLER · not betting advice"
X_LIMIT = 280

STATUS_PATH = os.path.join(ROOT, "data", "post_status.json")
STATUS_KEEP = 30   # matches post_discord.py / send_email.py


def load_status():
    if os.path.exists(STATUS_PATH):
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"posts": []}


def already_posted(date, mode):
    """True if this date already went to this platform. The committed status log
    is the cross-run memory the next grading run checks out."""
    for p in load_status().get("posts", []):
        if p.get("date") == date and p.get("mode") == mode and p.get("result") == "posted":
            return True
    return False


def record(mode, date, result, status=None, detail=""):
    """Append the outcome to data/post_status.json (shared with the Discord and
    email posters). Never records a token. Telemetry must never break a run."""
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
        print(f"NOTE: could not record social status: {exc}")


def load_ledger():
    p = os.path.join(ROOT, "data", "ledger.json")
    if not os.path.exists(p):
        return {"entries": [], "aggregates": None}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def recap(date):
    """The day's graded entries + running aggregates, or None if nothing graded
    for that date. The single source both platform builders draw from."""
    L = load_ledger()
    entries = [e for e in L["entries"] if e["date"] == date]
    if not entries or not L.get("aggregates"):
        return None
    agg = L["aggregates"]
    _d = datetime.strptime(date, "%Y-%m-%d")
    return {
        "nice": f"{_d:%A, %B} {_d.day}",
        "entries": entries,
        "day_pnl": sum(e["pnl"] for e in entries),
        "day_w": sum(1 for e in entries if e["result"] == "WIN"),
        "day_l": sum(1 for e in entries if e["result"] == "LOSS"),
        "day_v": sum(1 for e in entries if e["result"] == "VOID"),
        "record": agg["record"],
        "units_net": agg["units_net"],
        "roi_pct": agg.get("roi_pct"),
    }


def _running(r):
    roi = f" · ROI {r['roi_pct']:+.1f}%" if r["roi_pct"] is not None else ""
    return f"{r['record']} · {r['units_net']:+.2f}u net{roi}"


def build_x_text(date):
    """<=280 chars. Drops the tagline before the record or the legal line — the
    ledger numbers and 1-800-GAMBLER always survive the trim."""
    r = recap(date)
    if r is None:
        return None
    day = f"{r['day_w']}-{r['day_l']}" + (f"-{r['day_v']}" if r["day_v"] else "")
    head = f"Yesterday graded: {day}, {r['day_pnl']:+.2f}u."
    led = f"Ledger: {_running(r)}."
    tag = "Every pick public before first pitch, every result graded — wins and losses."
    lines = [head, led, tag, RG, SITE]
    text = "\n".join(lines)
    if len(text) > X_LIMIT:                       # drop the tagline first
        text = "\n".join([head, led, RG, SITE])
    return text[:X_LIMIT]


def build_fb_text(date):
    """No length cap on a Page post — the full per-pick result list."""
    r = recap(date)
    if r is None:
        return None
    day = f"{r['day_w']}-{r['day_l']}" + (f"-{r['day_v']}" if r["day_v"] else "")
    lines = [f"\U0001f4ca Open Ledger Sports — results for {r['nice']}", ""]
    for e in r["entries"]:
        lines.append(f"{CHIP.get(e['result'], '•')} {e['pick']}: "
                     f"{e.get('final_score', 'void')} ({e['pnl']:+.2f}u)")
    lines += [
        "",
        f"Day: {day}, {r['day_pnl']:+.2f}u",
        f"Running ledger: {_running(r)}",
        "",
        "Every pick is committed to the public record before first pitch and graded "
        "here after — wins and losses alike. Analytics, not betting advice. 21+. "
        "If you or someone you know has a gambling problem, call or text 1-800-GAMBLER.",
        "",
        SITE,
    ]
    return "\n".join(lines)


def post_to_x(text):
    """POST /2/tweets with OAuth 1.0a User Context (4 static keys, no refresh)."""
    import requests
    from requests_oauthlib import OAuth1
    auth = OAuth1(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                  os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
    r = requests.post("https://api.twitter.com/2/tweets",
                      json={"text": text}, auth=auth, timeout=30)
    if r.status_code < 300:
        pid = ""
        try:
            pid = str(r.json().get("data", {}).get("id", ""))
        except Exception:
            pass
        return True, r.status_code, pid
    return False, r.status_code, r.text[:300]


def post_to_facebook(text):
    """POST /{page-id}/feed with a Page access token."""
    import requests
    pid = os.environ["FB_PAGE_ID"]
    r = requests.post(f"https://graph.facebook.com/v21.0/{pid}/feed",
                      data={"message": text, "access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]},
                      timeout=30)
    if r.status_code < 300:
        post_id = ""
        try:
            post_id = str(r.json().get("id", ""))
        except Exception:
            pass
        return True, r.status_code, post_id
    return False, r.status_code, r.text[:300]


# mode -> (text builder, poster, required env var names)
def routes():
    return {
        "x": (build_x_text, post_to_x,
              ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")),
        "facebook": (build_fb_text, post_to_facebook,
                     ("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "x"
    table = routes()
    if mode not in table:
        print(f"Unknown platform {mode!r}. Use one of: {', '.join(table)}.")
        return
    build, poster, env_names = table[mode]

    # Grading posts yesterday's results, so default to yesterday like the recap.
    default = (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    date = args[1] if len(args) > 1 else default
    dry = "--dry-run" in sys.argv

    text = build(date)
    if text is None:
        print(f"No graded entries for {date} — nothing to post to {mode}.")
        if not dry:
            record(mode, date, "nothing_to_post")
        return
    if dry:
        print(f"[{mode}] {len(text)} chars:\n{text}")
        return

    missing = [n for n in env_names if not os.environ.get(n)]
    if missing:
        print(f"NOTE: {', '.join(missing)} not set — skipping {mode} "
              f"(ledger and site are unaffected).")
        record(mode, date, "no_config", detail=f"missing {', '.join(missing)}")
        return
    if already_posted(date, mode):
        print(f"{mode} post for {date} already sent — refusing to re-post.")
        return

    try:
        ok, status, detail = poster(text)
    except Exception as exc:
        print(f"WARNING: {mode} post failed to send: {exc}")
        record(mode, date, "failed", detail=str(exc))
        return
    if not ok:
        # Never fail the pipeline over a social post. "failed" does not block a
        # later retry; only "posted" does.
        print(f"WARNING: {mode} post failed ({status}): {detail}")
        record(mode, date, "failed", status=status, detail=detail)
        return
    print(f"Posted {mode} record for {date}" + (f" (id {detail})." if detail else "."))
    record(mode, date, "posted", status=status, detail=detail)


if __name__ == "__main__":
    main()
