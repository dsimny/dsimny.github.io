#!/usr/bin/env python3
"""
D.J. Mercer NFL Developmental Cohort v1 - historical DEVELOPMENT evaluation.

Answers one question before anything is registered: does a small, market-anchored
NFL moneyline model do anything on the seasons we are allowed to look at?

    p_model = sigmoid( logit(p_market) + w * (logit(p_rating) - logit(p_market)) )

p_market  median-price, proportionally de-vigged Tier-1 consensus at T-24
          (price_test.eligible_h2h / consensus_fair - the same maths as fb-v0.2)
p_rating  candidate A: frozen fb-v0.1 Elo (elo_grid.json, tuned 2015-21)
          candidate B: frozen fb-v0.1 game model (Elo + opponent-adjusted EPA
          ridge, fitted on 2015-21 only), P(home win | no tie)
w         shrink weight toward the rating, chosen on TRAIN only from a fixed grid

SEASONS - read this before quoting any number from the output
  2010-2014  burn-in for ratings only
  2015-2021  rating/model tuning (fb-v0.1, frozen). No timestamped odds exist.
  2022-2023  TRAIN for the market blend and the selection rule
  2024       VALIDATION
  2025       UNTOUCHED TEST - NOT RUN HERE. asof.load_games refuses 2025 unless
             the one-shot holdout is claimed, and this script also refuses any
             odds snapshot requested on or after 2025-03-01. Claiming it needs
             Daniel's explicit approval (docs/MERCER_DEVELOPMENTAL_PROTOCOL.md).

2022-2024 HAVE BEEN SCORED BEFORE (fb-v0.1 model-vs-market, fb-v0.2 price test,
fp-v0.4 rule characterisation). Nothing computed on them here may be called an
untouched or out-of-sample result. They are development evidence only.

The selection rule is FIXED IN ADVANCE below, not tuned on ROI. A sensitivity
table is printed for transparency and is never used to choose the rule.

Writes data/mercer_dev/research/nfl_v1_dev_eval.json (aggregates only).
No network, no credits, no Discord, no ledger.
"""
import argparse, io, json, math, os, random, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
FB_SCRIPTS = os.path.join(ROOT, "scripts", "football")
sys.path.insert(0, FB_SCRIPTS)

import asof                                         # noqa: E402
import elo as elo_mod                               # noqa: E402
import game_model as gm                             # noqa: E402
import ridge as ridge_mod                           # noqa: E402
import market_compare as mc                         # noqa: E402
import price_test as pt                             # noqa: E402
from teams import canonical                         # noqa: E402

OUT = os.path.join(ROOT, "data", "mercer_dev", "research", "nfl_v1_dev_eval.json")
HIST = os.path.join(ROOT, "data", "football", "odds", "hist")
HIST_INDEX = os.path.join(ROOT, "data", "football", "odds", "hist_index.json")

TRAIN = (2022, 2023)
VALIDATE = (2024,)
SNAPSHOT_CUTOFF = "2025-03-01T00:00:00Z"   # nothing from the restricted season
W_GRID = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50)

# The v1 selection rule, fixed before any result below was computed.
RULE = {
    "market": "moneyline",
    "min_edge_pts": 2.0,          # p_model - implied(best Tier-1 price), percentage points
    "max_divergence_pts": 4.0,    # |p_model - p_market| above this -> HOLD, never a play
    "min_fair": 0.25,             # no side below 25% or above 75% consensus probability
    "max_fair": 0.75,
    "min_price": -300,            # American odds window for the price taken
    "max_price": 300,
    "min_books": pt.MIN_BOOKS,    # eligible Tier-1 books in consensus
    "min_books_at_best": 2,       # corroboration: best price not a lone outlier
    "max_plays_per_week": 3,      # highest edge first; ties -> kickoff, then game id
    "stake_units": 1.0,           # PAPER basis for evaluation; live staking is 0u
}


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def blend(p_mkt, p_rating, w):
    return sigmoid(logit(p_mkt) + w * (logit(p_rating) - logit(p_mkt)))


def payout(american):
    a = float(american)
    return a / 100.0 if a > 0 else 100.0 / -a


