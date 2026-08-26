#!/usr/bin/env python3
"""
Open Ledger Sports — the pre-kickoff board (docs/FOOTBALL_PIPELINE.md, gap B).

Turns captures on disk into the thing members buy: layer 1 for every covered
game, the premium and free plays chosen by the precommitted rule, every excluded
game named, and a fingerprint committed before kickoff.

THIS PUBLISHES NOTHING IN THE CLEAR. It writes an ENCRYPTED board plus a
plaintext SHA-256, exactly as the MLB path does. Committing the fingerprint
before kickoff is what proves the play existed and was not edited afterwards;
encrypting the board is what stops anyone reading it early. After grading, the
plaintext is revealed and anyone can hash it and compare. Premium is early
access, never permanent secrecy (section 5).

IT DOES NOT PICK. market.evaluate() picks, and grade_football.py calls the SAME
function on the SAME snapshot. That is deliberate: a play this board publishes
is by construction a play the grader will accept. Two implementations of one
rule drift, and the drift shows up as a member holding a pick that never
appears in the record.

ONE POOL ACROSS SPORTS (fp-v0.2). NFL and NCAA FBS rank together and the week
gets ONE premium play. Expect college most weeks - it fields ~3x the games and
rank 1 is a minimum, not a median, so more draws produce a better tail even
though the typical college market is looser. That is the rule working.

NOTHING HERE IS FOOTBALL-SPECIFIC BY DESIGN. Sports are rows in SPORTS below;
the market maths lives in market.py and knows no sport at all. Adding a league
should be adding a row, not editing logic. See docs/FOOTBALL_LAUNCH.md s.9.

------------------------------------------------------------------------------
OPEN POLICY QUESTION, deliberately NOT decided in code
------------------------------------------------------------------------------
WHEN is the week's play chosen? Each game is evaluated at ITS OWN T-24, so the
week's full field does not exist at any single moment: a Sunday NFL game's T-24
lands on Saturday, by which time most of Saturday's college slate has kicked
off. There is NO instant at which every T-24 exists and no game has started.

So this script takes a DECISION MOMENT (--asof, default now) and ranks the games
that, at that moment, both have a usable T-24 capture and have not yet kicked
off. The moment is recorded in the board. Choosing the policy - a fixed weekly
time, or the first run that reaches some field size - is a product decision like
the pricing one, and it belongs in the spec before week 1 rather than in a
default here. Until it is set, `--asof` makes the choice explicit and auditable
rather than accidental.

Run:
  python scripts/football/board.py --dry-run
  python scripts/football/board.py --week 2026-09-01 --dry-run
  python scripts/football/board.py                      # writes .enc + commitment
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import market                                        # noqa: E402
import crypto_box                                    # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
ODDS_DIR = os.path.join(FB, "odds")
COMMITMENTS = os.path.join(FB, "commitments.json")

# Per-sport config. Everything sport-specific lives here; nothing below reads a
# sport name. `label` is what a member sees, so it is a name and not a key.
SPORTS = {
    "nfl":   {"label": "NFL"},
    "ncaaf": {"label": "NCAA FBS"},
}

# Section 6: if more than this share of the week's games are excluded, the page
# says "manual review" rather than claiming the slate was covered.
MAX_EXCLUDED_SHARE = 0.25


def board_paths(week):
    return (os.path.join(FB, f"board_{week}.json"),
            os.path.join(FB, f"board_{week}.enc"))


def collect(sport, snaps, week, asof):
    """(covered, excluded) for one sport's games in one slate week."""
    covered, excluded = [], []
    if not snaps:
        return covered, excluded

    # Every game the captures know about, deduped by matchup+kickoff. The
    # kickoff comes from the capture's own commence_time rather than a second
    # schedule source: it is what the market itself believes, and it means the
    # board needs no results feed to be built.
    games = {}
    for _, _, snap in snaps:
        for ev in snap.get("events", []):
            k = market.parse_utc(ev.get("commence_time"))
            if not k:
                continue
            games[(ev.get("away"), ev.get("home"), ev.get("commence_time"))] = k

    for (away, home, ct), kick in sorted(games.items(), key=lambda x: x[1]):
        if market.slate_week(kick) != week:
            continue
        matchup = f"{away} @ {home}"

        if kick <= asof:
            excluded.append((sport, matchup, "already kicked off at the decision moment"))
            continue

        t24, _close = market.pick_snapshots(snaps, kick)
        if not t24:
            excluded.append((sport, matchup, "NO MARKET (no capture before kickoff)"))
            continue
        t24_h = (kick - t24[0]).total_seconds() / 3600.0
        if abs(t24_h - market.IDEAL_T24_H) > market.T24_TOLERANCE_H:
            excluded.append((sport, matchup,
                             f"no T-24 capture yet (nearest is {t24_h:.1f}h before kickoff)"))
            continue

        ev = market.find_event(t24[2], away, home)
        if not ev:
            excluded.append((sport, matchup, "NO MARKET (game absent from its capture)"))
            continue
        q = market.eligible(ev, t24[0])
        try:
            m = market.evaluate(q, ev.get("away_raw"), ev.get("home_raw"))
        except market.NoMarket as why:
            excluded.append((sport, matchup, str(why)))
            continue

        m.update({
            "sport": sport,
            "league": SPORTS[sport]["label"],
            "matchup": matchup,
            "away": ev.get("away_raw"),
            "home": ev.get("home_raw"),
            "kickoff_utc": ct,
            "t24_capture": t24[1],
            "t24_hours_before_kickoff": round(t24_h, 2),
        })
        covered.append(m)
    return covered, excluded


