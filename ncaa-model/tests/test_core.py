"""Offline unit tests (no network, no downloaded data)."""
import numpy as np
import pandas as pd
import pytest

from ncaa import metrics as M
from ncaa.ingest import parse_pickcenter, possessions
from ncaa.ratings import Params, fit, predict


# ---------------------------------------------------------------- metrics
def test_brier_logloss_basics():
    assert M.brier([1, 0], [1, 0]) == 0
    assert M.brier([0.5, 0.5], [1, 0]) == 0.25
    assert M.log_loss([0.5], [1]) == pytest.approx(np.log(2))


def test_odds_conversion_and_devig():
    assert M.american_to_decimal(-110) == pytest.approx(1.909090, 1e-5)
    assert M.american_to_decimal(150) == pytest.approx(2.5)
    ph, pa = M.devig_two_way(-110, -110)
    assert ph == pytest.approx(0.5) and pa == pytest.approx(0.5)
    ph, pa = M.devig_two_way(-200, 170)
    assert ph + pa == pytest.approx(1)
    assert ph > 0.6


def test_ev_formula_and_push():
    # fair coin at +100 is zero EV
    assert M.ev_per_unit(0.5, 2.0) == pytest.approx(0)
    # 52.38% at -110 is break-even
    assert M.ev_per_unit(1 / M.american_to_decimal(-110), M.american_to_decimal(-110)) == pytest.approx(0, abs=1e-9)
    # push mass is excluded from the loss
    assert M.ev_per_unit(0.45, 2.0, p_push=0.1) == pytest.approx(0.45 - 0.45)


def test_cover_probs_sum_to_one_and_push_only_on_integers():
    ph, pp, pa = M.cover_probs(np.array([3.0, 3.0]), np.array([-3.0, -3.5]), 11.0)
    assert np.allclose(ph + pp + pa, 1)
    assert pp[0] > 0.02 and pp[1] == pytest.approx(0)
    # model margin == required margin -> symmetric
    assert ph[0] == pytest.approx(pa[0])


def test_max_drawdown():
    assert M.max_drawdown([1, -1, -1, 2, -3]) == 3


def test_possessions_formula():
    assert possessions(60, 10, 12, 20) == pytest.approx(60 - 10 + 12 + 9.5)


# ---------------------------------------------------------------- odds parsing
def test_parse_pickcenter_reads_open_close():
    doc = {"header": {"competitions": [{"neutralSite": True}]},
           "pickcenter": [{"provider": {"name": "DraftKings"}, "spread": -6.5, "overUnder": 146.5,
                           "homeTeamOdds": {"moneyLine": -285, "spreadOdds": -112},
                           "awayTeamOdds": {"moneyLine": 230, "spreadOdds": -108},
                           "pointSpread": {"home": {"open": {"line": "-7.5", "odds": "-105"},
                                                    "close": {"line": "-6.5", "odds": "-112"}},
                                           "away": {"open": {"line": "+7.5", "odds": "-115"}}},
                           "total": {"over": {"open": {"line": "o144.5", "odds": "-110"},
                                              "close": {"line": "o146.5"}}}}]}
    r = parse_pickcenter(1, doc)[0]
    assert r["spread"] == -6.5 and r["spread_open"] == -7.5 and r["spread_close"] == -6.5
    assert r["total_open"] == 144.5 and r["home_ml"] == -285 and r["json_neutral_site"] is True


# ---------------------------------------------------------------- ratings
def _synthetic(n_teams=40, rounds=12, seed=0, hca_pts=3.0):
    """Round-robin-ish season from KNOWN ratings, Gaussian noise."""
    rng = np.random.default_rng(seed)
    off = rng.normal(0, 6, n_teams)
    dfn = rng.normal(0, 6, n_teams)
    rows, gid = [], 0
    start = pd.Timestamp("2020-11-10")
    for k in range(rounds):
        perm = rng.permutation(n_teams)
        for a, b in zip(perm[::2], perm[1::2]):
            poss = 68 + rng.normal(0, 3)
            h = hca_pts / 2 / poss * 100
            eh = 103 + off[a] + dfn[b] + h + rng.normal(0, 6)
            ea = 103 + off[b] + dfn[a] - h + rng.normal(0, 6)
            rows.append(dict(game_id=gid, season=2020, game_date=start + pd.Timedelta(days=3 * k),
                             h_team_id=a, a_team_id=b, neutral_site=False, poss=poss,
                             h_pts=eh * poss / 100, a_pts=ea * poss / 100, conference_competition=False))
            gid += 1
    g = pd.DataFrame(rows)
    g["margin"] = g.h_pts - g.a_pts
    return g, off, dfn


def test_ratings_recover_known_truth():
    g, off, dfn = _synthetic(rounds=30)
    p = Params(prior_games=0.5, half_life_days=np.inf)
    r = fit(g, None, 2020, None, p, d1=set(range(40)))
    est_net = r.off - r.dfn
    true_net = (off - off.mean()) - (dfn - dfn.mean())
    assert np.corrcoef(est_net, true_net)[0, 1] > 0.9


def test_no_leakage_future_games_do_not_change_ratings():
    g, *_ = _synthetic()
    p = Params()
    asof = pd.Timestamp("2020-11-25")
    r1 = fit(g, asof, 2020, None, p, d1=set(range(40)))
    # scramble every result on/after asof: ratings must be identical
    g2 = g.copy()
    fut = g2.game_date >= asof
    g2.loc[fut, ["h_pts", "a_pts"]] = g2.loc[fut, ["a_pts", "h_pts"]].values * 3
    r2 = fit(g2, asof, 2020, None, p, d1=set(range(40)))
    assert np.allclose(r1.off, r2.off) and np.allclose(r1.dfn, r2.dfn) and np.allclose(r1.tempo, r2.tempo)


def test_no_data_returns_prior():
    g, *_ = _synthetic()
    r = fit(g, pd.Timestamp("2019-01-01"), 2020, None, Params(), d1=set(range(40)))
    assert np.allclose(r.off, 0) and r.n_games.sum() == 0


def test_predict_symmetry_on_neutral_court():
    g, *_ = _synthetic()
    r = fit(g, None, 2020, None, Params(), d1=set(range(40)))
    h1, a1, _ = predict(r, 1, 2, True)
    h2, a2, _ = predict(r, 2, 1, True)
    assert h1 == pytest.approx(a2) and a1 == pytest.approx(h2)
    hh, aa, _ = predict(r, 1, 2, False)
    assert hh - aa > h1 - a1  # home edge applies only off a neutral court
