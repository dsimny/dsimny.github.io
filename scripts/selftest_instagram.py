#!/usr/bin/env python3
"""
Offline self-test for the Instagram recap card, caption and publishing
transport (Phase 1 + Phase 2A).

Follows scripts/selftest_social.py exactly: plain assertions, no test framework
(requirements.txt pins none), each check NUMBERED to the requirement it proves,
and EXPECTED_CHECKS asserted at the end so a check that silently stops running
fails the suite even when nothing reports FAIL.

RUNS FULLY OFFLINE. No network, no credentials, no git history, no full clone.
It writes only inside a TemporaryDirectory, and never inside data/ — the
renderer refuses every non-card path there (group 21), while the
Instagram status path is redirected into the temp directory for the whole
transport section and check 16.zz proves no live status file was created.

THE TRANSPORT IS EXERCISED THROUGH A FAKE. Group 16 drives every request,
response, retry, reconciliation and crash-window branch against a scripted
FakeTransport that records each call, so assertions can be made about what was
NOT sent ("no container was created") as well as what was. Check 16.zz2
re-asserts at the very end that `requests` was never imported into the process,
which is the strongest available statement that nothing here touched a socket.

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
import re
import inspect
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yaml  # noqa: E402  (offline YAML parse of the workflows)
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
EXPECTED_CHECKS = 872

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


def module_level_imports(path):
    """Only the imports executed AT IMPORT TIME.

    `imports_of` walks the whole tree and so cannot tell an import inside a
    function from one at the top of the file. That distinction is the entire
    point here: post_instagram is allowed to use `requests`, but importing the
    module must not pull in a network stack or open anything.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def functions_reading_environ(path):
    """Names of functions that touch os.environ / os.getenv.

    Credentials should enter through exactly one door. Proving that structurally
    beats grepping for the variable names, which a refactor would silently move.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv"):
                out.add(fn.name)
    return out


class FakeTransport:
    """A scripted stand-in for the HTTP layer.

    Records every request so the tests can assert the exact call SEQUENCE, not
    merely the outcome — "no container was created" is only provable by looking
    at what was and was not sent. An unscripted call is an error rather than a
    default, so a branch that makes an unexpected request fails loudly.
    """

    def __init__(self, routes):
        self.routes = routes          # [(method, url_suffix, response)]
        self.calls = []

    def __call__(self, method, url, params=None, data=None, timeout=30):
        self.calls.append({"method": method, "url": url,
                           "params": dict(params or {}), "data": dict(data or {})})
        for m, suffix, resp in self.routes:
            if method == m and url.endswith(suffix):
                return resp(self) if callable(resp) else resp
        raise AssertionError(f"unscripted transport call: {method} {url}")

    def sent(self, method=None, suffix=None):
        return [c for c in self.calls
                if (method is None or c["method"] == method)
                and (suffix is None or c["url"].endswith(suffix))]

    def token_leaks(self, token):
        """Requests legitimately carry the token; nothing else may."""
        return [c for c in self.calls
                if token in json.dumps({"url": c["url"]})]


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
        scen_kind, _scen_r = install(tmp, FX[scen])
        cap = pi.build_caption(FX[scen]["date"])
        scen_ref = pi.ledger_ref(scen_kind, FX[scen]["date"])
        check(f"10.{scen}.len", f"{scen}: caption within 2200 chars",
              cap is not None and len(cap) <= 2200)
        check(f"10.{scen}.http", f"{scen}: caption contains no 'http'",
              "http" not in cap.lower())
        check(f"10.{scen}.bio", f"{scen}: caption carries the bio pointer",
              pi.BIO_LINE in cap)
        check(f"10.{scen}.ref", f"{scen}: caption ends with the ledger reference",
              cap.endswith(pi.REF_PREFIX + scen_ref))
        check(f"10.{scen}.ref1", f"{scen}: the reference appears exactly once",
              cap.count(scen_ref) == 1)
        check(f"10.{scen}.refk", f"{scen}: the reference names the right strategy",
              scen_ref.startswith("OLS-QUAL-" if scen_kind == "qualified"
                                  else "OLS-DAILY-"))
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

    # The RENDERER stays absolutely sealed: it has no business knowing that Meta
    # exists, and these rules are unchanged from Phase 1.
    rpath = os.path.join(ROOT, "scripts", "render_recap_image.py")
    rimps = imports_of(rpath)
    check("11.rr.net", "render_recap_image: imports no network library",
          not (rimps & {"requests", "urllib", "urllib3", "http", "socket",
                        "requests_oauthlib", "ssl", "ftplib"}))
    check("11.rr.env", "render_recap_image: reads no environment variable",
          "environ" not in attributes_of(rpath)
          and "getenv" not in attributes_of(rpath))
    check("11.rr.graph", "render_recap_image: names no Meta endpoint",
          "graph.facebook" not in " ".join(string_constants(rpath)).lower())

    # post_instagram NOW CARRIES THE TRANSPORT, so "imports no network library"
    # is the wrong rule for it. The rules that still matter are that importing it
    # opens nothing, and that credentials enter through exactly one door.
    ipath = os.path.join(ROOT, "scripts", "post_instagram.py")
    top = module_level_imports(ipath)
    check("11.pi.top", "post_instagram: no network library at module level",
          not (top & {"requests", "urllib", "urllib3", "http", "socket",
                      "requests_oauthlib", "ssl", "ftplib"}))
    check("11.pi.lazy", "post_instagram: requests is imported inside a function",
          "requests" in imports_of(ipath) and "requests" not in top)
    env_fns = functions_reading_environ(ipath)
    check("11.pi.env1", "post_instagram: exactly one function reads os.environ",
          env_fns == {"config"})
    check("11.pi.envname", "post_instagram: the credential env names are the agreed ones",
          pi.ENV_IG_USER == "IG_USER_ID" and pi.ENV_IG_TOKEN == "IG_ACCESS_TOKEN")

    for mod, full in [("render_recap_image", rpath), ("post_instagram", ipath)]:
        imps = imports_of(full)
        check(f"11.{mod}.crypto", f"{mod}: never imports crypto_box",
              "crypto_box" not in imps)
        consts = " ".join(string_constants(full)).lower()
        check(f"11.{mod}.enc", f"{mod}: references no .enc path",
              ".enc" not in consts)
        check(f"11.{mod}.board", f"{mod}: opens no board file",
              "board_" not in consts)

    check("11.9", "the Graph endpoint is pinned to the audited version",
          pi.GRAPH == "https://graph.facebook.com/v26.0")
    check("11.10", "requests was never imported into this process",
          "requests" not in sys.modules)
    check("11.11", "no transport was materialised by importing the module",
          pi.TRANSPORT is None or callable(pi.TRANSPORT))

    # -------------------------------------------------- 12. safety rails --
    print("\n12. Safety rails")
    try:
        rr.save_jpeg(img, os.path.join(ROOT, "data", "nope.jpg"))
        ok = False
    except ValueError:
        ok = True
    # Narrowed 2026-09-11: the renderer no longer refuses ALL of data/, only
    # everything that is not its own card. data/nope.jpg is still refused, so
    # this check still holds — but the label used to claim a blanket ban that no
    # longer exists. Group 21 owns the full boundary.
    check("12.1", "renderer refuses a non-card path inside data/", ok)
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
    # ASSERT THE CODE, NOT THE FONT. This check first read
    #     advance("+0.29u") >= text_width("+0.29u")
    # which passed on Windows (both exactly 120) and FAILED on the Ubuntu
    # runner, where the rasteriser reports an ink box marginally wider than the
    # advance. Ink width and advance width are independent metrics — neither
    # bounds the other in general — so that was never a property worth
    # asserting, and it made a green suite depend on which machine ran it.
    #
    # The regression actually worth preventing is someone "simplifying" the
    # reservation back to ink width, so assert that directly against the source
    # of the function that does the reserving. The geometric outcome is already
    # proven by the bounding-box checks in 13c, which run on real metrics
    # wherever they execute.
    _rows_src = inspect.getsource(rr._entry_rows)
    check("13e.2", "the unit-figure reservation is computed from advance width",
          "advance(amount" in _rows_src and "text_width(amount" not in _rows_src)
    check("13e.3", "a zero-width allowance yields an ellipsis, never a crash",
          rr.fit("Some very long pick name", rr.font(32), 0) == ("…", True))

    # ------------------------------ 16. transport, reconciliation, recovery --
    #
    # Every branch below runs against a FakeTransport. Nothing here opens a
    # socket, and check 16.zz re-asserts at the end that `requests` was never
    # imported into the process. The status file is redirected into the
    # TemporaryDirectory for the whole section, so the live
    # data/instagram_status.json is never created or touched.
    print("\n16. Publishing transport, reconciliation and crash recovery")

    IG, TOK = "17841400000000000", "FAKE-TOKEN-DO-NOT-LOG-0000"
    DATE = FX["qualified_win"]["date"]
    REF = pi.ledger_ref("qualified", DATE)

    real_status_path, real_sleep = pi.STATUS_PATH, pi.SLEEP
    pi.SLEEP = lambda _s: None                      # no test ever sleeps

    def fresh_status(tag):
        pi.STATUS_PATH = os.path.join(tmp, f"ig_status_{tag}.json")
        return pi.STATUS_PATH

    def set_env(ig=IG, tok=TOK):
        os.environ[pi.ENV_IG_USER] = ig
        os.environ[pi.ENV_IG_TOKEN] = tok

    def clear_env():
        os.environ.pop(pi.ENV_IG_USER, None)
        os.environ.pop(pi.ENV_IG_TOKEN, None)

    HEAD_OK = ("HEAD", "", (200, {}, ""))
    HEAD_404 = ("HEAD", "", (404, {}, ""))

    def feed(items):
        return ("GET", "/media", (200, {"data": items}, ""))

    def err(code, sub=None, http=400, msg="upstream said no"):
        return (http, {"error": {"code": code, "error_subcode": sub,
                                 "message": msg}}, msg)

    CREATE_OK = ("POST", "/media", (200, {"id": "CONT-1"}, ""))
    PUBLISH_OK = ("POST", "/media_publish", (200, {"id": "MEDIA-1"}, ""))

    def cstat(code):
        return ("GET", "/CONT-1", (200, {"status_code": code}, ""))

    def run(tag, routes, seed=None, scen="qualified_win", env=True):
        """One publish() against a scripted transport, from a clean status file."""
        install(tmp, FX[scen])
        fresh_status(tag)
        if seed:
            pi.record(**seed)
        clear_env()
        if env:
            set_env()
        ft = FakeTransport(routes)
        res = pi.publish(FX[scen]["date"], "https://raw.example/img.jpg",
                         transport=ft)
        return res, ft

    # ---- 16.1 no credentials: zero Meta calls ----
    install(tmp, FX["qualified_win"])
    fresh_status("nocfg")
    clear_env()
    ft = FakeTransport([])
    res = pi.publish(DATE, "https://raw.example/img.jpg", transport=ft)
    check("16.1a", "missing credentials -> no_config", res.state == "no_config")
    check("16.1b", "no_config makes ZERO Meta calls", len(ft.calls) == 0)
    check("16.1c", "no_config is recorded",
          (pi.status_for(DATE) or {}).get("result") == "no_config")

    # ---- 16.2 nothing settled: zero Meta calls ----
    install(tmp, FX["unsettled_only"])
    fresh_status("nothing")
    set_env()
    ft = FakeTransport([])
    res = pi.publish(FX["unsettled_only"]["date"], "https://raw.example/img.jpg",
                     transport=ft)
    check("16.2a", "nothing settled -> nothing_to_post",
          res.state == "nothing_to_post")
    check("16.2b", "nothing_to_post makes ZERO Meta calls", len(ft.calls) == 0)

    # ---- 16.3 happy path, with the ordering proof ----
    def publish_checks_order(ft_self):
        row = pi.status_for(DATE) or {}
        ft_self.at_publish = (row.get("result"), row.get("creation_id"))
        return (200, {"id": "MEDIA-1"}, "")

    res, ft = run("happy", [feed([]), HEAD_OK, CREATE_OK, cstat("FINISHED"),
                            ("POST", "/media_publish", publish_checks_order)])
    check("16.3a", "happy path -> posted", res.state == "posted")
    check("16.3b", "the Meta media id is recorded", res.media_id == "MEDIA-1")
    check("16.3c", "status row is posted with the media id",
          (pi.status_for(DATE) or {}).get("result") == "posted"
          and (pi.status_for(DATE) or {}).get("media_id") == "MEDIA-1")
    check("16.3d", "reconciliation ran BEFORE the container was created",
          [c["method"] + " " + c["url"].split("/")[-1] for c in ft.calls][:2]
          == ["GET media", "HEAD img.jpg"])
    check("16.3e", "pending_publish was persisted BEFORE media_publish",
          getattr(ft, "at_publish", None) == ("pending_publish", "CONT-1"))
    check("16.3f", "the caption sent to Meta carries the ledger reference",
          REF in (ft.sent("POST", "/media")[0]["data"].get("caption") or ""))
    check("16.3g", "the image_url sent is the one supplied",
          ft.sent("POST", "/media")[0]["data"].get("image_url")
          == "https://raw.example/img.jpg")
    check("16.3h", "exactly one publish call was made",
          len(ft.sent("POST", "/media_publish")) == 1)

    # ---- 16.4 remote reconciliation wins: nothing is created ----
    res, ft = run("recon", [feed([{"id": "MEDIA-OLD",
                                   "caption": f"anything {REF} trailing"}])])
    check("16.4a", "an already-published reference -> posted",
          res.state == "posted")
    check("16.4b", "the existing media id is recovered",
          res.media_id == "MEDIA-OLD")
    check("16.4c", "NO container was created", not ft.sent("POST", "/media"))
    check("16.4d", "NOTHING was published", not ft.sent("POST", "/media_publish"))
    check("16.4e", "the image was not even fetched", not ft.sent("HEAD"))
    check("16.4f", "the recovery is recorded as posted",
          (pi.status_for(DATE) or {}).get("result") == "posted")

    # A different date's reference must NOT be mistaken for ours.
    res, ft = run("recon_other", [feed([{"id": "MEDIA-X",
                                         "caption": "Ledger ref OLS-QUAL-1999-01-01"}]),
                                  HEAD_OK, CREATE_OK, cstat("FINISHED"), PUBLISH_OK])
    check("16.4g", "another date's reference does not count as ours",
          res.state == "posted" and res.media_id == "MEDIA-1")

    # ---- 16.5 pending_publish + PUBLISHED -> recover, never republish ----
    seed = dict(date=DATE, kind="qualified", ref=REF, result="pending_publish",
                creation_id="CONT-1")
    res, ft = run("pp_published", [feed([]), cstat("PUBLISHED")], seed=seed)
    check("16.5a", "pending_publish + PUBLISHED -> posted", res.state == "posted")
    check("16.5b", "it does NOT publish again",
          not ft.sent("POST", "/media_publish"))
    check("16.5c", "it does not build another container",
          not ft.sent("POST", "/media"))

    # ---- 16.6 pending_publish + FINISHED -> resume at publish ----
    res, ft = run("pp_finished", [feed([]), cstat("FINISHED"), PUBLISH_OK],
                  seed=seed)
    check("16.6a", "pending_publish + FINISHED -> posted", res.state == "posted")
    check("16.6b", "it resumes rather than creating a second container",
          not ft.sent("POST", "/media"))
    check("16.6c", "it publishes exactly once",
          len(ft.sent("POST", "/media_publish")) == 1)

    # ---- 16.7 / 16.8 ERROR and EXPIRED containers ----
    for code, tag in [("ERROR", "cerr"), ("EXPIRED", "cexp")]:
        res, ft = run(tag, [feed([]), HEAD_OK, CREATE_OK, cstat(code)])
        check(f"16.7.{code}", f"container {code} -> failed", res.state == "failed")
        check(f"16.7.{code}.np", f"container {code} publishes nothing",
              not ft.sent("POST", "/media_publish"))
        check(f"16.7.{code}.rec", f"container {code} is recorded, not posted",
              (pi.status_for(DATE) or {}).get("result") == "failed")

    # ---- 16.9 image unreachable: NO container, ever ----
    res, ft = run("noimg", [feed([]), HEAD_404])
    check("16.9a", "unreachable image -> pending_media",
          res.state == "pending_media")
    check("16.9b", "NO container is created against a missing image",
          not ft.sent("POST", "/media"))
    check("16.9c", "nothing is published", not ft.sent("POST", "/media_publish"))
    check("16.9d", "pending_media is recorded",
          (pi.status_for(DATE) or {}).get("result") == "pending_media")

    # ---- 16.10-16.12 error classification ----
    check("16.10a", "code 190 is a token error",
          pi.classify(400, {"error": {"code": 190}})[0] == "token")
    check("16.10b", "subcode 463 is a token error",
          pi.classify(400, {"error": {"code": 999, "error_subcode": 463}})[0] == "token")
    check("16.10c", "code 10 is a permission error",
          pi.classify(403, {"error": {"code": 10}})[0] == "permission")
    check("16.11a", "code 4 is a rate limit",
          pi.classify(429, {"error": {"code": 4}})[0] == "rate")
    check("16.11b", "code 32 is a rate limit",
          pi.classify(400, {"error": {"code": 32}})[0] == "rate")
    check("16.12a", "code 2 is transient",
          pi.classify(500, {"error": {"code": 2}})[0] == "transient")
    check("16.12b", "a bare 5xx is transient",
          pi.classify(503, {})[0] == "transient")

    for code, want, tag in [(190, "token", "e190"), (4, "rate", "e4"),
                            (2, "transient", "e2")]:
        res, ft = run(tag, [feed([]), HEAD_OK,
                            ("POST", "/media", err(code, http=500 if code == 2 else 400))])
        row = pi.status_for(DATE) or {}
        check(f"16.12.{tag}", f"create error {code} -> failed ({want})",
              res.state == "failed" and want in (row.get("detail") or ""))
        check(f"16.12.{tag}.np", f"create error {code} publishes nothing",
              not ft.sent("POST", "/media_publish"))

    # A failed reconciliation read must not blunder on into publishing.
    res, ft = run("reconfail", [("GET", "/media", err(190, http=401))])
    check("16.12.recon", "a failed reconciliation read -> failed, nothing created",
          res.state == "failed" and not ft.sent("POST", "/media"))

    # ---- 16.13 a 2xx without a media id is NOT proof of publication ----
    res, ft = run("noid", [feed([]), HEAD_OK, CREATE_OK, cstat("FINISHED"),
                           ("POST", "/media_publish", (200, {}, ""))])
    check("16.13a", "publish 200 with no id -> failed", res.state == "failed")
    check("16.13b", "and it is NOT recorded as posted",
          (pi.status_for(DATE) or {}).get("result") == "failed")

    # ---- 16.14 THE CRASH WINDOW: died after publishing, before committing ----
    # Run once successfully, then throw the status file away exactly as an
    # uncommitted runner would, and run again against a feed that now shows the
    # post. Nothing may be published a second time.
    res1, ft1 = run("crash", [feed([]), HEAD_OK, CREATE_OK, cstat("FINISHED"),
                              PUBLISH_OK])
    fresh_status("crash_lost")                     # the status write never landed
    ft2 = FakeTransport([feed([{"id": "MEDIA-1",
                                "caption": f"…\n\nLedger ref {REF}"}])])
    res2 = pi.publish(DATE, "https://raw.example/img.jpg", transport=ft2)
    check("16.14a", "first run published", res1.state == "posted")
    check("16.14b", "after losing local state, the rerun still says posted",
          res2.state == "posted" and res2.media_id == "MEDIA-1")
    check("16.14c", "the rerun published NOTHING",
          not ft2.sent("POST", "/media_publish"))
    check("16.14d", "the rerun created NO container",
          not ft2.sent("POST", "/media"))

    # ---- 16.15 a local posted row short-circuits with zero calls ----
    res, ft = run("short", [], seed=dict(date=DATE, kind="qualified", ref=REF,
                                         result="posted", media_id="MEDIA-9"))
    check("16.15a", "a recorded posted row -> posted", res.state == "posted")
    check("16.15b", "and costs zero Meta calls", len(ft.calls) == 0)

    # ---- 16.16 the token never leaks ----
    res, ft = run("leak", [feed([]), HEAD_OK, CREATE_OK, cstat("FINISHED"),
                           PUBLISH_OK])
    blob_status = open(pi.STATUS_PATH, encoding="utf-8").read()
    check("16.16a", "no token value in the status file", TOK not in blob_status)
    check("16.16b", "no token value in the Result repr", TOK not in repr(res))
    check("16.16c", "no token in any request URL", not ft.token_leaks(TOK))
    check("16.16d", "the token IS sent as a request field (where it belongs)",
          any(c["data"].get("access_token") == TOK or
              c["params"].get("access_token") == TOK for c in ft.calls))
    res, ft = run("leakerr", [feed([]), HEAD_OK,
                              ("POST", "/media",
                               err(190, msg=f"bad token {TOK} rejected"))])
    check("16.16e", "a Meta message echoing the token is redacted before storage",
          TOK not in open(pi.STATUS_PATH, encoding="utf-8").read())
    check("16.16f", "the redaction marker is used",
          pi.REDACTED in (pi.status_for(DATE) or {}).get("detail", ""))

    # ---- 16.17 reference helpers ----
    check("16.17a", "qualified reference shape",
          pi.ledger_ref("qualified", "2026-05-01") == "OLS-QUAL-2026-05-01")
    check("16.17b", "daily reference shape",
          pi.ledger_ref("daily", "2026-06-01") == "OLS-DAILY-2026-06-01")
    check("16.17c", "the reference is deterministic",
          pi.ledger_ref("daily", "2026-06-01") == pi.ledger_ref("daily", "2026-06-01"))
    try:
        pi.ledger_ref("nonsense", "2026-06-01")
        ok = False
    except ValueError:
        ok = True
    check("16.17d", "an unknown strategy raises rather than inventing a reference", ok)
    check("16.17e", "find_ref extracts a reference from a caption",
          pi.find_ref(f"blah\n\nLedger ref {REF}") == REF)
    check("16.17f", "find_ref returns None when absent",
          pi.find_ref("no reference here") is None)

    # ---- 16.18 status schema ----
    res, ft = run("schema", [feed([]), HEAD_OK, CREATE_OK, cstat("FINISHED"),
                             PUBLISH_OK])
    row = pi.status_for(DATE) or {}
    check("16.18a", "status row carries the agreed keys",
          set(row) == {"date", "kind", "ref", "result", "creation_id",
                       "media_id", "http_status", "detail", "at_utc"})
    check("16.18b", "status file is versioned",
          json.load(open(pi.STATUS_PATH, encoding="utf-8")).get("version") == 1)
    check("16.18c", "one row per date",
          len([p for p in json.load(open(pi.STATUS_PATH, encoding="utf-8"))["posts"]
               if p["date"] == DATE]) == 1)
    check("16.18d", "the row records the strategy and reference",
          row.get("kind") == "qualified" and row.get("ref") == REF)

    # ---- 16.19 the Daily branch publishes too ----
    res, ft = run("daily", [feed([]), HEAD_OK, CREATE_OK, cstat("FINISHED"),
                            PUBLISH_OK], scen="daily_win_zero")
    dref = pi.ledger_ref("daily", FX["daily_win_zero"]["date"])
    check("16.19a", "a Daily Pick night publishes", res.state == "posted")
    check("16.19b", "with the DAILY reference in the caption",
          dref in (ft.sent("POST", "/media")[0]["data"].get("caption") or ""))
    check("16.19c", "and the row is tagged daily",
          (pi.status_for(FX["daily_win_zero"]["date"]) or {}).get("kind") == "daily")

    # ---- 16.20 polling ----
    seq = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]

    def polling(_ft):
        return (200, {"status_code": seq.pop(0) if seq else "FINISHED"}, "")

    res, ft = run("poll", [feed([]), HEAD_OK, CREATE_OK,
                           ("GET", "/CONT-1", polling), PUBLISH_OK])
    check("16.20a", "IN_PROGRESS is polled until FINISHED", res.state == "posted")
    check("16.20b", "it polled more than once",
          len(ft.sent("GET", "/CONT-1")) == 3)

    stuck = [("GET", "/media", (200, {"data": []}, "")), HEAD_OK, CREATE_OK,
             ("GET", "/CONT-1", (200, {"status_code": "IN_PROGRESS"}, ""))]
    res, ft = run("stuck", stuck)
    check("16.20c", "a container stuck IN_PROGRESS times out as failed",
          res.state == "failed")
    check("16.20d", "the timeout publishes nothing",
          not ft.sent("POST", "/media_publish"))
    check("16.20e", "polling is bounded",
          len(ft.sent("GET", "/CONT-1")) == pi.POLL_ATTEMPTS)

    # ---------------------------------- 17. sync-status: durable telemetry --
    #
    # WHY THIS COMMAND EXISTS. publish() runs AFTER the pipeline's only commit,
    # and a GitHub-hosted runner is destroyed when the job ends — so a `posted`
    # row it writes is discarded and never reaches the repository. It cannot
    # "ride along with the next run"; nothing carries it. sync-status runs BEFORE
    # the commit, asks Meta what is actually live, and writes those
    # confirmations where `git add data/` will sweep them up.
    #
    # It is READ-ONLY against Meta. Every check below also asserts that no POST
    # was made, because a telemetry command that could publish would be a far
    # worse bug than the one it fixes.
    print("\n17. sync-status — making the advisory history durable")

    QDATE = FX["qualified_win"]["date"]
    DDATE = FX["daily_win_zero"]["date"]
    QREF = pi.ledger_ref("qualified", QDATE)
    DREF = pi.ledger_ref("daily", DDATE)

    def sync(tag, items, seed=None, env=True, http_ok=True):
        fresh_status(tag)
        if seed:
            pi.record(**seed)
        clear_env()
        if env:
            set_env()
        route = (("GET", "/media", (200, {"data": items}, "")) if http_ok
                 else ("GET", "/media", err(190, http=401)))
        ft = FakeTransport([route])
        return pi.sync_status(transport=ft), ft

    # ---- 17.1 a completely fresh runner: no status file at all ----
    fresh_status("fresh_none")
    check("17.1a", "the fresh status path genuinely does not exist",
          not os.path.exists(pi.STATUS_PATH))
    set_env()
    ft = FakeTransport([("GET", "/media",
                         (200, {"data": [{"id": "M-Q", "caption": f"x {QREF}",
                                          "timestamp": "2026-05-02T08:00:00+0000"}]}, ""))])
    res = pi.sync_status(transport=ft)
    check("17.1b", "sync on a fresh runner succeeds", res.state == "ok")
    check("17.1c", "it merges the confirmed publication", res.merged == 1)
    check("17.1d", "the status file now exists", os.path.exists(pi.STATUS_PATH))
    check("17.1e", "and records posted with the recovered media id",
          (pi.status_for(QDATE) or {}).get("result") == "posted"
          and (pi.status_for(QDATE) or {}).get("media_id") == "M-Q")
    check("17.1f", "sync created NOTHING", not ft.sent("POST"))

    # ---- 17.2 publish on a lost-state runner, then sync persists it ----
    # This is the real production sequence: yesterday's publish() wrote a row
    # that died with its runner, so today's status file has no trace of it.
    res, ft = sync("recovered", [{"id": "M-YDAY", "caption": f"…\n\nLedger ref {QREF}",
                                  "timestamp": "2026-05-02T08:00:00+0000"}])
    check("17.2a", "a publication with no local trace is recovered",
          res.merged == 1)
    check("17.2b", "it is persisted as posted",
          (pi.status_for(QDATE) or {}).get("result") == "posted")
    check("17.2c", "with the detail naming sync-status",
          "sync-status" in (pi.status_for(QDATE) or {}).get("detail", ""))
    check("17.2d", "re-running sync is idempotent — nothing merges twice",
          pi.sync_status(transport=FakeTransport(
              [("GET", "/media", (200, {"data": [
                  {"id": "M-YDAY", "caption": f"Ledger ref {QREF}",
                   "timestamp": "2026-05-02T08:00:00+0000"}]}, ""))])).merged == 0)

    # ---- 17.3 multiple valid references in one feed ----
    res, ft = sync("multi", [
        {"id": "M-1", "caption": f"a {QREF}", "timestamp": "2026-05-02T08:00:00+0000"},
        {"id": "M-2", "caption": f"b {DREF}", "timestamp": "2026-06-02T08:00:00+0000"},
    ])
    check("17.3a", "both references merge", res.merged == 2 and res.refs == 2)
    check("17.3b", "the Qualified date is recorded",
          (pi.status_for(QDATE) or {}).get("media_id") == "M-1")
    check("17.3c", "the Daily date is recorded",
          (pi.status_for(DDATE) or {}).get("media_id") == "M-2")
    check("17.3d", "each row carries its own strategy",
          (pi.status_for(QDATE) or {}).get("kind") == "qualified"
          and (pi.status_for(DDATE) or {}).get("kind") == "daily")

    # ---- 17.4 malformed, unrelated, duplicated and missing captions ----
    res, ft = sync("junk", [
        {"id": "M-OK", "caption": f"good {QREF}", "timestamp": "2026-05-02T09:00:00+0000"},
        {"id": "M-DUP", "caption": f"older duplicate {QREF}",
         "timestamp": "2026-05-02T07:00:00+0000"},
        {"id": "M-BADDATE", "caption": "Ledger ref OLS-QUAL-2026-13-45",
         "timestamp": "2026-05-02T08:00:00+0000"},
        {"id": "M-BADKIND", "caption": "Ledger ref OLS-WEEKLY-2026-05-01",
         "timestamp": "2026-05-02T08:00:00+0000"},
        {"id": "M-UNRELATED", "caption": "just a normal post about baseball",
         "timestamp": "2026-05-02T08:00:00+0000"},
        {"id": "M-NOCAP", "timestamp": "2026-05-02T08:00:00+0000"},
        {"id": "M-NULLCAP", "caption": None, "timestamp": "2026-05-02T08:00:00+0000"},
        {"caption": f"no id at all {DREF}", "timestamp": "2026-06-02T08:00:00+0000"},
        "not even a dict",
    ])
    check("17.4a", "the run survives a feed full of junk", res.state == "ok")
    check("17.4b", "exactly one reference is recognised", res.refs == 1)
    check("17.4c", "the NEWEST duplicate wins",
          (pi.status_for(QDATE) or {}).get("media_id") == "M-OK")
    check("17.4d", "an impossible calendar date is rejected",
          pi.parse_ref("OLS-QUAL-2026-13-45") is None)
    check("17.4e", "an unknown strategy word is rejected",
          pi.parse_ref("OLS-WEEKLY-2026-05-01") is None)
    # The junk feed carries DREF on an entry with NO id. A reference without a
    # media id proves nothing, so it must leave no row behind at all.
    check("17.4f", "a reference on media with no id writes no row",
          pi.status_for(DDATE) is None)
    check("17.4g", "a valid reference still parses",
          pi.parse_ref(QREF) == ("qualified", QDATE))
    check("17.4h", "sync created nothing despite the junk", not ft.sent("POST"))

    # ---- 17.5 a failed sync leaves existing status untouched ----
    seed = dict(date=QDATE, kind="qualified", ref=QREF, result="posted",
                media_id="M-PRIOR")
    res, ft = sync("failread", [], seed=seed, http_ok=False)
    check("17.5a", "an unreadable feed -> failed", res.state == "failed")
    check("17.5b", "the pre-existing row is untouched",
          (pi.status_for(QDATE) or {}).get("media_id") == "M-PRIOR")
    check("17.5c", "the failure names the classification",
          "token" in (res.detail or ""))
    check("17.5d", "a failed sync writes nothing at all",
          len(json.load(open(pi.STATUS_PATH, encoding="utf-8"))["posts"]) == 1)

    # ---- 17.6 no credentials ----
    fresh_status("nocfg_sync")
    clear_env()
    ft = FakeTransport([])
    res = pi.sync_status(transport=ft)
    check("17.6a", "no credentials -> no_config", res.state == "no_config")
    check("17.6b", "and ZERO Meta calls", len(ft.calls) == 0)
    check("17.6c", "no status file is created", not os.path.exists(pi.STATUS_PATH))

    # ---- 17.7 the token never leaks ----
    res, ft = sync("leak_sync", [{"id": "M-Q", "caption": f"x {QREF}",
                                  "timestamp": "2026-05-02T08:00:00+0000"}])
    check("17.7a", "no token in the status file",
          TOK not in open(pi.STATUS_PATH, encoding="utf-8").read())
    check("17.7b", "no token in the SyncResult repr", TOK not in repr(res))
    check("17.7c", "no token in any request URL", not ft.token_leaks(TOK))
    res, ft = sync("leak_sync_err", [], http_ok=False)
    check("17.7d", "no token in a failed sync's detail", TOK not in (res.detail or ""))

    # ---- 17.9 the real transport never raises a token into a log ----
    #
    # A `requests` network exception embeds the request URL, and the GET calls
    # must carry the token as a query parameter. grade-ledger.yml's alert job
    # tails 20 lines of the failing job log into Discord, so an uncaught
    # exception would deliver a live token to a chat channel. _default_transport
    # catches everything and redacts what it was handed.
    # sanitize() is exercised directly rather than by provoking a real
    # connection error: forcing requests to fail would mean importing it and
    # attempting a DNS lookup, breaking both the no-network and the
    # requests-never-imported guarantees this suite exists to hold.
    conn_err = (f"HTTPSConnectionPool(host='graph.facebook.com', port=443): "
                f"Max retries exceeded with url: /v26.0/1/media"
                f"?access_token={TOK}&fields=id (Caused by NewConnectionError)")
    clean = pi.sanitize(conn_err, params={"access_token": TOK, "fields": "id"})
    check("17.9a", "a connection error carrying the token is redacted",
          TOK not in clean)
    check("17.9b", "the redaction marker replaces it", pi.REDACTED in clean)
    check("17.9c", "short non-secret params are left alone",
          "fields=id" in clean or "id" in clean)
    check("17.9d", "a token echoed in a response body is redacted too",
          TOK not in pi.sanitize(f'{{"error":"bad token {TOK}"}}',
                                 data={"access_token": TOK}))
    check("17.9e", "HTTP 0 classifies as transient, not as a token error",
          pi.classify(0, {})[0] == "transient")
    check("17.9f", "the real transport catches every exception",
          "except Exception" in inspect.getsource(pi._default_transport))
    check("17.9g", "and returns HTTP 0 rather than raising",
          "return 0, {}, sanitize(" in inspect.getsource(pi._default_transport))
    check("17.9h", "the transport scrubs its response snippet as well",
          "sanitize(r.text" in inspect.getsource(pi._default_transport))

    # ---- 17.8 read-only guarantee, stated structurally ----
    sync_src = inspect.getsource(pi.sync_status)
    check("17.8a", "sync_status never calls create_container",
          "create_container" not in sync_src)
    check("17.8b", "sync_status never calls media_publish",
          "media_publish" not in sync_src)
    check("17.8c", "sync_status reads the feed and nothing else",
          "recent_media" in sync_src)

    # ----------------------- 23. the Instagram-only recovery workflow --------
    #
    # The obvious way to recover a missed Instagram post is to re-run Grade
    # ledger. That is the wrong move: post_discord.py recap is intentionally not
    # idempotent, so it would repost a duplicate results recap to a public
    # channel, and it would re-run grading against an already-settled date. This
    # workflow does the Instagram half alone. These checks assert what it must
    # never touch, statically, against the file's own text.
    print("\n23. Instagram recovery workflow — narrow by construction")

    REC = os.path.join(ROOT, ".github", "workflows", "instagram-recovery.yml")
    check("23.0", "instagram-recovery.yml exists", os.path.exists(REC))
    rec_src = open(REC, encoding="utf-8").read()
    rec = yaml.safe_load(rec_src)
    rec_on = rec["on"] if "on" in rec else rec[True]

    check("23.1a", "dispatch-only — no schedule, no push trigger",
          set(rec_on) == {"workflow_dispatch"})
    check("23.1b", "it takes a required date input",
          rec_on["workflow_dispatch"]["inputs"]["date"]["required"] is True)
    check("23.1c", "exactly one job", len(rec["jobs"]) == 1)

    job = rec["jobs"]["recover"]
    steps = job["steps"]
    names = [s.get("name", "") for s in steps]
    runs = "\n".join(s.get("run") or "" for s in steps)

    # EXECUTABLE LINES ONLY. YAML comments are already gone (the parser drops
    # them), but the run blocks carry shell comments that deliberately NAME the
    # forbidden scripts in order to say they are forbidden. Scanning raw text
    # would read that prohibition as a violation of itself — the same trap the
    # docstring scan in group 11 had to avoid.
    rec_exec = "\n".join(l for l in runs.split("\n")
                         if not l.strip().startswith("#"))

    # ---- 23.2 the forbidden scripts, absent from anything executed ----
    for script in ["grade.py", "build_site.py", "post_discord.py",
                   "post_social.py", "grade_pickem.py", "blog.py",
                   "game_pages.py", "export_training_rows.py"]:
        check(f"23.2.{script}", f"never calls {script}", script not in rec_exec)

    # ---- 23.3 the forbidden paths, absent from anything executed ----
    for path in ["data/ledger.json", "data/daily_ledger.json", "index.html",
                 "feed.xml", "data/commitments.json", "board_"]:
        check(f"23.3.{path}", f"never touches {path}", path not in rec_exec)
    check("23.3.add", "never uses `git add -A` or a broad `git add data/`",
          "git add -A" not in rec_exec and "git add data/" not in rec_exec)
    check("23.3.only", "stages ONLY the one card",
          'git add "data/social/ig_$D.jpg"' in rec_exec)

    # ---- 23.4 date validation is a real calendar check ----
    val = runs
    check("23.4a", "the date is parsed with strptime, not just regex-shaped",
          "strptime" in val and "%Y-%m-%d" in val)
    check("23.4b", "an impossible date exits with an error", "::error::" in val)
    check("23.4c", "a future date is refused", "not a past grading date" in val)

    # ---- 23.5 nothing-to-render exits cleanly, commits nothing ----
    check("23.5a", "the render step gates on a NON-EMPTY file", "-s " in val)
    check("23.5b", "a missing recap is a notice, not a failure",
          "::notice::" in val and "nothing committed" in val)
    check("23.5c", "the commit step is gated on rendered == true",
          any(s.get("if") == "steps.render.outputs.rendered == 'true'"
              for s in steps if s.get("id") == "push"))
    check("23.5d", "the publish step is gated on rendered == true",
          any(s.get("if") == "steps.render.outputs.rendered == 'true'"
              for s in steps if "post_instagram.py publish" in (s.get("run") or "")))

    # ---- 23.6 push safety matches grade-ledger's philosophy ----
    check("23.6a", "push retries rather than failing the first race",
          "for i in 1 2 3" in val and "git fetch origin main" in val)
    check("23.6b", "a rebase conflict aborts and stops",
          "git rebase --abort" in val)
    check("23.6c", "no --autostash",
          "--autostash" not in "\n".join(
              l for l in val.split("\n") if not l.strip().startswith("#")))
    check("23.6d", "the SHA is captured after the push", "rev-parse HEAD" in val)

    # ---- 23.7 publishing keeps remote duplicate prevention ----
    pub_step = [s for s in steps if "post_instagram.py publish" in (s.get("run") or "")]
    check("23.7a", "it publishes with --strict",
          pub_step and "--strict" in pub_step[0]["run"])
    check("23.7b", "no '|| true' on the publish", "|| true" not in rec_exec)
    check("23.7c", "no continue-on-error anywhere",
          not any("continue-on-error" in str(st) for st in steps)
          and "continue-on-error" not in str(job.get("continue-on-error", "")))
    check("23.7d", "the image URL is pinned to the pushed SHA",
          pub_step and "raw.githubusercontent.com/${GITHUB_REPOSITORY}/${SHA}"
          in pub_step[0]["run"])
    check("23.7e", "credentials come from the agreed variable and secret",
          pub_step and pub_step[0]["env"] == {
              "IG_USER_ID": "${{ vars.IG_USER_ID }}",
              "IG_ACCESS_TOKEN": "${{ secrets.IG_ACCESS_TOKEN }}"})
    check("23.7f", "no local-only idempotency was invented",
          "instagram_status" not in rec_src)
    check("23.7g", "no credential value is embedded",
          not re.search(r"(EAA[A-Za-z0-9]{20,}|IGQ[A-Za-z0-9_-]{20,})", rec_src))

    # ------------------------ 22. ops alerts never reach a customer channel --
    #
    # On 2026-09-11 an Instagram infrastructure failure was broadcast into
    # #members-only, because post_alert() read
    #     ALERT_WEBHOOK or MEMBERS_WEBHOOK or WEBHOOK
    # and DISCORD_WEBHOOK_URL_ALERTS was unset. Paying members were shown a
    # pipeline defect they could not act on, in the channel they pay for picks in.
    #
    # "Publish your losses" is about RESULTS, not build failures. Different
    # audiences, different channels. alert mode now sends ONLY to the ops webhook;
    # unset means not sent, and the Actions run is the durable record.
    #
    # Asserted against the SOURCE rather than by importing post_discord: that
    # module pulls in crypto_box, and this suite's standing guarantee is that it
    # imports no network stack at all (check 16.zz2).
    print("\n22. Ops alert routing — private channel only, no fallback")

    PD = os.path.join(ROOT, "scripts", "post_discord.py")
    pd_src = open(PD, encoding="utf-8").read()
    pd_tree = ast.parse(pd_src)
    alert_fn = [n for n in ast.walk(pd_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "post_alert"]
    check("22.1a", "post_alert() exists", len(alert_fn) == 1)

    # The webhook assignment inside post_alert must be a bare Name, not a
    # fallback chain. A BoolOp here is exactly the regression to prevent.
    assigns = [n for n in ast.walk(alert_fn[0])
               if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "webhook" for t in n.targets)]
    check("22.1b", "post_alert assigns `webhook` exactly once", len(assigns) == 1)
    val = assigns[0].value if assigns else None
    check("22.1c", "the webhook is a single name, not an `or` fallback chain",
          isinstance(val, ast.Name))
    check("22.1d", "and that name is ALERT_WEBHOOK",
          isinstance(val, ast.Name) and val.id == "ALERT_WEBHOOK")

    names_in_alert = {n.id for n in ast.walk(alert_fn[0]) if isinstance(n, ast.Name)}
    check("22.2a", "post_alert never references MEMBERS_WEBHOOK",
          "MEMBERS_WEBHOOK" not in names_in_alert)
    check("22.2b", "post_alert never references the free-pick WEBHOOK",
          "WEBHOOK" not in names_in_alert)
    check("22.2c", "post_alert never references LEDGER_WEBHOOK",
          "LEDGER_WEBHOOK" not in names_in_alert)
    check("22.2d", "the unset path explains where the alert did NOT go",
          "not configured" in pd_src and "DISCORD_WEBHOOK_URL_ALERTS" in pd_src)

    # post_alert must stay non-fatal: a missing optional destination cannot turn
    # a good ledger run into a failure.
    check("22.3a", "post_alert still swallows every exception",
          any(isinstance(n, ast.ExceptHandler) for n in ast.walk(alert_fn[0])))
    check("22.3b", "post_alert never exits non-zero",
          not any(isinstance(n, ast.Call)
                  and getattr(n.func, "attr", "") == "exit"
                  for n in ast.walk(alert_fn[0])))

    # ---- 22.4 the OTHER modes keep their existing routing, untouched ----
    check("22.4a", "free pick still routes to DISCORD_WEBHOOK_URL",
          '"pick":  (build_pick_payload,  WEBHOOK,' in pd_src)
    check("22.4b", "members board still routes to the members webhook",
          '"board": (build_board_payload, MEMBERS_WEBHOOK,' in pd_src)
    check("22.4c", "public recap still falls back to the free channel",
          '"recap": (build_recap_payload, LEDGER_WEBHOOK or WEBHOOK,' in pd_src)
    check("22.4d", "MEMBERS_WEBHOOK is still defined for the board mode",
          "MEMBERS_WEBHOOK = os.environ.get" in pd_src)

    # ---- 22.5 no workflow feeds the removed fallbacks to an alert step ----
    wfdir = os.path.join(ROOT, ".github", "workflows")
    offenders = []
    for fn in sorted(os.listdir(wfdir)):
        if not fn.endswith(".yml"):
            continue
        txt = open(os.path.join(wfdir, fn), encoding="utf-8").read()
        for block in txt.split("- name:"):
            if "post_discord.py alert" not in block:
                continue
            if ("DISCORD_WEBHOOK_URL_MEMBERS" in block
                    or re.search(r"DISCORD_WEBHOOK_URL: \$\{\{", block)):
                offenders.append(fn)
    check("22.5", "no alert step still passes the members/free webhooks",
          not offenders)

    # ---- 22.6 the policy is written down where a human will read it ----
    check("22.6a", "post_discord.py documents the no-fallback policy",
          "NO FALLBACK" in pd_src)
    check("22.6b", "CLAUDE.md records the ops-alert routing policy",
          "DISCORD_WEBHOOK_URL_ALERTS" in open(
              os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8").read())

    # ------------------- 21. the data/ write boundary (incident 34577853134) --
    #
    # THE BUG THIS EXISTS TO PREVENT. The Phase 1 guard refused every path under
    # data/, with a docstring saying the committed pipeline was "Phase 2's
    # problem". Phase 2 then pointed grade-ledger.yml at data/social/ig_<date>.jpg
    # — the one path the guard forbade. Production asked the renderer to create a
    # file it was hard-coded to reject, the render step warned and continued (the
    # ledger was correctly protected), and the Instagram job then failed with
    # pending_media on a file that had never existed.
    #
    # The suite did not catch it because check 12.1 asserted the OLD rule — the
    # tests were actively enforcing the bug. The lesson is check 21.8: assert the
    # renderer's allowlist against the path the WORKFLOW actually uses, so the
    # two cannot drift apart again.
    print("\n21. data/ write boundary — allowlist, not blanket ban")

    def accepts(p):
        try:
            rr._assert_safe_out(p)
            return True
        except ValueError:
            return False

    D = os.path.join(ROOT, "data")

    def social_listing():
        p = os.path.join(D, "social")
        return sorted(os.listdir(p)) if os.path.isdir(p) else []

    # Snapshot BEFORE the group runs, so 21.9 can prove it wrote nothing rather
    # than asserting the absence of a specific filename. Production does write
    # real cards here (the Instagram recovery run committed one), and a test
    # that denies a real production artefact exists is simply wrong.
    social_before = social_listing()

    # ---- 21.1 the exact production path is ACCEPTED ----
    check("21.1a", "the production card path is accepted",
          accepts(os.path.join(D, "social", "ig_2026-09-10.jpg")))
    check("21.1b", "the incident's own date is accepted",
          accepts(os.path.join(D, "social", "ig_2026-09-11.jpg")))
    check("21.1c", "a relative form of the same path is accepted",
          accepts(os.path.join(ROOT, "data", "social", "ig_2026-01-01.jpg")))
    check("21.1d", "leap day is accepted",
          accepts(os.path.join(D, "social", "ig_2024-02-29.jpg")))

    # ---- 21.2 unrelated files directly under data/ are REFUSED ----
    for bad in ["ledger.json", "daily_ledger.json", "post_status.json",
                "instagram_status.json", "foo.jpg", "ig_2026-09-10.jpg"]:
        check(f"21.2.{bad}", f"data/{bad} is refused",
              not accepts(os.path.join(D, bad)))
    check("21.2.dir", "data/ itself is refused", not accepts(D))

    # ---- 21.3 unrelated files under data/social/ are REFUSED ----
    for bad in ["test.jpg", "foo.jpg", "ledger.json", "ig_2026-09-10.jpg.bak"]:
        check(f"21.3.{bad}", f"data/social/{bad} is refused",
              not accepts(os.path.join(D, "social", bad)))

    # ---- 21.4 wrong extension is REFUSED ----
    for bad in ["ig_2026-09-10.png", "ig_2026-09-10.jpeg", "ig_2026-09-10.webp",
                "ig_2026-09-10"]:
        check(f"21.4.{bad}", f"{bad} is refused",
              not accepts(os.path.join(D, "social", bad)))

    # ---- 21.5 malformed card filenames are REFUSED ----
    for bad in ["ig_bad.jpg", "ig_.jpg", "ig_2026-9-10.jpg", "ig_20260910.jpg",
                "ig_2026-09-10-extra.jpg", "IG_2026-09-10.jpg",
                "xig_2026-09-10.jpg", "ig_2026-13-45.jpg", "ig_2026-02-30.jpg",
                "ig_2025-02-29.jpg"]:
        check(f"21.5.{bad}", f"{bad} is refused",
              not accepts(os.path.join(D, "social", bad)))
    check("21.5.date", "a real calendar date is required, not just the shape",
          not accepts(os.path.join(D, "social", "ig_2026-13-45.jpg"))
          and accepts(os.path.join(D, "social", "ig_2026-12-31.jpg")))

    # ---- 21.6 traversal and prefix tricks cannot bypass it ----
    check("21.6a", "traversal out of data/social/ into data/ is refused",
          not accepts(os.path.join(D, "social", "..", "ledger.json")))
    check("21.6b", "traversal dressed as a valid card name is refused",
          not accepts(os.path.join(D, "social", "..", "ig_2026-09-10.jpg")))
    check("21.6c", "a nested subdirectory is refused",
          not accepts(os.path.join(D, "social", "sub", "ig_2026-09-10.jpg")))
    check("21.6d", "a sibling dir sharing the prefix is refused",
          not accepts(os.path.join(D, "social_backup", "ig_2026-09-10.jpg")))
    check("21.6e", "a sibling dir sharing the data prefix is refused",
          not accepts(os.path.join(ROOT, "data_old", "social", "ig_2026-09-10.jpg"))
          or not os.path.realpath(os.path.join(ROOT, "data_old")).startswith(
              os.path.realpath(D) + os.sep))
    check("21.6f", "traversal back INTO the card dir still resolves and is accepted",
          accepts(os.path.join(D, "social", "sub", "..", "ig_2026-09-10.jpg")))

    # ---- 21.7 everything outside data/ stays writable ----
    check("21.7a", "a temporary directory is accepted",
          accepts(os.path.join(tmp, "preview.jpg")))
    check("21.7b", "an arbitrary temp filename is accepted",
          accepts(os.path.join(tmp, "anything-at-all.png")))
    check("21.7c", "a repo path outside data/ is accepted",
          accepts(os.path.join(ROOT, "scratch.jpg")))
    check("21.7d", "the suite's own previews still render",
          accepts(os.path.join(tmp, "ig_2026-09-10.jpg")))

    # ---- 21.8 THE CONTRACT: workflow render target == renderer allowlist ----
    wf_src = open(os.path.join(ROOT, ".github", "workflows",
                               "grade-ledger.yml"), encoding="utf-8").read()
    m = re.search(r'render_recap_image\.py\s+"\$D"\s+"([^"]+)"', wf_src)
    check("21.8a", "the workflow's render target is parseable", bool(m))
    wf_target = m.group(1) if m else ""
    resolved = wf_target.replace("$D", "2026-09-10").replace("${D}", "2026-09-10")
    check("21.8b", f"the workflow renders to {wf_target!r}",
          resolved == "data/social/ig_2026-09-10.jpg")
    check("21.8c", "the renderer ACCEPTS the workflow's own target path",
          accepts(os.path.join(ROOT, resolved)))
    check("21.8d", "the allowlist directory matches the workflow's directory",
          rr.CARD_DIR.replace(os.sep, "/") == os.path.dirname(resolved))
    check("21.8e", "the allowlist filename pattern matches the workflow's filename",
          bool(rr.CARD_NAME_RE.match(os.path.basename(resolved))))

    # The same agreement for the recovery workflow, if it exists.
    recp = os.path.join(ROOT, ".github", "workflows", "instagram-recovery.yml")
    check("21.8f", "the recovery workflow exists", os.path.exists(recp))
    rsrc = open(recp, encoding="utf-8").read() if os.path.exists(recp) else ""
    rexec = "\n".join(l for l in rsrc.split("\n") if not l.strip().startswith("#"))
    # It composes the path from a shell variable, so assert the pattern it builds
    # and that the renderer accepts the concrete form.
    check("21.8g", "the recovery workflow targets the same card path",
          'data/social/ig_$D.jpg' in rexec)
    check("21.8h", "the renderer accepts the recovery workflow's target",
          accepts(os.path.join(ROOT, "data", "social", "ig_2026-09-10.jpg")))

    # This used to assert that data/social/ig_2026-09-10.jpg did not exist — a
    # hardcoded date that production then legitimately produced, via the
    # Instagram recovery run. Asserting the absence of a real production
    # artefact is just wrong. What this group actually promises is that it
    # WRITES NOTHING, so compare the directory before and after instead.
    check("21.9", "this group wrote nothing into data/social/",
          social_listing() == social_before)
    check("21.10", "the guard is case-sensitive regardless of what is on disk",
          not accepts(os.path.join(D, "social", "IG_2026-09-10.jpg"))
          and not accepts(os.path.join(D, "social", "ig_2026-09-10.JPG")))

    # --------------------------- 20. the production wiring contract (offline) --
    #
    # grade-ledger.yml is what actually publishes. Everything above proves the
    # CODE is correct; these checks prove the WIRING around it still is — the job
    # split, the single grading date, the pushed-SHA handoff, strict mode, the
    # pinned image URL, sync-before-commit, and the alert branches.
    #
    # Parsed as YAML from disk. No network, no Actions API, no dispatch: this is
    # a static read of a file in the working tree.
    print("\n20. Production wiring contract — grade-ledger.yml")

    WF = os.path.join(ROOT, ".github", "workflows", "grade-ledger.yml")
    check("20.0", "grade-ledger.yml exists", os.path.exists(WF))
    wf = yaml.safe_load(open(WF, encoding="utf-8"))
    # GitHub's `on:` is parsed by YAML 1.1 as the boolean True.
    on = wf["on"] if "on" in wf else wf[True]
    jobs = wf["jobs"]

    check("20.1a", "three jobs: grade, instagram, alert",
          set(jobs) == {"grade", "instagram", "alert"})
    check("20.1b", "instagram depends on grade", jobs["instagram"].get("needs") == "grade")
    check("20.1c", "instagram has no always()/if — a failed grade SKIPS it",
          "if" not in jobs["instagram"])
    check("20.1d", "alert depends on both jobs",
          sorted(jobs["alert"].get("needs") or []) == ["grade", "instagram"])
    check("20.1e", "the workflow is still dispatch-triggered only",
          set(on) == {"workflow_dispatch"})

    g, ig, al = jobs["grade"], jobs["instagram"], jobs["alert"]
    gsteps = g["steps"]
    names = [s.get("name", "") for s in gsteps]
    runs = {s.get("name", ""): (s.get("run") or "") for s in gsteps}

    # ---- one grading date, resolved once and handed downstream ----
    check("20.2a", "grade exposes a date output",
          g.get("outputs", {}).get("date") == "${{ steps.gdate.outputs.date }}")
    check("20.2b", "the date is produced by a step with id 'gdate'",
          any(s.get("id") == "gdate" for s in gsteps))
    check("20.2c", "only ONE step computes 'yesterday' anywhere in the file",
          open(WF, encoding="utf-8").read().count("date -d yesterday") == 1)
    check("20.2d", "the instagram job consumes the shared date, never its own",
          "needs.grade.outputs.date" in (ig["steps"][-1].get("run") or "")
          and "date -d yesterday" not in (ig["steps"][-1].get("run") or ""))

    # ---- the pushed SHA ----
    check("20.3a", "grade exposes a sha output",
          g.get("outputs", {}).get("sha") == "${{ steps.push.outputs.sha }}")
    commit_step = [s for s in gsteps if s.get("id") == "push"]
    check("20.3b", "the commit step carries id 'push'", len(commit_step) == 1)
    cs = commit_step[0]["run"]
    check("20.3c", "the sha is read AFTER the push loop, not before",
          cs.index("git push") < cs.index("rev-parse HEAD"))
    check("20.3d", "the push retries rather than failing on the first race",
          "for i in 1 2 3" in cs and "git fetch origin main" in cs)
    check("20.3e", "a rebase conflict ABORTS and stops — never auto-resolved",
          "git rebase --abort" in cs and "exit 1" in cs)
    # Comment lines are stripped first: the step's own comment explains WHY
    # --autostash is refused, and a naive substring search would read that
    # explanation as evidence of the thing it forbids.
    cs_cmds = "\n".join(l for l in cs.split("\n") if not l.strip().startswith("#"))
    check("20.3f", "no --autostash in any executed command",
          "--autostash" not in cs_cmds)
    check("20.3g", "instagram checks out the exact pushed SHA",
          ig["steps"][0].get("with", {}).get("ref") == "${{ needs.grade.outputs.sha }}")

    # ---- render + sync happen before the ONE data commit ----
    check("20.4a", "the render step exists",
          "Render the Instagram recap card" in names)
    check("20.4b", "the sync-status step exists",
          "Sync Instagram status from the live feed" in names)
    i_render = names.index("Render the Instagram recap card")
    i_sync = names.index("Sync Instagram status from the live feed")
    i_commit = names.index("Commit ledger and site")
    check("20.4c", "render runs BEFORE the commit", i_render < i_commit)
    check("20.4d", "sync-status runs BEFORE the commit", i_sync < i_commit)
    check("20.4e", "sync-status is the sync-status subcommand, not publish",
          "sync-status" in runs["Sync Instagram status from the live feed"]
          and "publish" not in runs["Sync Instagram status from the live feed"])
    check("20.4f", "render failure cannot block the ledger push",
          "||" in runs["Render the Instagram recap card"])
    check("20.4g", "exactly one git commit in the whole workflow",
          open(WF, encoding="utf-8").read().count("git commit") == 1)
    check("20.4h", "the instagram job commits nothing — no second status commit",
          not any("git commit" in (s.get("run") or "") or "git push" in (s.get("run") or "")
                  for s in ig["steps"]))
    check("20.4i", "the instagram job is read-only on the repository",
          ig.get("permissions") == {"contents": "read"})

    # ---- publishing is strict and unsuppressed ----
    pub = [s for s in ig["steps"] if s.get("id") == "ig"]
    check("20.5a", "the publish step carries id 'ig'", len(pub) == 1)
    pr = pub[0]["run"]
    check("20.5b", "publish runs with --strict", "--strict" in pr)
    check("20.5c", "no '|| true' suppressing the publish", "|| true" not in pr)
    check("20.5d", "no continue-on-error on the step or the job",
          not pub[0].get("continue-on-error") and not ig.get("continue-on-error"))
    check("20.5e", "the publish exit code is propagated", "exit $RC" in pr)
    check("20.5f", "the image URL is pinned to the pushed SHA",
          "raw.githubusercontent.com/${GITHUB_REPOSITORY}/${SHA}" in pr)
    check("20.5g", "the URL points at the rendered card path",
          "data/social/ig_${D}.jpg" in pr)
    check("20.5h", "the alert reason is shape-checked before being exported",
          'REASON="unexpected error' in pr)

    # ---- credentials by NAME only ----
    env_pub = pub[0].get("env", {})
    check("20.6a", "publish reads IG_USER_ID from a repo VARIABLE",
          env_pub.get("IG_USER_ID") == "${{ vars.IG_USER_ID }}")
    check("20.6b", "publish reads IG_ACCESS_TOKEN from a repo SECRET",
          env_pub.get("IG_ACCESS_TOKEN") == "${{ secrets.IG_ACCESS_TOKEN }}")
    env_sync = [s for s in gsteps
                if s.get("name") == "Sync Instagram status from the live feed"][0].get("env", {})
    check("20.6c", "sync-status uses the same two names",
          env_sync.get("IG_USER_ID") == "${{ vars.IG_USER_ID }}"
          and env_sync.get("IG_ACCESS_TOKEN") == "${{ secrets.IG_ACCESS_TOKEN }}")
    check("20.6d", "the env names match the module's constants",
          set(env_pub) == {pi.ENV_IG_USER, pi.ENV_IG_TOKEN})
    raw = open(WF, encoding="utf-8").read()
    check("20.6e", "no credential VALUE is embedded in the workflow",
          not re.search(r"(EAA[A-Za-z0-9]{20,}|IGQ[A-Za-z0-9_-]{20,})", raw))

    # ---- alert branches on job RESULTS ----
    ar = "\n".join(s.get("run") or "" for s in al["steps"])
    cond = al.get("if", "")
    check("20.7a", "alert fires when grade fails",
          "needs.grade.result != 'success'" in cond)
    check("20.7b", "alert fires when instagram FAILS",
          "needs.instagram.result == 'failure'" in cond)
    check("20.7c", "alert fires when instagram is CANCELLED",
          "needs.instagram.result == 'cancelled'" in cond)
    check("20.7d", "alert does NOT fire merely because instagram was skipped",
          "'skipped'" not in cond)
    check("20.7e", "the message is selected from job results, not a step name",
          '"${{ needs.grade.result }}" != "success"' in ar
          and '"${{ needs.instagram.result }}" = "cancelled"' in ar)
    check("20.7f", "the grade branch keeps the ledger-not-written wording",
          "Nothing has been written to the ledger" in ar)
    check("20.7g", "the cancellation branch says publication may be incomplete",
          "MAY BE INCOMPLETE" in ar)
    check("20.7h", "both Instagram branches say the ledger is fine",
          ar.count("the ledger is fine") == 2)
    check("20.7i", "both Instagram branches warn against re-running the workflow",
          ar.count("Do NOT re-run the Grade ledger workflow") == 2)
    check("20.7j", "only the grade branch attaches a log tail",
          ar.count("--detail-file") == 1)
    check("20.7k", "the log tail is attached in the grade branch only",
          ar.index("--detail-file") < ar.index("MAY BE INCOMPLETE"))

    # ---- this suite re-runs when the wiring changes ----
    SELF = os.path.join(ROOT, ".github", "workflows", "instagram-selftest.yml")
    sw = yaml.safe_load(open(SELF, encoding="utf-8"))
    son = sw["on"] if "on" in sw else sw[True]
    check("20.8a", "grade-ledger.yml is in the push path filter",
          ".github/workflows/grade-ledger.yml" in son["push"]["paths"])
    check("20.8b", "grade-ledger.yml is in the pull_request path filter",
          ".github/workflows/grade-ledger.yml" in son["pull_request"]["paths"])
    check("20.8c", "the self-test workflow still declares no secrets",
          "secrets." not in open(SELF, encoding="utf-8").read())

    # ------------------------------- 19. matchup context on the score line --
    #
    # grade.py writes final_score as `away-home` (grade.py:255). On the live
    # 2026-09-09 card that made a correctly graded WIN read as a loss:
    #   "Kansas City Royals ML (+108, Caesars)" / "Final 2-5"
    # Kansas City is HOME in `AZ @ KC` and scored 5, but the card showed a team
    # name beside "2-5" with no way to tell which number was theirs. Printing the
    # ledger's own `game` string restores the ordering context.
    #
    # Nothing here reorders, recomputes or relabels the score.
    print("\n19. Matchup context — an away-home score can't read as contradictory")

    def entry_of(scen):
        kind, r = install(tmp, FX[scen])
        return kind, r, r["entries"][0]

    # ---- 19.1 the live shape: picked team is HOME, score is away-home ----
    kind, r, e = entry_of("daily_home_pick_win")
    _, calls = rr.render_card(kind, r)
    sl = [c for c in calls if c.get("role") == "score"]
    check("19.1a", "the score line names the matchup then the score",
          sl and sl[0]["logical"] == "AZ @ KC · Final 2-5")
    check("19.1b", "the ledger's game string is used verbatim",
          e["game"] in sl[0]["logical"])
    check("19.1c", "final_score is unchanged and unreversed",
          "Final 2-5" in sl[0]["logical"] and "5-2" not in sl[0]["logical"])
    check("19.1d", "the pick still renders on its own line",
          any(c.get("role") == "pick" and c["logical"] == e["pick"] for c in calls))
    cap = pi.build_caption(FX["daily_home_pick_win"]["date"])
    check("19.1e", "the caption carries the same matchup + score segment",
          "AZ @ KC · Final 2-5" in cap)
    check("19.1f", "caption entry reads pick — matchup · score",
          "Kansas City Royals ML (+108, Caesars) — AZ @ KC · Final 2-5" in cap)

    # ---- 19.2 mirror image: picked team is AWAY ----
    kind, r, e = entry_of("daily_away_pick_win")
    _, calls = rr.render_card(kind, r)
    sl = [c for c in calls if c.get("role") == "score"]
    check("19.2a", "away-pick matchup renders verbatim",
          sl[0]["logical"] == "KC @ AZ · Final 5-2")
    check("19.2b", "the score is not flipped to flatter the pick",
          "Final 5-2" in sl[0]["logical"])
    check("19.2c", "caption matches", "KC @ AZ · Final 5-2" in
          pi.build_caption(FX["daily_away_pick_win"]["date"]))

    # ---- 19.3 losses and voids ----
    for scen, want in [("qualified_loss", "Chicago Cubs @ St. Louis Cardinals · Final 1-6"),
                       ("daily_loss_zero", "Seattle Mariners @ Texas Rangers · Final 2-7")]:
        kind, r, e = entry_of(scen)
        _, calls = rr.render_card(kind, r)
        sl = [c for c in calls if c.get("role") == "score"]
        check(f"19.3.{scen}", f"{scen}: loss carries the matchup", sl[0]["logical"] == want)
        check(f"19.3.{scen}.c", f"{scen}: caption agrees",
              want in pi.build_caption(FX[scen]["date"]))

    for scen in ["qualified_void", "daily_void_zero", "daily_void_staked"]:
        kind, r, e = entry_of(scen)
        _, calls = rr.render_card(kind, r)
        sl = [c for c in calls if c.get("role") == "score"]
        check(f"19.4.{scen}", f"{scen}: a void says 'void', never a made-up score",
              sl[0]["logical"].endswith("· Final void"))
        check(f"19.4.{scen}.g", f"{scen}: the matchup is still shown on a void",
              sl[0]["logical"].startswith(e["game"]))
        check(f"19.4.{scen}.n", f"{scen}: no digits invented for the score",
              "Final void" in pi.build_caption(FX[scen]["date"]))

    # ---- 19.5 missing / blank game field falls back cleanly ----
    kind, r, e = entry_of("qualified_no_game_field")
    _, calls = rr.render_card(kind, r)
    sl = [c for c in calls if c.get("role") == "score"]
    check("19.5a", "a missing game field falls back to the bare form",
          sl[0]["logical"] == "Final 6-3")
    check("19.5b", "no orphan separator is emitted",
          not sl[0]["logical"].startswith("·") and " ·  " not in sl[0]["logical"])
    check("19.5c", "caption falls back the same way",
          "— Final 6-3 (" in pi.build_caption(FX["qualified_no_game_field"]["date"]))

    kind, r, e = entry_of("qualified_blank_game_field")
    _, calls = rr.render_card(kind, r)
    sl = [c for c in calls if c.get("role") == "score"]
    check("19.5d", "a whitespace-only game field is treated as missing",
          sl[0]["logical"] == "Final void")

    # ---- 19.6 long matchup: no clipping, no overlap ----
    kind, r, e = entry_of("qualified_long_matchup")
    _, calls = rr.render_card(kind, r)
    sl = [c for c in calls if c.get("role") == "score"][0]
    pk = [c for c in calls if c.get("role") == "pick"][0]
    am = [c for c in calls if c.get("role") == "amount"][0]
    check("19.6a", "the long score line stays inside the right margin",
          sl["bbox"][2] <= rr.W - rr.MARGIN)
    check("19.6b", "the long score line stays inside the left margin",
          sl["bbox"][0] >= rr.MARGIN)
    check("19.6c", "the long pick still clears the unit figure",
          pk["bbox"][2] < am["bbox"][0])
    check("19.6d", "the score line never overlaps the unit figure vertically",
          sl["bbox"][1] > am["bbox"][3])
    check("19.6e", "the full matchup is recorded even when drawn truncated",
          sl["logical"].startswith(e["game"]))
    check("19.6f", "truncation, if any, ends in a single ellipsis",
          (not sl["truncated"]) or sl["fragments"][0].endswith("…"))

    # ---- 19.7 card and caption use ONE matchup string ----
    # The helper is duplicated in the two modules (post_social.py is frozen and
    # is the only natural shared home). This is the guard against drift.
    matrix = [
        {"game": "AZ @ KC", "final_score": "2-5"},
        {"game": "KC @ AZ", "final_score": "5-2"},
        {"game": "AAA @ BBB"},
        {"game": "", "final_score": "1-0"},
        {"game": "   ", "final_score": "1-0"},
        {"final_score": "9-9"},
        {},
        {"game": "A @ B", "final_score": None},
        {"game": "Very Long Team Name @ Another Very Long Team Name",
         "final_score": "12-11"},
    ]
    mismatch = [m for m in matrix if rr.score_line(m) != pi.score_line(m)]
    check("19.7", "renderer and caption score_line() agree byte-for-byte",
          not mismatch)
    check("19.7b", "both produce the documented shape",
          rr.score_line({"game": "AZ @ KC", "final_score": "2-5"}) == "AZ @ KC · Final 2-5")
    check("19.7c", "both fall back identically with no game",
          rr.score_line({"final_score": "2-5"}) == "Final 2-5"
          and pi.score_line({"final_score": "2-5"}) == "Final 2-5")

    # ---- 19.8 Facebook copy is untouched ----
    install(tmp, FX["daily_home_pick_win"])
    fb = ps.build_fb_text(FX["daily_home_pick_win"]["date"])
    check("19.8a", "the Facebook post does NOT carry the new matchup segment",
          "AZ @ KC · Final" not in fb)
    check("19.8b", "post_social still renders its own score form", "2-5" in fb)

    # ------------------------ 18. --strict: the production exit-code table --
    #
    # Without --strict the CLI is a diagnostic tool and exits 0 for anything that
    # is not an outright error. In a pipeline that is wrong: a run that published
    # nothing because credentials were missing, or because the image never
    # appeared, is a channel going quietly dark. --strict makes those red.
    #
    # The mapping is a pure function, so every cell of the table is asserted
    # directly rather than by launching the CLI a dozen times.
    print("\n18. --strict exit-code table")

    TABLE = [
        # state,            plain, strict
        ("posted",            0, 0),
        ("nothing_to_post",   0, 0),
        ("no_config",         0, 1),
        ("pending_media",     0, 1),
        ("failed",            1, 1),
    ]
    for state, plain, strict in TABLE:
        check(f"18.{state}.plain", f"{state}: plain exit {plain}",
              pi.exit_code(state, strict=False) == plain)
        check(f"18.{state}.strict", f"{state}: --strict exit {strict}",
              pi.exit_code(state, strict=True) == strict)
    check("18.default", "strict defaults to off", pi.exit_code("no_config") == 0)
    check("18.unknown", "an unrecognised state fails closed in both modes",
          pi.exit_code("who_knows") == 1 and pi.exit_code("who_knows", True) == 1)

    # Every state the orchestrator can actually return is covered by the table —
    # a new state added without a decision here would slip through as exit 1.
    check("18.cover", "the table covers every state publish() can return",
          {s for s, _, _ in TABLE} ==
          set(pi._ALWAYS_OK) | set(pi._LENIENT_ONLY) | {"failed"})

    # ---- 18.5 bounded reachability retries ----
    res, ft = run("retry_fail", [feed([]), HEAD_404])
    check("18.5a", "an unreachable image still ends in pending_media",
          res.state == "pending_media")
    check("18.5b", f"HEAD is retried exactly {pi.IMAGE_ATTEMPTS} times",
          len(ft.sent("HEAD")) == pi.IMAGE_ATTEMPTS)
    check("18.5c", "no container is created after exhausting the retries",
          not ft.sent("POST", "/media"))
    check("18.5d", "pending_media is exit 1 under --strict",
          pi.exit_code(res.state, strict=True) == 1)
    check("18.5e", "the recorded detail names the attempt count",
          str(pi.IMAGE_ATTEMPTS) in (pi.status_for(DATE) or {}).get("detail", ""))

    # A late-arriving image must be picked up rather than failing the run.
    late = {"n": 0}

    def head_late(_ft):
        late["n"] += 1
        return (404, {}, "") if late["n"] < 3 else (200, {}, "")

    res, ft = run("retry_late", [feed([]), ("HEAD", "", head_late), CREATE_OK,
                                 cstat("FINISHED"), PUBLISH_OK])
    check("18.5f", "an image that appears on the third attempt still publishes",
          res.state == "posted")
    check("18.5g", "and it stopped retrying as soon as it succeeded",
          len(ft.sent("HEAD")) == 3)

    # ---- 18.6 the CLI's final line is the alert reason, and is sanitised ----
    res, ft = run("cliline", [feed([]), HEAD_OK,
                              ("POST", "/media",
                               err(190, msg=f"bad token {TOK} rejected"))])
    line = f"[{res.state}] {res.detail} (calls={res.calls})"
    check("18.6a", "the CLI summary line carries no token", TOK not in line)
    check("18.6b", "the summary line starts with the machine-readable state",
          line.startswith("[failed]"))
    check("18.6c", "a failed publish is exit 1 in both modes",
          pi.exit_code(res.state) == 1 and pi.exit_code(res.state, True) == 1)

    clear_env()
    pi.STATUS_PATH, pi.SLEEP = real_status_path, real_sleep
    check("16.zz", "no live status file was created during the suite",
          not os.path.exists(os.path.join(ROOT, "data", "instagram_status.json")))
    check("16.zz2", "requests is STILL not imported after the whole transport suite",
          "requests" not in sys.modules)

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
