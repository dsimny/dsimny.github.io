#!/usr/bin/env python3
"""
Open Ledger Sports — layer 2, the reasoning layer (FOOTBALL_PIPELINE.md s.2).

An LLM writes the per-game narrative FROM layer 1's numbers. It never produces a
number, a probability, or a pick. Layer 3 selects the play and never reads this
prose. That boundary is the product's whole safety argument: as a writer over
fixed numbers a model is genuinely good, and as the model it would cost the
pre-registration, calibration, CLV and append-only ledger that ARE the brand.

------------------------------------------------------------------------------
THE VALIDATOR IS THE POINT OF THIS FILE
------------------------------------------------------------------------------
`FOOTBALL_WRITEUP_TEMPLATE.md` section 1 states the rule: the model receives a
filled data block and MAY NOT introduce a number that is not in it.

A PROSE INSTRUCTION IS NOT AN ENFORCEMENT MECHANISM. Asking a model not to
invent statistics is not the same as it not inventing statistics, and the
failure is invisible: an invented rate reads exactly like a measured one. The
template records what that looked like in the source format - `.354 xwOBA
against right-handed power`, `28.4% four-seam whiff`, `4.42 bullpen xFIP`, none
of which came from any feed - sitting next to an audited append-only ledger in
the same voice.

So every writeup is MECHANICALLY CHECKED before it can be published: extract
every numeral, and refuse any that is not derivable from that game's data block.
Fail -> regenerate ONCE -> fail again -> the game publishes with NO WRITEUP and
its numbers alone. A missing writeup is a small loss. A fabricated statistic
beside a public ledger is the whole brand.

WHAT THE VALIDATOR DOES NOT CATCH, stated plainly so nobody trusts it further
than it goes. It checks NUMBERS, not claims. "the market is drifting toward
Buffalo" contains no numeral and would pass while being unsupported; the prompt
bars that kind of sentence, and only the prompt does. The validator is a floor,
not a ceiling.

------------------------------------------------------------------------------
MODEL
------------------------------------------------------------------------------
Claude, via the Messages API over plain `requests` - no new dependency; the repo
already ships requests. Default claude-opus-5. Set OLS_WRITEUP_MODEL to override
(claude-sonnet-5 costs less per token) - one variable, no code change.

THREE THINGS THIS FILE GOT WRONG ON THE FIRST PASS, all found by checking the
current API contract instead of trusting recall, and all of which would have
surfaced as a silent no-prose slate on the first live run:
  - it sent `temperature`. Sampling parameters were REMOVED on Sonnet 5 and
    Opus 5 and now return a 400. Every call would have failed.
  - it set `cache_control` on the system prompt. The minimum cacheable prefix
    is ~1024 tokens and this prompt is ~500, so it would never have cached
    while reading like an optimisation.
  - it defaulted to Sonnet to save money. Picking a cheaper model is the
    owner's call, not a default to bake in quietly.

DEGRADE, NEVER DIE. No ANTHROPIC_API_KEY, an API error, a timeout, a refusal:
the board publishes with numbers and no prose, exactly as a missing Discord
webhook never fails a run.

Run:
  python scripts/football/writeup.py --week 2026-09-01 --dry-run   # prompt only
  python scripts/football/writeup.py --week 2026-09-01
  python scripts/football/writeup.py --selftest                    # validator only
"""
import argparse
import io
import json
import os
import re
import sys
import unicodedata

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market                                        # noqa: E402
import localenv                                      # noqa: E402

# Local runs read the key from an untracked .env.local; CI supplies it from repo
# secrets and always wins (localenv never overrides a real environment value).
# Without this there is no way to exercise the API outside a live CI slate.
localenv.load(verbose=False)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
# claude-opus-5 is the default because it is the current default model, not
# because this task needs the top tier. Choosing a cheaper model to save money
# is Daniel's decision to make, not one to bake in quietly: set
# OLS_WRITEUP_MODEL=claude-sonnet-5 to halve the rate. At ~57 short paragraphs a
# week either is small money; the difference is real but it is his to weigh.
MODEL = os.environ.get("OLS_WRITEUP_MODEL", "claude-opus-5").strip()
MAX_TOKENS = 500
TIMEOUT = 60

