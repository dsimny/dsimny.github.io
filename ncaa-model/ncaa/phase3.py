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


def experiments():
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




# ====================================================================== final
# Frozen after the tune-season experiments above (reports/phase3_tune.md):
FINAL_SPEC = PriorSpec("ae-0.2 prior", ("last", "l2", "conf", "ret", "transfer", "recruit"), k=0.5, hetero=True)
FF_ALPHA = 100.0
FF_TRAIN = list(range(2018, 2023))       # tune seasons with a fitted preseason model
EVAL = list(range(2018, 2026)) + [C.CURRENT_SEASON]   # 2026 (holdout) excluded


def final_predictions(seasons=EVAL, include_holdout=False) -> pd.DataFrame:
    """Walk-forward predictions for ae-0.1, +priors, +priors+four-factors."""
    from .fourfactors import EVAL_SEASONS, build as ff_build, design, ridge_fit, ridge_pred
    OUT.mkdir(parents=True, exist_ok=True)
    seasons = list(seasons) + ([2026] if include_holdout else [])
    p, j = load_params()
    games = load_games()
    sr = season_ratings(games, p)
    feat = feature_table(sr, conferences(games), load_team_priors())
    # ae-0.1 chains each season's final rating into the next season's prior,
    # so its walk-forward must run through every season up to the last one
    # asked for. Rows for seasons not asked for (e.g. the 2026 holdout) are
    # dropped unseen.
    chain = list(range(min(seasons), max(seasons) + 1))
    a = attach(games, walk_forward(games, chain, p))
    a = a[a.season.isin(seasons)]
    b, coefs = walk_forward_prior(games, seasons, p, FINAL_SPEC, feat, sr)
    m = a.merge(b[["game_id", "pred_margin", "pred_total", "h_gp", "a_gp"]].rename(
        columns={"pred_margin": "pm_prior", "pred_total": "pt_prior", "h_gp": "h_gp2", "a_gp": "a_gp2"}),
        on="game_id")
    m = m.rename(columns={"pred_margin": "pm_ae01", "pred_total": "pt_ae01"})
    fff = C.DATA / "fourfactors" / "ff_pregame.parquet"
    if fff.exists() and not include_holdout:
        ff = pd.read_parquet(fff)
    else:
        ff = ff_build(games, sorted(set(EVAL_SEASONS) | set(seasons)))
        fff.parent.mkdir(parents=True, exist_ok=True)
        ff.to_parquet(fff, index=False)
    m = m.merge(ff, on="game_id", how="left")
    X = design(m).fillna(0.0)
    X["loc"] = np.where(m.neutral_site, 0.0, 1.0)
    tr = (m.season.isin(FF_TRAIN) & m.d1_game).values
    mcols = [c for c in X.columns if not c.startswith("s_") and c != "loc"]
    tcols = [c for c in X.columns if c.startswith("s_")] + ["loc"]
    rm = ridge_fit(X.loc[tr, mcols], (m.margin - m.pm_prior).values[tr], FF_ALPHA)
    rt = ridge_fit(X.loc[tr, tcols], (m.total_pts - m.pt_prior).values[tr], FF_ALPHA)
    has = m.x_efg_h.notna().values
    m["pm_ff"] = m.pm_prior + np.where(has, ridge_pred(rm, X[mcols]), 0.0)
    m["pt_ff"] = m.pt_prior + np.where(has, ridge_pred(rt, X[tcols]), 0.0)
    # sigma by games played, refit on the FF training seasons only
    gp = np.minimum(m.h_gp, m.a_gp)
    m["gp_bucket"] = np.select([gp <= 2, gp <= 5, gp <= 10], ["0-2", "3-5", "6-10"], "11+")
    for k in ("ae01", "prior", "ff"):
        sig = (m[tr].assign(e=m.margin - m[f"pm_{k}"]).groupby("gp_bucket").e.std())
        m[f"sig_{k}"] = m.gp_bucket.map(sig)
        m[f"p_{k}"] = norm.cdf(m[f"pm_{k}"] / m[f"sig_{k}"])
    meta = dict(prior_coefs=coefs, ff_margin={c: round(float(v), 4) for c, v in zip(mcols, rm["b"])},
                ff_total={c: round(float(v), 4) for c, v in zip(tcols, rt["b"])})
    keep = [c for c in m.columns if not c.startswith(("x_", "efg_", "tov_", "orb_", "ftr_", "tpar_", "tp_",
                                                       "twop_", "ft_"))]
    m[keep].to_parquet(OUT / ("final_holdout.parquet" if include_holdout else "final.parquet"), index=False)
    (OUT / "final_meta.json").write_text(json.dumps(meta, default=float, indent=1))
    return m[keep]


MODELS = [("ae01", "ae-0.1 (Phase 1)"), ("prior", "+ roster/transfer/recruit priors"),
          ("ff", "+ priors + four-factor matchup layer")]


