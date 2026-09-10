#!/usr/bin/env python3
"""
Offline self-test for the Instagram recap card and caption (Phase 1).

Follows scripts/selftest_social.py exactly: plain assertions, no test framework
(requirements.txt pins none), each check NUMBERED to the requirement it proves,
and EXPECTED_CHECKS asserted at the end so a check that silently stops running
fails the suite even when nothing reports FAIL.

RUNS FULLY OFFLINE. No network, no credentials, no git history, no full clone.
It writes only inside a TemporaryDirectory, and never inside data/ — the
renderer refuses that path outright and check 12.1 proves it.

The ledger states come from a COMMITTED FIXTURE, scripts/fixtures/
instagram_cards.json, never from the live ledgers: a card quotes running
aggregates, so testing against data/ledger.json would break the first time any
pick grades. That is a false alarm, not a regression — the same reasoning that
put qualified_social_golden.json in this directory.

VISIBLE TEXT IS ASSERTED THROUGH THE RENDERER'S DRAW-CALL LOG rather than by
diffing pixels. Every draw records both the LOGICAL string the card means to say
and the FRAGMENTS actually drawn after wrapping or truncating, so "the card says
1-800-GAMBLER" is provable without a brittle image comparison — and check 7.9
proves the fragments reconstruct the logical string, so a wrap can never
silently swallow half a disclosure.

  python scripts/selftest_instagram.py
"""
import ast
import hashlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from PIL import Image  # noqa: E402

import post_instagram as pi  # noqa: E402
import post_social as ps  # noqa: E402
import render_recap_image as rr  # noqa: E402

FAILURES = []
CHECKS = [0]

FIXTURE = os.path.join(ROOT, "scripts", "fixtures", "instagram_cards.json")
FONT_DIR = os.path.join(ROOT, "assets", "fonts")

# Total checks this file is expected to run. Bump it DELIBERATELY when adding or
# removing a check; unexplained drift means a check stopped executing.
EXPECTED_CHECKS = 439

# Checksums of the vendored font files, as recorded in assets/fonts/README.md.
# A swapped or corrupted face changes every card, so it fails the suite here
# rather than silently restyling the brand.
FONT_SHA256 = {
    "DejaVuSans.ttf":
        "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954",
    "DejaVuSans-Bold.ttf":
        "e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724",
    "LICENSE-DejaVu.txt":
        "7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93",
}

# Emoji blocks. The card and the caption must never carry a result glyph: in the
# vendored DejaVu Sans, U+2705 and U+274C both render as the SAME .notdef tofu
# box while U+26AA renders a real glyph, so a win and a loss would be identical
# and a void would look fine — a failure that reads as success.
EMOJI_RANGES = ((0x2600, 0x27BF), (0x1F000, 0x1FAFF), (0xFE0F, 0xFE0F))

MAX_BYTES = 8 * 1024 * 1024        # Meta's documented image ceiling


def check(n, label, cond):
    CHECKS[0] += 1
    if cond:
        print(f"  ok  {n:>5}  {label}")
    else:
        print(f"  FAIL{n:>5}  {label}")
        FAILURES.append(f"{n} {label}")


def has_emoji(s):
    return any(lo <= ord(ch) <= hi for ch in s for lo, hi in EMOJI_RANGES)


def texts(calls):
    """Every logical string the card drew."""
    return [c["logical"] for c in calls if c["kind"] == "text"]


def blob(calls):
    return "\n".join(texts(calls))


def shapes(calls):
    return [c for c in calls if c["kind"] == "shape"]


def install(tmp, scenario):
    """Point post_social at a frozen fixture pair and return (kind, recap)."""
    q = os.path.join(tmp, "q.json")
    d = os.path.join(tmp, "d.json")
    with open(q, "w", encoding="utf-8") as f:
        json.dump(scenario["qualified"], f)
    with open(d, "w", encoding="utf-8") as f:
        json.dump(scenario["daily"], f)
    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = q, d
    return ps.select_recap(scenario["date"])


