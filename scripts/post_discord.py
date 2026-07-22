#!/usr/bin/env python3
"""
Open Ledger Sports — Discord webhook poster.
Two modes, wired into the two daily workflows:

  pick   (after the morning board)  — posts the Free Pick of the Day as a rich
         embed, plus a one-line teaser of the rest of the board.
  board  (after the morning board)  — posts every held play in full to the
         members channel. Never includes the free pick (house rule 2).
  recap  (after nightly grading)    — posts yesterday's graded results and the
         running ledger (record, units, ROI).

Setup: Discord server -> channel -> Edit channel -> Integrations -> Webhooks ->
New Webhook -> Copy URL -> save it as the repo secret DISCORD_WEBHOOK_URL.
Optional: set a SITE_URL secret/variable to link posts back to the site.

No webhook configured? The script exits 0 with a note — it never fails a run.

Run:  python scripts/post_discord.py pick  [YYYY-MM-DD] [--dry-run]
      python scripts/post_discord.py board [YYYY-MM-DD] [--dry-run]
      python scripts/post_discord.py recap [YYYY-MM-DD] [--dry-run]
"""
import json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import crypto_box

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
# Separate channel for the held plays. Results always go to the public channel:
# the record stays public even when the picks are not.
MEMBERS_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL_MEMBERS", "")
SITE = os.environ.get("SITE_URL", "").rstrip("/")

MAX_EMBEDS = 10   # Discord's hard limit per message

BLUE, GREEN, ORANGE, RED, GRAY = 0x3987E5, 0x0CA30C, 0xEC835A, 0xD03B3B, 0x898781
TIER_COLOR = {"Low Risk (Safe Play)": GREEN, "Moderate Risk (Value Play)": ORANGE,
              "High Risk (Longshot)": RED}

FOOTER = "Open Ledger Sports · analytics, not betting advice · 21+ · 1-800-GAMBLER"

def load(path):
    p = os.path.join(ROOT, "data", path)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)

# %-d and %-I (strip the leading zero) only exist in glibc's strftime, so they
# work on the Linux runner but crash on Windows. Compose those parts by hand.
def et_time(utc_str):
    t = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
    return f"{t.hour % 12 or 12}:{t:%M %p} ET"

