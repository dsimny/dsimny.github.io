#!/usr/bin/env python3
"""
Open Ledger Sports — the game model (fb-v0.1).

The last market-blind stage. Takes the two rating families, produces a predicted
margin and total, and converts each into a discrete probability mass function
over integer outcomes. Nothing here has ever seen a price.

WHAT IT COMBINES, per game, all as of that game's own T-24:
    elo_diff    home Elo + HFA - away Elo          (who wins)
    ridge_edge  home expected EPA/play - away's    (by how much)
    ridge_sum   home + away expected EPA/play      (how much scoring)

Margin is fitted on elo_diff + ridge_edge; total on ridge_sum. Elo is not
offered to the total model, because Elo knows only who won - it carries no
information about scoring level, and handing a model a feature that cannot help
is how spurious coefficients get found.

UNCERTAINTY IS OUT-OF-FOLD, ALWAYS. The residual spread comes from walk-forward
predictions, each made from information that existed at its own T-24. In-sample
residuals would be narrower, and a too-narrow distribution manufactures edge
against every line on the board - it is the single most dangerous shortcut
available in this file.

THE KEY-NUMBER VARIANT. Football margins are not smooth. Scoring comes in 3s and
7s, so certain margins are far more likely than a normal curve says. Measured on
the TUNE seasons alone - never the validate seasons, never the holdout, and
never with reference to what any book prices - margins of 3 and 7 carry
roughly triple and double their smooth-curve share. A whole-number line sits
exactly on that spike, so push probability is a real quantity, not a rounding
detail. The variant reweights the PMF by an empirical key-number profile and is
tested against the plain normal rather than assumed better.

Run: python scripts/football/game_model.py
"""
import argparse, io, json, math, os, sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asof                                        # noqa: E402
import ridge as ridge_mod                          # noqa: E402
from elo import Elo                                # noqa: E402
from teams import canonical                        # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
OUT = os.path.join(FB, "game_model.json")
ELO_FIT = os.path.join(FB, "elo_grid.json")
RIDGE_FIT = os.path.join(FB, "ridge_fit.json")

BURN_IN = list(range(2010, 2015))
TUNE = list(range(2015, 2022))
VALIDATE = list(range(2022, 2025))

MARGIN_RANGE = range(-59, 60)      # covers every NFL margin since 2010 with room
TOTAL_RANGE = range(0, 121)

# How far the key-number profile may bend the smooth curve, and how hard it is
# shrunk toward 1.
#
# REVISED after the first run, BEFORE the freeze and before any price has been
# seen. Both changes fix a structural inability, not a disappointing number:
#
#  - KEY_MIN_COUNT is new. Without it the profile was dominated by the TAILS:
#    margins like -49 or +40 occur once or twice, the smooth curve gives them
#    almost no mass, so empirical/smooth explodes and pins to the clip. The
#    first run's top six multipliers were all tail values and not one was a key
#    number. Each value's ratio is now shrunk toward 1 by c/(c + KEY_MIN_COUNT),
#    so a margin seen twice barely moves and a margin seen 300 times moves fully.
#
#  - The clip floor drops from 0.40 to 0.02. Section 7 of the pre-registration
#    names 0 alongside 3 and 7, and the spike at 0 is NEGATIVE: ties happen in
#    0.30% of games while a normal curve puts ~2.8% there, a tenfold
#    overstatement. A floor of 0.40 made that suppression unrepresentable, so
#    the profile could not do the job the spec asked of it.
#
# Recorded rather than quietly edited. After the freeze this would be fb-v0.2.
KEY_SHRINK = 0.75                  # 0 = ignore the profile, 1 = trust it fully
KEY_CLIP = (0.02, 3.5)             # a single outcome may not move more than this
KEY_MIN_COUNT = 20                 # observations before a value is trusted fully


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def discretise(mean, sd, values):
    """P(outcome = v) by integrating the normal over [v-0.5, v+0.5]."""
    p = np.array([norm_cdf((v + 0.5 - mean) / sd) - norm_cdf((v - 0.5 - mean) / sd)
                  for v in values])
    s = p.sum()
    return p / s if s > 0 else p


# --- features ---------------------------------------------------------------

