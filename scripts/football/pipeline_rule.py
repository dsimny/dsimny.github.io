#!/usr/bin/env python3
"""
Open Ledger Sports — the football selection rule (docs/FOOTBALL_PIPELINE.md s.4).

Implements the rule that picks the premium play (rank 1) and the free play
(highest-ranked qualifier that is not rank 1), and reports what it would have
done historically.

WHAT THIS IS NOT. Not a gate, not a validation, not a search for a better rule.
The rule is FIXED by the spec. This exists so the rule's behaviour is known
before it ships rather than after, and so its record is published from day one
rather than discovered by members. If the numbers below are poor, the answer is
to say so in the copy — NOT to re-cut the rule. Any claim that it is +EV needs
fb-v0.3 with its own pre-registration and clean season.

Run: python scripts/football/pipeline_rule.py --seasons 2022-2024
"""
import argparse, io, json, os, statistics, sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asof                                        # noqa: E402
import price_test as pt                            # noqa: E402

OUT = os.path.join(pt.FB, "pipeline_rule.json")
MIN_CORROBORATION = 2       # section 4 step 2


def slate_plays(games, snaps, books):
    """Candidates grouped by SLATE WEEK. Football is weekly."""
    by_day = {}
    for g in games:
        if g.get("_margin") is None or not g["_t24"] or not g["_kickoff"]:
            continue
        t24 = g["_t24"].replace(minute=0, second=0, microsecond=0)
        close = (g["_kickoff"] - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        if t24 not in snaps or close not in snaps:
            continue
        away, home = g["away_team"], g["home_team"]
        q24, _, _ = pt.eligible_h2h(snaps[t24], away, home, books)
        qcl, _, _ = pt.eligible_h2h(snaps[close], away, home, books)
        if len(q24) < pt.MIN_BOOKS or len(qcl) < pt.MIN_BOOKS:
            continue
        fair24 = pt.consensus_fair(q24, away, home)
        faircl = pt.consensus_fair(qcl, away, home)
        if not fair24 or not faircl:
            continue

        bh, bkh, nh = pt.best_price(q24, home)
        ba, bka, na_ = pt.best_price(q24, away)
        if bh is None or ba is None:
            continue
        # step 1: effective overround at best prices = the toll to play this game
        eff = pt.implied(bh) + pt.implied(ba) - 1.0
        # step 3: side whose best price sits furthest above de-vigged fair
        cands = [{"side": home, "price": bh, "book": bkh, "near": nh,
                  "gap": fair24[home] - pt.implied(bh)},
                 {"side": away, "price": ba, "book": bka, "near": na_,
                  "gap": fair24[away] - pt.implied(ba)}]
        pick = max(cands, key=lambda c: c["gap"])
        # step 2: corroboration guard
        if pick["near"] < MIN_CORROBORATION:
            continue

        q = faircl[pick["side"]]
        margin = g["_margin"]
        if margin == 0:
            res, pnl = "push", 0.0
        else:
            won = ((pick["side"] == home) == (margin > 0))
            res, pnl = ("win", pt.payout(pick["price"])) if won else ("loss", -1.0)
        # SLATE = WEEK, not day. Football is a weekly sport - FOOTBALL_PREREG_V02
        # section 11 says so - and Thursday/Monday are single-game days, so a
        # per-day slate leaves no free play on half the calendar.
        by_day.setdefault((g["_season"], g["week"]), []).append({
            "season": g["_season"], "game_id": g["game_id"], "eff_overround": eff,
            "side": pick["side"], "price": pick["price"], "book": pick["book"],
            "near": pick["near"], "n_books": len(q24),
            "ev": q * pt.payout(pick["price"]) - (1 - q),
            "clv": q - pt.implied(pick["price"]),
            "result": res, "pnl": pnl,
        })
    return by_day


def summarise(rows, label):
    if not rows:
        return None
    n = len(rows)
    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    p = sum(1 for r in rows if r["result"] == "push")
    return {"tier": label, "n": n,
            "record": f"{w}-{l}" + (f"-{p}p" if p else ""),
            "win_pct": 100 * w / max(w + l, 1),
            "ev_pct": 100 * statistics.mean(r["ev"] for r in rows),
            "clv_pts": 100 * statistics.mean(r["clv"] for r in rows),
            "roi_pct": 100 * sum(r["pnl"] for r in rows) / n,
            "mean_eff_overround_pts": 100 * statistics.mean(r["eff_overround"] for r in rows),
            "mean_corroboration": statistics.mean(r["near"] for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2022-2024")
    args = ap.parse_args()
    a, _, b = args.seasons.partition("-")
    seasons = list(range(int(a), int(b or a) + 1))
    if asof.HOLDOUT in seasons:
        raise SystemExit(f"season {asof.HOLDOUT} is the holdout; not scored here.")

    snaps = pt.load_snapshots()
    games = asof.load_games(seasons=set(seasons), purpose="pipeline rule characterisation")
    by_day = slate_plays(games, snaps, pt.TIER1)

    premium, free, skipped = [], [], 0
    for day, cands in sorted(by_day.items()):
        cands.sort(key=lambda c: (c["eff_overround"], -c["n_books"]))
        premium.append(cands[0])
        if len(cands) > 1:
            free.append(cands[1])
        else:
            skipped += 1

    print(f"slate days: {len(by_day)}   days with no free play (single qualifier): {skipped}\n")
    rows = []
    print(f"  {'tier':<8} {'n':>5} {'record':>10} {'win%':>6} {'EV%':>8} {'CLV':>7} {'ROI%':>8} {'ovr pts':>8} {'corrob':>7}")
    for label, rs in (("premium", premium), ("free", free),
                      ("all-cands", [c for cs in by_day.values() for c in cs])):
        s = summarise(rs, label)
        rows.append(s)
        print(f"  {label:<8} {s['n']:>5} {s['record']:>10} {s['win_pct']:>5.1f}% "
              f"{s['ev_pct']:>+7.2f}% {s['clv_pts']:>+6.2f} {s['roi_pct']:>+7.2f}% "
              f"{s['mean_eff_overround_pts']:>7.2f} {s['mean_corroboration']:>7.2f}")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"_note": ("Characterisation of the FIXED selection rule in "
                             "docs/FOOTBALL_PIPELINE.md section 4. Not a gate. The "
                             "rule does not change in response to these numbers."),
                   "seasons": seasons, "summaries": rows}, f, indent=1)
    print(f"\nwrote {os.path.relpath(OUT, pt.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
