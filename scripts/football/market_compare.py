#!/usr/bin/env python3
"""
Open Ledger Sports — model vs market at T-24 (fb-v0.1).

The question the whole project was built to answer, and the first place a price
meets a model number.

    At the moment a wager would actually be placed - T-24, not the close - does
    a market-blind football model know anything the market does not?

The honest prior is no. The prior research on this framework found the market
beat every pure model in every season tested at the closing line. This measures
the same thing 24 hours earlier, where the market is thinner and the model has
its best chance.

HOW IT IS SCORED. Model and market are put side by side against the same
outcomes, on the same games, with the same metric. Neither gets a handicap:
  - moneyline: P(home wins) from the margin PMF vs the de-vigged consensus
  - spread:    P(home covers the market's own number) vs the de-vigged price
  - total:     P(over the market's own number) vs the de-vigged price
Scoring the model against the MARKET'S line, rather than a line of its own
choosing, is the point. A model that only looks good at numbers it picked is
not beating anything.

DE-VIG is proportional, and the consensus is the median across books of what was
actually OFFERED. No line is ever interpolated: if no book posted a number, that
number does not exist for our purposes.

PUSHES ARE EXCLUDED FROM THE HEAD-TO-HEAD, not counted as half. A push is not a
50/50 outcome, it is the absence of one, and both sides are compared on
P(cover | no push) so the vig cancels the same way for each.

SEASON HYGIENE. Seasons inside the TUNE window are IN-SAMPLE for the model and
are reported separately and labelled, never averaged into the headline. A model
scored on data it was fitted to will beat almost anything.

Run:
  python scripts/football/market_compare.py --selftest        # math only, no data
  python scripts/football/market_compare.py --seasons 2022-2024
"""
import argparse, glob, io, json, math, os, sys
from datetime import datetime, timedelta, timezone
from statistics import median

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asof                                        # noqa: E402
import game_model as gm                            # noqa: E402
from teams import canonical                        # noqa: E402
import ridge as ridge_mod                          # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
SNAPS = os.path.join(FB, "odds", "hist")
OUT = os.path.join(FB, "market_compare.json")

TUNE = set(gm.TUNE)
MATCH_TOLERANCE_H = 6      # snapshot commence_time vs our kickoff


# --- price maths ------------------------------------------------------------

def implied(american):
    o = float(american)
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def devig_pair(p_a, p_b):
    """Proportional de-vig of a two-way market."""
    s = p_a + p_b
    if s <= 0:
        return None, None
    return p_a / s, p_b / s


def consensus_two_way(books, market_key, side_a, side_b):
    """Median de-vigged P(side_a) across books that posted BOTH sides."""
    probs, points = [], []
    for bk in books:
        outs = bk.get("markets", {}).get(market_key)
        if not outs:
            continue
        a = next((o for o in outs if o.get("name") == side_a), None)
        b = next((o for o in outs if o.get("name") == side_b), None)
        if not a or not b or a.get("price") is None or b.get("price") is None:
            continue
        pa, _ = devig_pair(implied(a["price"]), implied(b["price"]))
        if pa is None:
            continue
        probs.append(pa)
        if a.get("point") is not None:
            points.append(float(a["point"]))
    if not probs:
        return None, None, 0
    return median(probs), (median(points) if points else None), len(probs)


# --- model probabilities at the market's number -----------------------------

def model_probs(pred_margin, pred_total, sd_m, sd_t, prof, spread_point, total_point):
    """P(home wins), P(home covers | no push), P(over | no push)."""
    vals = list(gm.MARGIN_RANGE)
    p = gm.pmf(pred_margin, sd_m, vals, prof)
    zero = vals.index(0)
    p_home = float(p[zero + 1:].sum())
    p_tie = float(p[zero])

    cover = None
    if spread_point is not None:
        # Home covers when margin + point > 0; pushes when margin + point == 0.
        need = -spread_point
        if abs(need - round(need)) < 1e-9 and int(round(need)) in vals:
            push = float(p[vals.index(int(round(need)))])
        else:
            push = 0.0
        win = float(sum(pv for v, pv in zip(vals, p) if v > need))
        cover = win / (1.0 - push) if push < 1.0 else None

    over = None
    if total_point is not None:
        tvals = list(gm.TOTAL_RANGE)
        pt = gm.pmf(pred_total, sd_t, tvals)
        push = float(pt[tvals.index(int(round(total_point)))]) \
            if abs(total_point - round(total_point)) < 1e-9 and \
            int(round(total_point)) in tvals else 0.0
        win = float(sum(pv for v, pv in zip(tvals, pt) if v > total_point))
        over = win / (1.0 - push) if push < 1.0 else None

    return p_home, p_tie, cover, over


