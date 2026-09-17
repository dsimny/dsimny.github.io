#!/usr/bin/env python3
"""
Open Ledger Sports — football grading (docs/FOOTBALL_PIPELINE.md sections 3-6).

Turns captured prices plus results into a graded, append-only record. This is
the step that makes a dark run mean something: without it a Saturday produces
data, with it a Saturday produces "here is what the product would have said,
and here is whether it was right".

WHAT IT DOES, in the spec's order:
  section 3  coverage filter — >=5 eligible books, quotes fresh within 15 min
             of their snapshot, both teams resolvable. Anything failing is
             recorded as NO MARKET, never silently dropped.
  section 4  the selection rule — slate is a WEEK; rank games by effective
             overround at best prices; corroboration guard; side furthest above
             de-vigged fair; rank 1 premium, rank 2 free.
  section 6  book it at ZERO UNITS into its own ledger, append-only.

SLATE WEEK = TUESDAY THROUGH MONDAY. Not the ISO week, which starts Monday and
would split an NFL week across two of them (Thursday and Sunday in one, Monday
in the next). Tuesday-anchored keeps Thu/Sun/Mon together for the NFL and
Thu/Fri/Sat together for college.

WHICH SNAPSHOT IS "T-24" AND WHICH IS "CLOSING" is derived from the captures on
disk rather than assumed from a schedule, because a capture that fired late is a
fact and a convention is not:
  T-24    = the capture nearest to kickoff-24h
  closing = the LAST capture strictly before kickoff
Both distances are recorded on every entry. A closing capture at or after
kickoff is refused outright — that is an in-play price, and booking CLV from one
would be inventing a number.

ZERO UNITS. Football does not size stakes. `units` is 0 on every entry and the
pnl column is what one unit WOULD have returned, labelled as such. The Daily
Pick's staking review is a different strategy and authorises nothing here.

APPEND-ONLY (House Rule 1). A slate week already in the ledger is refused rather
than recomputed. Never backfill, never delete a loss.

NO EXPECTATION CLAIM. Per pipeline spec section 1, this makes no +EV claim. It
records what the rule chose and what happened.

Run:
  python scripts/football/grade_football.py --sport ncaaf --dry-run
  python scripts/football/grade_football.py --sport ncaaf
"""
import argparse, glob, io, json, os, statistics, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn_ncaaf                                   # noqa: E402
import espn_nfl                                     # noqa: E402
import market                                       # noqa: E402
from market import (TIER1, TIER2, MIN_BOOKS, STALENESS_MIN,   # noqa: E402,F401
                    MIN_CORROBORATION, IDEAL_T24_H, MAX_CLOSE_H, T24_TOLERANCE_H,
                    implied, payout, parse_utc, iso, slate_week,
                    eligible, fair, best, find_event, pick_snapshots)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
ODDS_DIR = os.path.join(FB, "odds")
LEDGER = os.path.join(FB, "football_ledger.json")

# Research ledger — separate file, never mixed with the official record.
# Written only by grade_football.py --research; never read by the official path.
RESEARCH_DIR = os.path.join(FB, "research")
RESEARCH_LEDGER = os.path.join(RESEARCH_DIR, "research_ledger.json")
RESEARCH_PREREG_PATH = os.path.join(ROOT, "data", "football",
                                    "research_preregistrations.json")
RESEARCH_VERSION_ID = "fp-v0.4-market-observation-v1"

# EVERY MARKET FUNCTION AND THRESHOLD NOW LIVES IN market.py, imported above
# rather than defined here. They used to be defined in this file and board.py
# would have needed its own copy; two implementations of one rule drift, and the
# drift surfaces as a premium play the ledger refuses to grade. The extraction
# was verified by comparing every function against this file's previous version
# across 399 real events (11,340 assertions, all equal) - the same
# extract-then-prove-equivalent pattern used for engine.simulate_game in v0.5.
#
# The grading-specific parts stay here: which snapshot is T-24 and which is the
# close, settlement against a final score, and the append-only ledger write.


def load_snapshots(sport):
    """Grading reads captures from the repo's odds dir; market.py takes the
    directory as an argument so a caller can point it elsewhere."""
    return market.load_snapshots(sport, ODDS_DIR)


