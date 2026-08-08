#!/usr/bin/env python3
"""
Open Ledger Sports — pick'em grading (grade-ledger step, after grade.py).

Pulls the day's entries from the Worker, grades them against the featured
game's final score, pushes per-user results back to the Worker (which keeps
the ONLY copy of names/ids and returns the named leaderboards), commits the
AGGREGATES to data/pickem.json, and posts results + leaderboard to Discord.

Privacy split, deliberate: the public repo never stores a Discord name or id.
Per-day participation identity is committed as HMAC-SHA256(READ_TOKEN,
user_id) truncated — enough for the Monday audit to compute unique/repeat
participants in CI, not reversible without the secret.

The grader is the law on locking: only entries stamped before first pitch
count, regardless of what the button accepted. Idempotent: a date already in
pickem.json is never re-graded (and the Worker skips re-applied results too).

Skips cleanly when PICKEM_WORKER_URL / PICKEM_READ_TOKEN are unset.

Run:  python scripts/grade_pickem.py [YYYY-MM-DD]
      offline test: --entries-file f.json --scores-file s.json [--dry-run]
        entries-file: {"entries": [{"user_id","user_name","side","ts"}, ...]}
        scores-file:  {"<gamePk>": {"away": 3, "home": 5, "final": true}}
"""
import hashlib
import hmac as hmac_mod
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import crypto_box
from post_pickem import select_featured

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PICKEM_PATH = os.path.join(ROOT, "data", "pickem.json")

WORKER = os.environ.get("PICKEM_WORKER_URL", "").rstrip("/")
TOKEN = os.environ.get("PICKEM_READ_TOKEN", "")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("PICKEM_CHANNEL_ID", "")

FOOTER = ("No prizes, no stakes — points and bragging rights only. Analytics, not "
          "betting advice · 21+ · 1-800-GAMBLER")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def engine_result(featured, score):
    """WIN/LOSS/VOID for the ENGINE's side, mirroring grade.py's rules:
    moneyline by winner, -1.5 run line by margin >= 2, VOID on non-final."""
    if not score or not score.get("final") or score.get("away") is None:
        return "VOID"
    away_ab, home_ab = featured["abbr"].split(" @ ")
    pick_home = featured["pick_team_abbr"] == home_ab
    margin = (score["home"] - score["away"]) if pick_home else (score["away"] - score["home"])
    if "run line" in featured["pick"].lower():
        return "WIN" if margin >= 2 else "LOSS"
    if margin == 0:
        return "VOID"   # shouldn't happen in MLB, but never guess
    return "WIN" if margin > 0 else "LOSS"


def fetch_score(date, game_pk):
    import requests
    r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1, "date": date, "hydrate": "linescore"}, timeout=30)
    r.raise_for_status()
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            if g["gamePk"] == game_pk:
                ls = g.get("linescore", {}) or {}
                teams = ls.get("teams", {}) or {}
                final = (g.get("status", {}) or {}).get("abstractGameState") == "Final"
                return {"away": (teams.get("away") or {}).get("runs"),
                        "home": (teams.get("home") or {}).get("runs"),
                        "final": final}
    return None


def entry_hmac(user_id):
    return hmac_mod.new(TOKEN.encode() or b"offline-test",
                        str(user_id).encode(), hashlib.sha256).hexdigest()[:16]


