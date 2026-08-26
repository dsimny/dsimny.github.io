#!/usr/bin/env python3
"""
Open Ledger Sports — the football site surface (football/, gap F).

Writes:
    football/index.html            hub: the record, and every week
    football/<week>/index.html     one slate week

------------------------------------------------------------------------------
WHY THE PUBLIC PAGE IS REDACTED BEFORE KICKOFF, AND WHAT IT IS PROTECTING
------------------------------------------------------------------------------
THE SELECTION RULE IS PUBLIC AND DETERMINISTIC. Rank 1 is the lowest effective
overround; the side is the one furthest above de-vigged fair. Both are computed
from exactly the fields a full-slate writeup would print. So publishing every
covered game's data block before kickoff does not merely hint at the premium
play - it hands it over, exactly.

MEASURED 2026-08-26 on the live capture: given the 32 covered games' blocks,
recomputing the rule reproduces rank 1 and its side in full. A paywall over that
would be cosmetic, which is the precise failure PLAN-paid-tier.md names for the
MLB engine ("the engine is public and deterministic... anyone can re-run it").
It bites harder here because the football rule is arithmetic rather than a
simulation.

SO THE FULL SLATE IS THE MEMBERS' PRODUCT, and that is not a paywall bolted onto
a public page - it is what fp-v0.1 section 5 already said members buy: timing
and the full slate. Before kickoff the public page carries
  - the free play IN FULL, numbers and prose,
  - the coverage summary and the complete NO MARKET list with reasons,
  - the premium play as matchup and kickoff ONLY (House Rule 7's redaction:
    no side, no price, no probability),
and after grading the same URL reveals everything, win or lose.

WITHHOLDING A PICK BEFORE KICKOFF IS THE PRODUCT. WITHHOLDING IT AFTER IS FRAUD.
That sentence is the whole design and it is why the reveal is automatic rather
than a decision someone makes each week.

THE NO MARKET LIST IS PUBLISHED IN FULL AT ALL TIMES. It is the proof of no
cherry-picking and it leaks nothing - a game we could not cover is a game we
have no play on.

NO EXPECTATION CLAIM APPEARS ANYWHERE ON THESE PAGES (spec section 1, House
Rules 4 and 8). "edge", "+EV", "value" and "the model likes" are barred in
football copy. The page says what the market said and what it cost.

Run:
  python scripts/football/page.py --week 2026-09-01
  python scripts/football/page.py --week 2026-09-01 --reveal   # force full
  python scripts/football/page.py --hub-only
"""
import argparse
import html
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import market                                        # noqa: E402
import board as boardmod                             # noqa: E402
import crypto_box                                    # noqa: E402
from blog import PAGE_CSS, LEGAL, nice_date          # noqa: E402

ROOT = os.path.join(HERE, "..", "..")
FB = os.path.join(ROOT, "data", "football")
OUT = os.path.join(ROOT, "football")

E = html.escape

# The paid tier stays INVISIBLE until WHOP_CHECKOUT_URL is set, exactly as
# build_site.py does it: the site never advertises something that cannot be
# bought, and the switch is one repo variable rather than an edit.
PREMIUM_URL = os.environ.get("WHOP_CHECKOUT_URL", "").strip()
PREMIUM_PRICE = "$30/month"


def upgrade_block(week=None):
    """The football CTA. Leads with the SLATE, never with the play.

    One committed play a week is ~4 a month. Sold as a pick service that is
    indefensible at this price and invites comparison with people willing to
    promise a win - a comparison this brand loses by design, because it has
    disarmed on claims. The ~57 reasoned games are the product.
    """
    if not PREMIUM_URL:
        return ""
    return f'''
    <div class="upgrade">
      <p class="joinlead">Members get the whole slate, before kickoff.</p>
      <p class="joinsub">Every covered game reasoned through from market prices — not a shortlist —
      plus the one play we would act on, committed and fingerprinted before it starts.
      {PREMIUM_PRICE}.</p>
      <p class="joinsub">No claim is made that it wins. It is staked at zero units and every play
      lands on the public record above, win or lose, so you can check it before you pay and keep
      checking after. If that record is not good enough to justify this, do not buy it.</p>
      <a class="upgradebtn" href="{E(PREMIUM_URL)}" rel="noopener">Go premium</a>
    </div>'''


