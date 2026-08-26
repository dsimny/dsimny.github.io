#!/usr/bin/env python3
"""
Open Ledger Sports — the market layer (layer 1 of docs/FOOTBALL_PIPELINE.md s.2).

THE SINGLE IMPLEMENTATION of everything layer 1 does: which quotes are eligible,
what the de-vigged consensus is, what the best takeable price is, what the
effective overround is, and which side the selection rule takes. `board.py`
(pre-kickoff) and `grade_football.py` (post-result) both import from here and
neither has its own copy.

WHY THAT MATTERS ENOUGH TO REFACTOR A WORKING FILE. The spec says the grader
wins wherever the two disagree, which is only meaningful if a disagreement is
possible. Two implementations of one rule WILL drift, and the drift surfaces as
a premium play the ledger then refuses to grade — a member holding a pick that
never appears in the record, which is precisely the failure the transparency
brand cannot absorb. One implementation cannot drift from itself.

NOTHING IN THIS FILE KNOWS WHAT SPORT IT IS. Every function takes quotes, team
names and timestamps. That is not tidiness — it is the reason this product can
go year-round: layer 1 is market-derived, so it needs an odds feed and nothing
else. Contrast engine.py, which is irreducibly baseball (innings, parks,
starters). Sport-specific facts — results source, identity mode, season-type
allowlist, what a slate is — belong in the caller's config, never in here.
See docs/FOOTBALL_LAUNCH.md section 9.

DIRECTORY IS LEGACY. This lives under scripts/football/ because that is where
its callers are today. When a second sport arrives it should move somewhere
sport-neutral; nothing in the code needs to change when it does.

TIER LISTS ARE IMPORTED, NOT REDECLARED. price_test.py owns TIER1/TIER2 because
it declared them first, in a frozen pre-registration, and re-typing a book list
is exactly how a book quietly changes tier between two files.
"""
import glob
import io
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from price_test import TIER1, TIER2                 # noqa: E402,F401  (re-exported)

# ---- thresholds, all from the spec; changing one is a spec change ----
MIN_BOOKS = 5             # section 3 — fewer and the game is NO MARKET
STALENESS_MIN = 15        # section 3 — a quote older than this was not takeable
MIN_CORROBORATION = 2     # section 4 step 2 — the lone-outlier guard
IDEAL_T24_H = 24.0

# A capture only COUNTS as the moment it claims to be if it landed near it.
# Without these two windows a capture taken four days before kickoff is
# technically "the last one before the game" and would be booked as a close.
# That number looks fine and means nothing.
MAX_CLOSE_H = 6.0         # later than this before kickoff and it is not a close
T24_TOLERANCE_H = 6.0     # T-24 must land within this of the 24h mark


# ---------------- price arithmetic ----------------

