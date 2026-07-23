#!/usr/bin/env python3
"""
Open Ledger Sports — site generator (fully automated).
Reads data/board_<date>.json + data/ledger.json and writes index.html at the
repo root (served by GitHub Pages). The free pick, its written analysis, and
every card are generated from the engine's numbers — no hand-editing required.

Run: python scripts/build_site.py [YYYY-MM-DD]
"""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

import crypto_box

ET = ZoneInfo("America/New_York")
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).strftime("%Y-%m-%d")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# Optional, same graceful-skip pattern as the Discord webhook: set the repo
# variable DISCORD_INVITE_URL and the join prompts appear. Leave it unset and
# the site simply renders without them, never with a dead link.
DISCORD_INVITE = os.environ.get("DISCORD_INVITE_URL", "").strip()

# The paid tier stays invisible until WHOP_CHECKOUT_URL is set, so the site
# never advertises something that cannot be bought. Price lives here rather
# than inline so it only has to change in one place.
PREMIUM_URL = os.environ.get("WHOP_CHECKOUT_URL", "").strip()
PREMIUM_PRICE = "$30/month"

# Email capture. Set to a beehiiv form id to turn the embed (and its attribution
# script) back on; "" drops both. Dropped 2026-07-23: beehiiv's automated
# daily send needs a Max/Enterprise tier the empty list doesn't justify yet.
# The RSS feed stays (tool-agnostic) so any future email tool can plug in.
BEEHIIV_FORM_ID = ""
beehiiv_attribution = ('<script type="text/javascript" async src="https://subscribe-forms.beehiiv.com/attribution.js"></script>'
                       if BEEHIIV_FORM_ID else "")

B = crypto_box.load_dataset(ROOT, "board", DATE)
if B is None:
    raise SystemExit(f"No board for {DATE}. Run engine.py first.")
COMMITMENT = crypto_box.commitment_for(ROOT, DATE)
ledger = {"entries": [], "aggregates": None}
lp = os.path.join(ROOT, "data", "ledger.json")
if os.path.exists(lp):
    with open(lp, encoding="utf-8") as f:
        ledger = json.load(f)

# %-d and %-I (strip the leading zero) only exist in glibc's strftime, so they
# work on the Linux runner but crash on Windows. Compose those parts by hand.
_date_obj = datetime.strptime(DATE, "%Y-%m-%d")
NICE_DATE = f"{_date_obj:%A, %B} {_date_obj.day}, {_date_obj.year}"

def et_time(utc_str):
    t = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
    return f"{t.hour % 12 or 12}:{t:%M %p} ET"

plays = sorted([b for b in B["board"] if b.get("published")], key=lambda b: -b["confidence"])
leans = sorted([b for b in B["board"] if not b.get("published")], key=lambda b: -b["confidence"])
scratches = B["scratches"]
has_odds = any(b["mkt_odds"] is not None for b in B["board"])
n_r8 = sum(1 for b in leans if b.get("rule8_flag"))
n_noedge = sum(1 for b in leans if b.get("no_edge"))

