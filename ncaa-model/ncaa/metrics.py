"""Scoring rules, calibration, betting math. Pure functions, unit-tested."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

EPS = 1e-6


def brier(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def log_loss(p, y):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_table(p, y, bins=10) -> pd.DataFrame:
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    b = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    df = pd.DataFrame({"bin": b, "p": p, "y": y})
    t = df.groupby("bin").agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")).reset_index()
    t["range"] = [f"{edges[i]:.1f}-{edges[i + 1]:.1f}" for i in t.bin]
    se = np.sqrt(t.observed * (1 - t.observed) / t.n.clip(lower=1))
    t["obs_95ci"] = [f"±{1.96 * s:.3f}" for s in se]
    return t[["range", "n", "predicted", "observed", "obs_95ci"]]


def bootstrap_ci(values, stat=np.mean, n=1000, seed=0, alpha=0.05):
    v = np.asarray(values, float)
    if len(v) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    s = [stat(v[rng.integers(0, len(v), len(v))]) for _ in range(n)]
    return float(np.quantile(s, alpha / 2)), float(np.quantile(s, 1 - alpha / 2))


# ------------------------------------------------------------------ odds math
def american_to_decimal(a):
    a = np.asarray(a, float)
    return np.where(a > 0, 1 + a / 100.0, 1 + 100.0 / np.abs(a))


def implied_prob(a):
    return 1.0 / american_to_decimal(a)


def devig_two_way(a_home, a_away):
    """Multiplicative de-vig of a two-way market -> (p_home, p_away)."""
    ph, pa = implied_prob(a_home), implied_prob(a_away)
    s = ph + pa
    return ph / s, pa / s


def ev_per_unit(p_win, decimal_odds, p_push=0.0):
    """EV per 1u staked. Push returns the stake (zero profit).
    EV = P(win)(d-1) - P(loss), with P(loss) = 1 - P(win) - P(push)."""
    p_win = np.asarray(p_win, float)
    p_loss = 1.0 - p_win - np.asarray(p_push, float)
    return p_win * (np.asarray(decimal_odds, float) - 1.0) - p_loss


def cover_probs(pred_margin, home_spread, sigma):
    """P(home covers), P(push), P(away covers) for home line `home_spread`
    (home -7 => home must win by > 7). Margin ~ Normal(pred, sigma); on an
    integer line the push mass is the probability the margin lands on it,
    approximated by the continuity band [line-0.5, line+0.5]."""
    pred_margin, home_spread = np.asarray(pred_margin, float), np.asarray(home_spread, float)
    need = -home_spread  # home margin required
    integer = np.isclose(need, np.round(need))
    up = np.where(integer, need + 0.5, need)
    lo = np.where(integer, need - 0.5, need)
    p_home = 1 - norm.cdf((up - pred_margin) / sigma)
    p_away = norm.cdf((lo - pred_margin) / sigma)
    return p_home, 1 - p_home - p_away, p_away


def over_probs(pred_total, line, sigma):
    pred_total, line = np.asarray(pred_total, float), np.asarray(line, float)
    integer = np.isclose(line, np.round(line))
    up = np.where(integer, line + 0.5, line)
    lo = np.where(integer, line - 0.5, line)
    p_over = 1 - norm.cdf((up - pred_total) / sigma)
    p_under = norm.cdf((lo - pred_total) / sigma)
    return p_over, 1 - p_over - p_under, p_under


def max_drawdown(pnl):
    c = np.cumsum(np.asarray(pnl, float))
    peak = np.maximum.accumulate(np.r_[0.0, c])[1:]
    return float(np.max(peak - c)) if len(c) else 0.0
