"""CBBD -> ESPN join: date +-1, score pair, orientation flip, ambiguity."""
import numpy as np
import pandas as pd

from ncaa.cbbd import join_to_espn


def _games():
    return pd.DataFrame(dict(game_id=[1, 2, 3, 4], game_date=pd.to_datetime(["2024-11-10"] * 3 + ["2024-11-11"]),
                             h_pts=[80.0, 70.0, 66.0, 66.0], a_pts=[70.0, 65.0, 60.0, 60.0]))


def test_join_exact_flip_and_ambiguous():
    lines = pd.DataFrame(dict(
        cbbd_game_id=[10, 11, 12],
        # 10: same orientation, late tip (UTC next day); 11: home/away reversed; 12: score 66-60 twice -> ambiguous
        start=["2024-11-11T02:00:00Z", "2024-11-10T18:00:00Z", "2024-11-10T18:00:00Z"],
        home_score=[80, 65, 66], away_score=[70, 70, 60], provider="Book",
        spread=[-7.0, 3.0, -4.0], spread_open=[-6.5, 2.5, -4.0], home_ml=[-300, 140, -180.0],
        away_ml=[240, -160, 150.0], total=[150, 140, 130.0]))
    m, st = join_to_espn(lines, _games())
    got = m.set_index("cbbd_game_id")
    assert set(got.index) == {10, 11}
    assert got.loc[10, "game_id"] == 1 and got.loc[10, "spread"] == -7.0
    # flipped: CBBD home (65) is ESPN's away -> spread and moneylines re-oriented
    assert got.loc[11, "game_id"] == 2 and got.loc[11, "spread"] == -3.0 and got.loc[11, "home_ml"] == -160
    assert st["flipped_games"] == 1 and st["matched_games"] == 2