def implied(a):
    """American price -> implied probability (vig included)."""
    a = float(a)
    return (-a) / ((-a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def payout(a):
    """American price -> profit per 1 unit staked on a win."""
    a = float(a)
    return 100.0 / (-a) if a < 0 else a / 100.0


def parse_utc(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc():
    return datetime.now(timezone.utc)


def slate_week(kickoff):
    """The Tuesday on or before kickoff, as YYYY-MM-DD.

    TUESDAY-ANCHORED, not the ISO week. ISO weeks start Monday and would split
    an NFL week across two of them (Thursday and Sunday in one, Monday in the
    next). Tuesday keeps Thu/Sun/Mon together for the NFL and Thu/Fri/Sat
    together for college.
    """
    d = kickoff.date()
    return (d - timedelta(days=(d.weekday() - 1) % 7)).isoformat()


# ---------------- captures ----------------

def load_snapshots(sport, odds_dir):
    """[(captured_utc, filename, payload)] for one sport, oldest first."""
    out = []
    for f in sorted(glob.glob(os.path.join(odds_dir, f"{sport}_*.json"))):
        with io.open(f, encoding="utf-8") as fh:
            s = json.load(fh)
        t = parse_utc(s.get("captured_utc"))
        if t:
            out.append((t, os.path.basename(f), s))
    return out


def find_event(snap, away, home):
    for ev in snap.get("events", []):
        if ev.get("away") == away and ev.get("home") == home:
            return ev
    return None


def pick_snapshots(snaps, kickoff):
    """(t24, closing) captures for one kickoff, chosen from what exists.

    Derived from what is on disk rather than assumed from a schedule, because a
    capture that fired late is a fact and a convention is not.
    """
    before = [(t, n, s) for t, n, s in snaps if t < kickoff]
    if not before:
        return None, None
    target = kickoff - timedelta(hours=IDEAL_T24_H)
    t24 = min(snaps, key=lambda x: abs((x[0] - target).total_seconds()))
    if t24[0] >= kickoff:
        t24 = None
    return t24, before[-1]


# ---------------- the market picture ----------------

def eligible(ev, captured):
    """{book: {team: price}} for quotes fresh enough to have been takeable."""
    q = {}
    for bk in ev.get("books", []):
        lu = parse_utc(bk.get("last_update"))
        if captured and lu and abs((captured - lu).total_seconds()) > STALENESS_MIN * 60:
            continue
        h2h = (bk.get("markets") or {}).get("h2h") or []
        named = {o["name"]: o["price"] for o in h2h
                 if o.get("name") and o.get("price") is not None}
        if len(named) == 2:
            q[bk["book"]] = named
    return q


def fair(q, a, b):
    """Proportionally de-vigged consensus from the median price per side.

    Built from EVERY eligible book, any tier. Consensus is a MEASUREMENT of
    where the market sits, and an offshore book's price is perfectly good market
    information. Only the recommendation is tier-restricted — see best().
    """
    pa = [v[a] for v in q.values() if a in v]
    pb = [v[b] for v in q.values() if b in v]
    if len(pa) < MIN_BOOKS or len(pb) < MIN_BOOKS:
        return None
    ia, ib = implied(statistics.median(pa)), implied(statistics.median(pb))
    tot = ia + ib
    return None if tot <= 0 else {a: ia / tot, b: ib / tot, "_ovr": tot - 1.0}


def best(q, team, tier1_only=True):
    """Best price for a side, plus how many books corroborate it.

    tier1_only because measurement and action need different book sets: the
    consensus wants breadth, but a RECOMMENDATION has to be a number the reader
    can actually take. The first live capture picked bovada, which most of the
    audience cannot use. See docs/FOOTBALL_PIPELINE.md section 3.
    """
    ps = [(v[team], bk) for bk, v in q.items()
          if team in v and (not tier1_only or bk in TIER1)]
    if not ps:
        return None
    price, book = max(ps, key=lambda x: x[0])
    ib = implied(price)
    near = sum(1 for p, _ in ps if implied(p) <= ib + 0.01)
    return {"price": price, "book": book, "near": near}


def unclassified_books(q):
    """Books in neither tier. An unknown book is neither takeable nor known
    offshore, and guessing which would be exactly the wrong instinct."""
    return sorted({b for b in q if b not in TIER1 and b not in TIER2})


# ---------------- the selection rule (section 4) ----------------

class NoMarket(Exception):
    """A game that cannot be covered, carrying the reason verbatim.

    Section 3: a game failing any marker is LISTED as NO MARKET, never silently
    dropped. The reason string is published, so it is written to be read by a
    member rather than by a developer.
    """


def evaluate(q, away, home):
    """Layer 1 for ONE game at ONE capture, plus the side the rule takes.

    Raises NoMarket with a published reason. Returns the data block that layer 2
    is allowed to write from and NOTHING ELSE — every field here is measured, so
    a writeup restricted to these fields cannot invent a statistic.
    """
    # CHECK ORDER IS DELIBERATE and matches grade_football.py's original order
    # exactly: books -> consensus -> takeable price -> unclassified -> pick ->
    # corroboration. When a game fails more than one marker only the FIRST
    # reason is published, so reordering these silently changes what a member
    # reads on the NO MARKET list. Preserved on extraction rather than tidied.
    if len(q) < MIN_BOOKS:
        raise NoMarket(f"NO MARKET ({len(q)} eligible books, need {MIN_BOOKS})")

    f = fair(q, away, home)
    if not f:
        raise NoMarket("NO MARKET (consensus not computable)")

    ba, bh = best(q, away), best(q, home)
    if not ba or not bh:
        raise NoMarket("NO TAKEABLE PRICE (no Tier-1 book quoting)")

    unk = unclassified_books(q)
    if unk:
        raise NoMarket(f"UNCLASSIFIED BOOK(S) {unk} - add them to a tier in "
                       f"price_test.py before this game can be used")

    # step 1 — the toll you pay to play this game at the best numbers available
    eff = implied(bh["price"]) + implied(ba["price"]) - 1.0

    # step 3 — the side whose best price sits furthest above de-vigged fair
    opts = [{"side": away, **ba, "gap": f[away] - implied(ba["price"])},
            {"side": home, **bh, "gap": f[home] - implied(bh["price"])}]
    pick = max(opts, key=lambda o: o["gap"])

    # step 2 — corroboration guard. fb-v0.2's rule selected the single largest
    # book-vs-consensus disagreement and posted CLV of -0.49: the biggest
    # outlier is usually a slow or wrong book whose number converges by close,
    # so selecting outliers means buying the price most likely to move against
    # you. Requiring corroboration removes the lone-outlier case. Do not relax.
    if pick["near"] < MIN_CORROBORATION:
        raise NoMarket(f"corroboration guard ({pick['near']} book at best price)")

    off_a, off_h = best(q, away, tier1_only=False), best(q, home, tier1_only=False)
    offshore = None
    for cand, side in ((off_a, away), (off_h, home)):
        if cand and cand["book"] in TIER2 and side == pick["side"]:
            offshore = {"price": cand["price"], "book": cand["book"]}

    return {
        "n_books": len(q),
        "fair_away": round(f[away], 5),
        "fair_home": round(f[home], 5),
        "raw_overround_pts": round(100 * f["_ovr"], 3),
        "eff_overround_pts": round(100 * eff, 3),
        "side": pick["side"],
        "best_price": pick["price"],
        "best_book": pick["book"],
        "books_at_best": pick["near"],
        "fair_side": round(f[pick["side"]], 5),
        # Market colour only. Never the recommendation (section 3).
        "offshore_best": offshore,
        # Filled in after the close/grading; None here so layer 2 cannot
        # narrate a number that does not exist yet.
        "move_pts": None,
        "clv_pts": None,
    }


def rank(games):
    """Section 4 step 1: ascending by effective overround, lowest = tightest.

    Ties break to more eligible books, then to the earlier kickoff — so two runs
    of the same slate produce the same play.
    """
    return sorted(games, key=lambda g: (g["eff_overround_pts"],
                                        -g["n_books"],
                                        g.get("kickoff_utc") or ""))


def assign(ranked):
    """Section 4 step 4: rank 1 premium, next qualifier free.

    ONE POOL ACROSS SPORTS (fp-v0.2). Expect the premium play to be college most
    weeks — college fields ~3x the games and rank 1 is a minimum, not a median,
    so more draws produce a better tail even though the typical college market
    is looser. That is the rule working; it ranks by the toll you pay and does
    not know what is on television.
    """
    premium = ranked[0] if ranked else None
    free = ranked[1] if len(ranked) > 1 else None
    return premium, free