# The fields layer 2 may narrate. Anything else on the game dict is board
# bookkeeping (commitment hashes, ranks) and is not the model's business.
NARRATABLE = ("n_books", "fair_away", "fair_home", "raw_overround_pts",
              "eff_overround_pts", "side", "best_price", "best_book",
              "books_at_best", "fair_side", "offshore_best", "move_pts",
              "clv_pts", "league", "matchup", "away", "home", "kickoff_utc")

WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
         6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
         11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
         15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
         19: "nineteen", 20: "twenty"}

# Structural numerals that are part of a NAME, not a measurement. Kept tiny and
# explicit: every entry here is a hole in the validator, so each one must be a
# phrase a reader would never mistake for a statistic.
STRUCTURAL = ("tier 1", "tier-1", "tier 2", "tier-2")


# ---------------------------------------------------------------------------
# the validator
# ---------------------------------------------------------------------------

def _variants(x):
    """Every rendering of one number a writer might reasonably produce."""
    out = set()
    if x is None:
        return out
    if isinstance(x, bool):
        return out
    f = float(x)
    for v in (f, abs(f)):
        for dp in (0, 1, 2, 3, 4):
            out.add(f"{v:.{dp}f}")
        out.add(repr(v))
        # trailing zeros stripped: 1.20 -> 1.2, 24.0 -> 24
        for dp in (1, 2, 3):
            out.add(f"{v:.{dp}f}".rstrip("0").rstrip("."))
        if float(v).is_integer():
            out.add(str(int(v)))
            if 0 <= int(v) <= 20:
                out.add(WORDS[int(v)])
    return out


def allowed_numerals(g):
    """Every numeral derivable from ONE game's data block.

    Probabilities get their percentage forms too, because a writer says "49.6%"
    where the block says 0.4959 - that is the same measured quantity rendered
    for a human, not a new number.
    """
    ok = set()
    for k in ("n_books", "books_at_best", "best_price", "raw_overround_pts",
              "eff_overround_pts", "move_pts", "clv_pts"):
        ok |= _variants(g.get(k))
    for k in ("fair_away", "fair_home", "fair_side"):
        v = g.get(k)
        if v is not None:
            ok |= _variants(v)
            ok |= _variants(float(v) * 100.0)
    off = g.get("offshore_best")
    if isinstance(off, dict):
        ok |= _variants(off.get("price"))
    # Kickoff parts, so "kicks at 3:30" does not fail a writeup for saying when
    # the game is. Still derived from the block - no free numbers.
    k = market.parse_utc(g.get("kickoff_utc"))
    if k:
        for v in (k.year, k.month, k.day, k.hour, k.minute,
                  k.hour % 12 or 12):
            ok |= _variants(v)
    return ok


def _mask(text, g):
    """Blank out names before extracting numerals.

    WITHOUT THIS the validator fails honest writeups and, worse, teaches whoever
    reads the failures to loosen it. "San Francisco 49ers" contains 49;
    "888sport" contains 888. Those digits are parts of a NAME, so they are
    removed before anything is checked rather than added to the allowed set -
    masking a name cannot let an invented statistic through, whereas
    whitelisting 49 could.
    """
    names = []
    for k in ("matchup", "away", "home", "side", "best_book", "league"):
        v = g.get(k)
        if isinstance(v, str) and v:
            names.append(v)
    off = g.get("offshore_best")
    if isinstance(off, dict) and off.get("book"):
        names.append(off["book"])
    # Individual words too, so "Houston" masks even when the block only carries
    # "Houston Texans". Built into a SEPARATE list: extending `names` while
    # iterating it is an infinite loop, which is how this first shipped.
    words = [w for n in names for w in n.replace("@", " ").split()]
    names = names + words + list(STRUCTURAL)
    for n in sorted({n for n in names if len(n) > 1}, key=len, reverse=True):
        text = re.sub(re.escape(n), " ", text, flags=re.IGNORECASE)
    return text


