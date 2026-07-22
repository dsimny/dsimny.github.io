#!/usr/bin/env python3
"""
Open Ledger Sports — MLB Monte Carlo Engine v0.1
=================================================
A real simulation engine: for each game it runs N Monte Carlo simulations of
the final score using team run-scoring/run-prevention rates, starting-pitcher
adjustments, park factors, and home-field advantage, then applies the seven
"circuit breaker" risk rules as post-simulation filters.

Model (documented honestly — this is v0.1, not a black box):
  1. League baseline run rate = total runs / total team-games.
  2. Team attack rate  = RS / G  (normalized vs league).
  3. Team defense rate = RA / G  (normalized vs league).
  4. Expected runs for team X vs opponent Y:
       lambda_X = league_rate * attack_X * defense_Y * park * hfa_adj * sp_adj
     where sp_adj blends the opposing STARTER's ERA vs league ERA over the
     starter's share of the game (default 5.5 IP of 9).
  5. Runs are drawn from a negative binomial (Gamma-Poisson mixture) to match
     MLB's overdispersed run distribution (variance > mean).
  6. Ties after "regulation" are resolved by simulating extra frames from
     per-inning Poisson rates until the tie breaks.
Outputs per game: win probabilities, projected score, fair moneyline,
run-line (+/-1.5) cover rates, total-runs distribution, and a fully
transparent list of every circuit-breaker check that fired.

Circuit breakers implemented in code: Rules 2, 4 (heuristic), 7.
Rules 3/5/6 (velocity/spin telemetry, road wOBA) require Statcast feeds not
wired in v0.1 — they are surfaced as "manual review" flags, never silently
claimed. NRFI rules are N/A (no NRFI market in v0.1).
"""

import json, math, os, sys
import numpy as np

DATE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BOARD_DATE", "")
if not DATE:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    DATE = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

N_SIMS = 10_000
SEED = int(DATE.replace("-", ""))  # per-date seed: every day's run is reproducible/auditable
STARTER_SHARE = 5.5 / 9  # share of the game credited to the starting pitcher
HFA_RUNS = 1.026         # home team run-rate bump (≈54% HFA overall)
DISPERSION = 2.4         # negative binomial shape (lower = fatter tails)
LOW_IP_THRESHOLD = 60.0  # Rule 4 heuristic: starter under 60 IP this deep in
                         # the season => limited workload / possible IL return

# Rule 2 thresholds — v0.2: applied to MARKET lines (real prices, real juice)
ROAD_FAV_CAP = -180
HOME_FAV_CAP = -220

# v0.2 market-aware gates
MIN_EDGE = 0.02          # publish only if model prob beats vigged market implied by 2+ pts
DIVERGENCE_CAP = 0.12    # Rule 8 (Divergence Governor): if model vs de-vigged market
                         # disagreement exceeds 12 points, the market almost certainly
                         # knows something our inputs don't (lineups, injury news, form).
                         # Demote to lean + manual review instead of "bet the farm".
KELLY_FRACTION = 0.25    # quarter Kelly

# Unit sizing per the Open Ledger risk framework
def risk_tier(conf):
    if conf >= 0.80: return ("Low Risk (Safe Play)", 3.0)
    if conf >= 0.70: return ("Moderate Risk (Value Play)", 2.0)
    if conf >= 0.60: return ("High Risk (Longshot)", 1.0)
    return ("Pass", 0.0)

def prob_to_american(p):
    if p <= 0 or p >= 1: return None
    if p > 0.5: return int(round(-100 * p / (1 - p)))
    return int(round(100 * (1 - p) / p))

def american_to_implied(odds):
    """Implied win probability of American odds (includes the vig)."""
    return (-odds) / (-odds + 100) if odds < 0 else 100 / (odds + 100)

def american_to_b(odds):
    """Net decimal payout per 1 staked (decimal odds minus 1)."""
    return 100 / (-odds) if odds < 0 else odds / 100

def nb_draws(rng, mean, n):
    """Negative binomial via Gamma-Poisson mixture."""
    lam = rng.gamma(shape=DISPERSION, scale=mean / DISPERSION, size=n)
    return rng.poisson(lam)