def metric_rows(d: pd.DataFrame, label: str) -> list[dict]:
    out = []
    y = d.home_win.values
    for k, name in MODELS:
        e = d.margin - d[f"pm_{k}"]
        out.append(dict(split=label, model=name, n=len(d), margin_rmse=float(np.sqrt((e ** 2).mean())),
                        margin_mae=float(e.abs().mean()), su_acc=float(((d[f"pm_{k}"] > 0) == (y == 1)).mean()),
                        brier=M.brier(d[f"p_{k}"], y), log_loss=M.log_loss(d[f"p_{k}"], y),
                        total_mae=float((d.total_pts - d[f"pt_{k}"]).abs().mean())))
    return out


def market_rows(x: pd.DataFrame, label: str) -> list[dict]:
    """x: games with a named-book closing spread (`spread`, home line)."""
    from .evaluate import ats_sim
    out = []
    mk = -x.spread
    for k, name in MODELS:
        d = x.assign(mkt_margin=mk, pred_margin=x[f"pm_{k}"], sigma_m=x[f"sig_{k}"])
        X = np.c_[np.ones(len(d)), d.mkt_margin, d.pred_margin - d.mkt_margin]
        beta, *_ = np.linalg.lstsq(X, d.margin.values, rcond=None)
        res = d.margin.values - X @ beta
        se = np.sqrt(np.diag(np.linalg.inv(X.T @ X)) * res.var(ddof=3))
        a = ats_sim(d.assign(home_spread_odds=np.nan, away_spread_odds=np.nan))
        clv = np.nan
        if "spread_open" in d and d.spread_open.notna().mean() > 0.5:
            o = d[d.spread_open.notna()]
            ph, pp, pa = M.cover_probs(o.pred_margin, o.spread_open, o.sigma_m)
            side_home = ph > pa
            # + = the line moved toward the model's side between open and close
            clv = float(np.mean(np.where(side_home, o.spread_open - o.spread, o.spread - o.spread_open)))
        out.append(dict(split=label, model=name, n=len(d), clv_vs_open=clv,
                        line_rmse=float(np.sqrt(((d.margin - d.mkt_margin) ** 2).mean())),
                        model_rmse=float(np.sqrt(((d.margin - d.pred_margin) ** 2).mean())),
                        c=float(beta[2]), c_se=float(se[2]),
                        ats=f"{a['wins']}-{a['losses']}-{a['pushes']}", ats_pct=a["win_pct"], roi=a["roi"],
                        roi_ci=f"{a['roi_95ci'][0]:+.1%} to {a['roi_95ci'][1]:+.1%}"))
    return out


def blend_oos(train: pd.DataFrame, test: pd.DataFrame) -> list[dict]:
    """margin = a + b*line + c*(model - line), fit on train, RMSE on test."""
    out = []
    for k, name in MODELS:
        def X(d):
            return np.c_[np.ones(len(d)), -d.spread, d[f"pm_{k}"] + d.spread]
        beta, *_ = np.linalg.lstsq(X(train), train.margin.values, rcond=None)
        out.append(dict(model=name, c_train=float(beta[2]),
                        rmse_line=float(np.sqrt(((test.margin + test.spread) ** 2).mean())),
                        rmse_blend=float(np.sqrt(((test.margin - X(test) @ beta) ** 2).mean()))))
    return out