# PER-SPORT CONFIG. Everything that differs between leagues is a row here;
# nothing below branches on a sport name.
#
#   results    the module that owns that sport's outcomes
#   keyfn      the join key, or None where both sides already share an identity.
#              NFL captures and espn_nfl.py both carry teams.py franchise keys,
#              so `==` is exact there. College carries display strings from two
#              feeds that disagree on orthography, so it needs espn_ncaaf's
#              normaliser. MEASURED 2026-08-26 on the live store: without it, 7
#              of 99 results failed the join, 4 of them fully gradeable
#              (Hawai'i, San Jose State, Sam Houston) - games that WERE priced,
#              recorded as "absent from a capture".
#   gradeable  the SEASON-TYPE ALLOWLIST (spec section 3a). Positive naming: a
#              season type not on the list is refused, including values ESPN has
#              not invented yet. A blocklist would silently grade any new type,
#              and the direction of that error is a corrupted permanent ledger.
#
# BOTH SPORTS CARRY AN ALLOWLIST. College has no preseason, which makes it easy
# to assume it needs no filter - it does. Verified against the live endpoint
# 2026-08-26: college-football 2025-12-27 returns 8 events, ALL
# {'type': 3, 'slug': 'post-season'}, and 2026-01-01 returns 3 more. Without a
# college allowlist the first December grading run books eight bowls into the
# append-only ledger with no decision ever taken about whether a neutral-site,
# month-of-layoff market is the same product. That is the identical House
# Rule 1 failure preseason poses for the NFL, arriving four months later.
SPORTS = {
    "ncaaf": {"results": espn_ncaaf, "keyfn": espn_ncaaf.key_for,
              "gradeable": espn_ncaaf.gradeable},
    "nfl":   {"results": espn_nfl, "keyfn": None,
              "gradeable": espn_nfl.gradeable},
}


