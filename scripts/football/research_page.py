#!/usr/bin/env python3
"""
Open Ledger Sports — football research observation page (Step 4).

Writes:
    football/research/index.html     public research hub

WHAT THIS PAGE IS:
  A transparent record of live market observations generated under the
  fp-v0.4-market-observation-v1 research cohort.  It publishes exactly
  what the model would have chosen each week using the current fp-v0.4
  rule, along with graded results once games settle.

WHAT THIS PAGE IS NOT:
  - A picks service.  Every observation is 0 units.
  - An official record.  Research rows never contribute to official W-L,
    CLV, or ROI.  They cannot be retroactively promoted.
  - A recommendation.  Nothing here is betting advice.

SEPARATION GUARANTEES (mirrors grade_football.py --research):
  - never reads football_ledger.json
  - never reads official board files (board_*.enc / board_*.json)
  - never reads commitments.json or game_commitments.json
  - reads only data/football/research/ and
    data/football/research_preregistrations.json

Empty-state: renders cleanly when no research directory, no board files,
and no research_ledger.json exist.  The page says so rather than erroring.

Run:
  python scripts/football/research_page.py
  python scripts/football/research_page.py --dry-run   # print, no write
"""
import argparse
import glob
import html
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
from blog import PAGE_CSS, LEGAL, nice_date          # noqa: E402

ROOT = os.path.join(HERE, "..", "..")
FB   = os.path.join(ROOT, "data", "football")
RESEARCH_DATA_DIR = os.path.join(FB, "research")
RESEARCH_LEDGER   = os.path.join(RESEARCH_DATA_DIR, "research_ledger.json")
PREREG_PATH       = os.path.join(FB, "research_preregistrations.json")
RESEARCH_VERSION_ID = "fp-v0.4-market-observation-v1"

OUT = os.path.join(ROOT, "football", "research")

E = html.escape

# ---------------------------------------------------------------------------
# Disclaimer block — must appear near the top of the page on every render.
# Every sentence here is a factual statement from the preregistration.
# ---------------------------------------------------------------------------
RESEARCH_DISCLAIMER = (
    "<strong>🔬 Research Observation — 0 units — Not a recommendation.</strong> "
    "This page records live market observations produced under the "
    f"<code>{E(RESEARCH_VERSION_ID)}</code> research cohort. "
    "These observations are <strong>not official Open Ledger picks</strong>, "
    "are not part of the official football record, "
    "and are not eligible for retroactive promotion into the official record. "
    "No edge claim is made. "
    "All observations are 0 units; any hypothetical P/L shown is research "
    "analysis only and does not represent money risked."
)


# ---------------------------------------------------------------------------
# Data loaders — all fail-open (return empty state, never raise)
# ---------------------------------------------------------------------------

def load_prereg():
    """Load the registered research cohort entry, or None."""
    try:
        with io.open(PREREG_PATH, encoding="utf-8") as f:
            registry = json.load(f)
        return next((r for r in registry.get("registrations", [])
                     if r.get("id") == RESEARCH_VERSION_ID), None)
    except (OSError, ValueError, KeyError):
        return None


def load_research_ledger():
    """Load research_ledger.json.

    Absent file → valid empty state (nothing published yet).
    Corrupt/malformed JSON → raises ValueError so generation fails loudly
    rather than silently treating bad data as if no data exists.
    """
    try:
        with io.open(RESEARCH_LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"research_version_id": RESEARCH_VERSION_ID, "entries": []}
    # OSError other than FileNotFoundError (permissions, etc.) propagates —
    # that is also a "something is wrong" condition, not an empty state.
    # ValueError from json.load propagates — a corrupt ledger must not be
    # silently treated as an empty one.


def load_research_boards():
    """Return sorted list of (week, board_dict) for existing research boards.

    No research directory / no matching files → valid empty state.
    Corrupt/malformed board JSON → raises ValueError so generation fails
    loudly rather than skipping a board that contains bad data.
    """
    if not os.path.isdir(RESEARCH_DATA_DIR):
        return []
    pattern = os.path.join(RESEARCH_DATA_DIR,
                           f"board_*_{RESEARCH_VERSION_ID}.json")
    boards = []
    for path in sorted(glob.glob(pattern)):
        with io.open(path, encoding="utf-8") as f:
            b = json.load(f)   # ValueError propagates — corrupt board is not skipped
        week = b.get("slate_week", "unknown")
        boards.append((week, b))
    return boards


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def money(p):
    if p is None:
        return "—"
    return f"{int(p):+d}" if isinstance(p, (int, float)) else E(str(p))


def chip(result):
    return {"win": "✅", "loss": "❌", "push": "⚪"}.get(
        str(result).lower(), "•")