NOCLAIM = (
    "<strong>We make no claim that these plays win.</strong> Two pre-registered "
    "studies, both published in full, found this market cannot be out-forecast "
    "at the moment we can act: a market-blind model lost to the de-vigged "
    "consensus in every season and market tested, and buying the best available "
    "price returned less than the vig it was paying. So this is not sold as an "
    "edge. It is sold as process and receipts — the whole slate reasoned "
    "through from market prices, one play we would actually act on, and every "
    "one of them graded here in public, win or lose. Football is staked at "
    "<strong>zero units</strong>.")


def money(p):
    return f"{p:+d}" if isinstance(p, int) else E(str(p))


def pct(x):
    return f"{float(x) * 100:.1f}%"


def load_board(week, reveal=None):
    """Plaintext if present, else decrypt. Returns (board, revealed)."""
    plain, enc = boardmod.board_paths(week)
    b = None
    if os.path.exists(plain):
        with io.open(plain, encoding="utf-8") as f:
            b = json.load(f)
    elif os.path.exists(enc):
        if not crypto_box.have_key():
            return None, False
        b = crypto_box.decrypt_from(enc)
    if b is None:
        return None, False
    if reveal is not None:
        return b, reveal
    # Auto: revealed once the commitment log says so. One switch, not a
    # judgement call taken weekly.
    log = {}
    if os.path.exists(boardmod.COMMITMENTS):
        with io.open(boardmod.COMMITMENTS, encoding="utf-8") as f:
            log = json.load(f)
    for c in log.get("commitments", []):
        if c.get("slate_week") == week:
            return b, bool(c.get("revealed"))
    return b, False


def game_card(g, full):
    """One covered game. `full` decides whether numbers are shown."""
    head = (f'<span class="t">{E(g.get("league",""))} · '
            f'{E(g.get("matchup",""))}</span>')
    if not full:
        return (f'<article class="card">{head}'
                f'<p class="mut">Held until this week is graded. '
                f'House Rule 7: held plays are held, not hidden — this '
                f'publishes in full, with its result, once the week settles.</p>'
                f'</article>')
    off = g.get("offshore_best")
    rows = [
        ("Side", E(str(g.get("side", "")))),
        ("Best takeable price", f'{money(g.get("best_price"))} at '
                                f'{E(str(g.get("best_book","")))}'),
        ("Tier-1 books at or near it", E(str(g.get("books_at_best", "")))),
        ("De-vigged fair (this side)", pct(g["fair_side"])
         if g.get("fair_side") is not None else "—"),
        ("Eligible books", E(str(g.get("n_books", "")))),
        ("Overround, consensus → best",
         f'{g.get("raw_overround_pts")} → '
         f'<strong>{g.get("eff_overround_pts")}</strong> pts'),
    ]
    if off:
        rows.append(("Offshore, as colour only",
                     f'{money(off.get("price"))} at {E(str(off.get("book","")))}'))
    if g.get("clv_pts") is not None:
        rows.append(("CLV", f'{g["clv_pts"]} pts'))
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    wu = g.get("writeup")
    prose = f'<p class="lede">{E(wu)}</p>' if wu else (
        f'<p class="mut">{E(g.get("writeup_note") or "No writeup for this game.")}</p>')
    return (f'<article class="card">{head}{prose}'
            f'<div class="tablewrap"><table><tbody>{body}</tbody></table></div>'
            f'</article>')


