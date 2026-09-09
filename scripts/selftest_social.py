#!/usr/bin/env python3
"""
Offline self-test for scripts/post_social.py — the Qualified / Daily Pick
fallback, and the brand rules that must survive it.

Follows the convention of scripts/football/selftest_*.py: plain assertions, no
test framework (requirements.txt pins none), each one NUMBERED to the
requirement it proves. Runs fully offline — no network, no credentials, no git
history, no full clone — and writes only inside a TemporaryDirectory.

The Qualified regression check (13) compares against a COMMITTED FIXTURE,
scripts/fixtures/qualified_social_golden.json, not against a git revision. An
earlier version shelled out to `git show <baseline>`, which fails in the
shallow clone `actions/checkout@v4` produces by default and after any history
rewrite — and it SKIPPED on failure, so the most important check in the file
could vanish while the suite still printed PASS. The fixture removes both the
git dependency and the skip: a missing, malformed, empty or short fixture is a
FAILURE. EXPECTED_CHECKS below is asserted at the end, so a check that silently
stops running fails the suite even if nothing reports FAIL.

  python scripts/selftest_social.py
"""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import post_social as ps  # noqa: E402

FAILURES = []
CHECKS = [0]

# Committed, git-independent regression ground truth for the QUALIFIED recap.
# It carries a FROZEN copy of the ledger it was generated from, because the
# Qualified post quotes running aggregates — comparing against the live ledger
# would break the first time any Qualified play grades, which is a false alarm,
# not a regression.
FIXTURE = os.path.join(ROOT, "scripts", "fixtures", "qualified_social_golden.json")
MIN_FIXTURE_DATES = 3

# Total checks this file is expected to run. Bump it deliberately when adding or
# removing a check; an unexplained drift means a check stopped executing.
EXPECTED_CHECKS = 206


def check(n, label, cond):
    CHECKS[0] += 1
    if cond:
        print(f"  ok  {n:>4}  {label}")
    else:
        print(f"  FAIL{n:>4}  {label}")
        FAILURES.append(f"{n} {label}")


# ---------------------------------------------------------------- fixtures --
# Shapes copied from the real files (verified 2026-09-08): the Qualified ledger
# carries pnl/units/units_net/roi_pct; the Daily ledger carries
# pnl_paper/pnl_staked/units_staked/paper_basis and paper_units_net/paper_roi_pct
# and has NO pnl, units, units_net or roi_pct at all.
QUAL = {
    "entries": [
        {"date": "2026-05-01", "gamePk": 1, "game": "AAA @ BBB",
         "pick": "Boston Red Sox ML (-134)", "units": 1.0, "confidence": "2u",
         "final_score": "4-2", "result": "WIN", "pnl": 0.75},
        {"date": "2026-05-01", "gamePk": 2, "game": "CCC @ DDD",
         "pick": "Chicago Cubs ML (-120)", "units": 0.5, "confidence": "1u",
         "final_score": "1-6", "result": "LOSS", "pnl": -0.5},
    ],
    "aggregates": {"record": "4-6", "wins": 4, "losses": 6, "voids": 0,
                   "units_net": -2.254, "units_risked": 6.5, "roi_pct": -34.68},
}

DAILY = {
    "entries": [
        {"date": "2026-06-01", "gamePk": 11, "game": "EEE @ FFF",
         "pick": "Atlanta Braves ML (+100, Caesars)", "market": "moneyline",
         "price": 100, "book": "Caesars", "units_staked": 0.0,
         "paper_basis": 0.25, "strategy": "daily", "final_score": "5-4",
         "result": "WIN", "pnl_paper": 0.25, "pnl_staked": 0.0},
        {"date": "2026-06-02", "gamePk": 12, "game": "GGG @ HHH",
         "pick": "Seattle Mariners ML (-110, FanDuel)", "market": "moneyline",
         "price": -110, "book": "FanDuel", "units_staked": 0.0,
         "paper_basis": 0.25, "strategy": "daily", "final_score": "2-7",
         "result": "LOSS", "pnl_paper": -0.25, "pnl_staked": 0.0},
        # PRODUCTION SHAPE for a void: grade.py (scripts/grade.py:249-255) only
        # assigns final_score in the non-void branch, so on a VOID the key is
        # ABSENT — not null. The fixture mirrors that exactly; the explicit-null
        # variant is exercised separately in check 7.8.
        {"date": "2026-06-03", "gamePk": 13, "game": "III @ JJJ",
         "pick": "New York Mets ML (-105, DraftKings)", "market": "moneyline",
         "price": -105, "book": "DraftKings", "units_staked": 0.0,
         "paper_basis": 0.25, "strategy": "daily",
         "result": "VOID", "pnl_paper": 0.0, "pnl_staked": 0.0},
        # Same date as the Qualified entries — used to prove priority.
        {"date": "2026-05-01", "gamePk": 14, "game": "KKK @ LLL",
         "pick": "Texas Rangers ML (+115, BetMGM)", "market": "moneyline",
         "price": 115, "book": "BetMGM", "units_staked": 0.0,
         "paper_basis": 0.25, "strategy": "daily", "final_score": "8-3",
         "result": "WIN", "pnl_paper": 0.29, "pnl_staked": 0.0},
    ],
    "aggregates": {"record": "16-10", "wins": 16, "losses": 10, "voids": 0,
                   "paper_units_net": 1.382, "paper_roi_pct": 21.26,
                   "staked_units_net": 0.0, "opened": "2026-08-09",
                   "note": "Daily Pick strategy (v0.15)."},
}


