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
COMMIT PER GAME, CHOOSE PER WEEK (spec section 4 step 0b, fp-v0.3)
------------------------------------------------------------------------------
Each game is evaluated at ITS OWN T-24, so the week's full field never exists at
one moment: a Sunday NFL game's T-24 lands Saturday, after most of the college
slate has kicked off. There is NO instant at which every T-24 exists and no game
has started. The rule that resolves it:

  1. COMMIT PER GAME. The first time a game becomes evaluable, its layer-1 block
     is fingerprinted into game_commitments.json, append-only. That evaluation
     is then FROZEN - a later capture never revises it. This is what makes the
     eventual play a commitment rather than a running opinion.
  2. CHOOSE PER WEEK at the DECISION MOMENT D = Saturday 14:00 US/Eastern.
     At D, rank every game committed so far that has not kicked off.

THE POOL IS ALWAYS THE NEXT 24 HOURS. A game is eligible at D only if its T-24
has passed and it has not started, and those two conditions collapse to
`D < kickoff <= D+24h`. Saturday 2pm ET is chosen because that window spans the
college afternoon/evening slate AND the NFL's Sunday 1pm ET block - the one
window where the combined pool means something. Everything outside it still gets
full coverage on the board; it is simply not eligible to BE the committed play.

Run:
  python scripts/football/board.py --dry-run
  python scripts/football/board.py --week 2026-09-01 --dry-run
  python scripts/football/board.py --commit-only    # freeze new T-24s, choose nothing
  python scripts/football/board.py                  # commits, and selects once past D
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import market                                        # noqa: E402
import crypto_box                                    # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
ODDS_DIR = os.path.join(FB, "odds")
COMMITMENTS = os.path.join(FB, "commitments.json")
GAME_COMMITMENTS = os.path.join(FB, "game_commitments.json")

# Decision moment: Saturday 14:00 US/Eastern (spec s.4 step 0b).
# EASTERN, NOT UTC, and not by accident: the rest of the project is Eastern, and
# a UTC constant would silently shift the eligible window by an hour when DST
# ends mid-season - moving which games can be the play, without anyone deciding.
DECISION_TZ = "America/New_York"
DECISION_WEEKDAY = 5      # Monday=0 ... Saturday=5
DECISION_HOUR = 14

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


def decision_moment(week):
    """D for a slate week: the Saturday inside it, at 14:00 Eastern, as UTC.

    The week is Tuesday-anchored, so its Saturday is Tuesday + 4 days.
    """
    tue = datetime.strptime(week, "%Y-%m-%d").date() + timedelta(days=4)
    assert tue.weekday() == DECISION_WEEKDAY, f"{week} + 4d is not a Saturday"
    local = datetime(tue.year, tue.month, tue.day, DECISION_HOUR,
                     tzinfo=ZoneInfo(DECISION_TZ))
    return local.astimezone(timezone.utc)


def game_key(g):
    """Identity of a game for commitment purposes. Kickoff is included because a
    rescheduled game is a different market, not the same one moved."""
    return f"{g['sport']}|{g['matchup']}|{g['kickoff_utc']}"


def load_game_commitments():
    if os.path.exists(GAME_COMMITMENTS):
        with io.open(GAME_COMMITMENTS, encoding="utf-8") as f:
            return json.load(f)
    return {"_note": ("Per-game commitments, append-only (spec s.4 step 0b). "
                      "Each game's layer-1 block is fingerprinted the first time "
                      "it becomes evaluable at its own T-24, and NEVER revised - "
                      "a later capture does not get to change an evaluation that "
                      "has already been published. This is what makes the weekly "
                      "play a commitment rather than a running opinion."),
            "games": {}}