def elo_features(games, params):
    model, out = Elo(**params), {}

    def predict(g):
        model._roll_season(g["_season"])
        h, a = canonical(g["home_team"]), canonical(g["away_team"])
        out[g["game_id"]] = (model.rating(h) + model.hfa) - model.rating(a)
        return None

    asof.walk(games, model.update, predict)
    return out


def ridge_features(games, obs, params):
    teams = sorted({o["team"] for o in obs} | {o["opp"] for o in obs})
    weeks = {}
    for g in games:
        weeks.setdefault((g["_season"], int(g["week"])), []).append(g)

    out = {}
    for key in sorted(weeks):
        wk = weeks[key]
        asof_ts = min(g["_t24"] for g in wk)
        train = [o for o in obs if o["result_at"] <= asof_ts]
        if len(train) < ridge_mod.MIN_TRAIN:
            continue
        off, dfn, home = ridge_mod.solve(train, teams, params["lam"],
                                         params["half_life"], asof_ts)
        for g in wk:
            h, a = canonical(g["home_team"]), canonical(g["away_team"])
            eh = off[h] + dfn[a] + home
            ea = off[a] + dfn[h]
            out[g["game_id"]] = (eh - ea, eh + ea)
    return out


def assemble(games, elo_f, ridge_f, seasons):
    wanted, rows = set(seasons), []
    for g in games:
        if g["_season"] not in wanted:
            continue
        e = elo_f.get(g["game_id"])
        r = ridge_f.get(g["game_id"])
        if e is None or r is None:
            continue
        rows.append({"game_id": g["game_id"], "season": g["_season"],
                     "elo_diff": e, "ridge_edge": r[0], "ridge_sum": r[1],
                     "margin": float(g["_margin"]), "total": float(g["_total"])})
    return rows


def fit_linear(rows, xcols, ycol):
    X = np.column_stack([[r[c] for r in rows] for c in xcols] +
                        [np.ones(len(rows))])
    y = np.array([r[ycol] for r in rows])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    resid = y - pred
    # Out-of-fold by construction: every feature was computed at its own T-24.
    return {"coef": dict(zip(list(xcols) + ["intercept"], [float(b) for b in beta])),
            "resid_sd": float(resid.std(ddof=len(xcols) + 1))}


def apply_linear(fit, rows, xcols):
    b = fit["coef"]
    return np.array([sum(b[c] * r[c] for c in xcols) + b["intercept"] for r in rows])


# --- key-number profile -----------------------------------------------------

def key_profile(rows, values, ycol, sd):
    """Empirical / smooth ratio per outcome value, from TUNE margins alone.

    The smooth reference is a normal with the same mean and spread as the data,
    so the profile isolates exactly what football's 3-and-7 scoring adds on top
    of an otherwise unremarkable bell.
    """
    y = np.array([r[ycol] for r in rows])
    counts = Counter(int(round(v)) for v in y)
    n = len(y)
    smooth = discretise(float(y.mean()), sd, values)
    prof = {}
    for v, s in zip(values, smooth):
        emp = counts.get(v, 0) / n
        if s <= 0:
            prof[v] = 1.0
            continue
        ratio = emp / s
        ratio = min(max(ratio, KEY_CLIP[0]), KEY_CLIP[1])
        # Confidence comes from the EXPECTED count under the smooth curve, not
        # the observed one. Weighting by what we observed gets the rare cases
        # exactly backwards: ties appear 7 times where the normal predicts ~52,
        # and that gap is strong evidence precisely BECAUSE the outcome is rare.
        # Using the observed count would distrust the very cases the profile
        # exists to correct, while still letting a tail value seen twice - where
        # the expected count is a fraction of one - move nothing.
        expected = n * s
        conf = expected / (expected + KEY_MIN_COUNT)
        prof[v] = 1.0 + KEY_SHRINK * conf * (ratio - 1.0)
    return prof


def pmf(mean, sd, values, profile=None):
    p = discretise(mean, sd, values)
    if profile is not None:
        p = p * np.array([profile.get(v, 1.0) for v in values])
        s = p.sum()
        if s > 0:
            p = p / s
    return p


# --- scoring ----------------------------------------------------------------