def claims(tag, text, staked=False):
    """The four disclosure facts, asserted individually so a reworded post cannot
    quietly drop one.

    On the ALLOCATED path facts 3 and 4 would be FALSE of that pick — it is not
    paper-only and it did not contribute 0u — so they must be ABSENT, and the
    text must instead state the recorded allocation, keep the ledger separation,
    and say outright that no wager is placed or accepted. House rule 8: copy
    describes what the site actually does today; house rule 5: analytics, not a
    sportsbook."""
    check(f"{tag}.1", "fact 1: published for transparency",
          "published for transparency" in text.lower())
    if staked:
        check(f"{tag}.2", "fact 2: tracked in the Daily Pick ledger only",
              "Daily Pick ledger" in text
              and ("tracked exclusively" in text or "ledger only" in text))
        check(f"{tag}.3", "fact 3 correctly ABSENT — not paper-only once allocated",
              "paper-only" not in text)
        check(f"{tag}.4", "fact 4 correctly ABSENT — claims no 0u contribution",
              "contribute 0u" not in text and "contributing 0u" not in text)
        check(f"{tag}.5", "states a human-authorized RECORDED ALLOCATION",
              "allocation" in text and ("human authorized" in text
                                        or "human-authorized" in text))
        check(f"{tag}.6", "keeps the Qualified separation",
              "official Qualified record" in text)
        check(f"{tag}.7", "the old ambiguous phrasing is gone",
              "tracked separately at 0 units" not in text)
        # THE POINT OF THIS SECTION: the software records an allocation. It never
        # places a bet and cannot verify one, so the copy may not assert one.
        check(f"{tag}.8", "NEVER claims a 'real stake'", "real stake" not in text)
        check(f"{tag}.9", "NEVER uses 'staked' as a public claim",
              "staked" not in text and "stake" not in text.replace("stakeholder", ""))
        check(f"{tag}.10", "states that no wagers are placed or accepted",
              "do not place or accept wagers" in text
              or "does not place or accept wagers" in text)
    else:
        check(f"{tag}.2", "fact 2: tracked separately", "tracked separately" in text)
        check(f"{tag}.3", "fact 3: paper-only on a flat 0.25u basis",
              "paper-only" in text and "0.25u basis" in text)
        check(f"{tag}.4", "fact 4: contributes 0u to the official Qualified record and bankroll",
              ("contribute 0u to the official Qualified record and bankroll" in text
               or "contributing 0u to the official Qualified record and bankroll" in text))
        check(f"{tag}.5", "the AMBIGUOUS phrasing is gone (it conflicted with the paper figures)",
              "tracked separately at 0 units" not in text)
        scoped = "0u to the official Qualified record"
        bare = [m.start() for m in re.finditer(r"(?<![\d.])0u", text)]
        check(f"{tag}.6", "every standalone '0u' is scoped to the Qualified contribution "
                          "(a '+0.00u' figure is a number, not a claim)",
              all(text[i:i + len(scoped)] == scoped for i in bare))
        check(f"{tag}.7", "the paper basis is named as accounting, not as a stake",
              "paper-only at a flat" in text or "paper-only flat" in text)