def report(with_lines: bool) -> str:
    from .evaluate import md
    f = OUT / "final.parquet"
    m = pd.read_parquet(f) if f.exists() else final_predictions()
    m = m[m.d1_game]
    L = ["# Phase 3: preseason priors + four-factor matchup layer", "",
         "Walk-forward. Every prediction for date d uses only games before d; the preseason model for "
         "season s is fitted on seasons before s. Prior shrinkage k and the feature blocks were chosen on "
         "tune seasons 2018–22; the four-factor layer is fitted on 2018–22 residuals. Validation 2023–25 "
         "and the current season were not used for any choice. The 2026 holdout is not in this report.", "",
         "## Accuracy (D1 vs D1)", ""]
    rows = []
    rows += metric_rows(m[m.season.isin(FF_TRAIN)], "tune 2018–22 (in-sample for the FF layer)")
    rows += metric_rows(m[m.season.isin(C.VALIDATION_SEASONS)], "validation 2023–25")
    rows += metric_rows(m[m.season == C.CURRENT_SEASON], f"current {C.CURRENT_SEASON} to date")
    L += [md(pd.DataFrame(rows)), ""]
    v = m[m.season.isin(C.VALIDATION_SEASONS)].copy()
    v["when"] = np.select([np.minimum(v.h_gp, v.a_gp) <= 2, np.minimum(v.h_gp, v.a_gp) <= 5,
                           v.game_date.dt.month.isin([11, 12])],
                          ["a: 0–2 games played", "b: 3–5 games played", "c: rest of Nov–Dec"], "d: Jan–Apr")
    t = []
    for w, x in v.groupby("when"):
        r = dict(when=w[3:], n=len(x))
        for k, name in MODELS:
            r[f"RMSE {k}"] = float(np.sqrt(((x.margin - x[f"pm_{k}"]) ** 2).mean()))
        t.append(r)
    L += ["## Validation 2023–25 by point in the season (margin RMSE)", "", md(pd.DataFrame(t)), ""]
    by = []
    for s, x in v.groupby("season"):
        r = dict(season=s, n=len(x))
        for k, name in MODELS:
            r[f"RMSE {k}"] = float(np.sqrt(((x.margin - x[f"pm_{k}"]) ** 2).mean()))
        by.append(r)
    L += ["### By season", "", md(pd.DataFrame(by)), ""]
    L += ["### Calibration, validation, full model", "", md(M.calibration_table(v.p_ff, v.home_win)), ""]
    if with_lines:
        from .cbbd import _key, fetch_lines, join_to_espn
        key = _key()
        games = load_games()
        allx = []
        for s in C.VALIDATION_SEASONS + [C.CURRENT_SEASON]:
            ln = fetch_lines(s, key)
            if ln.empty:
                continue
            j_, _ = join_to_espn(ln, games[games.season == s])
            allx.append(j_)
        x = pd.concat(allx, ignore_index=True)
        x = x[x.provider.isin(["ESPN BET", "Draft Kings", "DraftKings"]) & x.spread.notna()]
        x = x.drop_duplicates(["game_id", "provider"])[["game_id", "provider", "spread", "spread_open"]]
        x = m.merge(x, on="game_id")
        L += ["## Against named-book closing lines (CBBD; simulated, no wagers)", "",
              "`c` = weight on (model − line) in margin = a + b·line + c·(model − line), fit on that cell. "
              "c ≈ 0 means no information beyond the line. ATS assumes −110 (CBBD carries no spread prices) "
              "and bets the side with positive EV on every game.", ""]
        mr = []
        for (s, pv), g in x.groupby(["season", "provider"]):
            if len(g) >= 300:
                mr += market_rows(g, f"{s} {pv}")
        eb = x[(x.provider == "ESPN BET") & x.season.isin(C.VALIDATION_SEASONS)]
        mr += market_rows(eb, "2023–25 ESPN BET pooled")
        L += [md(pd.DataFrame(mr)), ""]
        tr_, te_ = eb[eb.season.isin([2023, 2024])], eb[eb.season == 2025]
        if len(tr_) and len(te_):
            L += ["### Blend fit on 2023–24 ESPN BET, scored on 2025", "", md(pd.DataFrame(blend_oos(tr_, te_)), 4), ""]
        early = eb[np.minimum(eb.h_gp, eb.a_gp) <= 5]
        if len(early) > 300:
            L += ["### Early season only (≤5 games played), 2023–25 ESPN BET", "",
                  md(pd.DataFrame(market_rows(early, "early 2023–25"))), ""]
    return "\n".join(L)


def holdout_report() -> str:
    """ONE run on the 2026 holdout for the frozen ae-0.2 candidate."""
    from .cbbd import _key, fetch_lines, join_to_espn
    from .evaluate import md
    m = final_predictions(seasons=FF_TRAIN, include_holdout=True)
    m = m[m.d1_game & (m.season == 2026)]
    L = ["# Holdout 2025–26 — ae-0.2 candidate (single run)", "",
         "Frozen before this run: prior blocks, k = 0.5, four-factor layer (alpha 100, fit on 2018–22). "
         "Nothing below was used to choose anything. All betting figures simulated; no wagers.", "",
         md(pd.DataFrame(metric_rows(m, "holdout 2026"))), ""]
    m = m.copy()
    m["early"] = np.minimum(m.h_gp, m.a_gp) <= 5
    L += ["Early season (≤5 games played):", "", md(pd.DataFrame(metric_rows(m[m.early], "holdout 2026, early"))), ""]
    ln = fetch_lines(2026, _key())
    j_, _ = join_to_espn(ln, load_games().query("season == 2026"))
    x = j_[j_.provider.isin(["Draft Kings", "DraftKings"]) & j_.spread.notna()].drop_duplicates("game_id")
    x = m.merge(x[["game_id", "spread", "spread_open"]], on="game_id")
    L += ["## Against the DraftKings close (CBBD)", "", md(pd.DataFrame(market_rows(x, "2026 DraftKings"))), "",
          md(pd.DataFrame(market_rows(x[x.early], "2026 DraftKings, early"))), ""]
    return "\n".join(L)


def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["final", "report", "holdout"])
    ap.add_argument("--lines", action="store_true")
    a = ap.parse_args()
    if a.cmd == "holdout":
        out = C.REPORTS / "holdout_ae-0.2.md"
        if out.exists():
            raise SystemExit(f"{out} exists: the holdout has been run for ae-0.2. Bump the version.")
        txt = holdout_report()
        out.write_text(txt + "\n")
        print(txt)
        return
    if a.cmd == "final":
        final_predictions()
    txt = report(a.lines)
    if a.lines:  # the committed report always carries the market section
        C.REPORTS.mkdir(exist_ok=True)
        (C.REPORTS / "phase3_validation.md").write_text(txt + "\n")
    print(txt)


if __name__ == "__main__":
    import sys
    experiments() if sys.argv[1:2] == ["run"] else cli()
