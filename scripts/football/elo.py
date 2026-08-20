#!/usr/bin/env python3
"""
Open Ledger Sports — NFL Elo, fitted market-blind (fb-v0.1).

Elo is the first of the two rating families in the pre-registration. It is
fitted on football results ONLY. No sportsbook number is read at any point:
the market columns were physically removed from games.csv at ingest, so this
script could not read a spread if it tried.

THE GRID IS PRE-REGISTERED. It is the GRID constant below, declared in code
before any of it was run, and it is deliberately small and round-numbered.
Widening it after seeing results, or adding a parameter because the first pass
disappointed, is not tuning - it is fitting the search to the answer. That
change ships as fb-v0.2 with its own clean test season.

THE SPLIT, and why the headline number is the one from data the search never saw:
    2010-2014  burn-in     builds ratings, never scored
    2015-2021  tune        the grid is scored here, and the winner picked here
    2022-2024  validate    the winner is scored here, ONCE, having never
                           influenced the selection - this is the honest
                           pre-holdout estimate
    2025       holdout     locked by asof.py until the prereg is frozen
Reporting the best-on-tune score as if it were out-of-sample is the single most
common way a rating model flatters itself. Both numbers are printed, labelled.

EVERY prediction is made through asof.walk, so the rating for a game contains
exactly those results that were available at that game's T-24 - which correctly
lets Thursday night inform Sunday, and correctly refuses to let the Sunday 1pm
games inform the Sunday 4:25pm games.

Run: python scripts/football/elo.py [--quick]
"""
import argparse, itertools, json, math, os, sys
from datetime import timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asof                                        # noqa: E402
from teams import canonical                        # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUT = os.path.join(ROOT, "data", "football", "elo_grid.json")

BURN_IN = list(range(2010, 2015))
TUNE = list(range(2015, 2022))
VALIDATE = list(range(2022, 2025))

MEAN = 1500.0

# --- THE PRE-REGISTERED GRID (declared before it was run) -------------------
GRID = {
    "k":     [15, 20, 25, 32],        # update size
    "hfa":   [30, 45, 55, 65],        # home-field advantage, Elo points
    "carry": [0.25, 0.50, 0.75, 1.00],  # season carryover; 1.00 = no regression
    "mov":   [False, True],           # margin-of-victory multiplier
}
# 4 x 4 x 4 x 2 = 128 combinations.


def expected(home_r, away_r, hfa):
    return 1.0 / (1.0 + 10 ** (-((home_r + hfa) - away_r) / 400.0))


def mov_multiplier(margin, elo_diff_winner):
    """538's NFL form. Dampens blowouts by the favourite, which otherwise let a
    good team run away with the rating on scoreline noise."""
    return (math.log(abs(margin) + 1.0)
            * (2.2 / (elo_diff_winner * 0.001 + 2.2)))


class Elo:
    def __init__(self, k, hfa, carry, mov):
        self.k, self.hfa, self.carry, self.mov = k, hfa, carry, mov
        self.r = {}
        self.season = None

    def rating(self, team):
        return self.r.setdefault(team, MEAN)

    def _roll_season(self, season):
        if self.season is None:
            self.season = season
            return
        if season != self.season:
            # Regress toward the mean once per new season, for every team.
            for t in self.r:
                self.r[t] = MEAN + self.carry * (self.r[t] - MEAN)
            self.season = season

    def predict(self, g):
        self._roll_season(g["_season"])
        h = canonical(g["home_team"])
        a = canonical(g["away_team"])
        return expected(self.rating(h), self.rating(a), self.hfa)

    def update(self, g):
        self._roll_season(g["_season"])
        h = canonical(g["home_team"])
        a = canonical(g["away_team"])
        p = expected(self.rating(h), self.rating(a), self.hfa)
        margin = g["_margin"]
        actual = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)

        k = self.k
        if self.mov and margin != 0:
            # elo_diff from the WINNER's perspective, including HFA
            diff = (self.rating(h) + self.hfa) - self.rating(a)
            if margin < 0:
                diff = -diff
            k = self.k * mov_multiplier(margin, diff)

        delta = k * (actual - p)
        self.r[h] = self.rating(h) + delta
        self.r[a] = self.rating(a) - delta


def score(games, params, score_seasons):
    """Walk-forward once; score only the seasons asked for."""
    model = Elo(**params)
    wanted = set(score_seasons)
    rows = []

    def predict(g):
        p = model.predict(g)
        if g["_season"] in wanted:
            margin = g["_margin"]
            y = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
            rows.append((p, y, g["_season"]))
        return None

    asof.walk(games, model.update, predict)

    if not rows:
        return None
    eps = 1e-15
    n = len(rows)
    ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
              for p, y, _ in rows) / n
    brier = sum((p - y) ** 2 for p, y, _ in rows) / n
    # Ties count as half-right, consistent with y = 0.5.
    acc = sum(1.0 if (p > 0.5) == (y > 0.5) else (0.5 if y == 0.5 else 0.0)
              for p, y, _ in rows) / n
    per_season = {}
    for s in sorted(wanted):
        sr = [(p, y) for p, y, ss in rows if ss == s]
        if sr:
            per_season[s] = round(
                -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
                     for p, y in sr) / len(sr), 4)
    return {"log_loss": round(ll, 5), "brier": round(brier, 5),
            "accuracy": round(acc, 4), "n": n, "per_season_log_loss": per_season,
            "final_ratings": {t: round(v, 1) for t, v in sorted(model.r.items())}}


