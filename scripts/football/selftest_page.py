#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the football site surface (gap F).

    python scripts/football/selftest_page.py

Renders the week page in BOTH states from a fixture board and asserts the
redaction actually redacts.

The point is not that it renders. The point is that the pre-kickoff page cannot
be used to reconstruct the premium play, because the selection rule is public
and deterministic - so every number that would let someone recompute rank 1 must
be absent until the week is graded.
"""
import copy, io, json, os, re, shutil, sys, tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import market, board as boardmod, page

WEEK = "2026-09-08"
FIXTURE_WEEKS = {"2026-09-01", "2026-09-08"}
SRC = {"nfl": "nfl_20260825T030847Z.json", "ncaaf": "ncaaf_20260825T030833Z.json"}
ODDS = os.path.join(ROOT, "data", "football", "odds")


def scan_body(html):
    """The HTML with presentation stripped, ready for the leak scan.

    STYLING IS NOT CONTENT, and mixing the two produced a false alarm that stood
    for days. The scan looks for the premium play's numbers loose in the page; a
    CSS length is a number too. `page.py` renders the site logo with
    `style="width:300px;max-width:100%;height:auto;..."`, and with a premium
    price of +100 the scan matched `100` inside `max-width:100%` and reported
    `REDACTED page leaks premium numbers: ['best_price=100']`. There was no leak
    — no live or committed football page has ever contained `best_price`.

    A test that cries wolf gets loosened exactly like a validator that does, so
    the fix narrows the haystack rather than the assertion. `<style>` blocks were
    already removed; inline `style="..."` attributes were not, and that is where
    the collision lived.
    """
    out = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    out = re.sub(r'\sstyle="[^"]*"', "", out)
    out = re.sub(r"\sstyle='[^']*'", "", out)
    return out


# How production actually renders each premium field, so the scan looks for what
# a real leak would look like rather than for a bare integer. Only best_price
# goes through page.money() (`+100`); the rest are emitted as plain str(v).
# Keeping these in step with page.py's `rows` is the point — if that rendering
# changes, this is the place to follow it.
PREMIUM_FIELDS = ("best_price", "eff_overround_pts", "raw_overround_pts",
                  "books_at_best", "n_books")


def rendered_forms(field, v):
    """Every string shape `v` could legitimately take in the published page."""
    if field == "best_price":
        # page.py:163 -> money(v). Both the formatted form and the bare digits
        # are checked: the formatted one is what a real leak looks like, and the
        # bare one is now safe to check because scan_body() removed the CSS that
        # used to collide with it.
        return {page.money(v), str(v)}
    return {str(v)}


def premium_leaks(prem, body):
    """Premium numbers found loose in a REDACTED page. Empty list is the pass."""
    leaks = []
    for field in PREMIUM_FIELDS:
        v = prem.get(field)
        if v is None:
            continue
        for form in rendered_forms(field, v):
            # Single characters are skipped: a lone digit collides with
            # everything and proves nothing.
            if len(form) > 1 and re.search(
                    rf"(?<![\d.]){re.escape(form)}(?![\d.])", body):
                leaks.append(f"{field}={form}")
    # The emptiness guard is not cosmetic: `"" in body` is always True, so a
    # play with no best_book would have been reported as leaking one.
    book = str(prem.get("best_book", "")).strip()
    if book and book in body:
        leaks.append(f"best_book={book}")
    # The side is the thing that must never appear pre-kickoff.
    side = str(prem.get("side", ""))
    home, away = str(prem.get("home", "")), str(prem.get("away", ""))
    if side and side != home and side != away and side in body:
        leaks.append("side")
    return leaks


def shift(snap, when):
    s = copy.deepcopy(snap)
    old = datetime.fromisoformat(s["captured_utc"].replace("Z", "+00:00"))
    d = when - old
    s["captured_utc"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    for ev in s["events"]:
        for bk in ev.get("books", []):
            if bk.get("last_update"):
                t = datetime.fromisoformat(bk["last_update"].replace("Z", "+00:00")) + d
                bk["last_update"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return s


tmp = tempfile.mkdtemp(prefix="olspage")
try:
    odds = os.path.join(tmp, "odds"); os.makedirs(odds)
    for sport, f in SRC.items():
        src = json.load(io.open(os.path.join(ODDS, f), encoding="utf-8"))
        kicks = {market.parse_utc(e["commence_time"]) for e in src["events"]
                 if e.get("commence_time")
                 and market.slate_week(market.parse_utc(e["commence_time"])) in FIXTURE_WEEKS}
        for k in sorted(kicks):
            when = k - timedelta(hours=24)
            io.open(os.path.join(odds, f"{sport}_{when.strftime('%Y%m%dT%H%M%SZ')}.json"),
                    "w", encoding="utf-8").write(json.dumps(shift(src, when), indent=1))

    boardmod.ODDS_DIR = odds
    D = boardmod.decision_moment(WEEK)
    b = boardmod.build(["nfl", "ncaaf"], WEEK, D + timedelta(hours=6), commit=False)
    # Fake prose so the card path is exercised without an API key.
    for g in b["games"]:
        g["writeup"] = "The market is tight and the best price is corroborated."

    page.OUT = os.path.join(tmp, "football")
    page.FB = os.path.join(ROOT, "data", "football")
    plain = os.path.join(tmp, f"board_{WEEK}.json")
    io.open(plain, "w", encoding="utf-8").write(json.dumps(b, indent=1))
    boardmod.board_paths = lambda w: (plain, plain + ".enc")

    prem = b["premium"]
    print(f"premium play: {prem['league']} {prem['matchup']} -> "
          f"{prem['side']} {prem['best_price']:+d}, eff {prem['eff_overround_pts']}")
    print(f"covered {b['n_covered']}, excluded {b['n_excluded']}\n")

    fails = []
    for reveal in (False, True):
        page.render_week(WEEK, reveal)
        h = io.open(os.path.join(page.OUT, WEEK, "index.html"), encoding="utf-8").read()
        # Leak-check the BODY only. The stylesheet is full of incidental digits
        # (max-width:100%, rgba(0,0,0,.5)) and matching them produced a false
        # "premium price leaked" on the first run - a test that cries wolf gets
        # loosened exactly like a validator that does.
        body = scan_body(h)
        label = "REVEALED" if reveal else "REDACTED"
        print(f"--- {label}: {len(h)} bytes ---")

        # Legal + no-claim on every state.
        for must in ("1-800-GAMBLER", "not a sportsbook", "no claim that these plays win"):
            if must.lower() not in h.lower():
                fails.append(f"{label}: missing {must!r}")
        for barred in ("+EV", "our edge", "value play", "the model likes"):
            if barred.lower() in h.lower():
                fails.append(f"{label}: BARRED phrase {barred!r} present")

        # The NO MARKET list is public in both states.
        if b["no_market"] and "No market" not in h:
            fails.append(f"{label}: no-market section missing")

        if not reveal:
            # THE REDACTION TEST. None of the premium play's numbers may appear.
            leaks = premium_leaks(prem, body)
            if leaks:
                fails.append(f"REDACTED page leaks premium numbers: {leaks}")
            else:
                print("    premium numbers absent (side, price, book, overround)")
            # Matchup and 0 units ARE meant to be there.
            if prem["matchup"] not in h:
                fails.append("REDACTED: premium matchup should be listed")
            if "0 units" not in h:
                fails.append("REDACTED: should state 0 units")
            print("    matchup + 0 units present, as House Rule 7 requires")
        else:
            if str(prem.get("best_book", "")) not in h:
                fails.append("REVEALED: premium book missing after reveal")
            else:
                print("    premium published in full after grading")

    # ---- NEGATIVE CONTROL ----------------------------------------------
    # Narrowing the scan is only safe if it still catches the thing it exists
    # to catch. A guard loosened to stop a false alarm, and never re-proven, is
    # how a boundary quietly stops being enforced. So: inject each premium
    # number into a page body the way production would actually render it, and
    # require the scan to find it.
    # Multi-digit values throughout, deliberately. A single digit cannot be
    # distinguished from page noise — `len(form) > 1` skips it — so a leaked
    # one-digit n_books would NOT be caught. That is a real, accepted limit of
    # this scan, not something the control should paper over by pretending
    # otherwise. The fields that actually reconstruct the play (price, book,
    # side) are never single-digit.
    control = {"best_price": -135, "best_book": "pinnacle", "side": "Some Side FC",
               "home": "Home FC", "away": "Away FC", "n_books": 12,
               "books_at_best": 11, "eff_overround_pts": 1.22,
               "raw_overround_pts": 2.41}
    clean_page = ('<html><head><style>.x{max-width:100%}</style></head><body>'
                  '<img style="width:300px;max-width:100%;height:auto">'
                  '<p>Held until this week is graded. 0 units.</p></body></html>')
    if premium_leaks(control, scan_body(clean_page)):
        fails.append("CONTROL: a clean page was reported as leaking")

    injections = {
        "price as rendered": f'<tr><td>Best takeable price</td><td>{page.money(control["best_price"])} at x</td></tr>',
        "price as bare digits": "<p>the number is -135 flat</p>",
        "book": f'<td>{control["best_book"]}</td>',
        "side": f'<td>{control["side"]}</td>',
        "eff overround": f'<strong>{control["eff_overround_pts"]}</strong> pts',
        "raw overround": f'<td>{control["raw_overround_pts"]} -> x</td>',
        "eligible books": f'<td>Eligible books</td><td>{control["n_books"]}</td>',
    }
    for what, html in injections.items():
        leaked = premium_leaks(control, scan_body(clean_page.replace(
            "</body>", html + "</body>")))
        if not leaked:
            fails.append(f"CONTROL: an injected premium {what} was NOT caught")
    # And the specific collision that caused the false alarm must stay quiet:
    # a CSS length equal to the price is styling, not a leak.
    css_only = ('<html><body><img style="width:300px;max-width:100%">'
                '<div style="width:135px"></div></body></html>')
    if premium_leaks({"best_price": 100}, scan_body(css_only)):
        fails.append("CONTROL: a CSS length was reported as a leaked price")
    print(f"    negative control: {len(injections)} injected leaks all caught, "
          f"CSS lengths ignored")

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS - redacted page cannot reconstruct the play; revealed page")
    print("publishes it in full; legal and no-claim copy present in both;")
    print("and the leak scan still catches every injected premium number.")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
