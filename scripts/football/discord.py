#!/usr/bin/env python3
"""
Open Ledger Sports — football Discord delivery (gap G).

Two modes, two channels, and the split is the product:

  free   -> DISCORD_WEBHOOK_URL          the free play, in full
  slate  -> DISCORD_WEBHOOK_URL_MEMBERS  the FULL SLATE plus the premium play

THE MEMBERS' POST IS THE PRODUCT, NOT A BONUS. `docs/FOOTBALL_LAUNCH.md` gap F
records why: the selection rule is public and deterministic, so printing every
covered game's numbers publicly before kickoff would hand over the premium play
exactly. The whole reasoned slate is therefore what members buy - timing and
coverage, per fp-v0.1 section 5 - and the public page carries the free play, the
coverage summary and the NO MARKET list until the week is graded.

IDEMPOTENT, DELIBERATELY DIFFERENT FROM post_discord.py. CLAUDE.md records that
the MLB poster is intentionally NOT idempotent: a re-run re-posts one pick, which
is a small, tolerable duplicate. This posts a WEEK - up to a dozen messages
carrying ~57 games - and re-running it would bury the channel it is meant to
serve. So both modes check data/post_status.json first and skip if that slate
week already went out.

The status modes are `fb_free` and `fb_slate`, distinct from `pick`/`board`/
`email`, for the reason send_email.py documents: record() deletes any existing
(key, mode) row, so a shared mode would let one sender wipe another's guard and
double-post. Keyed by SLATE WEEK rather than date, because football's unit is a
week.

DEGRADE, NEVER DIE. Missing webhook, missing board, HTTP failure: log it, record
it, exit 0. A delivery problem must never fail the run that produced the board.

Run:
  python scripts/football/discord.py free  --week 2026-09-01 --dry-run
  python scripts/football/discord.py slate --week 2026-09-01 --dry-run
  python scripts/football/discord.py slate --week 2026-09-01
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import page as fbpage                                # noqa: E402
from post_discord import webhook_host_ok, FOOTER     # noqa: E402

ROOT = os.path.join(HERE, "..", "..")
STATUS_PATH = os.path.join(ROOT, "data", "post_status.json")
STATUS_KEEP = 30
SITE = (os.environ.get("SITE_URL", "").strip()
        or "https://openledgersports.com").rstrip("/")

FREE_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
MEMBERS_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL_MEMBERS", "")

BLUE, GREEN, GREY = 0x2C7BE5, 0x2E9E5B, 0x6B7280

# KEEP MESSAGE TEXT INSIDE cp1252. Discord renders any unicode fine, but
# --dry-run prints these to a console, and on Windows that console is cp1252: a
# "->" arrow (U+2192) is outside it and crashed the first real dry-run with
# UnicodeEncodeError. Em-dashes and middots are inside cp1252 and are fine, so
# this is not an ASCII rule - it is "nothing exotic in text a human previews".
# The dry-run is how copy gets checked before it reaches members, and a preview
# that only works on the CI runner is not a preview.

# Discord's own limits. Embed descriptions cap at 4096 and the TOTAL characters
# across all embeds in one message cap at 6000, which is the binding constraint
# here: a writeup plus its numbers runs ~700 characters, so ten embeds would
# overflow a limit that ten-per-message alone would not catch.
MAX_EMBEDS = 10
MAX_EMBED_CHARS = 5200          # under 6000, with headroom for titles
PACE = 1.2                      # seconds between messages; webhooks rate-limit


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------

def load_status():
    if os.path.exists(STATUS_PATH):
        try:
            with io.open(STATUS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            pass
    return {"posts": []}


def already_posted(week, mode):
    for p in load_status().get("posts", []):
        if (p.get("date") == week and p.get("mode") == mode
                and p.get("result") == "posted"):
            return True
    return False


def record(week, mode, result, status=None, detail=""):
    """Same file and schema as post_discord.py / send_email.py."""
    try:
        log = load_status()
        log["posts"] = [p for p in log.get("posts", [])
                        if not (p.get("date") == week and p.get("mode") == mode)]
        log["posts"].append({
            "date": week, "mode": mode, "result": result,
            "http_status": status, "detail": str(detail)[:200],
            "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        log["posts"] = sorted(log["posts"], key=lambda p: p["at_utc"])[-STATUS_KEEP:]
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        with io.open(STATUS_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:                         # noqa: BLE001
        print(f"NOTE: could not record post status: {exc}")


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------

NOCLAIM = ("No expectation claim is made. Two published studies found this "
           "market cannot be out-forecast at the moment we can act, so this is "
           "sold as process and receipts, not as an edge. Staked at 0 units, "
           "and every play is graded in public.")


def money(p):
    return f"{p:+d}" if isinstance(p, int) else str(p)


def game_embed(g, color=GREY, label=None):
    lines = []
    if g.get("writeup"):
        lines.append(g["writeup"])
    lines.append(
        f"**{g.get('side')}** {money(g.get('best_price'))} at "
        f"{g.get('best_book')} | {g.get('books_at_best')} Tier-1 books at/near it")
    fair = g.get("fair_side")
    lines.append(
        f"de-vigged fair {fair * 100:.1f}% | overround "
        f"{g.get('raw_overround_pts')} -> **{g.get('eff_overround_pts')}** pts at "
        f"best | {g.get('n_books')} books")
    off = g.get("offshore_best")
    if off:
        lines.append(f"offshore colour only: {money(off.get('price'))} "
                     f"at {off.get('book')}")
    title = f"{g.get('league','')} - {g.get('matchup','')}"
    if label:
        title = f"{label} — {title}"
    return {"title": title[:256], "description": "\n".join(lines)[:4096],
            "color": color}


def chunk(embeds):
    """Group embeds under BOTH Discord limits: count and total characters."""
    out, cur, chars = [], [], 0
    for e in embeds:
        n = len(e.get("title", "")) + len(e.get("description", ""))
        if cur and (len(cur) >= MAX_EMBEDS or chars + n > MAX_EMBED_CHARS):
            out.append(cur)
            cur, chars = [], 0
        cur.append(e)
        chars += n
    if cur:
        out.append(cur)
    return out


def free_messages(b, week):
    g = b.get("free")
    url = f"{SITE}/football/{week}/"
    if not g:
        return [{"username": "Open Ledger Sports",
                 "content": (f"**Football — week of {week}**\nNo qualifying "
                             f"free play this week. Passing is a position.\n"
                             f"{url}\n_{FOOTER}_")}]
    return [{
        "username": "Open Ledger Sports",
        "content": (f"**Football — the free play, week of {week}**\n{url}"),
        "embeds": [game_embed(g, GREEN, "FREE PLAY")],
    }, {
        "username": "Open Ledger Sports",
        "content": f"_{NOCLAIM}_\n_{FOOTER}_",
    }]


def slate_messages(b, week):
    url = f"{SITE}/football/{week}/"
    head = (f"**Football — the full slate, week of {week}**\n"
            f"{b.get('n_covered', 0)} games covered | "
            f"{b.get('n_excluded', 0)} no market | "
            f"coverage: {b.get('coverage_status', 'covered')}\n"
            f"{url}")
    msgs = [{"username": "Open Ledger Sports", "content": head}]

    prem, free = b.get("premium"), b.get("free")
    top = []
    if prem:
        top.append(game_embed(prem, BLUE, "PREMIUM PLAY · 0 units"))
    if free:
        top.append(game_embed(free, GREEN, "FREE PLAY"))
    if top:
        msgs.append({"username": "Open Ledger Sports", "embeds": top})

    rest = [g for g in b.get("games", []) if g.get("tier") == "slate"]
    for i, group in enumerate(chunk([game_embed(g) for g in rest])):
        msgs.append({"username": "Open Ledger Sports",
                     "content": (f"**The rest of the slate** "
                                 f"({len(rest)} games, tightest market first)"
                                 if i == 0 else None),
                     "embeds": group})

    nm = b.get("no_market", [])
    if nm:
        lines = [f"• {n.get('matchup','')} — {n.get('reason','')}" for n in nm]
        body = "\n".join(lines)
        # Named, never silently dropped (spec s.3). Clip rather than omit, and
        # say so, because a truncated list that looks complete is worse than a
        # short one that admits it.
        if len(body) > 1600:
            body = body[:1600].rsplit("\n", 1)[0] + f"\n… full list at {url}"
        msgs.append({"username": "Open Ledger Sports",
                     "content": f"**No market ({len(nm)})**\n{body}"})

    msgs.append({"username": "Open Ledger Sports",
                 "content": f"_{NOCLAIM}_\n_{FOOTER}_"})
    return [{k: v for k, v in m.items() if v is not None} for m in msgs]


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------

def send(webhook, messages, dry=False):
    """Post each message in order. Returns (ok, last_status, detail)."""
    if dry:
        for i, m in enumerate(messages, 1):
            print(f"--- message {i}/{len(messages)} ---")
            if m.get("content"):
                print(m["content"])
            for e in m.get("embeds", []):
                print(f"  [embed] {e['title']}")
                print("    " + e["description"].replace("\n", "\n    "))
        return True, None, "dry-run"
    last = None
    for i, m in enumerate(messages, 1):
        for attempt in range(3):
            try:
                r = requests.post(webhook, json=m, timeout=20)
            except requests.RequestException as exc:
                return False, None, f"message {i}: {exc}"
            last = r.status_code
            if r.status_code == 429:
                # Honour Discord's own backoff rather than guessing.
                wait = 2.0
                try:
                    wait = float(r.json().get("retry_after", 2.0))
                except ValueError:
                    pass
                print(f"    rate limited, waiting {wait:.1f}s")
                time.sleep(min(wait + 0.2, 10))
                continue
            if r.status_code >= 300:
                return False, r.status_code, f"message {i}: {r.text[:160]}"
            break
        else:
            return False, last, f"message {i}: still rate limited after 3 tries"
        time.sleep(PACE)
    return True, last, f"{len(messages)} messages"


MODES = {
    "free":  ("fb_free", FREE_WEBHOOK, "DISCORD_WEBHOOK_URL", free_messages),
    "slate": ("fb_slate", MEMBERS_WEBHOOK, "DISCORD_WEBHOOK_URL_MEMBERS",
              slate_messages),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=sorted(MODES))
    ap.add_argument("--week", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="post even if this week already went out")
    args = ap.parse_args()

    status_mode, webhook, varname, builder = MODES[args.mode]
    week = args.week

    if not args.dry_run and not args.force and already_posted(week, status_mode):
        print(f"{args.mode}: week {week} already posted; skipping. (--force "
              f"overrides, but a week is many messages - be sure.)")
        return 0

    b, revealed = fbpage.load_board(week)
    if b is None:
        print(f"no board for {week}; nothing to post.")
        # NOT under --dry-run. A preview that writes state is not a preview, and
        # this one did: two dry-runs left "no_board" rows in post_status.json,
        # which is a file CI commits. Every other write in this file was already
        # guarded; this path was missed.
        if not args.dry_run:
            record(week, status_mode, "no_board")
        return 0
    if not b.get("decision_made"):
        print(f"week {week} has no chosen play yet (decision moment "
              f"{b.get('decision_moment_utc')}); not posting.")
        return 0

    messages = builder(b, week)
    print(f"{args.mode}: {len(messages)} message(s) for week {week}")

    if not args.dry_run:
        if not webhook:
            print(f"{varname} is not set; skipping (this never fails a run).")
            record(week, status_mode, "no_config")
            return 0
        if not webhook_host_ok(webhook):
            print(f"WARNING: {varname} is not a discord.com URL — refusing to send.")
            record(week, status_mode, "refused", detail="webhook host not discord")
            return 0

    ok, status, detail = send(webhook, messages, dry=args.dry_run)
    if args.dry_run:
        print("\n(--dry-run: nothing sent, nothing recorded)")
        return 0
    record(week, status_mode, "posted" if ok else "failed", status, detail)
    print(("posted " if ok else "FAILED ") + str(detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
