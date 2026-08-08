#!/usr/bin/env python3
"""
Open Ledger Sports — The Morning Line (daily blog generator).

One post per day, fully deterministic, written by composing the day's REAL
numbers — never by a language model at run time and never by hand:

  game days  — the slate in review: yesterday's graded ledger, today's games
               with pitchers and totals, where the model and the market argue,
               and the watch list. Built from the same board build_site.py
               renders, so it can never say something the site doesn't.
  off days   — the next unused piece from the evergreen bettor-education
               library (scripts/blog_evergreen.py): CLV, de-vigging, Kelly,
               park factors, variance… written once, reviewed, committed.

PUBLIC-DATA RULE (House Rule 7 applies here too): a held play appears only as
matchup + time. No side, price, total, projection, or probability for held
plays — between them they give the pick away. The free pick, leans, scratches,
and watch picks are already public on the site and appear in full.

Rendered articles persist in data/blog_items.json (append-only by date, same
pattern as feed_items.json), so every page — including old ones — regenerates
from the store alone, no board files needed. That is what makes --rebuild-only
safe for the grading and rebuild workflows, which run while today's board is
still encrypted.

Run:  python scripts/blog.py [YYYY-MM-DD]       write today's post + all pages
      python scripts/blog.py --rebuild-only     re-render pages from the store
                                                (adds nothing; any workflow may
                                                run it at any hour)
Idempotent by date: an existing item is never rewritten, so a second morning
run re-renders identical pages and adds nothing.
"""
import html as _html
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import crypto_box
import blog_evergreen

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BLOG_DIR = os.path.join(ROOT, "blog")
STORE_PATH = os.path.join(ROOT, "data", "blog_items.json")
KEEP = 120   # ~four months of dailies; pages already written stay on disk

SITE = (os.environ.get("SITE_URL", "").strip() or "https://openledgersports.com").rstrip("/")

LEGAL = ("Open Ledger Sports is an analytics publication, not a sportsbook. We accept "
         "no wagers. Nothing here is betting advice, and no outcome is guaranteed. "
         "21+ only. If you or someone you know has a gambling problem, help is "
         "available: call or text 1-800-GAMBLER, or see the "
         '<a href="https://www.ncpgambling.org/responsible-gambling/safer-sports-betting/" '
         'rel="noopener">NCPG\'s safer sports betting resources</a>.')