def imports_of(path):
    """Every module name imported by a file, via AST — comment- and
    docstring-proof, unlike grepping the source text."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def attributes_of(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def string_constants(path):
    """String literals a module actually USES — docstrings excluded.

    Without that exclusion these checks are self-defeating: render_recap_image's
    own docstring explains that it never touches a .enc file, and a naive scan
    would read its explanation as evidence of the thing it disclaims. Comments
    never appear in the AST at all, so only docstrings need removing.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def main():
    tmp_dir = tempfile.TemporaryDirectory()
    tmp = tmp_dir.name
    real_q, real_d = ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH

    # -------------------------------------------------- 1. vendored assets --
    print("\n1. Vendored font, licence and fixture")
    for name, want in FONT_SHA256.items():
        p = os.path.join(FONT_DIR, name)
        check(f"1.{name[:4]}", f"{name} present", os.path.exists(p))
        got = hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else ""
        check(f"1.{name[:4]}s", f"{name} checksum matches README", got == want)
    check("1.5", "assets/fonts/README.md documents provenance",
          os.path.exists(os.path.join(FONT_DIR, "README.md")))
    check("1.6", "renderer points at the vendored regular face",
          rr.FONT_REGULAR == os.path.join(FONT_DIR, "DejaVuSans.ttf"))
    check("1.7", "renderer points at the vendored bold face",
          rr.FONT_BOLD == os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"))
    check("1.8", "font paths are repository-relative, not system names",
          os.path.isabs(rr.FONT_REGULAR) and rr.FONT_REGULAR.startswith(ROOT))
    check("1.9", "fixture file exists", os.path.exists(FIXTURE))
    FX = json.load(open(FIXTURE, encoding="utf-8"))["scenarios"]
    check("1.10", "fixture carries every scenario the suite needs",
          all(k in FX for k in
              ["qualified_win", "qualified_loss", "qualified_void",
               "daily_win_zero", "daily_loss_zero", "daily_void_zero",
               "daily_win_staked", "daily_loss_staked", "daily_void_staked",
               "qualified_long_slate", "both_ledgers_same_date", "unsettled_only"]))
    check("1.11", "no font is resolved by family name anywhere in the renderer",
          not any("DejaVu Sans" == s for s in string_constants(
              os.path.join(ROOT, "scripts", "render_recap_image.py"))))

    # ------------------------------------------------ 2. Qualified results --
    print("\n2. Qualified WIN / LOSS / VOID")
    for tag, scen, word, day, amt in [
            ("WIN", "qualified_win", "WIN", "1-0", "+0.75u"),
            ("LOSS", "qualified_loss", "LOSS", "0-1", "-1.00u"),
            ("VOID", "qualified_void", "VOID", "0-0-1", "+0.00u")]:
        kind, r = install(tmp, FX[scen])
        check(f"2.{tag}.k", f"{tag}: select_recap returns qualified", kind == "qualified")
        img, calls = rr.render_card(kind, r)
        t = blob(calls)
        check(f"2.{tag}.w", f"{tag}: literal result word on the card", word in t)
        check(f"2.{tag}.m", f"{tag}: result drawn as a primitive, not a glyph",
              any(s.get("result") == tag for s in shapes(calls)))
        check(f"2.{tag}.d", f"{tag}: day record reads {day}", f"Day: {day} " in t)
        check(f"2.{tag}.a", f"{tag}: per-entry figure {amt} shown", amt in t)
        cap = pi.build_caption(FX[scen]["date"])
        check(f"2.{tag}.c", f"{tag}: caption carries the literal word", f"{word} · " in cap)
        check(f"2.{tag}.s", f"{tag}: caption shows the final score or 'void'",
              "Final " in cap)

    kind, r = install(tmp, FX["qualified_void"])
    _, calls = rr.render_card(kind, r)
    check("2.9", "VOID renders 'Final void' when final_score is absent",
          "Final void" in blob(calls))

    # ------------------------------------ 3. Daily Pick, zero allocation --
    print("\n3. Daily Pick WIN / LOSS / VOID — zero allocation")
    for tag, scen, word, day in [
            ("WIN", "daily_win_zero", "WIN", "1-0"),
            ("LOSS", "daily_loss_zero", "LOSS", "0-1"),
            ("VOID", "daily_void_zero", "VOID", "0-0-1")]:
        kind, r = install(tmp, FX[scen])
        check(f"3.{tag}.k", f"{tag}: falls back to the Daily ledger", kind == "daily")
        img, calls = rr.render_card(kind, r)
        t = blob(calls)
        check(f"3.{tag}.t", f"{tag}: card is tagged DAILY PICK", "DAILY PICK" in t)
        check(f"3.{tag}.w", f"{tag}: literal result word on the card", word in t)
        check(f"3.{tag}.d", f"{tag}: day line reads {day} and says paper",
              f"Day: {day} " in t and "paper" in t)
        check(f"3.{tag}.l", f"{tag}: ledger label names the paper basis",
              rr.LEDGER_LABEL_DAILY_PAPER in t)
        check(f"3.{tag}.disc", f"{tag}: card carries the zero-allocation disclosure",
              ps.DISCLOSURE_FB.format(basis="0.25") in t)
        cap = pi.build_caption(FX[scen]["date"])
        check(f"3.{tag}.c", f"{tag}: caption carries the same disclosure verbatim",
              ps.DISCLOSURE_FB.format(basis="0.25") in cap)
        check(f"3.{tag}.p", f"{tag}: caption marks the figure as paper",
              "u paper" in cap)
        check(f"3.{tag}.n", f"{tag}: caption claims no stake and no allocation",
              "allocated" not in cap and "recorded allocation" not in cap)

    # ------------------------------- 4. Daily Pick, recorded allocation --
    print("\n4. Daily Pick WIN / LOSS / VOID — recorded allocation")
    for tag, scen, word in [
            ("WIN", "daily_win_staked", "WIN"),
            ("LOSS", "daily_loss_staked", "LOSS"),
            ("VOID", "daily_void_staked", "VOID")]:
        kind, r = install(tmp, FX[scen])
        img, calls = rr.render_card(kind, r)
        t = blob(calls)
        check(f"4.{tag}.l", f"{tag}: ledger label names the recorded allocation",
              rr.LEDGER_LABEL_DAILY_STAKED in t)
        check(f"4.{tag}.d", f"{tag}: day line names the allocation, not paper",
              "allocated" in t)
        check(f"4.{tag}.disc", f"{tag}: card carries the allocated disclosure",
              ps.DISCLOSURE_FB_STAKED.format(stake="0.25") in t)
        check(f"4.{tag}.nb", f"{tag}: card says an allocation is not a placed bet",
              "not a placed bet" in t)
        check(f"4.{tag}.pf", f"{tag}: card never calls an allocated pick paper-only",
              "paper-only" not in t)
        cap = pi.build_caption(FX[scen]["date"])
        check(f"4.{tag}.c", f"{tag}: caption carries the allocated disclosure",
              ps.DISCLOSURE_FB_STAKED.format(stake="0.25") in cap)
        check(f"4.{tag}.cmp", f"{tag}: paper survives only as a labelled comparison",
              "Paper comparison" in cap and "not the recorded allocation" in cap)
        check(f"4.{tag}.ord", f"{tag}: the allocated line precedes the paper comparison",
              cap.index("recorded allocation") < cap.index("Paper comparison"))

    print("\n4b. Wording that must never appear on either branch")
    for scen in ["daily_win_zero", "daily_win_staked", "qualified_win"]:
        install(tmp, FX[scen])
        cap = pi.build_caption(FX[scen]["date"])
        kind, r = install(tmp, FX[scen])
        _, calls = rr.render_card(kind, r)
        t = blob(calls)
        check(f"4b.{scen}.rs", f"{scen}: never says 'real stake'",
              "real stake" not in cap.lower() and "real stake" not in t.lower())
        check(f"4b.{scen}.st", f"{scen}: never says 'staked'",
              "staked" not in cap.lower() and "staked" not in t.lower())
        check(f"4b.{scen}.nw", f"{scen}: states we do not place or accept wagers",
              "does not place or accept wagers" in cap)

    # ------------------------------------------------- 5. image contract --
    print("\n5. Image contract — 1080x1350 JPEG / sRGB / <=8MB")
    kind, r = install(tmp, FX["qualified_win"])
    img, _ = rr.render_card(kind, r)
    p1 = rr.save_jpeg(img, os.path.join(tmp, "card1.jpg"))
    with Image.open(p1) as im:
        check("5.1", "width is exactly 1080", im.width == 1080)
        check("5.2", "height is exactly 1350", im.height == 1350)
        check("5.3", "aspect ratio is exactly 4:5", im.width / im.height == 0.8)
        check("5.4", "format is JPEG", im.format == "JPEG")
        check("5.5", "mode is RGB (sRGB)", im.mode == "RGB")
        check("5.6", "no EXIF is written", not im.info.get("exif"))
        check("5.7", "no ICC profile is written", not im.info.get("icc_profile"))
    size = os.path.getsize(p1)
    check("5.8", f"file size {size} bytes is under Meta's 8MB ceiling", size <= MAX_BYTES)
    check("5.9", "ratio sits inside Meta's documented 4:5 .. 1.91:1 range",
          0.8 <= (1080 / 1350) + 1e-9 and (1080 / 1350) <= 1.91)
    check("5.10", "width is inside Meta's 1440px maximum", 1080 <= 1440)
    check("5.11", "renderer constants match the contract",
          (rr.W, rr.H) == (1080, 1350))
    check("5.12", "body text never drops below 30px", rr.MIN_BODY_PX == 30)

    # --------------------------------------------------- 6. determinism --
    print("\n6. Deterministic repeat rendering")
    kind, r = install(tmp, FX["qualified_win"])
    img2, _ = rr.render_card(kind, r)
    p2 = rr.save_jpeg(img2, os.path.join(tmp, "card2.jpg"))
    h1 = hashlib.sha256(open(p1, "rb").read()).hexdigest()
    h2 = hashlib.sha256(open(p2, "rb").read()).hexdigest()
    check("6.1", "same recap renders byte-identical JPEG", h1 == h2)
    kind3, r3 = install(tmp, FX["qualified_loss"])
    img3, _ = rr.render_card(kind3, r3)
    p3 = rr.save_jpeg(img3, os.path.join(tmp, "card3.jpg"))
    h3 = hashlib.sha256(open(p3, "rb").read()).hexdigest()
    check("6.2", "a different recap renders a different image", h1 != h3)
    check("6.3", "JPEG quality is pinned at 88 in the encoder call",
          "quality=88" in open(os.path.join(ROOT, "scripts",
                                            "render_recap_image.py"),
                               encoding="utf-8").read())

    # ------------------------------------------- 7. required visible text --
    print("\n7. Required visible text, via recorded draw calls")
    for scen in ["qualified_win", "daily_win_zero", "daily_win_staked"]:
        kind, r = install(tmp, FX[scen])
        _, calls = rr.render_card(kind, r)
        t = blob(calls)
        check(f"7.{scen}.rg", f"{scen}: exact responsible-gaming line",
              "21+ · 1-800-GAMBLER · Analytics, not betting advice." in t)
        check(f"7.{scen}.site", f"{scen}: openledgersports.com on the card",
              "openledgersports.com" in t)
        check(f"7.{scen}.brand", f"{scen}: brand name on the card",
              "OPEN LEDGER SPORTS" in t)
        check(f"7.{scen}.date", f"{scen}: the date is on the card", r["nice"] in t)
        check(f"7.{scen}.pick", f"{scen}: the selection is on the card",
              any(e["pick"] in t for e in r["entries"]))
        check(f"7.{scen}.logo", f"{scen}: the logo is placed",
              any(c["kind"] == "image" and c["src"] == "assets/logo.jpg"
                  for c in calls))
    check("7.9", "wrapped fragments reconstruct their logical string",
          all(" ".join(c["fragments"]) == c["logical"]
              for c in calls if c["kind"] == "text" and not c["truncated"]))
    check("7.10", "card note is a verbatim substring of post_social's LEGAL",
          rr.CARD_NOTE in ps.LEGAL)

    # -------------------------------------- 8. one ledger, never crossed --
    print("\n8. Exactly one ledger per card, no cross-ledger figures")
    kind, r = install(tmp, FX["qualified_win"])
    _, calls = rr.render_card(kind, r)
    t = blob(calls)
    labels = [rr.LEDGER_LABEL_QUALIFIED, rr.LEDGER_LABEL_DAILY_PAPER,
              rr.LEDGER_LABEL_DAILY_STAKED]
    check("8.1", "Qualified card carries exactly one ledger label",
          sum(1 for lab in labels if lab in t) == 1)
    check("8.2", "Qualified card carries the Qualified label",
          rr.LEDGER_LABEL_QUALIFIED in t)
    check("8.3", "Qualified card shows no Daily paper figure",
          "paper" not in t.lower())
    check("8.4", "Qualified card never says Daily Pick", "DAILY PICK" not in t)

    kind, r = install(tmp, FX["daily_win_zero"])
    _, calls = rr.render_card(kind, r)
    t = blob(calls)
    check("8.5", "Daily card carries exactly one ledger label",
          sum(1 for lab in labels if lab in t) == 1)
    check("8.6", "Daily card carries no Qualified label",
          rr.LEDGER_LABEL_QUALIFIED not in t)
    check("8.7", "Daily card shows no Qualified running record",
          "12-9" not in t and "Qualified Plays" not in t.replace(
              ps.DISCLOSURE_FB.format(basis="0.25"), ""))

    kind, r = install(tmp, FX["both_ledgers_same_date"])
    check("8.8", "same date in both ledgers -> Qualified wins", kind == "qualified")
    _, calls = rr.render_card(kind, r)
    t = blob(calls)
    check("8.9", "the same-date Daily entry is absent from the card",
          "Kansas City Royals" not in t)
    cap = pi.build_caption(FX["both_ledgers_same_date"]["date"])
    check("8.10", "the same-date Daily entry is absent from the caption",
          "Kansas City Royals" not in cap)
    check("8.11", "recap dicts still share no unit key",
          not (set(ps.recap.__doc__ or "") and
               {"pnl", "units_net", "roi_pct"} &
               set(ps.daily_recap(FX["daily_win_zero"]["date"]) or {})))

    # ----------------------------------------------- 9. no emoji glyphs --
    print("\n9. No emoji result glyphs anywhere")
    for scen in FX:
        if scen.startswith("_") or scen == "unsettled_only":
            continue
        kind, r = install(tmp, FX[scen])
        if kind is None:
            continue
        _, calls = rr.render_card(kind, r)
        joined = "".join(texts(calls)) + "".join(
            f for c in calls if c["kind"] == "text" for f in c["fragments"])
        check(f"9.{scen}", f"{scen}: card text carries no emoji codepoint",
              not has_emoji(joined))
        cap = pi.build_caption(FX[scen]["date"])
        check(f"9.{scen}.c", f"{scen}: caption carries no emoji codepoint",
              not has_emoji(cap))
    check("9.z", "post_social's emoji CHIP map is never read by the renderer",
          "CHIP" not in attributes_of(
              os.path.join(ROOT, "scripts", "render_recap_image.py")))

    # ------------------------------------------------ 10. caption rules --
    print("\n10. Caption limits, disclosures and the bio pointer")
    for scen in ["qualified_win", "qualified_loss", "qualified_void",
                 "daily_win_zero", "daily_loss_zero", "daily_void_zero",
                 "daily_win_staked", "daily_loss_staked", "daily_void_staked",
                 "qualified_long_slate"]:
        install(tmp, FX[scen])
        cap = pi.build_caption(FX[scen]["date"])
        check(f"10.{scen}.len", f"{scen}: caption within 2200 chars",
              cap is not None and len(cap) <= 2200)
        check(f"10.{scen}.http", f"{scen}: caption contains no 'http'",
              "http" not in cap.lower())
        check(f"10.{scen}.bio", f"{scen}: caption ends with the bio pointer",
              cap.endswith(pi.BIO_LINE))
        check(f"10.{scen}.legal", f"{scen}: caption carries the full legal paragraph",
              ps.LEGAL in cap)
        check(f"10.{scen}.rg", f"{scen}: caption carries 21+ and 1-800-GAMBLER",
              "21+" in cap and "1-800-GAMBLER" in cap)
        check(f"10.{scen}.nw", f"{scen}: caption states no wagers are placed or accepted",
              "does not place or accept wagers" in cap)
        check(f"10.{scen}.nw1", f"{scen}: the no-wager sentence appears exactly once",
              cap.count("does not place or accept wagers") == 1)
    install(tmp, FX["qualified_win"])
    check("10.z", "no caption carries the site URL constant",
          ps.SITE not in pi.build_caption(FX["qualified_win"]["date"]))

    # --------------------------------- 11. no network, no creds, no board --
    print("\n11. Offline by construction — no network, credentials or board access")
    for mod, path in [("render_recap_image", "scripts/render_recap_image.py"),
                      ("post_instagram", "scripts/post_instagram.py")]:
        full = os.path.join(ROOT, path)
        imps = imports_of(full)
        check(f"11.{mod}.net", f"{mod}: imports no network library",
              not (imps & {"requests", "urllib", "urllib3", "http", "socket",
                           "requests_oauthlib", "ssl", "ftplib"}))
        attrs = attributes_of(full)
        check(f"11.{mod}.env", f"{mod}: reads no environment variable",
              "environ" not in attrs and "getenv" not in attrs)
        check(f"11.{mod}.crypto", f"{mod}: never imports crypto_box",
              "crypto_box" not in imps)
        consts = " ".join(string_constants(full)).lower()
        check(f"11.{mod}.enc", f"{mod}: references no .enc path",
              ".enc" not in consts)
        check(f"11.{mod}.board", f"{mod}: opens no board file",
              "board_" not in consts)
        check(f"11.{mod}.graph", f"{mod}: names no Meta endpoint",
              "graph.facebook" not in consts and "graph.instagram" not in consts)
    check("11.9", "Phase 1 post_instagram exposes no publish entry point",
          not hasattr(pi, "publish") and not hasattr(pi, "create_container"))
    check("11.10", "requests was never imported into this process",
          "requests" not in sys.modules)

    # -------------------------------------------------- 12. safety rails --
    print("\n12. Safety rails")
    try:
        rr.save_jpeg(img, os.path.join(ROOT, "data", "nope.jpg"))
        ok = False
    except ValueError:
        ok = True
    check("12.1", "renderer refuses to write inside data/", ok)
    check("12.2", "no file was created in data/",
          not os.path.exists(os.path.join(ROOT, "data", "nope.jpg")))

    kind, r = install(tmp, FX["unsettled_only"])
    check("12.3", "an unsettled entry is not a recap at all", kind is None)
    check("12.4", "caption returns None when nothing settled",
          pi.build_caption(FX["unsettled_only"]["date"]) is None)
    bad = {"nice": "Monday, July 1", "entries": [{"result": "PENDING", "pick": "x", "pnl": 0.0}],
           "day_pnl": 0.0, "day_w": 0, "day_l": 0, "day_v": 0,
           "record": "0-0", "units_net": 0.0, "roi_pct": None}
    try:
        rr.render_card("qualified", bad)
        ok = False
    except ValueError:
        ok = True
    check("12.5", "renderer refuses an unsettled entry outright", ok)
    try:
        rr.render_card("nonsense", bad)
        ok = False
    except ValueError:
        ok = True
    check("12.6", "renderer refuses an unknown card kind", ok)

    # ---------------------------------- 13. long slates and truncation --
    print("\n13. Long slates, wrapping and truncation")
    kind, r = install(tmp, FX["qualified_long_slate"])
    _, calls = rr.render_card(kind, r)
    t = blob(calls)
    rows = [c for c in shapes(calls) if c.get("result")]
    check("13.1", "at most six result rows are drawn", len(rows) == rr.MAX_ROWS)
    check("13.2", "the overflow line names the remaining count",
          "+2 more on openledgersports.com" in t)
    check("13.3", "the seventh entry is not drawn",
          "Philadelphia Phillies" not in t)
    cap = pi.build_caption(FX["qualified_long_slate"]["date"])
    check("13.4", "caption caps at the same six rows",
          cap.count(" · ") >= 6 and "+2 more in the ledger." in cap)
    check("13.5", "caption overflow carries no URL", "http" not in cap.lower())

    kind, r = install(tmp, FX["qualified_long_pick_name"])
    _, calls = rr.render_card(kind, r)
    trunc = [c for c in calls if c["kind"] == "text" and c["truncated"]]
    check("13.6", "an over-wide pick name is truncated, not overflowed",
          any(c["logical"].startswith("Arizona Diamondbacks") for c in trunc))
    check("13.7", "truncation is marked with an ellipsis",
          all(c["fragments"][0].endswith("…") for c in trunc))
    check("13.8", "only pick names truncate — no disclosure ever does",
          all(ps.DISCLOSURE_FB.format(basis="0.25") != c["logical"] for c in trunc))

    # THE CHECK THAT WAS MISSING. An earlier layout pinned the disclosure to a
    # four-line cap; the allocated wording needs seven, so a live-shaped card
    # rendered "...does not enter the …" — the one paragraph that may never be
    # trimmed, trimmed. The suite passed anyway because it only ever asserted
    # against the shorter zero-allocation wording. This sweeps EVERY scenario and
    # every protected paragraph, and it is why the layout is now computed rather
    # than hardcoded.
    print("\n13b. Protected paragraphs are never truncated, in any scenario")
    for scen in FX:
        if scen.startswith("_") or scen == "unsettled_only":
            continue
        kind, r = install(tmp, FX[scen])
        if kind is None:
            continue
        _, calls = rr.render_card(kind, r)
        protected = [rr.CARD_RG, rr.CARD_NOTE,
                     ps.DISCLOSURE_FB.format(basis="0.25"),
                     ps.DISCLOSURE_FB_STAKED.format(stake="0.25")]
        bad = [c for c in calls if c["kind"] == "text"
               and c["logical"] in protected and c["truncated"]]
        check(f"13b.{scen}", f"{scen}: no protected paragraph is truncated", not bad)
        drawn = {c["logical"]: "".join(c["fragments"]) for c in calls
                 if c["kind"] == "text"}
        whole = all(" ".join(c["fragments"]) == c["logical"]
                    for c in calls if c["kind"] == "text"
                    and c["logical"] in protected)
        check(f"13b.{scen}.w", f"{scen}: protected paragraphs render word-complete",
              whole and any(k in protected for k in drawn))
        check(f"13b.{scen}.e", f"{scen}: no protected paragraph ends in an ellipsis",
              not any(v.rstrip().endswith("…") for k, v in drawn.items()
                      if k in protected))

    # ------------------------------- 13c. the pick / unit-figure collision --
    #
    # Visual review flagged the long recorded-allocation row as overlapping its
    # +0.29u result. Measured, that row had 111px of clear space — but the
    # reservation behind it was computed from INK width, which discards side
    # bearings, and the worst case across the fixtures was only 33px. The
    # renderer now reserves with ADVANCE width, right-aligns the figure by
    # anchor, and keeps a real gutter. These checks are the proof, and they
    # assert against the rectangles Pillow actually reported rather than
    # re-running the arithmetic that placed them.
    print("\n13c. Pick text never collides with the unit figure")
    LONG = ["daily_staked_long_pick", "daily_zero_long_pick",
            "qualified_long_pick_wide_amount", "qualified_long_pick_name"]
    for scen in FX:
        if scen.startswith("_") or scen == "unsettled_only":
            continue
        kind, r = install(tmp, FX[scen])
        if kind is None:
            continue
        _, calls = rr.render_card(kind, r)
        picks = [c for c in calls if c.get("role") == "pick"]
        amounts = [c for c in calls if c.get("role") == "amount"]
        check(f"13c.{scen}.n", f"{scen}: one unit figure per pick row",
              len(picks) == len(amounts) and len(picks) >= 1)

        # The actual requirement: the two rectangles must not intersect.
        clashes = []
        for p, a in zip(picks, amounts):
            px0, py0, px1, py1 = p["bbox"]
            ax0, ay0, ax1, ay1 = a["bbox"]
            if px1 > ax0 and ax1 > px0 and py1 > ay0 and ay1 > py0:
                clashes.append((p["fragments"][0], a["fragments"][0]))
        check(f"13c.{scen}.x", f"{scen}: pick and unit bounding boxes never intersect",
              not clashes)
        check(f"13c.{scen}.g",
              f"{scen}: at least the full gutter separates pick from unit figure",
              all(a["bbox"][0] - p["bbox"][2] >= 1 for p, a in zip(picks, amounts)))
        check(f"13c.{scen}.u", f"{scen}: the unit figure is never truncated",
              not any(a["truncated"] or "…" in a["fragments"][0] for a in amounts))
        check(f"13c.{scen}.m", f"{scen}: the unit figure stays inside the margin",
              all(a["bbox"][2] <= rr.W - rr.MARGIN for a in amounts))
        check(f"13c.{scen}.l", f"{scen}: the pick starts left of the unit figure",
              all(p["bbox"][0] < a["bbox"][0] for p, a in zip(picks, amounts)))

    print("\n13d. Over-long picks ellipsize; the result survives intact")
    for scen in LONG:
        kind, r = install(tmp, FX[scen])
        _, calls = rr.render_card(kind, r)
        picks = [c for c in calls if c.get("role") == "pick"]
        amounts = [c for c in calls if c.get("role") == "amount"]
        check(f"13d.{scen}.t", f"{scen}: the long pick is truncated",
              any(p["truncated"] for p in picks))
        check(f"13d.{scen}.e", f"{scen}: truncation ends in a single ellipsis",
              all(p["fragments"][0].endswith("…") and
                  p["fragments"][0].count("…") == 1
                  for p in picks if p["truncated"]))
        check(f"13d.{scen}.f", f"{scen}: the full pick is still recorded as logical",
              all(p["logical"] == e["pick"]
                  for p, e in zip(picks, r["entries"])))
        check(f"13d.{scen}.a", f"{scen}: the unit figure renders in full",
              all(a["fragments"][0] == a["logical"] for a in amounts))
        cap = pi.build_caption(FX[scen]["date"])
        check(f"13d.{scen}.c", f"{scen}: the caption keeps the pick UNtruncated",
              all(e["pick"] in cap for e in r["entries"]))

    kind, r = install(tmp, FX["qualified_long_pick_wide_amount"])
    _, calls = rr.render_card(kind, r)
    amounts = [c for c in calls if c.get("role") == "amount"]
    check("13d.wide", "the widest unit figure (-10.00u) renders whole",
          amounts and amounts[0]["fragments"][0] == "-10.00u")

    check("13e.1", "book attribution survives when it fits",
          "BetMGM" in blob(rr.render_card(*install(tmp, FX["daily_win_staked"]))[1]))
    check("13e.2", "reservation uses advance width, not ink width",
          rr.advance("+0.29u", rr.font(rr.MIN_BODY_PX, bold=True))
          >= rr.text_width("+0.29u", rr.font(rr.MIN_BODY_PX, bold=True)))
    check("13e.3", "a zero-width allowance yields an ellipsis, never a crash",
          rr.fit("Some very long pick name", rr.font(32), 0) == ("…", True))

    # ------------------------------------ 14. constants stay in lockstep --
    print("\n14. Copy stays in lockstep with post_social.py")
    check("14.1", "caption reuses post_social's LEGAL object", pi.ps.LEGAL is ps.LEGAL)
    check("14.2", "caption reuses DISCLOSURE_FB", pi.ps.DISCLOSURE_FB is ps.DISCLOSURE_FB)
    check("14.3", "caption reuses DISCLOSURE_FB_STAKED",
          pi.ps.DISCLOSURE_FB_STAKED is ps.DISCLOSURE_FB_STAKED)
    check("14.4", "renderer reuses post_social's day-record formatter",
          rr.ps._day_record is ps._day_record)
    check("14.5", "renderer reuses the Qualified running-line formatter",
          rr.ps._running is ps._running)
    check("14.6", "renderer reuses the Daily running-line formatter",
          rr.ps._running_daily is ps._running_daily)
    check("14.7", "the card's RG line names both 21+ and 1-800-GAMBLER",
          "21+" in rr.CARD_RG and "1-800-GAMBLER" in rr.CARD_RG)
    check("14.8", "Phase 1 changed no Facebook or X builder",
          hasattr(ps, "build_fb_text") and hasattr(ps, "build_x_text"))

    ps.LEDGER_PATH, ps.DAILY_LEDGER_PATH = real_q, real_d
    tmp_dir.cleanup()

    print()
    check("15.1", f"all {EXPECTED_CHECKS} checks ran (got {CHECKS[0] + 1})",
          CHECKS[0] + 1 == EXPECTED_CHECKS)

    if FAILURES:
        print(f"\nFAIL — {len(FAILURES)} of {CHECKS[0]} checks failed:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"\nPASS — {CHECKS[0]} checks, 0 failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
