"""Preliminary prediction board for one date.

    python -m ncaa.board 2026-11-23

Ratings are fitted on this season's completed games strictly before the
date (prior = last season's final ratings, regressed). Output is written
once to boards/<date>.{csv,md}; an existing board is never overwritten,
because an original prediction must never be revised after the fact.

Market classification (Qualified / Watchlist / No Bet) needs live prices.
None are reachable from this build (see docs/DATA_SOURCES.md), so every
game is marked "no market line" and no EV is claimed.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import config as C
from .backtest import load_params, sigma_for
from .ingest import d1_teams, load_games, team_names
from .ratings import fit, predict


def season_final(games, season, p):
    prior = None
    for s in range(C.FIRST_SEASON, season):
        prior = fit(games, None, s, prior, p)
    return prior


def build(date: str):
    p, j = load_params()
    d = pd.Timestamp(date)
    games = load_games()
    season = C.CURRENT_SEASON
    prior = season_final(games, season, p)
    r = fit(games, d, season, prior, p)
    sch = pd.read_parquet(C.RAW / f"mbb_schedule_{season}.parquet")
    sch["game_date"] = pd.to_datetime(sch.game_date)
    day = sch[sch.game_date == d].drop_duplicates("game_id").sort_values("game_date_time")
    names = team_names()
    d1 = d1_teams(season)
    rows = []
    for g in day.itertuples():
        pr = predict(r, g.home_id, g.away_id, bool(g.neutral_site))
        if pr is None:
            continue
        hi, ai = r.idx[int(g.home_id)], r.idx[int(g.away_id)]
        gp = min(r.n_games[hi], r.n_games[ai])
        sm = float(sigma_for([gp], j["sigma"]["margin"])[0])
        st = float(sigma_for([gp], j["sigma"]["total"])[0])
        margin, total = pr[0] - pr[1], pr[0] + pr[1]
        rows.append(dict(
            game_id=int(g.game_id), tipoff_et=str(g.game_date_time)[11:16],
            away=names.get(int(g.away_id)), home=names.get(int(g.home_id)),
            neutral=bool(g.neutral_site), d1_vs_d1=(int(g.home_id) in d1 and int(g.away_id) in d1),
            proj_away=round(pr[1], 1), proj_home=round(pr[0], 1), proj_poss=round(pr[2], 1),
            home_margin=round(margin, 1), total=round(total, 1),
            p_home_win=round(float(norm.cdf(margin / sm)), 3),
            margin_80=f"{margin - 1.2816 * sm:+.0f} to {margin + 1.2816 * sm:+.0f}",
            total_80=f"{total - 1.2816 * st:.0f}-{total + 1.2816 * st:.0f}",
            games_played_min=int(gp), market="no market line (no EV computed)"))
    df = pd.DataFrame(rows)
    return df, r, names


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    C.BOARDS.mkdir(exist_ok=True)
    out_csv, out_md = C.BOARDS / f"{date}.csv", C.BOARDS / f"{date}.md"
    if out_csv.exists() and "--force" not in sys.argv:
        sys.exit(f"{out_csv} exists; boards are never overwritten.")
    df, r, names = build(date)
    df.to_csv(out_csv, index=False)
    _, j = load_params()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    top = r.frame(names)
    top = top[top.team_id.isin(d1_teams(C.CURRENT_SEASON))].head(25)
    L = [f"# Preliminary board — {date} (model {j['model_version']})", "",
         f"Generated {stamp}. Ratings use games before {date} only "
         f"({int(r.n_games.sum() / 2)} games this season). **PROJECTIONS, not verified results; no market prices, so no "
         "game is classified Qualified or Watchlist.** Early season: ratings still lean heavily on last season's "
         "(regressed) numbers, and roster turnover/transfers are NOT modelled yet.", "",
         f"{len(df)} games, {int(df.d1_vs_d1.sum())} D1-vs-D1.", "",
         "| tip (ET) | away | home | proj | home margin (80%) | total (80%) | P(home) |",
         "|---|---|---|---|---|---|---|"]
    for x in df.itertuples():
        site = " (N)" if x.neutral else ""
        L.append(f"| {x.tipoff_et} | {x.away} | {x.home}{site} | {x.proj_away:.0f}-{x.proj_home:.0f} | "
                 f"{x.home_margin:+.1f} ({x.margin_80}) | {x.total:.1f} ({x.total_80}) | {x.p_home_win:.0%} |")
    L += ["", "## Top 25 by adjusted efficiency margin (as of this board)", "",
          "| # | team | AdjO | AdjD | AdjEM | AdjT | GP |", "|---|---|---|---|---|---|---|"]
    for i, t in enumerate(top.itertuples(), 1):
        L.append(f"| {i} | {t.team} | {t.adj_off:.1f} | {t.adj_def:.1f} | {t.adj_em:+.1f} | {t.adj_tempo:.1f} | {int(t.games)} |")
    out_md.write_text("\n".join(L) + "\n")
    print("\n".join(L[:12]))
    print("wrote", out_csv, out_md)


if __name__ == "__main__":
    main()