_NUM = re.compile(r"[-+−–]?\d[\d,]*(?:\.\d+)?")


def extract_numerals(text, g):
    """Every numeric token a reader would take as a measurement."""
    masked = unicodedata.normalize("NFKC", _mask(text, g))
    found = []
    for m in _NUM.finditer(masked):
        tok = m.group(0).replace(",", "").replace("−", "-").replace("–", "-")
        found.append(tok)
    # Spelled-out numbers count too: "eleven books" when the block says 7 is
    # exactly the fabrication this exists to stop, and it carries no digit.
    #
    # "ONE" IS DELIBERATELY EXEMPT, and it is a known hole rather than an
    # oversight. In ordinary English "one" is overwhelmingly a determiner - "one
    # of the books", "more than one regulated book" - so counting it as a
    # numeral rejects honest prose constantly, and a validator that cries wolf
    # gets loosened by whoever is on shift when the board is late. The hole it
    # leaves is small and bounded: a writeup could understate a count as "one".
    # Every fabrication that actually matters - percentages, prices, rates,
    # records, counts of two or more - is still caught.
    low = masked.lower()
    for n, w in WORDS.items():
        if n == 1:
            continue
        if re.search(rf"\b{w}\b", low):
            found.append(w)
    return found


def validate(text, g):
    """[] if every numeral is derivable, else the offending tokens."""
    """
    THE MATCH IS EXACT AGAINST THE ALLOWED SET, IN ONE DIRECTION ONLY. All the
    rounding tolerance lives in allowed_numerals(), which expands each REAL
    value into the renderings a writer might use. The candidate token is never
    re-rounded to look for a match.

    That asymmetry is load-bearing and this shipped wrong the first time.
    Rounding the candidate too meant any number within rounding distance of a
    real one passed: "moved 2.5 points" rounds to "2", books_at_best is 2, so a
    fabricated line movement validated against an unrelated book count. Caught by
    selftest_writeup's line-movement case. Tolerance on both sides is not twice
    as forgiving, it is a hole.
    """
    ok = allowed_numerals(g)
    bad = []
    for tok in extract_numerals(text, g):
        cand = {tok, tok.lstrip("+"), tok.lstrip("+-"),
                tok.lstrip("+-").rstrip("."), tok.rstrip(".")}
        # Leading zeros are formatting, not a different number: "17:00" yields
        # "00", which is the block's minute 0. Integer-valued tokens also test
        # their canonical integer form. This normalises; it does not re-round.
        try:
            f = float(tok.lstrip("+"))
            if f.is_integer():
                cand.add(str(int(abs(f))))
                cand.add(str(int(f)))
        except ValueError:
            pass
        if cand & ok:
            continue
        bad.append(tok)
    return bad


# ---------------------------------------------------------------------------
# the prompt
# ---------------------------------------------------------------------------

SYSTEM = """You write one short paragraph about a football betting market for \
Open Ledger Sports, a publication whose entire brand is that its numbers are \
audited and its record is public.

THE ONE ABSOLUTE RULE: you may not write a number that is not in the DATA \
BLOCK you are given. Not a percentage, not a rate, not a record, not a score, \
not an injury count, not a temperature, not a rank. Every numeral you write is \
checked mechanically against the block and the paragraph is thrown away if it \
contains one that is not there. Rounding a number from the block is fine \
(0.4959 -> 49.6%). Inventing one is not.

WHAT YOU DO NOT KNOW, and must not imply you know: injuries, weather, travel, \
rest, coaching, momentum, efficiency ratings, matchup history, form, or any \
projection of the score. You have market prices and nothing else. This is a \
MARKET story, not a matchup story - two studies found this market cannot be \
out-forecast, which is exactly why the product reports the market instead of \
predicting the game.

BARRED LANGUAGE: "edge", "+EV", "value play", "the model likes", "lock", \
"confidence", or any phrasing implying the play is expected to win. No \
expectation claim of any kind is permitted. You are describing what the market \
says and what it costs to participate, not forecasting a result.

WHAT THE NUMBERS MEAN:
- de-vigged fair probability: the market's own view with the bookmaker margin \
stripped out.
- raw overround: the margin at consensus prices, in percentage points.
- effective overround: the margin at the BEST prices available - the toll you \
actually pay. Lower is a tighter, cleaner market.
- best price and the books at or near it: the number a reader can actually \
take, and how many regulated books corroborate it.
- offshore price: market colour only. Never a recommendation.

STYLE: 60-90 words, plain and blunt, no filler, no hype, no rhetorical \
questions. Do not open with the team names - the reader can see the matchup. \
Write about the market. One paragraph, no headings, no bullet points."""


