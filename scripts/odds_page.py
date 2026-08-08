#!/usr/bin/env python3
"""
Open Ledger Sports — odds-movement page (odds/index.html).

Purely market-observational: reads ONLY data/closing_<date>.json (plus the
plaintext board of an already-revealed date for matchup names on legacy
files). No model numbers, no picks, no board access — so there is nothing on
this page that could leak a held play, and the capture workflow can rebuild
it without the encryption key.

Honesty rules, enforced in copy and code:
  - The page says exactly what each point is: a scheduled capture (a few per
    day), never a live feed. Sparse observations are labeled as such.
  - Movement is DESCRIBED (which side the number moved toward, by how many
    de-vigged points), never explained. We do not know why a line moved and
    we do not guess "sharp action" — the disclaimer says so.
  - A close captured after first pitch carries the capture's own "weak
    close" note verbatim.

Movement per game = the deduped capture history fetch_closing.py appends
(first capture ≈ 11:30 AM ET, ~20 minutes after the board). Legacy files
from before the history shipped hold a single point and render as "one
capture — no movement to show".

Run: python scripts/odds_page.py [YYYY-MM-DD]   (default: today ET, falling
back to the most recent closing_*.json when today has no captures yet)
"""
import glob
import html as _html
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import crypto_box
from blog import PAGE_CSS, LEGAL, nice_date, et_time, load_json

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def implied(ml):
    return (-ml / (-ml + 100)) if ml < 0 else (100 / (ml + 100))


def devig_home(away_ml, home_ml):
    pa, ph = implied(away_ml), implied(home_ml)
    return ph / (pa + ph)


def ml_str(away_ml, home_ml):
    return f"{away_ml:+d} / {home_ml:+d}"


def movement_sentence(first, last, away, home):
    """Descriptive only: direction and de-vigged size. Never a cause."""
    d = (devig_home(last["away_ml"], last["home_ml"])
         - devig_home(first["away_ml"], first["home_ml"])) * 100
    if abs(d) < 0.5:
        return "Essentially unchanged between captures."
    side = home if d > 0 else away
    return (f"Moved toward <strong>{_html.escape(side)}</strong> by "
            f"{abs(d):.1f} de-vigged points between captures.")


def total_sentence(first, last):
    if first.get("total") is None or last.get("total") is None:
        return ""
    if first["total"] == last["total"]:
        return f"Total held at {last['total']:g}."
    arrow = "up" if last["total"] > first["total"] else "down"
    return (f"Total moved {arrow}: {first['total']:g} → {last['total']:g}.")


def pick_date():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        return args[0]
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if os.path.exists(os.path.join(ROOT, "data", f"closing_{today}.json")):
        return today
    dates = sorted(re.search(r"closing_(\d{4}-\d{2}-\d{2})\.json$", f).group(1)
                   for f in glob.glob(os.path.join(ROOT, "data", "closing_*.json")))
    return dates[-1] if dates else today


def board_names(date):
    """Matchup names from an already-REVEALED board, for legacy closing files
    without name fields. An encrypted (unrevealed) board is never read: this
    page must stay buildable without the key, and needs nothing from it."""
    plain, _ = crypto_box.paths_for(ROOT, "board", date)
    if not os.path.exists(plain):
        return {}
    with open(plain, encoding="utf-8") as f:
        B = json.load(f)
    out = {}
    for b in B.get("board", []) + B.get("scratches", []):
        if b.get("gamePk"):
            away, home = b["matchup"].split(" @ ")
            out[str(b["gamePk"])] = (away, home, b.get("utc"))
    return out


