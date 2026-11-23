"""Phase 3 experiments: does each preseason-prior block add predictive value?

    python -m ncaa.phase3 run   [--seasons tune|validation|tune+validation]
    python -m ncaa.phase3 report

Each variant is the ae-0.1 rating model with ONE thing changed: the
preseason prior (mean and per-team strength). Everything else (recency,
home edge, sigma table, possessions) is ae-0.1's, frozen. Predictions are
walk-forward: game on date d uses only games before d, and the preseason
model for season s is fitted on target seasons < s.

Variants are cumulative so each row's gain over the row above is that
block's incremental value. The 2026 holdout is never touched here.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import config as C
from . import metrics as M
from .backtest import attach, load_params, sigma_for, walk_forward
from .ingest import load_games
from .priors import (PriorSpec, build_prior, conferences, feature_table, load_team_priors,
                     season_ratings)
from .ratings import fit, predict

OUT = C.DATA / "phase3"

VARIANTS = [
    PriorSpec("P0 last season, refit slopes", ("last",)),
    PriorSpec("Q0 last season + end-of-season form", ("last", "form")),
    PriorSpec("Q2 + two back + conference", ("last", "form", "l2", "conf")),
    PriorSpec("P1 + two seasons back", ("last", "l2")),
    PriorSpec("P2 + conference pooling", ("last", "l2", "conf")),
    PriorSpec("P3 + returning minutes", ("last", "l2", "conf", "ret")),
    PriorSpec("P4 + incoming transfers", ("last", "l2", "conf", "ret", "transfer")),
    PriorSpec("P5 + recruits", ("last", "l2", "conf", "ret", "transfer", "recruit")),
    PriorSpec("P6 + roster-dependent prior width", ("last", "l2", "conf", "ret", "transfer", "recruit"),
              hetero=True),
]
K_GRID = [0.25, 0.35, 0.5]


def walk_forward_prior(games, seasons, p, spec, feat, sr, refit_every=1):
    rows, coefs = [], {}
    for s in seasons:
        built = build_prior(feat, sr, s, spec)
        if built is None:
            raise RuntimeError(f"no training seasons for {s}")
        prior, model = built
        coefs[s] = {c: list(np.round(model[c]["beta"], 4)) for c in ("off", "dfn", "tempo")}
        coefs[s]["var"] = {c: round(model[c]["var"], 2) for c in ("off", "dfn", "tempo")}
        coefs[s]["cols"] = model["cols"]
        gs = games[games.season == s]
        r, last = None, None
        for d in np.sort(gs.game_date.unique()):
            d = pd.Timestamp(d)
            if r is None or (d - last).days >= refit_every:
                r, last = fit(games, d, s, prior, p), d
            for g in gs[gs.game_date == d].itertuples():
                pr = predict(r, g.h_team_id, g.a_team_id, g.neutral_site)
                if pr is None:
                    continue
                hi, ai = r.idx[g.h_team_id], r.idx[g.a_team_id]
                rows.append((g.game_id, pr[0], pr[1], pr[2], r.n_games[hi], r.n_games[ai]))
    df = pd.DataFrame(rows, columns=["game_id", "pred_h", "pred_a", "pred_poss", "h_gp", "a_gp"])
    df["pred_margin"] = df.pred_h - df.pred_a
    df["pred_total"] = df.pred_h + df.pred_a
    return df, coefs


def summarize(m: pd.DataFrame, j: dict) -> dict:
    d = m[m.d1_game].copy()
    gp = np.minimum(d.h_gp, d.a_gp)
    sig = sigma_for(gp, j["sigma"]["margin"])
    p = norm.cdf(d.pred_margin / sig)
    e = d.margin - d.pred_margin
    te = d.total_pts - d.pred_total
    early = (gp <= 5).values
    novdec = d.game_date.dt.month.isin([11, 12]).values
    return dict(n=len(d), rmse=float(np.sqrt((e ** 2).mean())), mae=float(e.abs().mean()),
                brier=M.brier(p, d.home_win), log_loss=M.log_loss(p, d.home_win),
                total_mae=float(te.abs().mean()),
                rmse_gp0_5=float(np.sqrt((e[early] ** 2).mean())), n_gp0_5=int(early.sum()),
                rmse_novdec=float(np.sqrt((e[novdec] ** 2).mean())),
                brier_novdec=M.brier(p[novdec], d.home_win.values[novdec]),
                rmse_janmar=float(np.sqrt((e[~novdec] ** 2).mean())))


def run(seasons, label, variants=None, ks=None):
    OUT.mkdir(parents=True, exist_ok=True)
    p, j = load_params()
    games = load_games()
    t0 = time.time()
    sr = season_ratings(games, p)
    feat = feature_table(sr, conferences(games), load_team_priors())
    feat.to_parquet(OUT / "features.parquet", index=False)
    res = []
    base = attach(games, walk_forward(games, seasons, p))
    base.to_parquet(OUT / f"pred_{label}_ae01.parquet", index=False)
    res.append(dict(variant="ae-0.1 (current)", k=np.nan, **summarize(base, j)))
    print(res[-1], f"{time.time() - t0:.0f}s", flush=True)
    for spec in variants or VARIANTS:
        for k in ks or K_GRID:
            sp = PriorSpec(spec.name, spec.blocks, k=k, hetero=spec.hetero)
            pr, coefs = walk_forward_prior(games, seasons, p, sp, feat, sr)
            m = attach(games, pr)
            tag = f"{spec.name.split()[0]}_k{k}"
            m.to_parquet(OUT / f"pred_{label}_{tag}.parquet", index=False)
            res.append(dict(variant=spec.name, k=k, **summarize(m, j)))
            print({kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in res[-1].items()},
                  f"{time.time() - t0:.0f}s", flush=True)
            (OUT / f"coefs_{label}_{tag}.json").write_text(json.dumps(coefs, default=float))
    out = pd.DataFrame(res)
    out.to_csv(OUT / f"results_{label}.csv", index=False)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--seasons", default="tune")
    ap.add_argument("--variants", default="")
    ap.add_argument("--k", default="")
    a = ap.parse_args()
    seasons = {"tune": list(range(2018, 2023)), "validation": C.VALIDATION_SEASONS,
               "current": [C.CURRENT_SEASON]}[a.seasons]
    vs = [v for v in VARIANTS if not a.variants or v.name.split()[0] in a.variants.split(",")]
    ks = [float(x) for x in a.k.split(",")] if a.k else None
    print(run(seasons, a.seasons, vs, ks).round(4).to_string())