def build_prompt(g):
    block = {k: g[k] for k in NARRATABLE if k in g and g[k] is not None}
    return ("DATA BLOCK (the only numbers you may use):\n"
            + json.dumps(block, indent=1, sort_keys=True)
            + "\n\nWrite the paragraph.")


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def have_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def call_claude(prompt):
    """One Messages API call. Returns text, or None on any failure.

    NO `temperature`. Sampling parameters (temperature / top_p / top_k) were
    REMOVED on Claude Sonnet 5 and Opus 5 and are rejected with a 400 - so the
    first version of this file, which passed temperature to vary the retry,
    would have failed EVERY call on Saturday and published a slate with no
    prose at all. Retry variation now comes from the prompt (see write_one),
    which is better anyway: it tells the model what was wrong instead of
    re-rolling the dice.

    NO `cache_control` either. The minimum cacheable prefix is ~1024 tokens and
    this system prompt is ~500, so a breakpoint here would silently never cache
    while looking like an optimisation. If the prompt grows past ~1024 tokens,
    add it back and confirm with usage.cache_read_input_tokens rather than
    assuming.
    """
    try:
        r = requests.post(API_URL, timeout=TIMEOUT, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }, json={
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        })
        if r.status_code != 200:
            print(f"    API {r.status_code}: {r.text[:160]}")
            return None
        body = r.json()
        # A safety classifier may decline (HTTP 200, stop_reason "refusal").
        # Betting copy is exactly the kind of content that can trip one, so
        # check before reading content rather than silently getting "".
        if body.get("stop_reason") == "refusal":
            cat = (body.get("stop_details") or {}).get("category")
            print(f"    model declined this game (refusal, category {cat})")
            return None
        parts = [b.get("text", "") for b in body.get("content", [])
                 if b.get("type") == "text"]
        return "".join(parts).strip() or None
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"    API call failed: {exc}")
        return None


def write_one(g, attempts=2):
    """Generate and validate. Returns (text, note). text is None if refused."""
    prompt = build_prompt(g)
    last_bad = None
    for i in range(attempts):
        # THE RETRY IS TARGETED, not a re-roll. It names the exact tokens the
        # validator rejected, which is both more likely to succeed and more
        # honest than varying a sampling parameter the API no longer accepts.
        attempt_prompt = prompt if not last_bad else (
            prompt + "\n\nYour previous attempt was REJECTED. It contained "
            f"numbers that are not in the data block: {', '.join(last_bad)}. "
            "Every numeral you write is checked against the block. Rewrite the "
            "paragraph using only numbers that appear above, or write it with "
            "no numbers at all.")
        text = call_claude(attempt_prompt)
        if text is None:
            return None, "no writeup (generation failed)"
        bad = validate(text, g)
        if not bad:
            return text, None
        last_bad = bad
        print(f"    attempt {i+1} rejected, ungrounded numerals: {bad}")
    # REFUSED, not published-with-a-warning. A paragraph that invented a number
    # twice does not get to appear next to an audited ledger.
    return None, f"no writeup (unverifiable numerals: {', '.join(last_bad)})"


