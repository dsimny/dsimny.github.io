#!/usr/bin/env python3
"""
Open Ledger Sports - regression suite for the Mercer premium wall.

    python scripts/football/selftest_mercer_premium.py

No network, no credits, temp directory only.

WHAT THIS PROTECTS. Until 2026-09-12 the Mercer card was committed to a PUBLIC
repository in plaintext and rendered in full before kickoff, so the premium
selection was readable by anyone from three separate places: the rendered page,
the raw week file served by GitHub Pages, and the commitment log itself, which
carried selection, price, units and book beside the hash. Encrypting the card
alone would not have been enough; each of those is tested here.

THE TEST THAT MATTERS MOST IS [3]. It renders with a decryption key present, so
the renderer HAS the plaintext in memory and must still refuse to publish it.
"No key, therefore nothing leaks" is not the property we need - a developer
rendering locally, or any run that can decrypt, must produce exactly the same
locked public HTML. If that stops being true, the leak returns quietly through
someone's laptop.
"""
import base64
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
os.chdir(ROOT)
os.environ.setdefault("BOARD_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())
os.environ.setdefault("WHOP_CHECKOUT_URL", "https://whop.example/checkout")
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import market      # noqa: E402
import mercer      # noqa: E402

WEEK = "2026-09-15"
KICK = "2026-09-20T20:25:00Z"
# Distinctive strings so a hit is unambiguous rather than a coincidence.
SECRETS = ["Over 51.5", "51.5", "+145", "BetRivers", "1.5 unit",
           "ZZWHYSECRET", "ZZAGAINSTSECRET", "ZZCHANGESECRET",
           "ZZTITLESECRET", "ZZINTROSECRET", "ZZLEANSECRET", "ZZPASSSECRET"]

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def fixture_event(final=False):
    return {"espn_event_id": "999", "kickoff_utc": KICK, "final": final,
            "status": "STATUS_FINAL" if final else "STATUS_SCHEDULED",
            "season_type": 2, "season_slug": "regular-season", "season_year": 2026,
            "away_score": 30 if final else None, "home_score": 24 if final else None,
            "away": "BAL", "away_abbr": "BAL", "away_name": "Baltimore Ravens",
            "home": "BUF", "home_abbr": "BUF", "home_name": "Buffalo Bills"}


def fixture_doc():
    return {"slate_week": WEEK, "title": "ZZTITLESECRET", "intro": "ZZINTROSECRET",
            "picks": [{"id": "w2-nfl-01", "sport": "nfl", "status": "OFFICIAL",
                       "away": "Baltimore Ravens", "home": "Buffalo Bills",
                       "kickoff_utc": KICK, "market": "total", "side": "over",
                       "line": 51.5, "price": 145, "units": 1.5, "book": "BetRivers",
                       "conviction": "strong", "why": "ZZWHYSECRET",
                       "case_against": "ZZAGAINSTSECRET",
                       "changes_my_mind": "ZZCHANGESECRET"}],
            "leans": [{"sport": "nfl", "away": "Baltimore Ravens", "home": "Buffalo Bills",
                       "kickoff_utc": KICK, "lean": "ZZLEANSECRET", "note": "n"}],
            "passes": [{"sport": "nfl", "away": "Baltimore Ravens", "home": "Buffalo Bills",
                        "kickoff_utc": KICK, "temptation": "ZZPASSSECRET", "reason": "r"}]}


def read_pages():
    out = {}
    for root, _d, fs in os.walk(mercer.OUT):
        for f in fs:
            p = os.path.join(root, f)
            out[os.path.relpath(p, mercer.OUT).replace("\\", "/")] = \
                io.open(p, encoding="utf-8").read()
    return out


tmp = tempfile.mkdtemp(prefix="olspremium")
try:
    mercer.DATA = os.path.join(tmp, "data")
    mercer.WEEKS = os.path.join(mercer.DATA, "weeks")
    mercer.LEDGER = os.path.join(mercer.DATA, "mercer_ledger.json")
    mercer.COMMITMENTS = os.path.join(mercer.DATA, "commitments.json")
    mercer.OUT = os.path.join(tmp, "football", "mercer")
    os.makedirs(mercer.WEEKS)
    stores = {"nfl": {"999": fixture_event()}, "ncaaf": {}}
    mercer.save_json(os.path.join(mercer.WEEKS, f"{WEEK}.json"), fixture_doc())

    print("[D] repo storage: no unrevealed plaintext card on disk")
    T0 = market.parse_utc(KICK) - timedelta(hours=40)
    rc = mercer.cmd_commit(WEEK, now=T0, stores=stores)
    plain, enc = mercer.week_paths(WEEK)
    check(rc == 0, "commit exits 0")
    check(os.path.exists(enc), "encrypted card written")
    check(not os.path.exists(plain), "plaintext card is GONE from disk")
    raw = open(enc, "rb").read()
    check(not any(s.encode() in raw for s in SECRETS), "no secret in the encrypted bytes")

    print("\n[E] commitment log carries proof only")
    c = mercer.load_commitments()["commitments"][0]
    present = [f for f in mercer.ACTIONABLE_FIELDS if f in c]
    check(not present, f"no actionable field in the commitment ({present or 'none'})")
    check(not any(s in json.dumps(c) for s in SECRETS), "no secret string in the entry")
    check(bool(c.get("sha256")) and bool(c.get("committed_utc")),
          "hash and commit timestamp both present")
    check(c.get("revealed") is False, "starts unrevealed")

    print("\n[A/C] pre-reveal public HTML leaks nothing, WITH the key available")
    check(mercer.load_week(WEEK) is not None,
          "renderer can decrypt here - so this is the hard case, not the easy one")
    mercer.cmd_render(stores=stores)
    pages = read_pages()
    check(len(pages) >= 3, f"{len(pages)} pages rendered")
    leaks = [(n, s) for n, h in pages.items() for s in SECRETS if s in h]
    check(not leaks, f"no secret in any page source ({leaks[:3] or 'none'})")
    for pat, label in [(r"display\s*:\s*none", "display:none"),
                       (r"(?<!backdrop-)filter\s*:\s*blur", "content blur"),
                       (r"visibility\s*:\s*hidden", "visibility:hidden"),
                       (r"<script", "script tag"),
                       (r"application/ld\+json", "embedded JSON-LD"),
                       (r"data-[a-z-]+=", "data attribute"),
                       (r"<!--", "HTML comment"),
                       # aria-hidden on the decorative avatar is an
                       # ACCESSIBILITY hint (skip this glyph, screen
                       # reader), not content hiding, so it is excluded
                       # by name rather than by loosening the check.
                       (r"(?<!aria-)hidden\s*=", "hidden attribute")]:
        hit = [n for n, h in pages.items() if re.search(pat, h, re.I)]
        check(not hit, f"no {label} ({hit or 'none'})")
    # backdrop-filter on the sticky header is a site-wide visual effect on the
    # nav bar, not content hiding. Asserted rather than merely tolerated.
    hdr = [n for n, h in pages.items() if "backdrop-filter:blur" in h]
    check(bool(hdr), "the only blur present is the shared header backdrop")
    aria = [n for n, h in pages.items() if 'aria-hidden="true"' in h]
    check(bool(aria), "the only 'hidden' is aria-hidden on the decorative avatar")

    print("\n[B] pre-reveal public HTML carries what it should")
    wk = pages.get(f"{WEEK}/index.html", "")
    for want, label in [("Premium pick locked", "locked messaging"),
                        ("Baltimore Ravens @ Buffalo Bills", "matchup"),
                        (c["sha256"], "commitment fingerprint"),
                        ("Join Premium", "premium CTA"),
                        ("1-800-GAMBLER", "legal language"),
                        ("Strong", "conviction")]:
        check(want in wk, f"locked card carries {label}")
    hub = pages.get("index.html", "")
    check("Premium pick locked" in hub, "the HUB is locked too, same rule")

    print("\n[F/G] reveal on grade publishes everything, win or lose")
    for label, ev, expect in (("WIN", fixture_event(final=True), "WIN"),):
        pass
    stores2 = {"nfl": {"999": fixture_event(final=True)}, "ncaaf": {}}
    T1 = market.parse_utc(KICK) + timedelta(hours=4)
    mercer.cmd_grade(stores=stores2, now=T1)
    c2 = mercer.load_commitments()["commitments"][0]
    check(c2.get("revealed") is True, "commitment marked revealed")
    check(os.path.exists(plain) and not os.path.exists(enc),
          "plaintext published, encrypted copy removed")
    mercer.cmd_render(stores=stores2)
    wk2 = read_pages()[f"{WEEK}/index.html"]
    missing = [s for s in ["Over 51.5", "+145", "BetRivers", "ZZWHYSECRET",
                           "ZZAGAINSTSECRET", "ZZTITLESECRET", "ZZLEANSECRET",
                           "ZZPASSSECRET"] if s not in wk2]
    check(not missing, f"post-reveal page shows everything ({missing or 'all present'})")
    check("Premium pick locked" not in wk2, "locked messaging gone")

    print("\n[I] committed values are immutable at grading")
    e = mercer.load_ledger()["entries"][0]
    check((e["price"], e["units"], e["book"], e["selection"]) ==
          (145, 1.5, "BetRivers", "Over 51.5"),
          "price, stake, book and selection are the committed ones")
    n0 = len(mercer.load_ledger()["entries"])
    mercer.cmd_grade(stores=stores2, now=T1)
    check(len(mercer.load_ledger()["entries"]) == n0, "regrading books nothing twice")

    print("\n[G] a LOSS reveals identically to a win")
    shutil.rmtree(mercer.DATA); os.makedirs(mercer.WEEKS)
    shutil.rmtree(mercer.OUT, ignore_errors=True)
    mercer.save_json(os.path.join(mercer.WEEKS, f"{WEEK}.json"), fixture_doc())
    mercer.cmd_commit(WEEK, now=T0, stores=stores)
    lose = fixture_event(final=True)
    lose["away_score"], lose["home_score"] = 20, 24   # total 44, Over 51.5 loses
    mercer.cmd_grade(stores={"nfl": {"999": lose}, "ncaaf": {}}, now=T1)
    mercer.cmd_render(stores={"nfl": {"999": lose}, "ncaaf": {}})
    wk3 = read_pages()[f"{WEEK}/index.html"]
    check(mercer.load_ledger()["entries"][0]["result"] == "LOSS", "booked as a LOSS")
    missing = [s for s in ["Over 51.5", "+145", "BetRivers", "ZZWHYSECRET",
                           "ZZAGAINSTSECRET"] if s not in wk3]
    check(not missing, f"the losing card reveals in full too ({missing or 'all present'})")

    print("\n[H] tampering after the stamp is refused")
    shutil.rmtree(mercer.DATA); os.makedirs(mercer.WEEKS)
    mercer.save_json(os.path.join(mercer.WEEKS, f"{WEEK}.json"), fixture_doc())
    mercer.cmd_commit(WEEK, now=T0, stores=stores)
    doc = mercer.load_week(WEEK)
    doc["picks"][0]["line"] = 49.5              # move the number after committing
    mercer.save_week(WEEK, doc, revealed=False)
    rc = mercer.cmd_grade(stores={"nfl": {"999": fixture_event(final=True)}, "ncaaf": {}}, now=T1)
    check(rc != 0, "grade exits non-zero on a tampered card")
    check(len(mercer.load_ledger()["entries"]) == 0, "nothing booked from a tampered card")
    check(mercer.load_commitments()["commitments"][0].get("revealed") is not True,
          "a tampered card is NOT revealed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nmercer premium selftest: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
