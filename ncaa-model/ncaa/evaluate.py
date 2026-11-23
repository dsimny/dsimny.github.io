"""Full evaluation: model vs baselines vs market, per chronological split.

    python -m ncaa.evaluate predictions   # walk-forward all seasons -> data/predictions.parquet
    python -m ncaa.evaluate report        # tune + validation report
    python -m ncaa.evaluate holdout       # holdout report (run ONCE per model version)

Every number here is a backtest on recorded results and, where noted,
recorded closing (or opening) lines. Simulated ATS/ROI figures are
HYPOTHETICAL: no money was wagered.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import config as C
from . import elo, metrics as M
from .backtest import attach, load_params, sigma_for, walk_forward
from .ingest import load_games, load_odds

PRED = C.DATA / "predictions.parquet"


# ---------------------------------------------------------------- build
def build_predictions():
    p, j = load_params()
    g = load_games()
    seasons = list(range(min(C.TUNE_SEASONS), C.CURRENT_SEASON + 1))
    m = attach(g, walk_forward(g, seasons, p))
    ep = j["elo"]
    e = elo.run(g, elo.EloParams(k=ep["k"], carry=ep["carry"]))
    m = m.merge(e, on="game_id", how="left")
    m["elo_margin"] = ep["pts_per_elo"] * m.elo_diff
    gp = np.minimum(m.h_gp, m.a_gp)
    m["sigma_m"] = sigma_for(gp, j["sigma"]["margin"])
    m["sigma_t"] = sigma_for(gp, j["sigma"]["total"])
    m["p_home"] = norm.cdf(m.pred_margin / m.sigma_m)
    m.to_parquet(PRED, index=False)
    print(f"wrote {PRED}: {len(m)} games")
    return m


def market_frame(odds: pd.DataFrame) -> pd.DataFrame:
    """One real-book line per game. Providers that are not books (numberfire
    = a projection model; teamrankings = no spread) are dropped."""
    o = odds[odds.provider.isin(C.REAL_BOOK_PROVIDERS) & odds.spread.notna()].copy()
    o = o.sort_values("provider").drop_duplicates("game_id")
    o["mkt_margin"] = -o.spread
    ok = o.home_ml.notna() & o.away_ml.notna() & (o.home_ml.abs() >= 100) & (o.away_ml.abs() >= 100)
    ph, _ = M.devig_two_way(o.home_ml.where(ok, -110), o.away_ml.where(ok, -110))
    o["mkt_p_ml"] = np.where(ok, ph, np.nan)
    return o


# ---------------------------------------------------------------- tables
def _fmt(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def model_table(d: pd.DataFrame, j: dict, mkt_sigma: float | None) -> pd.DataFrame:
    """Same games, every model. d must be D1-vs-D1."""
    y = d.home_win.values
    tune_home_rate = j.get("home_rate", 0.65)
    rows = []

    def add(name, p, margin, total=None):
        r = dict(model=name, n=len(d), su_acc=float(((p > 0.5) == (y == 1)).mean()) if p is not None else np.nan,
                 brier=M.brier(p, y) if p is not None else np.nan,
                 log_loss=M.log_loss(p, y) if p is not None else np.nan,
                 margin_mae=float((d.margin - margin).abs().mean()) if margin is not None else np.nan,
                 margin_rmse=float(np.sqrt(((d.margin - margin) ** 2).mean())) if margin is not None else np.nan,
                 total_mae=float((d.total_pts - total).abs().mean()) if total is not None else np.nan)
        rows.append(r)

    hp = np.where(d.neutral_site, 0.5, tune_home_rate)
    add("home-team baseline", hp, np.where(d.neutral_site, 0.0, j.get("home_margin", 3.2)))
    add("Elo", d.elo_p_home.values, d.elo_margin.values)
    add("adj. efficiency ae-0.1", d.p_home.values, d.pred_margin.values, d.pred_total.values)
    if "mkt_margin" in d and d.mkt_margin.notna().all():
        pm = norm.cdf(d.mkt_margin / mkt_sigma)
        pml = d.mkt_p_ml.where(d.mkt_p_ml.notna(), pm)
        add("market (closing line)", pml.values, d.mkt_margin.values,
            d.total.values if d.total.notna().all() else None)
    return pd.DataFrame(rows)


def md(df: pd.DataFrame, nd=3) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join((str(int(v)) if float(v).is_integer() and abs(v) >= 1 and c in ("n", "games") else _fmt(v, nd))
                                     if isinstance(v, float) else str(v) for c, v in zip(cols, r)) + " |")
    return "\n".join(out)


def ats_sim(d: pd.DataFrame, line_col="spread", odds_h="home_spread_odds", odds_a="away_spread_odds",
            min_ev=0.0) -> dict:
    """Pre-declared rule (fixed before any holdout): bet the side whose
    model EV at the offered price exceeds `min_ev`; 1u flat. Missing price ->
    -110 (flagged in output). Push = stake returned."""
    x = d[d[line_col].notna()].copy()
    ph, pp, pa = M.cover_probs(x.pred_margin, x[line_col], x.sigma_m)
    oh = x[odds_h].where(x[odds_h].notna() & (x[odds_h].abs() >= 100), -110.0) if odds_h in x else pd.Series(-110.0, index=x.index)
    oa = x[odds_a].where(x[odds_a].notna() & (x[odds_a].abs() >= 100), -110.0) if odds_a in x else pd.Series(-110.0, index=x.index)
    priced = (x[odds_h].notna() & x[odds_a].notna()).mean() if odds_h in x else 0.0
    evh = M.ev_per_unit(ph, M.american_to_decimal(oh), pp)
    eva = M.ev_per_unit(pa, M.american_to_decimal(oa), pp)
    bet_home = (evh > eva) & (evh > min_ev)
    bet_away = (eva >= evh) & (eva > min_ev)
    res = x.margin + x[line_col]  # >0 home covered
    pnl = np.where(bet_home, np.where(res > 0, M.american_to_decimal(oh) - 1, np.where(res == 0, 0, -1)), 0.0)
    pnl = pnl + np.where(bet_away, np.where(res < 0, M.american_to_decimal(oa) - 1, np.where(res == 0, 0, -1)), 0.0)
    bets = bet_home | bet_away
    won = (bet_home & (res > 0)) | (bet_away & (res < 0))
    push = bets & (res == 0)
    nb = int(bets.sum())
    ordr = np.argsort(x.game_date.values, kind="stable")
    pb = pnl[ordr][np.asarray(bets)[ordr]]
    lo, hi = M.bootstrap_ci(pb) if nb else (np.nan, np.nan)
    return dict(games=len(x), bets=nb, wins=int(won.sum()), losses=int(nb - won.sum() - push.sum()),
                pushes=int(push.sum()), win_pct=float(won.sum() / max(1, nb - push.sum())),
                units=float(pnl.sum()), roi=float(pnl.sum() / max(1, nb)), roi_95ci=(lo, hi),
                max_drawdown=M.max_drawdown(pb), priced_share=float(priced))


def incremental_value(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    """Does the model add information beyond the closing spread?
    Fit  margin = a + b*market + c*(model - market)  on train; c ~ 0 => no.
    Score market-only vs blended out of sample on test."""
    def X(d):
        return np.c_[np.ones(len(d)), d.mkt_margin, d.pred_margin - d.mkt_margin]
    Xt, yt = X(train), train.margin.values
    beta, *_ = np.linalg.lstsq(Xt, yt, rcond=None)
    resid = yt - Xt @ beta
    cov = np.linalg.inv(Xt.T @ Xt) * resid.var(ddof=3)
    se = np.sqrt(np.diag(cov))
    out = dict(train_n=len(train), a=float(beta[0]), b=float(beta[1]), c=float(beta[2]), c_se=float(se[2]),
               train_sigma=float(resid.std()))
    if len(test):
        mk = test.mkt_margin.values
        bl = X(test) @ beta
        out.update(test_n=len(test), rmse_market=float(np.sqrt(((test.margin - mk) ** 2).mean())),
                   rmse_blend=float(np.sqrt(((test.margin - bl) ** 2).mean())),
                   rmse_model=float(np.sqrt(((test.margin - test.pred_margin) ** 2).mean())))
    return out


def section(title, m, j, odds_df, mkt_sigma, train_mkt=None, label=""):
    d = m[m.d1_game & m.season.isin(title[1])].copy()
    L = [f"## {title[0]} — seasons {title[1][0]}–{title[1][-1]}", ""]
    L.append("### All D1-vs-D1 games (every model on the same games)")
    L.append(md(model_table(d, j, mkt_sigma)))
    L.append("")
    by = d.groupby("season").apply(lambda x: pd.Series(dict(
        n=len(x), mae=float((x.margin - x.pred_margin).abs().mean()),
        rmse=float(np.sqrt(((x.margin - x.pred_margin) ** 2).mean())),
        su_acc=float(((x.pred_margin > 0) == (x.margin > 0)).mean()),
        brier=M.brier(x.p_home, x.home_win), total_mae=float((x.total_pts - x.pred_total).abs().mean())))).reset_index()
    by["n"] = by.n.astype(int)
    L += ["### ae-0.1 by season", md(by), ""]
    d["month"] = d.game_date.dt.month_name().str[:3]
    bm = d.groupby("month", sort=False).apply(lambda x: pd.Series(dict(
        n=int(len(x)), mae=float((x.margin - x.pred_margin).abs().mean()),
        bias=float((x.margin - x.pred_margin).mean()), brier=M.brier(x.p_home, x.home_win)))).reset_index()
    L += ["### ae-0.1 by month (early season = thin data)", md(bm), ""]
    conf = j.get("conf_names", {})
    cg = d[d.conference_competition == True].copy()  # noqa: E712
    cg["conf"] = cg.home_conference_id.map(lambda c: conf.get(str(int(c)), str(int(c))) if pd.notna(c) else "?")
    bc = cg.groupby("conf").apply(lambda x: pd.Series(dict(n=int(len(x)), mae=float((x.margin - x.pred_margin).abs().mean()),
                                                         brier=M.brier(x.p_home, x.home_win)))).reset_index()
    bc = bc[bc.n >= 100].sort_values("mae")
    L += ["### ae-0.1 by conference (conference games only, n ≥ 100)", md(bc), ""]
    ct = M.calibration_table(d.p_home, d.home_win)
    L += ["### Calibration (home win probability)", md(ct), ""]
    d["conf_bucket"] = pd.cut((d.p_home - 0.5).abs() + 0.5, [0.5, 0.6, 0.7, 0.8, 0.9, 1.0], include_lowest=True).astype(str)
    cb = d.groupby("conf_bucket").apply(lambda x: pd.Series(dict(
        n=int(len(x)), favourite_won=float(((x.p_home > 0.5) == (x.home_win == 1)).mean()),
        mean_conf=float(((x.p_home - 0.5).abs() + 0.5).mean())))).reset_index()
    L += ["### By prediction confidence", md(cb), ""]

    # market
    dm = d.merge(odds_df, on="game_id", how="inner", suffixes=("", "_o"))
    if len(dm):
        L += [f"### Against the market ({len(dm)} games with a real-book line; providers: "
              f"{', '.join(sorted(dm.provider.unique()))})", ""]
        L.append(md(model_table(dm, j, mkt_sigma)))
        L.append("")
        tot = dm[dm.total.notna()]
        if len(tot):
            L.append(f"Totals: ae-0.1 MAE {(tot.total_pts - tot.pred_total).abs().mean():.2f} vs market total MAE "
                     f"{(tot.total_pts - tot.total).abs().mean():.2f} (n={len(tot)}).")
            L.append("")
        if train_mkt is not None:
            iv = incremental_value(train_mkt, dm)
            L.append("**Incremental value test** (fit on tune-season lines, scored here): "
                     f"margin = a + {iv['b']:.3f}·market + c·(model − market), c = {iv['c']:.3f} ± {iv['c_se']:.3f} (SE, "
                     f"train n={iv['train_n']}). Out of sample RMSE: market {iv.get('rmse_market', np.nan):.3f}, "
                     f"blend {iv.get('rmse_blend', np.nan):.3f}, model alone {iv.get('rmse_model', np.nan):.3f}.")
            L.append("")
            L.append("By line provider (a 'line' that is not a true close would let the model look better than it is):")
            L.append("")
            L.append("| provider | c fitted on tune lines of this provider (±SE) | n here | RMSE market | RMSE blend |")
            L.append("|---|---|---|---|---|")
            for pv, x in dm.groupby("provider"):
                tr = train_mkt[train_mkt.provider == pv]
                if len(tr) < 500 or len(x) < 200:
                    continue
                ivp = incremental_value(tr, x)
                L.append(f"| {pv} | {ivp['c']:.3f} ± {ivp['c_se']:.3f} | {len(x)} | {ivp['rmse_market']:.3f} | {ivp['rmse_blend']:.3f} |")
            L.append("")
            # Pre-declared: the blend's ATS, betting the same closing line. The
            # blend is a linear map fitted on TUNE lines only; its residual SD
            # (tune) replaces the model sigma.
            bt = dm.copy()
            bt["pred_margin"] = iv["a"] + iv["b"] * bt.mkt_margin + iv["c"] * (dm.pred_margin - dm.mkt_margin)
            bt["sigma_m"] = iv["train_sigma"]
            ab = ats_sim(bt)
            L.append("**Simulated ATS, market+model blend, closing line** (HYPOTHETICAL): "
                     f"{ab['wins']}-{ab['losses']}-{ab['pushes']} ({ab['win_pct']:.1%}) on {ab['bets']} bets, "
                     f"{ab['units']:+.1f}u, ROI {ab['roi']:+.1%} (95% CI {ab['roi_95ci'][0]:+.1%} to {ab['roi_95ci'][1]:+.1%}).")
            bt3 = bt[(bt.pred_margin - bt.mkt_margin).abs() >= 1.5]
            if len(bt3):
                a3 = ats_sim(bt3)
                L.append(f"Of those, games where the blend differs from the line by ≥1.5 pts: "
                         f"{a3['wins']}-{a3['losses']}-{a3['pushes']} ({a3['win_pct']:.1%}), ROI {a3['roi']:+.1%} "
                         f"(95% CI {a3['roi_95ci'][0]:+.1%} to {a3['roi_95ci'][1]:+.1%}), n={a3['bets']}.")
            L.append("")
        a = ats_sim(dm)
        L.append("**Simulated ATS, closing line** (HYPOTHETICAL, 1u flat, rule: bet the positive-EV side; "
                 "betting the CLOSE is the hardest possible test): "
                 f"{a['wins']}-{a['losses']}-{a['pushes']} ({a['win_pct']:.1%}), {a['units']:+.1f}u, ROI {a['roi']:+.1%} "
                 f"(95% CI {a['roi_95ci'][0]:+.1%} to {a['roi_95ci'][1]:+.1%}), max drawdown {a['max_drawdown']:.1f}u; "
                 f"real prices on {a['priced_share']:.0%} of games, -110 assumed on the rest.")
        L.append("")
        op = dm[dm.spread_open.notna()]
        if len(op):
            a2 = ats_sim(op, "spread_open", "home_spread_odds_open", "away_spread_odds_open")
            ph, pp, pa = M.cover_probs(op.pred_margin, op.spread_open, op.sigma_m)
            side_home = ph > pa
            # CLV in points: + = the line moved toward our side after we "bet" the open.
            clv = np.where(side_home, op.spread_open - op.spread, op.spread - op.spread_open)
            L.append("**Simulated ATS, OPENING line** (DraftKings open; HYPOTHETICAL): "
                     f"{a2['wins']}-{a2['losses']}-{a2['pushes']} ({a2['win_pct']:.1%}), {a2['units']:+.1f}u, ROI {a2['roi']:+.1%} "
                     f"(95% CI {a2['roi_95ci'][0]:+.1%} to {a2['roi_95ci'][1]:+.1%}). "
                     f"CLV on the model's side vs close: mean {np.mean(clv):+.2f} pts, beat the close "
                     f"{np.mean(clv > 0):.1%}, matched {np.mean(clv == 0):.1%}, n={len(op)}.")
            L.append("")
    return "\n".join(L)


def run_report(which: str):
    p, j = load_params()
    m = pd.read_parquet(PRED)
    g = load_games()
    odds = market_frame(load_odds())
    tune_d = m[m.d1_game & m.season.isin(C.TUNE_SEASONS)]
    j["home_rate"] = float((tune_d[~tune_d.neutral_site].home_win).mean())
    j["home_margin"] = float(tune_d[~tune_d.neutral_site].margin.mean())
    tm = tune_d.merge(odds, on="game_id")
    mkt_sigma = float((tm.margin - tm.mkt_margin).std())
    sch = pd.concat([pd.read_parquet(C.RAW / f"mbb_schedule_{y}.parquet",
                                     columns=["groups_id", "groups_short_name", "groups_is_conference"])
                     for y in (2024, 2025, 2026)])
    sch = sch[sch.groups_is_conference == True].dropna().drop_duplicates("groups_id")  # noqa: E712
    j["conf_names"] = {str(int(a)): b for a, b in zip(sch.groups_id, sch.groups_short_name)}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    head = [f"# Backtest report — {which} — model {j['model_version']}", "",
            f"Generated {now}. Walk-forward: each game predicted from ratings fitted only on games before its date. "
            f"Hyper-parameters tuned on {C.TUNE_SEASONS[0]}–{C.TUNE_SEASONS[-1]} only: `{json.dumps(j['params'], default=float)}`. "
            f"Win probability = Φ(margin / σ), σ by games played (tune seasons): {json.dumps({k: round(v, 2) for k, v in j['sigma']['margin'].items()})}. "
            f"Market σ (closing spread residual, tune) = {mkt_sigma:.2f}.", "",
            "All betting figures are **simulated on recorded lines — no wagers were placed**. "
            "Closing lines come from ESPN's pickcenter (single book per game; consensus/Caesars before 2024, "
            "DraftKings from 2025-26); ESPN gives no capture timestamp for pre-2025 lines. "
            "No lines exist in the source for 2023-24 and 2024-25.", ""]
    if which == "report":
        body = [section(("Tune", C.TUNE_SEASONS), m, j, odds, mkt_sigma, None),
                section(("Validation", C.VALIDATION_SEASONS), m, j, odds, mkt_sigma, tm)]
        out = C.REPORTS / f"backtest_{j['model_version']}.md"
    else:
        body = [section(("HOLDOUT (single run)", C.HOLDOUT_SEASONS), m, j, odds, mkt_sigma, tm),
                section(("Current season to date (forward, not a holdout)", [C.CURRENT_SEASON]), m, j, odds, mkt_sigma, tm)]
        out = C.REPORTS / f"holdout_{j['model_version']}.md"
    txt = "\n".join(head + body)
    out.write_text(txt)
    print(txt)
    print("wrote", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["predictions", "report", "holdout"])
    a = ap.parse_args()
    if a.cmd == "predictions":
        build_predictions()
    else:
        if a.cmd == "holdout":
            done = C.REPORTS / f"holdout_{load_params()[1]['model_version']}.md"
            if done.exists() and "--force" not in sys.argv:
                sys.exit(f"{done} exists: the holdout has been run for this model version. Bump the version.")
        run_report(a.cmd)