def _rfc822(iso_utc):
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.now(timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def nice_date(date):
    d = datetime.strptime(date, "%Y-%m-%d")
    return f"{d:%A, %B} {d.day}, {d.year}"


def et_time(utc_str):
    t = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
    return f"{t.hour % 12 or 12}:{t:%M %p} ET"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def pick_free(plays):
    """Must mirror build_site.py / post_discord.py (House Rule 2)."""
    if not plays:
        return None
    return next((b for b in reversed(plays) if not b["rule4_flag"] and not b["rule2_pivot"]),
                plays[len(plays) // 2])


# ---------------- game-day article ----------------

def yesterday_section(date):
    """Graded results for the previous day, from the public ledger. Everything
    here is post-reveal and fully public."""
    yday = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    ledger = load_json(os.path.join(ROOT, "data", "ledger.json"), {"entries": [], "aggregates": None})
    entries = [e for e in ledger.get("entries", []) if e.get("date") == yday]
    agg = ledger.get("aggregates")
    running = ""
    if agg:
        roi = f" · ROI {agg['roi_pct']:+.1f}%" if agg.get("roi_pct") is not None else ""
        running = (f'<p class="mut">Running ledger: <strong>{agg["record"]}</strong> · '
                   f'{agg["units_net"]:+.2f}u{roi}, every entry public and append-only.</p>')

    wl = load_json(os.path.join(ROOT, "data", "watchlist.json"), {"entries": []})
    wl_y = [e for e in wl.get("entries", []) if e.get("date") == yday and e.get("result")]
    wl_line = ""
    if wl_y:
        w = sum(1 for e in wl_y if e["result"] == "WIN")
        l = sum(1 for e in wl_y if e["result"] == "LOSS")
        p = len(wl_y) - w - l
        wl_line = (f'<p class="mut">The watch list — tracked, not staked — went '
                   f'{w}-{l}{f"-{p}" if p else ""} on paper. That record accumulates toward '
                   f'the promotion criteria; it never touches the staked ledger.</p>')

    if not entries:
        return (f'<h2>Yesterday on the ledger</h2>'
                f'<p>No staked plays were graded for {nice_date(yday)} — the gates published '
                f'nothing, so nothing rode. Passing is a position too, and it grades itself.</p>'
                + wl_line + running)

    chip = {"WIN": "✅", "LOSS": "❌", "VOID": "⚪"}
    rows = "".join(
        f'<li>{chip.get(e["result"], "•")} {_html.escape(e["pick"])} — '
        f'{_html.escape(str(e.get("final_score", "void")))} ({e["pnl"]:+.2f}u)</li>'
        for e in entries)
    day_pnl = sum(e["pnl"] for e in entries)
    return (f'<h2>Yesterday on the ledger</h2>'
            f'<p>{nice_date(yday)} settled at <strong>{day_pnl:+.2f}u</strong>:</p>'
            f'<ul>{rows}</ul>' + wl_line + running)


def slate_table(board_entries, scratches, free):
    """Every game today, one row each. Held plays are matchup + time ONLY."""
    rows = []
    for b in sorted(board_entries, key=lambda x: x["utc"]):
        held = b.get("published") and b is not free
        matchup = _html.escape(b["matchup"])
        if held:
            rows.append(f'<tr><td>{et_time(b["utc"])}</td><td>{matchup}</td>'
                        f'<td colspan="3"><em>Held for members — publishes in full '
                        f'after grading</em></td></tr>')
            continue
        a_sp, h_sp = b["awaySP"], b["homeSP"]
        pitchers = (f'{_html.escape(a_sp["name"])} ({a_sp["era"]:.2f}) vs '
                    f'{_html.escape(h_sp["name"])} ({h_sp["era"]:.2f})')
        tot = b["mkt_total"] if b["mkt_total"] is not None else b["ref_total"]
        star = " ★" if (free is not None and b is free) else ""
        rows.append(f'<tr><td>{et_time(b["utc"])}</td><td>{matchup}{star}</td>'
                    f'<td>{pitchers}</td><td class="num">{b["proj_away"]:g}–{b["proj_home"]:g}</td>'
                    f'<td class="num">{b["mean_total"]:g} / {tot:g}</td></tr>')
    for s in sorted(scratches, key=lambda x: x["utc"]):
        rows.append(f'<tr><td>{et_time(s["utc"])}</td><td>{_html.escape(s["matchup"])}</td>'
                    f'<td colspan="3"><em>Scratched — {_html.escape(s["rule"])}: '
                    f'{_html.escape(s["reason"])}</em></td></tr>')
    return ('<div class="tablewrap"><table><thead><tr><th>First pitch</th><th>Game</th>'
            '<th>Starters (ERA)</th><th>Projected score</th><th>Sim total / line</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="mut">Projections are the engine\'s 10,000-simulation averages, already '
            'blended toward the de-vigged market. ★ marks the free pick. Held plays show no '
            'numbers on purpose: a projection plus a total is most of a pick.</p>')


def angles_section(board_entries, free):
    """Three deterministic talking points, scored and picked from today's
    PUBLIC games (held plays are excluded from every candidate). At most one
    per category, so a slate of lopsided pitching duels still yields a mix
    instead of three copies of the same observation."""
    public = [b for b in board_entries if not b.get("published") or b is free]
    cands = []   # (score, category, html)
    for b in public:
        a_sp, h_sp = b["awaySP"], b["homeSP"]
        matchup = _html.escape(b["matchup"])
        era_gap = abs(a_sp["era"] - h_sp["era"])
        if era_gap >= 0.9:
            better, worse = (a_sp, h_sp) if a_sp["era"] < h_sp["era"] else (h_sp, a_sp)
            cands.append((era_gap * 1.0, "mound", f'<li><strong>The mound mismatch: {matchup}.</strong> '
                          f'{_html.escape(better["name"])} ({better["era"]:.2f} ERA) against '
                          f'{_html.escape(worse["name"])} ({worse["era"]:.2f}) is a '
                          f'{era_gap:.1f}-run ERA gap. The engine stabilizes both with FIP and '
                          f'innings before believing it — a raw ERA gap this wide usually '
                          f'shrinks under the hood, and the market has already priced most of '
                          f'what survives.</li>'))
        pf = b["park_factor"]
        if abs(pf - 1.0) >= 0.08:
            kind = "hitter-friendly" if pf > 1 else "run-suppressing"
            cands.append((abs(pf - 1.0) * 8, "park", f'<li><strong>The park: {matchup}.</strong> '
                          f'{_html.escape(b["venue"])} carries a {pf:.2f} park factor — solidly '
                          f'{kind}. Known information, so it\'s in the posted total already; '
                          f'the question is whether this matchup\'s pitchers move the number '
                          f'off it. Our sim says {b["mean_total"]:g} runs.</li>'))
        if b.get("divergence") is not None and abs(b["divergence"]) >= 0.06:
            cands.append((abs(b["divergence"]) * 10, "divergence", f'<li><strong>The argument: {matchup}.</strong> '
                          f'Raw model and de-vigged market disagree by '
                          f'{abs(b["divergence"])*100:.1f} points here. Our standing rule: past '
                          f'12 points the market is assumed to know something we don\'t, and '
                          f'the game is a hold, not a bigger bet. Under it, a disagreement is '
                          f'just that — logged on the card, sized by the gates.</li>'))
        tot = b["mkt_total"] if b["mkt_total"] is not None else b["ref_total"]
        if tot is not None and abs(b["mean_total"] - tot) >= 0.7:
            side = "over" if b["mean_total"] > tot else "under"
            cands.append((abs(b["mean_total"] - tot) * 1.2, "total", f'<li><strong>The total: {matchup}.</strong> '
                          f'The sim lands on {b["mean_total"]:g} runs against a {tot:g} line — '
                          f'{abs(b["mean_total"] - tot):.1f} toward the {side}. Totals remain in '
                          f'their paper proving period on the watch list: computed, published, '
                          f'graded at 0 units until the record earns promotion.</li>'))
        if b.get("rule6_flag"):
            cands.append((1.5, "rule6", f'<li><strong>The cold bats: {matchup}.</strong> Rule 6 fired — '
                          f'the road offense\'s trailing-14-day wOBA sits far enough under '
                          f'league that the engine taxes its scoring 8%, a size set by a '
                          f'full-season backtest, printed on the card.</li>'))
    best_per_cat = {}
    for score, cat, html_frag in cands:
        if cat not in best_per_cat or score > best_per_cat[cat][0]:
            best_per_cat[cat] = (score, html_frag)
    top = [frag for _, frag in sorted(best_per_cat.values(), key=lambda c: -c[0])[:3]]
    if not top:
        return ""
    return ('<h2>Three angles a bettor would actually check</h2>'
            f'<ul>{"".join(top)}</ul>')


def watch_section(watch):
    if not watch:
        return ""
    rows = "".join(
        f'<li><code>{_html.escape(w["tag"])}</code> <strong>{_html.escape(w["matchup"])}</strong> — '
        f'{_html.escape(w["pick"])} · model {w["model_p"]*100:.1f}% · edge {w["edge"]*100:+.1f} pts</li>'
        for w in watch)
    return ('<h2>The watch list: tracked, not staked</h2>'
            f'<ul>{rows}</ul>'
            '<p class="mut">Watch picks clear some but not all gates, or belong to a market '
            'still proving itself on paper. Each is graded like a real pick at 0 units, on a '
            'record that never mixes with the staked ledger. When a segment reaches 100 graded '
            'picks with non-negative units and calibration inside 4 points, it gets promoted — '
            'publicly, as a version bump. Until then it is exactly this: watched.</p>')


def build_slate_article(date, B):
    board = B["board"]
    scratches = B.get("scratches", [])
    watch = B.get("watch_picks") or []
    plays = sorted([b for b in board if b.get("published")], key=lambda b: -b["confidence"])
    free = pick_free(plays)
    n_slate = B.get("n_slate", len(board) + len(scratches))
    units = B.get("published_units", 0)

    if plays:
        plays_clause = (f'{len(plays)} cleared the gates for '
                        f'{units:g}u of exposure')
    else:
        plays_clause = 'nothing cleared the gates — no manufactured pick, no exceptions'
    lead = (f'<p class="lede">{n_slate} games on the slate, {len(board)} simulated 10,000 times '
            f'each, and {plays_clause}. '
            f'{f"{len(scratches)} scratched by rule. " if scratches else ""}'
            f'{f"{len(watch)} on the watch list at 0 units. " if watch else ""}'
            f'The full board, with every circuit-breaker check printed, is '
            f'<a href="{SITE}/#board">on the site</a>.</p>')

    free_block = ""
    if free is not None:
        edge_txt = (f' · {free["edge"]*100:+.1f} pts vs the {free["mkt_odds"]:+d} price'
                    if free.get("mkt_odds") is not None else "")
        free_block = (f'<h2>The free pick, in full</h2>'
                      f'<p><strong>{_html.escape(free["pick"])}</strong> — '
                      f'{_html.escape(free["matchup"])}, {et_time(free["utc"])}. '
                      f'{free["confidence"]*100:.1f}% of 10,000 sims{edge_txt}, '
                      f'{free["units"]:g}u. Complete analysis and the full breaker log are '
                      f'<a href="{SITE}/#free">on the free-pick page</a>.</p>')
    elif any(b.get("best_of_board") for b in board):
        bob = next(b for b in board if b.get("best_of_board"))
        free_block = (f'<h2>No qualifying plays — and the best of what remained</h2>'
                      f'<p>Today\'s ✳ Best of Board is <strong>{_html.escape(bob["pick"])}</strong> '
                      f'({_html.escape(bob["matchup"])}) at 0 units: the model\'s top remaining '
                      f'choice, published with every gate it failed. We show it so you can see '
                      f'the reasoning; we don\'t stake it, and it never enters the ledger.</p>')

    parts = [lead,
             weekly_audit_section(date),
             yesterday_section(date),
             '<h2>Today\'s slate, game by game</h2>',
             slate_table(board, scratches, free),
             free_block,
             angles_section(board, free),
             watch_section(watch)]
    body = "".join(p for p in parts if p)

    if plays:
        summary = f'{n_slate} games, {len(plays)} play{"s" if len(plays) != 1 else ""} published'
    else:
        summary = f'{n_slate} games, no qualifying plays'
    if watch:
        summary += f', {len(watch)} on watch'
    title = f'The slate for {nice_date(date)}: {summary}'
    teaser = (f'{summary.capitalize()}. Yesterday\'s grades, every matchup with starters and '
              f'sim totals, and the angles worth checking — all from the engine\'s own numbers.')
    return title, teaser, body


def weekly_audit_section(date):
    """'What we learned' — Mondays only, auditing the previous Mon–Sun.

    Composed entirely from graded, public records (ledger, watchlist, revealed
    boards), so it can only ever discuss picks that already published in full.
    A board still encrypted (or unreadable locally without the key) is simply
    skipped — the audit reports what the public record holds, nothing more."""
    d = datetime.strptime(date, "%Y-%m-%d")
    if d.weekday() != 0:
        return ""
    days = [(d - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7, 0, -1)]
    start_nice, end_nice = nice_date(days[0]), nice_date(days[-1])

    n_slates = n_sim = n_plays = n_watch = n_scr = 0
    units = 0.0
    for day in days:
        try:
            B = crypto_box.load_dataset(ROOT, "board", day)
        except Exception:
            B = None   # still-encrypted board on a keyless machine: not public yet
        if not B:
            continue
        n_slates += 1
        n_sim += len(B.get("board", []))
        n_scr += len(B.get("scratches", []))
        n_plays += sum(1 for b in B["board"] if b.get("published"))
        units += B.get("published_units", 0) or 0
        n_watch += len(B.get("watch_picks") or [])

    ledger = load_json(os.path.join(ROOT, "data", "ledger.json"), {"entries": []})
    week = [e for e in ledger.get("entries", []) if days[0] <= e.get("date", "") <= days[-1]]
    wl = load_json(os.path.join(ROOT, "data", "watchlist.json"), {"entries": []})
    wl_week = [e for e in wl.get("entries", []) if days[0] <= e.get("date", "") <= days[-1]
               and e.get("result")]

    parts = [f'<h2>What we learned: {start_nice} – {end_nice}</h2>']
    if n_slates:
        parts.append(
            f'<p>The engine ran {n_slates} slate{"s" if n_slates != 1 else ""}: {n_sim} games '
            f'simulated, <strong>{n_plays} play{"s" if n_plays != 1 else ""} published</strong> '
            f'({units:g}u risked), {n_watch} tracked on the watch list at 0 units, '
            f'{n_scr} scratched by rule. Every no-play day was a decision, not an outage: '
            f'nothing cleared the gates at an allocatable price, and the leans that failed '
            f'are on each day\'s board with reasons.</p>')

    if week:
        w = sum(1 for e in week if e["result"] == "WIN")
        l = sum(1 for e in week if e["result"] == "LOSS")
        net = sum(e["pnl"] for e in week)
        chip = {"WIN": "✅", "LOSS": "❌", "VOID": "⚪"}
        rows = "".join(f'<li>{chip.get(e["result"], "•")} {e["date"]} · '
                       f'{_html.escape(e["pick"])} ({e["pnl"]:+.2f}u)</li>' for e in week)
        parts.append(f'<p>The staked ledger graded {len(week)} pick'
                     f'{"s" if len(week) != 1 else ""}: <strong>{w}-{l}, {net:+.2f}u</strong>.</p>'
                     f'<ul>{rows}</ul>')
        clv_week = [e for e in week if e.get("clv_pts") is not None]
        if clv_week:
            avg = sum(e["clv_pts"] for e in clv_week) / len(clv_week)
            beat = sum(1 for e in clv_week if e["clv_pts"] > 0)
            parts.append(f'<p>Closing line value on the week: <strong>{avg:+.2f} pts</strong> '
                         f'average across {len(clv_week)} picks with a captured close; we beat '
                         f'the close on {beat} of {len(clv_week)}. CLV settles faster than '
                         f'win-loss ever can — it\'s the number to watch here.</p>')
    else:
        tail = (' The paper records below are where a quiet week still teaches.' if wl_week
                else ' A quiet week is still a reading: the gates are the product, and they held.')
        parts.append(f'<p>No staked picks were graded this week — the gates held everything '
                     f'back.{tail}</p>')

    if wl_week:
        by_tag = {}
        for e in wl_week:
            by_tag.setdefault(e.get("tag", "?"), []).append(e)
        rows = []
        for tag in sorted(by_tag):
            g = by_tag[tag]
            w = sum(1 for e in g if e["result"] == "WIN")
            l = sum(1 for e in g if e["result"] == "LOSS")
            p = sum(1 for e in g if e["result"] == "PUSH")
            net = sum(e.get("pnl", 0) for e in g)
            rows.append(f'<li><code>{_html.escape(tag)}</code> {w}-{l}'
                        f'{f"-{p}p" if p else ""} · {net:+.2f}u paper</li>')
        parts.append('<p>The watch list on paper this week (flat 1u, 0 staked, never mixed '
                     f'into the ledger):</p><ul>{"".join(rows)}</ul>'
                     '<p class="mut">These records accumulate toward the public promotion '
                     'criteria — 100 graded picks, non-negative units, calibration inside '
                     '4 points — and nothing gets staked before then.</p>')

    if len(parts) == 1:
        return ""
    return "".join(parts)


# ---------------- evergreen ----------------

def build_evergreen_article(date, store):
    used = sum(1 for it in store["items"] if it.get("kind") == "evergreen")
    post = blog_evergreen.POSTS[used % len(blog_evergreen.POSTS)]
    intro = ('<p class="lede">No MLB slate today, so no board and no picks — we don\'t '
             'manufacture one for either. Off days here go to the bettor\'s bookshelf: '
             'one piece on the mechanics of this business, written once and published '
             'when the schedule goes quiet.</p>')
    # A Monday off day still audits the week — the recap belongs to the date,
    # not to whether baseball is played on it.
    return post["title"], post["teaser"], intro + post["body"] + weekly_audit_section(date)


# ---------------- pages ----------------

PAGE_CSS = """
  :root { color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --surface2:#222220;
    --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --ring:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926; --good:#0ca30c; --crit:#d03b3b; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--page); color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.6; }
  a { color:var(--s1); }
  .wrap { max-width:780px; margin:0 auto; padding:0 20px; }
  header.site { position:sticky; top:0; z-index:10; background:rgba(13,13,13,0.92); backdrop-filter:blur(8px); border-bottom:1px solid var(--grid); }
  .sitebar { display:flex; align-items:center; gap:14px; padding:12px 0; flex-wrap:wrap; }
  .sitelogo { width:38px; height:38px; border-radius:50%; }
  .markname { font-weight:800; letter-spacing:0.04em; font-size:0.98rem; }
  .markname .open { color:var(--s1); }
  .marksub { display:block; font-weight:500; font-size:0.6rem; letter-spacing:0.14em; color:var(--muted); text-transform:uppercase; }
  .navlinks { margin-left:auto; display:flex; gap:14px; font-size:0.84rem; }
  .navlinks a { color:var(--ink2); text-decoration:none; padding:6px 10px; border-radius:8px; }
  .navlinks a:hover { background:var(--surface2); }
  .navlinks a.here { background:var(--surface2); color:var(--ink); font-weight:650; }
  article { padding:26px 0 30px; }
  .kicker { display:inline-block; font-size:0.68rem; font-weight:800; letter-spacing:0.12em; text-transform:uppercase; color:var(--good); border:1px solid var(--ring); background:var(--surface); padding:5px 12px; border-radius:99px; margin-bottom:10px; }
  .postdate { display:block; font-size:0.72rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:var(--ink2); margin-bottom:14px; }
  h1 { font-size:1.55rem; line-height:1.2; letter-spacing:-0.01em; margin-bottom:14px; }
  h2 { font-size:1.02rem; text-transform:uppercase; letter-spacing:0.09em; color:var(--muted); margin:26px 0 10px; }
  h3 { font-size:0.98rem; margin:20px 0 8px; }
  p { color:var(--ink2); margin-bottom:12px; }
  p strong, li strong { color:var(--ink); }
  .lede { font-size:1.02rem; color:var(--ink2); }
  .mut { font-size:0.82rem; color:var(--muted); }
  ul { margin:0 0 12px 20px; color:var(--ink2); }
  li { margin-bottom:7px; }
  code { background:var(--surface2); border:1px solid var(--ring); border-radius:6px; padding:1px 6px; font-size:0.8em; }
  .tablewrap { overflow-x:auto; margin-bottom:10px; }
  table { width:100%; border-collapse:collapse; font-size:0.82rem; background:var(--surface); border:1px solid var(--ring); border-radius:12px; overflow:hidden; }
  th { text-align:left; font-size:0.64rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); padding:9px 10px; border-bottom:1px solid var(--grid); }
  td { padding:8px 10px; border-bottom:1px solid var(--grid); vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  td.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
  td em { color:var(--muted); }
  .backline { margin:20px 0; font-size:0.86rem; }
  .idx { padding:26px 0 30px; }
  .idxcard { display:block; background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:16px 18px; margin-bottom:12px; text-decoration:none; }
  .idxcard:hover { border-color:var(--s1); }
  .idxcard .d { font-size:0.7rem; font-weight:700; letter-spacing:0.1em; text-transform:uppercase; color:var(--muted); }
  .idxcard .t { display:block; color:var(--ink); font-weight:700; margin:4px 0 6px; font-size:1.02rem; }
  .idxcard .z { display:block; color:var(--ink2); font-size:0.86rem; }
  footer.legal { border-top:1px solid var(--grid); padding:22px 0 40px; margin-top:10px; }
  footer.legal p { font-size:0.75rem; color:var(--muted); max-width:90ch; }
"""


def page_shell(title_tag, active, inner):
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html.escape(title_tag)}</title>
<link rel="icon" href="../assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="shortcut icon" href="../favicon.ico">
<style>{PAGE_CSS}</style>
</head>
<body>
<header class="site"><div class="wrap sitebar">
  <img class="sitelogo" src="../assets/logo.jpg" width="440" height="440" alt="">
  <div><span class="markname"><span class="open">OPEN LEDGER</span> SPORTS</span>
    <small class="marksub">The Morning Line · the daily blog</small></div>
  <nav class="navlinks">
    <a href="../">Today's Board</a>
    <a href="../#ledger">The Ledger</a>
    <a href="./" class="{'here' if active == 'index' else ''}">Blog</a>
  </nav>
</div></header>
<div class="wrap">
{inner}
<footer class="legal"><p>{LEGAL}</p></footer>
</div>
</body>
</html>'''


def render_post_page(item):
    kicker = "The Morning Line" if item["kind"] == "slate" else "The Morning Line · Off-day desk"
    inner = (f'<article>'
             f'<span class="kicker">{kicker}</span>'
             f'<span class="postdate">{nice_date(item["date"])}</span>'
             f'<h1>{_html.escape(item["title"])}</h1>'
             f'{item["html"]}'
             f'<p class="backline"><a href="./">← All posts</a> · '
             f'<a href="../">Today\'s board</a> · '
             f'<a href="../feed.xml">Subscribe (RSS)</a></p>'
             f'</article>')
    return page_shell(f'{item["title"]} — Open Ledger Sports', "post", inner)


def render_index(items):
    cards = "".join(
        f'<a class="idxcard" href="{it["date"]}.html">'
        f'<span class="d">{nice_date(it["date"])}'
        f'{" · off-day desk" if it["kind"] == "evergreen" else ""}</span>'
        f'<span class="t">{_html.escape(it["title"])}</span>'
        f'<span class="z">{_html.escape(it["teaser"])}</span></a>'
        for it in sorted(items, key=lambda i: i["date"], reverse=True))
    if not cards:
        cards = ('<p class="mut">The first post lands with the next morning board. '
                 'One a day after that: the slate when there is one, the bettor\'s '
                 'bookshelf when there isn\'t.</p>')
    inner = (f'<div class="idx">'
             f'<span class="kicker">The Morning Line</span>'
             f'<h1>The daily blog</h1>'
             f'<p class="lede">One post every day. Game days walk the slate with the engine\'s '
             f'own numbers — yesterday\'s grades on the public ledger, every matchup, and the '
             f'angles worth a bettor\'s attention. Off days go to the mechanics: closing line '
             f'value, the vig, sizing, variance. Same rules as everything here: real numbers, '
             f'no locks, losses in the same font as wins.</p>'
             f'{cards}</div>')
    return page_shell("The Morning Line — Open Ledger Sports daily blog", "index", inner)


def render_all(store):
    os.makedirs(BLOG_DIR, exist_ok=True)
    for it in store["items"]:
        path = os.path.join(BLOG_DIR, f'{it["date"]}.html')
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_post_page(it))
    with open(os.path.join(BLOG_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index(store["items"]))
    print(f'Rendered {len(store["items"])} post page(s) + blog/index.html')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rebuild_only = "--rebuild-only" in sys.argv
    date = args[0] if args else datetime.now(ET).strftime("%Y-%m-%d")

    store = load_json(STORE_PATH, {"items": []})

    if not rebuild_only and not any(it["date"] == date for it in store["items"]):
        B = crypto_box.load_dataset(ROOT, "board", date)
        if B is not None and (B.get("board") or B.get("scratches")):
            title, teaser, body = build_slate_article(date, B)
            kind = "slate"
            pub = B.get("generated_utc") or f"{date}T15:10:00Z"
        else:
            title, teaser, body = build_evergreen_article(date, store)
            kind = "evergreen"
            pub = f"{date}T15:10:00Z"
        store["items"].append({
            "date": date, "kind": kind, "title": title, "teaser": teaser,
            "html": body, "pubDate": _rfc822(pub),
        })
        store["items"] = sorted(store["items"], key=lambda it: it["date"])[-KEEP:]
        os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1)
        print(f'Added {kind} post for {date}: {title}')
    elif not rebuild_only:
        print(f'Post for {date} already exists — re-rendering pages only.')

    render_all(store)

    # feed.xml carries the blog items too; regenerate it from both stores so
    # the feed a reader polls minutes after the board run already has today's
    # post. feed.rebuild reads blog_items.json itself.
    import feed
    feed.rebuild(ROOT, os.environ.get("SITE_URL", ""))
    print("Updated feed.xml")


if __name__ == "__main__":
    main()