def build(sport, snaps, results):
    """One candidate per gradeable game, plus the reasons games were skipped."""
    cfg = SPORTS[sport]
    cands, skipped = [], []
    for r in results.values():
        # SECTION 3a, THE ALLOWLIST, CHECKED BEFORE ANYTHING ELSE. Preseason is
        # a different population, not a smaller sample of the same one: playing
        # time is a coaching decision, the market prices exactly that, and we do
        # not observe it. Recorded as a refusal rather than dropped, by the same
        # rule as every other marker.
        if cfg["gradeable"] and not cfg["gradeable"](r):
            skipped.append((r["away"], r["home"],
                            f"NOT REGULAR SEASON ({r.get('season_slug', 'unknown')})"))
            continue
        if not r.get("final"):
            continue
        kick = parse_utc(r.get("kickoff_utc"))
        if not kick:
            skipped.append((r["away"], r["home"], "no kickoff time"))
            continue
        t24, close = pick_snapshots(snaps, kick)
        if not t24 or not close:
            skipped.append((r["away"], r["home"], "NO MARKET (no capture before kickoff)"))
            continue
        # An in-play price is never a close.
        if close[0] >= kick:
            skipped.append((r["away"], r["home"], "closing capture is in-play"))
            continue
        close_h = (kick - close[0]).total_seconds() / 3600.0
        if close_h > MAX_CLOSE_H:
            skipped.append((r["away"], r["home"],
                            f"no closing capture (latest is {close_h:.1f}h "
                            f"before kickoff, need <={MAX_CLOSE_H:.0f}h)"))
            continue
        t24_h = (kick - t24[0]).total_seconds() / 3600.0
        if abs(t24_h - IDEAL_T24_H) > T24_TOLERANCE_H:
            skipped.append((r["away"], r["home"],
                            f"no T-24 capture (nearest is {t24_h:.1f}h before "
                            f"kickoff)"))
            continue

        try:
            ev24 = find_event(t24[2], r["away"], r["home"], cfg["keyfn"])
            evcl = find_event(close[2], r["away"], r["home"], cfg["keyfn"])
        except market.AmbiguousEvent as why:
            skipped.append((r["away"], r["home"], f"AMBIGUOUS JOIN - {why}"))
            continue
        if not ev24 or not evcl:
            skipped.append((r["away"], r["home"], "NO MARKET (game absent from a capture)"))
            continue
        q24, qcl = eligible(ev24, t24[0]), eligible(evcl, close[0])
        if len(q24) < MIN_BOOKS or len(qcl) < MIN_BOOKS:
            skipped.append((r["away"], r["home"],
                            f"NO MARKET ({len(q24)}/{len(qcl)} eligible books, need {MIN_BOOKS})"))
            continue
        # PRICES ARE KEYED BY THE ODDS FEED'S OWN TEAM STRINGS, so every market
        # call below uses the CAPTURE's away_raw/home_raw - never the results
        # row's identity. The two are the same string for NCAA FBS (verbatim
        # identity) and are NOT for the NFL, where a capture stores the
        # canonical franchise key "NE" in `away` and "New England Patriots" in
        # `away_raw`. Passing the results row's key here silently produced
        # "NO MARKET (consensus not computable)" for every NFL game - a total
        # blackout that would have looked like a thin market rather than a bug.
        # Caught by selftest_allowlist.py's regular-season control case.
        #
        # EACH SNAPSHOT IS READ WITH ITS OWN STRINGS, and the side is carried
        # across as HOME-OR-AWAY rather than as a name. The earlier version took
        # away_raw/home_raw from the T-24 capture and used them against the
        # CLOSING capture's quotes, which assumes the feed spells a team
        # identically at both ends. That is fail-closed, not corrupting - fair()
        # filters on `if a in v`, an unmatched key empties the list, and the game
        # is skipped - but it would be skipped as "consensus not computable"
        # when the truth is "the feed renamed the team". A priced game recorded
        # with a reason that is not the true one is the exact class of failure
        # the join fix just removed, so it is not worth leaving in for the sake
        # of a rename being unlikely inside 24h. Raised in review by
        # claude-code-e2 from code reading; no instance has been observed.
        a24, h24 = ev24.get("away_raw"), ev24.get("home_raw")
        acl, hcl = evcl.get("away_raw"), evcl.get("home_raw")
        f24, fcl = fair(q24, a24, h24), fair(qcl, acl, hcl)
        if not f24 or not fcl:
            skipped.append((r["away"], r["home"], "NO MARKET (consensus not computable)"))
            continue
        # THE SELECTION RULE ITSELF (section 4 steps 1-3, plus the takeable-price
        # and unclassified-book markers) is market.evaluate(). The board builder
        # calls the identical function on the identical snapshot, so a play it
        # publishes is by construction a play this grader will accept.
        try:
            m = market.evaluate(q24, a24, h24)
        except market.NoMarket as why:
            skipped.append((r["away"], r["home"], str(why)))
            continue

        pick = {"side": m["side"], "price": m["best_price"],
                "book": m["best_book"], "near": m["books_at_best"]}
        # HOME-OR-AWAY IS THE IDENTITY from here on. m["side"] is a T-24 string;
        # resolving it to a side once means the close and the settlement never
        # have to agree with the T-24 spelling, only with the schedule.
        pick_is_home = (m["side"] == h24)
        close_side = hcl if pick_is_home else acl

        margin = r["margin"]
        if margin == 0:
            res, pnl = "push", 0.0
        else:
            # margin is from the HOME perspective, so this compares like with
            # like. Comparing a capture string against the results row's home
            # team - the earlier bug - settled every NFL pick as the away side.
            won = pick_is_home == (margin > 0)
            res, pnl = ("win", payout(pick["price"])) if won else ("loss", -1.0)

        cands.append({
            "sport": sport, "slate_week": slate_week(kick),
            "espn_event_id": r["espn_event_id"],
            "matchup": f'{r["away"]} @ {r["home"]}',
            "kickoff_utc": r["kickoff_utc"],
            "side": pick["side"], "price": pick["price"], "book": pick["book"],
            "books_at_best": pick["near"], "n_books_t24": len(q24),
            "eff_overround_pts": m["eff_overround_pts"],
            "fair_t24": round(f24[pick["side"]], 5),
            "fair_close": round(fcl[close_side], 5),
            "clv_pts": round(100 * (fcl[close_side] - implied(pick["price"])), 3),
            "t24_capture": t24[1],
            "t24_hours_before_kickoff": round((kick - t24[0]).total_seconds() / 3600, 2),
            "close_capture": close[1],
            "close_hours_before_kickoff": round((kick - close[0]).total_seconds() / 3600, 2),
            "final": f'{r["away_score"]}-{r["home_score"]}',
            "result": res,
            "units": 0,                       # section 6: football does not stake
            "pnl_per_unit": round(pnl, 4),    # what ONE unit would have returned
        })
    return cands, skipped


def load_ledger():
    try:
        with io.open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"_note": ("Football record, append-only (House Rule 1). ZERO UNITS "
                          "throughout - pnl_per_unit is what one unit WOULD have "
                          "returned, not money risked. Makes no expectation claim; "
                          "see docs/FOOTBALL_PIPELINE.md section 1. Never mixed with "
                          "ledger.json, daily_ledger.json, totals_ledger.json or "
                          "watchlist.json."),
                "entries": []}


