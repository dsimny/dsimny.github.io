"""Data-integrity checks. Each check returns (name, passed, detail); the
report is written to reports/data_validation.md.

    python -m ncaa.validate

A failed check is reported, never silently "fixed". Rows that fail a
game-level check are listed so they can be excluded by game_id.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .ingest import load_games, load_odds


def checks(g: pd.DataFrame, odds: pd.DataFrame | None = None) -> list[tuple[str, bool, str]]:
    out = []

    def add(name, ok, detail):
        out.append((name, bool(ok), detail))

    dup = g.game_id.duplicated().sum()
    add("unique game_id", dup == 0, f"{dup} duplicates")

    same = (g.h_team_id == g.a_team_id).sum()
    add("home != away team", same == 0, f"{same} games")

    ties = (g.margin == 0).sum()
    add("no tied finals", ties == 0, f"{ties} ties")

    low = g[(g.h_pts < 20) | (g.a_pts < 20)]
    neg = low.d1_game.sum()
    add("plausible D1-vs-D1 scores (>=20 each side)", neg == 0,
        f"{neg} D1 games below 20; {len(low) - neg} non-D1 blowouts kept (real results)")

    mism = g[(g.home_score.notna()) & (g.home_score > 0) & ((g.h_pts != g.home_score) | (g.a_pts != g.away_score))]
    add("box score == schedule score", len(mism) == 0,
        f"{len(mism)} mismatches" + (f": {mism.game_id.tolist()[:10]}" if len(mism) else ""))
    zero = g[(g.home_score == 0) & (g.away_score == 0)]
    add("schedule score present", len(zero) <= 5,
        f"{len(zero)} games where the schedule shows 0-0 but the box score has a final "
        f"(box score used): {zero.game_id.tolist()[:10]}")

    miss = g[["h_field_goals_attempted", "a_field_goals_attempted", "h_tov", "a_tov",
              "h_offensive_rebounds", "a_offensive_rebounds", "h_free_throws_attempted",
              "a_free_throws_attempted"]].isna().any(axis=1).sum()
    add("four-factor inputs present", miss / len(g) < 0.005, f"{miss} games missing a box stat")

    p = g.poss
    bad = ((p < 50) | (p > 100)).sum()
    add("possessions in [50,100] (OT allowed to 100)", bad / len(g) < 0.002, f"{bad} games outside")
    gap = (g.h_poss_est - g.a_poss_est).abs()
    add("home/away possession estimates agree (median |gap| < 3)", gap.median() < 3,
        f"median {gap.median():.2f}, p99 {gap.quantile(.99):.2f}")

    ppp = 100 * g.h_pts / g.poss
    add("points per 100 poss in [50,160]", ((ppp < 50) | (ppp > 160)).mean() < 0.002,
        f"{((ppp < 50) | (ppp > 160)).sum()} outside")

    by = g.groupby("season")
    lo = by.size()
    full = lo.drop([2021, C.CURRENT_SEASON], errors="ignore")
    add("full seasons have >= 5,500 games", (full >= 5500).all(), lo.to_dict().__str__())

    order = g.game_date.is_monotonic_increasing
    add("chronologically sorted", order, "")

    d1 = g[g.d1_game]
    hw = (d1[~d1.neutral_site].margin > 0).mean()
    add("home win rate (non-neutral, D1) in [0.55,0.70]", 0.55 <= hw <= 0.70, f"{hw:.3f}")

    if odds is not None and not odds.empty:
        out.extend(odds_checks(g, odds))
    return out


def odds_checks(g, odds):
    out = []
    o = odds[odds.provider.isin(C.REAL_BOOK_PROVIDERS)].drop_duplicates("game_id")
    m = o.merge(g[["game_id", "margin", "total_pts", "season"]], on="game_id", suffixes=("", "_g"))
    m = m[m.spread.notna()]
    c = np.corrcoef(m.spread, m.margin)[0, 1] if len(m) > 10 else np.nan
    # spread is the home line: home -7 means home expected to win by 7, so
    # corr(spread, margin) must be strongly NEGATIVE.
    out.append(("odds: spread is the home line (corr(spread, margin) < -0.5)", c < -0.5, f"corr={c:.3f}, n={len(m)}"))
    mae = (m.margin + m.spread).abs().mean()
    out.append(("odds: closing-spread MAE in [7,10] pts", 7 <= mae <= 10, f"{mae:.2f}"))
    t = m[m.total.notna()]
    if len(t):
        tm = (t.total_pts - t.total).abs().mean()
        out.append(("odds: total MAE in [10,16] pts", 10 <= tm <= 16, f"{tm:.2f}, n={len(t)}"))
    cov = m.groupby("season").size().to_dict()
    out.append(("odds: coverage by season (real books only)", True, str(cov)))
    return out


def report(results) -> str:
    lines = ["# Data validation report", "", "| check | result | detail |", "|---|---|---|"]
    for n, ok, d in results:
        lines.append(f"| {n} | {'PASS' if ok else 'FAIL'} | {d} |")
    return "\n".join(lines) + "\n"


def main():
    g = load_games()
    res = checks(g, load_odds())
    txt = report(res)
    C.REPORTS.mkdir(exist_ok=True)
    (C.REPORTS / "data_validation.md").write_text(txt)
    print(txt)
    failed = [r for r in res if not r[1]]
    print(f"{len(res) - len(failed)}/{len(res)} checks passed")


if __name__ == "__main__":
    main()