def build(sports, week, asof):
    covered, excluded = [], []
    for sport in sports:
        snaps = market.load_snapshots(sport, ODDS_DIR)
        c, e = collect(sport, snaps, week, asof)
        covered += c
        excluded += e

    # ONE pool, both sports (fp-v0.2).
    ranked = market.rank(covered)
    premium, free = market.assign(ranked)
    for i, g in enumerate(ranked, 1):
        g["rank"] = i
        g["tier"] = "premium" if i == 1 else ("free" if i == 2 else "slate")

    total = len(covered) + len(excluded)
    share = (len(excluded) / total) if total else 0.0
    return {
        "slate_week": week,
        "decision_moment_utc": market.iso(asof),
        "generated_utc": market.iso(market.now_utc()),
        "sports": list(sports),
        "n_covered": len(covered),
        "n_excluded": len(excluded),
        "excluded_share": round(share, 4),
        # Section 3: above the threshold the page must not claim coverage.
        "coverage_status": "manual review" if share > MAX_EXCLUDED_SHARE else "covered",
        "premium": premium,
        "free": free,
        "games": ranked,
        "no_market": [{"sport": s, "matchup": m, "reason": w} for s, m, w in excluded],
        "_note": ("Layer 1 only. Every number here is measured from captured "
                  "prices; none is modelled and none is predicted. Makes NO "
                  "expectation claim - see docs/FOOTBALL_PIPELINE.md section 1. "
                  "Zero units: football does not stake."),
    }


