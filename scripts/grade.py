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
Closing-line value (CLV): if fetch_closing.py captured a closing line for the
game (data/closing_<date>.json), each entry also books open-vs-close: open_ml,
close_ml, clv_pts (de-vigged prob points, + = we beat the close), beat_close.
Aggregates carry a CLV block. Entries with no closing line are simply blank,
so pre-CLV history is untouched (the ledger is append-only — never backfilled).

Ledger entries are never edited after grading; aggregates are recomputed
from the full entry list every run.

Run: python scripts/grade.py [YYYY-MM-DD]   (defaults to yesterday, ET)
Test: python scripts/grade.py YYYY-MM-DD --scores-file path.json
      where the file maps gamePk -> {"away": runs, "home": runs, "final": true}
      Optional: --closing-file path.json (gamePk -> {"away_ml","home_ml"}) to
      exercise CLV, and --ledger path.json to write a throwaway ledger instead
      of the real data/ledger.json.
"""
import json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import crypto_box

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LEDGER = os.path.join(ROOT, "data", "ledger.json")

def american_to_b(odds):
    return 100 / (-odds) if odds < 0 else odds / 100

def american_to_implied(odds):
    return (-odds) / (-odds + 100) if odds < 0 else 100 / (odds + 100)

def devig_pick_prob(away_ml, home_ml, pick_is_home):
    """No-vig probability of the pick side from a two-way moneyline."""
    ia, ih = american_to_implied(away_ml), american_to_implied(home_ml)
    tot = ia + ih
    if tot <= 0:
        return None
    return (ih if pick_is_home else ia) / tot

def load_closing(root, date):
    """Closing lines captured near first pitch by fetch_closing.py, or {}."""
    path = os.path.join(root, "data", f"closing_{date}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}

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

    # --ledger / --closing-file are test hooks (like --scores-file): they let the
    # full grade path run against throwaway files without touching the real,
    # append-only ledger. Default to the live paths.
    ledger_path = sys.argv[sys.argv.index("--ledger") + 1] if "--ledger" in sys.argv else LEDGER

    B = crypto_box.load_dataset(ROOT, "board", date)
    if B is None:
        print(f"No board for {date}: nothing to grade.")
        return

    # Check the board against its morning fingerprint BEFORE anything reaches
    # the ledger. The ledger is append-only, so a mismatched board that gets
    # graded first is recorded permanently, and the ledger is the one thing
    # here that has to stay trustworthy.
    verify_commitment(date, B)

    if "--scores-file" in sys.argv:
        with open(sys.argv[sys.argv.index("--scores-file") + 1], encoding="utf-8") as f:
            scores = json.load(f)
    else:
        scores = fetch_scores(date)

    if "--closing-file" in sys.argv:
        with open(sys.argv[sys.argv.index("--closing-file") + 1], encoding="utf-8") as f:
            closing = json.load(f)
    else:
        closing = load_closing(ROOT, date)

    ledger = {"entries": []}
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
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
        a_ab, h_ab = b["abbr"].split(" @ ")
        pick_is_home = b["pick_team_abbr"] == h_ab
        entry = {
            "date": date, "gamePk": b.get("gamePk"), "game": b["abbr"],
            "pick": b["pick"], "units": b["units"],
            "confidence": b["confidence"], "edge": b.get("edge"),
            "odds_basis": b.get("mkt_odds"),
        }
        if sc is None or not sc.get("final") or sc.get("away") is None:
            entry.update(result="VOID", pnl=0.0,
                         note="Game not final (postponed/suspended); stake returned.")
        else:
            margin = (sc["home"] - sc["away"]) if pick_is_home else (sc["away"] - sc["home"])
            won = margin > 1.5 if "run line" in b["pick"] else margin > 0
            odds = b.get("mkt_odds")
            if odds is None:
                odds = fair_pick_odds(b)
                entry["note"] = "No market odds logged; graded at model fair line."
            entry["final_score"] = f'{sc["away"]}-{sc["home"]}'
            if won:
                entry.update(result="WIN", pnl=round(b["units"] * american_to_b(odds), 3))
            else:
                entry.update(result="LOSS", pnl=-b["units"])

        # ---- CLV: our published (opening) price vs the closing line ----
        # Positive clv_pts = the market moved TOWARD our side after we posted, i.e.
        # we got a better-than-close price. Recorded when a closing line exists for
        # the game; blank otherwise (as it is for every pre-CLV entry). Result-
        # independent — it measures line-picking, not luck.
        cl = closing.get(str(b.get("gamePk", "")))
        open_a, open_h = b.get("mkt_away_ml"), b.get("mkt_home_ml")
        if cl and cl.get("away_ml") is not None and cl.get("home_ml") is not None \
                and open_a is not None and open_h is not None:
            open_p = devig_pick_prob(open_a, open_h, pick_is_home)
            close_p = devig_pick_prob(cl["away_ml"], cl["home_ml"], pick_is_home)
            if open_p is not None and close_p is not None:
                entry["open_ml"] = open_h if pick_is_home else open_a
                entry["close_ml"] = cl["home_ml"] if pick_is_home else cl["away_ml"]
                entry["clv_pts"] = round((close_p - open_p) * 100, 2)
                entry["beat_close"] = close_p > open_p

        ledger["entries"].append(entry)
        graded += 1

    # ---- Recompute aggregates from the full, append-only entry list ----
    ent = ledger["entries"]
    wins = sum(1 for e in ent if e["result"] == "WIN")
    losses = sum(1 for e in ent if e["result"] == "LOSS")
    voids = sum(1 for e in ent if e["result"] == "VOID")
    units_net = round(sum(e["pnl"] for e in ent), 3)
    units_risked = round(sum(e["units"] for e in ent if e["result"] != "VOID"), 3)
    # CLV over just the entries that carry a closing line. A far faster read on
    # whether the picks have edge than W/L: line-picking skill shows up here in
    # dozens of bets, ROI takes hundreds.
    clv_ent = [e for e in ent if e.get("clv_pts") is not None]
    clv_block = {"graded_with_clv": len(clv_ent), "avg_clv_pts": None, "beat_close_pct": None}
    if clv_ent:
        clv_block["avg_clv_pts"] = round(sum(e["clv_pts"] for e in clv_ent) / len(clv_ent), 2)
        clv_block["beat_close_pct"] = round(100 * sum(1 for e in clv_ent if e["clv_pts"] > 0) / len(clv_ent), 1)
    ledger["aggregates"] = {
        "record": f"{wins}-{losses}" + (f"-{voids}v" if voids else ""),
        "wins": wins, "losses": losses, "voids": voids,
        "units_net": units_net, "units_risked": units_risked,
        "roi_pct": round(100 * units_net / units_risked, 2) if units_risked else None,
        "clv": clv_block,
        "opened": "2026-07-22",
        "last_graded": date,
    }
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1)
    clv_s = (f", CLV {clv_block['avg_clv_pts']:+.2f} pts avg / beat close {clv_block['beat_close_pct']}%"
             f" (n={clv_block['graded_with_clv']})") if clv_ent else ""
    print(f"Graded {graded} picks for {date}. Ledger: {ledger['aggregates']['record']}, "
          f"{units_net:+.2f}u net, ROI {ledger['aggregates']['roi_pct']}%{clv_s}")

    reveal(date, B)

def verify_commitment(date, board):
    """Stop the run if the board no longer matches what we published.

    A board with no commitment is fine: those predate the commit-and-reveal
    work. A board WITH a commitment that does not match is not fine, and
    nothing further should happen until a human has looked at it.
    """
    committed = crypto_box.commitment_for(ROOT, date)
    if committed is None:
        return
    actual = crypto_box.sha256_of(board)
    if actual != committed["board_sha256"]:
        raise SystemExit(
            f"REFUSING to grade {date}: the board does not match the fingerprint "
            f"published that morning.\n  committed {committed['board_sha256']}\n"
            f"  actual    {actual}\nNothing has been written to the ledger. "
            f"Investigate before running this again.")

def reveal(date, board):
    """Publish the plaintext board and snapshot now that the games are over.

    The morning published a fingerprint; this publishes the thing it
    fingerprints. verify_commitment has already run, so by here the two are
    known to agree.
    """
    if crypto_box.commitment_for(ROOT, date) is None:
        return
    for kind in ("board", "snapshot"):
        obj = crypto_box.load_dataset(ROOT, kind, date)
        if obj is None:
            continue
        plain_path, enc_path = crypto_box.paths_for(ROOT, kind, date)
        with open(plain_path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
        if os.path.exists(enc_path):
            os.remove(enc_path)
    crypto_box.mark_revealed(ROOT, date)
    print(f"Revealed {date}: fingerprint matches the morning commitment "
          f"({crypto_box.sha256_of(board)[:16]}...). Board and snapshot are now public.")

if __name__ == "__main__":
    main()
