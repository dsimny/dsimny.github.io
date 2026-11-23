"""Leakage and sanity tests for the Phase 3 preseason prior (offline, synthetic)."""
import numpy as np
import pandas as pd
import pytest

from ncaa.priors import PriorSpec, build_prior
from ncaa.ratings import Params, PriorTable, fit, predict


def _feat(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(2017, 2024):
        for t in range(60):
            o1, d1 = rng.normal(0, 6), rng.normal(0, 6)
            ret = rng.uniform(0.1, 0.9)
            rows.append(dict(season=s, team_id=t, off=0.7 * o1 + rng.normal(0, 3), dfn=0.7 * d1 + rng.normal(0, 3),
                             tempo=rng.normal(0, 2), n=30, off_l1=o1, dfn_l1=d1, tempo_l1=0.0, off_l2=o1,
                             dfn_l2=d1, tempo_l2=0.0, conf_off_l1=0.0, conf_dfn_l1=0.0, conf_tempo_l1=0.0,
                             ret_c=ret - 0.55, off_l1_x_ret=o1 * (ret - 0.55), dfn_l1_x_ret=d1 * (ret - 0.55),
                             tempo_l1_x_ret=0.0, tr_in_min=0.1, tr_in_x_src_off=0.0, tr_in_x_src_dfn=0.0,
                             rec_score=0.0, has_roster=True))
    return pd.DataFrame(rows)


SR = pd.DataFrame(dict(season=[2021], team_id=[0], mu=[103.0], tmu=[69.0]))


@pytest.mark.parametrize("spec", [PriorSpec("a", ("last",)), PriorSpec("b", ("last", "ret"), hetero=True)])
def test_prior_ignores_current_and_future_targets(spec):
    f = _feat()
    p1, _ = build_prior(f, SR, 2021, spec)
    g = f.copy()
    later = g.season >= 2021
    g.loc[later, ["off", "dfn", "tempo"]] = 99.0       # scramble this season's and later outcomes
    p2, _ = build_prior(g, SR, 2021, spec)
    assert p1.mean == p2.mean and p1.lam == p2.lam


def test_prior_slope_recovers_regression_to_mean():
    _, model = build_prior(_feat(), SR, 2023, PriorSpec("a", ("last",)))
    assert model["off"]["beta"][1] == pytest.approx(0.7, abs=0.08)


def test_prior_table_used_when_no_games():
    games = pd.DataFrame(columns=["season", "game_date", "h_team_id", "a_team_id", "h_pts", "a_pts", "poss",
                                  "neutral_site", "margin", "conference_competition"])
    games = games.astype({"season": int, "h_team_id": int, "a_team_id": int, "neutral_site": bool})
    games["game_date"] = pd.to_datetime(games.game_date)
    pt = PriorTable(mean={1: (5.0, -3.0, 1.0), 2: (-2.0, 2.0, -1.0)}, lam={1: (3, 3, 3), 2: (3, 3, 3)},
                    mu=100.0, tmu=68.0)
    r = fit(games, pd.Timestamp("2021-11-01"), 2021, pt, Params(), teams=np.array([1, 2]), d1={1, 2})
    h, a, poss = predict(r, 1, 2, neutral=True)
    assert poss == pytest.approx(68.0)
    assert h - a == pytest.approx((5 + 2 - (-2 - 3)) * 68 / 100)
