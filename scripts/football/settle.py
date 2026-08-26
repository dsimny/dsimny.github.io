#!/usr/bin/env python3
"""
Open Ledger Sports — football bet settlement (fb-v0.1).

Pure functions, no I/O, no network. Given a final score and a market, return
WIN / LOSS / PUSH. This exists as its own module because settlement is where
football differs from baseball in ways that are easy to get wrong from MLB
intuition, and because it is the one piece of the football pipeline that can be
tested exhaustively without waiting for a game to be played.

THREE THINGS THAT BITE:

1. THE MONEYLINE PUSHES. NFL regular-season games can end tied - 13 of the 4,363
   games since 2010. An earlier draft of the pre-registration asserted "the
   moneyline cannot push", carried over from baseball where it is true. This
   module's selftest replays all 13 real ties, so the claim cannot quietly
   revert.

2. PUSHES ARE ZERO, NOT HALF. A push returns the stake: it is not a half-win and
   not a loss. Grading it as anything else silently rewrites the record. On the
   spread this is not a rounding detail - |margin| = 3 happens in 14.6% of games
   and |margin| = 7 in 8.7%, so a whole-number line sits on a real spike of
   probability mass.

3. SIGN CONVENTION. `line` is always THE NUMBER ADDED TO THE SELECTED TEAM'S
   SCORE. A 3-point favourite is line = -3. A 7-point underdog is line = +7.
   Every caller uses this one convention; there is no "home line" vs "away line"
   variant, because that is how sign errors get in.

Run: python scripts/football/settle.py    (executes the selftest)
"""
WIN, LOSS, PUSH = "WIN", "LOSS", "PUSH"


def _require_scores(team_score, opp_score):
    if team_score is None or opp_score is None:
        raise ValueError("cannot settle a game without both final scores")


def settle_spread(team_score, opp_score, line):
    """Selected team at `line` (favourite negative, underdog positive)."""
    _require_scores(team_score, opp_score)
    if line is None:
        raise ValueError("cannot settle a spread without a line")
    adj = (team_score - opp_score) + line
    if adj > 0:
        return WIN
    if adj < 0:
        return LOSS
    return PUSH  # landed exactly on the number


def settle_total(team_score, opp_score, line, side):
    """side is 'over' or 'under'."""
    _require_scores(team_score, opp_score)
    if line is None:
        raise ValueError("cannot settle a total without a line")
    s = side.strip().lower()
    if s not in ("over", "under"):
        raise ValueError(f"total side must be 'over' or 'under', got {side!r}")
    total = team_score + opp_score
    if total == line:
        return PUSH
    over_hit = total > line
    return WIN if (over_hit == (s == "over")) else LOSS


def settle_moneyline(team_score, opp_score):
    """A tie is a PUSH. This is football, not baseball - see the docstring."""
    _require_scores(team_score, opp_score)
    if team_score > opp_score:
        return WIN
    if team_score < opp_score:
        return LOSS
    return PUSH


def pnl(result, units, american_odds):
    """Paper P/L. A push returns the stake: exactly 0, never a fraction of it."""
    if result == PUSH:
        return 0.0
    if result == LOSS:
        return -float(units)
    if american_odds is None:
        raise ValueError("cannot price a win without odds")
    o = float(american_odds)
    return round(units * (o / 100.0 if o > 0 else 100.0 / abs(o)), 4)


# --- selftest ---------------------------------------------------------------
# The 13 real ties in games.csv since 2010, as (away, away_score, home,
# home_score). Replayed rather than asserted so that "the moneyline can push"
# is proven against actual games every time this file runs.
REAL_TIES = [
    ("STL", 24, "SF", 24), ("MIN", 26, "GB", 26), ("CAR", 37, "CIN", 37),
    ("SEA", 6, "ARI", 6), ("WAS", 27, "CIN", 27), ("PIT", 21, "CLE", 21),
    ("MIN", 29, "GB", 29), ("DET", 27, "ARI", 27), ("CIN", 23, "PHI", 23),
    ("DET", 16, "PIT", 16), ("IND", 20, "HOU", 20), ("WAS", 20, "NYG", 20),
    ("GB", 40, "DAL", 40),
]


def _selftest():
    cases = []

    # Spread: favourite, underdog, and both sides of the number.
    cases += [
        ("fav -3 wins by 7", settle_spread(24, 17, -3), WIN),
        ("fav -3 wins by 3 (on the number)", settle_spread(20, 17, -3), PUSH),
        ("fav -3 wins by 2", settle_spread(19, 17, -3), LOSS),
        ("fav -3.5 wins by 3", settle_spread(20, 17, -3.5), LOSS),
        ("dog +7 loses by 3", settle_spread(17, 20, 7), WIN),
        ("dog +7 loses by 7 (on the number)", settle_spread(17, 24, 7), PUSH),
        ("dog +7 loses by 10", settle_spread(14, 24, 7), LOSS),
        ("pick'em, tie game", settle_spread(20, 20, 0), PUSH),
    ]

    # Total: over, under, and exactly on the number.
    cases += [
        ("over 44.5, total 45", settle_total(24, 21, 44.5, "over"), WIN),
        ("under 44.5, total 45", settle_total(24, 21, 44.5, "under"), LOSS),
        ("over 45, total 45", settle_total(24, 21, 45, "over"), PUSH),
        ("under 45, total 45", settle_total(24, 21, 45, "under"), PUSH),
        ("under 51, total 45", settle_total(24, 21, 51, "under"), WIN),
    ]

    # Moneyline, including all 13 real ties.
    cases += [
        ("ml win", settle_moneyline(24, 17), WIN),
        ("ml loss", settle_moneyline(17, 24), LOSS),
    ]
    for away, a, home, h in REAL_TIES:
        cases.append((f"ml real tie {away} {a} @ {home} {h}", settle_moneyline(a, h), PUSH))
        cases.append((f"ml real tie (home side) {home} {h}", settle_moneyline(h, a), PUSH))

    # P/L, with the push-is-zero rule stated as a test rather than a comment.
    cases += [
        ("pnl push is exactly 0", pnl(PUSH, 1.0, -110), 0.0),
        ("pnl push at long odds is still 0", pnl(PUSH, 2.5, +450), 0.0),
        ("pnl loss risks the stake", pnl(LOSS, 1.0, -110), -1.0),
        ("pnl win at -110", pnl(WIN, 1.0, -110), 0.9091),
        ("pnl win at +150", pnl(WIN, 1.0, 150), 1.5),
    ]

    # Refusals: missing data must raise, never default.
    for label, fn in [
        ("no scores", lambda: settle_moneyline(None, 17)),
        ("no line", lambda: settle_spread(24, 17, None)),
        ("bad total side", lambda: settle_total(24, 21, 44.5, "middle")),
        ("win without odds", lambda: pnl(WIN, 1.0, None)),
    ]:
        try:
            fn()
            cases.append((f"refuses: {label}", "did not raise", "ValueError"))
        except ValueError:
            cases.append((f"refuses: {label}", "ValueError", "ValueError"))

    failed = [(n, g, w) for n, g, w in cases if g != w]
    for name, got, want in cases:
        if got != want:
            print(f"  FAIL {name}: got {got!r}, want {want!r}")
    print(f"settlement selftest: {len(cases) - len(failed)}/{len(cases)} passed"
          f"{' - FAILURES ABOVE' if failed else ''}")
    print(f"  (of which {2 * len(REAL_TIES)} are replays of the {len(REAL_TIES)} "
          "real NFL ties since 2010 - the moneyline pushes)")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
