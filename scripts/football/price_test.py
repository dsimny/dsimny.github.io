#!/usr/bin/env python3
"""
Open Ledger Sports — fb-v0.2 price test (docs/FOOTBALL_PREREG_V02.md section 8).

THE QUESTION. Not "can our model out-forecast the market" — fb-v0.1 answered that
NO, 9 of 9 (docs/FOOTBALL_RESULT_T24.md). This asks whether the BEST AVAILABLE
PRICE at T-24 beats the market's own closing fair value, after vig. No model
opinion enters anywhere: the side is chosen by a market rule and the probability
is inherited from the closing consensus.

THE GATE IS EV, NOT CLV, AND THAT IS THE WHOLE DESIGN (section 5). Shopping for
the best of N books posts positive CLV almost by construction — it compares an
extreme against a central tendency, so whenever the market does not move much
between T-24 and close, best-at-T-24 "beats" consensus-at-close with no skill
involved. CLV is therefore reported and explicitly NOT gated on. The gate is

    EV = q * payout(p) - (1 - q)          q = de-vigged CLOSING probability
                                          p = best T-24 price for that side

A NULL ARM runs beside the live arm: same pipeline, side chosen by a seeded coin
flip. Under the null it should post positive CLV (proving the machinery) and ~0
EV (proving there is no edge). If the live and null arms look alike, the live
arm's CLV means nothing, which is exactly what section 5 predicts.

HOLDOUT. 2025 is refused here. This script scores TUNE and VALIDATE only; the
holdout is claimed once, deliberately, elsewhere, and only after both gates pass.

Run:
  python scripts/football/price_test.py                    # TUNE + VALIDATE
  python scripts/football/price_test.py --seasons 2022-2023
"""
import argparse, io, json, os, re, statistics, sys, random
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asof                                        # noqa: E402
from teams import from_name, UnknownTeam           # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
SNAPS = os.path.join(FB, "odds", "hist")
OUT = os.path.join(FB, "price_test.json")

# --- section 6, declared before scoring, not adjustable here -----------------
TIER1 = frozenset({
    "draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "superbook",
    "pointsbetus", "twinspires", "barstool", "wynnbet", "sugarhouse", "unibet_us",
    "circasports", "foxbet", "betfair", "fanatics"})
TIER2 = frozenset({
    "betonlineag", "lowvig", "betus", "mybookieag", "bovada", "gtbets",
    "intertops", "unibet"})
