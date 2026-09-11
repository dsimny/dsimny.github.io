#!/usr/bin/env python3
"""
Open Ledger Sports — deterministic recap card for Instagram (Phase 1).

Renders YESTERDAY's graded result as a 1080x1350 JPEG. Instagram's documented
feed range is 4:5 to 1.91:1 with a 1440px maximum width, so 1080x1350 is exactly
4:5 and comfortably inside the width cap.

WHAT THIS FILE IS NOT. It performs NO ledger math. It consumes the dict
scripts/post_social.py's select_recap() already returns — the same dict the
Facebook and X posts are built from — so the card, the FB post and the site can
never disagree about a night's record. Every figure printed here was computed by
post_social.py and, before that, by grade.py. If a number looks wrong on a card,
the bug is upstream and the card is faithfully reporting it.

TWO LEDGERS, NEVER MIXED (house rules 6 and 9). select_recap() returns a `kind`
of "qualified" or "daily"; this module dispatches to _draw_qualified or
_draw_daily and the two never share a figure. The recap dicts carry disjoint
unit keys (the Qualified one has pnl/units_net/roi_pct, the Daily one has
pnl_paper/paper_units_net/staked_*), so a card can only ever show one ledger.
EXACTLY ONE running-ledger line is drawn on any card, and it is labelled with
the ledger it came from.

RESULT MARKS ARE DRAWN, NEVER TYPESET. The Facebook and X posts use ✅/❌/⚪.
In the vendored DejaVu Sans, U+2705 and U+274C both render as .notdef — the
SAME tofu box — while U+26AA renders a real glyph. A win and a loss would be
pixel-identical and a void would look correct, which is a failure that reads as
success at a glance. So WIN/LOSS/VOID are filled circles with stroked
primitives, and the literal WORD is set beside every one. The result is never
carried by a glyph and never by colour alone.

DETERMINISM IS THE CONTRACT. Same recap dict in, byte-identical JPEG out:
  - fonts load from assets/fonts/ by repository-relative path, never by name
    (Pillow 12 ships no font of its own; a name would resolve to whatever the
    host has, and would drift when GitHub refreshes the runner image);
  - no timestamp, no EXIF, no ICC profile is written;
  - JPEG parameters are pinned explicitly rather than left to Pillow defaults.
Pillow itself is pinned in requirements.txt for the same reason.

READS NOTHING SENSITIVE. The only files opened are the two fonts and the logo.
It never opens a board, never touches a .enc file, never imports crypto_box, and
only ever sees entries post_social.py already filtered to SETTLED — so no held
or unrevealed pick can reach a card. scripts/selftest_instagram.py asserts this
against the source text, not just by convention.

Run:  python scripts/render_recap_image.py <YYYY-MM-DD> <out.jpg>
"""
import os
import re
import sys
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import post_social as ps  # noqa: E402

# ---------------------------------------------------------------- geometry --
W, H = 1080, 1350
MARGIN = 64
CONTENT_W = W - 2 * MARGIN

HEADER_Y = 60
LOGO_PX = 88
RULE1_Y = 196
DATE_Y = 228
ROWS_Y0 = 312
ROW_H = 78
MAX_ROWS = 6                 # a long slate is capped; the card must never overflow
MORE_H = 40
NOTE_LINE_H = 38
BOTTOM_PAD = 62

# Clear space always kept between the end of the pick text and the start of the
# right-aligned unit figure. Wide enough that the two never read as one string
# on a phone — a pick like "Texas Rangers ML (+115, BetMGM)" already ends in a
# signed number, and "(+115, BetMGM) +0.29u" with a thin gap invites misreading
# the price as the result.
PICK_GUTTER = 40