def main():
    with open(os.path.join(ROOT, "data", f"snapshot_{DATE}.json"), encoding="utf-8") as f:
        data = json.load(f)

    teams = {int(k): v for k, v in data["teams"].items()}
    pitchers = {int(k): v for k, v in data["pitchers"].items()}
    parks = data["park_factors"]

    total_rs = sum(t["rs"] for t in teams.values())
    total_g = sum(t["w"] + t["l"] for t in teams.values())
    league_rate = total_rs / total_g                     # runs per team-game
    league_era = 9 * sum(t["ra"] for t in teams.values()) / (total_g * 9)  # ≈ RA9

    rng = np.random.default_rng(SEED)
    board, scratches = [], []

    for g in data["games"]:
        away, home = teams[g["away"]], teams[g["home"]]
        a_sp = pitchers.get(g["awaySP"]) if g["awaySP"] else None
        h_sp = pitchers.get(g["homeSP"]) if g["homeSP"] else None
        park = parks.get(g["venue"], 1.00)
        checks = []

        # ---- Rule 7: Late-Line Circuit Breaker (TBD starter => scratch) ----
        if a_sp is None or h_sp is None:
            side = []
            if a_sp is None: side.append(away["abbr"])
            if h_sp is None: side.append(home["abbr"])
            scratches.append({
                "gamePk": g["gamePk"],
                "matchup": f'{away["name"]} @ {home["name"]}',
                "abbr": f'{away["abbr"]} @ {home["abbr"]}',
                "utc": g["utc"], "venue": g["venue"],
                "rule": "Rule 7: Late-Line Circuit Breaker",
                "reason": f'Starter TBD for {", ".join(side)} inside the pre-game window. Automatic scratch, no position.'
            })
            continue

        # ---- Expected run rates ----
        def sp_adj(starter):
            return STARTER_SHARE * (starter["era"] / league_era) + (1 - STARTER_SHARE)

        a_attack = (away["rs"] / (away["w"] + away["l"])) / league_rate
        h_attack = (home["rs"] / (home["w"] + home["l"])) / league_rate
        a_def = (away["ra"] / (away["w"] + away["l"])) / league_rate
        h_def = (home["ra"] / (home["w"] + home["l"])) / league_rate

        lam_away = league_rate * a_attack * h_def * sp_adj(h_sp) * park / math.sqrt(HFA_RUNS)
        lam_home = league_rate * h_attack * a_def * sp_adj(a_sp) * park * math.sqrt(HFA_RUNS)

        # ---- Simulate ----
        a_runs = nb_draws(rng, lam_away, N_SIMS)
        h_runs = nb_draws(rng, lam_home, N_SIMS)

        ties = a_runs == h_runs
        n_ties = int(ties.sum())
        if n_ties:
            # extra innings: per-inning Poisson until decided
            ta = lam_away / 9 * 1.9  # ghost-runner era inflates XI scoring
            th = lam_home / 9 * 1.9
            xa, xh = a_runs[ties].copy(), h_runs[ties].copy()
            undecided = np.ones(n_ties, dtype=bool)
            while undecided.any():
                da = rng.poisson(ta, undecided.sum())
                dh = rng.poisson(th, undecided.sum())
                xa[undecided] += da
                xh[undecided] += dh
                undecided_idx = np.where(undecided)[0]
                still = da == dh
                undecided[undecided_idx[~still]] = False
            a_runs[ties], h_runs[ties] = xa, xh

        p_home = float((h_runs > a_runs).mean())
        p_away = 1 - p_home
        totals = a_runs + h_runs
        mean_total = float(totals.mean())
        # nearest half-run total line for reference
        line = round(mean_total * 2) / 2
        if line == int(line): line += 0.5
        p_over = float((totals > line).mean())
        rl_home_m15 = float(((h_runs - a_runs) > 1.5).mean())   # home -1.5
        rl_away_p15 = float(((a_runs - h_runs) > -1.5).mean())  # away +1.5

        fair_home = prob_to_american(p_home)
        fair_away = prob_to_american(p_away)

        # ---- Market odds (v0.2) ----
        mkt = data.get("odds", {}).get(str(g["gamePk"]))

        # ---- Pick side ----
        pick_home = p_home >= p_away
        pick_team = home if pick_home else away
        pick_prob = p_home if pick_home else p_away
        pick_fair = fair_home if pick_home else fair_away
        pick_label = f'{pick_team["name"]} ML'

        # Market numbers for the pick side
        mkt_odds = edge = ev = kelly_pct = divergence = None
        p_mkt_devig = None
        if mkt:
            mkt_odds = mkt["home_ml"] if pick_home else mkt["away_ml"]
            imp_pick = american_to_implied(mkt_odds)
            imp_a, imp_h = american_to_implied(mkt["away_ml"]), american_to_implied(mkt["home_ml"])
            p_mkt_devig = (imp_h if pick_home else imp_a) / (imp_a + imp_h)  # vig removed
            edge = pick_prob - imp_pick                     # vs the price you actually get
            divergence = pick_prob - p_mkt_devig            # honest model-vs-market gap
            b_net = american_to_b(mkt_odds)
            ev = pick_prob * b_net - (1 - pick_prob)        # EV per 1u staked
            kelly_pct = max(0.0, KELLY_FRACTION * ev / b_net) * 100
            pick_label = f'{pick_team["name"]} ML ({mkt_odds:+d})'

        # ---- Rule 2: High-Juice Favorite Cap (v0.2: on the MARKET line) ----
        rule2 = False
        cap_line = mkt_odds if mkt_odds is not None else pick_fair
        cap_src = "market" if mkt_odds is not None else "model-fair (no market line)"
        if (not pick_home and cap_line <= ROAD_FAV_CAP) or (pick_home and cap_line <= HOME_FAV_CAP):
            rule2 = True
            rl_prob = rl_home_m15 if pick_home else float(((a_runs - h_runs) > 1.5).mean())
            checks.append(f'Rule 2 fired: {cap_src} line {cap_line:+d} exceeds juice cap: pivoted off the moneyline to {pick_team["abbr"]} -1.5 (covers {rl_prob:.1%} of sims).')
            pick_label = f'{pick_team["name"]} -1.5 run line'
            pick_prob = rl_prob
        else:
            checks.append(f'Rule 2 check passed: {cap_src} line {cap_line:+d} within juice caps (road {ROAD_FAV_CAP}, home {HOME_FAV_CAP}).')

        # ---- Rule 8: Divergence Governor (v0.2) ----
        rule8 = False
        if divergence is not None:
            if abs(divergence) > DIVERGENCE_CAP:
                rule8 = True
                checks.append(f'Rule 8 fired: model sees {pick_prob:.1%}, de-vigged market says {p_mkt_devig:.1%}: a {abs(divergence)*100:.1f}-point divergence (cap {DIVERGENCE_CAP*100:.0f}). When the model and the market disagree this hard, the market usually knows something our inputs do not (lineups, injury news, form). Demoted to lean pending manual review.')
            else:
                checks.append(f'Rule 8 check passed: model {pick_prob:.1%} vs de-vigged market {p_mkt_devig:.1%}: {abs(divergence)*100:.1f}-point divergence within the {DIVERGENCE_CAP*100:.0f}-point cap.')

        # ---- Edge gate (v0.2) ----
        no_edge = False
        if edge is not None and edge < MIN_EDGE and not rule8:
            no_edge = True
            checks.append(f'Edge gate: model edge vs offered price is {edge*100:+.1f} pts (minimum {MIN_EDGE*100:.0f}). No allocation; a good side at a bad price is a bad bet.')

        # ---- Rule 4 heuristic: limited-workload starters ----
        flags4 = [sp["name"] for sp in (a_sp, h_sp) if sp["ip"] < LOW_IP_THRESHOLD]
        downgraded = False
        if flags4:
            downgraded = True
            checks.append(f'Rule 4 flag: {", ".join(flags4)} under {LOW_IP_THRESHOLD:.0f} IP this deep in the season (limited workload / possible IL return). Volume freeze: confidence downgraded one tier.')
        else:
            checks.append('Rule 4 check passed: both starters carry full-season workloads.')

        # ---- Rules 3/5/6: not automated in v0.1 — say so ----
        checks.append('Rules 3/5/6 (velocity/spin telemetry, road wOBA suppression): manual review required: Statcast feed not wired in v0.1.')

        tier, units = risk_tier(pick_prob)
        if downgraded and units > 0:
            units = max(units - 1.0, 0.5)
            if tier.startswith("Low"): tier = "Moderate Risk (Value Play)"
            elif tier.startswith("Moderate"): tier = "High Risk (Longshot)"
        # v0.2: market gates override the confidence tiers
        if rule8 or no_edge:
            tier, units = "Pass", 0.0
        elif kelly_pct is not None and units > 0:
            # units = the LESSER of the tier cap and quarter-Kelly (rounded to 0.5u)
            units = min(units, max(0.5, round(kelly_pct * 2) / 2))

        board.append({
            "gamePk": g["gamePk"],
            "matchup": f'{away["name"]} @ {home["name"]}',
            "abbr": f'{away["abbr"]} @ {home["abbr"]}',
            "utc": g["utc"], "venue": g["venue"], "park_factor": park,
            "away_rec": f'{away["w"]}-{away["l"]}', "home_rec": f'{home["w"]}-{home["l"]}',
            "away_rpg": round(away["rs"]/(away["w"]+away["l"]), 2), "home_rpg": round(home["rs"]/(home["w"]+home["l"]), 2),
            "away_rapg": round(away["ra"]/(away["w"]+away["l"]), 2), "home_rapg": round(home["ra"]/(home["w"]+home["l"]), 2),
            "awaySP": {"name": a_sp["name"], "era": a_sp["era"], "ip": a_sp["ip"], "whip": a_sp["whip"], "k9": a_sp["k9"]},
            "homeSP": {"name": h_sp["name"], "era": h_sp["era"], "ip": h_sp["ip"], "whip": h_sp["whip"], "k9": h_sp["k9"]},
            "p_home": round(p_home, 4), "p_away": round(p_away, 4),
            "proj_away": round(float(a_runs.mean()), 1), "proj_home": round(float(h_runs.mean()), 1),
            "fair_home": fair_home, "fair_away": fair_away,
            "ref_total": line, "p_over": round(p_over, 4), "mean_total": round(mean_total, 1),
            "rl_home_m15": round(rl_home_m15, 4), "rl_away_p15": round(rl_away_p15, 4),
            "pick": pick_label, "pick_team_abbr": pick_team["abbr"],
            "confidence": round(pick_prob, 4),
            "risk_tier": tier, "units": units,
            "rule2_pivot": rule2, "rule4_flag": bool(flags4), "rule8_flag": rule8,
            "no_edge": no_edge,
            "mkt_odds": mkt_odds, "mkt_total": mkt["total"] if mkt else None,
            "mkt_away_ml": mkt["away_ml"] if mkt else None, "mkt_home_ml": mkt["home_ml"] if mkt else None,
            "p_over_mkt": round(float((totals > mkt["total"]).mean()), 4) if mkt else None,
            "edge": round(edge, 4) if edge is not None else None,
            "ev_per_unit": round(ev, 4) if ev is not None else None,
            "kelly_pct": round(kelly_pct, 2) if kelly_pct is not None else None,
            "divergence": round(divergence, 4) if divergence is not None else None,
            "checks": checks,
            "n_sims": N_SIMS,
        })

    # Daily exposure cap: 10% of bankroll
    board_sorted = sorted([b for b in board if b["units"] > 0], key=lambda b: -b["confidence"])
    exposure, published = 0.0, []
    for b in board_sorted:
        if exposure + b["units"] <= 10.0:
            exposure += b["units"]; b["published"] = True; published.append(b)
        else:
            b["published"] = False
            b["checks"].append("Daily exposure cap: 10% bankroll ceiling reached; logged as model lean only, no allocation.")

    out = {
        "date": DATE,
        "generated_utc": data["snapshot_utc"], "n_sims": N_SIMS, "seed": SEED,
        "odds_source": data.get("odds_source"),
        "n_slate": len(data["games"]),
        "league_rate": round(league_rate, 3), "league_ra9": round(league_era, 2),
        "board": board, "scratches": scratches,
        "published_units": exposure,
        "n_published": len(published),
    }
    with open(os.path.join(ROOT, "data", f"board_{DATE}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    print(f"League run rate: {league_rate:.3f} r/g | League RA9: {league_era:.2f}")
    print(f"[{DATE}] Simulated {len(board)} games x {N_SIMS:,} sims | {len(scratches)} scratched (Rule 7)")
    print(f"Published allocations: {len(published)} plays, {exposure:.1f}u total (cap 10u)\n")
    for b in sorted(board, key=lambda b: -b["confidence"]):
        tag = "PLAY " if b.get("published") else "LEAN "
        mkt_s = f'{b["mkt_odds"]:+d}' if b["mkt_odds"] is not None else "n/a"
        edge_s = f'{b["edge"]*100:+.1f}' if b["edge"] is not None else "n/a"
        print(f'{tag}{b["abbr"]:<12} {b["pick"]:<34} conf {b["confidence"]:.1%}  mkt {mkt_s:>5}  edge {edge_s:>5}  {b["units"]}u'
              f'{"  [R2]" if b["rule2_pivot"] else ""}{"  [R4]" if b["rule4_flag"] else ""}{"  [R8 DIVERGENCE]" if b["rule8_flag"] else ""}{"  [no edge]" if b["no_edge"] else ""}')
    for s in scratches:
        print(f'SCRATCH {s["abbr"]:<12} {s["rule"]}')

if __name__ == "__main__":
    main()
