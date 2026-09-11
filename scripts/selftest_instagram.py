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
renderer refuses that path outright and check 12.1 proves it, while the
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
import inspect
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
EXPECTED_CHECKS = 622

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
