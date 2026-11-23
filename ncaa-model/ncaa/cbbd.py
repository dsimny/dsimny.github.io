"""CollegeBasketballData (CBBD) betting lines: fill the 2023-24 / 2024-25 gap
and cross-check ESPN's line providers.

    CBBD_API_KEY=... python -m ncaa.cbbd --seasons 2016-2027

The key lives ONLY in the environment (GitHub secret CBBD_API_KEY); it is
never written to disk or printed. Raw CBBD rows are cached under
data/cbbd_cache/ (gitignored) and never committed or uploaded: this repo is
public, and redistributing a provider's feed is not ours to do. The only
output is reports/cbbd_lines.md, which holds aggregates.

Join: CBBD game ids differ from ESPN's, so games are matched on
(date within +-1 day, final score pair). That's exact for ~all games without
any team-name mapping; a score pair seen twice in the window is dropped as
ambiguous. If CBBD lists the teams in the opposite home/away order (neutral
sites), the line is flipped to ESPN's orientation, and the flip is counted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import requests
from scipy.stats import norm

from . import config as C
from . import metrics as M

BASE = "https://api.collegebasketballdata.com"
CACHE = C.DATA / "cbbd_cache"


def _key() -> str:
    k = os.environ.get("CBBD_API_KEY", "").strip()
    if not k:
        sys.exit("CBBD_API_KEY is not set. Add it as a repo secret; nothing was fetched.")
    return k


def _get(path, params, key):
    for i in range(4):
        try:
            r = requests.get(BASE + path, params=params, timeout=180,
                             headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        except requests.RequestException as e:  # dropped/truncated connection: retry
            print(f"  {path} {params}: {type(e).__name__}, retrying", flush=True)
            time.sleep(5 * (i + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403):
            sys.exit(f"CBBD refused the key (HTTP {r.status_code}). Check the CBBD_API_KEY secret.")
        if r.status_code == 429:
            sys.exit("CBBD monthly call limit reached (HTTP 429).")
        print(f"  {path} {params}: HTTP {r.status_code}, retrying", flush=True)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"CBBD {path} failed")


def _g(d, *names):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return None


def fetch_lines(season: int, key: str) -> pd.DataFrame:
    """One row per (game, provider). Splits the season by month when a
    single call looks truncated (a round-number response size)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"lines_{season}.json"
    if f.exists() and season != C.CURRENT_SEASON:
        data = json.loads(f.read_text())
    else:
        data = _get("/lines", {"season": season}, key)
        if len(data) in (1000, 2000, 2500, 3000, 5000, 10000):  # probably capped: go by month
            print(f"  {season}: {len(data)} rows looks capped; fetching by month", flush=True)
            data = []
            for y, m in [(season - 1, mm) for mm in (11, 12)] + [(season, mm) for mm in (1, 2, 3, 4)]:
                start = pd.Timestamp(y, m, 1)
                end = start + pd.offsets.MonthEnd(1)
                data += _get("/lines", {"season": season, "startDateRange": start.strftime("%Y-%m-%d"),
                                        "endDateRange": end.strftime("%Y-%m-%d")}, key)
        f.write_text(json.dumps(data))
    if data:
        print(f"  {season}: {len(data)} games; record keys: {sorted(data[0].keys())}", flush=True)
        ls = next((d.get("lines") for d in data if d.get("lines")), None)
        if ls:
            print(f"           line keys: {sorted(ls[0].keys())}", flush=True)
    rows = []
    for d in data:
        hs, as_ = _g(d, "homeScore", "homePoints"), _g(d, "awayScore", "awayPoints")
        date = _g(d, "startDate", "start_date", "date")
        for ln in d.get("lines") or []:
            rows.append(dict(
                cbbd_game_id=_g(d, "gameId", "id"), season=season, start=date,
                home=_g(d, "homeTeam"), away=_g(d, "awayTeam"), home_score=hs, away_score=as_,
                provider=_g(ln, "provider"),
                spread=_num(_g(ln, "spread")), total=_num(_g(ln, "overUnder", "total")),
                home_ml=_num(_g(ln, "homeMoneyline", "homeMoneyLine")),
                away_ml=_num(_g(ln, "awayMoneyline", "awayMoneyLine")),
                spread_open=_num(_g(ln, "spreadOpen")), total_open=_num(_g(ln, "overUnderOpen", "totalOpen"))))
    return pd.DataFrame(rows)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def join_to_espn(lines: pd.DataFrame, games: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Attach ESPN game_id by (date +-1, score pair). Returns rows in ESPN's
    home/away orientation plus join statistics."""
    L = lines.dropna(subset=["home_score", "away_score", "start"]).copy()
    # CBBD start times are UTC; ESPN dates are ET. Compare on the UTC date
    # minus 5h, and allow +-1 day.
    L["d"] = (pd.to_datetime(L.start, utc=True) - pd.Timedelta(hours=5)).dt.tz_localize(None).dt.normalize()
    G = games[["game_id", "game_date", "h_pts", "a_pts"]].copy()
    out, stats = [], dict(cbbd_games=int(L.cbbd_game_id.nunique()))
    for flip in (False, True):
        hs, as_ = ("away_score", "home_score") if flip else ("home_score", "away_score")
        for shift in (0, -1, 1):
            k = L.assign(kd=L.d + pd.Timedelta(days=shift), kh=L[hs].astype(float), ka=L[as_].astype(float))
            m = k.merge(G, left_on=["kd", "kh", "ka"], right_on=["game_date", "h_pts", "a_pts"])
            m["flipped"] = flip
            out.append(m)
    m = pd.concat(out, ignore_index=True)
    # a CBBD game must map to exactly one ESPN game, and vice versa
    pairs = m[["cbbd_game_id", "game_id"]].drop_duplicates()
    ok_c = pairs.cbbd_game_id.value_counts()
    ok_e = pairs.game_id.value_counts()
    pairs = pairs[pairs.cbbd_game_id.map(ok_c).eq(1) & pairs.game_id.map(ok_e).eq(1)]
    m = m.merge(pairs, on=["cbbd_game_id", "game_id"]).drop_duplicates(["cbbd_game_id", "provider"])
    for c in ("spread", "spread_open"):
        m[c] = np.where(m.flipped, -m[c], m[c])
    m["home_ml"], m["away_ml"] = (np.where(m.flipped, m.away_ml, m.home_ml), np.where(m.flipped, m.home_ml, m.away_ml))
    stats.update(matched_games=int(m.cbbd_game_id.nunique()), flipped_games=int(m[m.flipped].cbbd_game_id.nunique()))
    return m, stats


def report(seasons, key):
    from .backtest import load_params
    from .evaluate import PRED, ats_sim, incremental_value, md

    _, j = load_params()
    games = pd.read_parquet(C.DATA / "games.parquet")
    pred = pd.read_parquet(PRED)
    allrows, jstats = [], {}
    for s in seasons:
        lines = fetch_lines(s, key)
        if lines.empty:
            jstats[s] = dict(cbbd_games=0)
            continue
        m, st = join_to_espn(lines, games[games.season == s])
        jstats[s] = st
        allrows.append(m)
    L = ["# CBBD betting lines: coverage, provider check, and the 2023-25 market test", "",
         f"Model {j['model_version']}, walk-forward predictions (unchanged). Lines from CollegeBasketballData; "
         "only aggregates are published here. **All betting figures are simulated; no wagers.**", "",
         "## Coverage and join (ESPN game matched on date ±1 and final score)", "",
         "| season | CBBD games with lines | matched to ESPN | flipped home/away |", "|---|---|---|---|"]
    for s, st in jstats.items():
        L.append(f"| {s} | {st.get('cbbd_games', 0)} | {st.get('matched_games', 0)} | {st.get('flipped_games', 0)} |")
    if not allrows:
        L.append("\nNo lines returned.")
        return "\n".join(L)
    x = pd.concat(allrows, ignore_index=True)
    x = x.merge(pred, on="game_id", suffixes=("", "_p"))
    x = x[x.d1_game & x.spread.notna()].copy()
    x["mkt_margin"] = -x.spread

    L += ["", "## Sign check and accuracy, by season and provider", "",
          "corr(spread, margin) must be strongly negative if `spread` is the home line.", "",
          "| season | provider | n | corr | line MAE | model MAE (same games) | c (±SE), fit on this cell |",
          "|---|---|---|---|---|---|---|"]
    for (s, pv), g in x.groupby(["season", "provider"]):
        if len(g) < 200:
            continue
        iv = incremental_value(g, g.iloc[:0])
        L.append(f"| {s} | {pv} | {len(g)} | {np.corrcoef(g.spread, g.margin)[0, 1]:.3f} | "
                 f"{(g.margin - g.mkt_margin).abs().mean():.2f} | {(g.margin - g.pred_margin).abs().mean():.2f} | "
                 f"{iv['c']:.3f} ± {iv['c_se']:.3f} |")
    L += ["", "`c` is the weight on (model − line) in margin = a + b·line + c·(model − line). "
          "Near 0 means the model adds nothing beyond that line. A provider with a much larger c than "
          "the others is likely publishing a stale or non-closing number.", ""]

    # The gap seasons. Pre-declared: one provider per game (the book with the
    # most games in the season), fit c on 2024 only, score 2025; ATS rule
    # identical to evaluate.ats_sim.
    gap = x[x.season.isin([2024, 2025])]
    if len(gap):
        L += ["## Validation seasons 2023-24 and 2024-25 against a real line", ""]
        for s in (2024, 2025):
            g = gap[gap.season == s]
            if g.empty:
                continue
            top = g.provider.value_counts().index[0]
            g = g[g.provider == top]
            mk_rmse = float(np.sqrt(((g.margin - g.mkt_margin) ** 2).mean()))
            md_rmse = float(np.sqrt(((g.margin - g.pred_margin) ** 2).mean()))
            a = ats_sim(g.assign(home_spread_odds=np.nan, away_spread_odds=np.nan))
            L.append(f"- **{s}, {top}** (n={len(g)}): line RMSE {mk_rmse:.3f}, model RMSE {md_rmse:.3f}. "
                     f"Simulated ATS at this line (-110 assumed; CBBD carries no spread prices): "
                     f"{a['wins']}-{a['losses']}-{a['pushes']} ({a['win_pct']:.1%}), ROI {a['roi']:+.1%} "
                     f"(95% CI {a['roi_95ci'][0]:+.1%} to {a['roi_95ci'][1]:+.1%}).")
        t24 = gap[gap.season == 2024]
        t25 = gap[gap.season == 2025]
        if len(t24) and len(t25):
            top = t24.provider.value_counts().index[0]
            tr, te = t24[t24.provider == top], t25[t25.provider == top]
            if len(tr) > 200 and len(te) > 200:
                iv = incremental_value(tr, te)
                L.append(f"- **Blend fit on 2024, scored on 2025 ({top})**: c = {iv['c']:.3f} ± {iv['c_se']:.3f}; "
                         f"RMSE line {iv['rmse_market']:.3f} vs blend {iv['rmse_blend']:.3f}.")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2016-2027")
    a = ap.parse_args()
    lo, hi = (int(v) for v in a.seasons.split("-"))
    txt = report(list(range(lo, hi + 1)), _key())
    C.REPORTS.mkdir(exist_ok=True)
    (C.REPORTS / "cbbd_lines.md").write_text(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
