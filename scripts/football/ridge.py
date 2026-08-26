#!/usr/bin/env python3
"""
Open Ledger Sports — opponent-adjusted ridge efficiency (fb-v0.1).

The second rating family in the pre-registration, and the one that answers a
question Elo cannot: Elo knows only who won. This knows how efficiently a team
moved the ball, and against whom.

STAGE 1, the ratings. One row per team-game:

    epa_per_play  ~  off_effect[team] + def_effect[opponent] + home

fitted by weighted ridge. Weights are possession-weighted (plays in the game)
times an exponential recency decay, so a team is judged mostly on recent
football without throwing away last season entirely. The off and def blocks are
re-centred to mean zero after each solve, because the model is otherwise only
identified up to a constant shift between offence and defence.

STAGE 2, turning ratings into a margin. A single linear map fitted on the TUNE
seasons only:  margin ~ a + b * efficiency_edge. Two parameters, deliberately -
the ratings are supposed to be doing the work.

AS-OF, and the conservative approximation. Refitting a ridge for every one of
4,000 games would be honest but pointless; refitting per week is standard. The
subtlety is which instant "per week" means. This uses the EARLIEST T-24 in the
week, so every game in that week is predicted from strictly less information
than it could legitimately have had. That direction matters: the approximation
can only ever understate the model, never leak. A refit at each game's own T-24
would be tighter and is a candidate for fb-v0.2.

MARKET-BLIND. Reads team_game_efficiency.csv and games.csv. Both are football
facts; the market columns were physically removed at ingest.

Run: python scripts/football/ridge.py [--quick]
"""
import argparse, csv, io, itertools, json, math, os, sys
from datetime import timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asof                                        # noqa: E402
from teams import canonical                        # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
EFF = os.path.join(FB, "team_game_efficiency.csv")
OUT = os.path.join(FB, "ridge_fit.json")

BURN_IN = list(range(2010, 2015))
TUNE = list(range(2015, 2022))
VALIDATE = list(range(2022, 2025))

# --- THE PRE-REGISTERED GRID (declared before it was run) -------------------
GRID = {
    "lam":       [1.0, 3.0, 10.0, 30.0, 100.0],   # ridge penalty
    "half_life": [90, 180, 365],                  # recency decay, days
}
# 5 x 3 = 15 combinations.

MIN_TRAIN = 200          # team-games before the ridge is trusted at all