# THE LAYOUT IS COMPUTED, NOT HARDCODED, and that is a correctness property
# rather than a style choice. An earlier draft pinned every band to a fixed y and
# gave the disclosure a four-line cap. The allocated disclosure needs seven lines,
# so it rendered as "...does not enter the …" — the card silently truncated the
# one paragraph that may never be trimmed, while the tests still passed because
# they only checked the shorter zero-allocation wording.
#
# So: the footer and the disclosure are laid out BOTTOM-UP from a fixed floor and
# are never capped, the header and results run TOP-DOWN, and the number of result
# rows yields to whatever the disclosure needs. _layout() raises rather than
# overlapping, so the failure mode is a loud error, never a quietly clipped
# legal paragraph.

# ----------------------------------------------------------------- palette --
# Lifted from scripts/build_site.py so a card looks like the site rather than
# like a second brand. Do not introduce a colour that is not already on the site.
BG = "#0d0d0d"
PANEL = "#1a1a19"
RULE = "#2c2c2a"
TEXT = "#c3c2b7"
MUTED = "#898781"
WIN_C = "#0ca30c"
LOSS_C = "#d03b3b"
VOID_C = "#898781"
ACCENT = "#d95926"
HIGHLIGHT = "#fab219"
WHITE = "#ffffff"

# -------------------------------------------------------------------- copy --
BRAND = "OPEN LEDGER SPORTS"
TAG_QUALIFIED = "QUALIFIED PLAYS"
TAG_DAILY = "DAILY PICK  ·  lower-bar strategy"

# The exact responsible-gaming line the card must carry. Deliberately NOT
# ps.RG: that one reads "not betting advice" and this one is required to read
# "Analytics, not betting advice." Both say the same thing; the card's wording
# is fixed by spec, so it is stated here literally rather than derived.
CARD_RG = "21+ · 1-800-GAMBLER · Analytics, not betting advice."
SITE_TEXT = "openledgersports.com"

# The first sentence of ps.LEGAL, verbatim. selftest_instagram.py asserts it is
# a substring of ps.LEGAL, so the card cannot drift away from the copy the
# Facebook post publishes.
CARD_NOTE = ("Every pick is committed to the public record before first pitch "
             "and graded here after — wins and losses alike.")

LEDGER_LABEL_QUALIFIED = "RUNNING LEDGER — QUALIFIED PLAYS"
LEDGER_LABEL_DAILY_PAPER = "DAILY PICK LEDGER — PAPER"
LEDGER_LABEL_DAILY_STAKED = "DAILY PICK LEDGER — RECORDED ALLOCATION"

RESULT_WORD = {"WIN": "WIN", "LOSS": "LOSS", "VOID": "VOID"}


def score_line(e):
    """`AZ @ KC · Final 2-5` — the matchup, then the score, both verbatim.

    WHY THE MATCHUP IS HERE. grade.py writes final_score as `away-home`
    (grade.py:255), so a correctly graded WIN on a HOME pick reads as a loss
    when the score stands alone: "Kansas City Royals ML … Final 2-5" looks like
    Kansas City scored 2. They scored 5 — the ledger's `game` is `AZ @ KC`, so
    Kansas City is the home side and the second number is theirs. Printing the
    matchup restores the only context that makes the ordering readable.

    NOTHING IS REINTERPRETED. The score is not reordered, recomputed or
    relabelled, and the matchup is the ledger's own `game` string verbatim —
    this module still reports what grade.py recorded and nothing else. An entry
    with no usable `game` falls back to the previous bare form rather than
    inventing a matchup, and a void still renders as `void` rather than a
    fabricated score.

    DUPLICATED, DELIBERATELY, in post_instagram.py. The natural shared home is
    post_social.py, which both modules already import — but the Facebook copy is
    frozen and out of scope here. Rather than have the renderer import the
    publishing module (which would drag PIL into a caption-only path) the four
    lines are repeated, and selftest_instagram.py check 19.7 asserts the two
    implementations agree byte-for-byte across a matrix of entries, so they
    cannot drift apart unnoticed.
    """
    score = e.get("final_score") or "void"
    game = (e.get("game") or "").strip()
    return f"{game} · Final {score}" if game else f"Final {score}"

# ------------------------------------------------------------------- fonts --
FONT_DIR = os.path.join(ROOT, "assets", "fonts")
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

MIN_BODY_PX = 30             # nothing readable on a phone goes below this