def commit_games(covered, now, write=True):
    """Freeze any newly-evaluable game. Returns (store, n_new).

    APPEND-ONLY. A game already present keeps its original fingerprint and its
    original layer-1 numbers; if a later capture would evaluate it differently,
    the earlier commitment stands and the difference is recorded rather than
    applied. Rewriting a published fingerprint is the one thing this file exists
    to prevent.
    """
    store = load_game_commitments()
    games, new = store["games"], 0
    for g in covered:
        k = game_key(g)
        block = {kk: g[kk] for kk in sorted(g) if kk not in
                 ("rank", "tier", "committed_utc", "commitment_sha", "restated")}
        sha = crypto_box.sha256_of(block)
        if k in games:
            if games[k]["sha256"] != sha:
                # Recorded, never applied. Seeing this means a later capture
                # disagrees with the frozen evaluation - which is information,
                # not licence to update.
                games[k].setdefault("later_disagreements", [])
                if sha not in games[k]["later_disagreements"]:
                    games[k]["later_disagreements"].append(sha)
            g["committed_utc"] = games[k]["committed_utc"]
            g["commitment_sha"] = games[k]["sha256"]
            g["restated"] = games[k]["sha256"] != sha
            continue
        games[k] = {"sha256": sha, "committed_utc": market.iso(now),
                    "kickoff_utc": g["kickoff_utc"], "sport": g["sport"],
                    "matchup": g["matchup"]}
        g["committed_utc"] = games[k]["committed_utc"]
        g["commitment_sha"] = sha
        g["restated"] = False
        new += 1
    if write and new:
        with io.open(GAME_COMMITMENTS, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1, sort_keys=True)
    return store, new


def collect(sport, snaps, week, asof):
    """(covered, excluded) for one sport's games in one slate week."""
    covered, excluded = [], []

    # A BOARD MAY NEVER READ A CAPTURE THAT HAS NOT HAPPENED YET. Without this
    # the builder evaluates a Sunday game at the decision moment using Saturday
    # night's capture, which is a look-ahead: it would publish an evaluation
    # nobody could have made at the time, and the commitment would be a fiction.
    # Caught by the self-test showing identical coverage three days before the
    # decision moment and at it - the field should GROW as captures land.
    #
    # It is also what enforces the upper half of the eligible window. A game
    # kicking off more than 24h after D has no capture at or before D that is
    # within T-24 tolerance, so it cannot be covered at D - which is why the
    # eligible set below needs only the `kickoff > D` half of the rule.
    snaps = [s for s in snaps if s[0] <= asof]
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

        # NOTE: a game that has already kicked off is NOT excluded here. Under
        # fp-v0.3 its evaluation is still committed at its own T-24 and it still
        # gets layer-2 coverage; it is only barred from BEING the play, which is
        # a selection concern and is applied in build().
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