def recompute_aggregates(days):
    wins = sum(d["community"]["wins"] for d in days)
    losses = sum(d["community"]["losses"] for d in days)
    voids = sum(d["community"]["voids"] for d in days)
    e_wins = sum(1 for d in days if d["engine_result"] == "WIN")
    e_losses = sum(1 for d in days if d["engine_result"] == "LOSS")
    uniq = {h for d in days for h in d.get("entry_hmacs", [])}
    return {
        "community_record": f"{wins}-{losses}" + (f"-{voids}v" if voids else ""),
        "community_wins": wins, "community_losses": losses, "community_voids": voids,
        "engine_days": f"{e_wins}-{e_losses}",
        "total_entries": sum(d["n_entries"] for d in days),
        "distinct_participants": len(uniq),
        "days_run": len(days),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=")[0] for a in sys.argv[1:] if a.startswith("--")}
    def flag_val(name):
        for i, a in enumerate(sys.argv[1:], 1):
            if a == name and i < len(sys.argv) - 1:
                return sys.argv[i + 1]
        return None
    date = args[0] if args and not args[0].endswith(".json") else \
        (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    entries_file = flag_val("--entries-file")
    scores_file = flag_val("--scores-file")
    dry = "--dry-run" in flags
    offline = entries_file is not None

    store = load_json(PICKEM_PATH, {"days": [], "aggregates": None})
    if any(d["date"] == date for d in store["days"]):
        print(f"Pick'em for {date} already graded — nothing to do.")
        return

    if not offline and (not WORKER or not TOKEN):
        print("NOTE: PICKEM_WORKER_URL / PICKEM_READ_TOKEN not set — pick'em grading skipped.")
        return

    # The featured game, from the now-REVEALED board (plaintext after grade.py).
    plain, _ = crypto_box.paths_for(ROOT, "board", date)
    if not os.path.exists(plain):
        print(f"Board for {date} not revealed — cannot grade pick'em (grade.py runs first).")
        return
    with open(plain, encoding="utf-8") as f:
        B = json.load(f)
    featured, source = select_featured(B)
    if featured is None:
        print(f"No featured game existed for {date} — no pick'em to grade.")
        return
    lock = int(datetime.fromisoformat(featured["utc"].replace("Z", "+00:00"))
               .replace(tzinfo=timezone.utc).timestamp())

    # Entries.
    if offline:
        entries = load_json(entries_file, {}).get("entries", [])
    else:
        import requests
        r = requests.get(f"{WORKER}/entries", params={"date": date},
                         headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
        r.raise_for_status()
        entries = r.json().get("entries", [])

    valid = [e for e in entries if e.get("ts", 0) < lock and e.get("side") in ("ride", "fade")]
    late = len(entries) - len(valid)

    # Outcome.
    score = (load_json(scores_file, {}).get(str(featured["gamePk"]))
             if scores_file else fetch_score(date, featured["gamePk"]))
    eng = engine_result(featured, score)
    opposite = {"WIN": "LOSS", "LOSS": "WIN", "VOID": "VOID"}
    results = [{"user_id": e["user_id"], "user_name": e.get("user_name"),
                "result": eng if e["side"] == "ride" else opposite[eng]} for e in valid]

    day = {
        "date": date, "gamePk": featured["gamePk"], "matchup": featured["matchup"],
        "engine_pick": featured["pick"], "engine_source": source, "engine_result": eng,
        "n_entries": len(valid), "n_late": late,
        "n_ride": sum(1 for e in valid if e["side"] == "ride"),
        "n_fade": sum(1 for e in valid if e["side"] == "fade"),
        "community": {
            "wins": sum(1 for r in results if r["result"] == "WIN"),
            "losses": sum(1 for r in results if r["result"] == "LOSS"),
            "voids": sum(1 for r in results if r["result"] == "VOID"),
        },
        "entry_hmacs": sorted(entry_hmac(e["user_id"]) for e in valid),
    }

    if dry:
        print(json.dumps(day, indent=2))
        return

    # Standings + named leaderboards live in the Worker, never the repo.
    boards = {"month_leaderboard": [], "season_leaderboard": []}
    if not offline:
        import requests
        r = requests.post(f"{WORKER}/results",
                          headers={"Authorization": f"Bearer {TOKEN}"},
                          json={"date": date, "results": results}, timeout=30)
        if r.status_code < 300:
            boards = r.json()
        else:
            print(f"WARNING: worker /results failed ({r.status_code}): {r.text[:200]}")

    store["days"] = sorted(store["days"] + [day], key=lambda d: d["date"])
    store["aggregates"] = recompute_aggregates(store["days"])
    os.makedirs(os.path.dirname(PICKEM_PATH), exist_ok=True)
    with open(PICKEM_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1)
    print(f"Graded pick'em {date}: engine {eng}, {day['n_entries']} entries "
          f"({day['n_ride']} ride / {day['n_fade']} fade, {late} late ignored).")

    # Discord results post.
    if not BOT_TOKEN or not CHANNEL_ID or offline:
        if not offline:
            print("NOTE: DISCORD_BOT_TOKEN / PICKEM_CHANNEL_ID not set — results post skipped.")
        return
    agg = store["aggregates"]
    chip = {"WIN": "✅ the engine's side WON", "LOSS": "❌ the engine's side LOST",
            "VOID": "⚪ no action (game not final)"}
    lb = "\n".join(
        f'{i}. **{r["user_name"] or "?"}** — {r["month_wins"]}-{r["month_losses"]}'
        + (f' · {"🔥" if r["streak"] >= 3 else ""}{r["streak"]:+d} streak' if r["streak"] else "")
        for i, r in enumerate(boards.get("month_leaderboard", []), 1)) or "*No graded entries yet.*"
    embed = {
        "title": f"🎯 Beat the Engine — results for {date}",
        "description": (f'**{featured["matchup"]}** · {featured["pick"]}\n{chip[eng]}.\n'
                        f'{day["n_ride"]} rode · {day["n_fade"]} faded → '
                        f'{day["community"]["wins"]} won, {day["community"]["losses"]} lost.\n\n'
                        f'**This month\'s leaderboard**\n{lb}\n\n'
                        f'Season, community vs engine: community picks are '
                        f'**{agg["community_record"]}**; the engine\'s featured side has gone '
                        f'**{agg["engine_days"]}**.'),
        "color": 0x0CA30C if eng == "WIN" else (0xD03B3B if eng == "LOSS" else 0x898781),
        "footer": {"text": FOOTER},
    }
    import requests
    r = requests.post(f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
                      headers={"Authorization": f"Bot {BOT_TOKEN}"},
                      json={"embeds": [embed]}, timeout=30)
    if r.status_code >= 300:
        print(f"WARNING: pick'em results post failed ({r.status_code}): {r.text[:300]}")
    else:
        print("Posted pick'em results + leaderboard.")


if __name__ == "__main__":
    main()