_FONT_CACHE = {}


def font(size, bold=False):
    """Load a vendored face by PATH. Never ImageFont.truetype("DejaVu Sans") —
    that asks the host for a font and makes the output machine-dependent."""
    key = (size, bold)
    if key not in _FONT_CACHE:
        path = FONT_BOLD if bold else FONT_REGULAR
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"vendored font missing: {path}. See assets/fonts/README.md — the "
                f"renderer will not fall back to a system font.")
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


def text_width(s, f):
    """INK width — the extent of the drawn pixels only.

    Use this for wrapping, where what matters is whether the glyphs fit. Do NOT
    use it to reserve space next to another element: ink width ignores side
    bearings, so `x0` is discarded and the figure comes out narrower than the
    space the string actually occupies.
    """
    if not s:
        return 0
    box = f.getbbox(s)
    return box[2] - box[0]


def advance(s, f):
    """TRUE advance width — what the string actually occupies, bearings included.

    This is the measurement that governs collision. The result column reserves
    space with `advance`, never with `text_width`, so a pick can never be sized
    into the gap the unit figure needs.
    """
    return f.getlength(s) if s else 0.0


def fit(s, f, max_w):
    """Truncate to max_w, appending an ellipsis. Returns (drawn, truncated).

    Measured with `advance`, not ink width: this decides how much room a string
    is allowed to CLAIM, and claiming is an advance-width question.
    """
    if max_w <= 0:
        return "…", True
    if advance(s, f) <= max_w:
        return s, False
    ell = "…"
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if advance(s[:mid] + ell, f) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    # rstrip can only shorten, so the result still fits.
    return (s[:lo].rstrip() + ell) if lo else ell, True


def wrap(s, f, max_w, max_lines=None):
    """Greedy word wrap on measured widths. Returns (lines, dropped_any)."""
    words, lines, cur = s.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if advance(trial, f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if max_lines is not None and len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1], _ = fit(kept[-1] + " …", f, max_w)
        return kept, True
    return lines, False


