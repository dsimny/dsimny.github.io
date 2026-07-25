#!/usr/bin/env python3
"""
Open Ledger Sports — model backtest harness.

Replays the ACTUAL engine over historical games to measure calibration — the only
way to tell whether v0.3–v0.5 actually helped, and to tune MODEL_WEIGHT /
FIP_WEIGHT / PRIOR_IP on evidence instead of taste. Every game's inputs are
reconstructed strictly AS OF that morning (point-in-time, no look-ahead):
  - team W/L, RS, RA        : standings?date=<day before>
  - starter ERA + FIP parts : people byDateRange, season start .. day before
  - league pitching totals  : teams byDateRange, season start .. day before
  - final scores            : schedule linescore for the game day
then it runs engine.simulate_game() — literally the function the daily board
uses — so the backtest scores the model that ships, not a copy of it.

Two honest limits:
  * NO historical odds. The Odds API free tier serves only current lines, so by
    default the backtest scores the RAW model's win probabilities (Brier / log
    loss / calibration), NOT ROI or CLV, and can't tune MODEL_WEIGHT (the market
    blend). Pass --odds-dir with odds_<date>.json files (gamePk -> away_ml/home_ml)
    to unlock a de-vigged-market comparison and a flat-stake ROI.
  * The bullpen split (v0.4) can't be reconstructed point-in-time — MLB's reliever
    statSplit ignores the date cutoff and returns full-season numbers, which would
    leak the future. So the backtest uses the engine's team-RA fallback for the
    pen; it slightly understates v0.4's contribution but never leaks.

Reconstructed inputs are cached under data/backtest/ so sweeps re-simulate without
re-hitting the API. Sims are seeded per date, so a run is reproducible.

Run:
  python scripts/backtest.py --days 14
  python scripts/backtest.py --start 2026-05-01 --end 2026-06-30 --sims 3000
  python scripts/backtest.py --start 2026-05-01 --end 2026-06-30 --sweep prior_ip:30,60,90
  python scripts/backtest.py --start 2026-05-01 --end 2026-06-30 --sweep fip_weight:0,0.5,1
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
from zoneinfo import ZoneInfo

import numpy as np
import requests

# engine (and fetch_data) parse a CLI date at import time; hide our argv so importing
# them here can't be mis-read as an engine run.
_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
import engine
from fetch_data import PARK_FACTORS
sys.argv = _saved_argv

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MLB = "https://statsapi.mlb.com/api/v1"


def get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def ip_to_float(x):
    w, _, frac = str(x).partition(".")
    try:
        return int(w) + {"1": 1 / 3, "2": 2 / 3}.get(frac, 0.0)
    except ValueError:
        return 0.0


def daterange(start, end):
    d, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def team_meta(season):
    d = get(f"{MLB}/teams", sportId=1, season=season)
    return {t["id"]: {"name": t["name"], "abbr": t.get("abbreviation", "???")} for t in d["teams"]}


def reconstruct(date, season, meta, cache_dir, refresh=False):
    """Point-in-time snapshot for one slate (cached). Everything is as of the day
    BEFORE the games, so nothing the model sees includes the games it predicts."""
    cache = os.path.join(cache_dir, f"bt_{date}.json")
    if os.path.exists(cache) and not refresh:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)

    prev = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    start = f"{season}-03-01"

    sched = get(f"{MLB}/schedule", sportId=1, date=date, hydrate="probablePitcher,linescore")
    graw = [g for g in (sched["dates"][0]["games"] if sched.get("dates") else [])
            if g.get("gameType") == "R"]
    games, results, pids = [], {}, set()
    for g in graw:
        a, h = g["teams"]["away"], g["teams"]["home"]
        asp = (a.get("probablePitcher") or {}).get("id")
        hsp = (h.get("probablePitcher") or {}).get("id")
        ls = g.get("linescore", {}).get("teams", {})
        results[str(g["gamePk"])] = {
            "away": ls.get("away", {}).get("runs"), "home": ls.get("home", {}).get("runs"),
            "final": g.get("status", {}).get("abstractGameState") == "Final"}
        games.append({"gamePk": g["gamePk"], "away": a["team"]["id"], "home": h["team"]["id"],
                      "venue": g.get("venue", {}).get("name", "Unknown"), "awaySP": asp, "homeSP": hsp})
        pids.update(x for x in (asp, hsp) if x)

    teams = {}
    st = get(f"{MLB}/standings", leagueId="103,104", season=season,
             standingsTypes="regularSeason", date=prev)
    for div in st["records"]:
        for r in div["teamRecords"]:
            tid = r["team"]["id"]
            teams[str(tid)] = {"name": meta.get(tid, {}).get("name", "?"),
                               "abbr": meta.get(tid, {}).get("abbr", "???"),
                               "w": r["wins"], "l": r["losses"],
                               "rs": r.get("runsScored"), "ra": r.get("runsAllowed"),
                               "pen_era": None}  # bullpen split leaks; engine falls back to team RA

    pitchers = {}
    if pids:
        ppl = get(f"{MLB}/people", personIds=",".join(map(str, sorted(pids))),
                  hydrate=f"stats(group=[pitching],type=[byDateRange],startDate={start},endDate={prev})")
        for p in ppl["people"]:
            stt = p.get("stats") or []
            if not stt or not stt[0].get("splits"):
                continue
            s = stt[0]["splits"][0]["stat"]
            try:
                pitchers[str(p["id"])] = {
                    "name": p["fullName"], "era": float(s["era"]), "ip": ip_to_float(s.get("inningsPitched", 0)),
                    "hr": int(s.get("homeRuns", 0)), "bb": int(s.get("baseOnBalls", 0)),
                    "hbp": int(s.get("hitByPitch", 0)), "k": int(s.get("strikeOuts", 0))}
            except (KeyError, ValueError):
                continue  # no usable line in range (season debut / bad ERA string) -> game skipped later

    league_pitching = None
    try:
        tp = get(f"{MLB}/teams/stats", stats="byDateRange", group="pitching",
                 startDate=start, endDate=prev, sportIds=1, gameType="R")
        tot = {"ip": 0.0, "hr": 0, "bb": 0, "hbp": 0, "k": 0, "er": 0}
        for s in (tp.get("stats") or [{}])[0].get("splits") or []:
            stat = s["stat"]
            tot["ip"] += ip_to_float(stat.get("inningsPitched", 0))
            tot["hr"] += stat.get("homeRuns", 0)
            tot["bb"] += stat.get("baseOnBalls", 0)
            tot["hbp"] += stat.get("hitByPitch", 0)
            tot["k"] += stat.get("strikeOuts", 0)
            tot["er"] += stat.get("earnedRuns", 0)
        if tot["ip"] > 0:
            league_pitching = {k: round(v, 3) for k, v in tot.items()}
    except Exception:
        pass

    snap = {"date": date, "season": season, "teams": teams, "pitchers": pitchers,
            "league_pitching": league_pitching, "games": games, "results": results,
            "park_factors": PARK_FACTORS}
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(snap, f)
    return snap


def evaluate(snap, sims, model_weight, odds_by_pk):
    """Run the shared engine core on one reconstructed slate; return one row per
    graded game with model / market / blended home-win probs and the outcome."""
    teams = {int(k): v for k, v in snap["teams"].items()}
    pitchers = {int(k): v for k, v in snap["pitchers"].items()}
    parks = snap["park_factors"]
    tv = [t for t in teams.values() if t["rs"] is not None and t["ra"] is not None]
    total_g = sum(t["w"] + t["l"] for t in tv)
    if total_g == 0:
        return []
    league_rate = sum(t["rs"] for t in tv) / total_g
    league_era = sum(t["ra"] for t in tv) / total_g
    fip_constant = engine.fip_constant_from(snap.get("league_pitching"), league_era)
    rng = np.random.default_rng(int(snap["date"].replace("-", "")))  # reproducible per date

    rows = []
    for g in snap["games"]:
        res = snap["results"].get(str(g["gamePk"]))
        if not res or not res.get("final") or res.get("away") is None or res.get("home") is None:
            continue
        away, home = teams.get(g["away"]), teams.get(g["home"])
        a_sp = pitchers.get(g["awaySP"]) if g["awaySP"] else None
        h_sp = pitchers.get(g["homeSP"]) if g["homeSP"] else None
        if not away or not home or a_sp is None or h_sp is None:
            continue
        if None in (away["rs"], away["ra"], home["rs"], home["ra"]):
            continue
        if (away["w"] + away["l"]) == 0 or (home["w"] + home["l"]) == 0:
            continue
        park = parks.get(g["venue"], 1.00)
        sim = engine.simulate_game(away, home, a_sp, h_sp, park, league_rate, league_era,
                                   fip_constant, rng, n_sims=sims)
        p_model = sim["p_home"]
        y = 1 if res["home"] > res["away"] else 0
        row = {"p_model": p_model, "p_blend": p_model, "p_mkt": None, "away_ml": None, "home_ml": None, "y": y}
        od = (odds_by_pk or {}).get(str(g["gamePk"]))
        if od and od.get("away_ml") is not None and od.get("home_ml") is not None:
            ia, ih = engine.american_to_implied(od["away_ml"]), engine.american_to_implied(od["home_ml"])
            p_mkt = ih / (ia + ih)
            row.update(p_mkt=p_mkt, p_blend=model_weight * p_model + (1 - model_weight) * p_mkt,
                       away_ml=od["away_ml"], home_ml=od["home_ml"])

        # ---- Totals: the model's simulated run-total distribution vs the actual total ----
        totals = sim["a_runs"] + sim["h_runs"]
        t_actual = res["home"] + res["away"]
        p10, p25, p75, p90 = (float(v) for v in np.percentile(totals, [10, 25, 75, 90]))
        row["t_mean"] = float(totals.mean())
        row["t_actual"] = t_actual
        # mid-P PIT: where the actual total lands in the model's distribution (uniform if calibrated)
        row["t_pit"] = (int((totals < t_actual).sum()) + 0.5 * int((totals == t_actual).sum())) / len(totals)
        row["t_in50"] = 1 if p25 <= t_actual <= p75 else 0   # central-50% interval coverage
        row["t_in80"] = 1 if p10 <= t_actual <= p90 else 0   # central-80% interval coverage

        rows.append(row)
    return rows


def brier(rows, key):
    return sum((r[key] - r["y"]) ** 2 for r in rows) / len(rows)


def logloss(rows, key):
    tot = 0.0
    for r in rows:
        p = min(max(r[key], 1e-9), 1 - 1e-9)
        tot += -(r["y"] * math.log(p) + (1 - r["y"]) * math.log(1 - p))
    return tot / len(rows)


def accuracy(rows, key):
    return sum(1 for r in rows if (r[key] >= 0.5) == (r["y"] == 1)) / len(rows)


def calibration(rows, key):
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        b = [r for r in rows if (lo <= r[key] < hi) or (i == 9 and r[key] == 1.0)]
        if not b:
            continue
        pred = sum(r[key] for r in b) / len(b)
        act = sum(r["y"] for r in b) / len(b)
        print(f"  {lo:.1f}-{hi:.1f}  n={len(b):>4}   pred {pred * 100:>5.1f}%   actual {act * 100:>5.1f}%")


def roi(rows, model_weight):
    bets, pnl = 0, 0.0
    for r in [r for r in rows if r["p_mkt"] is not None]:
        home = r["p_blend"] >= 0.5
        offered = r["home_ml"] if home else r["away_ml"]
        p_side = r["p_blend"] if home else 1 - r["p_blend"]
        if p_side - engine.american_to_implied(offered) < engine.MIN_EDGE:
            continue
        bets += 1
        won = (r["y"] == 1) if home else (r["y"] == 0)
        pnl += engine.american_to_b(offered) if won else -1
    if bets:
        print(f"  Flat-stake ROI (blended edge>{engine.MIN_EDGE:.0%} @ offered price): "
              f"{bets} bets, {pnl:+.2f}u, ROI {100 * pnl / bets:+.1f}%")
    else:
        print("  Flat-stake ROI: no bets cleared the edge gate.")


def totals_report(rows):
    """Calibrate the model's run-total distribution against actual finals. Needs no
    market — just the sim distribution vs what the game actually totaled. Bias/MAE
    test the mean; coverage + PIT test the whole distribution (and thus DISPERSION)."""
    r = [x for x in rows if x.get("t_mean") is not None]
    if not r:
        return
    n = len(r)
    mmt = sum(x["t_mean"] for x in r) / n
    mat = sum(x["t_actual"] for x in r) / n
    bias = mmt - mat
    mae = sum(abs(x["t_mean"] - x["t_actual"]) for x in r) / n
    rmse = (sum((x["t_mean"] - x["t_actual"]) ** 2 for x in r) / n) ** 0.5
    over_mean = sum(1 for x in r if x["t_actual"] > x["t_mean"]) / n
    cov50 = sum(x["t_in50"] for x in r) / n
    cov80 = sum(x["t_in80"] for x in r) / n
    print("\nTotals — model run-total distribution vs actual finals:")
    print(f"  model mean {mmt:.2f}  vs  actual {mat:.2f}  ->  bias {bias:+.2f} runs")
    print(f"  MAE {mae:.2f}  RMSE {rmse:.2f}   |  actual over model-mean {over_mean * 100:.1f}%  (50% = unbiased)")
    print(f"  interval coverage: central 50% caught {cov50 * 100:.1f}% (want ~50), central 80% caught {cov80 * 100:.1f}% (want ~80)")
    print("    <50 = distribution too NARROW (DISPERSION too high); >50/80 = too WIDE (too low)")
    bins = [0] * 10
    for x in r:
        bins[min(9, int(x["t_pit"] * 10))] += 1
    print("  PIT deciles (actual's percentile in model dist; flat ~10% each = calibrated):")
    print("    " + "  ".join(f"{b / n * 100:4.1f}" for b in bins))


def report(rows, dates, args, have_odds):
    if not rows:
        print("No graded games in range (all skipped: not final, missing starter, or too early).")
        return
    n = len(rows)
    base = sum(r["y"] for r in rows) / n
    print(f"\nBacktest {dates[0]} .. {dates[-1]}  | engine {engine.ENGINE_VERSION} | "
          f"MODEL_WEIGHT={args.model_weight} FIP_WEIGHT={engine.FIP_WEIGHT} PRIOR_IP={engine.PRIOR_IP} | sims={args.sims}")
    print(f"Games graded: {n}  | home win base rate {base * 100:.1f}%  (no-skill Brier {base * (1 - base):.4f})\n")
    print("Model (raw win prob, pre-market):")
    print(f"  Brier    {brier(rows, 'p_model'):.4f}   (lower is better; beat the no-skill number above)")
    print(f"  LogLoss  {logloss(rows, 'p_model'):.4f}")
    print(f"  Accuracy {accuracy(rows, 'p_model') * 100:.1f}%   (picks the higher-prob side)")
    if have_odds:
        mrows = [r for r in rows if r["p_mkt"] is not None]
        if mrows:
            print(f"\nMarket comparison on {len(mrows)} games with odds:")
            print(f"  Market (de-vig) Brier {brier(mrows, 'p_mkt'):.4f}   <- the number to beat")
            print(f"  Blended         Brier {brier(mrows, 'p_blend'):.4f}   (MODEL_WEIGHT={args.model_weight})")
            roi(mrows, args.model_weight)
    print("\nCalibration (predicted home-win% -> actual):")
    calibration(rows, "p_model")
    totals_report(rows)


def main():
    ap = argparse.ArgumentParser(description="Backtest the OLS engine over historical games.")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--days", type=int, help="backtest the last N days ending yesterday (ET)")
    ap.add_argument("--sims", type=int, default=2000, help="sims per game (default 2000; the board uses 10000)")
    ap.add_argument("--model-weight", type=float, default=engine.MODEL_WEIGHT)
    ap.add_argument("--odds-dir", help="dir of odds_<date>.json (gamePk -> away_ml/home_ml) to unlock market/ROI")
    ap.add_argument("--sweep", help="param:v1,v2,... over fip_weight | prior_ip | model_weight")
    ap.add_argument("--season", type=int)
    ap.add_argument("--cache-dir", default=os.path.join(ROOT, "data", "backtest"))
    ap.add_argument("--refresh", action="store_true", help="ignore cached snapshots and re-fetch")
    args = ap.parse_args()

    if args.days:
        end = dt.datetime.now(ZoneInfo("America/New_York")).date() - dt.timedelta(days=1)
        dates = list(daterange((end - dt.timedelta(days=args.days - 1)).isoformat(), end.isoformat()))
    elif args.start and args.end:
        dates = list(daterange(args.start, args.end))
    else:
        ap.error("give --days N, or both --start and --end")

    season = args.season or int(dates[0][:4])
    meta = team_meta(season)

    snaps = []
    for i, date in enumerate(dates):
        if not os.path.exists(os.path.join(args.cache_dir, f"bt_{date}.json")) or args.refresh:
            print(f"  reconstructing {date} ({i + 1}/{len(dates)})...", flush=True)
        snaps.append(reconstruct(date, season, meta, args.cache_dir, args.refresh))

    def odds_for(date):
        if not args.odds_dir:
            return {}
        p = os.path.join(args.odds_dir, f"odds_{date}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def run(model_weight):
        out = []
        for snap in snaps:
            out += evaluate(snap, args.sims, model_weight, odds_for(snap["date"]))
        return out

    if args.sweep:
        param, _, raw = args.sweep.partition(":")
        param = param.strip().lower()
        vals = [v.strip() for v in raw.split(",") if v.strip()]
        # engine module globals a sweep can drive; model_weight is passed to run() instead
        ATTR = {"fip_weight": "FIP_WEIGHT", "prior_ip": "PRIOR_IP",
                "dispersion": "DISPERSION", "hfa": "HFA_RUNS", "factor_shrink": "FACTOR_SHRINK"}
        if param not in ATTR and param != "model_weight":
            ap.error("sweep param must be one of: " + ", ".join(list(ATTR) + ["model_weight"]))
        print(f"\nSweep {param} over {vals}  | {dates[0]}..{dates[-1]}, sims={args.sims}")
        saved = {a: getattr(engine, a) for a in ATTR.values()}
        key = "p_blend" if (args.odds_dir and param == "model_weight") else "p_model"
        print(f'  {param:>12}  {"Brier":>8} {"LogLoss":>8} {"Acc":>7}  {"N":>5}')
        for v in vals:
            fv, mw = float(v), args.model_weight
            if param == "model_weight":
                mw = fv
            else:
                setattr(engine, ATTR[param], fv)
            rows = run(mw)
            if rows:
                print(f'  {v:>12}  {brier(rows, key):>8.4f} {logloss(rows, key):>8.4f} '
                      f'{accuracy(rows, key) * 100:>6.1f}% {len(rows):>5}')
        for a, val in saved.items():
            setattr(engine, a, val)
        return

    report(run(args.model_weight), dates, args, bool(args.odds_dir))


if __name__ == "__main__":
    main()
