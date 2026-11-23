"""Phase 3: preseason priors.

ae-0.1 starts every team at last season's final rating, with one fixed prior
strength (3 games) for everybody. That ignores everything that changes over an
off-season: who came back, who transferred in, who enrolled, and how far a
team is likely to drift toward its conference. This module replaces that one
line with a small, explicit Bayesian prior:

    prior mean      = a linear preseason model fitted on PAST seasons only
                      (expanding window: season s is fit on seasons < s)
    prior strength  = k * sigma2_obs / sigma2_prior(team)

where sigma2_prior is the preseason model's out-of-sample error variance
(optionally a function of returning minutes, so a rebuilt roster gets a
looser prior), sigma2_obs is the single-game noise of a per-possession
efficiency observation, and k is the one shrinkage knob (k = 1 is the
textbook Normal-Normal posterior; tuned on tune seasons only).

Targets and lag features are "data-only" season ratings: fitted on all of a
season's games with a negligible prior and no recency weighting, so a team's
target never contains the prior that is being learned.

Leakage rules:
- the preseason model for season s is fitted on target seasons < s only;
- every feature for season s is known before season s's first game
  (last season's ratings, the conference schedule, roster/transfer/recruit
  aggregates from ncaa.cbbd_roster).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from . import config as C
from .ingest import d1_teams
from .ratings import Params, PriorTable, fit

DERIVED = C.ROOT / "data" / "derived"
TEAM_PRIORS_CSV = DERIVED / "team_priors.csv"

# Feature blocks. Each variant is a cumulative list of blocks so the report
# reads as "what did this block add on top of the previous one".
BLOCKS = {
    "last": ["off_l1", "dfn_l1"],                    # last season (refit slope; ae-0.1 uses 1.0)
    "l2": ["off_l2", "dfn_l2"],                      # two seasons back (programme strength)
    "conf": ["conf_off_l1", "conf_dfn_l1"],          # partial pooling toward this season's conference
    "ret": ["ret_min", "off_l1_x_ret", "dfn_l1_x_ret"],  # returning minutes, and how much of last season carries
    "transfer": ["tr_in_min", "tr_in_x_src_off", "tr_in_x_src_dfn"],
    "recruit": ["rec_score"],
}
TEMPO_FEATURES = ["tempo_l1", "tempo_l2", "conf_tempo_l1", "tempo_l1_x_ret"]


@dataclass
class PriorSpec:
    name: str
    blocks: tuple = ()
    k: float = 1.0                  # shrinkage scale on the Bayesian prior strength
    hetero: bool = False            # prior variance depends on returning minutes
    min_train_seasons: int = 1


# ------------------------------------------------------------------ data
def season_ratings(games: pd.DataFrame, p: Params, seasons=None) -> pd.DataFrame:
    """Data-only end-of-season ratings for D1 teams, one row per (season, team)."""
    pw = replace(p, prior_games=0.3, tempo_prior_games=0.3, half_life_days=np.inf)
    seasons = seasons or range(C.FIRST_SEASON, C.CURRENT_SEASON + 1)
    rows = []
    for s in seasons:
        r = fit(games, None, s, None, pw)
        d1 = d1_teams(s)
        for i, t in enumerate(r.teams):
            if t in d1:
                rows.append((s, int(t), r.off[i], r.dfn[i], r.tempo[i], r.n_games[i], r.mu, r.tmu))
    return pd.DataFrame(rows, columns=["season", "team_id", "off", "dfn", "tempo", "n", "mu", "tmu"])


def conferences(games: pd.DataFrame) -> pd.DataFrame:
    """Each team's conference in each season, from the published schedule
    (known before the season): the modal conference id it plays under."""
    h = games[["season", "h_team_id", "home_conference_id"]].set_axis(["season", "team_id", "conf"], axis=1)
    a = games[["season", "a_team_id", "away_conference_id"]].set_axis(["season", "team_id", "conf"], axis=1)
    x = pd.concat([h, a]).dropna()
    return (x.groupby(["season", "team_id"]).conf.agg(lambda v: v.value_counts().index[0])
            .reset_index())


def load_team_priors() -> pd.DataFrame | None:
    if TEAM_PRIORS_CSV.exists():
        return pd.read_csv(TEAM_PRIORS_CSV)
    return None


def feature_table(sr: pd.DataFrame, conf: pd.DataFrame, tp: pd.DataFrame | None) -> pd.DataFrame:
    """One row per (season s, D1 team) with targets (season s data-only
    ratings) and features known before season s."""
    base = sr[["season", "team_id", "off", "dfn", "tempo", "n"]]
    l1 = sr[["season", "team_id", "off", "dfn", "tempo"]].assign(season=sr.season + 1)
    l1.columns = ["season", "team_id", "off_l1", "dfn_l1", "tempo_l1"]
    l2 = sr[["season", "team_id", "off", "dfn", "tempo"]].assign(season=sr.season + 2)
    l2.columns = ["season", "team_id", "off_l2", "dfn_l2", "tempo_l2"]
    # every D1 team of every season that has a schedule, even with no games yet
    teams = pd.DataFrame([(s, t) for s in range(C.FIRST_SEASON + 1, C.CURRENT_SEASON + 1) for t in d1_teams(s)],
                         columns=["season", "team_id"])
    x = teams.merge(base, on=["season", "team_id"], how="left")
    x = x.merge(l1, on=["season", "team_id"], how="left").merge(l2, on=["season", "team_id"], how="left")
    x["new_d1"] = x.off_l1.isna()
    for c in ("off", "dfn", "tempo"):
        # no previous D1 season: the ae-0.1 new-programme defaults
        x[f"{c}_l1"] = x[f"{c}_l1"].fillna({"off": -4.0, "dfn": 4.0, "tempo": 0.0}[c])
        x[f"{c}_l2"] = x[f"{c}_l2"].fillna(x[f"{c}_l1"])
    x = x.merge(conf, on=["season", "team_id"], how="left")
    # conference mean of last season's ratings, excluding the team itself
    for c in ("off", "dfn", "tempo"):
        g = x.groupby(["season", "conf"])[f"{c}_l1"]
        s_, n_ = g.transform("sum"), g.transform("count")
        x[f"conf_{c}_l1"] = np.where(n_ > 1, (s_ - x[f"{c}_l1"]) / (n_ - 1).clip(lower=1), 0.0)
        x[f"conf_{c}_l1"] = x[f"conf_{c}_l1"].fillna(0.0)
    if tp is not None:
        x = x.merge(tp, on=["season", "team_id"], how="left")
    for c in ("ret_min", "tr_in_min", "tr_in_src_off", "tr_in_src_dfn", "rec_score"):
        if c not in x:
            x[c] = np.nan
    x["has_roster"] = x.ret_min.notna()
    # roster aggregates are centred on their tune-season means so a missing
    # value (filled with 0) means "average", not "nobody came back".
    for c, fill in (("ret_min", 0.55), ("tr_in_min", 0.15), ("tr_in_src_off", 0.0),
                    ("tr_in_src_dfn", 0.0), ("rec_score", 0.0)):
        x[c] = x[c].fillna(fill)
    x["ret_c"] = x.ret_min - 0.55
    x["off_l1_x_ret"] = x.off_l1 * x.ret_c
    x["dfn_l1_x_ret"] = x.dfn_l1 * x.ret_c
    x["tempo_l1_x_ret"] = x.tempo_l1 * x.ret_c
    x["tr_in_x_src_off"] = x.tr_in_min * x.tr_in_src_off
    x["tr_in_x_src_dfn"] = x.tr_in_min * x.tr_in_src_dfn
    return x


# ------------------------------------------------------------------ preseason model
def _cols(spec: PriorSpec) -> list[str]:
    cols = []
    for b in spec.blocks:
        cols += BLOCKS[b]
    return [c.replace("ret_min", "ret_c") for c in cols]


def _ols(X, y):
    X1 = np.c_[np.ones(len(X)), X]
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    return beta, y - X1 @ beta


def fit_preseason(train: pd.DataFrame, spec: PriorSpec) -> dict:
    """Linear preseason model per component, plus its error variance."""
    cols = _cols(spec)
    tcols = [c for c in TEMPO_FEATURES if (c != "tempo_l2" or "l2" in spec.blocks)
             and (c != "conf_tempo_l1" or "conf" in spec.blocks)
             and (c != "tempo_l1_x_ret" or "ret" in spec.blocks)]
    tr = train[train.n >= 15]          # targets from teams with a real season
    out = dict(cols=cols, tcols=tcols)
    for comp, cc in (("off", cols), ("dfn", cols), ("tempo", tcols)):
        beta, res = _ols(tr[cc].values, tr[comp].values)
        # Target noise: a data-only season rating carries ~sigma2_obs / n of
        # estimation noise that is not prior error. Remove it.
        noise = SIGMA2_OBS[comp] / tr.n.values
        v = max(float(np.mean(res ** 2 - noise)), 0.25)
        out[comp] = dict(beta=beta, var=v)
        if spec.hetero and comp != "tempo":
            # variance as a function of returning minutes (looser prior for rebuilt rosters)
            Z = np.c_[np.ones(len(tr)), tr.ret_c.values]
            g, *_ = np.linalg.lstsq(Z, res ** 2 - noise, rcond=None)
            out[comp]["var_g"] = g
    return out


def predict_preseason(model: dict, x: pd.DataFrame, spec: PriorSpec):
    m = {}
    for comp in ("off", "dfn", "tempo"):
        cc = model["tcols"] if comp == "tempo" else model["cols"]
        b = model[comp]["beta"]
        mean = b[0] + x[cc].values @ b[1:]
        if "var_g" in model[comp]:
            g = model[comp]["var_g"]
            var = np.clip(g[0] + g[1] * x.ret_c.values, 0.25 * model[comp]["var"], 4 * model[comp]["var"])
        else:
            var = np.full(len(x), model[comp]["var"])
        m[comp] = (mean, var)
    return m


# Single-observation noise variance (points per 100 possessions for off/dfn,
# possessions for tempo), measured on TUNE seasons by `measure_obs_noise` and
# frozen here so the prior strengths are reproducible.
SIGMA2_OBS = {"off": 120.0, "dfn": 120.0, "tempo": 30.0}


def measure_obs_noise(games: pd.DataFrame, sr: pd.DataFrame) -> dict:
    """Residual variance of one team-game efficiency (and one game's
    possessions) around the data-only season ratings, tune seasons only."""
    g = games[games.season.isin(C.TUNE_SEASONS) & games.d1_game]
    r = sr.set_index(["season", "team_id"])
    hk = list(zip(g.season, g.h_team_id))
    ak = list(zip(g.season, g.a_team_id))
    H, A = r.reindex(hk), r.reindex(ak)
    ok = H.off.notna().values & A.off.notna().values
    hca = 1.6  # per side, close to the fixed ae-0.1 home edge
    loc = np.where(g.neutral_site.values, 0, 1)
    eff_h = 100 * g.h_pts.values / g.poss.values
    eff_a = 100 * g.a_pts.values / g.poss.values
    ph = H.mu.values + H.off.values + A.dfn.values + hca * loc
    pa = A.mu.values + A.off.values + H.dfn.values - hca * loc
    e = np.r_[(eff_h - ph)[ok], (eff_a - pa)[ok]]
    pt = H.tmu.values + H.tempo.values + A.tempo.values
    et = (g.poss.values - pt)[ok]
    return dict(eff=float(np.var(e)), tempo=float(np.var(et)), n=int(ok.sum()))


def build_prior(feat: pd.DataFrame, sr: pd.DataFrame, season: int, spec: PriorSpec):
    """PriorTable for `season`, fitted on target seasons < season.
    Returns None when there are too few training seasons (caller falls back
    to the ae-0.1 prior)."""
    train = feat[(feat.season < season) & (feat.season >= C.FIRST_SEASON + 2) & feat.n.notna()]
    if spec.blocks and any(b in ("ret", "transfer", "recruit") for b in spec.blocks):
        train = train[train.has_roster]
    if train.season.nunique() < spec.min_train_seasons:
        return None
    model = fit_preseason(train, spec)
    x = feat[feat.season == season]
    pm = predict_preseason(model, x, spec)
    prev = sr[sr.season == season - 1]
    mu = float(prev.mu.mean()) if len(prev) else 103.0
    tmu = float(prev.tmu.mean()) if len(prev) else 69.5
    mean, lam = {}, {}
    for i, t in enumerate(x.team_id.values):
        mean[int(t)] = (pm["off"][0][i], pm["dfn"][0][i], pm["tempo"][0][i])
        lam[int(t)] = tuple(spec.k * SIGMA2_OBS[c] / pm[c][1][i] for c in ("off", "dfn", "tempo"))
    return PriorTable(mean=mean, lam=lam, mu=mu, tmu=tmu), model