# ------------------------------------------------------------------ canvas --
class Canvas:
    """A thin recorder around ImageDraw.

    Every draw is logged. The tests assert on the LOG rather than on pixels, so
    they can prove "the card says 1-800-GAMBLER" without a brittle image diff.

    Each text record carries both `logical` (the full string the card means to
    say) and `fragments` (what was physically drawn after wrapping or
    truncating). The suite checks the logical string is present AND that the
    fragments reconstruct it whenever nothing was truncated — so a wrap can
    never silently swallow half a disclosure.
    """

    def __init__(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img)
        self.calls = []

    def text(self, xy, s, f, fill, anchor=None, logical=None, truncated=False,
             role=None):
        """Draw and record. `bbox` is the REAL ink rectangle Pillow reports for
        the drawn string at this position and anchor, so the tests can prove two
        elements do not intersect instead of trusting the arithmetic that placed
        them."""
        self.d.text(xy, s, font=f, fill=fill, anchor=anchor)
        self.calls.append({
            "kind": "text", "role": role,
            "logical": logical if logical is not None else s,
            "fragments": [s],
            "truncated": truncated,
            "xy": xy, "size": f.size, "fill": fill,
            "bbox": self.d.textbbox(xy, s, font=f, anchor=anchor),
        })

    def text_block(self, x, y, s, f, fill, line_h, max_w, max_lines=None,
                   role=None):
        """Wrapped paragraph recorded as ONE logical string."""
        lines, dropped = wrap(s, f, max_w, max_lines)
        boxes = []
        for i, line in enumerate(lines):
            pos = (x, y + i * line_h)
            self.d.text(pos, line, font=f, fill=fill)
            boxes.append(self.d.textbbox(pos, line, font=f))
        self.calls.append({
            "kind": "text", "role": role,
            "logical": s,
            "fragments": lines,
            "truncated": dropped,
            "xy": (x, y), "size": f.size, "fill": fill,
            "bbox": (min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes))
            if boxes else None,
        })
        return y + len(lines) * line_h

    def rule(self, y, fill=RULE):
        self.d.rectangle([MARGIN, y, W - MARGIN, y + 1], fill=fill)
        self.calls.append({"kind": "shape", "shape": "rule", "y": y})

    def panel(self, box, fill=PANEL):
        self.d.rectangle(box, fill=fill)
        self.calls.append({"kind": "shape", "shape": "panel", "box": tuple(box)})

    def mark(self, cx, cy, result):
        """WIN / LOSS / VOID as primitives. No glyph, no emoji, and never
        colour alone — the literal word is drawn beside this by the caller."""
        r = 22
        box = [cx - r, cy - r, cx + r, cy + r]
        if result == "WIN":
            self.d.ellipse(box, fill=WIN_C)
            self.d.line([(cx - 10, cy + 1), (cx - 3, cy + 8), (cx + 11, cy - 8)],
                        fill=WHITE, width=5, joint="curve")
            shape = "circle+check"
        elif result == "LOSS":
            self.d.ellipse(box, fill=LOSS_C)
            self.d.line([(cx - 8, cy - 8), (cx + 8, cy + 8)], fill=WHITE, width=5)
            self.d.line([(cx - 8, cy + 8), (cx + 8, cy - 8)], fill=WHITE, width=5)
            shape = "circle+cross"
        else:
            self.d.ellipse(box, outline=VOID_C, width=4)
            self.d.line([(cx - 9, cy), (cx + 9, cy)], fill=VOID_C, width=4)
            shape = "ring+dash"
        self.calls.append({"kind": "shape", "shape": shape, "result": result,
                           "center": (cx, cy)})

    def logo(self, x, y, px):
        src = os.path.join(ROOT, "assets", "logo.jpg")
        with Image.open(src) as im:
            im = im.convert("RGB").resize((px, px), Image.LANCZOS)
            self.img.paste(im, (x, y))
        self.calls.append({"kind": "image", "src": "assets/logo.jpg",
                           "box": (x, y, px, px)})


# ------------------------------------------------------------------ shared --
# Height of the day-line + running-ledger block, measured from its top rule to
# the bottom of the ledger figure. Fixed because every card draws the same three
# elements at the same sizes.
BLOCK_H = 186


def _layout(note):
    """Bottom-up floor. The disclosure is wrapped WITHOUT a line cap, so it
    always renders in full; everything above yields to the space it needs."""
    f = font(MIN_BODY_PX)
    note_lines, dropped = wrap(note, f, CONTENT_W)
    if dropped:                                   # unreachable: no cap is passed
        raise ValueError("the disclosure must never be truncated")
    rg_lines, _ = wrap(CARD_RG, f, CONTENT_W)

    site_y = H - BOTTOM_PAD - 38
    rg_y = site_y - 12 - len(rg_lines) * NOTE_LINE_H
    rule3_y = rg_y - 22
    note_y = rule3_y - 26 - len(note_lines) * NOTE_LINE_H
    return {"note": note, "note_y": note_y, "rule3_y": rule3_y,
            "rg_y": rg_y, "site_y": site_y, "floor": note_y - 30}


