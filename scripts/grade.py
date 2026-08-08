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
      Optional: --closing-file path.json (gamePk -> {"away_ml","home_ml",...}) to
      exercise CLV, --ledger path.json and --totals-ledger path.json to write
      throwaway ledgers instead of the real data/*.json.
"""
import json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import crypto_box

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LEDGER = os.path.join(ROOT, "data", "ledger.json")
TOTALS_LEDGER = os.path.join(ROOT, "data", "totals_ledger.json")  # separate PAPER track
WATCHLIST = os.path.join(ROOT, "data", "watchlist.json")          # v0.14 watch tier — PAPER, never mixes with LEDGER
DAILY_LEDGER = os.path.join(ROOT, "data", "daily_ledger.json")    # v0.15 Daily Pick strategy — own record, never mixes with LEDGER

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

def grade_totals(date, board, scores, closing, path):
    """Paper-grade the per-game totals picks into a SEPARATE ledger: W/L/PUSH at a flat
    1u, plus totals CLV (did the closing line move toward our side?). Deliberately apart
    from the real moneyline ledger so it never touches the public record — it exists to
    measure whether the calibrated run model beats the closing total, before any real
    allocation. Append-only and idempotent per (date, gamePk), like the main ledger."""
    led = {"entries": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            led = json.load(f)
    already = {(e["date"], e["gamePk"]) for e in led["entries"]}
    graded = 0
    for b in board:
        tp = b.get("total_pick")
        if not tp:
            continue
        key = (date, b.get("gamePk"))
        if key in already:
            continue
        side, line, price = tp["side"], tp["line"], tp["price"]
        entry = {"date": date, "gamePk": b.get("gamePk"), "game": b["abbr"], "market": "total",
                 "side": side, "line": line, "price": price,
                 "model_p": tp.get("model_p"), "edge": tp.get("edge"),
                 # v0.10: split the ledger by weather-adjusted vs not (None = pre-v0.10 board)
                 "wx_applied": tp.get("wx_applied")}
        sc = scores.get(str(b.get("gamePk", "")))
        if sc is None or not sc.get("final") or sc.get("away") is None:
            entry.update(result="VOID", pnl=0.0, note="Game not final; paper stake returned.")
        else:
            actual = sc["home"] + sc["away"]
            entry["actual_total"] = actual
            if actual == line:
                entry.update(result="PUSH", pnl=0.0)
            else:
                won = (actual > line) if side == "Over" else (actual < line)
                entry.update(result="WIN" if won else "LOSS",
                             pnl=round(american_to_b(price), 3) if won else -1.0)
        # ---- totals CLV: closing line movement toward our side (runs), price as tiebreak ----
        cl = closing.get(str(b.get("gamePk", "")))
        if cl and cl.get("total") is not None:
            close_line = cl["total"]
            clv_runs = (close_line - line) if side == "Over" else (line - close_line)
            entry["close_line"] = close_line
            entry["clv_runs"] = round(clv_runs, 1)
            if clv_runs != 0:
                entry["beat_close"] = clv_runs > 0
            elif cl.get("over_price") is not None and cl.get("under_price") is not None and tp.get("mkt_devig") is not None:
                io, iu = american_to_implied(cl["over_price"]), american_to_implied(cl["under_price"])
                close_side = (io / (io + iu)) if side == "Over" else 1 - (io / (io + iu))
                entry["clv_pts"] = round((close_side - tp["mkt_devig"]) * 100, 2)
                entry["beat_close"] = close_side > tp["mkt_devig"]
        led["entries"].append(entry)
        graded += 1

    ent = led["entries"]
    wins = sum(1 for e in ent if e["result"] == "WIN")
    losses = sum(1 for e in ent if e["result"] == "LOSS")
    pushes = sum(1 for e in ent if e["result"] == "PUSH")
    voids = sum(1 for e in ent if e["result"] == "VOID")
    net = round(sum(e["pnl"] for e in ent), 3)
    decided = wins + losses
    clv_e = [e for e in ent if e.get("clv_runs") is not None]
    led["aggregates"] = {
        "record": f"{wins}-{losses}" + (f"-{pushes}p" if pushes else "") + (f"-{voids}v" if voids else ""),
        "win_pct": round(100 * wins / decided, 1) if decided else None,
        "paper_units_net": net,
        "paper_roi_pct": round(100 * net / decided, 2) if decided else None,   # flat 1u/bet
        "avg_clv_runs": round(sum(e["clv_runs"] for e in clv_e) / len(clv_e), 3) if clv_e else None,
        "beat_close_pct": round(100 * sum(1 for e in clv_e if e.get("beat_close")) / len(clv_e), 1) if clv_e else None,
        "graded_with_clv": len(clv_e),
        "note": "PAPER track — flat 1u, not staked, separate from the moneyline ledger.",
        "last_graded": date,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(led, f, indent=1)
    a = led["aggregates"]
    print(f"Totals paper: graded {graded} for {date}. {a['record']}, win {a['win_pct']}%, "
          f"{net:+.2f}u paper, CLV {a['avg_clv_runs']} runs / beat close {a['beat_close_pct']}% (n={len(clv_e)})")

def grade_watchlist(date, watch, scores, path):
    """Grade the day's watch picks (v0.14) into their own append-only paper
    ledger. Same grading rules as the real ledger — final scores, price P&L,
    VOID on non-final — at flat 1u PAPER stakes so the record accumulates the
    evidence the promotion criteria need; every surface displays them as 0u
    staked. Aggregates per tag and per market. Never touches ledger.json, and
    nothing here is ever summed into the staked record. Idempotent per
    (date, gamePk, market, tag)."""
    led = {"entries": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            led = json.load(f)
    already = {(e["date"], e["gamePk"], e["market"], e["tag"]) for e in led["entries"]}
    graded = 0
    for w in watch:
        key = (date, w["gamePk"], w["market"], w["tag"])
        if key in already:
            continue
        entry = {"date": date, "gamePk": w["gamePk"], "game": w["abbr"], "market": w["market"],
                 "tag": w["tag"], "pick": w["pick"], "price": w.get("price"), "book": w.get("book"),
                 "model_p": w.get("model_p"), "edge": w.get("edge"), "divergence": w.get("divergence")}
        sc = scores.get(str(w["gamePk"]))
        if sc is None or not sc.get("final") or sc.get("away") is None:
            entry.update(result="VOID", pnl=0.0, note="Game not final; paper stake returned.")
        elif w["market"] == "total":
            actual = sc["home"] + sc["away"]
            entry["actual_total"] = actual
            if actual == w["line"]:
                entry.update(result="PUSH", pnl=0.0)
            else:
                won = (actual > w["line"]) if w["side"] == "Over" else (actual < w["line"])
                entry.update(result="WIN" if won else "LOSS",
                             pnl=round(american_to_b(w["price"]), 3) if won else -1.0)
        else:  # moneyline
            a_ab, h_ab = w["abbr"].split(" @ ")
            is_home = w.get("pick_team_abbr") == h_ab
            margin = (sc["home"] - sc["away"]) if is_home else (sc["away"] - sc["home"])
            entry["final_score"] = f'{sc["away"]}-{sc["home"]}'
            if w.get("price") is None:
                entry.update(result="VOID", pnl=0.0, note="No price logged; not paper-graded.")
            elif margin > 0:
                entry.update(result="WIN", pnl=round(american_to_b(w["price"]), 3))
            else:
                entry.update(result="LOSS", pnl=-1.0)
        led["entries"].append(entry)
        graded += 1

    def agg(entries):
        wins = sum(1 for e in entries if e["result"] == "WIN")
        losses = sum(1 for e in entries if e["result"] == "LOSS")
        pushes = sum(1 for e in entries if e["result"] == "PUSH")
        voids = sum(1 for e in entries if e["result"] == "VOID")
        net = round(sum(e["pnl"] for e in entries), 3)
        decided = wins + losses
        return {"record": f"{wins}-{losses}" + (f"-{pushes}p" if pushes else "") + (f"-{voids}v" if voids else ""),
                "n_graded": len(entries),
                "win_pct": round(100 * wins / decided, 1) if decided else None,
                "paper_units_net": net,
                "paper_roi_pct": round(100 * net / decided, 2) if decided else None}

    ent = led["entries"]
    led["aggregates"] = {
        **agg(ent),
        "by_tag": {t: agg([e for e in ent if e["tag"] == t]) for t in sorted({e["tag"] for e in ent})},
        "by_market": {m: agg([e for e in ent if e["market"] == m]) for m in sorted({e["market"] for e in ent})},
        "note": "WATCH tier PAPER record — flat 1u paper, 0u staked, never mixed into the staked ledger.",
        "last_graded": date,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(led, f, indent=1)
    a = led["aggregates"]
    print(f"Watch list: graded {graded} for {date}. {a['record']}, win {a['win_pct']}%, "
          f"{a['paper_units_net']:+.2f}u paper across {a['n_graded']} tracked.")

def fair_pick_odds(b):
    """Model fair odds for the pick side (used only if no market odds logged)."""
    return b["fair_home"] if b["pick_team_abbr"] == b["abbr"].split(" @ ")[1] else b["fair_away"]

def grade_daily(date, B, scores, closing, path):
    """Daily Pick strategy ledger (v0.15) — the always-on strategy's own
    append-only record, SEPARATE from the qualified ledger and never mixed
    into it. Same grading rules as the real ledger; P&L is booked two ways:
    pnl_staked at the units actually risked (0 during the proving window) and
    pnl_paper at the strategy's flat 0.25u basis, so the record the staking
    review reads exists from day one. Idempotent per date."""
    dp = B.get("daily_pick")
    if not dp:
        return
    led = {"entries": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            led = json.load(f)
    if any(e["date"] == date for e in led["entries"]):
        return

    a_ab, h_ab = dp["abbr"].split(" @ ")
    pick_is_home = dp["pick_team_abbr"] == h_ab
    entry = {
        "date": date, "gamePk": dp["gamePk"], "game": dp["abbr"], "pick": dp["pick"],
        "market": dp.get("market"), "price": dp.get("price"), "book": dp.get("book"),
        "model_p": dp.get("model_p"), "blend_p": dp.get("blend_p"),
        "edge": dp.get("edge"), "score": dp.get("score"),
        "units_staked": dp.get("units", 0.0), "paper_basis": 0.25, "strategy": "daily",
    }
    sc = scores.get(str(dp["gamePk"]))
    if sc is None or not sc.get("final") or sc.get("away") is None:
        entry.update(result="VOID", pnl_paper=0.0, pnl_staked=0.0,
                     note="Game not final (postponed/suspended).")
    else:
        margin = (sc["home"] - sc["away"]) if pick_is_home else (sc["away"] - sc["home"])
        won = margin > 1.5 if "run line" in dp["pick"].lower() else margin > 0
        entry["final_score"] = f'{sc["away"]}-{sc["home"]}'
        b_mult = american_to_b(dp["price"]) if dp.get("price") is not None else 1.0
        if won:
            entry.update(result="WIN", pnl_paper=round(0.25 * b_mult, 3),
                         pnl_staked=round(entry["units_staked"] * b_mult, 3))
        else:
            entry.update(result="LOSS", pnl_paper=-0.25, pnl_staked=-entry["units_staked"])

    # CLV, same definition as the qualified ledger: board-time consensus vs
    # captured close for the pick side. Needs the board row for the open MLs.
    b_row = next((b for b in B["board"] if b.get("gamePk") == dp["gamePk"]), None)
    cl = closing.get(str(dp["gamePk"]))
    if b_row and cl and cl.get("away_ml") is not None and cl.get("home_ml") is not None \
            and b_row.get("mkt_away_ml") is not None and b_row.get("mkt_home_ml") is not None:
        open_p = devig_pick_prob(b_row["mkt_away_ml"], b_row["mkt_home_ml"], pick_is_home)
        close_p = devig_pick_prob(cl["away_ml"], cl["home_ml"], pick_is_home)
        if open_p is not None and close_p is not None:
            entry["open_ml"] = b_row["mkt_home_ml"] if pick_is_home else b_row["mkt_away_ml"]
            entry["close_ml"] = cl["home_ml"] if pick_is_home else cl["away_ml"]
            entry["clv_pts"] = round((close_p - open_p) * 100, 2)
            entry["beat_close"] = close_p > open_p

    led["entries"].append(entry)
    ent = led["entries"]
    wins = sum(1 for e in ent if e["result"] == "WIN")
    losses = sum(1 for e in ent if e["result"] == "LOSS")
    voids = sum(1 for e in ent if e["result"] == "VOID")
    decided = wins + losses
    paper_net = round(sum(e["pnl_paper"] for e in ent), 3)
    paper_risked = round(0.25 * decided, 3)
    clv_e = [e for e in ent if e.get("clv_pts") is not None]
    led["aggregates"] = {
        "record": f"{wins}-{losses}" + (f"-{voids}v" if voids else ""),
        "wins": wins, "losses": losses, "voids": voids,
        "paper_units_net": paper_net,
        "paper_roi_pct": round(100 * paper_net / paper_risked, 2) if paper_risked else None,
        "staked_units_net": round(sum(e["pnl_staked"] for e in ent), 3),
        "clv": {"graded_with_clv": len(clv_e),
                "avg_clv_pts": round(sum(e["clv_pts"] for e in clv_e) / len(clv_e), 2) if clv_e else None,
                "beat_close_pct": round(100 * sum(1 for e in clv_e if e["clv_pts"] > 0) / len(clv_e), 1) if clv_e else None},
        "opened": "2026-08-09",
        "proving_until": dp.get("proving_until"),
        "note": ("Daily Pick strategy (v0.15): always-on, lower precommitted bar, flat 0.25u "
                 "paper basis; 0u staked through the proving window. Never mixed with the "
                 "Qualified Plays ledger."),
        "last_graded": date,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(led, f, indent=1)
    a = led["aggregates"]
    print(f"Daily Pick: graded {date}. {a['record']}, {a['paper_units_net']:+.2f}u paper "
          f"(0.25u basis), staked {a['staked_units_net']:+.2f}u.")

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
    totals_ledger_path = sys.argv[sys.argv.index("--totals-ledger") + 1] if "--totals-ledger" in sys.argv else TOTALS_LEDGER

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
        # odds_basis (v0.13): the price the pick was actually priced at, its book,
        # and the consensus alongside. Pre-v0.13 boards have no mkt_book, so their
        # basis reads "consensus-median" — old entries in the ledger keep the bare
        # int this field used to be (append-only: never rewritten).
        entry = {
            "date": date, "gamePk": b.get("gamePk"), "game": b["abbr"],
            "pick": b["pick"], "units": b["units"],
            "confidence": b["confidence"], "edge": b.get("edge"),
            "odds_basis": {
                "price": b.get("mkt_odds"),
                "book": b.get("mkt_book"),
                "basis": "best-price" if b.get("mkt_book") else "consensus-median",
                "consensus": b.get("mkt_odds_consensus", b.get("mkt_odds")),
            },
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

    # Paper-grade the totals track into its own ledger (never touches the record above).
    grade_totals(date, B["board"], scores, closing, totals_ledger_path)
    watchlist_path = sys.argv[sys.argv.index("--watchlist") + 1] if "--watchlist" in sys.argv else WATCHLIST
    grade_watchlist(date, B.get("watch_picks") or [], scores, watchlist_path)
    daily_path = sys.argv[sys.argv.index("--daily-ledger") + 1] if "--daily-ledger" in sys.argv else DAILY_LEDGER
    grade_daily(date, B, scores, closing, daily_path)

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
