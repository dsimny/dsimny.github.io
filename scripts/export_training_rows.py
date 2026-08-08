#!/usr/bin/env python3
"""
Open Ledger Sports — point-in-time training rows for the future challenger
model (per the v0.4 plan: logistic calibrator first, JSON artifact, never
pickle, chronological validation only).

Every REVEALED board is a point-in-time snapshot BY CONSTRUCTION: it was
built pregame, fingerprinted, and committed before first pitch, so exporting
features from it cannot leak post-game information. Market probabilities are
the board-time consensus — genuine decision-time odds, exactly what a
morning-pick model may know. Closing odds are deliberately NOT exported as
features (they'd leak information unavailable at pick time); outcomes come
from the MLB schedule after the fact and fill the label column only.

Output: data/model/training_rows.csv, append-only, deduped by (date,gamePk),
one row per simulated game with a final score. data/model/training_manifest.json
records row count, date span, and the file's sha256 for reproducibility.

Run:  python scripts/export_training_rows.py [YYYY-MM-DD]   (one date; nightly use)
      python scripts/export_training_rows.py --backfill     (every revealed board)
      First run with no CSV present backfills automatically.
      --scores-file f.json  offline test hook ({gamePk: {away, home, final}}).
"""
import csv
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT_DIR = os.path.join(ROOT, "data", "model")
CSV_PATH = os.path.join(OUT_DIR, "training_rows.csv")
MANIFEST = os.path.join(OUT_DIR, "training_manifest.json")

FIELDS = ["date", "game_pk", "first_pitch_utc",
          "p_sim_home", "p_market_home", "p_blend_home",
          "proj_home_runs", "proj_away_runs", "mean_total", "mkt_total",
          "park_factor", "starter_stab_diff", "starter_min_ip",
          "bullpen_diff", "woba_flag", "wx_present", "best_price_delta",
          "engine_version", "home_won"]


def implied(ml):
    return (-ml / (-ml + 100)) if ml < 0 else (100 / (ml + 100))


def devig_home(away_ml, home_ml):
    pa, ph = implied(away_ml), implied(home_ml)
    return ph / (pa + ph)


def fetch_finals(date):
    import requests
    r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1, "date": date, "hydrate": "linescore"}, timeout=30)
    r.raise_for_status()
    out = {}
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            ls = g.get("linescore", {}) or {}
            teams = ls.get("teams", {}) or {}
            out[str(g["gamePk"])] = {
                "away": (teams.get("away") or {}).get("runs"),
                "home": (teams.get("home") or {}).get("runs"),
                "final": (g.get("status", {}) or {}).get("abstractGameState") == "Final",
            }
    return out


def rows_for(date, scores):
    """Feature rows from one revealed board. Only games with a final score
    get a row — a postponed game has no label and is simply skipped."""
    path = os.path.join(ROOT, "data", f"board_{date}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        B = json.load(f)
    out = []
    for b in B.get("board", []):
        sc = scores.get(str(b.get("gamePk")))
        if not sc or not sc.get("final") or sc.get("away") is None:
            continue
        p_market_home = None
        if b.get("mkt_away_ml") is not None and b.get("mkt_home_ml") is not None:
            p_market_home = round(devig_home(b["mkt_away_ml"], b["mkt_home_ml"]), 4)
        best_delta = None
        if b.get("mkt_book") and b.get("mkt_odds") is not None and b.get("mkt_odds_consensus") is not None:
            best_delta = round(implied(b["mkt_odds_consensus"]) - implied(b["mkt_odds"]), 4)
        a_sp, h_sp = b.get("awaySP") or {}, b.get("homeSP") or {}
        stab_diff = None
        if a_sp.get("stab_rate") is not None and h_sp.get("stab_rate") is not None:
            stab_diff = round(a_sp["stab_rate"] - h_sp["stab_rate"], 3)  # + = home starter better
        pen_diff = None
        if b.get("away_pen_era") is not None and b.get("home_pen_era") is not None:
            pen_diff = round(b["away_pen_era"] - b["home_pen_era"], 3)   # + = home pen better
        out.append({
            "date": date, "game_pk": b.get("gamePk"), "first_pitch_utc": b.get("utc"),
            "p_sim_home": b.get("p_home_model"), "p_market_home": p_market_home,
            "p_blend_home": b.get("p_home"),
            "proj_home_runs": b.get("proj_home"), "proj_away_runs": b.get("proj_away"),
            "mean_total": b.get("mean_total"), "mkt_total": b.get("mkt_total"),
            "park_factor": b.get("park_factor"),
            "starter_stab_diff": stab_diff,
            "starter_min_ip": min(a_sp.get("ip", 0), h_sp.get("ip", 0)) or None,
            "bullpen_diff": pen_diff,
            "woba_flag": int(bool(b.get("rule6_flag"))),
            "wx_present": int(b.get("wx") is not None),
            "best_price_delta": best_delta,
            "engine_version": B.get("engine_version"),
            "home_won": int(sc["home"] > sc["away"]),
        })
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    backfill = "--backfill" in sys.argv or not os.path.exists(CSV_PATH)
    scores_file = None
    if "--scores-file" in sys.argv:
        scores_file = sys.argv[sys.argv.index("--scores-file") + 1]

    existing, have = [], set()
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8", newline="") as f:
            existing = list(csv.DictReader(f))
        have = {(r["date"], r["game_pk"]) for r in existing}

    if backfill:
        dates = sorted(re.search(r"board_(\d{4}-\d{2}-\d{2})\.json$", p).group(1)
                       for p in glob.glob(os.path.join(ROOT, "data", "board_*.json")))
    else:
        dates = [args[0] if args else
                 (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")]

    added = 0
    new_rows = []
    for date in dates:
        if scores_file:
            with open(scores_file, encoding="utf-8") as f:
                scores = json.load(f)
        else:
            try:
                scores = fetch_finals(date)
            except Exception as e:
                print(f"WARNING: could not fetch finals for {date} ({e}); skipped.")
                continue
        for row in rows_for(date, scores):
            if (row["date"], str(row["game_pk"])) in have:
                continue
            new_rows.append(row)
            have.add((row["date"], str(row["game_pk"])))
            added += 1

    if not added:
        print("No new training rows.")
        return

    all_rows = sorted(existing + [{k: ("" if v is None else v) for k, v in r.items()}
                                  for r in new_rows],
                      key=lambda r: (r["date"], str(r["game_pk"])))
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    sha = hashlib.sha256(open(CSV_PATH, "rb").read()).hexdigest()
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump({
            "rows": len(all_rows),
            "first_date": all_rows[0]["date"], "last_date": all_rows[-1]["date"],
            "fields": FIELDS, "csv_sha256": sha,
            "note": ("Point-in-time features from REVEALED boards (built pregame by "
                     "construction); label = home_won from MLB finals. Closing odds "
                     "deliberately excluded from features. Chronological splits only."),
        }, f, indent=1)
    print(f"Training rows: +{added} (total {len(all_rows)}, "
          f"{all_rows[0]['date']}..{all_rows[-1]['date']}). sha256 {sha[:16]}…")


if __name__ == "__main__":
    main()
