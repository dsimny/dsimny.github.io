#!/usr/bin/env python3
"""
Open Ledger Sports — daily data fetch.
Pulls today's MLB slate, standings, and probable-pitcher season stats from the
MLB Stats API, plus (optionally) live market odds from The Odds API when an
ODDS_API_KEY env var / repo secret is present. Writes data/snapshot_<date>.json
in the exact shape engine.py consumes.

Run: python scripts/fetch_data.py [YYYY-MM-DD]
"""
import json, os, statistics, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

import crypto_box
import season

DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SEASON = int(DATE[:4])
MLB = "https://statsapi.mlb.com/api/v1"

# Static season-level approximations — refresh from Baseball Savant seasonally.
PARK_FACTORS = {
    "Coors Field": 1.24, "Fenway Park": 1.06, "Chase Field": 1.05, "Kauffman Stadium": 1.03,
    "Yankee Stadium": 1.03, "Wrigley Field": 1.02, "Great American Ball Park": 1.04,
    "Citizens Bank Park": 1.01, "Angel Stadium": 1.00, "Truist Park": 1.00, "Rogers Centre": 1.00,
    "Dodger Stadium": 0.98, "American Family Field": 0.99, "Globe Life Field": 0.98,
    "Progressive Field": 0.97, "Daikin Park": 0.97, "Busch Stadium": 0.97, "Nationals Park": 0.99,
    "PNC Park": 0.97, "Oracle Park": 0.94, "Petco Park": 0.95, "T-Mobile Park": 0.92,
    "Citi Field": 0.96, "loanDepot park": 0.95, "Camden Yards": 1.00, "Target Field": 0.99,
    "Comerica Park": 0.97, "Guaranteed Rate Field": 1.03, "Rate Field": 1.03,
    "George M. Steinbrenner Field": 1.06, "Sutter Health Park": 1.02,
}

# Approximate park coordinates + roof flag for the game-time weather fetch
# (v0.10, totals paper track only). Roofed/retractable parks get no weather:
# climate-controlled when closed, and whether it WILL be closed isn't knowable
# pre-game. Keys match PARK_FACTORS.
PARK_COORDS = {
    "Coors Field": (39.756, -104.994, False), "Fenway Park": (42.346, -71.097, False),
    "Chase Field": (33.445, -112.067, True), "Kauffman Stadium": (39.051, -94.480, False),
    "Yankee Stadium": (40.829, -73.926, False), "Wrigley Field": (41.948, -87.655, False),
    "Great American Ball Park": (39.097, -84.507, False), "Citizens Bank Park": (39.906, -75.166, False),
    "Angel Stadium": (33.800, -117.883, False), "Truist Park": (33.891, -84.468, False),
    "Rogers Centre": (43.641, -79.389, True), "Dodger Stadium": (34.074, -118.240, False),
    "American Family Field": (43.028, -87.971, True), "Globe Life Field": (32.747, -97.084, True),
    "Progressive Field": (41.496, -81.685, False), "Daikin Park": (29.757, -95.356, True),
    "Busch Stadium": (38.623, -90.193, False), "Nationals Park": (38.873, -77.007, False),
    "PNC Park": (40.447, -80.006, False), "Oracle Park": (37.778, -122.389, False),
    "Petco Park": (32.707, -117.157, False), "T-Mobile Park": (47.591, -122.332, True),
    "Citi Field": (40.757, -73.846, False), "loanDepot park": (25.778, -80.220, True),
    "Camden Yards": (39.284, -76.622, False), "Target Field": (44.982, -93.278, False),
    "Comerica Park": (42.339, -83.049, False), "Guaranteed Rate Field": (41.830, -87.634, False),
    "Rate Field": (41.830, -87.634, False), "George M. Steinbrenner Field": (27.980, -82.507, False),
    "Sutter Health Park": (38.580, -121.513, False),
}

def get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

# ---- The Odds API budget (2026-08-17) ----------------------------------------
# The free tier is 500 credits a month and the /odds endpoint bills ONE CREDIT
# PER MARKET PER REGION per call — so this string is the run's price tag, not a
# preference. "h2h,totals" over one region costs 2 credits a call. Adding
# spreads would cost 50% more for a market nothing is staked on today.
#
# Credits were NOT the cause of the 2026-08-17 outage (the morning workflow never
# started at all), but the run was flying blind on them: nothing logged, nothing
# stored, no warning before exhaustion. Now every run records what it spent and
# what is left. The arithmetic and the upgrade rule live in CLAUDE.md.
ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
ODDS_MARKETS = os.environ.get("ODDS_MARKETS", "h2h,totals").strip()
ODDS_REGIONS = os.environ.get("ODDS_REGIONS", "us").strip()
CREDITS_LOW = 50          # warn below this, with ~5 days of headroom left at 8/day