def render_prereg_section(prereg):
    if prereg is None:
        return (
            '<p class="mut">⚠️ Research version not found in registry. '
            'No observations will be generated until the preregistration is registered.</p>'
        )
    doc_path = prereg.get("path", "")
    doc_link = (f'<a href="/{E(doc_path)}">{E(doc_path)}</a>'
                if doc_path else "<em>not recorded</em>")
    first = prereg.get("first_commit") or "<em>not yet recorded</em>"
    return f"""
  <div class="commit">
    <p class="commitlead">Research cohort registration</p>
    <p class="commithash">
      <code>{E(RESEARCH_VERSION_ID)}</code><br>
      Frozen: {E(prereg.get('frozen_date', '—'))} &nbsp;·&nbsp;
      Selection rule: <code>{E(prereg.get('selection_rule', '—'))}</code> &nbsp;·&nbsp;
      Units: {E(str(prereg.get('units', 0)))}
    </p>
    <p class="commitsub">
      Pre-observation document: {doc_link}<br>
      First commit: <code>{E(str(first))}</code><br>
      Authority: <code>{E(prereg.get('authority', '—'))}</code>
    </p>
  </div>"""


def render_results_table(entries):
    """Table of graded research observations from research_ledger.json."""
    if not entries:
        return ""
    rows_html = ""
    for e in entries:
        res = str(e.get("result", "")).lower()
        pnl = e.get("pnl_per_unit")
        pnl_str = (f"{pnl:+.4f}u" if isinstance(pnl, (int, float)) else "—")
        clv = e.get("clv_pts")
        clv_str = f"{clv:+.2f} pts" if isinstance(clv, (int, float)) else "—"
        rows_html += (
            f"<tr>"
            f"<td>{E(e.get('slate_week', ''))}</td>"
            f"<td>{E(e.get('sport', '').upper())}</td>"
            f"<td>{E(e.get('matchup', ''))}</td>"
            f"<td>{E(e.get('side', ''))}</td>"
            f"<td class=\"num\">{money(e.get('best_price'))}</td>"
            f"<td>{chip(res)} {E(res)}</td>"
            f"<td class=\"num\">{E(pnl_str)} <span class=\"mut\">(hypothetical)</span></td>"
            f"<td class=\"num\">{E(clv_str)}</td>"
            f"</tr>"
        )
    return f"""
  <div class="tablewrap"><table>
    <thead><tr>
      <th>Week</th><th>Sport</th><th>Game</th><th>Observed side</th>
      <th>Price</th><th>Result</th>
      <th>Hypothetical 1u return</th><th>CLV</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table></div>"""


def render_aggregates(entries):
    """W/L/P counts and hypothetical total. Only if entries exist."""
    if not entries:
        return ""
    wins   = sum(1 for e in entries if str(e.get("result","")).lower() == "win")
    losses = sum(1 for e in entries if str(e.get("result","")).lower() == "loss")
    pushes = sum(1 for e in entries if str(e.get("result","")).lower() == "push")
    total  = sum(e.get("pnl_per_unit", 0) or 0 for e in entries)
    rec    = f"{wins}–{losses}" + (f"–{pushes}p" if pushes else "")
    clv_e  = [e["clv_pts"] for e in entries if isinstance(e.get("clv_pts"), (int, float))]
    clv_str = (f"{sum(clv_e)/len(clv_e):+.2f} pts avg"
               if clv_e else "not yet available")
    return f"""
  <p class="mut"><strong>Research summary ({len(entries)} graded):</strong>
  {E(rec)} &nbsp;·&nbsp;
  Hypothetical 1u return: <strong>{total:+.4f}u</strong>
  <em>(research analysis only — 0 units staked)</em> &nbsp;·&nbsp;
  Avg CLV: {E(clv_str)}</p>"""