# The six that survive into 2025, for the like-for-like arm section 6 requires.
TIER1_2025 = frozenset({
    "draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "fanatics"})

STALENESS_MIN = 15          # a quote older than this vs the snapshot is not takeable
MIN_BOOKS = 5               # fewer eligible books than this and the side is not scored
NULL_SEED = 20260824        # fixed so the null arm is reproducible, never re-rolled


def parse_utc(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def implied(american):
    """American odds -> implied probability (with vig still in it)."""
    a = float(american)
    return (-a) / ((-a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def payout(american):
    """Profit per 1 unit staked on a win."""
    a = float(american)
    return 100.0 / (-a) if a < 0 else a / 100.0


def load_snapshots():
    """{utc datetime: parsed snapshot}. One pass over disk, held in memory."""
    out = {}
    for f in sorted(os.listdir(SNAPS)):
        m = re.match(r"nfl_(\d{8}T\d{6})Z\.json$", f)
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        with io.open(os.path.join(SNAPS, f), encoding="utf-8") as fh:
            out[t] = json.load(fh)
    return out


def eligible_h2h(snap, away, home, books_allowed):
    """Eligible moneyline quotes for one game in one snapshot.

    Returns (quotes, n_seen, n_stale) where quotes is {book: {side: price}}.
    A quote is eligible only if the book's last_update is within STALENESS_MIN of
    the snapshot itself. A stale quote is the likeliest source of a fake edge: it
    is a number the book has not moved yet and would not have honoured.
    """
    snap_t = parse_utc(snap.get("snapshot_utc"))
    quotes, seen, stale = {}, 0, 0
    for ev in snap.get("events", []):
        if ev.get("away") != away or ev.get("home") != home:
            continue
        for bk in ev.get("books", []):
            name = bk.get("book")
            if name not in books_allowed:
                continue
            h2h = (bk.get("markets") or {}).get("h2h") or []
            if len(h2h) < 2:
                continue
            seen += 1
            lu = parse_utc(bk.get("last_update"))
            if snap_t and lu and abs((snap_t - lu).total_seconds()) > STALENESS_MIN * 60:
                stale += 1
                continue
            # Outcome names are full team names ("Kansas City Chiefs"); the event
            # and games.csv both use abbreviations. Resolve through teams.from_name
            # rather than string-matching, and DROP a quote we cannot resolve
            # instead of guessing which side it was (House Rule 4).
            named = {}
            for o in h2h:
                if o.get("price") is None:
                    continue
                try:
                    named[from_name(o["name"], source="price-test")] = o["price"]
                except UnknownTeam:
                    named = {}
                    break
            if set(named) != {away, home}:
                continue
            quotes[name] = named
        break
    return quotes, seen, stale


def consensus_fair(quotes, name_a, name_b):
    """Proportionally de-vigged consensus from the MEDIAN price per side.

    Median across observed books only — section 8 forbids interpolating a line
    nobody offered.
    """
    pa = [q[name_a] for q in quotes.values() if name_a in q]
    pb = [q[name_b] for q in quotes.values() if name_b in q]
    if len(pa) < MIN_BOOKS or len(pb) < MIN_BOOKS:
        return None
    ia, ib = implied(statistics.median(pa)), implied(statistics.median(pb))
    tot = ia + ib
    if tot <= 0:
        return None
    return {name_a: ia / tot, name_b: ib / tot, "_overround": tot - 1.0}


def best_price(quotes, name):
    """Highest American odds for a side = best price for the bettor."""
    ps = [(q[name], b) for b, q in quotes.items() if name in q]
    if not ps:
        return None, None, 0
    best = max(ps, key=lambda x: x[0])
    # How many books are at or within 1 point of implied prob of the best price —
    # section 7 wants this, because an edge resting on one outlier book is
    # fragile in a way an edge available at four books is not.
    ib = implied(best[0])
    near = sum(1 for p, _ in ps if implied(p) <= ib + 0.01)
    return best[0], best[1], near


def score(seasons, tier_name, books_allowed, snaps, rng):
    games = asof.load_games(seasons=set(seasons), purpose="fb-v0.2 price test")
    stamps = sorted(snaps)
    rows = []
    excl = {"no_t24_snap": 0, "no_close_snap": 0, "too_few_books": 0, "no_result": 0}
    stale_seen = stale_n = 0

    for g in games:
        t24, kick = g["_t24"], g["_kickoff"]
        if not t24 or not kick or g.get("_margin") is None:
            excl["no_result"] += 1
            continue
        t24_hour = t24.replace(minute=0, second=0, microsecond=0)
        close_hour = (kick - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        if t24_hour not in snaps:
            excl["no_t24_snap"] += 1
            continue
        if close_hour not in snaps:
            excl["no_close_snap"] += 1
            continue

        away, home = g["away_team"], g["home_team"]
        q24, seen, st = eligible_h2h(snaps[t24_hour], away, home, books_allowed)
        stale_seen += seen; stale_n += st
        qcl, seen2, st2 = eligible_h2h(snaps[close_hour], away, home, books_allowed)
        stale_seen += seen2; stale_n += st2
        if len(q24) < MIN_BOOKS or len(qcl) < MIN_BOOKS:
            excl["too_few_books"] += 1
            continue

        names = sorted({n for q in q24.values() for n in q})
        if len(names) != 2:
            excl["too_few_books"] += 1
            continue
        na, nb = names
        fair24 = consensus_fair(q24, na, nb)
        faircl = consensus_fair(qcl, na, nb)
        if not fair24 or not faircl:
            excl["too_few_books"] += 1
            continue

        cand = []
        for n in (na, nb):
            bp, bk, near = best_price(q24, n)
            if bp is None:
                continue
            cand.append({"side": n, "price": bp, "book": bk, "near": near,
                         "gap": fair24[n] - implied(bp)})
        if len(cand) != 2:
            excl["too_few_books"] += 1
            continue

        # Section 4 rule: the side whose best price implies the LOWEST probability
        # relative to contemporaneous de-vigged fair value = largest gap.
        live = max(cand, key=lambda c: c["gap"])
        null = cand[0] if rng.random() < 0.5 else cand[1]

        margin = g["_margin"]          # home perspective
        for arm, pick in (("live", live), ("null", null)):
            q = faircl[pick["side"]]
            ev = q * payout(pick["price"]) - (1 - q)
            # settlement, pushes (ties) at zero
            if margin == 0:
                res, pnl = "push", 0.0
            else:
                won = ((pick["side"] == home) == (margin > 0))
                res, pnl = ("win", payout(pick["price"])) if won else ("loss", -1.0)
            rows.append({
                "season": g["_season"], "game_id": g["game_id"], "arm": arm,
                "side": pick["side"], "book": pick["book"], "price": pick["price"],
                "near_best": pick["near"],
                "fair_t24": fair24[pick["side"]], "fair_close": q,
                "gap_t24": pick["gap"],
                "ev": ev,
                "clv": q - implied(pick["price"]),   # diagnostic ONLY (section 5)
                "result": res, "pnl": pnl,
                "overround_t24": fair24["_overround"],
            })
    return rows, excl, (stale_seen, stale_n)


def summarise(rows, arm, season=None):
    r = [x for x in rows if x["arm"] == arm and (season is None or x["season"] == season)]
    if not r:
        return None
    n = len(r)
    wins = sum(1 for x in r if x["result"] == "win")
    losses = sum(1 for x in r if x["result"] == "loss")
    push = sum(1 for x in r if x["result"] == "push")
    return {
        "n": n, "record": f"{wins}-{losses}" + (f"-{push}p" if push else ""),
        "ev_mean_pct": 100 * statistics.mean(x["ev"] for x in r),
        "ev_positive_share": 100 * sum(1 for x in r if x["ev"] > 0) / n,
        "clv_mean_pts": 100 * statistics.mean(x["clv"] for x in r),
        "clv_positive_share": 100 * sum(1 for x in r if x["clv"] > 0) / n,
        "realised_roi_pct": 100 * sum(x["pnl"] for x in r) / n,
        "mean_overround_pts": 100 * statistics.mean(x["overround_t24"] for x in r),
        "mean_books_at_best": statistics.mean(x["near_best"] for x in r),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2022-2024")
    args = ap.parse_args()
    a, _, b = args.seasons.partition("-")
    seasons = list(range(int(a), int(b or a) + 1))
    if asof.HOLDOUT in seasons:
        raise SystemExit(
            f"season {asof.HOLDOUT} is the fb-v0.2 holdout. This script scores TUNE "
            "and VALIDATE only. The holdout is claimed once, deliberately, and only "
            "after both gates in section 10 pass.")

    snaps = load_snapshots()
    print(f"{len(snaps)} snapshots loaded\n")

    report = {"_spec": "docs/FOOTBALL_PREREG_V02.md (frozen 2026-08-24)",
              "seasons": seasons, "market": "moneyline (h2h)",
              "staleness_min": STALENESS_MIN, "min_books": MIN_BOOKS, "arms": {}}

    for tier_name, books in (("tier1", TIER1), ("tier2", TIER2),
                             ("tier1_2025_subset", TIER1_2025)):
        rng = random.Random(NULL_SEED)
        rows, excl, (seen, stale) = score(seasons, tier_name, books, snaps, rng)
        if not rows:
            print(f"{tier_name}: no scored games\n")
            continue
        print(f"=== {tier_name} ===")
        print(f"  excluded: {excl}   stale quotes: {stale}/{seen} "
              f"({100*stale/max(seen,1):.1f}%)")
        hdr = f"  {'arm':<5} {'season':>7} {'n':>5} {'record':>10} {'EV%':>8} {'CLV pts':>8} {'ROI%':>8}"
        print(hdr)
        tier_out = {}
        for arm in ("live", "null"):
            for s in seasons + [None]:
                sm = summarise(rows, arm, s)
                if not sm:
                    continue
                label = str(s) if s else "ALL"
                print(f"  {arm:<5} {label:>7} {sm['n']:>5} {sm['record']:>10} "
                      f"{sm['ev_mean_pct']:>+7.2f}% {sm['clv_mean_pts']:>+7.2f} "
                      f"{sm['realised_roi_pct']:>+7.2f}%")
                tier_out[f"{arm}_{label}"] = sm
        report["arms"][tier_name] = {
            "summary": tier_out,
            "excluded": excl,
            "stale_rate_pct": 100 * stale / max(seen, 1),
        }
        print()

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=1)
    print(f"wrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