# ---------------------------------------------------------------------------
# Research grading — completely separate from the official path above.
# These functions never read or write LEDGER (football_ledger.json).
# ---------------------------------------------------------------------------

def load_research_prereg():
    """Load and validate the registered research cohort entry.

    Fails closed if the registry is missing, the version ID is absent,
    required fields are missing, or the authority is wrong.
    """
    if not os.path.exists(RESEARCH_PREREG_PATH):
        raise SystemExit(f"research_preregistrations.json not found")
    with io.open(RESEARCH_PREREG_PATH, encoding="utf-8") as f:
        registry = json.load(f)
    entry = next((r for r in registry.get("registrations", [])
                  if r.get("id") == RESEARCH_VERSION_ID), None)
    if entry is None:
        raise SystemExit(
            f"research version {RESEARCH_VERSION_ID!r} not in registry; "
            f"register it before grading")
    if entry.get("authority") != "research_only_no_selection_delivery_or_holdout":
        raise SystemExit(
            f"research version {RESEARCH_VERSION_ID!r} has wrong authority")
    if entry.get("units") != 0:
        raise SystemExit(
            f"research version {RESEARCH_VERSION_ID!r} declares non-zero units")
    return entry


def load_research_ledger():
    """Load the research ledger, or return an empty one."""
    try:
        with io.open(RESEARCH_LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "_note": (
                "Football research ledger, append-only. "
                f"Observations produced by {RESEARCH_VERSION_ID!r}. "
                "ZERO UNITS throughout — pnl_per_unit is hypothetical research "
                "analysis showing what one unit would have returned, not money "
                "risked. Never mixed with football_ledger.json or any official record. "
                "No research entry contributes to official W-L, CLV, or ROI."
            ),
            "research_version_id": RESEARCH_VERSION_ID,
            "entries": [],
        }


def research_entry_key(entry):
    """Stable idempotency key for one graded research observation.

    Derived from version + slate identifiers so the same observation cannot
    be appended twice even across re-runs.  Does not use the game's result,
    so it is safe to compute before grading.
    """
    return "|".join([
        entry.get("research_version_id", ""),
        entry.get("slate_week", ""),
        entry.get("sport", ""),
        entry.get("matchup", ""),
        entry.get("kickoff_utc", ""),
        entry.get("tier", ""),
    ])


def validate_research_board(b, prereg):
    """Fail closed if the board is not a legitimate research observation.

    Checks every field the fail-closed spec requires.  Returns the tier
    string ('research_premium' or 'research_free') or raises SystemExit.
    """
    if b.get("tier") != "research":
        raise SystemExit(
            f"board tier is {b.get('tier')!r}, not 'research'; "
            f"only boards produced by --research may be graded here")
    if b.get("research_version_id") != RESEARCH_VERSION_ID:
        raise SystemExit(
            f"board research_version_id {b.get('research_version_id')!r} "
            f"!= {RESEARCH_VERSION_ID!r}")
    if b.get("record_cohort") != RESEARCH_VERSION_ID:
        raise SystemExit(
            f"board record_cohort {b.get('record_cohort')!r} "
            f"!= {RESEARCH_VERSION_ID!r}")
    if b.get("units", -1) != 0:
        raise SystemExit(
            f"board units={b.get('units')} — research boards must be 0 units")
    if b.get("research_selection_rule") != prereg.get("selection_rule"):
        raise SystemExit(
            f"board selection rule {b.get('research_selection_rule')!r} "
            f"!= registered {prereg.get('selection_rule')!r}")


