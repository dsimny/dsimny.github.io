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
       lambda_X = league_rate * attack_X * prevention_Y * park * hfa_adj
     where prevention_Y (v0.4) blends the opposing STARTER's rate over the
     starter's share of the game (default 5.5 IP of 9) with the opposing
     BULLPEN's ERA over the rest, each vs league ERA. Missing reliever ERA
     falls back to the team's overall RA/G. The starter's rate (v0.5) is not
     raw ERA but a stabilized rate: ERA blended with FIP (strips defense/luck)
     and regressed toward league average by innings pitched (small samples pulled
     toward the mean). Missing FIP components fall back to raw ERA.
  5. Runs are drawn from a negative binomial (Gamma-Poisson mixture) to match
     MLB's overdispersed run distribution (variance > mean).
  6. Ties after "regulation" are resolved by simulating extra frames from
     per-inning Poisson rates until the tie breaks.
  7. v0.3: the raw sim prints systematically overconfident moneyline
     probabilities (no bullpen, raw-ERA starters, season-long team rates, no
     lineups). Before pricing any edge we shrink each model win probability
     toward the de-vigged market price (MODEL_WEIGHT). The market is the
     sharpest public MLB estimator there is; blending toward it is the single
     biggest calibration fix in the engine.
Outputs per game: win probabilities, projected score, fair moneyline,
run-line (+/-1.5) cover rates, total-runs distribution, and a fully
transparent list of every circuit-breaker check that fired.

