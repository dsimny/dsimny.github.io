#!/usr/bin/env python3
"""
Open Ledger Sports — permanent per-game pick pages.

One stable, indexable URL per game on the board:

    /picks/mlb/<date>/<away-slug>-at-<home-slug>/

The SAME page is written twice by the pipeline and enriched in place — no
fragmented preview/result URLs, one permanent evidence trail per game:

  board day    (morning-board.yml)  — only what the product may reveal: the
               free pick in full; leans, scratches and watch picks with their
               public numbers; a HELD play as matchup + time + venue + tier
               ONLY (same redaction as the site and the blog: a projection
               plus a total is most of a pick).
  after grading (grade-ledger.yml)  — the reveal: every pick with its price
               and units, the staked result and P&L from the ledger, closing
               line vs our open (with the capture's own "weak close" caveat
               when it fired late), watch-pick paper grades, and a short
               deterministic postgame note.

Reveal detection is structural, not stateful: a date is "revealed" exactly
when data/board_<date>.json exists in PLAINTEXT (grade.py writes it only
after the fingerprint check passes). While only the .enc exists, held plays
stay redacted no matter who runs this script.

Also maintains picks/mlb/<date>/index.html per slate and the picks/index.html
archive, both rebuilt from the pages on disk. All URLs are root-absolute so
the nesting depth never breaks assets.

Run:  python scripts/game_pages.py [YYYY-MM-DD] [--unrevealed]
      --unrevealed forces board-day redaction regardless of what's on disk
      (used by offline tests; CI never passes it).
"""
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
PICKS_DIR = os.path.join(ROOT, "picks", "mlb")


def slugify(name):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def game_slug(matchup):
    away, home = matchup.split(" @ ")
    return f"{slugify(away)}-at-{slugify(home)}"


def page_shell(title_tag, meta_desc, inner):
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html.escape(title_tag)}</title>
<meta name="description" content="{_html.escape(meta_desc)}">
<link rel="icon" href="/assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="shortcut icon" href="/favicon.ico">
<style>{PAGE_CSS}</style>
</head>
<body>
<header class="site"><div class="wrap sitebar">
  <img class="sitelogo" src="/assets/logo.jpg" width="440" height="440" alt="">
  <div><span class="markname"><span class="open">OPEN LEDGER</span> SPORTS</span>
    <small class="marksub">Pick pages · one permanent URL per game</small></div>
  <nav class="navlinks">
    <a href="/">Today's Board</a>
    <a href="/#ledger">The Ledger</a>
    <a href="/blog/">Blog</a>
    <a href="/picks/">Pick archive</a>
  </nav>
