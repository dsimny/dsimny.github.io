#!/usr/bin/env python3
"""
Open Ledger Sports - adversarial self-test for the layer-2 numeral validator.

    python scripts/football/selftest_writeup.py

WHY ADVERSARIAL. The validator is the only mechanical thing standing between a
generated paragraph and a public page that sits beside an audited ledger. A test
that only feeds it honest writeups proves nothing - it would pass with the
validator deleted. So every MUST-REJECT case below is a genuine attempt to smuggle
a fabricated number past it, and each one is a fabrication that has actually
appeared in this format's ancestor (the template records `.354 xwOBA`,
`28.4% four-seam whiff`, `4.42 bullpen xFIP`, `84 degrees`).

The MUST-ACCEPT cases matter just as much and are the easier thing to get wrong.
A validator that rejects honest prose gets loosened by whoever is on shift when
the board is late, and a loosened validator is no validator. "San Francisco
49ers" must not fail for containing 49.

NO API KEY AND NO NETWORK ARE NEEDED: this tests validation, not generation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writeup as W                                  # noqa: E402

# A real data block, shaped exactly like board.py emits. Numbers taken from the
# live 2026-08-25 capture (Buffalo @ Houston) so the fixture cannot drift from
# the real field set.
GAME = {
    "n_books": 7,
    "fair_away": 0.49604,
    "fair_home": 0.50396,
    "raw_overround_pts": 3.84,
    "eff_overround_pts": 1.22,
    "side": "Houston Texans",
    "best_price": 100,
    "best_book": "betrivers",
    "books_at_best": 2,
    "fair_side": 0.50396,
    "offshore_best": {"price": -110, "book": "bovada"},
    "move_pts": None,
    "clv_pts": None,
    "league": "NFL",
    "matchup": "BUF @ HOU",
    "away": "Buffalo Bills",
    "home": "Houston Texans",
    "kickoff_utc": "2026-09-13T17:00:00Z",
}

# A block whose team name contains digits, which is the classic false positive.
NINERS = dict(GAME, side="San Francisco 49ers", home="San Francisco 49ers",
              away="Los Angeles Rams", matchup="LAR @ SF",
              best_book="888sport")

MUST_ACCEPT = [
    ("bare numbers straight from the block",
     GAME,
     "The market is close to a coin flip: de-vigged fair value sits at 50.4% "
     "for Houston against 49.6% for Buffalo. Seven books quote it, and the "
     "best takeable number is +100 at betrivers with 2 books at or near it. "
     "The toll matters here - 3.84 points of raw overround compresses to 1.22 "
     "at best prices."),
    ("rounded and reworded, same quantities",
     GAME,
     "Consensus splits this almost exactly down the middle, 50% to 50%. Best "
     "corroborated price is even money, offered at two regulated books. "
     "Effective overround of 1.2 points is about as tight as this board gets."),
    ("spelled-out counts that are true",
     GAME,
     "Seven books price it and two sit at the best number, which is as much "
     "corroboration as a market this size offers."),
    ("team name containing digits, and a book name containing digits",
     NINERS,
     "San Francisco 49ers are the side the best price favours at 888sport, "
     "with 2 books at or near +100."),
    ("Tier 1 is a name, not a measurement",
     GAME,
     "The recommendation is restricted to Tier 1 books, and 2 of them sit at "
     "the best number."),
    ("kickoff time from the block",
     GAME,
     "Kicks at 17:00 UTC, and the market has 7 books quoting by then."),
    ("no numbers at all",
     GAME,
     "The market is tight, the best price is corroborated at more than one "
     "regulated book, and the toll to participate is unusually low."),
]

MUST_REJECT = [
    ("an invented efficiency stat - the template's exact failure mode",
     GAME,
     "Houston have converted 47.2% of third downs at home this season, which "
     "the market has not fully priced. Fair value sits at 50.4%.",
     "47.2"),
    ("an invented record",
     GAME,
     "Buffalo are 8-3 in this spot. De-vigged fair value is 49.6%.",
     "8"),
    ("an invented injury count",
     GAME,
     "With 3 starters out, Houston at +100 still looks like the market's "
     "honest number.",
     "3"),
    ("an invented temperature",
     GAME,
     "At 84 degrees this becomes a different game, though the market holds "
     "Houston at 50.4%.",
     "84"),
    ("a SPELLED-OUT count that contradicts the block",
     GAME,
     "Eleven books quote this market, and the best price is +100.",
     "eleven"),
    ("a plausible-looking probability that is simply not in the block",
     GAME,
     "The market gives Houston 63.5% here, best price +100 at betrivers.",
     "63.5"),
    ("an invented line-movement figure",
     GAME,
     "Houston has moved 2.5 points since open; fair value now 50.4%.",
     "2.5"),
    ("a number within ROUNDING DISTANCE of a real one - the hole that shipped",
     GAME,
     "Effective overround of 1.4 points, with Houston best priced at +100.",
     "1.4"),
    ("a decimal that rounds onto an unrelated field's value",
     GAME,
     "The line has drifted 6.8 points, though fair value holds at 50.4%.",
     "6.8"),
    ("a fabricated ledger record, which is the ancestor format's own bug",
     GAME,
     "Our record stands at 105-51 for +124.55 units. Houston best priced at "
     "+100.",
     "105"),
]


def main():
    fails = []
    print(f"{len(MUST_ACCEPT)} must-accept, {len(MUST_REJECT)} must-reject\n")

    print("MUST ACCEPT (honest prose - a validator that rejects these gets "
          "loosened, and\n              a loosened validator is no validator)")
    for label, g, text in MUST_ACCEPT:
        bad = W.validate(text, g)
        mark = "OK  " if not bad else "FAIL"
        print(f"  {mark} {label}")
        if bad:
            print(f"       falsely rejected: {bad}")
            fails.append(f"false reject [{label}]: {bad}")

    print("\nMUST REJECT (each is a real fabrication attempt)")
    for label, g, text, expect in MUST_REJECT:
        bad = W.validate(text, g)
        caught = bool(bad)
        mark = "OK  " if caught else "FAIL"
        print(f"  {mark} {label}")
        if not caught:
            print(f"       SMUGGLED THROUGH: {text[:70]}...")
            fails.append(f"missed fabrication [{label}]")
        elif expect not in " ".join(bad):
            # It caught something, but not the thing that was planted. Worth
            # knowing: it means the test is passing for the wrong reason.
            print(f"       caught {bad} but not the planted {expect!r}")
            fails.append(f"caught wrong token [{label}]: {bad} vs {expect!r}")

    # The allowed set must be derived, never open. If everything validates,
    # the validator is a no-op and every case above passes for free.
    ok = W.allowed_numerals(GAME)
    for impossible in ("47.2", "63.5", "84", "2.5"):
        if impossible in ok:
            fails.append(f"allowed set wrongly contains {impossible}")
    if not {"7", "seven", "1.22", "3.84", "100"} <= ok:
        missing = {"7", "seven", "1.22", "3.84", "100"} - ok
        fails.append(f"allowed set missing real values: {missing}")

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS - every fabrication was caught, every honest writeup survived,")
    print("and the allowed set is derived from the block rather than open.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