def render(date):
    C = load_json(os.path.join(ROOT, "data", f"closing_{date}.json"), {})
    fallback = board_names(date)

    cards = []
    def sort_key(item):
        pk, g = item
        return (g.get("utc") or (fallback.get(pk) or ("", "", ""))[2] or "9999", pk)
    for pk, g in sorted(C.items(), key=sort_key):
        away, home = g.get("away_name"), g.get("home_name")
        utc = g.get("utc")
        if not (away and home) and pk in fallback:
            away, home, utc = fallback[pk]
        title = (f"{_html.escape(away)} <span class='at'>@</span> {_html.escape(home)}"
                 if away and home else f"Game {pk}")
        when = f"{et_time(utc)} first pitch" if utc else "first-pitch time not recorded"

        hist = g.get("history") or [{k: g.get(k) for k in
                                     ("away_ml", "home_ml", "total", "over_price",
                                      "under_price")} | {"captured_utc": g.get("captured_utc")}]
        rows = "".join(
            f'<tr><td>{et_time(p["captured_utc"]) if p.get("captured_utc") else "—"}</td>'
            f'<td class="num">{ml_str(p["away_ml"], p["home_ml"])}</td>'
            f'<td class="num">{f"{p['total']:g}" if p.get("total") is not None else "—"}'
            f'{f" (o{p['over_price']:+d}/u{p['under_price']:+d})" if p.get("over_price") is not None else ""}'
            f'</td></tr>'
            for p in hist)
        if len(hist) >= 2 and away and home:
            summary = f'<p>{movement_sentence(hist[0], hist[-1], away, home)} {total_sentence(hist[0], hist[-1])}</p>'
        elif len(hist) >= 2:
            summary = f'<p>{total_sentence(hist[0], hist[-1])}</p>'
        else:
            summary = ('<p class="mut">One capture on record — no movement to show. '
                       'History accumulates from the day\'s scheduled runs.</p>')
        caveat = (f'<p class="mut">Capture caveat, verbatim: {_html.escape(g["note"])}.</p>'
                  if g.get("note") else "")
        mins = g.get("mins_to_first_pitch")
        closed = (f'<p class="mut">Last pre-pitch capture: '
                  f'{et_time(g["captured_utc"])}'
                  f'{f", {mins} min before first pitch" if isinstance(mins, int) and mins > 0 else ""}.</p>'
                  if g.get("captured_utc") else "")
        cards.append(f'''
<article class="idxcard" style="cursor:default;">
  <span class="d">{when}</span>
  <span class="t">{title}</span>
  <div class="tablewrap"><table>
    <thead><tr><th>Captured (ET)</th><th>Moneyline (away / home)</th><th>Total</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
  {summary}{caveat}{closed}
</article>''')

    body = "".join(cards) if cards else (
        '<p class="mut">No captures on file for this date yet. The first scheduled run of '
        'the day lands around 11:30 AM ET.</p>')
    inner = f'''
<div class="idx">
  <span class="kicker">Line movement</span>
  <span class="postdate">{nice_date(date)}</span>
  <h1>Consensus odds, capture by capture</h1>
  <p class="lede">The median US-book moneyline and total for every game on the slate, recorded a
  few times a day by a scheduled job and frozen at the last capture before each game's first
  pitch — the same closes our <a href="/#ledger">ledger CLV</a> is graded against.</p>
  <p class="mut"><strong>Read this before reading anything into it:</strong> these are a handful
  of scheduled snapshots, not a live feed — a line can move and move back between captures and
  this page would never see it. And movement is a fact, not an explanation: we log which way the
  number went and by how many de-vigged points, but we do not know why it moved and will not
  dress a data point up as "sharp action." Consensus = median across US books at capture time;
  your book's price will differ. This page never contains model output or picks.</p>
  {body}
  <p class="backline"><a href="/">Today's board</a> · <a href="/picks/">Pick archive</a> ·
  <a href="/blog/">The Morning Line</a></p>
</div>'''

    shell = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MLB line movement — {nice_date(date)} — Open Ledger Sports</title>
<meta name="description" content="Consensus MLB moneylines and totals for {nice_date(date)},
captured a few times daily and frozen at each game's last pre-first-pitch line.">
<link rel="icon" href="/assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="shortcut icon" href="/favicon.ico">
<style>{PAGE_CSS}</style>
</head>
<body>
<header class="site"><div class="wrap sitebar">
  <img class="sitelogo" src="/assets/logo.jpg" width="440" height="440" alt="">
  <div><span class="markname"><span class="open">OPEN LEDGER</span> SPORTS</span>
    <small class="marksub">Odds movement · scheduled captures, not a live feed</small></div>
  <nav class="navlinks">
    <a href="/">Today's Board</a>
    <a href="/#ledger">The Ledger</a>
    <a href="/blog/">Blog</a>
    <a href="/picks/">Pick archive</a>
    <a href="/odds/" class="here">Odds</a>
  </nav>
</div></header>
<div class="wrap">
{inner}
<footer class="legal"><p>{LEGAL}</p></footer>
</div>
</body>
</html>'''
    out_dir = os.path.join(ROOT, "odds")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(shell)
    print(f"Wrote odds/index.html for {date}: {len(cards)} game(s).")


if __name__ == "__main__":
    render(pick_date())