def render_week(week, reveal=None):
    b, revealed = load_board(week, reveal)
    if b is None:
        print(f"no board for {week} (build one with board.py, or set "
              f"BOARD_ENCRYPTION_KEY to read the encrypted one).")
        return False

    prem, free = b.get("premium"), b.get("free")
    status = b.get("coverage_status", "covered")
    warn = ""
    if status != "covered":
        warn = ('<p class="mut"><strong>Manual review.</strong> More than a '
                'quarter of this week\'s games failed our coverage markers, so '
                'we are not calling this slate covered. Every exclusion is '
                'listed below with its reason.</p>')

    parts = []
    if not b.get("decision_made"):
        parts.append('<p class="mut">The play for this week has not been chosen '
                     'yet. Games are fingerprinted as each one reaches 24 hours '
                     'before kickoff, and the week\'s play is picked at a '
                     'pre-committed moment — Saturday 2pm Eastern — '
                     'from what is committed and not yet started.</p>')
    else:
        parts.append('<h2>The free play</h2>')
        parts.append(game_card(free, True) if free else
                     '<p class="mut">No qualifying free play this week. '
                     'Passing is a position.</p>')
        parts.append('<h2>The premium play</h2>')
        if not prem:
            parts.append('<p class="mut">No qualifying play this week.</p>')
        elif revealed:
            parts.append(game_card(prem, True))
        else:
            parts.append(
                f'<article class="card"><span class="t">'
                f'{E(prem.get("league",""))} · {E(prem.get("matchup",""))}</span>'
                f'<p class="mut">One play, <strong>0 units</strong>, committed '
                f'before kickoff and fingerprinted. The side, the price and the '
                f'numbers publish here after the week is graded — win or '
                f'lose. Members get it before kickoff.</p></article>')

    parts.append('<h2>The rest of the slate</h2>')
    rest = [g for g in b.get("games", []) if g.get("tier") == "slate"]
    if revealed:
        parts.append(f'<p class="mut">All {len(rest)} other covered games, '
                     f'ranked by how tight their market was.</p>')
        parts += [game_card(g, True) for g in rest]
    else:
        names = "".join(f'<li>{E(g.get("league",""))} · {E(g.get("matchup",""))}</li>'
                        for g in rest)
        parts.append(
            f'<p class="mut">{len(rest)} further games passed our coverage '
            f'markers this week. <strong>Their numbers and write-ups are the '
            f'members\' product and publish here once the week is graded.</strong> '
            f'They are withheld for a specific reason rather than a commercial '
            f'one: the selection rule is public and deterministic, so printing '
            f'every game\'s numbers before kickoff would hand over the premium '
            f'play exactly, and a paywall over that would be theatre. The '
            f'matchups are listed so you can see nothing was added later.</p>'
            f'<ul class="plain">{names}</ul>')

    parts.append(upgrade_block(week))

    nm = b.get("no_market", [])
    parts.append('<h2>No market</h2>')
    if nm:
        rows = "".join(
            f'<tr><td>{E(n.get("matchup",""))}</td><td>{E(n.get("reason",""))}</td></tr>'
            for n in nm)
        parts.append(
            '<p class="mut">Every game we could not cover, with the reason. '
            'Published in full at all times: a game we cannot cover is a game we '
            'have no play on, so this leaks nothing and it is the proof that '
            'nothing was quietly dropped.</p>'
            f'<div class="tablewrap"><table><thead><tr><th>Game</th>'
            f'<th>Why</th></tr></thead><tbody>{rows}</tbody></table></div>')
    else:
        parts.append('<p class="mut">Every game on the slate passed the coverage '
                     'markers.</p>')

    inner = f'''
<div class="idx">
  <span class="kicker">Football · slate week</span>
  <span class="postdate">{nice_date(week)}</span>
  <h1>{b.get("n_covered", 0)} games covered, one play</h1>
  <p class="lede">{NOCLAIM}</p>
  {warn}
  <p class="mut">{b.get("n_covered",0)} covered · {b.get("n_excluded",0)} no
  market · decision moment {E(b.get("decision_moment_utc",""))}</p>
  {"".join(parts)}
  <p class="backline"><a href="/football/">Football record</a> ·
  <a href="/">Today's board</a> · <a href="/blog/">The Morning Line</a></p>
</div>'''
    write(os.path.join(OUT, week), inner,
          f"Football, week of {nice_date(week)} — Open Ledger Sports",
          "Every covered game reasoned from market prices, one graded play, "
          "and every exclusion named. No expectation claim.")
    print(f"wrote football/{week}/index.html "
          f"({'revealed' if revealed else 'redacted'}, "
          f"{b.get('n_covered',0)} covered)")
    return True