def pick_free(plays):
    """Must mirror build_site.py: cleanest lower-board play, never the headliner."""
    if not plays:
        return None
    return next((b for b in reversed(plays) if not b["rule4_flag"] and not b["rule2_pivot"]),
                plays[len(plays) // 2])

def build_pick_payload(date):
    # Must go through crypto_box: from 2026-07-23 the board is encrypted
    # until grading, and a plain file read would silently find nothing and
    # post no picks at all while the workflow still went green.
    B = crypto_box.load_dataset(ROOT, "board", date)
    if B is None:
        print(f"No board for {date} — nothing to post.")
        return None
    plays = sorted([b for b in B["board"] if b.get("published")], key=lambda b: -b["confidence"])
    _d = datetime.strptime(date, "%Y-%m-%d")
    nice = f"{_d:%A, %B} {_d.day}"

    if not plays:
        embed = {
            "title": f"No qualifying plays today: {nice}",
            "description": ("The engine ran the full slate and nothing cleared the circuit breakers "
                            "and the edge gate at an allocatable price. We don't manufacture a pick to "
                            "fill the slot. **Passing is a position too.**"),
            "color": GRAY,
            "footer": {"text": FOOTER},
        }
        if SITE:
            embed["url"] = SITE + "/#board"
        return {"embeds": [embed]}

    free = pick_free(plays)
    a_sp, h_sp = free["awaySP"], free["homeSP"]
    fields = [
        {"name": "Play", "value": f'**{free["pick"]}**', "inline": True},
        {"name": "Confidence", "value": f'{free["confidence"]*100:.1f}% of {free["n_sims"]:,} sims', "inline": True},
        {"name": "Suggested", "value": f'{free["units"]:g}u ({free["units"]:g}% bankroll)', "inline": True},
        {"name": "Projected", "value": f'{free["proj_away"]:g}–{free["proj_home"]:g}', "inline": True},
        {"name": "Model fair", "value": f'{free["fair_away"]:+d} / {free["fair_home"]:+d}', "inline": True},
    ]
    if free["mkt_odds"] is not None:
        fields.append({"name": "Edge vs price",
                       "value": f'{free["edge"]*100:+.1f} pts · EV {free["ev_per_unit"]*100:+.1f}%', "inline": True})
    else:
        fields.append({"name": "Market", "value": "no feed this run; compare at your book", "inline": True})
    fields.append({"name": "Circuit breakers",
                   "value": "\n".join(f"• {c}" for c in free["checks"])[:1024]})

    others = [b for b in plays if b is not free]
    if others:
        # Matchups only. This embed goes to the PUBLIC channel, so printing unit
        # sizes here would hand out the premium board's shape for free, and the
        # site stopped showing them when the board was gated.
        teaser = " · ".join(b["abbr"] for b in others)
        tail = ". Premium members get these in full before first pitch."
        fields.append({"name": f"Also on today's board ({len(others)} premium)",
                       "value": (teaser + tail)[:1024]})

    embed = {
        "title": f'★ Free Pick of the Day: {nice}',
        "description": (f'**{free["matchup"]}** · {et_time(free["utc"])} · {free["venue"]}\n'
                        f'{a_sp["name"]} ({a_sp["era"]:.2f} ERA) vs {h_sp["name"]} ({h_sp["era"]:.2f} ERA)\n'
                        f'*A strong play, but not our Play of the Day: the top-confidence plays go '
                        f'to premium members. Committed to the public record before first pitch, '
                        f'graded on the ledger after.*'),
        "color": TIER_COLOR.get(free["risk_tier"], BLUE),
        "fields": fields,
        "footer": {"text": FOOTER},
    }
    if SITE:
        embed["url"] = SITE
    return {"username": "Open Ledger Sports", "embeds": [embed]}

def build_board_payload(date):
    """Members post: every published play EXCEPT the free one, in full.

    House rule 2 keeps the free pick out of here. It is already public, and
    pick_free must stay mirrored with build_site.py so the two never disagree
    about which play is the giveaway.
    """
    # Must go through crypto_box: from 2026-07-23 the board is encrypted
    # until grading, and a plain file read would silently find nothing and
    # post no picks at all while the workflow still went green.
    B = crypto_box.load_dataset(ROOT, "board", date)
    if B is None:
        print(f"No board for {date}: nothing to post.")
        return None
    plays = sorted([b for b in B["board"] if b.get("published")], key=lambda b: -b["confidence"])
    free = pick_free(plays)
    held = [b for b in plays if b is not free]
    if not held:
        print(f"No held plays for {date}: the free pick was the only allocation.")
        return None

    _d = datetime.strptime(date, "%Y-%m-%d")
    nice = f"{_d:%A, %B} {_d.day}"
    shown, dropped = held[:MAX_EMBEDS], held[MAX_EMBEDS:]

    embeds = []
    for b in shown:
        a_sp, h_sp = b["awaySP"], b["homeSP"]
        fields = [
            {"name": "Play", "value": f'**{b["pick"]}**', "inline": True},
            {"name": "Confidence", "value": f'{b["confidence"]*100:.1f}% of {b["n_sims"]:,} sims', "inline": True},
            {"name": "Suggested", "value": f'{b["units"]:g}u ({b["units"]:g}% bankroll)', "inline": True},
            {"name": "Projected", "value": f'{b["proj_away"]:g}–{b["proj_home"]:g}', "inline": True},
            {"name": "Model fair", "value": f'{b["fair_away"]:+d} / {b["fair_home"]:+d}', "inline": True},
        ]
        if b["mkt_odds"] is not None:
            fields.append({"name": "Edge vs price",
                           "value": f'{b["edge"]*100:+.1f} pts · EV {b["ev_per_unit"]*100:+.1f}%', "inline": True})
        else:
            fields.append({"name": "Market", "value": "no feed this run; compare at your book", "inline": True})
        fields.append({"name": "Circuit breakers",
                       "value": "\n".join(f"• {c}" for c in b["checks"])[:1024]})
        embeds.append({
            "title": b["matchup"],
            "description": (f'{et_time(b["utc"])} · {b["venue"]}\n'
                            f'{a_sp["name"]} ({a_sp["era"]:.2f} ERA) vs {h_sp["name"]} ({h_sp["era"]:.2f} ERA)'),
            "color": TIER_COLOR.get(b["risk_tier"], BLUE),
            "fields": fields,
        })
    embeds[-1]["footer"] = {"text": FOOTER + " · every one of these lands on the public ledger once graded"}

    held_units = sum(b["units"] for b in held)
    lead = (f"**Members board: {nice}** · {len(held)} held "
            f"{'play' if len(held) == 1 else 'plays'} · {held_units:g}u exposure")
    if dropped:
        lead += f" · {len(dropped)} more on the site once graded"
    if SITE:
        lead += f"\nFree pick and full methodology: {SITE}"
    return {"username": "Open Ledger Sports", "content": lead, "embeds": embeds}

def build_recap_payload(date):
    L = load("ledger.json") or {"entries": [], "aggregates": None}
    entries = [e for e in L["entries"] if e["date"] == date]
    if not entries:
        print(f"No graded entries for {date} — nothing to post.")
        return None
    _d = datetime.strptime(date, "%Y-%m-%d")
    nice = f"{_d:%A, %B} {_d.day}"
    chip = {"WIN": "✅", "LOSS": "❌", "VOID": "⚪"}
    day_pnl = sum(e["pnl"] for e in entries)
    lines = [f'{chip.get(e["result"], "•")} {e["pick"]}: {e.get("final_score", "void")} '
             f'({e["pnl"]:+.2f}u)' for e in entries]
    agg = L["aggregates"]
    embed = {
        "title": f"Results for {nice}: {day_pnl:+.2f}u",
        "description": "\n".join(lines)[:4000],
        "color": GREEN if day_pnl > 0 else (RED if day_pnl < 0 else GRAY),
        "fields": [
            {"name": "Ledger", "value": f'**{agg["record"]}** · {agg["units_net"]:+.2f}u net'
                                        + (f' · ROI {agg["roi_pct"]:+.1f}%' if agg["roi_pct"] is not None else ""),
             "inline": True},
        ],
        "footer": {"text": FOOTER + " · every result on the record, wins and losses alike"},
    }
    if SITE:
        embed["url"] = SITE + "/#ledger"
    return {"username": "Open Ledger Sports", "embeds": [embed]}

# mode -> (payload builder, webhook, env var name for the skip message)
def routes():
    return {
        "pick":  (build_pick_payload,  WEBHOOK,         "DISCORD_WEBHOOK_URL"),
        "board": (build_board_payload, MEMBERS_WEBHOOK, "DISCORD_WEBHOOK_URL_MEMBERS"),
        "recap": (build_recap_payload, WEBHOOK,         "DISCORD_WEBHOOK_URL"),
    }

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "pick"
    table = routes()
    if mode not in table:
        print(f"Unknown mode {mode!r}. Use one of: {', '.join(table)}.")
        return
    build, webhook, env_name = table[mode]

    if mode == "recap":
        default = (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        default = datetime.now(ET).strftime("%Y-%m-%d")
    date = args[1] if len(args) > 1 else default
    dry = "--dry-run" in sys.argv

    payload = build(date)
    if payload is None:
        return
    if dry:
        print(json.dumps(payload, indent=2))
        return
    if not webhook:
        print(f"NOTE: {env_name} not set — skipping Discord post (board is unaffected).")
        return
    import requests
    r = requests.post(webhook, json=payload, timeout=30)
    if r.status_code >= 300:
        # Never fail the pipeline over a chat post — log and move on.
        print(f"WARNING: Discord post failed ({r.status_code}): {r.text[:300]}")
    else:
        print(f"Posted {mode} for {date} to Discord.")

if __name__ == "__main__":
    main()