# --- scoring ----------------------------------------------------------------

def head_to_head(rows, mkey, kkey):
    """Log loss and Brier for model and market on the same outcomes."""
    eps = 1e-15
    pm = np.array([r[mkey] for r in rows])
    pk = np.array([r[kkey] for r in rows])
    y = np.array([r["y"] for r in rows])

    def sc(p):
        p = np.clip(p, eps, 1 - eps)
        return (float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
                float(np.mean((p - y) ** 2)),
                float(np.mean((p > 0.5) == (y > 0.5))))

    mll, mb, ma = sc(pm)
    kll, kb, ka = sc(pk)
    return {"n": len(rows),
            "model": {"log_loss": round(mll, 5), "brier": round(mb, 5), "acc": round(ma, 4)},
            "market": {"log_loss": round(kll, 5), "brier": round(kb, 5), "acc": round(ka, 4)},
            "model_minus_market_log_loss": round(mll - kll, 5),
            "model_beats_market": bool(mll < kll)}


# --- self-test (no data, no network) ----------------------------------------

def selftest():
    fails = []
    # de-vig: a fair -110/-110 market must come back 50/50 with the vig removed
    pa, pb = devig_pair(implied(-110), implied(-110))
    if abs(pa - 0.5) > 1e-9 or abs(pa + pb - 1.0) > 1e-9:
        fails.append(f"de-vig of -110/-110 gave {pa}, {pb}")
    # a lopsided market still sums to 1
    pa, pb = devig_pair(implied(-300), implied(+250))
    if abs(pa + pb - 1.0) > 1e-9 or not (0.70 < pa < 0.78):
        fails.append(f"de-vig of -300/+250 gave {pa}, {pb}")
    if abs(implied(+100) - 0.5) > 1e-12:
        fails.append("implied(+100) != 0.5")

    # model probabilities: a pick'em should be near 50/50, and a whole-number
    # spread must produce a cover probability that EXCLUDES the push mass.
    prof = {v: 1.0 for v in gm.MARGIN_RANGE}
    ph, pt, cover, over = model_probs(0.0, 44.0, 13.0, 13.5, prof, 0.0, 44.0)
    if abs(ph - (1 - pt) / 2) > 0.02:
        fails.append(f"pick'em P(home)={ph:.4f} not ~half of the non-tie mass")
    if cover is None or abs(cover - 0.5) > 0.02:
        fails.append(f"pick'em cover={cover}")
    if over is None or abs(over - 0.5) > 0.05:
        fails.append(f"even total over={over}")

    # a 7-point home favourite must cover a -7 line less often than it wins
    ph7, _, cover7, _ = model_probs(7.0, 44.0, 13.0, 13.5, prof, -7.0, 44.0)
    if not (cover7 < ph7):
        fails.append(f"cover at -7 ({cover7:.3f}) should be below P(win) ({ph7:.3f})")

    # consensus: two books, one missing a side, must use only the complete one
    books = [{"markets": {"h2h": [{"name": "A", "price": -110},
                                  {"name": "B", "price": -110}]}},
             {"markets": {"h2h": [{"name": "A", "price": -150}]}}]
    p, pts, n = consensus_two_way(books, "h2h", "A", "B")
    if n != 1 or abs(p - 0.5) > 1e-9:
        fails.append(f"consensus used {n} books, p={p}")

    for f in fails:
        print("  FAIL", f)
    print(f"market_compare selftest: {'FAILED' if fails else 'PASS'} "
          f"({5 + 1 - len(fails)}/6 checks)")
    return 1 if fails else 0