def grade_research_board(b, sport, results, cfg, snaps, prereg, done_keys):
    """Grade the selected plays in one research board against final results.

    Returns a list of new ledger rows (may be empty if nothing is final yet
    or all observations are already graded).

    ISOLATION GUARANTEES:
    - never opens football_ledger.json
    - never opens commitments.json or game_commitments.json
    - never calls grade_committed.additions() or reveal_completed()
    - settlement math (payout/implied) is shared from market.py — pure functions
    - result sources (espn_ncaaf/nfl) are shared — read-only stores
    """
    new_rows = []
    week = b.get("slate_week", "")

    for tier in ("premium", "free"):
        g = b.get(tier)
        if not g:
            continue
        if g.get("sport") != sport:
            continue

        # Build a synthetic entry key to check idempotency before doing any work.
        candidate_meta = {
            "research_version_id": RESEARCH_VERSION_ID,
            "slate_week": week,
            "sport": sport,
            "matchup": g.get("matchup", ""),
            "kickoff_utc": g.get("kickoff_utc", ""),
            "tier": f"research_{tier}",
        }
        key = research_entry_key(candidate_meta)
        if key in done_keys:
            continue  # already graded; skip without raising

        kick = parse_utc(g.get("kickoff_utc"))
        if not kick:
            continue

        # Find the result by matchup + kickoff join, using the same keyfn
        # the official grader uses (handles NCAAF orthography normalisation).
        keyfn = cfg["keyfn"] or (lambda x: x)
        away_m, home_m = g.get("matchup", "").split(" @ ", 1) if " @ " in g.get("matchup", "") else ("", "")
        matches = [
            r for r in results.values()
            if (keyfn(r.get("away", "")) == keyfn(away_m) and
                keyfn(r.get("home", "")) == keyfn(home_m) and
                parse_utc(r.get("kickoff_utc")) == kick)
        ]

        if len(matches) != 1 or not matches[0].get("final"):
            continue  # not final yet or ambiguous join; try again later
        r = matches[0]

        if cfg["gradeable"] and not cfg["gradeable"](r):
            continue  # not regular season; skip

        # Settle using the observed side from the research board.
        # The research board stores the T-24 raw strings in the premium/free
        # block via market.evaluate().  We resolve home-or-away once from the
        # capture's away_raw / home_raw fields, exactly as grade_committed does.
        away_raw = g.get("away", away_m)
        home_raw = g.get("home", home_m)

        # Determine which side was selected (home or away) from the board.
        pick_is_home = (g.get("side") == home_raw or
                        (g.get("side") and g.get("side") == g.get("home")))

        margin = r.get("margin")  # home_score - away_score
        if margin is None:
            continue
        if margin == 0:
            result, pnl = "push", 0.0
        else:
            won = pick_is_home == (margin > 0)
            result = "win" if won else "loss"
            pnl = round(payout(g["best_price"]), 4) if won else -1.0

        # CLV from the closing capture, if available.
        clv_pts, close_capture = None, None
        _, close = pick_snapshots(snaps, kick)
        if close and close[0] < kick:
            try:
                evcl = find_event(close[2], away_m, home_m, cfg["keyfn"])
                if evcl:
                    qcl = eligible(evcl, close[0])
                    fcl = fair(qcl, evcl.get("away_raw", away_m),
                               evcl.get("home_raw", home_m))
                    if fcl:
                        close_side_key = evcl.get("home_raw", home_m) if pick_is_home \
                            else evcl.get("away_raw", away_m)
                        if close_side_key in fcl:
                            clv_pts = round(
                                100 * (fcl[close_side_key] - implied(g["best_price"])), 3)
                            close_capture = close[1]
            except Exception:
                pass  # CLV is diagnostic; never block grading over it

        row = {
            # Provenance
            "research_version_id": RESEARCH_VERSION_ID,
            "research_authority": "research_only_no_selection_delivery_or_holdout",
            # Classification — explicit non-official markers
            "tier": f"research_{tier}",
            "record_cohort": RESEARCH_VERSION_ID,
            "is_official": False,
            # Observation metadata
            "sport": sport,
            "slate_week": week,
            "matchup": g.get("matchup", ""),
            "kickoff_utc": g.get("kickoff_utc", ""),
            "side": g.get("side", ""),
            "best_price": g.get("best_price"),
            "best_book": g.get("best_book"),
            "books_at_best": g.get("books_at_best"),
            "eff_overround_pts": g.get("eff_overround_pts"),
            "fair_side": g.get("fair_side"),
            "t24_capture": g.get("t24_capture"),
            "t24_hours_before_kickoff": g.get("t24_hours_before_kickoff"),
            "observed_utc": b.get("asof_utc"),
            # Result
            "espn_event_id": r.get("espn_event_id"),
            "final": f'{r.get("away_score", "?")}-{r.get("home_score", "?")}',
            "result": result,
            # Units and hypothetical P/L — 0 units, clearly labeled
            "units": 0,
            "pnl_per_unit": pnl,
            "_pnl_note": (
                "Hypothetical research analysis only. "
                "pnl_per_unit shows what one unit would have returned. "
                "No stake was placed or recorded."
            ),
            # CLV
            "clv_pts": clv_pts,
            "close_capture": close_capture,
            "clv_status": "measured" if clv_pts is not None else "unavailable",
            "graded_utc": iso(datetime.now(timezone.utc)),
        }
        # APPEND-ONLY IDENTITY. entry_key is the stable idempotency key stored
        # directly on the row so it is visible in the ledger and verifiable
        # without recomputing it. The key is set AFTER all fields are final so
        # it reflects the row as written, never a draft.
        row["entry_key"] = research_entry_key(row)
        new_rows.append(row)
        done_keys.add(key)

    return new_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="ncaaf", choices=sorted(SPORTS))
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--research", action="store_true",
                    help="grade research observations from data/football/research/ "
                         "into data/football/research/research_ledger.json; "
                         "never touches football_ledger.json or official state")
    args = ap.parse_args()

    snaps = load_snapshots(args.sport)
    cfg = SPORTS[args.sport]
    results = cfg["results"].load_store()["events"]
    n_final = sum(1 for r in results.values() if r.get("final"))
    line = (f"{len(snaps)} captures, {len(results)} known events, {n_final} final")
    if cfg["gradeable"]:
        n_ok = sum(1 for r in results.values() if cfg["gradeable"](r))
        line += f", {n_ok} on the season-type allowlist"
    print(line)

    # ---- RESEARCH PATH ---------------------------------------------------
    # Completely separate from the official path below.  Reads only files
    # under data/football/research/; writes only research_ledger.json.
    if args.research:
        prereg = load_research_prereg()
        rl = load_research_ledger()
        # Prefer the stored entry_key field on each row for idempotency; fall
        # back to recomputing it for rows written before entry_key was added,
        # so backwards-compatibility is preserved.
        done_keys = {
            e.get("entry_key") or research_entry_key(e)
            for e in rl.get("entries", [])
        }

        # Find all research board files for this sport/version.
        import glob as _glob
        pattern = os.path.join(RESEARCH_DIR,
                               f"board_*_{RESEARCH_VERSION_ID}.json")
        board_files = sorted(_glob.glob(pattern))
        print(f"research boards on disk ({args.sport}): {len(board_files)}")

        new_rows = []
        for bf in board_files:
            with io.open(bf, encoding="utf-8") as f:
                b = json.load(f)
            # FAIL CLOSED on any provenance violation — wrong tier, wrong
            # version, wrong cohort, wrong selection rule, wrong authority,
            # non-zero units. A malformed research board is not skipped; it
            # stops the run. Only not-yet-final games are skipped silently
            # (inside grade_research_board), because that is a timing
            # condition, not a provenance defect.
            validate_research_board(b, prereg)
            # Grade only observations for the requested sport.
            rows = grade_research_board(
                b, args.sport, results, cfg, snaps, prereg, done_keys)
            for row in rows:
                print(f"  {row['slate_week']} {row['tier']}: "
                      f"{row['side']} {row['result']} "
                      f"({row['pnl_per_unit']:+.4f}u hypothetical)")
            new_rows.extend(rows)

        if args.dry_run:
            print(f"\n--dry-run: {len(new_rows)} new research row(s) found, "
                  f"research_ledger.json untouched")
            return 0
        if not new_rows:
            print("\nno new research observations to grade.")
            return 0

        rl["entries"].extend(new_rows)
        rl["updated_utc"] = iso(datetime.now(timezone.utc))
        os.makedirs(RESEARCH_DIR, exist_ok=True)
        with io.open(RESEARCH_LEDGER, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rl, f, indent=1)
        print(f"\nappended {len(new_rows)} research row(s) → "
              f"{os.path.relpath(RESEARCH_LEDGER, ROOT)} "
              f"({len(rl['entries'])} total)")
        return 0

    # ---- OFFICIAL PATH ---------------------------------------------------
    import grade_committed
    ledger = load_ledger()
    new = grade_committed.additions(args.sport, ledger, results, cfg, snaps)
    for row in new:
        print(f"{row['slate_week']} {row['tier']}: {row['side']} {row['result']}")

    if args.dry_run:
        print("\n--dry-run: ledger untouched")
        return 0
    if not new:
        grade_committed.reveal_completed(ledger)
        print("\nnothing new to append.")
        return 0

    ledger["entries"].extend(new)
    ledger["updated_utc"] = iso(datetime.now(timezone.utc))
    os.makedirs(FB, exist_ok=True)
    with io.open(LEDGER, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ledger, f, indent=1)
    grade_committed.reveal_completed(ledger)
    print(f"\nappended {len(new)} entries -> {os.path.relpath(LEDGER, ROOT)} "
          f"({len(ledger['entries'])} total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