def record_commitment(week, board_sha, committed_utc):
    """Append-only, mirroring crypto_box.record_commitment.

    Football keeps its OWN commitments file for the same reason it keeps its own
    ledger (section 6): never mixed with the MLB record.
    """
    log = {"_note": ("Football commit-and-reveal log, append-only. The SHA-256 "
                     "of each slate week's PLAINTEXT board, published before "
                     "kickoff. Never mixed with data/commitments.json."),
           "commitments": []}
    if os.path.exists(COMMITMENTS):
        with io.open(COMMITMENTS, encoding="utf-8") as f:
            log = json.load(f)
    if any(c["slate_week"] == week for c in log["commitments"]):
        return False
    log["commitments"].append({
        "slate_week": week,
        "board_sha256": board_sha,
        "committed_utc": committed_utc,
        "revealed": False,
    })
    log["commitments"].sort(key=lambda c: c["slate_week"])
    with io.open(COMMITMENTS, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    return True


def render(b):
    out = []
    out.append(f"slate week {b['slate_week']}   decision moment {b['decision_moment_utc']}")
    out.append(f"  {b['n_covered']} covered | {b['n_excluded']} excluded "
               f"({b['excluded_share']*100:.1f}%) -> {b['coverage_status'].upper()}")
    for tier, g in (("PREMIUM", b["premium"]), ("FREE", b["free"])):
        if not g:
            out.append(f"\n  {tier}: no qualifying game (House Rule 6 - passing is a position)")
            continue
        off = g.get("offshore_best")
        out.append(
            f"\n  {tier}  {g['league']}  {g['matchup']}  ({g['kickoff_utc']})\n"
            f"      side {g['side']}  best {g['best_price']:+d} at {g['best_book']}"
            f"  ({g['books_at_best']} Tier-1 books at/near it)\n"
            f"      de-vigged fair {g['fair_side']*100:.1f}%  |  eff. overround "
            f"{g['eff_overround_pts']:.2f} pts (raw {g['raw_overround_pts']:.2f})\n"
            f"      {g['n_books']} eligible books  |  T-24 capture {g['t24_capture']}"
            f" ({g['t24_hours_before_kickoff']}h out)"
            + (f"\n      offshore colour: {off['price']:+d} at {off['book']} (never the play)"
               if off else ""))
    rest = [g for g in b["games"] if g["tier"] == "slate"]
    if rest:
        out.append(f"\n  rest of the covered slate ({len(rest)}), by market tightness:")
        for g in rest[:10]:
            out.append(f"    {g['rank']:>3}. {g['eff_overround_pts']:6.2f}  "
                       f"{g['league']:<9} {g['matchup']}")
        if len(rest) > 10:
            out.append(f"    ... and {len(rest)-10} more")
    if b["no_market"]:
        out.append(f"\n  NO MARKET ({len(b['no_market'])}) - named, never dropped:")
        for nm in b["no_market"][:8]:
            out.append(f"    {nm['matchup']:<44} {nm['reason']}")
        if len(b["no_market"]) > 8:
            out.append(f"    ... and {len(b['no_market'])-8} more")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", default="nfl,ncaaf",
                    help="comma-separated; they rank in ONE pool (fp-v0.2)")
    ap.add_argument("--week", help="slate week (Tuesday, YYYY-MM-DD); default from --asof")
    ap.add_argument("--asof", help="decision moment, ISO UTC; default now")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    asof = market.parse_utc(args.asof) if args.asof else market.now_utc()
    if args.asof and not asof:
        raise SystemExit(f"could not parse --asof {args.asof!r}")
    week = args.week or market.slate_week(asof)
    sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    for s in sports:
        if s not in SPORTS:
            raise SystemExit(f"unknown sport {s!r}; known: {sorted(SPORTS)}")

    b = build(sports, week, asof)
    print(render(b))

    if args.dry_run:
        print("\n(--dry-run: nothing written, no commitment made)")
        return 0
    if not b["premium"]:
        print("\nNo qualifying game: nothing to commit. Passing is a position.")
        return 0

    crypto_box.refuse_plaintext_in_ci(f"the football board for {week}")
    plain, enc = board_paths(week)
    if os.path.exists(enc) or os.path.exists(plain):
        print(f"\nboard for {week} already exists; refusing to overwrite "
              f"(a commitment is not rewritten).")
        return 0

    sha = crypto_box.sha256_of(b)
    if crypto_box.have_key():
        crypto_box.encrypt_to(enc, b)
        wrote = enc
    else:
        # Local convenience only; CI was refused above.
        with io.open(plain, "w", encoding="utf-8") as f:
            json.dump(b, f, indent=1, sort_keys=True)
        wrote = plain
    fresh = record_commitment(week, sha, market.iso(market.now_utc()))
    print(f"\nwrote {os.path.relpath(wrote, ROOT)}")
    print(f"sha256 {sha}")
    print("commitment recorded" if fresh else "commitment already existed; left alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