# --- main -------------------------------------------------------------------

def load_snapshots():
    snaps = {}
    for path in sorted(glob.glob(os.path.join(SNAPS, "*.json"))):
        with io.open(path, encoding="utf-8") as f:
            s = json.load(f)
        snaps[s["requested_utc"]] = s
    return snaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2022-2024")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    a, _, b = args.seasons.partition("-")
    target = list(range(int(a), int(b or a) + 1))

    snaps = load_snapshots()
    if not snaps:
        raise SystemExit(
            f"no snapshots in {os.path.relpath(SNAPS, ROOT)}. Run "
            "fetch_historical_odds.py first (start with --plan, then --probe).")
    print(f"{len(snaps)} snapshots on disk")

    # Model, fitted on TUNE only - exactly as frozen.
    fitseasons = gm.BURN_IN + gm.TUNE + gm.VALIDATE
    games = asof.load_games(seasons=sorted(set(fitseasons + target)),
                            purpose="market comparison")
    obs = ridge_mod.build(games, ridge_mod.load_efficiency())
    with io.open(gm.ELO_FIT, encoding="utf-8") as f:
        elo_p = json.load(f)["selected"]
    with io.open(gm.RIDGE_FIT, encoding="utf-8") as f:
        ridge_p = json.load(f)["selected"]
    ef = gm.elo_features(games, elo_p)
    rf = gm.ridge_features(games, obs, ridge_p)
    tune_rows = gm.assemble(games, ef, rf, gm.TUNE)
    mfit = gm.fit_linear(tune_rows, ["elo_diff", "ridge_edge"], "margin")
    tfit = gm.fit_linear(tune_rows, ["ridge_sum"], "total")
    prof = gm.key_profile(tune_rows, gm.MARGIN_RANGE, "margin", mfit["resid_sd"])

    rows = gm.assemble(games, ef, rf, target)
    by_id = {g["game_id"]: g for g in games}
    mpred = gm.apply_linear(mfit, rows, ["elo_diff", "ridge_edge"])
    tpred = gm.apply_linear(tfit, rows, ["ridge_sum"])

    ml, sp, tot, unmatched = [], [], [], 0
    for r, pm_, pt_ in zip(rows, mpred, tpred):
        g = by_id[r["game_id"]]
        slot = g["_t24"].replace(minute=0, second=0, microsecond=0)
        snap = snaps.get(slot.strftime("%Y-%m-%dT%H:%M:%SZ"))
        if snap is None:
            unmatched += 1
            continue
        ev = None
        ha, hh = canonical(g["away_team"]), canonical(g["home_team"])
        for e in snap["events"]:
            if e["away"] == ha and e["home"] == hh:
                ct = datetime.strptime(e["commence_time"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc) if e.get("commence_time") else None
                if ct is None or abs((ct - g["_kickoff"]).total_seconds()) <= MATCH_TOLERANCE_H * 3600:
                    ev = e
                    break
        if ev is None:
            unmatched += 1
            continue

        # Outcome names inside the payload are the API's full team names;
        # consensus_two_way_named resolves them through the same map as
        # everything else, and skips any book it cannot place.
        books = ev["books"]
        k_home, _, n_ml = consensus_two_way_named(books, "h2h", hh, ha)
        k_cover, spread_pt, n_sp = consensus_two_way_named(books, "spreads", hh, ha)
        k_over, total_pt, n_tt = consensus_totals(books)

        ph, ptie, cover, over = model_probs(pm_, pt_, mfit["resid_sd"],
                                            tfit["resid_sd"], prof,
                                            spread_pt, total_pt)
        season, margin, total = r["season"], g["_margin"], g["_total"]

        if k_home is not None and margin != 0:
            ml.append({"season": season, "m": ph / (1 - ptie), "k": k_home,
                       "y": 1.0 if margin > 0 else 0.0, "n_books": n_ml})
        if k_cover is not None and cover is not None and spread_pt is not None:
            adj = margin + spread_pt
            if abs(adj) > 1e-9:
                sp.append({"season": season, "m": cover, "k": k_cover,
                           "y": 1.0 if adj > 0 else 0.0, "n_books": n_sp})
        if k_over is not None and over is not None and total_pt is not None:
            if abs(total - total_pt) > 1e-9:
                tot.append({"season": season, "m": over, "k": k_over,
                            "y": 1.0 if total > total_pt else 0.0, "n_books": n_tt})

    print(f"matched: {len(ml)} moneyline, {len(sp)} spread, {len(tot)} total; "
          f"{unmatched} games without a usable snapshot")

    report = {}
    for name, data in (("moneyline", ml), ("spread", sp), ("total", tot)):
        if not data:
            report[name] = {"n": 0, "note": "no matched games"}
            continue
        oos = [r for r in data if r["season"] not in TUNE]
        ins = [r for r in data if r["season"] in TUNE]
        report[name] = {
            "out_of_sample": head_to_head(oos, "m", "k") if oos else None,
            "in_sample_LABELLED": head_to_head(ins, "m", "k") if ins else None,
            "per_season": {s: head_to_head([r for r in data if r["season"] == s], "m", "k")
                           for s in sorted({r["season"] for r in data})},
        }

    print(f"\n{'market':<11} {'n':>5} {'model LL':>9} {'market LL':>10} "
          f"{'diff':>8}  verdict")
    for name in ("moneyline", "spread", "total"):
        h = (report[name] or {}).get("out_of_sample")
        if not h:
            print(f"{name:<11} {'-':>5}  (no out-of-sample matches)")
            continue
        d = h["model_minus_market_log_loss"]
        print(f"{name:<11} {h['n']:>5} {h['model']['log_loss']:>9.5f} "
              f"{h['market']['log_loss']:>10.5f} {d:>+8.5f}  "
              f"{'MODEL BEATS MARKET' if d < 0 else 'market beats model'}")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "_note": ("Model vs market at T-24, scored on the market's own "
                      "numbers. Pushes excluded, not counted as half. Seasons "
                      "inside TUNE are in-sample for the model and reported "
                      "separately - never averaged into the headline."),
            "seasons": target, "tune_seasons_in_sample": sorted(TUNE & set(target)),
            "prereg_frozen": asof.frozen_date(), "report": report,
        }, f, indent=1, sort_keys=True)
    print(f"\nwrote {os.path.relpath(OUT, ROOT)}")
    return 0