# --- data ---------------------------------------------------------------------

def load_snapshots():
    snaps = {}
    for name in sorted(os.listdir(HIST)):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(HIST, name), encoding="utf-8") as f:
            s = json.load(f)
        req = s.get("requested_utc", "")
        if req >= SNAPSHOT_CUTOFF:
            continue            # restricted-season prices are never loaded
        snaps[req] = s
    return snaps


def close_slot_by_game():
    with io.open(HIST_INDEX, encoding="utf-8") as f:
        idx = json.load(f)
    out = {}
    for slot, ids in idx.get("close_slots", {}).items():
        if slot >= SNAPSHOT_CUTOFF:
            continue
        for gid in ids:
            out[gid] = slot
    return out


def market_for(snap, g):
    """Tier-1 consensus + best prices for one game in one snapshot, or None."""
    if snap is None:
        return None
    away, home = canonical(g["away_team"]), canonical(g["home_team"])
    quotes, seen, stale = pt.eligible_h2h(snap, away, home, pt.TIER1)
    fair = pt.consensus_fair(quotes, home, away)
    if not fair:
        return None
    bh, bh_book, bh_near = pt.best_price(quotes, home)
    ba, ba_book, ba_near = pt.best_price(quotes, away)
    return {"p_home": fair[home], "overround": fair["_overround"], "n_books": len(quotes),
            "best": {"home": (bh, bh_book, bh_near), "away": (ba, ba_book, ba_near)},
            "stale": stale}


def build_rows():
    seasons = list(range(2010, 2025))
    games = asof.load_games(seasons=seasons, purpose="mercer-dev nfl v1 development")
    # Ratings walk over every played game (playoffs included), exactly as the
    # frozen elo.py / game_model.py fits did. Only REG games are scored below.
    if any(g["_season"] >= 2025 for g in games):
        raise SystemExit("restricted season reached the development loader")

    with io.open(gm.ELO_FIT, encoding="utf-8") as f:
        elo_p = json.load(f)["selected"]
    with io.open(gm.RIDGE_FIT, encoding="utf-8") as f:
        ridge_p = json.load(f)["selected"]

    # Candidate A: Elo win probability at each game's own T-24.
    model = elo_mod.Elo(**elo_p)
    p_elo = {}

    def predict(g):
        p_elo[g["game_id"]] = model.predict(g)
        return None

    asof.walk(games, model.update, predict)

    # Candidate B: frozen fb-v0.1 game model, fitted on 2015-21 only.
    obs = ridge_mod.build(games, ridge_mod.load_efficiency())
    ef = gm.elo_features(games, elo_p)
    rf = gm.ridge_features(games, obs, ridge_p)
    tune_rows = gm.assemble(games, ef, rf, gm.TUNE)
    mfit = gm.fit_linear(tune_rows, ["elo_diff", "ridge_edge"], "margin")
    prof = gm.key_profile(tune_rows, gm.MARGIN_RANGE, "margin", mfit["resid_sd"])
    target = [s for s in (TRAIN + VALIDATE)]
    gm_rows = gm.assemble(games, ef, rf, target)
    mpred = gm.apply_linear(mfit, gm_rows, ["elo_diff", "ridge_edge"])
    p_gm = {}
    for r, pm_ in zip(gm_rows, mpred):
        ph, ptie, _, _ = mc.model_probs(pm_, 45.0, mfit["resid_sd"], 10.0, prof, None, None)
        p_gm[r["game_id"]] = ph / (1 - ptie) if ptie < 1 else None

    snaps = load_snapshots()
    close_slot = close_slot_by_game()
    rows, unmatched = [], {"no_t24_snapshot_or_market": 0, "no_rating": 0}
    for g in games:
        if g["_season"] not in target or g["game_type"] != "REG":
            continue
        slot = g["_t24"].replace(minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        m = market_for(snaps.get(slot), g)
        if m is None:
            unmatched["no_t24_snapshot_or_market"] += 1
            continue
        if g["game_id"] not in p_elo or p_gm.get(g["game_id"]) is None:
            unmatched["no_rating"] += 1
            continue
        cs = close_slot.get(g["game_id"])
        mclose = market_for(snaps.get(cs), g) if cs else None
        rows.append({
            "game_id": g["game_id"], "season": g["_season"], "week": int(g["week"]),
            "kickoff": g["kickoff_utc"], "margin": g["_margin"],
            "p_mkt": m["p_home"], "p_close": mclose["p_home"] if mclose else None,
            "p_elo": p_elo[g["game_id"]], "p_gm": p_gm[g["game_id"]],
            "best": m["best"], "n_books": m["n_books"],
            "rest_diff": _int(g.get("home_rest")) - _int(g.get("away_rest")),
        })
    return rows, unmatched


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


# --- scoring ------------------------------------------------------------------

def prob_metrics(rows, key):
    rs = [r for r in rows if r["margin"] != 0]
    n = len(rs)
    if not n:
        return None
    eps = 1e-12
    ys = [1.0 if r["margin"] > 0 else 0.0 for r in rs]
    ps = [r[key] for r in rs]
    ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
              for p, y in zip(ps, ys)) / n
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    acc = sum(1 for p, y in zip(ps, ys) if (p > 0.5) == (y > 0.5)) / n
    buckets = []
    for lo in [i / 10 for i in range(10)]:
        b = [(p, y) for p, y in zip(ps, ys) if lo <= p < lo + 0.1 or (lo == 0.9 and p == 1.0)]
        if b:
            buckets.append({"bucket": f"{lo:.1f}-{lo + 0.1:.1f}", "n": len(b),
                            "mean_p": round(sum(p for p, _ in b) / len(b), 4),
                            "win_rate": round(sum(y for _, y in b) / len(b), 4)})
    return {"n": n, "log_loss": round(ll, 5), "brier": round(brier, 5),
            "accuracy": round(acc, 4), "calibration": buckets}