def fetch_odds(key, credits_out):
    """The odds call. Returns the events; fills credits_out with the balance.

    The Odds API reports the running balance on every response, including the
    error ones. credits_out is filled BEFORE raise_for_status, so the readings
    that matter most — the 401 that says the key died, the 429 that says the
    month is spent — survive the exception instead of being lost with it.
    """
    r = requests.get(ODDS_URL, timeout=30, params={
        "apiKey": key, "regions": ODDS_REGIONS,
        "markets": ODDS_MARKETS, "oddsFormat": "american"})
    def _int(name):
        try:
            return int(r.headers.get(name))
        except (TypeError, ValueError):
            return None
    credits_out.update({
        "remaining": _int("x-requests-remaining"),
        "used": _int("x-requests-used"),
        "last_call_cost": _int("x-requests-last"),
        "markets": ODDS_MARKETS, "regions": ODDS_REGIONS,
        "http_status": r.status_code, "source": "fetch_data",
        "read_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    r.raise_for_status()
    return r.json()


CREDIT_LOG_KEEP = 60

def record_credits(credits):
    """Append the reading to data/odds_credits.json, IN THE CLEAR.

    The snapshot carries the same numbers, but the snapshot is encrypted until
    grading reveals it — so it cannot be what makes the balance "visible in the
    repo". This file can, and it costs nothing: no picks, no model output, just
    a counter and a timestamp.
    """
    path = os.path.join(ROOT, "data", "odds_credits.json")
    try:
        log = {"readings": []}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                log = json.load(f)
        log["readings"] = (log.get("readings", []) + [credits])[-CREDIT_LOG_KEEP:]
        log["note"] = ("The Odds API bills one credit per market per region per call. "
                       "Free tier: 500/month. See CLAUDE.md for the budget and the "
                       "decision rule for upgrading.")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:                 # telemetry must never sink the board
        print(f"NOTE: could not record odds credits: {exc}")


def alert_low_credits(remaining):
    """Tell a human BEFORE the feed dies, not after. Best-effort, like every
    other alerting path: it can never fail the fetch."""
    try:
        import post_discord
        post_discord.post_alert([
            f"**Odds API credits low: {remaining} remaining.** At roughly "
            f"8 credits/day (1 board call + 3 closing captures, {ODDS_MARKETS} "
            f"over {ODDS_REGIONS}) that is about {remaining // 8} days of feed left. "
            f"When it runs out the board still publishes — model fair lines only, "
            f"no market gates and no staked plays priced — and the site says so."])
    except Exception as exc:
        print(f"NOTE: could not send the low-credit alert: {exc}")

def _payout(o):
    """Net decimal payout per 1u staked. Higher = better for the bettor, which is
    what 'best price' means: -105 beats -120, +130 beats +110."""
    return 100 / -o if o < 0 else o / 100

def consolidate_odds(events, games, teams):
    """Per-game odds record: MEDIAN consensus + BEST price per side (v0.13).

    The consensus median stays the de-vig anchor — market blend, Rule 8
    divergence, and CLV all read it, so best-price ingestion can't flatter
    them. The best price (with its book) is what a bettor can actually get,
    so edge/EV/Kelly and Rule 2 evaluate against it. Totals best prices are
    taken ONLY at the consensus line: a better price on a different total is
    a different bet, not a better price.

    Module-level and pure so it can be unit-tested offline against a
    fabricated Odds API payload.
    """
    by_name = {(ev["away_team"], ev["home_team"]): ev for ev in events}
    odds = {}
    for g in games:
        a_name, h_name = teams[str(g["away"])]["name"], teams[str(g["home"])]["name"]
        ev = by_name.get((a_name, h_name))
        if not ev:
            continue
        a_mls, h_mls, tots, tot_quotes = [], [], [], []
        best = {}   # side -> (payout, price, book)
        for bk in ev.get("bookmakers", []):
            book = bk.get("title") or bk.get("key", "?")
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        side = "away" if o["name"] == a_name else "home"
                        (a_mls if side == "away" else h_mls).append(o["price"])
                        cur = best.get(side)
                        if cur is None or _payout(o["price"]) > cur[0]:
                            best[side] = (_payout(o["price"]), o["price"], book)
                elif m["key"] == "totals":
                    for o in m.get("outcomes", []):
                        if o.get("point") is None:
                            continue
                        tots.append(o["point"])
                        if o.get("name") in ("Over", "Under") and o.get("price") is not None:
                            tot_quotes.append((o["point"], o["name"], o["price"], book))
        if not (a_mls and h_mls):
            continue
        rec = {
            "away_ml": int(statistics.median(a_mls)),
            "home_ml": int(statistics.median(h_mls)),
            "total": float(statistics.median(tots)) if tots else None,
        }
        if "away" in best:
            rec["best_away_ml"], rec["best_away_book"] = best["away"][1], best["away"][2]
        if "home" in best:
            rec["best_home_ml"], rec["best_home_book"] = best["home"][1], best["home"][2]
        if rec["total"] is not None:
            ovr = [(p, b) for pt, s, p, b in tot_quotes if s == "Over" and pt == rec["total"]]
            und = [(p, b) for pt, s, p, b in tot_quotes if s == "Under" and pt == rec["total"]]
            # over/under prices enable the totals paper track (edge + CLV); only
            # books quoting the consensus line count, else totals stays untracked.
            if ovr and und:
                rec["over_price"] = int(statistics.median([p for p, _ in ovr]))
                rec["under_price"] = int(statistics.median([p for p, _ in und]))
                bo = max(ovr, key=lambda x: _payout(x[0]))
                bu = max(und, key=lambda x: _payout(x[0]))
                rec["best_over_price"], rec["best_over_book"] = bo[0], bo[1]
                rec["best_under_price"], rec["best_under_book"] = bu[0], bu[1]
        odds[str(g["gamePk"])] = rec
    return odds

# ---- Rule 6 (Road wOBA Suppression, engine v0.9): trailing-window team wOBA ----
# Static modern wOBA weights (approximate). Fine here because the rule compares
# team wOBA vs league wOBA computed with the SAME weights over the SAME window,
# so weight error largely cancels out of the gap. Shared with backtest.py.
WOBA_WINDOW_DAYS = 14
WOBA_WEIGHTS = {"ubb": 0.69, "hbp": 0.72, "b1": 0.89, "b2": 1.27, "b3": 1.62, "hr": 2.10}

def woba_from(st):
    """wOBA from an MLB API hitting stat dict. None when components are missing
    or the sample is under ~50 PA (a couple of games — too thin to mean anything)."""
    try:
        ab = int(st.get("atBats", 0)); h = int(st.get("hits", 0))
        b2 = int(st.get("doubles", 0)); b3 = int(st.get("triples", 0)); hr = int(st.get("homeRuns", 0))
        bb = int(st.get("baseOnBalls", 0)); ibb = int(st.get("intentionalWalks", 0))
        hbp = int(st.get("hitByPitch", 0)); sf = int(st.get("sacFlies", 0))
    except (TypeError, ValueError):
        return None
    ubb = max(bb - ibb, 0)
    denom = ab + ubb + sf + hbp
    if denom < 50:
        return None
    b1 = h - b2 - b3 - hr
    w = WOBA_WEIGHTS
    num = w["ubb"] * ubb + w["hbp"] * hbp + w["b1"] * b1 + w["b2"] * b2 + w["b3"] * b3 + w["hr"] * hr
    return round(num / denom, 4)

def main():
    # The board runs on several cron windows because GitHub's scheduler is
    # unreliable. Whichever fires first wins; later ones must not re-fetch,
    # since overwriting the snapshot would break the hash already published in
    # that day's commitment.
    if crypto_box.already_published(ROOT, DATE) and "--force" not in sys.argv:
        print(f"Board for {DATE} is already published. Nothing to fetch.")
        return

    # ---- Schedule + probable pitchers ----
    sched = get(f"{MLB}/schedule", sportId=1, date=DATE, hydrate="probablePitcher")
    games_raw = sched["dates"][0]["games"] if sched.get("dates") else []
    games_raw = [g for g in games_raw if g.get("gameType") == "R"]  # regular season only

    # ---- Season state: the offseason guard (see scripts/season.py) ----
    # Written on EVERY run, including the happy path, because heartbeat.yml
    # trusts an offseason reading only while it is fresh. Placed here on purpose:
    # before the standings/pitcher calls and, critically, before the Odds API
    # call below. An empty slate that fell through to that call would bill 2
    # credits a day for a slate with no games in it — ~60 a month, spent on
    # nothing, in the months when the free tier has to cover football instead.
    state = season.write(ROOT, season.classify(DATE, get, games_today=games_raw))
    if not games_raw:
        if state["state"] == season.OFFSEASON:
            last = state.get("last_game_date") or "unknown"
            print(f"[{DATE}] OFFSEASON — no regular-season games today and none "
                  f"within {season.LOOKAHEAD_DAYS} days (last game: {last}). "
                  f"No snapshot, no odds call, no board. The site renders its "
                  f"season-complete state and the ledger stands as final.")
        else:
            print(f"[{DATE}] No games on the slate; next regular-season date is "
                  f"{state.get('resumes')}. No snapshot and no odds call today — "
                  f"the site says there are no games and the blog runs its "
                  f"evergreen off-day piece.")
        return

    # ---- Teams (abbreviations) ----
    teams_resp = get(f"{MLB}/teams", sportId=1, season=SEASON)
    abbr = {t["id"]: t.get("abbreviation", "???") for t in teams_resp["teams"]}
    names = {t["id"]: t["name"] for t in teams_resp["teams"]}

    # ---- Standings (W, L, runs scored/allowed) ----
    standings = get(f"{MLB}/standings", leagueId="103,104", season=SEASON, standingsTypes="regularSeason")
    teams = {}
    for div in standings["records"]:
        for rec in div["teamRecords"]:
            tid = rec["team"]["id"]
            teams[str(tid)] = {
                "name": names.get(tid, rec["team"].get("name", "?")),
                "abbr": abbr.get(tid, "???"),
                "w": rec["wins"], "l": rec["losses"],
                "rs": rec.get("runsScored"), "ra": rec.get("runsAllowed"),
            }
    missing = [t for t in teams.values() if t["rs"] is None or t["ra"] is None]
    if missing:
        raise SystemExit(f"Standings missing runs data for: {[t['abbr'] for t in missing]}")

    # ---- Bullpen: team reliever ERA (one bulk call: pitching statSplits, sitCode rp) ----
    # v0.4 gives the engine a real bullpen for the ~3.5/9 of the game the starter
    # doesn't cover, instead of assuming league-average relief. Best-effort: if the
    # split is unavailable, pen_era stays None and the engine falls back to the
    # team's overall run-prevention rate, exactly as v0.3 did.
    try:
        pen = get(f"{MLB}/teams/stats", stats="statSplits", group="pitching",
                  season=SEASON, sitCodes="rp", sportIds=1, gameType="R")
        for s in (pen.get("stats") or [{}])[0].get("splits") or []:
            tid = str(s.get("team", {}).get("id"))
            era = s.get("stat", {}).get("era")
            if tid in teams and era is not None:
                try:
                    teams[tid]["pen_era"] = float(era)
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # never sink the board over the bullpen split
        print(f"WARNING: bullpen split fetch failed ({e}); engine falls back to team RA for the pen.")
    for t in teams.values():
        t.setdefault("pen_era", None)   # None = no data → engine fallback

    # ---- Trailing-14-day team wOBA (Rule 6 detection, engine v0.9) ----
    # One bulk byDateRange call over the window ENDING YESTERDAY (no same-day
    # leak). Best-effort like the bullpen split: any failure leaves woba_14d
    # None and the engine reports Rule 6 as "manual review" for the day.
    league_woba_14d = None
    try:
        d0 = datetime.fromisoformat(DATE)
        win_start = (d0 - timedelta(days=WOBA_WINDOW_DAYS)).strftime("%Y-%m-%d")
        win_end = (d0 - timedelta(days=1)).strftime("%Y-%m-%d")
        hs = get(f"{MLB}/teams/stats", stats="byDateRange", group="hitting",
                 startDate=win_start, endDate=win_end, season=SEASON, sportIds=1, gameType="R")
        agg = {}
        for s in (hs.get("stats") or [{}])[0].get("splits") or []:
            tid = str(s.get("team", {}).get("id"))
            st = s.get("stat", {})
            if tid in teams:
                teams[tid]["woba_14d"] = woba_from(st)
            for k in ("atBats", "hits", "doubles", "triples", "homeRuns",
                      "baseOnBalls", "intentionalWalks", "hitByPitch", "sacFlies"):
                try:
                    agg[k] = agg.get(k, 0) + int(st.get(k, 0) or 0)
                except (TypeError, ValueError):
                    pass
        league_woba_14d = woba_from(agg)
    except Exception as e:  # never sink the board over a split
        print(f"WARNING: 14-day hitting split fetch failed ({e}); Rule 6 stays manual-review today.")
    for t in teams.values():
        t.setdefault("woba_14d", None)

    # ---- League pitching totals (for the engine's FIP constant, v0.5) ----
    # One bulk call summed across teams. The engine derives its FIP constant from
    # these so league-average FIP lands on the league run scale. Absent -> engine
    # skips FIP and uses ERA only.
    def _ip(x):  # "103.2" means 103 innings and 2/3, not 103.2
        w, _, frac = str(x).partition(".")
        try:
            return int(w) + {"1": 1 / 3, "2": 2 / 3}.get(frac, 0.0)
        except ValueError:
            return 0.0
    league_pitching = None
    try:
        tp = get(f"{MLB}/teams/stats", stats="season", group="pitching",
                 season=SEASON, sportIds=1, gameType="R")
        tot = {"ip": 0.0, "hr": 0, "bb": 0, "hbp": 0, "k": 0, "er": 0}
        for s in (tp.get("stats") or [{}])[0].get("splits") or []:
            st = s["stat"]
            tot["ip"] += _ip(st.get("inningsPitched", 0))
            tot["hr"] += st.get("homeRuns", 0)
            tot["bb"] += st.get("baseOnBalls", 0)
            tot["hbp"] += st.get("hitByPitch", 0)
            tot["k"] += st.get("strikeOuts", 0)
            tot["er"] += st.get("earnedRuns", 0)
        if tot["ip"] > 0:
            league_pitching = {k: round(v, 3) for k, v in tot.items()}
    except Exception as e:  # never sink the board over it
        print(f"WARNING: league pitching totals fetch failed ({e}); engine skips FIP, uses ERA only.")

    # ---- Probable pitcher season stats (one batched call) ----
    pids = sorted({g["teams"][side].get("probablePitcher", {}).get("id")
                   for g in games_raw for side in ("away", "home")
                   if g["teams"][side].get("probablePitcher")})
    pitchers = {}
    if pids:
        ppl = get(f"{MLB}/people", personIds=",".join(map(str, pids)),
                  hydrate=f"stats(group=[pitching],type=[season],season={SEASON})")
        for p in ppl["people"]:
            splits = (p.get("stats") or [{}])[0].get("splits") or []
            if not splits:
                continue
            s = splits[0]["stat"]
            try:
                pitchers[str(p["id"])] = {
                    "name": p["fullName"],
                    "era": float(s["era"]),
                    "ip": float(s["inningsPitched"]),
                    "whip": float(s["whip"]),
                    "k9": float(s["strikeoutsPer9Inn"]),
                    # FIP components (v0.5): the engine blends ERA with FIP to strip
                    # defense/luck. Absent -> engine falls back to ERA only.
                    "hr": int(s.get("homeRuns", 0)),
                    "bb": int(s.get("baseOnBalls", 0)),
                    "hbp": int(s.get("hitByPitch", 0)),
                    "k": int(s.get("strikeOuts", 0)),
                    # outs mix (v0.10): fly-ball-starter proxy for the weather
                    # kicker on the totals paper track. Absent -> no kicker.
                    "ao": int(s.get("airOuts", 0)),
                    "go": int(s.get("groundOuts", 0)),
                }
            except (KeyError, ValueError):
                continue  # no usable season line -> treated as TBD by the engine

    games = []
    for g in games_raw:
        a, h = g["teams"]["away"], g["teams"]["home"]
        a_sp = a.get("probablePitcher", {}).get("id")
        h_sp = h.get("probablePitcher", {}).get("id")
        games.append({
            "gamePk": g["gamePk"],
            "away": a["team"]["id"], "home": h["team"]["id"],
            "utc": g["gameDate"],
            "venue": g.get("venue", {}).get("name", "Unknown"),
            "awaySP": a_sp if str(a_sp) in pitchers else None,
            "homeSP": h_sp if str(h_sp) in pitchers else None,
        })

    # ---- Weather (v0.10): game-time forecast per outdoor park (Open-Meteo, no key) ----
    # Feeds ONLY the totals paper track: the staked moneyline board ignores weather
    # entirely (engine runs the weather sim separately). The hourly grid is requested
    # in UTC and matched against the game's UTC start hour — no timezone math.
    # Best-effort per venue; any failure leaves wx None and the totals go un-adjusted.
    wx_cache = {}
    for g in games:
        park = PARK_COORDS.get(g["venue"])
        if park is None:
            g["wx"] = None
            continue
        lat, lon, roof = park
        if roof:
            g["wx"] = {"roof": True}
            continue
        try:
            if g["venue"] not in wx_cache:
                end_d = (datetime.fromisoformat(DATE) + timedelta(days=1)).strftime("%Y-%m-%d")
                wx_cache[g["venue"]] = get("https://api.open-meteo.com/v1/forecast",
                    latitude=lat, longitude=lon,
                    hourly="temperature_2m,windspeed_10m,winddirection_10m,relative_humidity_2m",
                    temperature_unit="fahrenheit", windspeed_unit="mph",
                    start_date=DATE, end_date=end_d, timezone="UTC")
            h = wx_cache[g["venue"]].get("hourly", {})
            want = g["utc"][:13]  # "YYYY-MM-DDTHH" — floor of first pitch, close enough for temp
            idx = next((i for i, t in enumerate(h.get("time", [])) if t.startswith(want)), None)
            if idx is None or h["temperature_2m"][idx] is None:
                g["wx"] = None
            else:
                g["wx"] = {"roof": False,
                           "temp_f": h["temperature_2m"][idx],
                           "wind_mph": h["windspeed_10m"][idx],
                           "wind_dir_deg": h["winddirection_10m"][idx],
                           "humidity_pct": h["relative_humidity_2m"][idx]}
        except Exception as e:  # weather is optional — never sink the board over it
            print(f"WARNING: weather fetch failed for {g['venue']} ({e}); totals un-adjusted there.")
            g["wx"] = None

    # ---- Odds (optional): The Odds API — median consensus + best price (v0.13) ----
    # DEGRADE, NEVER DIE. Quota exhaustion, a revoked key, a 429, a timeout — all
    # of it lands in the same place: a warning, an empty odds dict, and a board
    # that still publishes. A missing price feed should cost us picks, never the
    # whole board. The site says plainly what it is showing when this happens.
    odds, odds_source, odds_credits = {}, None, {}
    key = os.environ.get("ODDS_API_KEY")
    if key:
        try:
            events = fetch_odds(key, odds_credits)
            odds = consolidate_odds(events, games, teams)
            odds_source = (f"The Odds API, US books: consensus = median, plus best price per side "
                           f"with book, markets {ODDS_MARKETS}, "
                           f"fetched {datetime.utcnow().isoformat()}Z")
            print(f"Odds: fetched markets [{ODDS_MARKETS}] over regions [{ODDS_REGIONS}] "
                  f"for {len(odds)} games.")
        except Exception as e:  # odds are optional — never sink the board over them
            print(f"WARNING: odds fetch failed ({e}); the board will publish with MODEL FAIR "
                  f"LINES ONLY — no market gates, no priced edges. The site says so.")
            odds, odds_source = {}, None
        # Runs whether the call succeeded or failed: a 401 or a 429 still reports
        # the balance, and that reading is exactly the one worth keeping.
        if odds_credits:
            rem, used = odds_credits.get("remaining"), odds_credits.get("used")
            print(f"Odds API credits: {rem if rem is not None else '?'} remaining, "
                  f"{used if used is not None else '?'} used this period, "
                  f"this call cost {odds_credits.get('last_call_cost', '?')}.")
            record_credits(odds_credits)
            if rem is not None and rem < CREDITS_LOW:
                print(f"WARNING: odds credits below {CREDITS_LOW}.")
                alert_low_credits(rem)
    else:
        print("NOTE: no ODDS_API_KEY set — running without market odds (edge/Kelly/Rule 8 inactive).")

    snapshot = {
        "snapshot_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"MLB Stats API (statsapi.mlb.com), fetched {DATE}",
        "season": SEASON,
        "teams": teams,
        "pitchers": pitchers,
        "league_pitching": league_pitching,
        "league_woba_14d": league_woba_14d,
        "odds_source": odds_source,
        "odds_credits": odds_credits or None,
        "odds_markets": ODDS_MARKETS if key else None,
        "odds": odds,
        "park_factors_note": "Approximate season-level run park factors; refresh from Baseball Savant.",
        "park_factors": PARK_FACTORS,
        "games": games,
    }
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    # Encrypted when a key is present. The snapshot is withheld alongside the
    # board because the engine is public and deterministic: publish the inputs
    # and anyone can re-derive the picks exactly.
    out, _sha, enc = crypto_box.save_dataset(ROOT, "snapshot", DATE, snapshot)
    print(f"Wrote {out}{' (encrypted)' if enc else ''}: {len(games)} games, "
          f"{len(pitchers)} pitchers, odds for {len(odds)} games")

if __name__ == "__main__":
    main()