def score_margin(rows, preds, sd, values, profile=None):
    eps = 1e-15
    vals = list(values)
    zero = vals.index(0)
    logscore, yb, phome = [], [], []
    for r, m in zip(rows, preds):
        p = pmf(m, sd, vals, profile)
        actual = int(round(r["margin"]))
        j = vals.index(actual) if actual in vals else None
        logscore.append(-math.log(max(p[j] if j is not None else eps, eps)))
        ph = p[zero + 1:].sum()
        pt = p[zero]
        phome.append(ph + 0.5 * pt)          # ties split, matching y = 0.5
        y = 1.0 if r["margin"] > 0 else (0.0 if r["margin"] < 0 else 0.5)
        yb.append(y)
    phome = np.array(phome)
    yb = np.array(yb)
    ll = float(-np.mean(yb * np.log(np.clip(phome, eps, 1)) +
                        (1 - yb) * np.log(np.clip(1 - phome, eps, 1))))
    return {"log_loss": round(ll, 5),
            "brier": round(float(np.mean((phome - yb) ** 2)), 5),
            "accuracy": round(float(np.mean(
                [1.0 if (p > 0.5) == (t > 0.5) else (0.5 if t == 0.5 else 0.0)
                 for p, t in zip(phome, yb)])), 4),
            "pmf_log_score": round(float(np.mean(logscore)), 5),
            "rmse": round(float(np.sqrt(np.mean(
                (np.array([r["margin"] for r in rows]) - preds) ** 2))), 4),
            "n": len(rows)}


def push_table(rows, preds, sd, values, profile):
    """P(margin lands exactly on n) - the quantity a whole-number line settles on."""
    vals = list(values)
    out = {}
    for n_ in (0, 3, 6, 7, 10, 14):
        plain = np.mean([pmf(m, sd, vals)[vals.index(n_)] for m in preds])
        keyed = np.mean([pmf(m, sd, vals, profile)[vals.index(n_)] for m in preds])
        emp = np.mean([1.0 if abs(int(round(r["margin"]))) == n_ else 0.0 for r in rows]) \
            if n_ != 0 else np.mean([1.0 if int(round(r["margin"])) == 0 else 0.0
                                     for r in rows])
        # plain/keyed are signed-margin == +n_; double for |margin| except 0.
        mult = 1.0 if n_ == 0 else 2.0
        out[n_] = {"plain": round(float(plain * mult), 5),
                   "key_number": round(float(keyed * mult), 5),
                   "empirical": round(float(emp), 5)}
    return out