def build(sports, week, asof, commit=True):
    covered, excluded = [], []
    for sport in sports:
        snaps = market.load_snapshots(sport, ODDS_DIR)
        c, e = collect(sport, snaps, week, asof)
        covered += c
        excluded += e

    # Step 0b part 1: freeze every newly-evaluable game at its own T-24.
    _store, n_new = commit_games(covered, asof, write=commit)

    # ONE pool, both sports (fp-v0.2), ranked for display regardless of
    # eligibility - the whole covered slate is the product.
    ranked = market.rank(covered)
    for i, g in enumerate(ranked, 1):
        g["rank"] = i
        g["tier"] = "slate"

    # Step 0b part 2: choose at D, from games committed and not yet kicked off.
    D = decision_moment(week)
    eligible = [g for g in ranked if market.parse_utc(g["kickoff_utc"]) > D]
    premium = free = None
    if asof >= D:
        premium, free = market.assign(market.rank(eligible))
        for g, tier in ((premium, "premium"), (free, "free")):
            if g:
                g["tier"] = tier

    total = len(covered) + len(excluded)
    share = (len(excluded) / total) if total else 0.0
    return {
        "slate_week": week,
        "decision_moment_utc": market.iso(D),
        "decision_made": asof >= D,
        "asof_utc": market.iso(asof),
        "n_eligible_at_decision": len(eligible),
        "n_newly_committed": n_new,
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


def mark_revealed(week):
    """Flip a week's commitment to revealed once its plaintext is published.

    Mirrors crypto_box.mark_revealed. Called by grading, not by a person: House
    Rule 7 says held plays are held, not hidden, and 'withholding a pick before
    kickoff is the product, withholding it after is fraud'. A reveal that
    depends on someone remembering to run it is a reveal that eventually does
    not happen.
    """
    if not os.path.exists(COMMITMENTS):
        return False
    with io.open(COMMITMENTS, encoding="utf-8") as f:
        log = json.load(f)
    hit = False
    for c in log.get("commitments", []):
        if c.get("slate_week") == week and not c.get("revealed"):
            c["revealed"] = True
            c["revealed_utc"] = market.iso(market.now_utc())
            hit = True
    if hit:
        with io.open(COMMITMENTS, "w", encoding="utf-8", newline="\n") as f:
            json.dump(log, f, indent=1)
    return hit


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
    out.append(f"slate week {b['slate_week']}   asof {b['asof_utc']}")
    out.append(f"  decision moment D = {b['decision_moment_utc']} (Sat 14:00 ET) -> "
               f"{'REACHED' if b['decision_made'] else 'NOT YET'}")
    out.append(f"  {b['n_covered']} covered | {b['n_excluded']} excluded "
               f"({b['excluded_share']*100:.1f}%) -> {b['coverage_status'].upper()}")
    out.append(f"  {b['n_newly_committed']} newly committed at their T-24 | "
               f"{b['n_eligible_at_decision']} eligible to be the play "
               f"(kickoff after D)")
    if not b["decision_made"]:
        out.append("\n  No play chosen yet: the decision moment has not arrived. "
                   "Games keep\n  committing at their own T-24 until it does.")
        return "\n".join(out + _slate_tail(b))
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
    return "\n".join(out + _slate_tail(b))


def _slate_tail(b):
    out = []
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
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", default="nfl,ncaaf",
                    help="comma-separated; they rank in ONE pool (fp-v0.2)")
    ap.add_argument("--week", help="slate week (Tuesday, YYYY-MM-DD); default from --asof")
    ap.add_argument("--asof", help="decision moment, ISO UTC; default now")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--commit-only", action="store_true",
                    help="freeze newly-evaluable games at their T-24; never select")
    ap.add_argument("--writeups", action="store_true",
                    help="generate layer-2 prose BEFORE fingerprinting, so the "
                         "commitment covers the words as well as the numbers")
    ap.add_argument("--mark-revealed", metavar="WEEK",
                    help="flip a week's commitment to revealed (grading does this)")
    args = ap.parse_args()

    if args.mark_revealed:
        done = mark_revealed(args.mark_revealed)
        print(f"{args.mark_revealed}: "
              + ("marked revealed" if done else "no unrevealed commitment"))
        return 0

    asof = market.parse_utc(args.asof) if args.asof else market.now_utc()
    if args.asof and not asof:
        raise SystemExit(f"could not parse --asof {args.asof!r}")
    week = args.week or market.slate_week(asof)
    sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    for s in sports:
        if s not in SPORTS:
            raise SystemExit(f"unknown sport {s!r}; known: {sorted(SPORTS)}")

    b = build(sports, week, asof, commit=not args.dry_run)
    print(render(b))

    if args.dry_run:
        print("\n(--dry-run: nothing written, no commitment made)")
        return 0
    if b["n_newly_committed"]:
        print(f"\nfroze {b['n_newly_committed']} game(s) at their T-24 in "
              f"{os.path.relpath(GAME_COMMITMENTS, ROOT)}")
    if args.commit_only:
        print("(--commit-only: no selection made)")
        return 0
    if not b["decision_made"]:
        print(f"\nDecision moment {b['decision_moment_utc']} not reached; "
              f"no play selected. Nothing else to write.")
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

    # WRITEUPS BEFORE THE FINGERPRINT, on purpose. Layer 2 is generated once,
    # here, and then hashed with everything else - so the published commitment
    # proves the PROSE was not edited after kickoff either, not just the
    # numbers. Generating it after the hash would leave the words outside the
    # only mechanism that makes them checkable. It also bounds the API cost to
    # one slate per week: this branch is reached once, when the board is first
    # written, and the exists-check above refuses a second pass.
    if args.writeups:
        import writeup
        writeup.annotate(b)

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
