"""Phase 3: Four Factors and matchup features, walk-forward.

    python -m ncaa.fourfactors build      # adjusted four-factor ratings before every game
    python -m ncaa.fourfactors test       # residual model: fit on tune, score on validation

For each box-score rate (eFG%, turnover rate, offensive-rebound rate, FT
rate, 3PA share, 3P%, 2P%, FT%) we fit an opponent-adjusted offence and
defence rating exactly like the efficiency model:

    rate(team vs opp) = mu + off[team] + dfn[opp] + noise

on games strictly before the prediction date, shrunk toward last season's
final value. The matchup features for a game are the expected rates for each
side, their home-minus-away differences, and a few interactions
(three-point volume x opponent three-point defence, offensive boards x
opponent defensive boards).

The question these features answer is narrow: given the efficiency model's
projected margin and total, do the components and the matchup carry ANY
additional information? The test is a ridge regression of the efficiency
model's residual on the features, fitted on tune seasons and scored on the
validation seasons. The holdout is not touched.
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from . import config as C
from .ingest import load_games
from .ratings import _ridge

OUT = C.DATA / "fourfactors"

STATS = ["efg", "tov", "orb", "ftr", "tpar", "tp", "twop", "ft"]


def side_rates(g: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Per-game rates for (home offence, away offence) with their weights
    (the denominator, so a 3P% on 30 attempts counts more than on 10)."""
    out = {}
    for s, o in (("h", "a"), ("a", "h")):
        fgm, fga = g[f"{s}_field_goals_made"], g[f"{s}_field_goals_attempted"]
        tpm, tpa = g[f"{s}_three_point_field_goals_made"], g[f"{s}_three_point_field_goals_attempted"]
        ftm, fta = g[f"{s}_free_throws_made"], g[f"{s}_free_throws_attempted"]
        orb, odrb = g[f"{s}_offensive_rebounds"], g[f"{o}_defensive_rebounds"]
        tov, poss = g[f"{s}_tov"], g.poss
        vals = dict(efg=((fgm + 0.5 * tpm) / fga, fga), tov=(tov / poss, poss), orb=(orb / (orb + odrb), orb + odrb),
                    ftr=(fta / fga, fga), tpar=(tpa / fga, fga), tp=(tpm / tpa, tpa),
                    twop=((fgm - tpm) / (fga - tpa), fga - tpa), ft=(ftm / fta, fta))
        out[s] = {k: (np.asarray(v[0], float), np.asarray(v[1], float)) for k, v in vals.items()}
    return out


def fit_stat(y, w, hi, ai, n, prior_o, prior_d, mu0, lam):
    """Ridge fit of rate = mu + off[team] + dfn[opp]; rows are (home off,
    away off) stacked."""
    m = len(hi)
    rows = np.arange(2 * m)
    off_col = np.concatenate([hi, ai])
    def_col = n + np.concatenate([ai, hi])
    mu_col = np.full(2 * m, 2 * n)
    R = np.concatenate([rows, rows, rows])
    Cc = np.concatenate([off_col, def_col, mu_col])
    V = np.ones(6 * m)
    lamv = np.concatenate([np.full(2 * n, lam), [1e-3]])
    pri = np.concatenate([prior_o, prior_d, [mu0]])
    beta = _ridge(R, Cc, V, y, w, 2 * n + 1, pri, lamv)
    return beta[:n], beta[n:2 * n], beta[2 * n]


# typical per-game denominators, so `lam` reads as games-equivalent
DENOM = dict(efg=55, tov=68, orb=35, ftr=55, tpar=55, tp=20, twop=35, ft=18)