def main():
    argparse.ArgumentParser().parse_args()

    with io.open(ELO_FIT, encoding="utf-8") as f:
        elo_params = json.load(f)["selected"]
    with io.open(RIDGE_FIT, encoding="utf-8") as f:
        ridge_params = json.load(f)["selected"]
    print(f"elo params   {elo_params}")
    print(f"ridge params {ridge_params}")

    seasons = BURN_IN + TUNE + VALIDATE
    games = asof.load_games(seasons=seasons, purpose="game model")
    obs = ridge_mod.build(games, ridge_mod.load_efficiency())
    print(f"holdout {asof.HOLDOUT} not loaded (prereg frozen: "
          f"{asof.frozen_date() or 'NOT YET'})\n")

    print("building as-of features for both rating families...")
    ef = elo_features(games, elo_params)
    rf = ridge_features(games, obs, ridge_params)
    tune = assemble(games, ef, rf, TUNE)
    val = assemble(games, ef, rf, VALIDATE)
    print(f"  tune {len(tune):,} games, validate {len(val):,} games")

    # --- fit on TUNE only ---------------------------------------------------
    MX = ["elo_diff", "ridge_edge"]
    TX = ["ridge_sum"]
    mfit = fit_linear(tune, MX, "margin")
    tfit = fit_linear(tune, TX, "total")
    print(f"\nmargin = {mfit['coef']['intercept']:+.3f} "
          f"{mfit['coef']['elo_diff']:+.5f}*elo_diff "
          f"{mfit['coef']['ridge_edge']:+.3f}*ridge_edge   "
          f"resid sd {mfit['resid_sd']:.3f}")
    print(f"total  = {tfit['coef']['intercept']:+.3f} "
          f"{tfit['coef']['ridge_sum']:+.3f}*ridge_sum   "
          f"resid sd {tfit['resid_sd']:.3f}")

    mprof = key_profile(tune, MARGIN_RANGE, "margin", mfit["resid_sd"])
    spikes = sorted(mprof.items(), key=lambda kv: -kv[1])[:6]
    print("\nkey-number profile from TUNE margins (top multipliers): " +
          ", ".join(f"{v:+d}: x{m:.2f}" for v, m in spikes))

    # --- score --------------------------------------------------------------
    mt = apply_linear(mfit, tune, MX)
    mv = apply_linear(mfit, val, MX)

    plain_t = score_margin(tune, mt, mfit["resid_sd"], MARGIN_RANGE)
    keyed_t = score_margin(tune, mt, mfit["resid_sd"], MARGIN_RANGE, mprof)
    plain_v = score_margin(val, mv, mfit["resid_sd"], MARGIN_RANGE)
    keyed_v = score_margin(val, mv, mfit["resid_sd"], MARGIN_RANGE, mprof)

    print(f"\n{'margin model':<26} {'rmse':>7} {'log loss':>9} {'acc':>7} "
          f"{'pmf log score':>14}")
    for label, s in (("TUNE plain normal", plain_t), ("TUNE key-number", keyed_t),
                     ("VALIDATE plain normal", plain_v), ("VALIDATE key-number", keyed_v)):
        print(f"  {label:<24} {s['rmse']:>7.3f} {s['log_loss']:>9.5f} "
              f"{s['accuracy']:>7.3f} {s['pmf_log_score']:>14.5f}")

    delta = plain_v["pmf_log_score"] - keyed_v["pmf_log_score"]
    print(f"\nkey-number variant vs plain normal on VALIDATE: "
          f"{delta:+.5f} PMF log score ({'better' if delta > 0 else 'WORSE'})")

    # --- totals -------------------------------------------------------------
    tt = apply_linear(tfit, tune, TX)
    tv = apply_linear(tfit, val, TX)
    ty = np.array([r["total"] for r in val])
    league_mean = float(np.mean([r["total"] for r in tune]))
    print(f"\n{'total model':<26} {'rmse':>7}")
    print(f"  {'TUNE':<24} {np.sqrt(np.mean((np.array([r['total'] for r in tune]) - tt) ** 2)):>7.3f}")
    print(f"  {'VALIDATE':<24} {np.sqrt(np.mean((ty - tv) ** 2)):>7.3f}")
    print(f"  {'league mean baseline':<24} "
          f"{np.sqrt(np.mean((ty - league_mean) ** 2)):>7.3f}   (mean {league_mean:.2f})")

    # --- pushes -------------------------------------------------------------
    pt = push_table(val, mv, mfit["resid_sd"], MARGIN_RANGE, mprof)
    print(f"\nP(|margin| lands exactly on n) on VALIDATE")
    print(f"{'n':>3} {'plain':>9} {'key-number':>12} {'empirical':>11}")
    for n_, d in pt.items():
        print(f"{n_:>3} {d['plain']:>9.4f} {d['key_number']:>12.4f} "
              f"{d['empirical']:>11.4f}")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "_note": ("Game model, market-blind. Fitted on TUNE only and applied "
                      "to VALIDATE once. Residual spread is out-of-fold: every "
                      "feature was computed at its own T-24. The key-number "
                      "profile is measured from TUNE margins alone - never the "
                      "validate seasons, never the holdout, never a price."),
            "elo_params": elo_params, "ridge_params": ridge_params,
            "split": {"tune": TUNE, "validate": VALIDATE,
                      "holdout": asof.HOLDOUT, "holdout_loaded": False},
            "prereg_frozen": asof.frozen_date(),
            "margin_fit": mfit, "total_fit": tfit,
            "key_shrink": KEY_SHRINK, "key_clip": list(KEY_CLIP),
            "key_profile": {str(k): round(v, 4) for k, v in mprof.items()
                            if abs(v - 1.0) > 0.01},
            "scores": {"tune_plain": plain_t, "tune_key": keyed_t,
                       "validate_plain": plain_v, "validate_key": keyed_v},
            "total_league_mean_baseline": round(league_mean, 3),
            "push_probabilities_validate": {str(k): v for k, v in pt.items()},
        }, f, indent=1, sort_keys=True)
    print(f"\nwrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