</div></header>
<div class="wrap">
{inner}
<footer class="legal"><p>{LEGAL}</p></footer>
</div>
</body>
</html>'''


def market_line(b):
    if b.get("mkt_odds") is None:
        return "<p class='mut'>No market feed captured for this game's board.</p>"
    a_ml, h_ml = b.get("mkt_away_ml"), b.get("mkt_home_ml")
    tot = b.get("mkt_total")
    bits = []
    if a_ml is not None and h_ml is not None:
        bits.append(f"moneyline {a_ml:+d} / {h_ml:+d} (consensus at board time)")
    if tot is not None:
        bits.append(f"total {tot:g}")
    return f"<p class='mut'>Market at board time: {' · '.join(bits)}.</p>" if bits else ""


def open_vs_close(b, cl):
    """The board-time consensus against the captured close — the page's
    evidence trail. Renders only when both ends exist; carries the capture's
    own caveat when the 'close' was recorded after first pitch."""
    if not cl or b.get("mkt_away_ml") is None:
        return ""
    rows = (f'<tr><td>Moneyline (away / home)</td>'
            f'<td class="num">{b["mkt_away_ml"]:+d} / {b["mkt_home_ml"]:+d}</td>'
            f'<td class="num">{cl["away_ml"]:+d} / {cl["home_ml"]:+d}</td></tr>')
    if b.get("mkt_total") is not None and cl.get("total") is not None:
        rows += (f'<tr><td>Total</td><td class="num">{b["mkt_total"]:g}</td>'
                 f'<td class="num">{cl["total"]:g} (o{cl["over_price"]:+d} / u{cl["under_price"]:+d})</td></tr>')
    note = ""
    if cl.get("note"):
        note = f'<p class="mut">Capture caveat, verbatim: {_html.escape(cl["note"])}.</p>'
    return ('<h2>Open vs close</h2><div class="tablewrap"><table>'
            '<thead><tr><th>Market</th><th>Board time</th><th>Close</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>{note}'
            '<p class="mut">The close is the last pre-first-pitch capture. Movement toward a '
            'side after we post is the fastest honest read on pick quality — see the CLV '
            'column on <a href="/#ledger">the ledger</a>.</p>')


def pick_detail(b, held, revealed):
    """The pick block. A held play before the reveal is matchup-level only."""
    if held and not revealed:
        return ('<h2>The pick</h2>'
                f'<p><strong>Held for members.</strong> Side, price, and sizing went to premium '
                f'members before first pitch. The pick is fingerprinted in the public repository '
                f'(<a href="/#board">commitment on the board</a>) and this page fills in '
                f'completely — side, price, closing comparison, result, units — once the game '
                f'is graded. We hold the position, never the result. '
                f'{len(b.get("checks", []))} circuit-breaker checks were run.</p>')
    a_sp, h_sp = b.get("awaySP"), b.get("homeSP")
    pitchers = ""
    if a_sp and h_sp:
        pitchers = (f'<p>{_html.escape(a_sp["name"])} ({a_sp["era"]:.2f} ERA, '
                    f'{a_sp["whip"]:.2f} WHIP) vs {_html.escape(h_sp["name"])} '
                    f'({h_sp["era"]:.2f} ERA, {h_sp["whip"]:.2f} WHIP).</p>')
    tot = b["mkt_total"] if b.get("mkt_total") is not None else b.get("ref_total")
    edge = (f' · edge {b["edge"]*100:+.1f} pts' if b.get("edge") is not None
            and b.get("mkt_odds") is not None else "")
    if b.get("published"):
        label = "The pick" if not held else "The pick (held on board day, now revealed)"
        stake = f'{b["units"]:g}u'
    else:
        label = "The lean (0 units — logged, not staked)"
        stake = "0u"
    checks = "".join(f"<li>{_html.escape(c)}</li>" for c in b.get("checks", []))
    return (f'<h2>{label}</h2>'
            f'<p><strong>{_html.escape(b["pick"])}</strong> · confidence '
            f'{b["confidence"]*100:.1f}% of {b.get("n_sims", 10000):,} sims{edge} · {stake}</p>'
            + pitchers +
            f'<p class="mut">Projected {b["proj_away"]:g}–{b["proj_home"]:g}; sim total '
            f'{b["mean_total"]:g} against a {tot:g} line; park factor {b["park_factor"]:.2f}.</p>'
            + market_line(b) +
            (f'<details><summary>Circuit-breaker log ({len(b.get("checks", []))} checks)</summary>'
             f'<ul>{checks}</ul></details>' if checks else ""))


def result_block(entry, wl_entries):
    parts = []
    if entry:
        chip = {"WIN": "✅ Win", "LOSS": "❌ Loss", "VOID": "⚪ Void"}
        clv = ""
        if entry.get("close_ml") is not None and entry.get("clv_pts") is not None:
            beat = "beat the close" if entry["clv_pts"] > 0 else (
                "closed worse than our number" if entry["clv_pts"] < 0 else "matched the close")
            clv = (f' The market closed {entry["close_ml"]:+d} on our side — we {beat} '
                   f'({entry["clv_pts"]:+.1f} pts).')
        parts.append(f'<h2>Result</h2>'
                     f'<p><strong>{chip.get(entry["result"], entry["result"])}</strong> · '
                     f'final {_html.escape(str(entry.get("final_score", "—")))} · '
                     f'{entry["pnl"]:+.2f}u on {entry["units"]:g}u staked.{clv} '
                     f'This entry is on <a href="/#ledger">the append-only ledger</a>; '
                     f'it will never be edited.</p>')
    for w in wl_entries:
        if not w.get("result"):
            continue
        chip = {"WIN": "✅", "LOSS": "❌", "PUSH": "➖", "VOID": "⚪"}
        parts.append(f'<p class="mut">{chip.get(w["result"], "•")} Watch pick '
                     f'(<code>{_html.escape(w.get("tag", ""))}</code>, paper, 0u staked): '
                     f'{_html.escape(w["pick"])} — {w["result"]} '
                     f'({w.get("pnl", 0):+.2f}u paper).</p>')
    return "".join(parts)


def postgame_note(b, entry, held):
    if entry:
        if entry["result"] == "WIN":
            return ('<p>The position landed. One game proves nothing — the sample does; '
                    'the ledger keeps counting either way.</p>')
        if entry["result"] == "LOSS":
            return ('<p>The position lost. It stays on the record at full size, next to '
                    'every other one. If the process was wrong the closing-line column '
                    'will say so long before the win-loss column does.</p>')
        return '<p>Voided — no action, nothing risked, nothing claimed.</p>'
    if b.get("published") and held:
        return ""
    return ('<p>No stake rode on this game. The lean and its failed gates above are the '
            'whole position: passing is a position too.</p>')


def render_game(date, b, kind, revealed, closing, ledger_by_pk, wl_by_pk):
    matchup = b["matchup"]
    slug = game_slug(matchup)
    held = bool(b.get("published")) and not b.get("_is_free")
    pk = b.get("gamePk")
    entry = ledger_by_pk.get(pk)
    wl_entries = wl_by_pk.get(pk, [])
    cl = closing.get(str(pk)) if closing else None

    status = ("Graded — this page is the complete record."
              if revealed else "Board day — grades land the next morning and this page fills in.")
    inner_parts = [
        f'<article><span class="kicker">MLB · {nice_date(date)}</span>',
        f'<span class="postdate">{et_time(b["utc"])} · {_html.escape(b.get("venue", ""))}</span>',
        f'<h1>{_html.escape(matchup)}</h1>',
        f'<p class="lede">{status}</p>',
    ]
    if kind == "scratch":
        inner_parts.append(f'<h2>Scratched</h2><p><strong>{_html.escape(b["rule"])}.</strong> '
                           f'{_html.escape(b["reason"])} No simulation was published and nothing '
                           f'was staked.</p>')
    else:
        if b.get("_is_free"):
            inner_parts.append('<p class="mut">★ This was the Free Pick of the Day — public in '
                               'full from board time.</p>')
        inner_parts.append(pick_detail(b, held, revealed))
        for w in b.get("_watch", []):
            div = (f' · divergence {w["divergence"]*100:+.1f} pts'
                   if w.get("divergence") is not None else "")
            inner_parts.append(f'<p class="mut">👁 Watch (<code>{_html.escape(w["tag"])}</code>, '
                               f'0u): {_html.escape(w["pick"])} · model {w["model_p"]*100:.1f}% '
                               f'· edge {w["edge"]*100:+.1f} pts{div}</p>')
        if revealed:
            inner_parts.append(open_vs_close(b, cl))
            inner_parts.append(result_block(entry, wl_entries))
            inner_parts.append(postgame_note(b, entry, held))
    inner_parts.append(f'<p class="backline"><a href="/picks/mlb/{date}/">All games this day</a> '
                       f'· <a href="/picks/">Pick archive</a> · <a href="/blog/{date}.html">'
                       f'The Morning Line for this date</a></p></article>')

    away, home = matchup.split(" @ ")
    title = f"{away} @ {home} — MLB pick and result, {nice_date(date)}"
    desc = (f"Open Ledger Sports on {away} at {home}, {nice_date(date)}: the model's numbers, "
            f"the pick where one published, the closing line, and the graded result — "
            f"one permanent record.")
    out_dir = os.path.join(PICKS_DIR, date, slug)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page_shell(title, desc, "".join(p for p in inner_parts if p)))
    return slug, matchup, kind


def render_date_index(date, games, revealed):
    rows = "".join(
        f'<a class="idxcard" href="{slug}/"><span class="d">{label}</span>'
        f'<span class="t">{_html.escape(matchup)}</span></a>'
        for slug, matchup, label in games)
    state = "graded" if revealed else "board day — pending grades"
    inner = (f'<div class="idx"><span class="kicker">MLB · {nice_date(date)}</span>'
             f'<h1>The slate, game by game ({state})</h1>'
             f'<p class="lede">One permanent page per game: what we published before first '
             f'pitch, and the graded record after. Nothing is edited; pages only gain '
             f'information.</p>{rows}'
             f'<p class="backline"><a href="/picks/">All dates</a> · '
             f'<a href="/blog/{date}.html">The Morning Line for this date</a></p></div>')
    with open(os.path.join(PICKS_DIR, date, "index.html"), "w", encoding="utf-8") as f:
        f.write(page_shell(f"MLB pick pages — {nice_date(date)}",
                           f"Every game on the {nice_date(date)} MLB slate: picks, leans, "
                           f"scratches, and graded results.", inner))


def render_archive_index():
    dates = sorted((d for d in os.listdir(PICKS_DIR)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
                    and os.path.isdir(os.path.join(PICKS_DIR, d))), reverse=True)
    cards = "".join(
        f'<a class="idxcard" href="mlb/{d}/"><span class="d">MLB</span>'
        f'<span class="t">{nice_date(d)}</span>'
        f'<span class="z">{max(len(os.listdir(os.path.join(PICKS_DIR, d))) - 1, 0)} games</span></a>'
        for d in dates)
    inner = (f'<div class="idx"><span class="kicker">Pick archive</span>'
             f'<h1>Every slate, every game, on the record</h1>'
             f'<p class="lede">Permanent pages for every game the engine has boarded. Each page '
             f'is written before first pitch with only what the product may reveal, then '
             f'completed by the overnight grading run — the same append-only discipline as '
             f'<a href="/#ledger">the ledger</a>.</p>{cards or ""}</div>')
    os.makedirs(os.path.join(ROOT, "picks"), exist_ok=True)
    with open(os.path.join(ROOT, "picks", "index.html"), "w", encoding="utf-8") as f:
        f.write(page_shell("MLB pick archive — Open Ledger Sports",
                           "Permanent per-game pick pages: preview, pick, closing line, "
                           "and graded result for every boarded MLB game.", inner))


def pick_free(plays):
    """Must mirror build_site.py / post_discord.py / blog.py (House Rule 2)."""
    if not plays:
        return None
    return next((b for b in reversed(plays) if not b["rule4_flag"] and not b["rule2_pivot"]),
                plays[len(plays) // 2])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date = args[0] if args else datetime.now(ET).strftime("%Y-%m-%d")

    try:
        B = crypto_box.load_dataset(ROOT, "board", date)
    except Exception:
        B = None
    if not B or not (B.get("board") or B.get("scratches")):
        print(f"No board for {date} — no game pages to write.")
        # The archive index still refreshes so a new deploy never links a
        # date that produced nothing.
        if os.path.isdir(PICKS_DIR):
            render_archive_index()
        return

    plain_path, _ = crypto_box.paths_for(ROOT, "board", date)
    revealed = os.path.exists(plain_path) and "--unrevealed" not in sys.argv

    closing = load_json(os.path.join(ROOT, "data", f"closing_{date}.json"), {})
    ledger = load_json(os.path.join(ROOT, "data", "ledger.json"), {"entries": []})
    ledger_by_pk = {e["gamePk"]: e for e in ledger.get("entries", [])
                    if e.get("date") == date}
    wl = load_json(os.path.join(ROOT, "data", "watchlist.json"), {"entries": []})
    wl_by_pk = {}
    for e in wl.get("entries", []):
        if e.get("date") == date:
            wl_by_pk.setdefault(e.get("gamePk"), []).append(e)

    plays = sorted([b for b in B["board"] if b.get("published")], key=lambda b: -b["confidence"])
    free = pick_free(plays)
    watch_by_pk = {}
    for w in (B.get("watch_picks") or []):
        watch_by_pk.setdefault(w.get("gamePk"), []).append(w)

    games = []
    for b in sorted(B["board"], key=lambda x: x["utc"]):
        b = dict(b)
        b["_is_free"] = free is not None and b["gamePk"] == free["gamePk"]
        b["_watch"] = watch_by_pk.get(b["gamePk"], [])
        if b.get("published"):
            label = "★ free pick" if b["_is_free"] else ("pick" if revealed else "held")
        else:
            label = "lean"
        slug, matchup, _ = render_game(date, b, "board", revealed, closing,
                                       ledger_by_pk, wl_by_pk)
        games.append((slug, matchup, label))
    for s in sorted(B.get("scratches", []), key=lambda x: x["utc"]):
        slug, matchup, _ = render_game(date, dict(s), "scratch", revealed, closing,
                                       ledger_by_pk, wl_by_pk)
        games.append((slug, matchup, "scratched"))

    render_date_index(date, games, revealed)
    render_archive_index()
    print(f"Wrote {len(games)} game page(s) for {date} "
          f"({'revealed' if revealed else 'board day'}) + date index + archive index.")


if __name__ == "__main__":
    main()