def _rows_that_fit(n_entries, floor):
    """How many result rows the remaining space allows, never above MAX_ROWS.
    A long slate gives way to the disclosure, never the other way round."""
    avail = floor - BLOCK_H - ROWS_Y0
    fit = max(1, avail // ROW_H)
    if n_entries > fit:
        fit = max(1, (avail - MORE_H) // ROW_H)
    return int(min(MAX_ROWS, fit))


def _header(c, tag):
    c.logo(MARGIN, HEADER_Y, LOGO_PX)
    tx = MARGIN + LOGO_PX + 24
    c.text((tx, HEADER_Y + 6), BRAND, font(40, bold=True), TEXT)
    c.text((tx, HEADER_Y + 56), tag, font(MIN_BODY_PX), ACCENT)
    c.rule(RULE1_Y)


def _entry_rows(c, entries, amount_fn, cap):
    """One row per settled entry, capped. Returns the y the rows end at."""
    shown = entries[:cap]
    for i, e in enumerate(shown):
        y = ROWS_Y0 + i * ROW_H
        result = e.get("result", "")
        c.mark(MARGIN + 22, y + 24, result)

        word = RESULT_WORD.get(result, "—")
        c.text((MARGIN + 56, y + 8), word, font(26, bold=True),
               {"WIN": WIN_C, "LOSS": LOSS_C}.get(result, VOID_C),
               role="result_word")

        # THE UNIT FIGURE IS PLACED FIRST AND IS NEVER NEGOTIABLE. It is the
        # result of the pick; a card that truncates or overlaps it has published
        # a wrong number, which is worse than a shortened team name.
        #
        # Right-aligned by ANCHOR ("ra") at the margin rather than by subtracting
        # a measured width from it. Subtraction placed the draw origin using ink
        # width, which ignores side bearings and let the glyphs drift past the
        # right margin; the anchor pins the right edge exactly where it belongs.
        amount = amount_fn(e)
        af = font(MIN_BODY_PX, bold=True)
        c.text((W - MARGIN, y + 6), amount, af,
               WIN_C if amount.startswith("+") else
               (LOSS_C if amount.startswith("-") else MUTED),
               anchor="ra", role="amount")

        # The pick then gets whatever is left, measured with ADVANCE (not ink)
        # so the reservation reflects the space the figure truly occupies, and
        # with a real gutter so the two never read as one string.
        amount_left = (W - MARGIN) - advance(amount, af)
        px = MARGIN + 150
        pf = font(32)
        pick = e.get("pick", "")
        drawn, trunc = fit(pick, pf, amount_left - PICK_GUTTER - px)
        c.text((px, y + 4), drawn, pf, TEXT, logical=pick, truncated=trunc,
               role="pick")

        # Belt and braces against the arithmetic above: compare the rectangles
        # Pillow actually reported. A future font, size or format change that
        # reintroduces a collision fails loudly here rather than shipping a card
        # with the result sitting on top of the selection.
        pick_box, amount_box = c.calls[-1]["bbox"], c.calls[-2]["bbox"]
        if pick_box[2] >= amount_box[0]:
            raise ValueError(
                f"row collision: pick ends at x={pick_box[2]} but the unit "
                f"figure starts at x={amount_box[0]} ({pick!r} vs {amount!r})")

        sf = font(26)
        line, strunc = fit(score_line(e), sf, W - MARGIN - px)
        c.text((px, y + 44), line, sf, MUTED, logical=score_line(e),
               truncated=strunc, role="score")

    y_end = ROWS_Y0 + len(shown) * ROW_H
    if len(entries) > len(shown):
        more = f"+{len(entries) - len(shown)} more on {SITE_TEXT}"
        c.text((MARGIN, y_end + 4), more, font(MIN_BODY_PX), MUTED)
        y_end += MORE_H
    return y_end


def _footer(c, L):
    """Disclosure, rule, responsible-gaming line, site — all bottom-anchored and
    none of them capped."""
    c.text_block(MARGIN, L["note_y"], L["note"], font(MIN_BODY_PX), MUTED,
                 NOTE_LINE_H, CONTENT_W)
    c.rule(L["rule3_y"])
    c.text_block(MARGIN, L["rg_y"], CARD_RG, font(MIN_BODY_PX), TEXT,
                 NOTE_LINE_H, CONTENT_W)
    c.text((MARGIN, L["site_y"]), SITE_TEXT, font(MIN_BODY_PX, bold=True),
           HIGHLIGHT)


def _block_top(rows_end, floor):
    """Where the day + ledger block starts.

    It is anchored to the BOTTOM of the available space, not to the end of the
    results. Most nights carry a single pick, and top-anchoring left a ~500px
    hole between the totals and the disclosure that read as a broken card.
    Bottom-anchoring moves that slack above the totals instead, where it reads
    as a deliberate break between "what happened" and "where that leaves us".
    On a full slate the slack is zero and this is a no-op.
    """
    return max(rows_end, floor - BLOCK_H)


def _day_and_ledger(c, y0, day_text, ledger_label, ledger_value, floor):
    """The ONE running ledger a card is allowed to carry."""
    c.rule(y0 + 22)
    c.text((MARGIN, y0 + 44), day_text, font(44, bold=True), TEXT)
    c.text((MARGIN, y0 + 116), ledger_label, font(26), MUTED)
    c.text((MARGIN, y0 + 148), ledger_value, font(40, bold=True), TEXT)
    bottom = y0 + BLOCK_H
    if bottom > floor:
        raise ValueError(
            f"layout overflow: the ledger block ends at {bottom} but the "
            f"disclosure starts at {floor}. Something above must give way — "
            f"never the disclosure.")
    return bottom


# -------------------------------------------------------------- card types --
def _draw_qualified(c, r):
    L = _layout(CARD_NOTE)
    _header(c, TAG_QUALIFIED)
    c.text((MARGIN, DATE_Y), r["nice"], font(52, bold=True), TEXT)
    cap = _rows_that_fit(len(r["entries"]), L["floor"])
    y = _entry_rows(c, r["entries"], lambda e: f"{e['pnl']:+.2f}u", cap)
    day = ps._day_record(r["day_w"], r["day_l"], r["day_v"])
    _day_and_ledger(c, _block_top(y, L["floor"]),
                    f"Day: {day} · {r['day_pnl']:+.2f}u",
                    LEDGER_LABEL_QUALIFIED,
                    ps._running(r), L["floor"])
    _footer(c, L)


def _draw_daily(c, r):
    staked = r["staked_units"]

    # Whichever basis is OFFICIAL for this pick leads — the recorded allocation
    # when a human authorised one, the paper basis otherwise. Calling an
    # allocated pick paper-only would be the false claim house rule 8 exists to
    # prevent; neither wording asserts that a bet was placed.
    #
    # The disclosure is chosen FIRST because the layout is built around it: the
    # allocated wording is markedly longer (it must also say we place no wagers)
    # and the rest of the card yields to it.
    if staked:
        note = ps.DISCLOSURE_FB_STAKED.format(stake=f"{staked:.2f}")
        amount = lambda e: f"{(e.get('pnl_staked') or 0):+.2f}u"          # noqa: E731
        day_line = None
    else:
        note = ps.DISCLOSURE_FB.format(basis=ps._basis_str(r))
        amount = lambda e: f"{(e.get('pnl_paper') or 0):+.2f}u"           # noqa: E731
        day_line = None

    L = _layout(note)
    _header(c, TAG_DAILY)
    c.text((MARGIN, DATE_Y), r["nice"], font(52, bold=True), TEXT)
    cap = _rows_that_fit(len(r["entries"]), L["floor"])
    y = _entry_rows(c, r["entries"], amount, cap)
    day = ps._day_record(r["day_w"], r["day_l"], r["day_v"])

    if staked:
        net = r["staked_net"] if r["staked_net"] is not None else 0.0
        day_line = f"Day: {day} · {r['staked_pnl']:+.2f}u on {staked:.2f}u allocated"
        label, value = (LEDGER_LABEL_DAILY_STAKED,
                        f"{r['record']} · {net:+.2f}u net")
    else:
        day_line = f"Day: {day} · {r['day_pnl_paper']:+.2f}u paper"
        label, value = LEDGER_LABEL_DAILY_PAPER, ps._running_daily(r)

    _day_and_ledger(c, _block_top(y, L["floor"]), day_line, label, value, L["floor"])
    _footer(c, L)


# ------------------------------------------------------------------- entry --
def render_card(kind, r):
    """(kind, recap) -> (PIL.Image RGB, draw-call log). No I/O beyond fonts."""
    if kind not in ("qualified", "daily"):
        raise ValueError(f"unknown recap kind {kind!r}")

    # Belt and braces on top of post_social's own filter: a card may only ever
    # show a settled result, so an entry in any other state is a hard error
    # rather than something that renders and gets published.
    for e in r["entries"]:
        if e.get("result") not in ps.SETTLED:
            raise ValueError(f"refusing to render unsettled entry: {e.get('result')!r}")

    img = Image.new("RGB", (W, H), BG)
    c = Canvas(img)
    c.panel([0, 0, W, RULE1_Y])
    if kind == "qualified":
        _draw_qualified(c, r)
    else:
        _draw_daily(c, r)
    return img, c.calls


# The ONE file this module is allowed to create inside data/. Must agree with
# the render target in .github/workflows/grade-ledger.yml; selftest_instagram.py
# check 21.8 parses that workflow and asserts the two have not drifted apart.
CARD_DIR = os.path.join("data", "social")
CARD_NAME_RE = re.compile(r"^ig_(\d{4}-\d{2}-\d{2})\.jpg$")


def _assert_safe_out(path):
    """Refuse to write anywhere inside data/ except this module's own card.

    WHY THIS IS AN ALLOWLIST AND NOT A BLANKET BAN. The Phase 1 version of this
    guard rejected everything under data/, with a docstring saying the committed
    pipeline was "Phase 2's problem". Phase 2 then wired grade-ledger.yml to
    render to data/social/ig_<date>.jpg — the exact path the guard forbade — and
    production asked the renderer to create a file it was hard-coded to refuse.
    The render step's `|| ::warning::` correctly kept the ledger safe, so grading
    pushed cleanly and only Instagram failed, with `pending_media` on a file that
    had never been written. Run 34577853134, 2026-09-11.

    The original protection is still worth having: this module must never be
    able to clobber a ledger, a board, a commitment file or a status file. So
    the rule is narrowed rather than dropped —

        outside data/            -> always allowed (temp dirs, previews, tests)
        data/social/ig_<date>.jpg -> allowed, the production card
        anything else under data/ -> refused

    Paths are normalised BEFORE the decision, so `data/social/../ledger.json`
    resolves to data/ledger.json and is refused, and a sibling directory that
    merely shares a prefix (data/social_backup/) does not pass as data/social/.
    The date is parsed as a real calendar date, so ig_2026-13-45.jpg is refused.
    """
    full = os.path.realpath(os.path.abspath(path))
    data_dir = os.path.realpath(os.path.join(ROOT, "data"))

    # Outside data/ entirely: not our business. Previews and the whole test
    # suite live here.
    if full != data_dir and not full.startswith(data_dir + os.sep):
        return

    card_dir = os.path.realpath(os.path.join(ROOT, CARD_DIR))
    name = os.path.basename(full)
    m = CARD_NAME_RE.match(name)
    if os.path.dirname(full) == card_dir and m:
        try:
            datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            m = None
        if m:
            return

    raise ValueError(
        f"refusing to write {full}: the only file this renderer may create "
        f"inside data/ is {CARD_DIR}/ig_<YYYY-MM-DD>.jpg. Everything else "
        f"under data/ is ledger, board, commitment or status data.")


def save_jpeg(img, path):
    """Pinned encoder settings — the difference between a reproducible card and
    a file that changes whenever a default moves. No EXIF and no ICC profile is
    attached: RGB with no profile is interpreted as sRGB, which is what
    Instagram documents it wants, and it keeps the bytes stable."""
    _assert_safe_out(path)
    if img.mode != "RGB":
        raise ValueError(f"expected RGB (sRGB), got {img.mode}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, format="JPEG", quality=88, subsampling=0,
             optimize=True, progressive=False)
    return path


def render(date, out_path):
    """Build the card for `date` straight from the live ledgers."""
    kind, r = ps.select_recap(date)
    if kind is None:
        return None
    img, calls = render_card(kind, r)
    save_jpeg(img, out_path)
    return {"kind": kind, "path": out_path, "calls": calls}


def main():
    if len(sys.argv) < 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    date, out = sys.argv[1], sys.argv[2]
    res = render(date, out)
    if res is None:
        print(f"No settled entries for {date} — nothing to render.")
        return 0
    print(f"[{res['kind']}] {res['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
