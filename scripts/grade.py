#!/usr/bin/env python3
"""
Open Ledger Sports — nightly grader.
Fetches final scores for a date's board, grades every PUBLISHED pick, and
appends results to data/ledger.json (the append-only public ledger).

Grading rules:
  - Moneyline pick: pick team scored more runs. Payout at the logged market
    odds (falls back to the model fair line if no market odds were logged,
    and says so in the entry).
  - Run-line -1.5 pick: pick team won by 2+.
  - Game not Final (postponed/suspended): VOID — stake returned, logged.
Ledger entries are never edited after grading; aggregates are recomputed
from the full entry list every run.

Run: python scripts/grade.py [YYYY-MM-DD]   (defaults to yesterday, ET)
Test: python scripts/grade.py YYYY-MM-DD --scores-file path.json
      where the file maps gamePk -> {"away": runs, "home": runs, "final": true}
"""
import json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LEDGER = os.path.join(ROOT, "data", "ledger.json")

def american_to_b(odds):
    return 100 / (-odds) if odds < 0 else odds / 100

def fair_pick_odds(b):
    """Model fair odds for the pick side (used only if no market odds logged)."""
    return b["fair_home"] if b["pick_team_abbr"] == b["abbr"].split(" @ ")[1] else b["fair_away"]

def fetch_scores(date):
    import requests
    r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1, "date": date, "hydrate": "linescore"}, timeout=30)
    r.raise_for_status()
    out = {}
    for d in r.json().get("dates", []):
        for g in d["games"]:
            state = g.get("status", {}).get("abstractGameState")
            ls = g.get("linescore", {})
            out[str(g["gamePk"])] = {
                "away": ls.get("teams", {}).get("away", {}).get("runs"),
                "home": ls.get("teams", {}).get("home", {}).get("runs"),
                "final": state == "Final",
            }
    return out

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date = args[0] if args else (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).strftime("%Y-%m-%d")

    board_path = os.path.join(ROOT, "data", f"board_{date}.json")
    if not os.path.exists(board_path):
        print(f"No board for {date} — nothing to grade.")
        return
    with open(board_path, encoding="utf-8") as f:
        B = json.load(f)

    if "--scores-file" in sys.argv:
        with open(sys.argv[sys.argv.index("--scores-file") + 1], encoding="utf-8") as f:
            scores = json.load(f)
    else:
        scores = fetch_scores(date)

    ledger = {"entries": []}
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            ledger = json.load(f)
    already = {(e["date"], e["gamePk"]) for e in ledger["entries"]}

    graded = 0
    for b in B["board"]:
        if not b.get("published"):
            continue
        key = (date, b.get("gamePk"))
        if key in already:
            continue
        sc = scores.get(str(b.get("gamePk", "")))
        entry = {
            "date": date, "gamePk": b.get("gamePk"), "game": b["abbr"],
            "pick": b["pick"], "units": b["units"],
            "confidence": b["confidence"], "edge": b.get("edge"),
            "odds_basis": b.get("mkt_odds"),
        }
        if sc is None or not sc.get("final") or sc.get("away") is None:
            entry.update(result="VOID", pnl=0.0,
                         note="Game not final (postponed/suspended) — stake returned.")
        else:
            a_ab, h_ab = b["abbr"].split(" @ ")
            pick_is_home = b["pick_team_abbr"] == h_ab
            margin = (sc["home"] - sc["away"]) if pick_is_home else (sc["away"] - sc["home"])
            won = margin > 1.5 if "run line" in b["pick"] else margin > 0
            odds = b.get("mkt_odds")
            if odds is None:
                odds = fair_pick_odds(b)
                entry["note"] = "No market odds logged — graded at model fair line."
            entry["final_score"] = f'{sc["away"]}-{sc["home"]}'
            if won:
                entry.update(result="WIN", pnl=round(b["units"] * american_to_b(odds), 3))
            else:
                entry.update(result="LOSS", pnl=-b["units"])
        ledger["entries"].append(entry)
        graded += 1

    # ---- Recompute aggregates from the full, append-only entry list ----
    ent = ledger["entries"]
    wins = sum(1 for e in ent if e["result"] == "WIN")
    losses = sum(1 for e in ent if e["result"] == "LOSS")
    voids = sum(1 for e in ent if e["result"] == "VOID")
    units_net = round(sum(e["pnl"] for e in ent), 3)
    units_risked = round(sum(e["units"] for e in ent if e["result"] != "VOID"), 3)
    ledger["aggregates"] = {
        "record": f"{wins}-{losses}" + (f"-{voids}v" if voids else ""),
        "wins": wins, "losses": losses, "voids": voids,
        "units_net": units_net, "units_risked": units_risked,
        "roi_pct": round(100 * units_net / units_risked, 2) if units_risked else None,
        "opened": "2026-07-22",
        "last_graded": date,
    }
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1)
    print(f"Graded {graded} picks for {date}. Ledger: {ledger['aggregates']['record']}, "
          f"{units_net:+.2f}u net, ROI {ledger['aggregates']['roi_pct']}%")

if __name__ == "__main__":
    main()