def with_model(rows, cand, w):
    out = []
    for r in rows:
        rr = dict(r)
        rr["p_model"] = blend(r["p_mkt"], r["p_elo" if cand == "A" else "p_gm"], w)
        out.append(rr)
    return out


def select(rows, rule=RULE, key="p_model"):
    """Apply the fixed v1 rule. Returns (plays, holds, passes)."""
    cands, holds = [], 0
    for r in rows:
        for side in ("home", "away"):
            p = r[key] if side == "home" else 1 - r[key]
            pm = r["p_mkt"] if side == "home" else 1 - r["p_mkt"]
            price, book, near = r["best"][side]
            if price is None:
                continue
            if not (rule["min_fair"] <= pm <= rule["max_fair"]):
                continue
            if not (rule["min_price"] <= price <= rule["max_price"]):
                continue
            if r["n_books"] < rule["min_books"] or near < rule["min_books_at_best"]:
                continue
            edge = (p - pt.implied(price)) * 100
            if edge < rule["min_edge_pts"]:
                continue
            if abs(p - pm) * 100 > rule["max_divergence_pts"]:
                holds += 1
                continue
            cands.append({"row": r, "side": side, "price": price, "book": book,
                          "edge": edge, "p": p, "pm": pm})
    weeks = {}
    for c in cands:
        weeks.setdefault((c["row"]["season"], c["row"]["week"]), []).append(c)
    plays = []
    for wk in sorted(weeks):
        ranked = sorted(weeks[wk], key=lambda c: (-c["edge"], c["row"]["kickoff"],
                                                  c["row"]["game_id"], c["side"]))
        taken_games = set()
        for c in ranked:
            if c["row"]["game_id"] in taken_games:
                continue          # never both sides of one game
            plays.append(c)
            taken_games.add(c["row"]["game_id"])
            if len(taken_games) >= rule["max_plays_per_week"]:
                break
    return plays, holds


