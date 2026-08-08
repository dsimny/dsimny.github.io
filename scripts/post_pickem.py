#!/usr/bin/env python3
"""
Open Ledger Sports — pick'em featured-game announce (morning board step).

Posts the day's "Beat the Engine" prompt to the free Discord channel with two
buttons (ride / fade). The featured game is the FREE PICK when one publishes,
else the ✳ Best of Board — both already public in full, so the prompt reveals
nothing the site doesn't.

Posted via the BOT token, not a webhook: webhook messages can't carry
interactive components. Button custom_ids encode everything the Worker needs
(side, date, lock epoch = first pitch), so the Worker stays stateless about
the slate. Grading enforces the lock again from the same timestamp — the
button refusing late presses is UX, the grader is the law.

Skips cleanly (exit 0) when DISCORD_BOT_TOKEN or PICKEM_CHANNEL_ID is unset —
the pilot ships dark and lights up when the secrets land. Like the other
Discord posts this is NOT idempotent: a second morning run posts again.

Run: python scripts/post_pickem.py [YYYY-MM-DD] [--dry-run]
"""
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import crypto_box

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("PICKEM_CHANNEL_ID", "")
SITE = (os.environ.get("SITE_URL", "").strip() or "https://openledgersports.com").rstrip("/")

FOOTER = ("No prizes, no stakes — points and bragging rights only. Analytics, not "
          "betting advice · 21+ · 1-800-GAMBLER")


def pick_free(plays):
    """Must mirror build_site.py / post_discord.py / blog.py (House Rule 2)."""
    if not plays:
        return None
    return next((b for b in reversed(plays) if not b["rule4_flag"] and not b["rule2_pivot"]),
                plays[len(plays) // 2])


def select_featured(B):
    """The day's featured game: free pick, else the v0.15 Daily Pick, else
    ✳ Best of Board. One source of truth — grade_pickem.py imports this so
    announce and grading can never disagree about which game the community
    was betting on. All three candidates are public rows by construction."""
    plays = sorted([b for b in B["board"] if b.get("published")], key=lambda b: -b["confidence"])
    free = pick_free(plays)
    if free is not None:
        return free, "free pick"
    dp = B.get("daily_pick")
    if dp is not None:
        dpr = next((b for b in B["board"] if b.get("gamePk") == dp["gamePk"]), None)
        if dpr is not None:
            return dpr, "daily pick"
    bob = next((b for b in B["board"] if b.get("best_of_board")), None)
    if bob is not None:
        return bob, "best of board"
    return None, None


def et_time(utc_str):
    t = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
    return f"{t.hour % 12 or 12}:{t:%M %p} ET"


def build_payload(date):
    B = crypto_box.load_dataset(ROOT, "board", date)
    if B is None:
        print(f"No board for {date} — no pick'em to post.")
        return None
    featured, source = select_featured(B)
    if featured is None:
        print(f"No featured game for {date} (no free pick, no best-of-board) — skipping pick'em.")
        return None

    lock = int(datetime.fromisoformat(featured["utc"].replace("Z", "+00:00"))
               .replace(tzinfo=timezone.utc).timestamp())
    if lock <= int(datetime.now(timezone.utc).timestamp()):
        print(f"Featured game for {date} has already started — no pick'em today.")
        return None

    tag = {"free pick": "★ today's free pick",
           "daily pick": "🎯 today's Daily Pick (0u proving)",
           "best of board": "✳ today's Best of Board (0u lean)"}[source]
    embed = {
        "title": f"🎯 Beat the Engine: {featured['matchup']}",
        "description": (f"{et_time(featured['utc'])} · {featured['venue']}\n"
                        f"The engine's side ({tag}): **{featured['pick']}**\n\n"
                        f"With it or against it? One pick a day, graded overnight, standings "
                        f"posted here every morning. Change your mind as often as you like "
                        f"until first pitch — the last press counts.\n"
                        f"[The engine's full reasoning]({SITE}/?utm_source=discord&utm_medium=pickem)"),
        "color": 0x3987E5,
        "footer": {"text": FOOTER},
    }
    components = [{
        "type": 1,
        "components": [
            {"type": 2, "style": 1, "label": "🤝 Ride with the engine",
             "custom_id": f"pickem:ride:{date}:{lock}"},
            {"type": 2, "style": 4, "label": "⚔️ Fade the engine",
             "custom_id": f"pickem:fade:{date}:{lock}"},
        ],
    }]
    return {"embeds": [embed], "components": components}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date = args[0] if args else datetime.now(ET).strftime("%Y-%m-%d")
    dry = "--dry-run" in sys.argv

    payload = build_payload(date)
    if payload is None:
        return
    if dry:
        print(json.dumps(payload, indent=2))
        return
    if not BOT_TOKEN or not CHANNEL_ID:
        print("NOTE: DISCORD_BOT_TOKEN / PICKEM_CHANNEL_ID not set — pick'em post skipped "
              "(board is unaffected).")
        return

    import requests
    r = requests.post(f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
                      headers={"Authorization": f"Bot {BOT_TOKEN}"},
                      json=payload, timeout=30)
    if r.status_code >= 300:
        # Never fail the board over a chat post.
        print(f"WARNING: pick'em post failed ({r.status_code}): {r.text[:300]}")
        return
    print(f"Posted pick'em featured game for {date}.")


if __name__ == "__main__":
    main()