Circuit breakers implemented in code: Rules 2, 4 (heuristic), 6 (detection
only — the run tax is gated behind backtest validation, WOBA_TAX=0), 7.
Rules 3/5 (velocity/spin telemetry) require Statcast feeds not wired — they
are surfaced as "manual review" flags, never silently claimed. NRFI rules are
N/A (no NRFI market yet).
"""

import json, math, os, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np

import crypto_box

DATE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BOARD_DATE", "")
if not DATE:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    DATE = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

ENGINE_VERSION = "0.10-wx-totals"
N_SIMS = 10_000
SEED = int(DATE.replace("-", ""))  # per-date seed: every day's run is reproducible/auditable
STARTER_SHARE = 5.5 / 9  # share of the game credited to the starting pitcher
HFA_RUNS = 1.026         # home team run-rate bump (≈54% HFA overall)
DISPERSION = 2.4         # negative binomial shape (lower = fatter tails)
FACTOR_SHRINK = 0.6      # v0.6: regression-to-mean for TEAM rates. Season RS/RA overstate the
                         # true talent spread (noisy, schedule-unadjusted, mid-season), so we
                         # shrink each attack/prevention factor 40% toward 1.0 (league average) —
                         # the same principle v0.5 applies to pitchers. A full-season backtest
                         # (1414 games) showed the raw model over-dispersed on favourites (the
                         # 0.6-0.7 bucket predicted 64% but went 55%); shrinking dropped Brier
                         # from 0.2504 to ~0.2483, below the no-skill baseline, WITHOUT the
                         # run-total distortion that lowering DISPERSION would cause. Backtest
                         # plateaus 0.5-0.7; 0.6 is the conservative pick. 1.0 = old behaviour.
LOW_IP_THRESHOLD = 60.0  # Rule 4 heuristic: starter under 60 IP this deep in
                         # the season => limited workload / possible IL return

# Rule 6 (Road wOBA Suppression) — v0.9: DETECTION live, ACTION gated.
# Trigger: away team's trailing-14-day wOBA trails the league's by > WOBA_GAP
# (playbook threshold .035). WOBA_TAX is the run-rate reduction applied to the
# away team when the trigger fires. It is 0.0 ON PURPOSE: the playbook
# prescribes 12%, but a hardwired recency tax cuts against the engine's
# validated regression-to-mean philosophy (FACTOR_SHRINK exists because short
# samples mislead), so the tax ships only after a full-season backtest sweep
# (backtest.py --sweep woba_tax:0,0.04,0.08,0.12) shows it helps — and watch
# the totals calibration too, not just moneyline Brier. Do not set non-zero
# by taste.
WOBA_GAP = 0.035
WOBA_TAX = 0.0

# Weather → run environment (v0.10) — TOTALS PAPER TRACK ONLY. The staked
# moneyline board never sees these: main() runs the weather-adjusted sim as a
# SECOND simulation on a SEPARATE rng stream, used solely for total_pick, so
# the real product is bit-identical with or without weather data. Coefficients
# are conservative approximations of public run-scoring research; the totals
# paper ledger (win%/CLV, entries stamped wx_applied) is the validation
# instrument — measuring whether weather beats the opening total is the whole
# point of the track.
WX_BASE_TEMP = 70.0     # °F at which the temperature multiplier is 1.0
WX_TEMP_COEF = 0.004    # +0.4% runs per °F above base; cold days go below 1.0
WX_MULT_MIN, WX_MULT_MAX = 0.90, 1.18   # sanity clamp on each side's COMBINED multiplier.
                                        # 1.18 leaves room for a realistic worst case
                                        # (100°F base 1.12 x 1.05 kicker = 1.176) while
                                        # still bounding a data glitch; 0.90 floors a
                                        # 45°F cold snap.
WX_HOT_TEMP = 85.0      # playbook Thermal/Venue trigger temperature
WX_HOT_KICKER = 0.05    # extra runs OFF a fly-ball starter on a hot day. Adaptation
                        # of the playbook's "35% HR/FB inflation tax": this engine has
                        # no HR/FB component, so the intent (heat hurts fly-ball
                        # pitchers most) maps to a modest bump on the runs scored
                        # against that starter — not a literal 35% of anything.
FLYBALL_AO_MIN = 0.55   # fly-ball starter proxy: airOuts/(airOuts+groundOuts) above
                        # this. Stands in for the playbook's FB%>42% — true batted-ball
                        # FB% isn't in the MLB API's standard stats; the outs mix is.

# Rule 2 thresholds — v0.8 (2026-07-29, docs/SYSTEM_PLAYBOOK.md adoption): a flat
# cap on ANY favorite, tighter for day games. Applied to MARKET lines (real
# prices, real juice). Replaces v0.2's road −180 / home −220 split; the change is
# strictly TIGHTER (home favorites now capped at −180/−170 instead of −220), so
# nothing the old rule blocked is allowed now.
NIGHT_FAV_CAP = -180
DAY_FAV_CAP = -170
DAY_GAME_CUTOFF_HOUR = 17  # first pitch before 5 PM venue-local counts as a day game

# Venue -> IANA timezone, for Rule 2's day/night classification. Static like
# PARK_FACTORS in fetch_data.py; keys must match the snapshot's venue names.
# Unknown venue falls back to America/New_York.
VENUE_TZ = {
    "Coors Field": "America/Denver", "Fenway Park": "America/New_York",
    "Chase Field": "America/Phoenix", "Kauffman Stadium": "America/Chicago",
    "Yankee Stadium": "America/New_York", "Wrigley Field": "America/Chicago",
    "Great American Ball Park": "America/New_York", "Citizens Bank Park": "America/New_York",
    "Angel Stadium": "America/Los_Angeles", "Truist Park": "America/New_York",
    "Rogers Centre": "America/Toronto", "Dodger Stadium": "America/Los_Angeles",
    "American Family Field": "America/Chicago", "Globe Life Field": "America/Chicago",
    "Progressive Field": "America/New_York", "Daikin Park": "America/Chicago",
    "Busch Stadium": "America/Chicago", "Nationals Park": "America/New_York",
    "PNC Park": "America/New_York", "Oracle Park": "America/Los_Angeles",
    "Petco Park": "America/Los_Angeles", "T-Mobile Park": "America/Los_Angeles",
    "Citi Field": "America/New_York", "loanDepot park": "America/New_York",
    "Camden Yards": "America/New_York", "Target Field": "America/Chicago",
    "Comerica Park": "America/Detroit", "Guaranteed Rate Field": "America/Chicago",
    "Rate Field": "America/Chicago", "George M. Steinbrenner Field": "America/New_York",
    "Sutter Health Park": "America/Los_Angeles",
}

# v0.2 market-aware gates
MIN_EDGE = 0.02          # publish only if model prob beats vigged market implied by 2+ pts
DIVERGENCE_CAP = 0.12    # Rule 8 (Divergence Governor): if model vs de-vigged market
                         # disagreement exceeds 12 points, the market almost certainly
                         # knows something our inputs don't (lineups, injury news, form).
                         # Demote to lean + manual review instead of "bet the farm".
KELLY_FRACTION = 0.25    # quarter Kelly
FIP_WEIGHT = 0.5         # v0.5: weight on FIP vs ERA in the starter's stabilized rate.
                         # FIP (defense/luck-independent) is more predictive; ERA reflects
                         # what actually happened. 0.5 splits them; 0.0 = pure ERA (old).
PRIOR_IP = 60.0          # v0.5: regression strength in innings. A starter is weighted
                         # 50/50 with the league at 60 IP, ~3/4 his own rate by 180 IP.
                         # Stops a 20-IP hot streak being trusted at face value.
MODEL_WEIGHT = 0.5       # v0.3 market blend: weight on the sim vs the de-vigged market
                         # when both are available. 0.5 = trust the model and the market
                         # equally. Lower it to lean harder on the market while the sim's
                         # calibration is still unproven; 1.0 reproduces the old v0.2
                         # (pure-model) behaviour. No effect on games with no market line.

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

def first_pitch_passed(utc_str, now_utc):
    """Has this game already started? Unparseable times count as started.

    Erring toward "started" means the worst case is a missed pick, not a
    published one we cannot honestly claim to have made in advance.
    """
    try:
        start = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True
    return start <= now_utc

def flyball_proxy(sp):
    """airOuts share of a starter's outs-on-contact mix; None under 50 recorded
    outs (too thin) or when the snapshot predates v0.10 (no ao/go fields)."""
    ao, go = sp.get("ao") or 0, sp.get("go") or 0
    if ao + go < 50:
        return None
    return ao / (ao + go)

def weather_multipliers(wx, a_sp, h_sp):
    """(m_away, m_home, note) for the totals-track sim. (1, 1, None) for roofed
    parks or missing data. Temperature moves both sides symmetrically; the
    hot-day kicker adds runs only against a fly-ball starter."""
    if not wx or wx.get("roof") or wx.get("temp_f") is None:
        return 1.0, 1.0, None
    t = float(wx["temp_f"])
    base = 1.0 + WX_TEMP_COEF * (t - WX_BASE_TEMP)
    m_away = m_home = base
    kicks = []
    if t >= WX_HOT_TEMP:
        fb_h, fb_a = flyball_proxy(h_sp), flyball_proxy(a_sp)
        if fb_h is not None and fb_h > FLYBALL_AO_MIN:   # heat hurts the HOME starter -> away runs up
            m_away *= 1.0 + WX_HOT_KICKER
            kicks.append(f'home SP fly-ball ({fb_h:.0%} air outs)')
        if fb_a is not None and fb_a > FLYBALL_AO_MIN:
            m_home *= 1.0 + WX_HOT_KICKER
            kicks.append(f'away SP fly-ball ({fb_a:.0%} air outs)')
    m_away = min(max(m_away, WX_MULT_MIN), WX_MULT_MAX)
    m_home = min(max(m_home, WX_MULT_MIN), WX_MULT_MAX)
    note = f'{t:.0f}°F at first pitch: run environment x{base:.3f}'
    if kicks:
        note += f' + {WX_HOT_KICKER:.0%} hot-day kicker vs {", ".join(kicks)}'
    return m_away, m_home, note

def woba_suppression(away, league_woba):
    """Rule 6 trigger: (fired, gap) from the away team's trailing-14d wOBA vs the
    league's over the same window. (None, None) when either side is missing (old
    snapshots, failed fetch): absent data must read as 'unknown', never 'passed'."""
    w = away.get("woba_14d")
    if w is None or league_woba is None:
        return None, None
    gap = league_woba - w
    return gap > WOBA_GAP, gap

def is_day_game(utc_str, venue):
    """Day game = first pitch before DAY_GAME_CUTOFF_HOUR venue-local (MLB's
    convention). Unparseable times count as night: the night cap is the looser
    of the two, so a bad timestamp can only make Rule 2 no stricter than v0.2's
    road cap — never let a data glitch tighten a rule invisibly.
    """
    try:
        start = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    local = start.astimezone(ZoneInfo(VENUE_TZ.get(venue, "America/New_York")))
    return local.hour < DAY_GAME_CUTOFF_HOUR

def fip_constant_from(league_pitching, league_era):
    """FIP constant anchoring league-average FIP to the league run scale, from
    league pitching totals. None when totals are unavailable (older snapshots)."""
    lp = league_pitching
    if not lp or not lp.get("ip"):
        return None
    league_fip_core = (13 * lp["hr"] + 3 * (lp["bb"] + lp["hbp"]) - 2 * lp["k"]) / lp["ip"]
    return league_era - league_fip_core

def stabilized_starter_rate(sp, league_era, fip_constant):
    """Stabilized starter run rate on the league_era scale (v0.5).

    ERA blended with FIP (defense/luck-independent), then regressed toward the
    league by innings pitched: a small sample is pulled toward average, a full
    season keeps most of its own rate. Falls back to raw ERA when FIP components
    or the league constant are missing (older snapshots).
    """
    era = sp["era"]
    ip = sp.get("ip", 0.0) or 0.0
    if fip_constant is not None and ip > 0 and all(k in sp for k in ("hr", "bb", "hbp", "k")):
        fip = (13 * sp["hr"] + 3 * (sp["bb"] + sp["hbp"]) - 2 * sp["k"]) / ip + fip_constant
        skill = FIP_WEIGHT * fip + (1 - FIP_WEIGHT) * era
    else:
        skill = era
    return (ip * skill + PRIOR_IP * league_era) / (ip + PRIOR_IP)

def simulate_game(away, home, a_sp, h_sp, park, league_rate, league_era, fip_constant, rng, n_sims=N_SIMS, league_woba=None, wx_mult=(1.0, 1.0)):
    """Pure Monte Carlo core: expected runs -> N sims -> score/win distributions.

    No market or circuit-breaker logic — just the model, plus the one breaker that
    IS model (Rule 6's away-run tax, inert while WOBA_TAX=0). Shared by the daily
    engine (main) and scripts/backtest.py so both exercise exactly the same model —
    which is what lets the backtest sweep WOBA_TAX honestly. Returns the raw
    run-count arrays plus p_home and the Rule 6 trigger state; callers derive
    whatever markets they need (moneyline, totals, run lines) from the arrays.
    """
    def shrink(factor):
        # pull a rate factor toward 1.0 (league average). FACTOR_SHRINK=1.0 is identity.
        return 1.0 + FACTOR_SHRINK * (factor - 1.0)

    def prevention(starter, team):
        starter_factor = stabilized_starter_rate(starter, league_era, fip_constant) / league_era
        pen = team.get("pen_era")
        pen_factor = (pen / league_era) if pen else (team["ra"] / (team["w"] + team["l"])) / league_rate
        return STARTER_SHARE * starter_factor + (1 - STARTER_SHARE) * pen_factor

    a_attack = shrink((away["rs"] / (away["w"] + away["l"])) / league_rate)
    h_attack = shrink((home["rs"] / (home["w"] + home["l"])) / league_rate)
    lam_away = league_rate * a_attack * shrink(prevention(h_sp, home)) * park / math.sqrt(HFA_RUNS)
    lam_home = league_rate * h_attack * shrink(prevention(a_sp, away)) * park * math.sqrt(HFA_RUNS)

    woba_fired, woba_gap = woba_suppression(away, league_woba)
    if woba_fired and WOBA_TAX > 0:
        lam_away *= 1.0 - WOBA_TAX

    # v0.10 weather multiplier — (1,1) except on the totals-track second sim,
    # so the staked board's lams (and draws) are untouched by weather.
    lam_away *= wx_mult[0]
    lam_home *= wx_mult[1]

    a_runs = nb_draws(rng, lam_away, n_sims)
    h_runs = nb_draws(rng, lam_home, n_sims)

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

    return {"a_runs": a_runs, "h_runs": h_runs, "p_home": float((h_runs > a_runs).mean()),
            "woba_fired": woba_fired, "woba_gap": woba_gap}

def main():
    if crypto_box.already_published(ROOT, DATE) and "--force" not in sys.argv:
        print(f"Board for {DATE} is already published. Nothing to do.")
        return

    data = crypto_box.load_dataset(ROOT, "snapshot", DATE)
    if data is None:
        raise SystemExit(f"No snapshot for {DATE}. Run fetch_data.py first.")

    teams = {int(k): v for k, v in data["teams"].items()}
    pitchers = {int(k): v for k, v in data["pitchers"].items()}
    parks = data["park_factors"]

    total_rs = sum(t["rs"] for t in teams.values())
    total_g = sum(t["w"] + t["l"] for t in teams.values())
    league_rate = total_rs / total_g                     # runs per team-game
    league_era = 9 * sum(t["ra"] for t in teams.values()) / (total_g * 9)  # ≈ RA9

    # FIP constant for the stabilized starter rate (v0.5); None on older snapshots.
    fip_constant = fip_constant_from(data.get("league_pitching"), league_era)

    league_woba = data.get("league_woba_14d")

    rng = np.random.default_rng(SEED)
    # Separate stream for the weather-adjusted totals sims (v0.10): the second
    # simulation must not advance the main stream, or the mere PRESENCE of
    # weather data would shift every later game's draws and change the staked
    # board. Two independent seeded streams keep both reproducible.
    rng_wx = np.random.default_rng(SEED + 1)
    board, scratches = [], []

    now_utc = datetime.now(timezone.utc)

    for g in data["games"]:
        away, home = teams[g["away"]], teams[g["home"]]
        a_sp = pitchers.get(g["awaySP"]) if g["awaySP"] else None
        h_sp = pitchers.get(g["homeSP"]) if g["homeSP"] else None
        park = parks.get(g["venue"], 1.00)
        checks = []

        # ---- Late publication guard ----
        # GitHub's cron is best-effort and has already run five hours late once.
        # The site promises every pick is committed before first pitch, so a game
        # that has already started cannot be published no matter how good it
        # looks. Structurally impossible beats carefully avoided.
        if first_pitch_passed(g["utc"], now_utc):
            scratches.append({
                "gamePk": g["gamePk"],
                "matchup": f'{away["name"]} @ {home["name"]}',
                "abbr": f'{away["abbr"]} @ {home["abbr"]}',
                "utc": g["utc"], "venue": g["venue"],
                "rule": "Late publication guard",
                "reason": ("First pitch had already passed when this board was built, so no "
                           "position was taken. We only publish picks we committed to before "
                           "the game started.")
            })
            continue

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

        # ---- Expected run rates + simulation (shared core) ----
        # Run prevention splits the STARTER (over STARTER_SHARE of the game, at the
        # stabilized ERA/FIP rate) from the team's BULLPEN over the rest — see
        # simulate_game(). backtest.py calls the identical function, so the backtest
        # measures exactly the model that ships.
        sim = simulate_game(away, home, a_sp, h_sp, park, league_rate, league_era, fip_constant, rng,
                            league_woba=league_woba)
        a_runs, h_runs = sim["a_runs"], sim["h_runs"]
        p_home_model = sim["p_home"]
        p_away_model = 1 - p_home_model
        totals = a_runs + h_runs
        mean_total = float(totals.mean())
        # nearest half-run total line for reference
        line = round(mean_total * 2) / 2
        if line == int(line): line += 0.5
        p_over = float((totals > line).mean())
        rl_home_m15 = float(((h_runs - a_runs) > 1.5).mean())   # home -1.5
        rl_away_p15 = float(((a_runs - h_runs) > -1.5).mean())  # away +1.5

        # ---- Market odds (v0.2) ----
        mkt = data.get("odds", {}).get(str(g["gamePk"]))

        # ---- Market blend (v0.3): shrink the model win prob toward the de-vigged market ----
        # Everything downstream (pick side, confidence, fair line, edge, EV, sizing) runs on
        # the BLENDED probability. The raw model prob is kept for the Rule 8 divergence check
        # and for calibration tracking on the ledger. With no market line we fall back to the
        # pure model and MODEL_WEIGHT has no effect.
        p_home_mkt = None
        if mkt:
            imp_a0, imp_h0 = american_to_implied(mkt["away_ml"]), american_to_implied(mkt["home_ml"])
            p_home_mkt = imp_h0 / (imp_a0 + imp_h0)         # de-vigged market home prob
            p_home = MODEL_WEIGHT * p_home_model + (1 - MODEL_WEIGHT) * p_home_mkt
        else:
            p_home = p_home_model
        p_away = 1 - p_home

        fair_home = prob_to_american(p_home)
        fair_away = prob_to_american(p_away)

        # ---- Pick side (chosen on the blended probability) ----
        pick_home = p_home >= p_away
        pick_team = home if pick_home else away
        pick_prob = p_home if pick_home else p_away                    # blended: drives edge/EV/sizing
        pick_prob_model = p_home_model if pick_home else p_away_model  # raw sim, for divergence only
        pick_fair = fair_home if pick_home else fair_away
        pick_label = f'{pick_team["name"]} ML'

        # Market numbers for the pick side
        mkt_odds = edge = ev = kelly_pct = divergence = None
        p_mkt_devig = None
        if mkt:
            mkt_odds = mkt["home_ml"] if pick_home else mkt["away_ml"]
            imp_pick = american_to_implied(mkt_odds)
            p_mkt_devig = p_home_mkt if pick_home else (1 - p_home_mkt)  # vig removed
            edge = pick_prob - imp_pick                     # blended prob vs the price you actually get
            divergence = pick_prob_model - p_mkt_devig      # honest RAW-model-vs-market gap
            b_net = american_to_b(mkt_odds)
            ev = pick_prob * b_net - (1 - pick_prob)        # EV per 1u staked (blended)
            kelly_pct = max(0.0, KELLY_FRACTION * ev / b_net) * 100
            pick_label = f'{pick_team["name"]} ML ({mkt_odds:+d})'

        # ---- Rule 2: High-Juice Favorite Cap (v0.8: flat day/night caps on the MARKET line) ----
        rule2 = False
        day_game = is_day_game(g["utc"], g["venue"])
        fav_cap = DAY_FAV_CAP if day_game else NIGHT_FAV_CAP
        cap_kind = "day" if day_game else "night"
        cap_line = mkt_odds if mkt_odds is not None else pick_fair
        cap_src = "market" if mkt_odds is not None else "model-fair (no market line)"
        if cap_line <= fav_cap:
            rule2 = True
            rl_prob = rl_home_m15 if pick_home else float(((a_runs - h_runs) > 1.5).mean())
            checks.append(f'Rule 2 fired: {cap_src} line {cap_line:+d} exceeds the {cap_kind}-game juice cap ({fav_cap}): pivoted off the moneyline to {pick_team["abbr"]} -1.5 (covers {rl_prob:.1%} of sims).')
            pick_label = f'{pick_team["name"]} -1.5 run line'
            pick_prob = rl_prob
        else:
            checks.append(f'Rule 2 check passed: {cap_src} line {cap_line:+d} within the {cap_kind}-game juice cap ({fav_cap}; night {NIGHT_FAV_CAP}, day {DAY_FAV_CAP}, day = first pitch before {DAY_GAME_CUTOFF_HOUR}:00 venue-local).')

        # ---- Rule 8: Divergence Governor (v0.2) ----
        rule8 = False
        if divergence is not None:
            if abs(divergence) > DIVERGENCE_CAP:
                rule8 = True
                checks.append(f'Rule 8 fired: raw model sees {pick_prob_model:.1%}, de-vigged market says {p_mkt_devig:.1%}: a {abs(divergence)*100:.1f}-point divergence (cap {DIVERGENCE_CAP*100:.0f}). When the model and the market disagree this hard, the market usually knows something our inputs do not (lineups, injury news, form). Demoted to lean pending manual review.')
            else:
                checks.append(f'Rule 8 check passed: raw model {pick_prob_model:.1%} vs de-vigged market {p_mkt_devig:.1%}: {abs(divergence)*100:.1f}-point divergence within the {DIVERGENCE_CAP*100:.0f}-point cap (blended to {pick_prob:.1%} at MODEL_WEIGHT={MODEL_WEIGHT}).')

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

        # ---- Rule 6 (Road wOBA Suppression): detection automated in v0.9, tax gated ----
        wf, wg = sim["woba_fired"], sim["woba_gap"]
        if wf is None:
            checks.append('Rule 6 (Road wOBA Suppression): no 14-day wOBA in this snapshot: manual review.')
        elif wf:
            action = (f'{WOBA_TAX:.0%} run tax applied to the away run rate.' if WOBA_TAX > 0 else
                      'Detection only — the run tax is OFF pending full-season backtest validation, so this flag changes nothing today.')
            checks.append(f'Rule 6 flag: away 14-day wOBA {away["woba_14d"]:.3f} trails league {league_woba:.3f} '
                          f'by {wg:.3f} (threshold {WOBA_GAP:.3f}). {action}')
        else:
            checks.append(f'Rule 6 check passed: away 14-day wOBA {away["woba_14d"]:.3f} vs league {league_woba:.3f} '
                          f'(gap {wg:+.3f}, threshold {WOBA_GAP:.3f}).')

        # ---- Rules 3/5: not automated — say so ----
        checks.append('Rules 3/5 (velocity/spin telemetry): manual review required: Statcast feed not wired.')

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

        # ---- Totals paper track (v0.7): log the model's over/under call vs the market ----
        # NOT staked and NOT in the moneyline exposure — recorded so grade.py can book its
        # W/L and CLV in a SEPARATE ledger (data/totals_ledger.json). The full-season backtest
        # showed the run-total model is well-calibrated; this measures whether that translates
        # into beating the closing total before any real allocation. Side = the one the model
        # rates above the de-vigged market; needs the over/under prices, else stays None.
        total_pick = None
        wx = g.get("wx")
        if mkt and mkt.get("total") is not None and mkt.get("over_price") is not None and mkt.get("under_price") is not None:
            line_t = mkt["total"]
            # v0.10: weather adjusts ONLY this paper pick, via a second sim on its
            # own rng stream. No usable weather -> the vanilla totals, as v0.7.
            wxa, wxh, wx_note = weather_multipliers(wx, a_sp, h_sp)
            wx_applied = (wxa, wxh) != (1.0, 1.0)
            if wx_applied:
                sim_wx = simulate_game(away, home, a_sp, h_sp, park, league_rate, league_era,
                                       fip_constant, rng_wx, league_woba=league_woba, wx_mult=(wxa, wxh))
                totals_wx = sim_wx["a_runs"] + sim_wx["h_runs"]
            else:
                totals_wx = totals
            m_over = float((totals_wx > line_t).mean())
            m_over_nowx = float((totals > line_t).mean())
            io, iu = american_to_implied(mkt["over_price"]), american_to_implied(mkt["under_price"])
            mkt_over_devig = io / (io + iu)
            pick_over = m_over >= mkt_over_devig
            side_price = mkt["over_price"] if pick_over else mkt["under_price"]
            side_model_p = m_over if pick_over else (1 - m_over)
            side_mkt_devig = mkt_over_devig if pick_over else (1 - mkt_over_devig)
            t_b = american_to_b(side_price)
            total_pick = {
                "side": "Over" if pick_over else "Under",
                "line": line_t, "price": side_price,
                "over_price": mkt["over_price"], "under_price": mkt["under_price"],
                "model_p": round(side_model_p, 4), "mkt_devig": round(side_mkt_devig, 4),
                "edge": round(side_model_p - american_to_implied(side_price), 4),
                "ev_per_unit": round(side_model_p * t_b - (1 - side_model_p), 4),
                # weather audit trail: what was applied, and what the pick's prob
                # would have been WITHOUT weather (same side), for the ledger split.
                "wx_applied": wx_applied,
                "wx_mult": [round(wxa, 4), round(wxh, 4)] if wx_applied else None,
                "model_p_nowx": round(m_over_nowx if pick_over else 1 - m_over_nowx, 4),
            }
            if wx_applied:
                checks.append(f'Weather (totals paper track ONLY): {wx_note}. Applied to the paper totals pick; '
                              'the staked board ignores weather entirely.')
            elif wx and wx.get("roof"):
                checks.append('Weather: roofed park, no adjustment (totals paper track).')

        board.append({
            "gamePk": g["gamePk"],
            "matchup": f'{away["name"]} @ {home["name"]}',
            "abbr": f'{away["abbr"]} @ {home["abbr"]}',
            "utc": g["utc"], "venue": g["venue"], "park_factor": park, "wx": wx,
            "away_rec": f'{away["w"]}-{away["l"]}', "home_rec": f'{home["w"]}-{home["l"]}',
            "away_rpg": round(away["rs"]/(away["w"]+away["l"]), 2), "home_rpg": round(home["rs"]/(home["w"]+home["l"]), 2),
            "away_rapg": round(away["ra"]/(away["w"]+away["l"]), 2), "home_rapg": round(home["ra"]/(home["w"]+home["l"]), 2),
            "awaySP": {"name": a_sp["name"], "era": a_sp["era"], "ip": a_sp["ip"], "whip": a_sp["whip"], "k9": a_sp["k9"], "stab_rate": round(stabilized_starter_rate(a_sp, league_era, fip_constant), 2)},
            "homeSP": {"name": h_sp["name"], "era": h_sp["era"], "ip": h_sp["ip"], "whip": h_sp["whip"], "k9": h_sp["k9"], "stab_rate": round(stabilized_starter_rate(h_sp, league_era, fip_constant), 2)},
            "away_pen_era": away.get("pen_era"), "home_pen_era": home.get("pen_era"),
            "p_home": round(p_home, 4), "p_away": round(p_away, 4),
            "p_home_model": round(p_home_model, 4), "p_away_model": round(p_away_model, 4),
            "model_conf": round(pick_prob_model, 4),
            "p_mkt_devig": round(p_mkt_devig, 4) if p_mkt_devig is not None else None,
            "model_weight": MODEL_WEIGHT if mkt else None,
            "proj_away": round(float(a_runs.mean()), 1), "proj_home": round(float(h_runs.mean()), 1),
            "fair_home": fair_home, "fair_away": fair_away,
            "ref_total": line, "p_over": round(p_over, 4), "mean_total": round(mean_total, 1),
            "rl_home_m15": round(rl_home_m15, 4), "rl_away_p15": round(rl_away_p15, 4),
            "pick": pick_label, "pick_team_abbr": pick_team["abbr"],
            "confidence": round(pick_prob, 4),
            "risk_tier": tier, "units": units,
            "rule2_pivot": rule2, "day_game": day_game,
            "rule4_flag": bool(flags4), "rule8_flag": rule8,
            "rule6_flag": wf, "away_woba_14d": away.get("woba_14d"), "league_woba_14d": league_woba,
            "no_edge": no_edge,
            "mkt_odds": mkt_odds, "mkt_total": mkt["total"] if mkt else None,
            "mkt_away_ml": mkt["away_ml"] if mkt else None, "mkt_home_ml": mkt["home_ml"] if mkt else None,
            "p_over_mkt": round(float((totals > mkt["total"]).mean()), 4) if (mkt and mkt.get("total") is not None) else None,
            "total_pick": total_pick,
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
        "engine_version": ENGINE_VERSION, "model_weight": MODEL_WEIGHT,
        "generated_utc": data["snapshot_utc"], "n_sims": N_SIMS, "seed": SEED,
        "odds_source": data.get("odds_source"),
        "n_slate": len(data["games"]),
        "league_rate": round(league_rate, 3), "league_ra9": round(league_era, 2),
        "board": board, "scratches": scratches,
        "published_units": exposure,
        "n_published": len(published),
    }
    board_path, board_sha, enc = crypto_box.save_dataset(ROOT, "board", DATE, out)
    if enc:
        # The fingerprint goes public in the clear while the board itself does
        # not. Anyone can re-hash the revealed board after grading and check it
        # against what we published this morning.
        snap = crypto_box.load_dataset(ROOT, "snapshot", DATE)
        _, recorded = crypto_box.record_commitment(
            ROOT, DATE, board_sha, crypto_box.sha256_of(snap),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        print(f"Board encrypted to {os.path.basename(board_path)}")
        print(f"Commitment {'recorded' if recorded else 'already present, left alone'}: "
              f"sha256 {board_sha[:16]}...")

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
