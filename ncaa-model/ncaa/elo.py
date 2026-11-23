"""Margin-of-victory Elo: an independent, deliberately simple benchmark.

Updated game by game in date order, so a prediction for date d only ever
reflects games completed before d (all games on one date are predicted
before any of them updates the ratings).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class EloParams:
    k: float = 25.0
    hca: float = 85.0            # Elo points for a true home game
    carry: float = 0.70          # fraction of (rating - 1500) kept across seasons
    new_team: float = 1300.0     # first sighting (new D1 or non-D1 team)
    pts_per_elo: float = 1 / 28  # margin per Elo point; refit by backtest.calibrate


def expected(diff):
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def run(games: pd.DataFrame, p: EloParams = EloParams()) -> pd.DataFrame:
    """Return per-game pregame Elo diff (home - away, incl. HCA) and win prob."""
    rating: dict[int, float] = {}
    season_seen: dict[int, int] = {}
    out_diff = np.empty(len(games))
    g = games.reset_index(drop=True)
    for d, idx in g.groupby("game_date", sort=True).groups.items():
        rows = g.loc[idx]
        # season rollover regression
        for t, s in zip(np.r_[rows.h_team_id.values, rows.a_team_id.values],
                        np.r_[rows.season.values, rows.season.values]):
            if t not in rating:
                rating[t] = p.new_team
                season_seen[t] = s
            elif season_seen[t] != s:
                rating[t] = 1500 + p.carry * (rating[t] - 1500)
                season_seen[t] = s
        diffs = []
        for i, r in rows.iterrows():
            diff = rating[r.h_team_id] - rating[r.a_team_id] + (0 if r.neutral_site else p.hca)
            out_diff[i] = diff
            diffs.append((i, r, diff))
        for i, r, diff in diffs:  # update after the whole date is predicted
            e = expected(diff)
            s = 1.0 if r.margin > 0 else 0.0
            mov = np.log(abs(r.margin) + 1) * 2.2 / ((diff if s else -diff) * 0.001 + 2.2)
            delta = p.k * mov * (s - e)
            rating[r.h_team_id] += delta
            rating[r.a_team_id] -= delta
    return pd.DataFrame({"game_id": g.game_id.values, "elo_diff": out_diff,
                         "elo_p_home": expected(out_diff)})
