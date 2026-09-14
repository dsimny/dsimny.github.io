#!/usr/bin/env python3
"""
D.J. Mercer developmental cohorts - the public record page.

    football/mercer/developmental/index.html

NFL AND NCAA ARE NEVER COMBINED. Each cohort gets its own section, its own
tiles and its own table, and there is no combined total anywhere on the page.

NOTHING ACTIONABLE BEFORE KICKOFF. A released play whose game has not started is
shown from its public commitment and receipt only - matchup, start time, release
time and artifact hash. Selection, price, book and reasoning appear once the
publication row enters the ledger at kickoff; reasoning once the play is graded
and its artifact revealed.

UNAVAILABLE MEANS UNAVAILABLE. A metric the data cannot support is printed as
"unavailable" with its reason. Nothing is estimated to fill a gap.

Reads data/mercer_dev only. Writes football/mercer/developmental/ only - never
the root index.html or feed.xml.
"""
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "football"))

import mdcore                                        # noqa: E402

E = html.escape
OUT_REL = os.path.join("football", "mercer", "developmental")
RESEARCH = os.path.join("data", "mercer_dev", "research", "nfl_v1_dev_eval.json")


def _fmt_units(u):
    return f"{u:+.2f}u"


def status_block(sport, now):
    sid = mdcore.SPORTS[sport]["strategy_id"]
    try:
        reg, entry = mdcore.active_registration(sport, now)
    except mdcore.IntegrityError:
        return ('<p class="bad"><strong>Registration integrity failure.</strong> The registration '
                'file no longer matches its logged hash. Nothing may publish.</p>')
    logged = mdcore.log_entries(sid)
    ctl = mdcore.load_json(os.path.join(mdcore.DATA, "control.json"),
                           {"publication_enabled": False, "sports": {}})
    live = ctl.get("publication_enabled") is True and ctl.get("sports", {}).get(sport) is True
    if reg:
        reg_txt = (f'Registered version {reg["version"]}, effective {E(entry["effective_utc"])}, '
                   f'<code>sha256 {E(entry["sha256"])}</code>')
    elif logged:
        e = logged[0]
        reg_txt = (f'Registered version {e["version"]}, effective {E(e["effective_utc"])} '
                   f'(not yet effective), <code>sha256 {E(e["sha256"])}</code>')
    else:
        reg_txt = "Proposed, not yet registered. No play can be released."
    pub_txt = ("Publication switched on for this cohort." if live else
               "Publication is off. No developmental play is released until commissioning is complete.")
    return f'<p class="mut">{reg_txt}<br>{pub_txt}</p>'


def tiles(agg):
    clv = agg["clv"]
    if clv["n"]:
        clv_val = f'{clv["mean_pts"]:+.2f} pts'
        clv_sub = f'{clv["n"]} of {clv["of_graded"]} graded plays · beat the close {clv["beat_close_pct"]:g}%'
    else:
        clv_val = "unavailable"
        clv_sub = "no graded play has a usable closing line yet"
    if clv["unavailable"]:
        clv_sub += " · unavailable for " + "; ".join(f"{n}: {E(r)}" for r, n in clv["unavailable"].items())
    roi = f'{agg["roi_pct"]:+.1f}%' if agg["roi_pct"] is not None else "unavailable"
    roi_sub = (f'{_fmt_units(agg["units"])} on {agg["risked_units"]:g}u risked'
               if agg["risked_units"] else "no graded play yet")
    return (
        '<div class="tiles">'
        f'<div class="tile"><span class="tl">Record</span><span class="tv">{E(agg["record"])}</span>'
        f'<span class="td">{agg["graded"]} graded · {agg["pending"]} pending result · {agg["voids"]} void</span></div>'
        f'<div class="tile"><span class="tl">Units / ROI</span><span class="tv">{roi}</span>'
        f'<span class="td">{roi_sub} · flat 0.25u</span></div>'
        f'<div class="tile"><span class="tl">Closing-line value</span><span class="tv">{clv_val}</span>'
        f'<span class="td">{clv_sub}</span></div>'
        '</div>')


def released_not_started(sport, now):
    """Receipts whose game has not started: proof only, nothing actionable."""
    rows = []
    ddir = os.path.join(mdcore.DATA, "deliveries")
    for name in sorted(os.listdir(ddir)) if os.path.isdir(ddir) else []:
        if not name.endswith(".delivered.json"):
            continue
        r = mdcore.load_json(os.path.join(ddir, name))
        start = mdcore.parse_utc(r.get("scheduled_start_utc"))
        if r.get("sport") != sport or not start or start <= now:
            continue
        rows.append(r)
    if not rows:
        return ""
    commits = {c["play_id"]: c for c in mdcore.load_json(
        os.path.join(mdcore.DATA, "commitments.json"), {"commitments": []})["commitments"]}
    body = "".join(
        f'<tr><td>{E((commits.get(r["play_id"]) or {}).get("matchup", "—"))}</td>'
        f'<td>{E(r["scheduled_start_utc"])}</td><td>{E(r["published_utc"])}</td>'
        f'<td>{"★ Spotlight" if r.get("featured") else ""}</td>'
        f'<td><code>{E(r["artifact_sha256"][:16])}…</code></td></tr>' for r in rows)
    return ('<h3>Released, game not started</h3>'
            '<p class="mut">The selection is withheld from the public page until kickoff. The '
            'artifact hash was committed before release; hash the revealed artifact later and compare.</p>'
            '<div class="tablewrap"><table class="ledger"><tr><th>Matchup</th><th>Start (UTC)</th>'
            f'<th>Released</th><th></th><th>Artifact</th></tr>{body}</table></div>')


