#!/usr/bin/env python3
"""
Open Ledger Sports — Instagram caption construction (PHASE 1: TEXT ONLY).

THIS FILE MAKES NO NETWORK CALLS AND READS NO CREDENTIAL. There is deliberately
no container creation, no media_publish, no token, no `requests` import and no
status file here. Phase 2 adds the transport in a separate module; keeping the
copy in its own file first means the wording can be reviewed, frozen and
regression-tested before a single byte reaches Meta.

WHY IT REUSES post_social.py RATHER THAN RESTATING THE COPY. The Facebook
disclosure wording is load-bearing and already guarded by 206 offline checks —
that an allocated pick is never called paper-only, that "real stake" never
appears, that the four facts of the Daily Pick disclosure all survive. Retyping
any of it here would create a second copy that drifts. So select_recap(), LEGAL,
DISCLOSURE_FB, DISCLOSURE_FB_STAKED, _day_record, _running, _running_daily and
_basis_str are IMPORTED. The leading underscore on some of them marks them
private to post_social's own callers; reusing them across the two posters is
deliberate, and selftest_instagram.py asserts the imported text still matches
what post_social publishes, so a change there fails here rather than silently
diverging.

THREE THINGS DIFFER FROM THE FACEBOOK POST, ALL ON PURPOSE:

  1. NO URL. Instagram renders caption links as inert text, so a URL is visual
     noise that cannot be clicked. The caption ends with a pointer to the
     profile bio instead. That makes the bio link a LAUNCH PREREQUISITE, not a
     nicety: without it the caption points at nothing.
  2. NO EMOJI. The card draws WIN/LOSS/VOID as primitives because the vendored
     font renders ✅ and ❌ as the same tofu box; the caption uses the literal
     words for the same reason and for parity with the card.
  3. AN EXPLICIT NO-WAGER SENTENCE ON EVERY CAPTION. house rule 5 requires
     "analytics not a sportsbook, no bets accepted". ps.LEGAL carries "Analytics,
     not betting advice" but not the no-bets half, and DISCLOSURE_FB_STAKED
     carries the full sentence only on the allocated branch. NO_WAGER below
     closes that gap on every branch, and is suppressed only when the assembled
     text already says it — so it is stated exactly once, never twice.

TWO LEDGERS, NEVER MIXED. select_recap() picks the strategy; _qualified() and
_daily() build from disjoint keys and never reference each other's figures.
EXACTLY ONE running record appears in a caption.

Run:  python scripts/post_instagram.py caption <YYYY-MM-DD>
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import post_social as ps  # noqa: E402

# Instagram's documented caption ceiling. No live post comes close; this is a
# guard against a pathological slate, not the usual path.
CAPTION_LIMIT = 2200

# The caption's only route to the site. Instagram makes caption URLs unclickable,
# so the link lives in the profile bio and the copy says so plainly.
BIO_LINE = "Full ledger — every pick, every result — at the link in our bio."

# House rule 5, stated in the post rather than left to a footer. The pipeline
# records unit ALLOCATIONS; it does not place bets, never contacts a sportsbook,
# and cannot verify that money moved.
NO_WAGER = "Open Ledger Sports does not place or accept wagers."

# The substring that proves the no-wager fact is already present, so it is never
# printed twice (DISCLOSURE_FB_STAKED ends with its own copy of it).
_NO_WAGER_MARK = "does not place or accept wagers"

RESULT_WORD = {"WIN": "WIN", "LOSS": "LOSS", "VOID": "VOID"}

# A long slate is capped in the caption exactly as it is on the card, so the two
# never disagree about how much of a night they show.
MAX_ROWS = 6


def _entry_line(e, amount):
    """`WIN · Boston Red Sox ML (-134) — Final 4-2 (+0.75u)`

    Book names inside `pick` (e.g. "(+100, Caesars)") are kept verbatim: the
    price source is a fact about the graded entry and stating it is the point of
    an auditable ledger. It is text attribution only — no bookmaker mark, logo
    or link appears anywhere in the caption or on the card.
    """
    word = RESULT_WORD.get(e.get("result", ""), "—")
    score = e.get("final_score") or "void"
    return f"{word} · {e.get('pick', '')} — Final {score} ({amount})"


def _rows(entries, amount_fn, cap):
    lines = [_entry_line(e, amount_fn(e)) for e in entries[:cap]]
    if len(entries) > cap:
        lines.append(f"+{len(entries) - cap} more in the ledger.")
    return lines


def _assemble(blocks):
    """Blocks are paragraphs; empty ones drop out. Keeps the blank-line rhythm
    consistent between the two card types."""
    return "\n\n".join(b for b in blocks if b)


def _with_no_wager(body_blocks):
    """Append the no-wager sentence unless the text already carries it."""
    joined = "\n".join(b for b in body_blocks if b)
    if _NO_WAGER_MARK in joined:
        return body_blocks
    return body_blocks + [NO_WAGER]


def _qualified(r, cap=MAX_ROWS, roi=True):
    day = ps._day_record(r["day_w"], r["day_l"], r["day_v"])
    running = ps._running(r) if roi else f"{r['record']} · {r['units_net']:+.2f}u net"
    blocks = [
        f"Open Ledger Sports — results for {r['nice']}",
        "\n".join(_rows(r["entries"], lambda e: f"{e['pnl']:+.2f}u", cap)),
        f"Day: {day} · {r['day_pnl']:+.2f}u\n"
        f"Running ledger (Qualified Plays): {running}",
    ]
    return _assemble(_with_no_wager(blocks + [ps.LEGAL]) + [BIO_LINE])


def _daily(r, cap=MAX_ROWS, roi=True, comparison=True):
    day = ps._day_record(r["day_w"], r["day_l"], r["day_v"])
    staked = r["staked_units"]

    if staked:
        # A human authorised an allocation, so the OFFICIAL allocated result
        # leads and the paper figure survives only as a labelled comparison —
        # never as the headline, never unlabelled beside the recorded one.
        rows = _rows(r["entries"],
                     lambda e: f"{(e.get('pnl_staked') or 0):+.2f}u allocated", cap)
        net = r["staked_net"] if r["staked_net"] is not None else 0.0
        ledger = [
            f"Day: {day} · {r['staked_pnl']:+.2f}u on a {staked:.2f}u recorded allocation",
            f"Daily Pick ledger (recorded allocation): {r['record']} · {net:+.2f}u net",
        ]
        if comparison:
            ledger.append(
                f"Paper comparison, flat {ps._basis_str(r)}u basis "
                f"(not the recorded allocation): {ps._running_daily(r)}")
        disclosure = ps.DISCLOSURE_FB_STAKED.format(stake=f"{staked:.2f}")
    else:
        rows = _rows(r["entries"],
                     lambda e: f"{(e.get('pnl_paper') or 0):+.2f}u paper", cap)
        running = (ps._running_daily(r) if roi
                   else f"{r['record']} · "
                        f"{(r['paper_units_net'] or 0.0):+.2f}u")
        ledger = [
            f"Day: {day} · {r['day_pnl_paper']:+.2f}u paper",
            f"Daily Pick ledger (paper): {running}",
        ]
        disclosure = ps.DISCLOSURE_FB.format(basis=ps._basis_str(r))

    blocks = [
        f"Open Ledger Sports — Daily Pick result for {r['nice']}",
        "\n".join(rows),
        "\n".join(ledger),
        disclosure,
    ]
    return _assemble(_with_no_wager(blocks + [ps.LEGAL]) + [BIO_LINE])


def build_caption(date):
    """The caption for `date`, or None when nothing settled.

    TRIM ORDER IS A SAFETY PROPERTY, not a formatting preference. If the text
    ever exceeds 2200 characters the LEDGER DETAIL degrades first — the paper
    comparison, then the running ROI, then the number of result rows. The
    disclosure, the legal paragraph, the no-wager sentence and the bio line are
    never candidates for removal at any rung. A unit figure published without
    its disclosure is the misleading artefact; a missing ROI is not.
    """
    kind, r = ps.select_recap(date)
    if kind is None:
        return None

    if kind == "qualified":
        ladder = [
            lambda: _qualified(r),
            lambda: _qualified(r, roi=False),
            lambda: _qualified(r, cap=3, roi=False),
            lambda: _qualified(r, cap=1, roi=False),
        ]
    else:
        ladder = [
            lambda: _daily(r),
            lambda: _daily(r, comparison=False),
            lambda: _daily(r, comparison=False, roi=False),
            lambda: _daily(r, cap=3, comparison=False, roi=False),
            lambda: _daily(r, cap=1, comparison=False, roi=False),
        ]

    text = ladder[0]()
    for rung in ladder:
        text = rung()
        if len(text) <= CAPTION_LIMIT:
            return text
    # Every rung still too long: return the tightest form rather than a text
    # sliced mid-sentence, which could truncate the legal language itself.
    return text


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "caption"
    if mode != "caption":
        print(f"Phase 1 supports only 'caption'. Live publishing lands in Phase 2.")
        return 2
    from datetime import datetime, timedelta
    default = (datetime.now(ps.ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    date = args[1] if len(args) > 1 else default
    text = build_caption(date)
    if text is None:
        print(f"No settled entries for {date} — nothing to caption.")
        return 0
    print(text)
    print(f"\n[{len(text)} characters]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