def load_efficiency():
    with io.open(EFF, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build(games, eff_rows):
    """Attach each team-game's efficiency to its game's timing."""
    by_id = {g["game_id"]: g for g in games}
    obs = []
    for r in eff_rows:
        g = by_id.get(r["game_id"])
        if g is None or not r["epa_per_play"]:
            continue
        obs.append({
            "game_id": r["game_id"],
            "team": canonical(r["team"]),
            "opp": canonical(r["opponent"]),
            "is_home": r["is_home"] == "1",
            "y": float(r["epa_per_play"]),
            "w_plays": int(r["n_plays"]),
            "result_at": g["_result_at"],
            "season": g["_season"],
        })
    return obs


def solve(obs, teams, lam, half_life, asof_ts):
    """Weighted ridge on everything available at asof_ts. Returns off/def/home."""
    idx = {t: i for i, t in enumerate(teams)}
    n_t = len(teams)
    rows, ys, ws = [], [], []
    hl = float(half_life)
    for o in obs:
        age = (asof_ts - o["result_at"]).total_seconds() / 86400.0
        x = np.zeros(2 * n_t + 1)
        x[idx[o["team"]]] = 1.0                  # offence
        x[n_t + idx[o["opp"]]] = 1.0             # defence faced
        x[-1] = 1.0 if o["is_home"] else 0.0
        rows.append(x)
        ys.append(o["y"])
        ws.append(o["w_plays"] * (0.5 ** (age / hl)))
    X = np.array(rows)
    y = np.array(ys)
    w = np.array(ws)

    XtW = X.T * w
    A = XtW @ X
    # Penalise the team effects, not the home term: home field is a real
    # quantity we want estimated, not shrunk toward zero.
    P = np.eye(A.shape[0]) * lam
    P[-1, -1] = 0.0
    beta = np.linalg.solve(A + P, XtW @ y)

    off = beta[:n_t]
    dfn = beta[n_t:2 * n_t]
    # Re-centre: the model is identified only up to a shift between the blocks.
    off = off - off.mean()
    dfn = dfn - dfn.mean()
    return {t: float(off[idx[t]]) for t in teams}, \
           {t: float(dfn[idx[t]]) for t in teams}, float(beta[-1])


def walk_forward(games, obs, params, seasons_to_score):
    """Refit weekly at the earliest T-24 in each week; predict that week."""
    teams = sorted({o["team"] for o in obs} | {o["opp"] for o in obs})
    wanted = set(seasons_to_score)

    weeks = {}
    for g in games:
        weeks.setdefault((g["_season"], int(g["week"])), []).append(g)

    preds = []
    for key in sorted(weeks):
        wk_games = weeks[key]
        asof_ts = min(g["_t24"] for g in wk_games)
        train = [o for o in obs if o["result_at"] <= asof_ts]
        if len(train) < MIN_TRAIN:
            continue
        off, dfn, home = solve(train, teams, params["lam"],
                               params["half_life"], asof_ts)
        for g in wk_games:
            h, a = canonical(g["home_team"]), canonical(g["away_team"])
            # The fit is ADDITIVE: y = off[team] + def[opponent] + home, so a
            # good defence carries a NEGATIVE def effect (it suppresses the
            # offence it faces). Expected efficiency for the home side is
            # therefore off[h] + def[a] + home, and the edge is that minus the
            # away side's. Writing `off[h] - def[a]` here inverts every defence
            # in the league, which costs about 0.03 log loss and looks merely
            # mediocre rather than broken - so it is spelled out rather than
            # left to be re-derived.
            edge = (off[h] + dfn[a] + home) - (off[a] + dfn[h])
            if g["_season"] in wanted:
                preds.append({"edge": edge, "margin": g["_margin"],
                              "season": g["_season"], "game_id": g["game_id"]})
    return preds, teams


def fit_stage2(preds):
    """margin ~ a + b*edge, least squares. Two parameters, on purpose."""
    x = np.array([p["edge"] for p in preds])
    y = np.array([float(p["margin"]) for p in preds])
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    return float(a), float(b), float(resid.std(ddof=2))


def evaluate(preds, a, b, sd):
    """Margin RMSE plus a win probability, so this is comparable with Elo."""
    x = np.array([p["edge"] for p in preds])
    y = np.array([float(p["margin"]) for p in preds])
    pred = a + b * x
    rmse = float(np.sqrt(((y - pred) ** 2).mean()))
    mae = float(np.abs(y - pred).mean())

    # P(home wins) via a normal around the predicted margin. Ties -> y = 0.5.
    from math import erf
    p_home = np.array([0.5 * (1.0 + erf(m / (sd * math.sqrt(2.0)))) for m in pred])
    yb = np.array([1.0 if m > 0 else (0.0 if m < 0 else 0.5) for m in y])
    eps = 1e-15
    ll = float(-np.mean(yb * np.log(np.clip(p_home, eps, 1)) +
                        (1 - yb) * np.log(np.clip(1 - p_home, eps, 1))))
    brier = float(np.mean((p_home - yb) ** 2))
    acc = float(np.mean([1.0 if (p > 0.5) == (t > 0.5) else (0.5 if t == 0.5 else 0.0)
                         for p, t in zip(p_home, yb)]))
    per_season = {}
    for s in sorted({p["season"] for p in preds}):
        m = np.array([i for i, p in enumerate(preds) if p["season"] == s])
        per_season[s] = round(float(np.sqrt(((y[m] - pred[m]) ** 2).mean())), 3)
    return {"margin_rmse": round(rmse, 4), "margin_mae": round(mae, 4),
            "log_loss": round(ll, 5), "brier": round(brier, 5),
            "accuracy": round(acc, 4), "n": len(preds),
            "per_season_rmse": per_season}


def margin_baselines(preds):
    y = np.array([float(p["margin"]) for p in preds])
    out = {}
    for name, pred in (("predict_zero", np.zeros_like(y)),
                       ("predict_mean_hfa", np.full_like(y, y.mean()))):
        out[name] = {"margin_rmse": round(float(np.sqrt(((y - pred) ** 2).mean())), 4),
                     "value": round(float(pred[0]), 3)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="lam grid only, half_life=180")
    args = ap.parse_args()

    seasons = BURN_IN + TUNE + VALIDATE
    games = asof.load_games(seasons=seasons, purpose="ridge fit")
    obs = build(games, load_efficiency())
    print(f"loaded {len(games):,} games, {len(obs):,} team-game efficiencies")
    print(f"holdout {asof.HOLDOUT} not loaded (prereg frozen: "
          f"{asof.frozen_date() or 'NOT YET'})\n")

    grid = dict(GRID)
    if args.quick:
        grid = {"lam": GRID["lam"], "half_life": [180]}
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    print(f"scoring {len(combos)} pre-registered combinations on TUNE")

    results = []
    for i, params in enumerate(combos, 1):
        preds, _ = walk_forward(games, obs, params, TUNE)
        a, b, sd = fit_stage2(preds)
        m = evaluate(preds, a, b, sd)
        results.append({"params": params, "stage2": {"a": round(a, 4), "b": round(b, 4),
                                                     "resid_sd": round(sd, 4)},
                        "tune": {k: v for k, v in m.items() if k != "per_season_rmse"}})
        print(f"  {i}/{len(combos)}  lam={params['lam']:<6} hl={params['half_life']:<4} "
              f"rmse={m['margin_rmse']:.4f}  ll={m['log_loss']:.5f}")

    results.sort(key=lambda r: r["tune"]["margin_rmse"])
    best = results[0]
    print(f"\nselected on TUNE: {best['params']}  "
          f"(stage 2: margin = {best['stage2']['a']} + "
          f"{best['stage2']['b']} x edge)")

    # The honest pass: stage 2 refitted on TUNE only, applied to VALIDATE once.
    tune_preds, _ = walk_forward(games, obs, best["params"], TUNE)
    a, b, sd = fit_stage2(tune_preds)
    val_preds, teams = walk_forward(games, obs, best["params"], VALIDATE)
    val = evaluate(val_preds, a, b, sd)
    base_v = margin_baselines(val_preds)

    print(f"\n{'':<18} {'rmse':>7} {'mae':>7} {'log loss':>9} {'acc':>7}   n")
    print(f"  {'ridge (TUNE)':<16} {best['tune']['margin_rmse']:>7.3f} "
          f"{best['tune']['margin_mae']:>7.3f} {best['tune']['log_loss']:>9.5f} "
          f"{best['tune']['accuracy']:>7.3f}   {best['tune']['n']}")
    print(f"  {'ridge (VALIDATE)':<16} {val['margin_rmse']:>7.3f} "
          f"{val['margin_mae']:>7.3f} {val['log_loss']:>9.5f} "
          f"{val['accuracy']:>7.3f}   {val['n']}")
    print(f"  {'predict 0':<16} {base_v['predict_zero']['margin_rmse']:>7.3f}")
    print(f"  {'predict mean':<16} {base_v['predict_mean_hfa']['margin_rmse']:>7.3f}"
          f"   (mean home margin {base_v['predict_mean_hfa']['value']})")

    lift = base_v["predict_mean_hfa"]["margin_rmse"] - val["margin_rmse"]
    print(f"\nvs predict-the-mean on VALIDATE: {lift:+.3f} RMSE points "
          f"({'better' if lift > 0 else 'WORSE'})")
    print("per-season RMSE: " + ", ".join(f"{s}: {v}" for s, v in
                                          val["per_season_rmse"].items()))

    # Final ratings, as of the end of the walk.
    last_ts = max(o["result_at"] for o in obs)
    off, dfn, home = solve(obs, teams, best["params"]["lam"],
                           best["params"]["half_life"], last_ts + timedelta(seconds=1))
    net = sorted(((t, off[t] - dfn[t]) for t in teams), key=lambda x: -x[1])
    print(f"\nhome-field effect: {home:+.4f} EPA/play")
    print("net efficiency rating (off - def), top 6 / bottom 4:")
    for t, v in net[:6]:
        print(f"  {t:<4} {v:+.4f}   off {off[t]:+.4f}  def {dfn[t]:+.4f}")
    print("  ...")
    for t, v in net[-4:]:
        print(f"  {t:<4} {v:+.4f}   off {off[t]:+.4f}  def {dfn[t]:+.4f}")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "_note": ("Opponent-adjusted ridge efficiency, market-blind. Grid "
                      "pre-registered in ridge.py before running. Stage 2 is "
                      "fitted on TUNE only and applied to VALIDATE once. Weekly "
                      "refit uses the EARLIEST T-24 in each week, so every game "
                      "is predicted from strictly less information than it could "
                      "legitimately have had - conservative, never leaking."),
            "grid": grid,
            "split": {"burn_in": BURN_IN, "tune": TUNE, "validate": VALIDATE,
                      "holdout": asof.HOLDOUT, "holdout_loaded": False},
            "prereg_frozen": asof.frozen_date(),
            "selected": best["params"],
            "stage2": {"a": round(a, 4), "b": round(b, 4), "resid_sd": round(sd, 4)},
            "tune": best["tune"], "validate": val, "baselines_validate": base_v,
            "home_field_epa_per_play": round(home, 5),
            "final_ratings": {t: {"off": round(off[t], 5), "def": round(dfn[t], 5),
                                  "net": round(off[t] - dfn[t], 5)} for t in teams},
            "all_results": results,
        }, f, indent=1, sort_keys=True)
    print(f"\nwrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