def settle(plays):
    out = []
    for c in sorted(plays, key=lambda c: (c["row"]["kickoff"], c["row"]["game_id"])):
        m = c["row"]["margin"]
        if m == 0:
            res, pnl = "PUSH", 0.0
        else:
            won = (m > 0) == (c["side"] == "home")
            res, pnl = ("W", payout(c["price"])) if won else ("L", -1.0)
        clv = None
        if c["row"]["p_close"] is not None:
            pc = c["row"]["p_close"] if c["side"] == "home" else 1 - c["row"]["p_close"]
            clv = (pc - pt.implied(c["price"])) * 100      # price-taken CLV, pts
        out.append({"season": c["row"]["season"], "res": res, "pnl": pnl,
                    "edge": c["edge"], "clv": clv, "price": c["price"]})
    return out


def bet_metrics(settled):
    n = len(settled)
    if not n:
        return {"n": 0}
    w = sum(1 for s in settled if s["res"] == "W")
    l = sum(1 for s in settled if s["res"] == "L")
    pnl = sum(s["pnl"] for s in settled)
    eq, peak, dd, streak, worst = 0.0, 0.0, 0.0, 0, 0
    for s in settled:
        eq += s["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if s["res"] == "L" else 0
        worst = max(worst, streak)
    clvs = [s["clv"] for s in settled if s["clv"] is not None]
    mean = pnl / n
    sd = math.sqrt(sum((s["pnl"] - mean) ** 2 for s in settled) / max(n - 1, 1))
    be = sum(pt.implied(s["price"]) for s in settled) / n
    return {"n": n, "W": w, "L": l, "push": n - w - l,
            "win_rate": round(w / max(w + l, 1), 4),
            "breakeven_win_rate": round(be, 4),
            "units": round(pnl, 3), "roi_pct": round(100 * mean, 2),
            "roi_95ci_pct": [round(100 * (mean - 1.96 * sd / math.sqrt(n)), 1),
                             round(100 * (mean + 1.96 * sd / math.sqrt(n)), 1)],
            "max_drawdown_u": round(dd, 3), "longest_losing_streak": worst,
            "clv_n": len(clvs),
            "clv_mean_pts": round(sum(clvs) / len(clvs), 3) if clvs else None,
            "beat_close_pct": round(100 * sum(1 for c in clvs if c > 0) / len(clvs), 1)
            if clvs else None}


def by(settled, keyfn):
    groups = {}
    for s in settled:
        groups.setdefault(keyfn(s), []).append(s)
    return {k: bet_metrics(v) for k, v in sorted(groups.items())}


def edge_bucket(s):
    e = s["edge"]
    return "2-3" if e < 3 else ("3-4" if e < 4 else ("4-6" if e < 6 else "6+"))


def random_side_baseline(rows, n_target, seed=20260914):
    """Same eligible games, random side, same count: proves the machinery."""
    rng = random.Random(seed)
    pool = [r for r in rows if RULE["min_fair"] <= r["p_mkt"] <= RULE["max_fair"]]
    rng.shuffle(pool)
    plays = []
    for r in pool[:n_target]:
        side = rng.choice(("home", "away"))
        price, book, _ = r["best"][side]
        if price is None:
            continue
        plays.append({"row": r, "side": side, "price": price, "book": book,
                      "edge": 0.0, "p": 0.5, "pm": 0.5})
    return bet_metrics(settle(plays))


def run():
    rows, unmatched = build_rows()
    train = [r for r in rows if r["season"] in TRAIN]
    val = [r for r in rows if r["season"] in VALIDATE]
    report = {"_note": __doc__.split("\n\n")[0].strip(),
              "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "seasons": {"train": list(TRAIN), "validation": list(VALIDATE),
                          "untouched_test": "2025 NOT RUN - requires holdout claim approval",
                          "contamination": "2022-2024 previously scored by fb-v0.1, "
                                           "fb-v0.2 and fp-v0.4; development evidence only"},
              "rule": RULE, "unmatched": unmatched,
              "n_rows": {"train": len(train), "validation": len(val)}}

    # 1. Probability layer. w chosen on TRAIN log loss only.
    report["market_only"] = {"train": prob_metrics(train, "p_mkt"),
                             "validation": prob_metrics(val, "p_mkt")}
    report["rating_only"] = {
        c: {"train": prob_metrics(train, k), "validation": prob_metrics(val, k)}
        for c, k in (("A_elo", "p_elo"), ("B_game_model", "p_gm"))}
    grid = {}
    for cand in ("A", "B"):
        grid[cand] = {str(w): prob_metrics(with_model(train, cand, w), "p_model")["log_loss"]
                      for w in W_GRID}
    report["train_w_grid_log_loss"] = grid
    chosen = min(((grid[c][str(w)], c, w) for c in grid for w in W_GRID),
                 key=lambda t: (round(t[0], 6), t[2], t[1]))
    _, cand, w = chosen
    report["chosen_on_train"] = {"candidate": cand, "w": w,
                                 "tie_break": "lowest train log loss, then smaller w, then A"}
    report["chosen_model"] = {
        "train": prob_metrics(with_model(train, cand, w), "p_model"),
        "validation": prob_metrics(with_model(val, cand, w), "p_model")}

    # 2. Selection layer: the fixed rule, chosen model vs market-only (w = 0).
    for label, cw in (("chosen_model_rule", w), ("market_only_rule", 0.0)):
        block = {}
        for split, data in (("train", train), ("validation", val)):
            plays, holds = select(with_model(data, cand, cw))
            st = settle(plays)
            block[split] = {"bets": bet_metrics(st), "holds": holds,
                            "by_season": by(st, lambda s: s["season"]),
                            "by_edge_bucket": by(st, edge_bucket),
                            "by_price_side": by(st, lambda s: "favourite" if s["price"] < 0
                                                else "underdog")}
        report[label] = block
    report["random_side_baseline"] = {
        split: random_side_baseline(data, report["chosen_model_rule"][split]["bets"]["n"] or 50)
        for split, data in (("train", train), ("validation", val))}

    # 2b. Why the rule selects what it selects: with the chosen model, the edge
    # available at the best Tier-1 price across every side inside the odds window.
    def reachable(data):
        edges = []
        for r in with_model(data, cand, w):
            for side in ("home", "away"):
                price = r["best"][side][0]
                pm = r["p_mkt"] if side == "home" else 1 - r["p_mkt"]
                if price is None or not (RULE["min_fair"] <= pm <= RULE["max_fair"]):
                    continue
                p = r["p_model"] if side == "home" else 1 - r["p_model"]
                edges.append((p - pt.implied(price)) * 100)
        edges.sort()
        if not edges:
            return None
        q = lambda f: round(edges[min(len(edges) - 1, int(f * len(edges)))], 3)
        return {"sides": len(edges), "p50": q(0.5), "p90": q(0.9), "p99": q(0.99),
                "max": round(edges[-1], 3),
                "share_ge_1pt": round(sum(e >= 1 for e in edges) / len(edges), 4),
                "share_ge_2pt": round(sum(e >= 2 for e in edges) / len(edges), 4)}
    report["reachable_edge_at_best_price_pts"] = {"train": reachable(train),
                                                  "validation": reachable(val)}

    # 3. Sensitivity - printed, NEVER used to choose the rule.
    sens = {}
    for me in (1.0, 2.0, 3.0, 4.0):
        rule = dict(RULE, min_edge_pts=me)
        plays, _ = select(with_model(train, cand, w), rule)
        sens[str(me)] = bet_metrics(settle(plays))
    report["sensitivity_train_min_edge_NOT_USED_FOR_SELECTION"] = sens

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=1, sort_keys=True)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()
    r = run()
    mo, cm = r["market_only"], r["chosen_model"]
    print(f"rows train={r['n_rows']['train']} validation={r['n_rows']['validation']} "
          f"unmatched={r['unmatched']}")
    print(f"chosen on train: candidate {r['chosen_on_train']['candidate']} w={r['chosen_on_train']['w']}")
    for split in ("train", "validation"):
        print(f"  {split:<10} market LL {mo[split]['log_loss']:.5f} brier {mo[split]['brier']:.5f} | "
              f"model LL {cm[split]['log_loss']:.5f} brier {cm[split]['brier']:.5f}")
    for label in ("chosen_model_rule", "market_only_rule"):
        for split in ("train", "validation"):
            b = r[label][split]["bets"]
            print(f"  {label:<18} {split:<10} {b}")
    print(f"wrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