def baselines(games, score_seasons):
    """What the model has to beat to be worth anything."""
    wanted = set(score_seasons)
    ys = []
    for g in games:
        if g["_season"] in wanted:
            m = g["_margin"]
            ys.append(1.0 if m > 0 else (0.0 if m < 0 else 0.5))
    n = len(ys)
    eps = 1e-15
    out = {}
    for name, p in (("coin_flip", 0.5), ("always_home", sum(ys) / n)):
        ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
                  for y in ys) / n
        out[name] = {"p": round(p, 4), "log_loss": round(ll, 5),
                     "brier": round(sum((p - y) ** 2 for y in ys) / n, 5)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="k and hfa only, carry=0.75 mov=True (smoke test)")
    args = ap.parse_args()

    seasons = BURN_IN + TUNE + VALIDATE
    games = asof.load_games(seasons=seasons, purpose="elo fit")
    print(f"loaded {len(games):,} played games  "
          f"burn-in {BURN_IN[0]}-{BURN_IN[-1]}, tune {TUNE[0]}-{TUNE[-1]}, "
          f"validate {VALIDATE[0]}-{VALIDATE[-1]}")
    print(f"holdout {asof.HOLDOUT} is not loaded at all "
          f"(prereg frozen: {asof.frozen_date() or 'NOT YET'})\n")

    grid = dict(GRID)
    if args.quick:
        grid = {"k": GRID["k"], "hfa": GRID["hfa"], "carry": [0.75], "mov": [True]}

    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    print(f"scoring {len(combos)} pre-registered combinations on the TUNE seasons")

    results = []
    for i, params in enumerate(combos, 1):
        s = score(games, params, TUNE)
        results.append({"params": params, "tune": {k: v for k, v in s.items()
                                                   if k != "final_ratings"}})
        if i % 16 == 0 or i == len(combos):
            print(f"  {i}/{len(combos)}")

    results.sort(key=lambda r: r["tune"]["log_loss"])
    best = results[0]
    print("\ntop 5 on TUNE (selection happens here, so these are IN-SAMPLE "
          "for the search):")
    print(f"{'k':>3} {'hfa':>4} {'carry':>6} {'mov':>6}  {'log loss':>9} "
          f"{'brier':>7} {'acc':>6}")
    for r in results[:5]:
        p, t = r["params"], r["tune"]
        print(f"{p['k']:>3} {p['hfa']:>4} {p['carry']:>6} {str(p['mov']):>6}  "
              f"{t['log_loss']:>9.5f} {t['brier']:>7.5f} {t['accuracy']:>6.3f}")

    # The honest number: the winner, scored once on seasons the search never saw.
    val = score(games, best["params"], VALIDATE)
    base_t = baselines(games, TUNE)
    base_v = baselines(games, VALIDATE)

    print(f"\nselected: {best['params']}")
    print(f"\n{'':<14} {'log loss':>9} {'brier':>8} {'acc':>7}   n")
    print(f"{'TUNE (in-sample for the search)':<14}")
    print(f"  {'elo':<12} {best['tune']['log_loss']:>9.5f} "
          f"{best['tune']['brier']:>8.5f} {best['tune']['accuracy']:>7.3f}   "
          f"{best['tune']['n']}")
    print(f"  {'always_home':<12} {base_t['always_home']['log_loss']:>9.5f} "
          f"{base_t['always_home']['brier']:>8.5f}")
    print(f"  {'coin_flip':<12} {base_t['coin_flip']['log_loss']:>9.5f} "
          f"{base_t['coin_flip']['brier']:>8.5f}")
    print(f"{'VALIDATE (never influenced selection - the honest one)':<14}")
    print(f"  {'elo':<12} {val['log_loss']:>9.5f} {val['brier']:>8.5f} "
          f"{val['accuracy']:>7.3f}   {val['n']}")
    print(f"  {'always_home':<12} {base_v['always_home']['log_loss']:>9.5f} "
          f"{base_v['always_home']['brier']:>8.5f}")
    print(f"  {'coin_flip':<12} {base_v['coin_flip']['log_loss']:>9.5f} "
          f"{base_v['coin_flip']['brier']:>8.5f}")

    lift = base_v["always_home"]["log_loss"] - val["log_loss"]
    print(f"\nvs always-home on VALIDATE: {lift:+.5f} log loss "
          f"({'better' if lift > 0 else 'WORSE'})")
    print("per-season log loss on VALIDATE: " +
          ", ".join(f"{s}: {v}" for s, v in val["per_season_log_loss"].items()))

    print("\nfinal ratings after the walk (top 8 / bottom 4):")
    fr = sorted(val["final_ratings"].items(), key=lambda x: -x[1])
    for t, v in fr[:8]:
        print(f"  {t:<4} {v:>7.1f}")
    print("  ...")
    for t, v in fr[-4:]:
        print(f"  {t:<4} {v:>7.1f}")

    payload = {
        "_note": ("Elo grid search, market-blind. The grid was pre-registered in "
                  "elo.py before being run. TUNE selects; VALIDATE is scored once "
                  "with the winner and is the honest pre-holdout estimate. The "
                  "holdout season was never loaded."),
        "grid": grid,
        "split": {"burn_in": BURN_IN, "tune": TUNE, "validate": VALIDATE,
                  "holdout": asof.HOLDOUT, "holdout_loaded": False},
        "prereg_frozen": asof.frozen_date(),
        "selected": best["params"],
        "tune": best["tune"],
        "validate": {k: v for k, v in val.items() if k != "final_ratings"},
        "baselines": {"tune": base_t, "validate": base_v},
        "final_ratings": val["final_ratings"],
        "all_results": results,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    print(f"\nwrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