def build(games: pd.DataFrame, seasons, lam_games=5.0, regress=0.6, half_life=60.0, refit_every=2) -> pd.DataFrame:
    """Pre-game adjusted rates for every game in `seasons` (walk-forward)."""
    games = games[games.poss > 0].copy()
    rates = side_rates(games)
    finals = {}  # season -> {stat: (teams, off, dfn, mu)}
    rows = []
    t0 = time.time()
    for s in range(C.FIRST_SEASON, max(seasons) + 1):
        idx = np.where(games.season.values == s)[0]
        gs = games.iloc[idx]
        teams = np.unique(np.r_[gs.h_team_id.values, gs.a_team_id.values]).astype(int)
        n = len(teams)
        ti = {t: i for i, t in enumerate(teams)}
        hi_all = gs.h_team_id.map(ti).values
        ai_all = gs.a_team_id.map(ti).values
        prev = finals.get(s - 1)
        priors = {}
        for k in STATS:
            po, pd_, mu0 = np.zeros(n), np.zeros(n), None
            if prev is not None:
                pt, poff, pdfn, pmu = prev[k]
                pm = {t: i for i, t in enumerate(pt)}
                for t, i in ti.items():
                    if t in pm:
                        po[i], pd_[i] = regress * poff[pm[t]], regress * pdfn[pm[t]]
                mu0 = pmu
            if mu0 is None:
                yh = rates["h"][k][0][idx]
                mu0 = float(np.nanmean(yh))
            priors[k] = (po, pd_, mu0)
        dates = np.sort(gs.game_date.unique())
        cur, last = None, None
        if s in seasons:
            for d in dates:
                d = pd.Timestamp(d)
                if cur is None or (d - last).days >= refit_every:
                    msk = (gs.game_date < d).values
                    cur, last = {}, d
                    for k in STATS:
                        cur[k] = _fit_season(k, rates, idx[msk], hi_all[msk], ai_all[msk], n, priors[k],
                                             gs.game_date.values[msk], d, lam_games, half_life)
                today = np.where((gs.game_date == d).values)[0]
                for j in today:
                    hi, ai = hi_all[j], ai_all[j]
                    rec = [gs.game_id.values[j]]
                    for k in STATS:
                        off, dfn, mu = cur[k]
                        rec += [mu + off[hi] + dfn[ai], mu + off[ai] + dfn[hi], off[hi], dfn[hi], off[ai], dfn[ai]]
                    rows.append(rec)
        # season final (all games) -> next season's prior
        finals[s] = {}
        for k in STATS:
            off, dfn, mu = _fit_season(k, rates, idx, hi_all, ai_all, n, priors[k], None, None, lam_games, np.inf)
            finals[s][k] = (teams, off, dfn, mu)
        print(f"  {s} done {time.time() - t0:.0f}s", flush=True)
    cols = ["game_id"]
    for k in STATS:
        cols += [f"x_{k}_h", f"x_{k}_a", f"{k}_off_h", f"{k}_dfn_h", f"{k}_off_a", f"{k}_dfn_a"]
    return pd.DataFrame(rows, columns=cols)


def _fit_season(k, rates, rows_idx, hi, ai, n, prior, dates, asof, lam_games, half_life):
    yh, wh = rates["h"][k][0][rows_idx], rates["h"][k][1][rows_idx]
    ya, wa = rates["a"][k][0][rows_idx], rates["a"][k][1][rows_idx]
    if asof is not None and np.isfinite(half_life) and len(rows_idx):
        age = (pd.Timestamp(asof) - pd.to_datetime(dates)).days.values.astype(float)
        rw = 0.5 ** (age / half_life)
    else:
        rw = np.ones(len(rows_idx))
    y = np.r_[yh, ya]
    w = np.r_[wh * rw, wa * rw] / DENOM[k]
    ok = np.isfinite(y) & np.isfinite(w) & (w > 0)
    po, pd_, mu0 = prior
    if len(rows_idx) == 0 or ok.sum() == 0:
        return po, pd_, mu0
    y, w = np.where(ok, y, 0.0), np.where(ok, w, 0.0)
    return fit_stat(y, w, hi, ai, n, po, pd_, mu0, lam_games)