# Free pick = cleanest lower-board play (no flags); fallback mid-board; None if no plays
free = None
if plays:
    free = next((b for b in reversed(plays) if not b["rule4_flag"] and not b["rule2_pivot"]),
                plays[len(plays) // 2])

TIER_META = {
    "Low Risk (Safe Play)":       ("tier-safe", "🟢"),
    "Moderate Risk (Value Play)": ("tier-value", "🟡"),
    "High Risk (Longshot)":       ("tier-long", "🔴"),
    "Pass":                       ("tier-pass", "⚪"),
}

# ---------------- auto-generated analysis ----------------
def gen_analysis(b):
    """Four paragraphs of pick analysis generated from the engine's numbers."""
    a_name, h_name = b["matchup"].split(" @ ")
    a_ab, h_ab = b["abbr"].split(" @ ")
    pick_home = b["pick_team_abbr"] == h_ab
    pick_name, opp_name = (h_name, a_name) if pick_home else (a_name, h_name)
    psp, osp = (b["homeSP"], b["awaySP"]) if pick_home else (b["awaySP"], b["homeSP"])
    p_rec, o_rec = (b["home_rec"], b["away_rec"]) if pick_home else (b["away_rec"], b["home_rec"])
    p_rpg, p_rapg = (b["home_rpg"], b["home_rapg"]) if pick_home else (b["away_rpg"], b["away_rapg"])
    o_rpg, o_rapg = (b["away_rpg"], b["away_rapg"]) if pick_home else (b["home_rpg"], b["home_rapg"])
    conf, fair = b["confidence"], (b["fair_home"] if pick_home else b["fair_away"])
    proj = f'{b["proj_away"]:g}–{b["proj_home"]:g}'

    # P1 — pitching matchup
    era_gap = osp["era"] - psp["era"]
    if era_gap >= 0.75:
        p1 = (f'<p><strong>The mound tilts this game.</strong> {psp["name"]} brings a {psp["era"]:.2f} ERA and '
              f'{psp["whip"]:.2f} WHIP over {psp["ip"]:g} innings against {osp["name"]} at {osp["era"]:.2f} '
              f'({osp["whip"]:.2f} WHIP). A {era_gap:.1f}-run ERA gap, weighted over the ~5.5 innings a starter '
              f'covers, moves real win probability before anyone swings. Baserunners are kindling: '
              f'the {psp["whip"]:.2f}-vs-{osp["whip"]:.2f} WHIP spread decides whose innings stay quiet.</p>')
    elif era_gap <= -0.5:
        p1 = (f'<p><strong>The pick survives a starter disadvantage.</strong> {osp["name"]} ({osp["era"]:.2f} ERA, '
              f'{osp["whip"]:.2f} WHIP) outgrades {psp["name"]} ({psp["era"]:.2f}, {psp["whip"]:.2f}) on the season '
              f'line, so this edge is carried by the lineups and bullpens, not the first five innings. The engine '
              f'weighs starters over ~5.5 innings; the other 3.5 belong to the clubs.</p>')
    else:
        p1 = (f'<p><strong>The starters roughly cancel.</strong> {psp["name"]} ({psp["era"]:.2f} ERA, {psp["whip"]:.2f} '
              f'WHIP over {psp["ip"]:g} IP) against {osp["name"]} ({osp["era"]:.2f}, {osp["whip"]:.2f}) is close to a '
              f'push on season numbers, which hands the game to the team-level gap below.</p>')

    # P2 — clubs, park, simulation result
    pf = b["park_factor"]
    park_note = ("a hitter-friendly park (factor %.2f)" % pf) if pf >= 1.05 else \
                ("a run-suppressing park (factor %.2f)" % pf) if pf <= 0.95 else \
                ("a neutral park (factor %.2f)" % pf)
    p2 = (f'<p><strong>The clubs, the park, the count.</strong> {pick_name} ({p_rec}) score {p_rpg:g} and allow '
          f'{p_rapg:g} runs a game; {opp_name} ({o_rec}) score {o_rpg:g} and allow {o_rapg:g}. In {park_note}, '
          f'{B["n_sims"]:,} simulations land on a {proj} average score, with {pick_name} winning '
          f'<strong>{conf*100:.1f}%</strong> of them, a fair price of <strong>{fair:+d}</strong>.</p>')

    # P3 — market (or its absence)
    if b["mkt_odds"] is not None:
        p3 = (f'<p><strong>The market price is the point.</strong> The consensus line is <strong>{b["mkt_odds"]:+d}</strong> '
              f'against our {fair:+d} fair, a <strong>{b["edge"]*100:+.1f}-point edge</strong> and '
              f'<strong>{b["ev_per_unit"]*100:+.1f}% EV per unit</strong> at the listed price. Rule 8 checked the '
              f'disagreement with the de-vigged market: {abs(b["divergence"])*100:.1f} points, inside the 12-point cap: '
              f'a disagreement, not a delusion. Quarter-Kelly would size this at {b["kelly_pct"]:.1f}% of bankroll; '
              f'the tier framework governs it to <strong>{b["units"]:g}u</strong>. The governor beats the gas pedal.</p>')
    else:
        p3 = (f'<p><strong>No market feed today.</strong> The {fair:+d} shown is our model fair line, not a sportsbook '
              f'price. The edge only exists if your book beats that number; compare before betting anything.</p>')

    # P4 — breaker summary + bottom line
    flags = []
    if b["rule4_flag"]: flags.append("Rule 4 cut the allocation over a limited-workload starter")
    if b["rule2_pivot"]: flags.append("Rule 2 pivoted the play off a capped moneyline")
    flag_txt = ("; ".join(flags) + ".") if flags else \
        "Every automated breaker passed at full size. The full check log is printed below, including the passes."
    if b["mkt_odds"] is not None:
        bl = (f'{pick_name} at {b["mkt_odds"]:+d} carries a {b["edge"]*100:.1f}-point model edge at {b["units"]:g}u. '
              f'If the line steams past our {fair:+d} fair before first pitch, the edge is gone; pass without regret. '
              f'Passing is a position too.')
    else:
        bl = (f'{pick_name} is a {b["units"]:g}u play only at prices better than our {fair:+d} fair line. '
              f'Worse than that, pass without regret; passing is a position too.')
    p4 = f'<p><strong>Breaker sheet:</strong> {flag_txt}</p><p><strong>Bottom line:</strong> {bl}</p>'
    return p1 + p2 + p3 + p4

# ---------------- shared components ----------------
def prob_bar(b, h=14):
    pa, ph = b["p_away"] * 100, b["p_home"] * 100
    a_ab, h_ab = b["abbr"].split(" @ ")
    return f'''
      <div class="probrow">
        <span class="problab">{a_ab} {pa:.1f}%</span>
        <div class="probbar" style="height:{h}px" role="img" aria-label="Win probability: {a_ab} {pa:.1f} percent, {h_ab} {ph:.1f} percent">
          <div class="seg segA" style="width:{pa:.1f}%"></div>
          <div class="seg segH" style="width:{ph:.1f}%"></div>
        </div>
        <span class="problab">{h_ab} {ph:.1f}%</span>
      </div>'''

def market_cells(b):
    if b["mkt_odds"] is None:
        return '<div><span class="stlab">Market</span><span class="stval">n/a</span></div>'
    edge_cls = "edge-pos" if b["edge"] >= 0.02 else ("edge-neg" if b["edge"] < 0 else "")
    return f'''
        <div><span class="stlab">Market line</span><span class="stval">{b["mkt_odds"]:+d} <em>consensus</em></span></div>
        <div><span class="stlab">Edge vs price</span><span class="stval {edge_cls}">{b["edge"]*100:+.1f} pts</span></div>
        <div><span class="stlab">EV per 1u</span><span class="stval {edge_cls}">{b["ev_per_unit"]*100:+.1f}%</span></div>'''

def statrow(b, big=False):
    tot = b["mkt_total"] if b["mkt_total"] is not None else b["ref_total"]
    pov = b["p_over_mkt"] if b["p_over_mkt"] is not None else b["p_over"]
    sims = f'<div><span class="stlab">Simulations</span><span class="stval">{b["n_sims"]:,}</span></div>' if big else ""
    return f'''
      <div class="statrow">
        <div><span class="stlab">Projected</span><span class="stval">{b["proj_away"]:g}–{b["proj_home"]:g}</span></div>
        <div><span class="stlab">Model fair</span><span class="stval">{b["fair_away"]:+d} / {b["fair_home"]:+d}</span></div>
        {market_cells(b)}
        <div><span class="stlab">Sim total</span><span class="stval">{b["mean_total"]:g} <em>({pov*100:.0f}% over {tot:g})</em></span></div>
        {sims}
      </div>'''

def flags_html(b):
    flags = ""
    if b["rule2_pivot"]: flags += '<span class="flag">R2 PIVOT</span>'
    if b["rule4_flag"]: flags += '<span class="flag">R4 FLAG</span>'
    if b.get("rule8_flag"): flags += '<span class="flag flag-scr">R8 DIVERGENCE</span>'
    if free is not None and b is free: flags += '<span class="flag flag-free">★ FREE PICK</span>'
    return flags

def card(b, published):
    cls, icon = TIER_META[b["risk_tier"]]
    away_name, home_name = b["matchup"].split(" @ ")
    checks = "".join(f"<li>{c}</li>" for c in b["checks"])
    if published:
        units_str = f'{b["units"]:g}u ({b["units"]:g}% bankroll)'
    elif b.get("rule8_flag"):
        units_str = "0u, held by Rule 8"
    elif b.get("no_edge"):
        units_str = "0u, no edge at market price"
    else:
        units_str = "0u, logged as lean only"
    tier_short = b["risk_tier"].split("(")[-1].rstrip(")") if "(" in b["risk_tier"] else b["risk_tier"]
    return f'''
    <article class="card {'card-lean' if not published else ''}">
      <header class="cardhead">
        <div>
          <h3>{away_name} <span class="at">@</span> {home_name}</h3>
          <p class="meta">{et_time(b["utc"])} · {b["venue"]} · park factor {b["park_factor"]:.2f}</p>
        </div>
        <div class="flags">{flags_html(b)}</div>
      </header>
      <div class="pitchers">
        <span>{b["awaySP"]["name"]} <em>{b["awaySP"]["era"]:.2f} ERA · {b["awaySP"]["ip"]:g} IP</em></span>
        <span class="vs">vs</span>
        <span>{b["homeSP"]["name"]} <em>{b["homeSP"]["era"]:.2f} ERA · {b["homeSP"]["ip"]:g} IP</em></span>
      </div>
      {prob_bar(b)}
      {statrow(b)}
      <div class="playrow">
        <div><span class="playlab">Play</span><span class="playval">{b["pick"]}</span></div>
        <div><span class="playlab">Confidence</span><span class="playval">{b["confidence"]*100:.1f}%</span></div>
        <div><span class="playlab">Risk</span><span class="playval tierchip {cls}">{icon} {tier_short}</span></div>
        <div><span class="playlab">Suggested</span><span class="playval">{units_str}</span></div>
      </div>
      <details class="breakers">
        <summary>Circuit-breaker log ({len(b["checks"])} checks)</summary>
        <ul>{checks}</ul>
      </details>
    </article>'''

def locked_card(b):
    """Board card for a premium play: proves the position exists without giving it away.

    Deliberately NOT a transparency rollback. The pick is committed to the public
    repo before first pitch and published in full on the ledger once graded, win
    or lose, so the record stays verifiable end to end (house rules 1 and 3).
    Only the pre-game reveal is withheld.
    """
    cls, icon = TIER_META[b["risk_tier"]]
    away_name, home_name = b["matchup"].split(" @ ")
    tier_short = b["risk_tier"].split("(")[-1].rstrip(")") if "(" in b["risk_tier"] else b["risk_tier"]
    return f'''
    <article class="card card-locked">
      <header class="cardhead">
        <div>
          <h3>{away_name} <span class="at">@</span> {home_name}</h3>
          <p class="meta">{et_time(b["utc"])} · {b["venue"]}</p>
        </div>
        <div class="flags"><span class="flag flag-lock">🔒 PREMIUM</span></div>
      </header>
      <div class="playrow">
        <div><span class="playlab">Play</span><span class="playval lockedval">Premium Only</span></div>
        <div><span class="playlab">Risk</span><span class="playval tierchip {cls}">{icon} {tier_short}</span></div>
        <div><span class="playlab">Breaker checks</span><span class="playval">{len(b["checks"])} run</span></div>
      </div>
      <p class="lockednote">Side, price, and sizing go to premium members before first pitch. The
      pick is timestamped in the public repository ahead of the game and publishes in full, with
      its complete breaker log, on the ledger once graded. We hold the position, never the result.</p>
    </article>'''

def scratch_card(s):
    return f'''
    <article class="card card-scratch">
      <header class="cardhead">
        <div>
          <h3>{s["matchup"].replace(" @ ", ' <span class="at">@</span> ')}</h3>
          <p class="meta">{et_time(s["utc"])} · {s["venue"]}</p>
        </div>
        <div class="flags"><span class="flag flag-scr">⛔ SCRATCH</span></div>
      </header>
      <p class="scratchreason"><strong>{s["rule"]}.</strong> {s["reason"]}</p>
    </article>'''

# Only rendered once a board is actually being committed encrypted. Without a
# commitment there is nothing to verify, and claiming otherwise would be the
# exact sort of unearned assurance this site exists to argue against.
commit_block = f'''
    <div class="commit">
      <p class="commitlead">This board was fingerprinted before first pitch.</p>
      <p class="commitsub">SHA-256 of the full board, published {COMMITMENT["committed_utc"]},
      while the picks themselves were still encrypted:</p>
      <code class="commithash">{COMMITMENT["board_sha256"]}</code>
      <p class="commitsub">{"The board has since been revealed: hash it yourself and compare." if COMMITMENT["revealed"] else "The board publishes in full after grading. Hash it then and compare against the fingerprint above."}
      If the two match, nothing was altered once the games were underway. That is the whole guarantee, and you do not have to take our word for any of it.</p>
    </div>''' if COMMITMENT else ""

# Deliberately promises access and disclosure, never profit. The ledger is the
# only claim we are entitled to make, and it is public either way.
upgrade_block = f'''
    <div class="upgrade">
      <p class="joinlead">Premium: the whole board, before first pitch.</p>
      <p class="joinsub">Every play we allocate, with the side, the price, the sizing, the edge and the
      full circuit-breaker log, in Discord before the games start. {PREMIUM_PRICE}.</p>
      <p class="joinsub">Every one of them still publishes on the public ledger after grading, winners
      and losers alike, so you can check the record before you pay and keep checking after. If the
      ledger is not good enough to justify this, do not buy it.</p>
      <a class="upgradebtn" href="{PREMIUM_URL}" rel="noopener">Go premium</a>
    </div>''' if PREMIUM_URL else ""

# Email capture, for people who won't join a Discord but will leave an address.
# The beehiiv loader renders its own form (title, field, button, consent) inline
# where the script sits; a short lead-in sets the context above it.
email_block = f'''
    <div class="emailcap">
      <p class="joinlead">Prefer email? The free pick, in your inbox each morning.</p>
      <script async src="https://subscribe-forms.beehiiv.com/v3/loader.js" data-beehiiv-form="{BEEHIIV_FORM_ID}"></script>
    </div>''' if BEEHIIV_FORM_ID else ""

# ---------------- free pick section ----------------
# Renders only when DISCORD_INVITE_URL is set, so the site never ships a dead
# "join" button. Deliberately makes no promise about record or profit.
join_block = f'''
    <div class="join">
      <p class="joinlead">The free pick lands in Discord every morning before first pitch.</p>
      <p class="joinsub">Every graded result follows it, win or lose. A members channel for the rest of the board opens once the ledger has a record worth charging for; join now and you will be there when it does. The ledger is the pitch, so go read it before you decide we are worth following.</p>
      <a class="joinbtn" href="{DISCORD_INVITE}" rel="noopener">Join the Discord</a>
    </div>''' if DISCORD_INVITE else ""

def tease(b):
    # Matchup and risk tier only. Printing confidence, edge and unit size here
    # gave away most of a held play: on a two-team game, a stated confidence
    # points straight at the side.
    _, icon = TIER_META[b["risk_tier"]]
    return (f'<div class="tease"><span>{b["abbr"]}</span>'
            f'<span class="tval">{icon} premium</span></div>')
teasers = "".join(tease(b) for b in plays if b is not free)

if free is not None:
    f_away, f_home = free["matchup"].split(" @ ")
    ff = flags_html(free).replace('<span class="flag flag-free">★ FREE PICK</span>', '')
    if not ff.replace(" ", ""):
        ff = '<span class="flag flag-ok">✓ ALL BREAKERS CLEAR</span>'
    tier_cls, tier_icon = TIER_META[free["risk_tier"]]
    if free["rule4_flag"]:
        bet_note = ", downgraded by Rule 4"
    elif free.get("kelly_pct") is not None:
        bet_note = f', quarter-Kelly suggests {free["kelly_pct"]:.1f}% of bankroll; the tier cap governs it down to {free["units"]:g}u'
    else:
        bet_note = ""
    edge_row = (f'<div><span class="k">Edge</span><span class="v edge-pos">{free["edge"]*100:+.1f} pts vs the '
                f'{free["mkt_odds"]:+d} consensus price</span></div>') if free["mkt_odds"] is not None else ""
    free_section = f'''
    <div class="hero">
      <span class="kicker">★ Free Pick of the Day</span>
      <span class="kickerdate">{NICE_DATE}</span>
      <h1>{f_away} <span class="at">@</span> {f_home}</h1>
      <p class="sub">Every day, one pick free and in full: complete analysis, unit sizing, market edge, and every circuit-breaker check. By design it's a <strong>strong play, but not our Play of the Day</strong>: the top-confidence plays go to premium members before first pitch, then publish in full, winners and losers alike, on <a href="#" data-goto="ledger">the ledger</a> once graded. Same engine, same {free["n_sims"]:,} simulations, measured against real market prices, committed to the public record before first pitch and graded on the ledger after.</p>
    </div>
    <article class="card freecard">
      <header class="cardhead">
        <div>
          <h2>{f_away} <span class="at">@</span> {f_home}</h2>
          <p class="meta">{et_time(free["utc"])} · {free["venue"]} · park factor {free["park_factor"]:.2f}</p>
        </div>
        <div class="flags">{ff}</div>
      </header>
      <div class="pitchers">
        <span>{free["awaySP"]["name"]} <em>{free["awaySP"]["era"]:.2f} ERA · {free["awaySP"]["whip"]:.2f} WHIP · {free["awaySP"]["ip"]:g} IP</em></span>
        <span class="vs">vs</span>
        <span>{free["homeSP"]["name"]} <em>{free["homeSP"]["era"]:.2f} ERA · {free["homeSP"]["whip"]:.2f} WHIP · {free["homeSP"]["ip"]:g} IP</em></span>
      </div>
      {prob_bar(free, h=16)}
      {statrow(free, big=True)}
      <div class="schema">
        <div><span class="k">🗓️ Date</span><span class="v">{NICE_DATE}</span></div>
        <div><span class="k">⚾ League</span><span class="v">MLB</span></div>
        <div><span class="k">Game</span><span class="v">{f_away} vs. {f_home}</span></div>
        <div><span class="k">Play</span><span class="v">{free["pick"]}</span></div>
        <div><span class="k">Confidence</span><span class="v">{free["confidence"]*100:.1f}%</span></div>
        {edge_row}
        <div><span class="k">Risk level</span><span class="v {tier_cls}">{tier_icon} {free["risk_tier"]}</span></div>
        <div><span class="k">Suggested bet</span><span class="v">{free["units"]:g} unit{"s" if free["units"] != 1 else ""} ({free["units"]:g}% bankroll){bet_note}</span></div>
      </div>
      <div class="analysis"><h3>Analysis</h3>{gen_analysis(free)}</div>
      <details class="breakers" open>
        <summary>Circuit-breaker log: every check, including the ones that passed</summary>
        <ul>{"".join(f"<li>{c}</li>" for c in free["checks"])}</ul>
      </details>
    </article>
    <h2 class="sect">The rest of today's board</h2>
    <p class="sectsub">{max(len(plays)-1,0)} more plays and {len(scratches)} scratches on today's board. Leans and scratches publish in full; the held plays post with their breaker logs once graded.</p>
    <div class="boardteasers">{teasers}</div>
    {upgrade_block}
    {join_block}
    {email_block}
    <div><button class="boardcta" data-goto="board">See the full board →</button></div>
    <p class="sectsub" style="margin-top:14px;">Curious how the pick was made? <a href="#" data-goto="method">Read the methodology</a>. The whole tank is behind glass.</p>'''
else:
    free_section = f'''
    <div class="hero">
      <span class="kicker">Free Pick of the Day</span>
      <span class="kickerdate">{NICE_DATE}</span>
      <h1>No qualifying plays today.</h1>
      <p class="sub">The engine ran the full slate, and nothing cleared the circuit breakers and the edge gate at an allocatable price. We don't manufacture a pick to fill the slot. <strong>Passing is a position too.</strong> The full board of leans and scratches, with reasons, is one click away.</p>
      <div style="margin-top:10px;"><button class="boardcta" data-goto="board">See today's board →</button></div>
    </div>'''

# ---------------- ledger section ----------------
agg = ledger.get("aggregates")
graded_entries = sorted(ledger.get("entries", []), key=lambda e: e["date"], reverse=True)[:25]
res_chip = {"WIN": '<span class="res-w">● Win</span>', "LOSS": '<span class="res-l">● Loss</span>',
            "VOID": '<span class="res-v">● Void</span>'}
graded_rows = "".join(f'''
  <tr>
    <td>{e["date"]}</td><td>{e["game"]}</td><td>{e["pick"]}</td>
    <td class="num">{(e["edge"]*100 if e["edge"] is not None else 0):+.1f}</td>
    <td class="num">{e["units"]:g}u</td>
    <td class="num">{e["pnl"]:+.2f}u</td>
    <td>{res_chip.get(e["result"], e["result"])}</td>
  </tr>''' for e in graded_entries)
# A pending row for a held play must not print the side, the price, the edge or
# the size: between them they give the whole pick away, and this table was
# quietly doing exactly that while the board tab held the same plays back.
# Rows stay listed so the count is honest; the contents fill in once graded.
def pending_row(b):
    held = b is not free
    return f'''
  <tr>
    <td>{DATE}</td><td>{b["abbr"]}</td>
    <td>{"<em>Premium Only</em>" if held else b["pick"]}</td>
    <td class="num">{"n/a" if held else f'{(b["edge"]*100 if b["edge"] is not None else 0):+.1f}'}</td>
    <td class="num">{"n/a" if held else f'{b["units"]:g}u'}</td>
    <td class="num">n/a</td>
    <td><span class="pend">● Pending</span></td>
  </tr>'''
pending_rows = "".join(pending_row(b) for b in plays)
if agg:
    tiles = f'''
      <div class="tile"><span class="tl">Record</span><span class="tv">{agg["record"]}</span><span class="td">graded picks</span></div>
      <div class="tile"><span class="tl">Units</span><span class="tv">{agg["units_net"]:+.2f}</span><span class="td">net P&amp;L</span></div>
      <div class="tile"><span class="tl">ROI</span><span class="tv">{f"{agg['roi_pct']:+.1f}%" if agg["roi_pct"] is not None else "n/a"}</span><span class="td">on {agg["units_risked"]:g}u risked</span></div>
      <div class="tile"><span class="tl">Pending</span><span class="tv">{len(plays)}</span><span class="td">{B["published_units"]:g}u at risk today</span></div>'''
    strip = f'Ledger: <b>{agg["record"]}</b> · <b>{agg["units_net"]:+.2f}u</b> · opened Jul 22, 2026'
else:
    tiles = f'''
      <div class="tile"><span class="tl">Record</span><span class="tv">0–0</span><span class="td">graded picks</span></div>
      <div class="tile"><span class="tl">Units</span><span class="tv">+0.00</span><span class="td">net P&amp;L</span></div>
      <div class="tile"><span class="tl">ROI</span><span class="tv">n/a</span><span class="td">needs graded picks</span></div>
      <div class="tile"><span class="tl">Pending</span><span class="tv">{len(plays)}</span><span class="td">{B["published_units"]:g}u at risk today</span></div>'''
    strip = 'Ledger: <b>0–0</b> · <b>+0.00u</b> · opened Jul 22, 2026'

# ---------------- page ----------------
odds_note = B.get("odds_source") or "no market feed this run"
html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Open Ledger Sports: Every pick on the record</title>
<link rel="icon" href="assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="assets/apple-touch-icon.png">
<link rel="shortcut icon" href="favicon.ico">
{beehiiv_attribution}
<style>
  :root {{
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --surface2:#222220;
    --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --ring:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926;
    --good:#0ca30c; --warn:#fab219; --serious:#ec835a; --crit:#d03b3b;
  }}
  * {{ box-sizing:border-box; margin:0; }}
  body {{ background:var(--page); color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.55; }}
  a {{ color:var(--s1); }}
  .wrap {{ max-width:1060px; margin:0 auto; padding:0 20px; }}
  header.site {{ position:sticky; top:0; z-index:10; background:rgba(13,13,13,0.92); backdrop-filter:blur(8px); border-bottom:1px solid var(--grid); }}
  .sitebar {{ display:flex; align-items:center; gap:24px; padding:14px 0 4px; flex-wrap:wrap; }}
  /* The logo carries the name now, so it sits beside the tagline rather than
     above it. Circular crop matches the mark's gold ring. */
  .wordmark {{ display:flex; align-items:center; gap:12px; }}
  .sitelogo {{ width:46px; height:46px; flex:none; border-radius:50%; }}
  .markname {{ font-weight:800; letter-spacing:0.04em; font-size:1.05rem; }}
  .markname .open {{ color:var(--s1); }}
  .wordmark small {{ display:block; font-weight:500; font-size:0.62rem; letter-spacing:0.14em; color:var(--muted); text-transform:uppercase; line-height:1.4; }}
  nav.tabs {{ display:flex; gap:4px; margin-left:auto; flex-wrap:wrap; }}
  nav.tabs button {{ background:none; border:none; color:var(--ink2); font:inherit; font-size:0.86rem; padding:7px 13px; border-radius:8px; cursor:pointer; }}
  nav.tabs button:hover {{ background:var(--surface2); }}
  nav.tabs button.active {{ background:var(--surface2); color:var(--ink); font-weight:650; }}
  .ledgerstrip {{ font-size:0.76rem; color:var(--muted); width:100%; padding-bottom:10px; }}
  .ledgerstrip b {{ color:var(--ink); }}
  section.tab {{ display:none; padding:10px 0 40px; }}
  section.tab.active {{ display:block; }}
  h2.sect {{ font-size:1.02rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin:26px 0 12px; }}
  .sectsub {{ color:var(--muted); font-size:0.82rem; margin:-6px 0 12px; }}
  /* Deliberately larger than body text: this is the banner line of the page.
     The date sits below it at small size so the headline never wraps on a phone. */
  .kicker {{ display:inline-block; font-size:1.05rem; font-weight:800; letter-spacing:0.09em; text-transform:uppercase; color:var(--good); border:1px solid var(--ring); background:var(--surface); padding:8px 16px; border-radius:99px; margin-bottom:6px; }}
  .kickerdate {{ display:block; font-size:0.72rem; font-weight:700; letter-spacing:0.14em; text-transform:uppercase; color:var(--ink2); margin-bottom:14px; }}
  .hero {{ padding:28px 0 6px; }}
  .hero h1 {{ font-size:1.7rem; line-height:1.18; letter-spacing:-0.01em; }}
  .hero p.sub {{ color:var(--ink2); margin-top:8px; max-width:64ch; }}
  .slateline {{ display:flex; gap:26px; flex-wrap:wrap; margin:18px 0 6px; padding:14px 18px; background:var(--surface); border:1px solid var(--ring); border-radius:12px; }}
  .slateline div {{ font-size:0.85rem; color:var(--muted); }}
  .slateline b {{ display:block; font-size:1.15rem; color:var(--ink); font-weight:700; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(460px,1fr)); gap:14px; }}
  @media (max-width:520px) {{ .cards {{ grid-template-columns:1fr; }} }}
  .card {{ background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:18px 18px 14px; }}
  .card-lean {{ opacity:0.82; }}
  .card-scratch {{ border-style:dashed; }}
  .freecard {{ border-color:rgba(57,135,229,0.45); box-shadow:0 0 0 1px rgba(57,135,229,0.18); padding:22px; margin:16px 0; }}
  .cardhead {{ display:flex; justify-content:space-between; gap:10px; align-items:flex-start; flex-wrap:wrap; }}
  .cardhead h3 {{ font-size:1.0rem; }}
  .freecard .cardhead h2 {{ font-size:1.25rem; }}
  .at {{ color:var(--muted); font-weight:400; }}
  .meta {{ color:var(--muted); font-size:0.78rem; margin-top:2px; }}
  .flags {{ display:flex; gap:6px; flex-shrink:0; flex-wrap:wrap; }}
  .flag {{ font-size:0.66rem; font-weight:700; letter-spacing:0.06em; padding:3px 8px; border-radius:99px; background:var(--surface2); border:1px solid var(--ring); color:var(--warn); }}
  .flag-scr {{ color:var(--crit); }} .flag-ok {{ color:var(--good); }} .flag-free {{ color:var(--s1); }}
  .flag-lock {{ color:var(--s1); }}
  .card-locked {{ border-style:dashed; }}
  .lockedval {{ color:var(--ink2); font-style:italic; }}
  .commit {{ margin:16px 0 4px; padding:14px 16px; border:1px solid var(--ring); border-radius:12px; background:var(--surface); }}
  .commitlead {{ font-weight:700; font-size:0.92rem; }}
  .commitsub {{ margin-top:6px; font-size:0.8rem; color:var(--ink2); line-height:1.5; max-width:70ch; }}
  .commithash {{ display:block; margin:10px 0; padding:8px 10px; border-radius:8px; background:var(--page); border:1px solid var(--grid); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:0.72rem; color:var(--s1); word-break:break-all; }}
  /* Email-capture wrapper: styled here so a future embed drops in cleanly.
     The beehiiv-specific iframe sizing was removed with the beehiiv embed. */
  .emailcap {{ margin:18px 0 6px; }}
  .emailcap .joinlead {{ margin-bottom:10px; }}
  .upgrade {{ margin:18px 0 6px; padding:16px 18px; border:1px solid var(--s1); border-radius:14px; background:var(--surface); }}
  .upgradebtn {{ display:inline-block; margin-top:12px; padding:10px 18px; border-radius:99px; background:var(--good); color:#0d0d0d; font-weight:800; font-size:0.88rem; text-decoration:none; }}
  .join {{ margin:18px 0 6px; padding:16px 18px; border:1px solid var(--ring); border-radius:14px; background:var(--surface); }}
  .joinlead {{ font-weight:700; font-size:0.98rem; }}
  .joinsub {{ margin-top:6px; font-size:0.83rem; color:var(--ink2); line-height:1.55; max-width:60ch; }}
  .joinbtn {{ display:inline-block; margin-top:12px; padding:10px 18px; border-radius:99px; background:var(--s1); color:#0d0d0d; font-weight:800; font-size:0.88rem; text-decoration:none; }}
  .lockednote {{ margin-top:10px; font-size:0.8rem; line-height:1.5; color:var(--ink2); border-top:1px solid var(--grid); padding-top:10px; }}
  .pitchers {{ display:flex; gap:10px; align-items:baseline; font-size:0.82rem; margin:12px 0 4px; flex-wrap:wrap; }}
  .pitchers em {{ color:var(--muted); font-style:normal; font-size:0.75rem; }}
  .vs {{ color:var(--muted); font-size:0.7rem; }}
  .probrow {{ display:flex; align-items:center; gap:10px; margin:10px 0 2px; }}
  .problab {{ font-size:0.75rem; color:var(--ink2); min-width:74px; font-variant-numeric:tabular-nums; }}
  .problab:last-child {{ text-align:right; }}
  .probbar {{ flex:1; display:flex; gap:2px; }}
  .seg {{ border-radius:4px; }} .segA {{ background:var(--s1); }} .segH {{ background:var(--s2); }}
  .statrow {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px 10px; margin-top:12px; }}
  .playrow {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:12px; }}
  @media (max-width:560px) {{ .statrow, .playrow {{ grid-template-columns:repeat(2,1fr); }} }}
  .edge-pos {{ color:var(--good); }} .edge-neg {{ color:var(--crit); }}
  .stlab, .playlab {{ display:block; font-size:0.64rem; text-transform:uppercase; letter-spacing:0.09em; color:var(--muted); }}
  .stval, .playval {{ font-size:0.9rem; font-weight:650; font-variant-numeric:tabular-nums; }}
  .stval em {{ font-style:normal; font-weight:400; color:var(--muted); font-size:0.76rem; }}
  .playrow {{ background:var(--surface2); border-radius:10px; padding:10px 12px; }}
  .tierchip {{ font-size:0.8rem; }}
  .tier-safe {{ color:var(--good); }} .tier-value {{ color:var(--warn); }} .tier-long {{ color:var(--serious); }} .tier-pass {{ color:var(--muted); }}
  details.breakers {{ margin-top:12px; border-top:1px solid var(--grid); padding-top:8px; }}
  details.breakers summary {{ font-size:0.78rem; color:var(--ink2); cursor:pointer; }}
  details.breakers ul {{ margin:8px 0 2px 18px; font-size:0.78rem; color:var(--ink2); }}
  details.breakers li {{ margin-bottom:5px; }}
  .scratchreason {{ font-size:0.84rem; color:var(--ink2); margin-top:10px; }}
  .schema {{ background:var(--surface2); border-radius:12px; padding:16px 18px; margin-top:16px; font-size:0.92rem; }}
  .schema div {{ display:flex; gap:10px; padding:4px 0; }}
  .schema .k {{ min-width:118px; color:var(--muted); font-size:0.78rem; text-transform:uppercase; letter-spacing:0.07em; padding-top:2px; }}
  .schema .v {{ font-weight:600; }}
  .analysis {{ margin-top:16px; }}
  .analysis h3 {{ font-size:0.78rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin-bottom:8px; }}
  .analysis p {{ color:var(--ink2); font-size:0.93rem; margin-bottom:10px; }}
  .analysis strong {{ color:var(--ink); }}
  .boardteasers {{ display:flex; gap:10px; flex-wrap:wrap; margin:14px 0 6px; }}
  .tease {{ background:var(--surface); border:1px solid var(--ring); border-radius:10px; padding:10px 14px; font-size:0.8rem; color:var(--ink2); display:flex; flex-direction:column; gap:2px; }}
  .tease .tval {{ color:var(--muted); font-size:0.72rem; }}
  .boardcta {{ display:inline-block; margin:10px 0 0; background:var(--s1); color:#fff; border:none; font:inherit; font-weight:700; font-size:0.9rem; padding:11px 20px; border-radius:10px; cursor:pointer; }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-bottom:20px; }}
  .tile {{ background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:16px; }}
  .tile .tl {{ display:block; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.09em; color:var(--muted); }}
  .tile .tv {{ display:block; font-size:1.7rem; font-weight:750; margin-top:2px; }}
  .tile .td {{ display:block; font-size:0.74rem; color:var(--muted); }}
  table.ledger {{ width:100%; border-collapse:collapse; font-size:0.84rem; background:var(--surface); border:1px solid var(--ring); border-radius:12px; overflow:hidden; }}
  table.ledger th {{ text-align:left; font-size:0.66rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); padding:10px 12px; border-bottom:1px solid var(--grid); }}
  table.ledger td {{ padding:9px 12px; border-bottom:1px solid var(--grid); }}
  table.ledger tr:last-child td {{ border-bottom:none; }}
  td.num {{ font-variant-numeric:tabular-nums; }}
  .pend {{ color:var(--warn); font-size:0.78rem; }}
  .res-w {{ color:var(--good); font-size:0.78rem; }} .res-l {{ color:var(--crit); font-size:0.78rem; }} .res-v {{ color:var(--muted); font-size:0.78rem; }}
  .tablewrap {{ overflow-x:auto; }}
  .prose {{ max-width:74ch; }}
  .prose h3 {{ margin:24px 0 8px; font-size:1.02rem; }}
  .prose p {{ margin-bottom:12px; color:var(--ink2); }}
  .prose p strong, .prose li strong {{ color:var(--ink); }}
  .prose ul {{ margin:0 0 12px 20px; color:var(--ink2); }}
  .prose li {{ margin-bottom:6px; }}
  .callout {{ background:var(--surface); border:1px solid var(--ring); border-left:3px solid var(--s1); border-radius:10px; padding:14px 16px; margin:16px 0; color:var(--ink2); font-size:0.9rem; }}
  .rulecard {{ background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:16px 18px; margin-bottom:12px; }}
  .rulecard h3 {{ font-size:0.95rem; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .rulecard p {{ color:var(--ink2); font-size:0.87rem; margin-top:6px; }}
  .badge {{ font-size:0.62rem; font-weight:700; letter-spacing:0.07em; text-transform:uppercase; padding:3px 9px; border-radius:99px; border:1px solid var(--ring); }}
  .b-auto {{ color:var(--good); }} .b-man {{ color:var(--warn); }} .b-na {{ color:var(--muted); }}
  footer.legal {{ border-top:1px solid var(--grid); padding:26px 0 40px; margin-top:20px; }}
  footer.legal p {{ font-size:0.76rem; color:var(--muted); max-width:90ch; margin-bottom:8px; }}
  footer.legal strong {{ color:var(--ink2); }}
</style>
</head>
<body>
<header class="site"><div class="wrap sitebar">
  <div class="wordmark"><img class="sitelogo" src="assets/logo.jpg" width="440" height="440" alt="">
    <div class="marktext"><span class="markname"><span class="open">OPEN LEDGER</span> SPORTS</span>
      <small>Every pick on the record. Every rule in public.</small></div></div>
  <nav class="tabs" role="tablist">
    <button class="active" data-tab="free">Free Pick</button>
    <button data-tab="board">Today's Board</button>
    <button data-tab="ledger">The Ledger</button>
    <button data-tab="method">Methodology</button>
    <button data-tab="rules">The Rules</button>
  </nav>
  <div class="ledgerstrip">{strip} · today: <b>{len(plays)}</b> plays, <b>{B["published_units"]:g}u</b> exposure</div>
</div></header>

<div class="wrap">
  <section class="tab active" id="tab-free">{free_section}</section>

  <section class="tab" id="tab-board">
    <div class="hero">
      <h1>MLB Board: {NICE_DATE}</h1>
      <p class="sub">Every game on today's slate simulated <strong>{B["n_sims"]:,} times</strong>, priced against real market lines, then passed through eight risk circuit breakers. What survives gets an allocation. What doesn't, we tell you why.</p>
      <div class="slateline">
        <div><b>{B.get("n_slate", len(B["board"]) + len(scratches))}</b> games on slate</div>
        <div><b>{len(B["board"])}</b> simulated ({B["n_sims"]:,}× each)</div>
        <div><b>{len(scratches)}</b> scratched (Rule 7)</div>
        <div><b>{n_r8}</b> held by Rule 8</div>
        <div><b>{len(plays)}</b> plays published</div>
        <div><b>{B["published_units"]:g}u</b> exposure (cap 10u)</div>
      </div>
    </div>
    {commit_block}
    <h2 class="sect">Published plays: {B["published_units"]:g}u total exposure</h2>
    <p class="sectsub">The free pick is shown in full. The rest go to premium members before first pitch. Every one of them, winners and losers alike, publishes on the ledger with its full breaker log once graded.</p>
    {upgrade_block}
    <div class="cards">{"".join(card(b, True) if b is free else locked_card(b) for b in plays) or "<p class='sectsub'>None today. Nothing cleared the gates. Passing is a position.</p>"}</div>
    <h2 class="sect">Model leans: no allocation, logged for transparency</h2>
    <p class="sectsub">{n_r8} held by the Rule 8 Divergence Governor, {n_noedge} benched by the edge gate, and the rest below the confidence floor.</p>
    <div class="cards">{"".join(card(b, False) for b in leans)}</div>
    <h2 class="sect">Scratched by circuit breaker</h2>
    <div class="cards">{"".join(scratch_card(s) for s in scratches)}</div>
  </section>

  <section class="tab" id="tab-ledger">
    <h2 class="sect">The public ledger</h2>
    <div class="callout"><strong>The ledger opened July 22, 2026 at 0–0.</strong> No backfilled hot streaks, no screenshots, no deleted losses. Every pick is timestamped and logged before first pitch with the market line and unit size, graded automatically against final scores overnight, and never edited. Judge us on the full column, not the highlight reel.</div>
    <div class="tiles">{tiles}</div>
    <div class="tablewrap">
    <table class="ledger">
      <thead><tr><th>Date</th><th>Game</th><th>Pick</th><th>Edge</th><th>Units</th><th>P&amp;L</th><th>Result</th></tr></thead>
      <tbody>{pending_rows}{graded_rows}</tbody>
    </table>
    </div>
    <p style="font-size:0.78rem;color:var(--muted);margin-top:10px;">Showing today's pending picks and the last 25 graded. A meaningful sample is 500–1,000 picks; anything we say about ROI before then is weather, not climate.</p>
  </section>

  <section class="tab" id="tab-method">
    <h2 class="sect">How the engine works</h2>
    <div class="prose">
      <p>Think of the engine as a <strong>flight simulator for tonight's games</strong>. Instead of predicting one outcome, it plays each game {B["n_sims"]:,} times and counts what happens. A team that wins 6,110 of 10,000 simulations is a 61.1% team, and the fair line falls straight out of the count. No gut feelings, no vibes; arithmetic.</p>
      <h3>What each simulation knows</h3>
      <ul>
        <li><strong>Team run rates:</strong> runs scored and allowed per game for both clubs, live from the MLB Stats API, normalized to the league average.</li>
        <li><strong>Starting pitchers:</strong> each starter's ERA vs league, weighted over the ~5.5 innings starters actually cover; the bullpen inherits the team's overall run prevention.</li>
        <li><strong>Park factors:</strong> Coors Field is a hot-air balloon (1.24); T-Mobile Park is a walk-in freezer (0.92). Static season approximations, refreshed manually.</li>
        <li><strong>Home-field advantage:</strong> an evidence-sized bump (~54% for an even matchup), not the folk-wisdom 60%.</li>
        <li><strong>Fat-tailed scoring:</strong> runs come in bunches, so scores are drawn from a negative binomial distribution that allows crooked innings and blowouts, not a tidy bell curve.</li>
      </ul>
      <h3>The market is on the card</h3>
      <p>A probability alone isn't a bet; a bet is a probability <em>versus a price</em>. Each game carries the market line and three numbers computed from it: <strong>Edge</strong> (our win probability minus the probability implied by the offered price), <strong>EV per unit</strong>, and a <strong>quarter-Kelly</strong> stake suggestion, always capped by the risk-tier framework. The governor beats the gas pedal. Two hard gates follow: the <strong>edge gate</strong> (no allocation under a 2-point edge; a good side at a bad price is a bad bet) and <strong>Rule 8, the Divergence Governor</strong>: when our model and the de-vigged market disagree by more than 12 points, we assume the market knows something our inputs don't (lineups, injury news, form), and the play is held for manual review instead of bet harder. A model that never doubts itself is a tout with extra steps.</p>
      <h3>Then the circuit breakers get a veto</h3>
      <p>The model proposes; eight risk rules dispose. Games with TBD starters are scratched, limited-workload starters trigger downgrades, capped juice forces run-line pivots, and every check, passed or failed, is printed on every card. That's the product.</p>
      <h3>What this engine does <em>not</em> do: read this before betting</h3>
      <ul>
        <li><strong>Market lines are a snapshot</strong>, not a live tick-by-tick feed. Lines move, and the price at your book may differ. Confirm the number before you bet it.</li>
        <li><strong>Telemetry rules are manual.</strong> Statcast velocity/spin trends and rolling road wOBA (Rules 3, 5, 6) aren't automated yet; they're flagged for human review, never silently claimed.</li>
        <li><strong>Not modeled:</strong> lineups, rest, umpires, weather. Park factors are static season approximations.</li>
      </ul>
      <div class="callout">Why publish our limitations next to our picks? Because a pick site that hides its wiring is asking you to bet on a magic trick. Ours is an aquarium; the whole tank is behind glass, down to the per-date random seed ({B["seed"]}) that makes every day's board reproducible.</div>
    </div>
  </section>

  <section class="tab" id="tab-rules">
    <h2 class="sect">The eight circuit breakers</h2>
    <div class="prose"><p>Bankroll rules work like the breaker panel in your house: any single circuit can fail without burning the place down. These run on every pick, every day.</p></div>
    <div class="rulecard"><h3>Rule 2: High-Juice Favorite Cap <span class="badge b-auto">Automated · market lines</span></h3><p>No straight moneylines on road favorites at −180 or heavier, or home favorites past −220, evaluated against the actual market price. The play pivots to the −1.5 run line or passes. Laying heavy juice is renting a favorite at luxury prices: the wins are small and the losses are structural.</p></div>
    <div class="rulecard"><h3>Rule 4: Injury Return Protocol <span class="badge b-auto">Automated (heuristic)</span></h3><p>Mandatory volume freeze on pitchers fresh off the IL or on restricted workloads. Heuristic: a starter far under the expected innings load this deep in the season triggers the freeze and a one-tier confidence downgrade.</p></div>
    <div class="rulecard"><h3>Rule 5: Trailing Telemetry Deviation Penalty <span class="badge b-man">Manual review</span></h3><p>Sharp velocity, spin, or efficiency drops over a starter's trailing three outings trigger a downgrade or fade. Requires a Statcast feed, so it is flagged for human review for now.</p></div>
    <div class="rulecard"><h3>Rule 6: Road wOBA Suppression Multiplier <span class="badge b-man">Manual review</span></h3><p>A 12% projected-scoring tax on visitors whose rolling 3-game road wOBA trails league baseline by .035+. Requires rolling splits, so it is manual for now.</p></div>
    <div class="rulecard"><h3>Rule 7: Late-Line Circuit Breaker <span class="badge b-auto">Automated</span></h3><p>Any game with a TBD starter inside the pre-game window is scratched automatically. No named starter, no position: you don't board a flight with an unnamed pilot.</p></div>
    <div class="rulecard"><h3>Rule 8: Divergence Governor <span class="badge b-auto">Automated</span></h3><p>If the model's win probability and the de-vigged market disagree by more than 12 points, the play is held for manual review, with no allocation, no matter how juicy the "edge" looks. A huge gap isn't a gift; it's a warning that the market has priced in something our inputs haven't seen. The best defense against a model's blind spots is respecting the one opponent that never sleeps.</p></div>
    <div class="rulecard"><h3>Rookie Ambush Overhaul &amp; Two-Out NRFI Override <span class="badge b-na">Dormant</span></h3><p>NRFI/early-under governors (rookie launch-angle variance; #2–3 slot ISO vs high-ride fastballs). Open Ledger doesn't publish NRFI positions yet, so these breakers are dormant until that market ships.</p></div>
    <div class="rulecard"><h3>Edge Gate &amp; Bankroll Governor <span class="badge b-auto">Automated</span></h3><p>No allocation under a 2-point edge versus the offered price. Sizing is the lesser of the confidence tier (3u / 2u / 1u) and a quarter-Kelly stake, with one-tier downgrades on flags and a hard daily ceiling of 10% of bankroll across all published plays.</p></div>
  </section>

  <footer class="legal">
    <p><strong>Open Ledger Sports is an analytics publication, not a sportsbook.</strong> We do not accept, place, or facilitate wagers of any kind. All content is for informational and entertainment purposes only and is not betting advice. No outcome is guaranteed. Anyone promising you a lock is selling you one.</p>
    <p><strong>21+ only.</strong> If you or someone you know has a gambling problem, help is available: call or text <strong>1-800-GAMBLER</strong>. Please check the laws in your jurisdiction before wagering.</p>
    <p>Data: MLB Stats API, snapshot {B["generated_utc"]}. Market lines: {odds_note}. Lines move; verify prices at your sportsbook. Not affiliated with or endorsed by Major League Baseball. Engine {B["n_sims"]:,} simulations per game · seed {B["seed"]} (reproducible).</p>
  </footer>
</div>

<script>
  const tabs = document.querySelectorAll("nav.tabs button");
  const sections = document.querySelectorAll("section.tab");
  function goTab(name) {{
    tabs.forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    sections.forEach(s => s.classList.toggle("active", s.id === "tab-" + name));
    window.scrollTo({{top: 0}});
  }}
  tabs.forEach(btn => btn.addEventListener("click", () => goTab(btn.dataset.tab)));
  document.querySelectorAll("[data-goto]").forEach(el => el.addEventListener("click", e => {{
    e.preventDefault(); goTab(el.dataset.goto);
  }}));
  const hash = location.hash.replace("#", "");
  if (["free","board","ledger","method","rules"].includes(hash)) goTab(hash);
  // Email capture is embedded by its provider's own script when enabled.
</script>
</body>
</html>'''

out = os.path.join(ROOT, "index.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Wrote {out}: {len(html):,} bytes | free pick: {free['pick'] if free else 'NONE'} | {len(plays)} plays")

# Public RSS feed of the free pick only. Built from the same `free` the page
# uses, so it can never contain a premium play. Idempotent by date.
import feed
feed.update(ROOT, DATE, free,
            NICE_DATE if free is None else f"{_date_obj:%A, %B} {_date_obj.day}",
            gen_analysis(free) if free is not None else "",
            B.get("generated_utc", ""), os.environ.get("SITE_URL", ""))
print("Updated feed.xml")