def render_hub():
    led = {"entries": []}
    p = os.path.join(FB, "football_ledger.json")
    if os.path.exists(p):
        with io.open(p, encoding="utf-8") as f:
            led = json.load(f)
    entries = led.get("entries", [])

    weeks = sorted({d for d in os.listdir(OUT)} if os.path.isdir(OUT) else [],
                   reverse=True)
    weeks = [w for w in weeks if os.path.isdir(os.path.join(OUT, w))]
    wl = "".join(f'<li><a href="/football/{w}/">Week of {nice_date(w)}</a></li>'
                 for w in weeks) or "<li>No weeks published yet.</li>"

    if entries:
        rows = "".join(
            f'<tr><td>{E(e.get("slate_week",""))}</td>'
            f'<td>{E(e.get("matchup",""))}</td><td>{E(str(e.get("side","")))}</td>'
            f'<td>{money(e.get("price"))}</td><td>{E(str(e.get("result","")))}</td>'
            f'<td>{e.get("clv_pts","")}</td></tr>' for e in entries)
        table = (f'<div class="tablewrap"><table><thead><tr><th>Week</th>'
                 f'<th>Game</th><th>Side</th><th>Price</th><th>Result</th>'
                 f'<th>CLV</th></tr></thead><tbody>{rows}</tbody></table></div>')
    else:
        table = ('<p class="mut"><strong>No week has been graded yet.</strong> '
                 'This table is empty because the season has not started, not '
                 'because nothing has been published — an empty record and '
                 'a hidden one look identical, so we say which this is.</p>')

    inner = f'''
<div class="idx">
  <span class="kicker">Football</span>
  <h1>The football record</h1>
  <p class="lede">{NOCLAIM}</p>
  <h2>Every graded play</h2>
  <p class="mut">Append-only. Nothing is edited, nothing is deleted, losses
  publish exactly like wins. Zero units throughout — the P&amp;L column is
  what one unit would have returned, not money risked.</p>
  {table}
  {upgrade_block()}
  <h2>Weeks</h2>
  <ul class="plain">{wl}</ul>
  <p class="backline"><a href="/">Today's board</a> ·
  <a href="/#ledger">MLB ledger</a> · <a href="/blog/">The Morning Line</a></p>
</div>'''
    write(OUT, inner, "Football record — Open Ledger Sports",
          "Every football play we have published, graded in public, win or "
          "lose. No expectation claim is made.")
    print(f"wrote football/index.html ({len(entries)} graded, {len(weeks)} weeks)")


def write(dirpath, inner, title, desc):
    shell = f'''<!DOCTYPE html>
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
  <img class="sitelogo" src="/assets/logo.jpg" width="440" height="440" alt="">
  <div><span class="markname"><span class="open">OPEN LEDGER</span> SPORTS</span>
    <small class="marksub">Football · process and receipts, not predictions</small></div>
  <nav class="navlinks">
    <a href="/">Today's Board</a>
    <a href="/#ledger">The Ledger</a>
    <a href="/football/" class="here">Football</a>
    <a href="/blog/">Blog</a>
    <a href="/odds/">Odds</a>
  </nav>
</div></header>
<div class="wrap">
{inner}
<footer class="legal"><p>{LEGAL}</p></footer>
</div>
</body>
</html>'''
    os.makedirs(dirpath, exist_ok=True)
    with io.open(os.path.join(dirpath, "index.html"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(shell)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week")
    ap.add_argument("--reveal", action="store_true",
                    help="force the full reveal (normally driven by the "
                         "commitment log, so it is not a weekly judgement call)")
    ap.add_argument("--hub-only", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="re-render every week that has a board, then the hub")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    if args.all:
        # RE-RENDER EVERY WEEK, not just the current one. A week's page changes
        # when its commitment flips to revealed, which happens during GRADING -
        # a different run, days after that page was last written. Rendering only
        # "this week" would leave graded weeks frozen in their redacted form,
        # which is the exact failure House Rule 7 forbids: held after grading is
        # not held, it is hidden.
        weeks = sorted({os.path.basename(p).replace("board_", "")
                        .replace(".json", "").replace(".enc", "")
                        for p in os.listdir(FB)
                        if p.startswith("board_")})
        for w in weeks:
            render_week(w, True if args.reveal else None)
        if not weeks:
            print("no boards on disk yet; hub only.")
    elif not args.hub_only and args.week:
        render_week(args.week, True if args.reveal else None)
    render_hub()
    return 0


if __name__ == "__main__":
    sys.exit(main())