def render_boards_section(boards, ledger_entries):
    """Cards for each research board on disk."""
    if not boards:
        return '<p class="mut">No live research observations have been published yet.</p>'

    graded_by_week = {}
    for e in ledger_entries:
        graded_by_week.setdefault(e.get("slate_week", ""), []).append(e)

    parts = []
    for week, b in boards:
        prem = b.get("premium")
        free = b.get("free")
        graded = graded_by_week.get(week, [])
        graded_note = (
            f'<p class="mut">{len(graded)} observation(s) graded this week.</p>'
            if graded else
            '<p class="mut">Not yet graded — game may not have finished.</p>'
        )
        selections = []
        for tier_key, label in (("premium", "Rank 1"), ("free", "Rank 2")):
            g = b.get(tier_key)
            if not g:
                continue
            selections.append(
                f"<tr><th>{E(label)}</th>"
                f"<td>{E(g.get('matchup',''))} · {E(g.get('kickoff_utc',''))}</td>"
                f"<td>{E(g.get('side',''))}</td>"
                f"<td class=\"num\">{money(g.get('best_price'))}"
                f" at {E(g.get('best_book',''))}</td>"
                f"<td class=\"num\">{g.get('eff_overround_pts','—')} pts</td>"
                f"</tr>"
            )
        sel_html = (
            f'<div class="tablewrap"><table><thead><tr>'
            f'<th>Tier</th><th>Game</th><th>Observed side</th>'
            f'<th>Price</th><th>Eff. overround</th>'
            f'</tr></thead><tbody>{"".join(selections)}</tbody></table></div>'
            if selections else
            '<p class="mut">No qualifying observation this week.</p>'
        )
        parts.append(f"""
  <article class="card">
    <span class="t">🔬 Research · week of {E(nice_date(week))}</span>
    <p class="mut">0 units · Not a recommendation · Not part of the official record</p>
    {sel_html}
    {graded_note}
  </article>""")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(dry_run=False):
    prereg  = load_prereg()
    rl      = load_research_ledger()
    boards  = load_research_boards()
    entries = rl.get("entries", [])

    prereg_section = render_prereg_section(prereg)
    agg_section    = render_aggregates(entries)
    results_table  = render_results_table(entries)
    boards_section = render_boards_section(boards, entries)

    inner = f"""
<div class="idx">
  <span class="kicker">Football · Research</span>
  <h1>Live model research observations</h1>
  <div class="commit">
    <p class="commitlead">Research disclaimer</p>
    <p class="commitsub">{RESEARCH_DISCLAIMER}</p>
  </div>

  {prereg_section}

  <h2>Current research observations</h2>
  <p class="mut">Each observation records what the fp-v0.4 market selection rule
  would have chosen in live conditions. Observations are published before kickoff;
  results are added after games settle. Nothing here is a bet recommendation.
  The official football record is at
  <a href="/football/">openledgersports.com/football</a>.</p>
  {boards_section}

  <h2>Graded results</h2>
  <p class="mut">Research results only. Not included in official W–L, CLV, or ROI.
  Hypothetical returns are shown for research analysis — 0 units were staked.</p>
  {agg_section if entries else '<p class="mut">No graded results yet.</p>'}
  {results_table if entries else ''}

  <p class="backline">
    <a href="/football/">Official football record</a> ·
    <a href="/football/mercer/">D.J. Mercer Spotlight</a> ·
    <a href="/">Today's board</a>
  </p>
</div>"""

    title = "Football research observations — Open Ledger Sports"
    desc  = ("Live market research observations using the fp-v0.4 selection rule. "
             "0 units. Not official picks. Not recommendations.")

    if dry_run:
        print(f"[dry-run] would write football/research/index.html")
        print(f"  prereg found: {prereg is not None}")
        print(f"  boards: {len(boards)}")
        print(f"  graded entries: {len(entries)}")
        return

    _write(OUT, inner, title, desc)
    print(f"wrote football/research/index.html "
          f"({len(boards)} boards, {len(entries)} graded)")


def _write(dirpath, inner, title, desc):
    """Write the standard OLS HTML shell, reusing page.py's nav pattern."""
    shell = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{E(title)}</title>
<meta name="description" content="{E(desc)}">
<link rel="icon" href="/assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="shortcut icon" href="/favicon.ico">
<style>{PAGE_CSS}
  ul.plain {{ list-style:none; padding-left:0; }}
  ul.plain li {{ padding:3px 0; }}
</style>
</head>
<body>
<header class="site"><div class="wrap sitebar">
  <img class="sitelogo" src="/assets/branding/ols-horizontal-on-dark-transparent.svg"
       width="1200" height="200"
       style="width:300px;max-width:100%;height:auto;border-radius:0"
       alt="Open Ledger Sports">
  <div>
    <small class="marksub">Football · research observations</small></div>
  <nav class="navlinks">
    <a href="/">Today's Board</a>
    <a href="/#ledger">The Ledger</a>
    <a href="/football/">Football</a>
    <a href="/football/research/" class="here">Research</a>
    <a href="/football/mercer/">D.J. Mercer</a>
    <a href="/blog/">Blog</a>
    <a href="/odds/">Odds</a>
  </nav>
</div></header>
<div class="wrap">
{inner}
<footer class="legal"><p>{LEGAL}</p></footer>
</div>
</body>
</html>"""
    os.makedirs(dirpath, exist_ok=True)
    with io.open(os.path.join(dirpath, "index.html"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(shell)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written; write nothing")
    args = ap.parse_args()
    render(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
