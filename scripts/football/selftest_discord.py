#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for football Discord delivery (gap G).

    python scripts/football/selftest_discord.py

NO NETWORK, NO WEBHOOK. This tests what gets BUILT, not what gets sent.

THE FAILURE IT EXISTS TO PREVENT is a full slate that silently will not post.
Discord caps a message at 2000 characters of content, 10 embeds, and 6000
characters across all embeds in one message. The character cap is the one that
bites: a writeup plus its numbers runs ~700 characters, so ten embeds overflow a
limit that a ten-per-message rule alone would never catch. A real college
Saturday is ~57 games. The fixture here inflates the slate well past that,
because the week this breaks will be the biggest week of the season.

It also asserts the redaction boundary the site page enforces: the FREE post may
not carry the premium play, and the members' post must.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord as fbd                                # noqa: E402

WEEK = "2026-09-08"

# Deliberately long: 90 words of prose plus a full number line is the realistic
# upper end of a writeup, and the limits must hold there rather than on a stub.
PROSE = ("The market is close to a coin flip here and the toll to play it is "
         "unusually small, which is the only reason this game is near the top "
         "of the board at all. The best corroborated price sits at more than "
         "one regulated book, so this is not a lone outlier being chased. "
         "Consensus has barely separated the sides since the number opened.")


def game(i, tier="slate"):
    return {"n_books": 10, "fair_away": 0.4812, "fair_home": 0.5188,
            "raw_overround_pts": 4.305, "eff_overround_pts": 1.749 + i * 0.01,
            "side": f"Team {i} Fighting Longnames", "best_price": -190,
            "best_book": "betmgm", "books_at_best": 2, "fair_side": 0.6491,
            "offshore_best": {"price": -185, "book": "bovada"},
            "league": "NCAA FBS", "matchup": f"AAAA{i} @ BBBB{i}",
            "kickoff_utc": "2026-09-12T20:00:00Z", "tier": tier,
            "writeup": PROSE}


def board(n):
    games = [game(i) for i in range(n)]
    games[0]["tier"] = "premium"
    games[1]["tier"] = "free"
    return {"slate_week": WEEK, "decision_made": True,
            "n_covered": n, "n_excluded": 14, "coverage_status": "covered",
            "premium": games[0], "free": games[1], "games": games,
            "no_market": [{"sport": "ncaaf", "matchup": f"CCCC{i} @ DDDD{i}",
                           "reason": "NO MARKET (3 eligible books, need 5)"}
                          for i in range(14)]}


def check_limits(msgs, label, fails):
    for i, m in enumerate(msgs, 1):
        content = m.get("content") or ""
        embeds = m.get("embeds", [])
        chars = sum(len(e.get("title", "")) + len(e.get("description", ""))
                    for e in embeds)
        if len(content) > 2000:
            fails.append(f"{label} msg {i}: content {len(content)} > 2000")
        if len(embeds) > 10:
            fails.append(f"{label} msg {i}: {len(embeds)} embeds > 10")
        if chars > 6000:
            fails.append(f"{label} msg {i}: embed chars {chars} > 6000")
        for e in embeds:
            if len(e.get("description", "")) > 4096:
                fails.append(f"{label} msg {i}: an embed description > 4096")
        # A message with neither content nor embeds is rejected by Discord.
        if not content and not embeds:
            fails.append(f"{label} msg {i}: empty message")


def main():
    fails = []
    for n in (2, 9, 57, 120):
        b = board(n)
        slate = fbd.slate_messages(b, WEEK)
        free = fbd.free_messages(b, WEEK)
        check_limits(slate, f"slate[{n}]", fails)
        check_limits(free, f"free[{n}]", fails)
        print(f"  {n:>3} games -> slate {len(slate):>2} messages, "
              f"free {len(free)} messages")

        # THE REDACTION BOUNDARY. The public post carries the free play only.
        blob = " ".join((m.get("content") or "") + " ".join(
            e["title"] + e["description"] for e in m.get("embeds", []))
            for m in free)
        if b["premium"]["matchup"] in blob:
            fails.append(f"free[{n}]: premium matchup leaked into the free post")
        if b["free"]["matchup"] not in blob:
            fails.append(f"free[{n}]: free play missing from the free post")

        sblob = " ".join((m.get("content") or "") + " ".join(
            e["title"] + e["description"] for e in m.get("embeds", []))
            for m in slate)
        if b["premium"]["matchup"] not in sblob:
            fails.append(f"slate[{n}]: premium missing from the members post")
        if "0 units" not in sblob:
            fails.append(f"slate[{n}]: members post should state 0 units")
        for barred in ("+EV", "our edge", "value play", "the model likes"):
            if barred.lower() in sblob.lower() or barred.lower() in blob.lower():
                fails.append(f"[{n}]: BARRED phrase {barred!r} present")

    # No-play weeks must still produce a sendable message.
    empty = {"slate_week": WEEK, "decision_made": True, "n_covered": 0,
             "n_excluded": 3, "coverage_status": "manual review",
             "premium": None, "free": None, "games": [], "no_market": []}
    check_limits(fbd.free_messages(empty, WEEK), "free[none]", fails)
    check_limits(fbd.slate_messages(empty, WEEK), "slate[none]", fails)
    print("    no-play week renders a sendable message in both channels")

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS - every slate size stays inside Discord's content, embed-count")
    print("and embed-character limits; the free post carries the free play and")
    print("not the premium one; no barred language in either channel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