def consensus_two_way_named(books, key, home, away):
    """Consensus where outcome names are full team names needing resolution."""
    from teams import from_name, UnknownTeam
    probs, points = [], []
    for bk in books:
        outs = bk.get("markets", {}).get(key)
        if not outs or len(outs) < 2:
            continue
        h = a = None
        for o in outs:
            try:
                t = from_name(o["name"], source="odds-api")
            except UnknownTeam:
                continue
            if t == home:
                h = o
            elif t == away:
                a = o
        if not h or not a or h.get("price") is None or a.get("price") is None:
            continue
        ph, _ = devig_pair(implied(h["price"]), implied(a["price"]))
        if ph is None:
            continue
        probs.append(ph)
        if h.get("point") is not None:
            points.append(float(h["point"]))
    if not probs:
        return None, None, 0
    return median(probs), (median(points) if points else None), len(probs)


def consensus_totals(books):
    probs, points = [], []
    for bk in books:
        outs = bk.get("markets", {}).get("totals")
        if not outs:
            continue
        o = next((x for x in outs if (x.get("name") or "").lower() == "over"), None)
        u = next((x for x in outs if (x.get("name") or "").lower() == "under"), None)
        if not o or not u or o.get("price") is None or u.get("price") is None:
            continue
        po, _ = devig_pair(implied(o["price"]), implied(u["price"]))
        if po is None:
            continue
        probs.append(po)
        if o.get("point") is not None:
            points.append(float(o["point"]))
    if not probs:
        return None, None, 0
    return median(probs), (median(points) if points else None), len(probs)


if __name__ == "__main__":
    sys.exit(main())
