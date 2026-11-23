"""Offline test of the CBBD roster -> team-prior aggregation (synthetic rows)."""
import pandas as pd
import pytest

from ncaa.cbbd_roster import build_features


def _stat(team, ath, mins, por=1.0, ws=1.0):
    return {"teamId": team, "athleteId": ath, "minutes": mins, "PORPAG": por, "winShares": {"total": ws}}


def test_returning_and_transfer_minutes():
    # season 2025 rosters; team 1 (ESPN 101) keeps athletes 1,2 and adds 5 from team 2 (ESPN 202)
    ros = {2025: [{"teamId": 1, "teamSourceId": "101", "players": [{"id": 1}, {"id": 2}, {"id": 5}, {"id": 9}]},
                  {"teamId": 2, "teamSourceId": "202", "players": [{"id": 6}]}]}
    st = {2024: [_stat(1, 1, 600), _stat(1, 2, 200), _stat(1, 3, 200),       # team 1: 1000 min
                 _stat(2, 5, 300), _stat(2, 6, 700)]}                         # team 2: 1000 min
    rec = {2024: [{"committedTo": {"id": 1}, "ranking": 1}, {"committedTo": {"id": 2}, "ranking": 301}]}
    sr = pd.DataFrame({"season": [2024, 2024], "team_id": [101, 202], "off": [5.0, -3.0], "dfn": [-2.0, 1.0]})
    df, diag = build_features(ros, st, rec, sr)
    a = df.set_index("team_id").loc[101]
    assert a.ret_min == pytest.approx(0.8)          # 600 + 200 of 1000
    assert a.tr_in_min == pytest.approx(0.3)        # athlete 5's 300 minutes, per own last-season minutes
    assert a.tr_in_n == 1
    assert a.tr_in_src_off == pytest.approx(-3.0)   # came from team 202
    assert a.rec_n == 1 and a.rec_score == pytest.approx(1.0)
    b = df.set_index("team_id").loc[202]
    assert b.ret_min == pytest.approx(0.7)
    assert b.rec_score == pytest.approx(0.0)
