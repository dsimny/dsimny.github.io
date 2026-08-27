#!/usr/bin/env python3
"""
Open Ledger Sports - prove the layer-2 API round-trip, on demand.

    python scripts/football/livecheck_writeup.py            # 2 games
    python scripts/football/livecheck_writeup.py --games 5

THIS ONE SPENDS MONEY. It is the only script in scripts/football/ that makes a
real, billable API call, which is why it is not named selftest_* and is never
wired into a workflow. Two games costs well under a cent.

WHY IT EXISTS. Everything else about layer 2 is tested offline: the numeral
validator has seventeen adversarial cases, the prompt renders, and the
degrade-when-broken path is exercised with the API faked out. The one thing none
of that can prove is that the KEY WORKS and the request shape is accepted - and
in normal operation that is only ever discovered on a live slate at the decision
moment, which is the worst possible moment to learn a key is wrong.

Three API defects were shipped in the first version of writeup.py and every one
of them would have surfaced exactly there: a `temperature` field that returns
400 on Sonnet 5 and Opus 5, a cache breakpoint below the cacheable minimum, and
a model default chosen for cost rather than by the owner. Run this after adding
a key, after rotating one, and after touching the request shape.

It uses a REAL game from the most recent capture, so the data block is the same
shape the live pipeline will send - not a hand-written fixture that could drift.

Read the key from an untracked .env.local (ANTHROPIC_API_KEY=...) or the
environment. Never pass it on the command line, where it lands in shell history.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import market                                        # noqa: E402
import writeup as W                                  # noqa: E402

ROOT = os.path.join(HERE, "..", "..")
ODDS = os.path.join(ROOT, "data", "football", "odds")


def real_games(limit):
    """Covered games from the newest capture of either sport."""
    out = []
    for sport in ("nfl", "ncaaf"):
        snaps = market.load_snapshots(sport, ODDS)
        if not snaps:
            continue
        when, name, snap = snaps[-1]
        for ev in snap.get("events", []):
            if len(out) >= limit:
                return out
            try:
                m = market.evaluate(market.eligible(ev, when),
                                    ev.get("away_raw"), ev.get("home_raw"))
            except market.NoMarket:
                continue
            m.update({"sport": sport,
                      "league": "NFL" if sport == "nfl" else "NCAA FBS",
                      "matchup": f"{ev.get('away')} @ {ev.get('home')}",
                      "away": ev.get("away_raw"), "home": ev.get("home_raw"),
                      "kickoff_utc": ev.get("commence_time")})
            out.append(m)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2)
    args = ap.parse_args()

    if not W.have_key():
        print("No ANTHROPIC_API_KEY found.\n")
        print("Add it to an untracked .env.local at the repo root:")
        print("    ANTHROPIC_API_KEY=sk-ant-...")
        print("\n.env.local is gitignored and localenv.py only reads names on "
              "its allowlist. The value is never printed by anything here.")
        return 2

    games = real_games(args.games)
    if not games:
        print("No covered games in the captures on disk yet - nothing to "
              "narrate. Captures land as each game reaches its T-24 window.")
        return 1

    print(f"model  {W.MODEL}")
    print(f"games  {len(games)} real, from the latest capture\n")

    ok = 0
    for g in games:
        print(f"--- {g['league']} {g['matchup']} ---")
        text, note = W.write_one(g)
        if not text:
            print(f"    REFUSED: {note}\n")
            continue
        ok += 1
        # Re-validate here as well as inside write_one. A live check that
        # trusted the code it is checking would prove less than it appears to.
        bad = W.validate(text, g)
        print(f"    {text}\n")
        print(f"    words {len(text.split())} | numerals verified against the "
              f"block: {'ALL' if not bad else f'FAILED {bad}'}\n")
        if bad:
            print("    ^ the validator disagrees with itself; investigate "
                  "before trusting a slate.\n")

    print(f"{ok}/{len(games)} written.")
    if ok:
        print("\nThe key works, the request shape is accepted, and the prose "
              "came back grounded. This is the link no offline test can cover.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