def graded_table(led):
    sets = mdcore.effective_settlements(led)
    rows = []
    for p in sorted(mdcore.publications(led), key=lambda r: r["scheduled_start_utc"], reverse=True):
        s = sets.get(p["play_id"])
        sel = E(p["selection"]) + (f' {p["line"]:+g}' if p["market"] == "spread" and p["line"] is not None
                                    else (f' {p["line"]:g}' if p["line"] is not None else ""))
        if s is None:
            res, pnl, clv = "pending", "—", "—"
        else:
            res = s["result"] + (f' ({E(s["void_reason"])})' if s["result"] == "VOID" else "")
            pnl = _fmt_units(float(s["profit_units"]))
            clv = (f'{s["clv_pts"]:+.2f}' if isinstance(s.get("clv_pts"), (int, float))
                   else f'unavailable — {E(s.get("clv_unavailable_reason") or "no closing line")}')
        rows.append(
            f'<tr><td>{E(p["scheduled_start_utc"][:10])}</td><td>{E(p["matchup"])}'
            f'{" ★ Spotlight" if p.get("featured") else ""}</td>'
            f'<td>{E(p["market"])}: {sel} {p["odds"]:+d} ({E(p["book"])})</td>'
            f'<td>{p["recommended_units"]:g}u</td><td>{res}</td><td>{pnl}</td><td>{clv}</td>'
            f'<td><code>{E(p["play_id"])}</code></td></tr>')
    if not rows:
        return '<p class="mut">No play has been released in this cohort.</p>'
    return ('<div class="tablewrap"><table class="ledger"><tr><th>Date</th><th>Matchup</th>'
            '<th>Selection</th><th>Stake</th><th>Result</th><th>P/L</th><th>CLV (pts)</th>'
            f'<th>Play ID</th></tr>{"".join(rows)}</table></div>')


def research_block(sport):
    if sport == "ncaaf":
        return ('<p class="mut"><strong>Historical research:</strong> none. The repository holds no '
                'historical college data, and no NCAA model exists or is claimed.</p>')
    ev = mdcore.load_json(os.path.join(mdcore.ROOT, RESEARCH))
    if not ev:
        return ""
    ch = ev["chosen_model_rule"]["validation"]["bets"]["n"] + ev["chosen_model_rule"]["train"]["bets"]["n"]
    games = ev["n_rows"]["train"] + ev["n_rows"]["validation"]
    return ('<p class="mut"><strong>Historical research — separate from the record above, not '
            'an untouched test.</strong> A market-anchored NFL model evaluated on 2022–2024 '
            f'(seasons already scored by earlier studies) found no independent edge: the best weight on '
            f'the model was {ev["chosen_on_train"]["w"]:g}, and its pre-set rule selected {ch} plays in '
            f'{games} games. No model selects or sizes these plays.</p>')


def render(now=None):
    import mercer                                       # shared page shell, nav and legal footer
    now = now or mdcore.now_utc()
    sections = []
    for sport, label in (("nfl", "NFL"), ("ncaaf", "NCAA")):
        sid = mdcore.SPORTS[sport]["strategy_id"]
        led = mdcore.load_ledger(sid)
        agg = mdcore.aggregates(led)
        sections.append(
            f'<section class="cohort" id="{sport}"><h2>{E(mdcore.SPORTS[sport]["name"])}</h2>'
            f'{status_block(sport, now)}{tiles(agg)}{released_not_started(sport, now)}'
            f'<h3>{label} record</h3>{graded_table(led)}{research_block(sport)}</section>')
    inner = f'''
<div class="idx">
  <span class="kicker">D.J. Mercer · developmental cohorts</span>
  <h1>The developmental records</h1>
  <p class="lede">Two separate records, one for NFL and one for college football. Selections are
  human-researched by D.J. Mercer, supported by market and analytical data. <strong>Neither is a
  proven model, and no model edge is claimed.</strong> Every released play is fingerprinted before
  release, approved one at a time, flat-staked at 0.25 units, and graded here win or lose.</p>
  <p class="mut">When the weekly D.J. Mercer Spotlight pick is in one of these sports, it is recorded
  here, in that sport's record only, marked ★ Spotlight. It never also counts on the Spotlight
  ledger. Records begin at each cohort's effective date; nothing earlier is added.</p>
  {"".join(sections)}
  <p class="rgline"><strong>21+.</strong> Open Ledger Sports is an analytics site, not a sportsbook,
  and does not accept wagers. No outcome is guaranteed. Gambling problem? Call 1-800-GAMBLER.</p>
  <p class="backline"><a href="/football/mercer/">D.J. Mercer Spotlight</a> ·
  <a href="/football/">Model football record</a></p>
</div>'''
    out = os.path.join(mdcore.ROOT, OUT_REL)
    mercer.write(out, inner, "D.J. Mercer developmental cohorts — Open Ledger Sports",
                 "Separate NFL and NCAA developmental records: human-researched selections, "
                 "approved one at a time and graded in public, win or lose.")
    return os.path.join(out, "index.html")


if __name__ == "__main__":
    print(f"wrote {os.path.relpath(render(), mdcore.ROOT)}")