# ------------------------------------------------------------------ residual test
def design(df: pd.DataFrame) -> pd.DataFrame:
    """Matchup features (home minus away where it is a difference)."""
    X = pd.DataFrame(index=df.index)
    for k in ("efg", "tov", "orb", "ftr", "tpar", "tp", "twop", "ft"):
        X[f"d_{k}"] = df[f"x_{k}_h"] - df[f"x_{k}_a"]
        X[f"s_{k}"] = df[f"x_{k}_h"] + df[f"x_{k}_a"]      # sum: for the total
    # luck / regression terms: a defence's 3P% and FT% allowed are mostly
    # noise; a rating built on them should be expected to give some back
    X["luck_tp_dfn"] = df.tp_dfn_h - df.tp_dfn_a
    X["luck_ft_dfn"] = df.ft_dfn_h - df.ft_dfn_a
    # matchup interactions
    X["int_3pt"] = df.tpar_off_h * df.tp_dfn_a - df.tpar_off_a * df.tp_dfn_h
    X["int_orb"] = df.orb_off_h * df.orb_dfn_a - df.orb_off_a * df.orb_dfn_h
    X["int_tov"] = df.tov_off_h * df.tov_dfn_a - df.tov_off_a * df.tov_dfn_h
    return X


def ridge_fit(X, y, alpha):
    mu, sd = X.mean(0), X.std(0).replace(0, 1)
    Z = ((X - mu) / sd).values
    A = Z.T @ Z + alpha * np.eye(Z.shape[1])
    b = np.linalg.solve(A, Z.T @ (y - y.mean()))
    return dict(mu=mu, sd=sd, b=b, c=float(y.mean()), cols=list(X.columns))


def ridge_pred(m, X):
    Z = ((X[m["cols"]] - m["mu"]) / m["sd"]).values
    return m["c"] + Z @ m["b"]


EVAL_SEASONS = list(range(2016, 2026)) + [C.CURRENT_SEASON]   # 2026 = holdout: never built here


def residual_test(ff: pd.DataFrame, base: pd.DataFrame, alphas=(10, 100, 1000)) -> pd.DataFrame:
    """Fit residual = margin - base margin (and total) on tune seasons,
    score on validation. Returns one row per (target, alpha)."""
    d = base[base.d1_game].merge(ff, on="game_id")
    X = design(d).fillna(0.0)
    X["loc"] = np.where(d.neutral_site, 0.0, 1.0)
    tr = d.season.isin(C.TUNE_SEASONS).values
    va = d.season.isin(C.VALIDATION_SEASONS).values
    out = []
    for target, pred in (("margin", "pred_margin"), ("total_pts", "pred_total")):
        r = (d[target] - d[pred]).values
        cols = [c for c in X.columns if (c.startswith("s_") if target == "total_pts" else not c.startswith("s_"))]
        if target == "total_pts":
            cols += ["loc"]
        for a in alphas:
            m = ridge_fit(X.loc[tr, cols], r[tr], a)
            adj = ridge_pred(m, X.loc[va, cols])
            base_rmse = float(np.sqrt(np.mean(r[va] ** 2)))
            new_rmse = float(np.sqrt(np.mean((r[va] - adj) ** 2)))
            out.append(dict(target=target, alpha=a, n_train=int(tr.sum()), n_val=int(va.sum()),
                            val_rmse_base=base_rmse, val_rmse_with_ff=new_rmse, gain=base_rmse - new_rmse,
                            top_coefs=", ".join(f"{c}:{b:+.2f}" for c, b in
                                                sorted(zip(cols, m["b"]), key=lambda x: -abs(x[1]))[:4])))
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "test"])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "build":
        ff = build(load_games(), EVAL_SEASONS)
        ff.to_parquet(OUT / "ff_pregame.parquet", index=False)
        print("rows", len(ff))
    else:
        from .backtest import attach, load_params, walk_forward
        g = load_games()
        f = OUT / "base_ae01.parquet"
        if not f.exists():
            p, _ = load_params()
            attach(g, walk_forward(g, list(range(2016, 2026)), p)).to_parquet(f, index=False)
        base = pd.read_parquet(f)
        ff = pd.read_parquet(OUT / "ff_pregame.parquet")
        print(residual_test(ff, base).round(4).to_string())


if __name__ == "__main__":
    main()