def write(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return p


def main():
    tmp_dir = tempfile.TemporaryDirectory()
    tmp = tmp_dir.name
    real_qual, real_daily, real_status = ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH, ps.STATUS_PATH

    empty = write(tmp, "empty.json", {"entries": [], "aggregates": None})
    qual = write(tmp, "qual.json", QUAL)
    daily = write(tmp, "daily.json", DAILY)
    ps.STATUS_PATH = os.path.join(tmp, "post_status.json")

    print("\n-- 1. Qualified takes priority when it has a settled entry --")
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = qual, daily
    kind, _ = ps.select_recap("2026-05-01")
    x1, fb1 = ps.build_x_text("2026-05-01"), ps.build_fb_text("2026-05-01")
    check(1.1, "select_recap returns 'qualified' when both ledgers have the date",
          kind == "qualified")
    check(1.2, "X uses the Qualified wording, not the Daily one",
          "Yesterday graded:" in x1 and "Daily Pick" not in x1)
    check(1.3, "Facebook uses the Qualified headline",
          "results for" in fb1 and "Daily Pick" not in fb1)
    check(1.4, "the same-date Daily entry is absent from the Qualified post",
          "Texas Rangers" not in fb1)

    print("\n-- 2. Daily Pick is used when Qualified has no entry for the date --")
    kind2, r2 = ps.select_recap("2026-06-01")
    x2, fb2 = ps.build_x_text("2026-06-01"), ps.build_fb_text("2026-06-01")
    check(2.1, "select_recap falls back to 'daily'", kind2 == "daily")
    check(2.2, "X built from the Daily ledger", x2 is not None and "Daily Pick" in x2)
    check(2.3, "Facebook built from the Daily ledger",
          fb2 is not None and "Atlanta Braves" in fb2)
    check(2.4, "fallback still happens when the Qualified FILE is absent entirely",
          (setattr(ps, "LEDGER_PATH", os.path.join(tmp, "nope.json")) or
           ps.select_recap("2026-06-01")[0]) == "daily")
    ps.LEDGER_PATH = qual
    check(2.5, "fallback happens when the Qualified ledger is empty",
          (setattr(ps, "LEDGER_PATH", empty) or ps.select_recap("2026-06-01")[0]) == "daily")
    ps.LEDGER_PATH = qual

    print("\n-- 3. Daily Pick output is explicitly labeled --")
    check(3.1, "X names the strategy in the result line", "Daily Pick graded:" in x2)
    check(3.2, "X names the strategy on the ledger line", "Daily Pick ledger (paper):" in x2)
    check(3.3, "Facebook headline says Daily Pick",
          fb2.splitlines()[0].endswith("Daily Pick result for Monday, June 1")
          and "Daily Pick" in fb2.splitlines()[0])
    check(3.4, "Facebook ledger line is labeled Daily Pick and paper",
          "Daily Pick ledger (paper):" in fb2)
    check(3.5, "Facebook carries the disclosure verbatim",
          ps.DISCLOSURE_FB.format(basis="0.25") in fb2)
    check(3.6, "X carries the disclosure verbatim",
          ps.DISCLOSURE_X.format(basis="0.25") in x2)
    check(3.7, "unit figures are marked paper, never bare",
          "u paper" in x2 and "+0.25u paper)" in fb2)
    for label, t in [("Facebook", fb2), ("X", x2)]:
        claims(f"3.8 {label}", t)

    print("\n-- 4. Qualified and Daily records are NEVER combined --")
    check(4.1, "Daily post carries the Daily record", "16-10" in x2 and "16-10" in fb2)
    check(4.2, "Daily post carries NO Qualified record", "4-6" not in x2 and "4-6" not in fb2)
    check(4.3, "Daily post carries no Qualified units/ROI",
          "-2.25" not in fb2 and "-34.7" not in fb2)
    check(4.4, "Qualified post carries the Qualified record", "4-6" in x1 and "4-6" in fb1)
    check(4.5, "Qualified post carries NO Daily record", "16-10" not in x1 and "16-10" not in fb1)
    check(4.6, "Qualified post carries no Daily paper units/ROI",
          "+1.38" not in fb1 and "21.3" not in fb1 and "paper" not in fb1)
    check(4.7, "the recap dicts share no unit key (schemas stay disjoint)",
          set(ps.recap("2026-05-01")) & set(r2) == {"nice", "entries", "day_w", "day_l",
                                                    "day_v", "record"})
    check(4.8, "daily_recap never reads the Qualified ledger",
          (setattr(ps, "LEDGER_PATH", os.path.join(tmp, "nope.json")) or
           ps.daily_recap("2026-06-01")["record"]) == "16-10")
    ps.LEDGER_PATH = qual
    check(4.9, "recap() never reads the Daily ledger",
          (setattr(ps, "DAILY_LEDGER_PATH", os.path.join(tmp, "nope.json")) or
           ps.recap("2026-05-01")["record"]) == "4-6")
    ps.DAILY_LEDGER_PATH = daily

    print("\n-- 5/6/7. Win, loss and void all publish --")
    for n, (date, result, chip, pnl) in enumerate(
            [("2026-06-01", "WIN", "✅", "+0.25"),
             ("2026-06-02", "LOSS", "❌", "-0.25"),
             ("2026-06-03", "VOID", "⚪", "+0.00")], start=5):
        xt, ft = ps.build_x_text(date), ps.build_fb_text(date)
        check(n + 0.1, f"{result}: X post is produced (no skip path)", xt is not None)
        check(n + 0.2, f"{result}: Facebook post is produced", ft is not None)
        check(n + 0.3, f"{result}: correct chip on the Facebook line", chip in ft)
        check(n + 0.4, f"{result}: paper P&L rendered ({pnl}u)", f"{pnl}u paper" in ft)
        check(n + 0.5, f"{result}: labeled Daily Pick", "Daily Pick" in xt and "Daily Pick" in ft)
        claims(f"{n}.9 {result} Facebook", ft)
        claims(f"{n}.9 {result} X", xt)
    check(7.6, "VOID day record reads 0-0-1", "0-0-1" in ps.build_x_text("2026-06-03"))
    check(6.6, "LOSS day record reads 0-1", "0-1," in ps.build_x_text("2026-06-02"))
    check(5.6, "WIN day record reads 1-0", "1-0," in ps.build_x_text("2026-06-01"))
    check(7.7, "VOID renders 'void' when final_score is absent (production shape)",
          ": void (" in ps.build_fb_text("2026-06-03")
          and "None" not in ps.build_fb_text("2026-06-03"))
    nullscore = write(tmp, "nullscore.json", {
        "entries": [dict(DAILY["entries"][2], final_score=None)],
        "aggregates": DAILY["aggregates"]})
    ps.DAILY_LEDGER_PATH = nullscore
    check(7.8, "VOID renders 'void' even if final_score is an explicit null",
          ": void (" in ps.build_fb_text("2026-06-03")
          and "None" not in ps.build_fb_text("2026-06-03"))
    ps.DAILY_LEDGER_PATH = daily

    print("\n-- 8. Neither ledger settled for the date -> nothing_to_post --")
    check(8.1, "select_recap returns (None, None)", ps.select_recap("2026-01-01") == (None, None))
    check(8.2, "X builder returns None", ps.build_x_text("2026-01-01") is None)
    check(8.3, "Facebook builder returns None", ps.build_fb_text("2026-01-01") is None)
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = empty, empty
    check(8.4, "both ledgers empty -> None", ps.build_fb_text("2026-06-01") is None)
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = qual, daily
    unsettled = write(tmp, "unsettled.json",
                      {"entries": [dict(DAILY["entries"][0], result="PENDING")],
                       "aggregates": DAILY["aggregates"]})
    ps.DAILY_LEDGER_PATH = unsettled
    check(8.5, "an unsettled entry is not treated as a result",
          ps.build_fb_text("2026-06-01") is None)
    ps.DAILY_LEDGER_PATH = daily
    argv = sys.argv[:]
    sys.argv = ["post_social.py", "facebook", "2026-01-01"]
    ps.main()
    sys.argv = argv
    logged = [p for p in ps.load_status()["posts"] if p["date"] == "2026-01-01"]
    check(8.6, "main() records nothing_to_post and posts nothing",
          len(logged) == 1 and logged[0]["result"] == "nothing_to_post"
          and logged[0]["mode"] == "facebook")

    print("\n-- 9. Facebook retains the site link --")
    check(9.1, "Qualified Facebook post ends with the site URL", fb1.endswith(ps.SITE))
    check(9.2, "Daily Facebook post ends with the site URL", fb2.endswith(ps.SITE))
    check(9.3, "the link is a real URL", ps.SITE.startswith("http"))

    print("\n-- 10. X carries no URL and stays within 280 --")
    longpick = write(tmp, "long.json", {
        "entries": [dict(DAILY["entries"][0],
                         pick="Los Angeles Angels of Anaheim " + "Extremely Long " * 12 + "ML")],
        "aggregates": DAILY["aggregates"]})
    for label, path, date in [("qualified", qual, "2026-05-01"),
                              ("daily", daily, "2026-06-01"),
                              ("daily w/ absurd pick name", longpick, "2026-06-01")]:
        if label == "qualified":
            ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = qual, daily
        else:
            ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = empty, path
        t = ps.build_x_text(date)
        check(10.1, f"{label}: <= {ps.X_LIMIT} chars ({len(t)})", len(t) <= ps.X_LIMIT)
        check(10.2, f"{label}: contains no URL",
              "http" not in t and "www." not in t and ".com" not in t
              and ps.SITE not in t)
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = qual, daily

    print("\n-- 11. Legal / responsible-gambling language survives everywhere --")
    for label, t in [("X qualified", x1), ("X daily", x2),
                     ("FB qualified", fb1), ("FB daily", fb2)]:
        check(11.1, f"{label}: 21+", "21+" in t)
        check(11.2, f"{label}: 1-800-GAMBLER", "1-800-GAMBLER" in t)
        check(11.3, f"{label}: not betting advice",
              "not betting advice" in t or "not betting advice" in t.lower()
              or "Analytics, not betting advice" in t)
    check(11.4, "Facebook posts carry the full legal paragraph",
          ps.LEGAL in fb1 and ps.LEGAL in fb2)
    check(11.5, "X posts carry the RG line verbatim", x1.endswith(ps.RG) and x2.endswith(ps.RG))
    over = "\n".join(["x" * 300, "y" * 300, "z" * 300])
    check(11.6, "the RG line survives a forced over-length X trim",
          ps.RG in ps._x_trim("h" * 100, "l" * 100, "t" * 500))

    print("\n-- 12. Idempotency stays per (date, platform) --")
    ps.STATUS_PATH = os.path.join(tmp, "idem.json")
    ps.record("facebook", "2026-06-01", "posted", status=200, detail="123_456")
    check(12.1, "facebook/2026-06-01 is now blocked",
          ps.already_posted("2026-06-01", "facebook") is True)
    check(12.2, "the OTHER platform, same date, is not blocked",
          ps.already_posted("2026-06-01", "x") is False)
    check(12.3, "the SAME platform, other date, is not blocked",
          ps.already_posted("2026-06-02", "facebook") is False)
    ps.record("x", "2026-06-01", "failed", status=402, detail="credits")
    check(12.4, "a failed post does not block a retry",
          ps.already_posted("2026-06-01", "x") is False)
    ps.record("x", "2026-06-01", "posted", status=200, detail="789")
    check(12.5, "the retry then records posted and blocks",
          ps.already_posted("2026-06-01", "x") is True)
    check(12.6, "facebook's record was not disturbed by x's writes",
          ps.already_posted("2026-06-01", "facebook") is True)
    modes = [(p["date"], p["mode"]) for p in ps.load_status()["posts"]]
    check(12.7, "one row per (date, mode) — no duplicates", len(modes) == len(set(modes)))
    check(12.8, "no credential value is ever written to the status log",
          not any("token" in json.dumps(p).lower() for p in ps.load_status()["posts"]))

    print("\n-- 14. The recorded-allocation branch (no code path reaches it today) --")
    staked = write(tmp, "staked.json", {
        "entries": [dict(DAILY["entries"][0], units_staked=0.25, pnl_staked=0.25)],
        "aggregates": dict(DAILY["aggregates"], staked_units_net=0.25)})
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = empty, staked
    fbs, xs = ps.build_fb_text("2026-06-01"), ps.build_x_text("2026-06-01")
    claims("14.1 Facebook allocated", fbs, staked=True)
    claims("14.2 X allocated", xs, staked=True)

    # HIERARCHY: the official RECORDED ALLOCATION must LEAD; the paper figure may
    # only appear as an explicitly labelled comparison, never as the headline.
    fb_day = next(l for l in fbs.splitlines() if l.startswith("Day: "))
    check(14.3, f"Facebook day line leads with the recorded allocation ({fb_day!r})",
          "recorded allocation" in fb_day and "paper" not in fb_day)
    check(14.4, "Facebook per-pick line reports the allocated result, not the paper one",
          "(+0.25u allocated)" in fbs and "u paper)" not in fbs)
    check(14.5, "Facebook ledger line is the recorded-allocation one",
          "Daily Pick ledger (recorded allocation):" in fbs)
    check(14.6, "the paper figure survives ONLY as a labelled comparison",
          "Paper comparison, flat 0.25u basis (not the recorded allocation):" in fbs)
    check(14.7, "the allocated headline precedes the paper comparison",
          fbs.index("Daily Pick ledger (recorded allocation):") < fbs.index("Paper comparison,"))
    check(14.8, "Facebook names the human-authorized allocation in the disclosure",
          "A human authorized and recorded a 0.25u allocation on this pick." in fbs)
    check(14.9, "Facebook says the allocation is an accounting entry, not a placed bet",
          "an accounting entry, not a placed bet" in fbs)
    x_head = xs.splitlines()[0]
    check("14.10", f"X leads with the allocated result ({x_head!r})",
          "allocated" in x_head and "paper" not in x_head)
    check(14.11, "X shows no paper figure at all beside the recorded one", "paper" not in xs)
    check(14.12, "X stays in budget and URL-free on the allocated branch",
          len(xs) <= ps.X_LIMIT and "http" not in xs)
    check(14.13, "legal language survives the allocated branch",
          ps.LEGAL in fbs and xs.endswith(ps.RG))
    check(14.14, "the allocated post is still labeled Daily Pick and still separate",
          "Daily Pick" in fbs and "Daily Pick" in xs)
    check(14.15, "NEITHER platform ever says 'real stake' or 'staked'",
          "real stake" not in fbs and "real stake" not in xs
          and "staked" not in fbs and "staked" not in xs)

    # And the zero-allocation branch must NOT drift into allocation language.
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = empty, daily
    fbz, xz = ps.build_fb_text("2026-06-01"), ps.build_x_text("2026-06-01")
    check(14.16, "zero-allocation Facebook leads with the paper result",
          next(l for l in fbz.splitlines() if l.startswith("Day: ")).endswith("u paper"))
    check(14.17, "zero-allocation X leads with the paper result", "paper" in xz.splitlines()[0])
    check(14.18, "zero-allocation posts claim no stake and no allocation",
          "staked" not in fbz and "staked" not in xz
          and "allocation" not in fbz and "allocation" not in xz)

    print("\n-- 15. The X disclosure is never what gets trimmed away --")
    huge = write(tmp, "huge.json", {
        "entries": [dict(DAILY["entries"][0], pnl_paper=-12345.6789)],
        "aggregates": dict(DAILY["aggregates"], record="1234-5678",
                           paper_units_net=-98765.4321, paper_roi_pct=-12345.6)})
    ps.DAILY_LEDGER_PATH = huge
    xh = ps.build_x_text("2026-06-01")
    check(15.1, f"absurd figures still fit in {ps.X_LIMIT} ({len(xh)})", len(xh) <= ps.X_LIMIT)
    claims("15.2", xh)
    check(15.3, "the ROI detail is what gave way, not a claim", "ROI" not in xh)
    check(15.4, "the RG line still survives", xh.endswith(ps.RG))
    check(15.5, "still labeled Daily Pick", "Daily Pick graded:" in xh)
    check(15.6, "still no URL", "http" not in xh and ".com" not in xh)
    ps.DAILY_LEDGER_PATH = daily
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = qual, daily

    print("\n-- 13. REGRESSION: Qualified output matches the committed fixture --")
    # No git, no network, no full clone. Every failure mode below FAILS; none skips.
    fx, why = None, ""
    try:
        with open(FIXTURE, encoding="utf-8") as f:
            fx = json.load(f)
    except FileNotFoundError:
        why = "fixture file is missing"
    except json.JSONDecodeError as exc:
        why = f"fixture is malformed JSON ({exc})"
    except OSError as exc:
        why = f"fixture is unreadable ({exc})"
    check(13.1, f"fixture loads: {os.path.relpath(FIXTURE, ROOT)}" + (f" — {why}" if why else ""),
          fx is not None)

    cases = (fx or {}).get("cases") or []
    frozen = (fx or {}).get("ledger") or {}
    check(13.2, f"fixture is non-empty ({len(cases)} case(s))", len(cases) > 0)
    check(13.3, f"fixture covers at least {MIN_FIXTURE_DATES} dates "
                f"({len(({c.get('date') for c in cases}) - {None})} distinct)",
          len({c.get("date") for c in cases} - {None}) >= MIN_FIXTURE_DATES)
    check(13.4, "every case carries both platforms' expected text",
          bool(cases) and all(c.get("x") and c.get("facebook") for c in cases))
    check(13.5, "fixture carries the frozen ledger the expectations were built from",
          bool(frozen.get("entries")) and bool(frozen.get("aggregates")))

    if fx and cases and frozen.get("entries"):
        # Feed the builders the FROZEN ledger, so a later Qualified grading (which
        # moves the running aggregates quoted in the post) cannot fail this check.
        frozen_path = write(tmp, "frozen_qualified.json", frozen)
        ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = frozen_path, empty
        site_was = ps.SITE
        ps.SITE = fx.get("site") or ps.SITE   # the fixture records the URL it used
        try:
            bad_x = [c["date"] for c in cases if ps.build_x_text(c["date"]) != c["x"]]
            bad_fb = [c["date"] for c in cases if ps.build_fb_text(c["date"]) != c["facebook"]]
            check(13.6, f"X byte-identical on all {len(cases)} fixture dates"
                        + (f" — differs: {bad_x}" if bad_x else ""), not bad_x)
            check(13.7, f"Facebook byte-identical on all {len(cases)} fixture dates"
                        + (f" — differs: {bad_fb}" if bad_fb else ""), not bad_fb)
        finally:
            ps.SITE = site_was
    else:
        check(13.6, "X comparison could not run — fixture unusable", False)
        check(13.7, "Facebook comparison could not run — fixture unusable", False)

    # The bug this change fixed, asserted against the real ledgers.
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = real_qual, real_daily
    check(13.8, "a real Daily-only date has no Qualified entry (so it used to post nothing)",
          ps.recap("2026-09-06") is None)
    check(13.9, "and now posts as a labeled Daily Pick",
          "Daily Pick" in (ps.build_fb_text("2026-09-06") or ""))

    print("\n-- 16. DAILY PICK copy is locked by the fixture, both branches --")
    for n, (sec, label) in enumerate([("daily_zero_allocation", "zero-allocation"),
                                      ("daily_allocated", "recorded allocation")], start=1):
        blk = (fx or {}).get(sec) or {}
        dcases, dledger = blk.get("cases") or [], blk.get("ledger") or {}
        check(f"16.{n}.1", f"{label}: fixture section present and non-empty",
              bool(dcases) and bool(dledger.get("entries")))
        if not (dcases and dledger.get("entries")):
            check(f"16.{n}.2", f"{label}: comparison could not run", False)
            continue
        dpath = write(tmp, f"frozen_{sec}.json", dledger)
        ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = empty, dpath
        site_was, ps.SITE = ps.SITE, (fx.get("site") or ps.SITE)
        try:
            bx = [c["date"] for c in dcases if ps.build_x_text(c["date"]) != c["x"]]
            bf = [c["date"] for c in dcases if ps.build_fb_text(c["date"]) != c["facebook"]]
            check(f"16.{n}.2", f"{label}: X byte-identical on {len(dcases)} date(s)"
                               + (f" — differs: {bx}" if bx else ""), not bx)
            check(f"16.{n}.3", f"{label}: Facebook byte-identical on {len(dcases)} date(s)"
                               + (f" — differs: {bf}" if bf else ""), not bf)
        finally:
            ps.SITE = site_was
        # The fixture is public copy: assert the wager claim can never creep in.
        blob = json.dumps(dcases, ensure_ascii=False)
        check(f"16.{n}.4", f"{label}: fixture asserts no 'real stake' anywhere",
              "real stake" not in blob)
        check(f"16.{n}.5", f"{label}: every fixture X output is <= {ps.X_LIMIT}",
              all(len(c["x"]) <= ps.X_LIMIT for c in dcases))
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = real_qual, real_daily

    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH, ps.STATUS_PATH = real_qual, real_daily, real_status
    tmp_dir.cleanup()

    # A check that stops running reports nothing at all, so the count is asserted.
    # This is what makes a vanished test loud instead of invisible.
    if CHECKS[0] != EXPECTED_CHECKS:
        FAILURES.append(f"check count drifted: ran {CHECKS[0]}, expected {EXPECTED_CHECKS} "
                        f"(update EXPECTED_CHECKS deliberately, or find the check that "
                        f"stopped running)")

    print(f"\n{'=' * 66}")
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} problem(s) across {CHECKS[0]} checks:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"PASS — {CHECKS[0]} checks, 0 failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