# Below this success rate, a configured key is assumed BROKEN rather than the
# validator being strict. The two failure modes separate cleanly: the validator
# rejecting a genuinely awkward game costs one or two writeups, while an expired
# key, an exhausted balance or a wrong model name costs ALL of them. Half is
# comfortably between those and needs no tuning.
MIN_WRITEUP_RATE = 0.5


def annotate(board, verbose=True):
    """Add `writeup` to every covered game, in place. Returns a status dict.

    THE STATUS EXISTS BECAUSE THIS FAILURE IS OTHERWISE SILENT. Every path here
    degrades rather than raising - which is right, a board with numbers and no
    prose is still a board - but that means an expired key, a dry balance or a
    typo'd model produces a GREEN run, a normal-looking page, and prose that
    quietly stopped appearing. That is the same shape as the dead free-pick
    webhook that went unnoticed for a day and is why post_status.json exists.

    So the caller gets `degraded`, and board.py turns it into a non-zero exit
    AFTER everything has been published. Publish first, then go red: the slate
    still reaches members, and the run still tells somebody.
    """
    games = board.get("games", [])
    if not have_key():
        # Not degraded: no key configured is a deliberate state, not a fault.
        print("ANTHROPIC_API_KEY not set: publishing numbers without prose.")
        for g in games:
            g["writeup"] = None
            g["writeup_note"] = "no writeup (no API key configured)"
        return {"ok": 0, "refused": 0, "degraded": False,
                "reason": "no API key configured"}

    ok = refused = 0
    last_note = ""
    for g in games:
        if verbose:
            print(f"  {g.get('league','')} {g.get('matchup','')}")
        text, note = write_one(g)
        g["writeup"], g["writeup_note"] = text, note
        if text:
            ok += 1
            if verbose:
                print(f"    OK ({len(text.split())} words)")
        else:
            refused += 1
            last_note = note or last_note
    print(f"\n{ok} written, {refused} refused ({MODEL})")

    total = ok + refused
    rate = (ok / total) if total else 1.0
    degraded = bool(total) and rate < MIN_WRITEUP_RATE
    if degraded:
        print(f"\nWRITEUP FAILURE: only {ok} of {total} games were written "
              f"({rate*100:.0f}%, floor {MIN_WRITEUP_RATE*100:.0f}%).")
        print(f"  last reason: {last_note}")
        print("  A key IS configured, so this is not the intended no-prose "
              "mode. Check, in order: the key has not expired, the account "
              "has credit, and OLS_WRITEUP_MODEL names a real model.")
    return {"ok": ok, "refused": refused, "degraded": degraded,
            "reason": last_note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="slate week (Tuesday, YYYY-MM-DD)")
    ap.add_argument("--board", help="path to a plaintext board json")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt for the first game; call nothing")
    ap.add_argument("--selftest", action="store_true",
                    help="run the validator's adversarial cases and exit")
    args = ap.parse_args()

    if args.selftest:
        import selftest_writeup
        return selftest_writeup.main()

    path = args.board or (os.path.join(FB, f"board_{args.week}.json")
                          if args.week else None)
    if not path or not os.path.exists(path):
        print(f"no plaintext board at {path}. Build one with board.py first "
              f"(an encrypted board cannot be narrated without the key).")
        return 1
    with io.open(path, encoding="utf-8") as f:
        board = json.load(f)

    if args.dry_run:
        games = board.get("games", [])
        if not games:
            print("board has no covered games.")
            return 0
        print(f"MODEL {MODEL}\n\n--- SYSTEM ---\n{SYSTEM}\n")
        print(f"--- USER (game 1 of {len(games)}) ---\n{build_prompt(games[0])}")
        print("\n(--dry-run: no API call made)")
        return 0

    status = annotate(board)
    # Written either way - partial prose is still worth keeping, and the note on
    # each game records why any given one is missing.
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(board, f, indent=1, sort_keys=True)
    print(f"wrote {os.path.relpath(path, ROOT)}")
    return 1 if status.get("degraded") else 0


if __name__ == "__main__":
    sys.exit(main())
