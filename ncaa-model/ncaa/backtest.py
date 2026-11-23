"""Walk-forward backtest. Every prediction for date d is made from ratings
fitted on games strictly before d, with priors from the previous season's
final ratings. Nothing from d or later can reach it.

    python -m ncaa.backtest tune          # grid search on TUNE seasons only
    python -m ncaa.backtest report        # tuned params -> tune + validation
    python -m ncaa.backtest holdout       # run ONCE per model version
"""
from __future__ import annotations

import argparse
import itertools
import json
import time
from dataclasses import asdict

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import config as C
from . import elo, metrics as M
from .ingest import load_games, load_odds
from .ratings import Params, Ratings, fit, predict

PARAMS_FILE = C.ROOT / "model_params.json"


def final_ratings(games, seasons, p: Params) -> dict[int, Ratings]:
    """End-of-season ratings for each season (used only as NEXT season's prior)."""
    out, prev = {}, None
    for s in seasons:
        prev = fit(games, None, s, prev, p)
        out[s] = prev
    return out


def walk_forward(games: pd.DataFrame, seasons, p: Params, refit_every: int = 1) -> pd.DataFrame:
    first = min(seasons)
    prior_seasons = [s for s in range(C.FIRST_SEASON, first)]
    finals = final_ratings(games, prior_seasons, p) if prior_seasons else {}
    prior = finals.get(first - 1)
    rows = []
    for s in seasons:
        gs = games[games.season == s]
        dates = np.sort(gs.game_date.unique())
        r, last_fit = None, None
        for d in dates:
            d = pd.Timestamp(d)
            if r is None or (d - last_fit).days >= refit_every:
                r, last_fit = fit(games, d, s, prior, p), d
            for g in gs[gs.game_date == d].itertuples():
                pr = predict(r, g.h_team_id, g.a_team_id, g.neutral_site)
                if pr is None:
                    continue
                hi, ai = r.idx[g.h_team_id], r.idx[g.a_team_id]
                rows.append((g.game_id, pr[0], pr[1], pr[2], r.n_games[hi], r.n_games[ai]))
        prior = fit(games, None, s, prior, p)  # season final -> next prior
    df = pd.DataFrame(rows, columns=["game_id", "pred_h", "pred_a", "pred_poss", "h_gp", "a_gp"])
    df["pred_margin"] = df.pred_h - df.pred_a
    df["pred_total"] = df.pred_h + df.pred_a
    return df


def attach(games, preds):
    m = preds.merge(games[["game_id", "season", "game_date", "margin", "total_pts", "poss", "neutral_site",
                           "d1_game", "h_team_id", "a_team_id", "conference_competition",
                           "home_conference_id", "season_type"]], on="game_id")
    m["home_win"] = (m.margin > 0).astype(int)
    return m


def score(m: pd.DataFrame, sigma: float | None = None) -> dict:
    d = m[m.d1_game]
    err = d.margin - d.pred_margin
    terr = d.total_pts - d.pred_total
    sigma = sigma or float(err.std())
    p = norm.cdf(d.pred_margin / sigma)
    return dict(n=len(d), margin_mae=float(err.abs().mean()), margin_rmse=float(np.sqrt((err ** 2).mean())),
                margin_bias=float(err.mean()), total_mae=float(terr.abs().mean()),
                total_rmse=float(np.sqrt((terr ** 2).mean())), total_bias=float(terr.mean()),
                su_acc=float(((d.pred_margin > 0) == (d.margin > 0)).mean()),
                brier=M.brier(p, d.home_win), log_loss=M.log_loss(p, d.home_win), sigma=sigma)


def fit_sigma(m: pd.DataFrame) -> dict:
    """Residual SDs from TUNE seasons only, split by how many games each team
    had played (early season is noisier). Stored in model_params.json."""
    d = m[m.d1_game].copy()
    d["gp"] = np.minimum(d.h_gp, d.a_gp)
    d["bucket"] = pd.cut(d.gp, [-1, 2, 5, 10, 1000], labels=["0-2", "3-5", "6-10", "11+"])
    sm = d.groupby("bucket", observed=True).apply(lambda x: float((x.margin - x.pred_margin).std())).to_dict()
    st = d.groupby("bucket", observed=True).apply(lambda x: float((x.total_pts - x.pred_total).std())).to_dict()
    return {"margin": {str(k): v for k, v in sm.items()}, "total": {str(k): v for k, v in st.items()}}


def sigma_for(gp, table):
    gp = np.asarray(gp)
    keys = np.select([gp <= 2, gp <= 5, gp <= 10], ["0-2", "3-5", "6-10"], "11+")
    return np.array([table[k] for k in keys])


# ----------------------------------------------------------------- tuning
GRID = dict(prior_games=[3.0, 6.0, 10.0], prior_regress=[0.5, 0.65, 0.8], half_life_days=[30.0, 60.0, np.inf])


def tune(games):
    res = []
    for pg, pr, hl in itertools.product(*GRID.values()):
        p = Params(prior_games=pg, prior_regress=pr, half_life_days=hl, tempo_prior_games=pg,
                   tempo_regress=min(0.9, pr + 0.1))
        t0 = time.time()
        m = attach(games, walk_forward(games, C.TUNE_SEASONS, p, refit_every=3))
        s = score(m)
        s.update(asdict(p))
        s["secs"] = round(time.time() - t0, 1)
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}, flush=True)
        res.append(s)
    df = pd.DataFrame(res).sort_values("margin_rmse")
    df.to_csv(C.REPORTS / "tuning_grid.csv", index=False)
    best = df.iloc[0]
    p = Params(prior_games=best.prior_games, prior_regress=best.prior_regress,
               half_life_days=best.half_life_days, tempo_prior_games=best.prior_games,
               tempo_regress=min(0.9, best.prior_regress + 0.1))
    # residual SDs from a daily-refit run on tune seasons
    m = attach(games, walk_forward(games, C.TUNE_SEASONS, p))
    sig = fit_sigma(m)
    elo_p = tune_elo(games)
    PARAMS_FILE.write_text(json.dumps({"model_version": "ae-0.1", "params": asdict(p), "sigma": sig,
                                       "elo": elo_p, "tuned_on": C.TUNE_SEASONS},
                                      indent=2, default=float))
    print("saved", PARAMS_FILE)


def tune_elo(games):
    best = None
    tune = games[games.season.isin(C.TUNE_SEASONS) & games.d1_game]
    for k, carry in itertools.product([15, 25, 35], [0.5, 0.7, 0.85]):
        e = elo.run(games[games.season <= max(C.TUNE_SEASONS)], elo.EloParams(k=k, carry=carry))
        x = e.merge(tune[["game_id", "margin"]], on="game_id")
        ll = M.log_loss(x.elo_p_home, x.margin > 0)
        if best is None or ll < best[0]:
            slope = float(np.polyfit(x.elo_diff, x.margin, 1)[0])
            sig = float((x.margin - slope * x.elo_diff).std())
            best = (ll, dict(k=k, carry=carry, pts_per_elo=slope, margin_sigma=sig))
    print("elo best", best)
    return best[1]


def load_params():
    j = json.loads(PARAMS_FILE.read_text())
    pp = j["params"]
    return Params(**pp), j


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["tune"])
    a = ap.parse_args()
    C.REPORTS.mkdir(exist_ok=True)
    tune(load_games())
