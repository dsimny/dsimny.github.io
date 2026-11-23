"""Opponent-adjusted efficiency and tempo ratings (KenPom-style), fitted as a
penalised least-squares problem with a prior-season prior.

Offence observation, one per team per game:
    100 * pts / poss  =  mu + off[team] + dfn[opp] + h * loc + noise
        loc = +1 for the home side, -1 for the away side, 0 on a neutral court
        off  > 0 : better than average offence
        dfn  > 0 : WORSE than average defence (points allowed above average)
Tempo observation, one per game:
    poss  =  tmu + tempo[home] + tempo[away] + noise

The penalty pulls each team toward its prior (a shrunk copy of last season's
final rating). That is the Bayesian reading: a Gaussian prior whose strength
`prior_games` is expressed in games-equivalent, so after N games a team's
rating is roughly N/(N + prior_games) data and the rest prior. Early in the
season, when the data are thin, the prior dominates; by February it barely
matters. Recency weighting (`half_life_days`) lets the rating move.

Leakage rule, enforced by `fit(games, asof)`: only games with
game_date < asof are used. Tests assert this.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Params:
    prior_games: float = 6.0        # strength of the team prior, in games
    prior_regress: float = 0.65     # fraction of last season's rating carried over
    tempo_prior_games: float = 6.0
    tempo_regress: float = 0.75
    half_life_days: float = 60.0    # recency weighting within a season (inf = off)
    hca_lambda: float = 1e4         # home edge is effectively FIXED to its prior (see fit)
    nond1_off: float = -12.0        # prior for a non-D1 opponent
    nond1_dfn: float = 10.0
    newd1_off: float = -4.0         # prior for a D1 team with no previous-season rating
    newd1_dfn: float = 4.0          # (new programme, or the very first season)


@dataclass
class Ratings:
    teams: np.ndarray
    off: np.ndarray
    dfn: np.ndarray
    tempo: np.ndarray
    mu: float
    h: float
    tmu: float
    n_games: np.ndarray
    asof: pd.Timestamp | None = None
    idx: dict = field(default_factory=dict)

    def __post_init__(self):
        self.idx = {int(t): i for i, t in enumerate(self.teams)}

    def get(self, team: int):
        i = self.idx.get(int(team))
        if i is None:
            return None
        return self.off[i], self.dfn[i], self.tempo[i]

    @property
    def net(self):
        return self.off - self.dfn

    def frame(self, names=None) -> pd.DataFrame:
        df = pd.DataFrame(dict(team_id=self.teams, adj_off=self.mu + self.off, adj_def=self.mu + self.dfn,
                               adj_em=self.off - self.dfn, adj_tempo=self.tmu + 2 * self.tempo,
                               games=self.n_games))
        if names:
            df.insert(1, "team", df.team_id.map(names))
        return df.sort_values("adj_em", ascending=False).reset_index(drop=True)


def _ridge(X_rows, X_cols, X_vals, y, w, ncol, prior, lam):
    """Solve min sum w (y - Xb)^2 + sum lam_j (b_j - prior_j)^2 via normal equations.

    X is given in COO form (rows, cols, vals). lam may be 0 for free columns.
    """
    from scipy.sparse import coo_matrix
    X = coo_matrix((X_vals, (X_rows, X_cols)), shape=(len(y), ncol)).tocsr()
    Xw = X.multiply(w[:, None]).tocsr()
    A = (X.T @ Xw).toarray()
    b = X.T @ (w * y)
    A[np.diag_indices(ncol)] += lam
    b = b + lam * prior
    return np.linalg.solve(A, b)


def fit(games: pd.DataFrame, asof, season: int, prior: "Ratings | None", p: Params,
        teams: np.ndarray | None = None, d1: set | None = None) -> Ratings:
    """Fit ratings for `season` from that season's games strictly before `asof`.

    `games` may hold many seasons; only rows of `season` with game_date < asof
    are used. `prior` is last season's final Ratings (or None).
    """
    asof = pd.Timestamp(asof) if asof is not None else None
    g = games[games.season == season]
    if asof is not None:
        g = g[g.game_date < asof]
    if teams is None:
        teams = np.unique(np.concatenate([games.loc[games.season == season, "h_team_id"].values,
                                          games.loc[games.season == season, "a_team_id"].values]))
    teams = np.asarray(teams, dtype=int)
    n = len(teams)
    ti = {t: i for i, t in enumerate(teams)}

    # ---- priors. D1 membership comes from the published schedule (not results).
    if d1 is None:
        from .ingest import d1_teams
        d1 = d1_teams(season)
    isd1 = np.array([t in d1 for t in teams])
    po = np.where(isd1, p.newd1_off if prior is not None else 0.0, p.nond1_off)
    pd_ = np.where(isd1, p.newd1_dfn if prior is not None else 0.0, p.nond1_dfn)
    pt = np.zeros(n)
    mu0, tmu0 = 103.0, 69.5
    if prior is not None:
        mu0, tmu0 = prior.mu, prior.tmu
        for t, i in ti.items():
            r = prior.get(t)
            if r is not None:
                po[i] = p.prior_regress * r[0]
                pd_[i] = p.prior_regress * r[1]
                pt[i] = p.tempo_regress * r[2]
    h0 = home_edge_prior(games, season)

    ng = np.zeros(n)
    if len(g) == 0:
        return Ratings(teams, po.astype(float), pd_.astype(float), pt, mu0, h0, tmu0, ng, asof)

    hi = g.h_team_id.map(ti).values.astype(int)
    ai = g.a_team_id.map(ti).values.astype(int)
    loc = np.where(g.neutral_site.values, 0.0, 1.0)
    if asof is not None and np.isfinite(p.half_life_days):
        age = (asof - g.game_date).dt.days.values.astype(float)
        wg = 0.5 ** (age / p.half_life_days)
    else:
        wg = np.ones(len(g))
    np.add.at(ng, hi, 1)
    np.add.at(ng, ai, 1)

    # ---- efficiency: columns [off (n) | dfn (n) | mu | h]
    m = len(g)
    y = np.concatenate([100 * g.h_pts.values / g.poss.values, 100 * g.a_pts.values / g.poss.values])
    w = np.concatenate([wg, wg])
    r = np.arange(2 * m)
    off_col = np.concatenate([hi, ai])
    def_col = n + np.concatenate([ai, hi])
    mu_col = np.full(2 * m, 2 * n)
    h_col = np.full(2 * m, 2 * n + 1)
    rows = np.concatenate([r, r, r, r])
    cols = np.concatenate([off_col, def_col, mu_col, h_col])
    vals = np.concatenate([np.ones(2 * m), np.ones(2 * m), np.ones(2 * m), np.concatenate([loc, -loc])])
    lam = np.concatenate([np.full(2 * n, p.prior_games), [1.0, p.hca_lambda]])
    pri = np.concatenate([po, pd_, [mu0, h0]])
    beta = _ridge(rows, cols, vals, y, w, 2 * n + 2, pri, lam)
    off, dfn, mu, h = beta[:n], beta[n:2 * n], beta[2 * n], beta[2 * n + 1]
    # Identification: (mu, off, dfn) are only defined up to shifts. Centre
    # off and dfn on the D1 average so "0" means an average D1 team and mu is
    # the D1 average efficiency. Predictions are unchanged by this.
    co, cd = off[isd1].mean(), dfn[isd1].mean()
    off, dfn, mu = off - co, dfn - cd, mu + co + cd

    # ---- tempo: columns [tempo (n) | tmu]
    yt = g.poss.values
    rt = np.arange(m)
    rows_t = np.concatenate([rt, rt, rt])
    cols_t = np.concatenate([hi, ai, np.full(m, n)])
    vals_t = np.ones(3 * m)
    lam_t = np.concatenate([np.full(n, p.tempo_prior_games), [5.0]])
    pri_t = np.concatenate([pt, [tmu0]])
    bt = _ridge(rows_t, cols_t, vals_t, yt, wg, n + 1, pri_t, lam_t)
    tempo, tmu = bt[:n], bt[n]
    ct = tempo[isd1].mean()
    tempo, tmu = tempo - ct, tmu + 2 * ct
    return Ratings(teams, off, dfn, tempo, float(mu), float(h), float(tmu), ng, asof)


_HCA_CACHE: dict = {}


def home_edge_prior(games: pd.DataFrame, season: int) -> float:
    """Home edge per side, in points per 100 possessions, measured on the
    PREVIOUS season's non-neutral conference games (balanced schedules, so
    team strength cancels and the mean home margin estimates the home edge).

    Why fixed rather than fitted in-season: early-season home games are
    mostly 'buy games' (strong host, weak visitor). With team ratings shrunk
    toward priors, a free home parameter absorbs that strength gap and was
    measured inflating the edge to ~4.7 points, biasing every home
    projection by about -2.3 points out of sample.
    """
    key = (id(games), season)
    if key in _HCA_CACHE:
        return _HCA_CACHE[key]
    prev = games[(games.season == season - 1) & (~games.neutral_site) & (games.conference_competition == True)]  # noqa: E712
    if len(prev) < 500:
        val = 2.3  # ~3.2 points at 69 possessions, the long-run D1 figure
    else:
        val = float(prev.margin.mean() / (2 * prev.poss.mean() / 100))
    _HCA_CACHE[key] = val
    return val


def predict(r: Ratings, home: int, away: int, neutral: bool):
    """Projected (home_pts, away_pts, possessions). Unknown team -> None."""
    a, b = r.get(home), r.get(away)
    if a is None or b is None:
        return None
    loc = 0.0 if neutral else 1.0
    poss = r.tmu + a[2] + b[2]
    eh = r.mu + a[0] + b[1] + r.h * loc
    ea = r.mu + b[0] + a[1] - r.h * loc
    return eh * poss / 100.0, ea * poss / 100.0, poss
