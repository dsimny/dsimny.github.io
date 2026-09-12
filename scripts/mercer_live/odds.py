#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1, live market observations.

SOURCE: The Odds API `/v4/sports/{sport}/odds`, the endpoint every existing
odds caller in this repository uses (fetch_data.py, fetch_closing.py,
scripts/football/fetch_odds.py). The response lists upcoming AND in-play
events together, with no live flag: a quote's phase has to be established by
Open Ledger, from the game state observed in the same tick. That is what this
module does, and it is the single most important thing it does — a live quote
that leaks into a pregame or closing-line calculation is a corrupted number
that looks fine.

WHAT ONE RECORD IS. One `live_market_observation` per (event, book, market)
per tick, carrying both outcomes of that market. Every book's quote is kept;
nothing is collapsed into a consensus (pre-registration section 8). One
`live_market_coverage` per event per tick says which books quoted which
markets, so a suspended or withdrawn market is visible as an absence rather
than merely missing.

THE JOIN (pre-registration sections 7-8, ML-1 requirement 2). Prices join to
game state by the identity the pregame system already trusts:
    NFL      teams.from_name(raw)  -> franchise key  (same as fetch_odds.py)
    NCAA FBS espn_ncaaf.key_for(raw) -> normalised key (same as grade_football.py)
then (away_key, home_key) must match EXACTLY ONE game observed this tick whose
scheduled start is within JOIN_WINDOW_H of the quote's commence_time. Anything
else is recorded with its reason and NOT joined:
    unjoined             no game-state match this tick
    ambiguous            more than one match — refuse to choose (market.find_event's rule)
    home_away_conflict   the pair matches only with home and away SWAPPED
    identity_error       a team name this repository cannot resolve
An unresolvable NFL name does NOT abort the tick, unlike fetch_odds.py. There,
one bad name aborts one scheduled pull that the next hour repeats. Here, an
abort would lose every other game's observation for that minute, and the
minute does not come back. The row is written, flagged, and never joined.

PHASE. `phase` is `live` ONLY when the joined game state says `in`. A quote
with no joined game state is `pregame` if its commence_time is still in the
future and `unknown` otherwise — never `live`, because "probably started" is
not an observation. `phase_basis` says which rule produced it.

COST. This endpoint bills markets x regions per call regardless of how many
events come back, so one call per league per tick is the cheapest possible
shape and no event filter would reduce it. The credit headers are read BEFORE
raise_for_status so a 401/429 reading survives the failure (same rule as
fetch_data.py), appended to data/odds_credits.json through
fetch_odds.record_credits, and returned to the caller for its own budget.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "football"))

import mlcommon as C                                  # noqa: E402
import teams                                          # noqa: E402
import espn_ncaaf                                     # noqa: E402
from price_test import TIER1, TIER2                   # noqa: E402

API = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SPORT_KEYS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}
DEFAULT_MARKETS = "h2h,spreads,totals"
DEFAULT_REGIONS = "us"
JOIN_WINDOW_H = 12.0       # commence_time vs scheduled_start tolerance for the join
MARKETS_KNOWN = ("h2h", "spreads", "totals")


def call_cost(markets, regions):
    n_m = len([m for m in markets.split(",") if m.strip()])
    n_r = len([r for r in regions.split(",") if r.strip()])
    return n_m * n_r


def fetch(league, key, markets=DEFAULT_MARKETS, regions=DEFAULT_REGIONS, timeout=20):
    """One GET. Returns (body_bytes, meta, credits). Raises requests
    exceptions; an HTTP error is returned in meta (credits already read) and
    the caller decides — it never fabricates a price."""
    import requests
    sent = C.now_utc()
    r = requests.get(API.format(sport=SPORT_KEYS[league]), timeout=timeout,
                     params={"apiKey": key, "regions": regions, "markets": markets,
                             "oddsFormat": "american", "dateFormat": "iso"})
    received = C.now_utc()

    def _int(name):
        try:
            return int(r.headers.get(name))
        except (TypeError, ValueError):
            return None

    credits = {"remaining": _int("x-requests-remaining"), "used": _int("x-requests-used"),
               "last_call_cost": _int("x-requests-last"), "markets": markets,
               "regions": regions, "http_status": r.status_code,
               "source": f"mercer_live:{league}", "read_utc": C.iso_s(received)}
    meta = {"provider": "the-odds-api", "sport_key": SPORT_KEYS[league],
            "http_status": r.status_code, "provider_http_date": r.headers.get("Date"),
            "request_sent_at": C.iso_ms(sent), "response_received_at": C.iso_ms(received),
            "markets": markets, "regions": regions}
    return r.content, meta, credits


# ------------------------------------------------------------- identity --

def resolve_team(league, raw):
    """(key, error). NFL through teams.py, college through espn_ncaaf. Never fuzzy."""
    if not raw:
        return None, "empty team name"
    if league == "nfl":
        try:
            return teams.from_name(raw, source="odds-api"), None
        except teams.UnknownTeam as e:
            return None, str(e)
    return espn_ncaaf.key_for(raw), None


def book_tier(book):
    if book in TIER1:
        return "tier1"
    if book in TIER2:
        return "tier2"
    return "unclassified"


def side_for(market, outcome_name, away_raw, home_raw):
    """Which side an outcome is, by the feed's OWN strings for this event."""
    if market == "totals":
        n = (outcome_name or "").strip().lower()
        return n if n in ("over", "under") else None
    if outcome_name == home_raw:
        return "home"
    if outcome_name == away_raw:
        return "away"
    return None


def join(league, away_key, home_key, commence, game_index):
    """-> (event_id, gamestate_obs_id, join_status, game_record_or_None)."""
    if away_key is None or home_key is None:
        return None, None, "identity_error", None

    def near(recs):
        out = []
        for r in recs:
            k = C.parse_utc(r["scheduled_start"])
            if commence is None or k is None:
                out.append(r)
            elif abs((k - commence).total_seconds()) <= JOIN_WINDOW_H * 3600:
                out.append(r)
        return out

    hits = near(game_index.get((away_key, home_key), []))
    if len(hits) == 1:
        g = hits[0]
        return g["event_id"], g["obs_id"], "joined", g
    if len(hits) > 1:
        return None, None, "ambiguous", None
    if near(game_index.get((home_key, away_key), [])):
        return None, None, "home_away_conflict", None
    return None, None, "unjoined", None


def phase_for(game, commence, observed_at):
    """(phase, basis). `live` requires an observed in-progress game state."""
    if game is not None:
        return {"pre": "pregame", "in": "live", "post": "post"}[game["state"]], "gamestate"
    if commence is not None and observed_at < commence:
        return "pregame", "commence_time"
    return "unknown", "commence_time"


def _age_s(observed_at, ts):
    t = C.parse_utc(ts)
    if t is None:
        return None
    return round((observed_at - t).total_seconds(), 3)


# ------------------------------------------------------------- parsing --

def observe(league, payload, ctx, game_index, markets_requested=DEFAULT_MARKETS):
    """Odds API payload -> (market_records, coverage_records, errors).

    A payload that is not a list raises MalformedPayload and nothing is
    written. An individual event that cannot be read is recorded in `errors`
    and skipped; every readable event is kept whether or not it joins.
    """
    if not isinstance(payload, list):
        raise C.MalformedPayload("odds payload is not a list of events")
    observed_at = C.parse_utc(ctx["observed_at"])
    requested = [m for m in markets_requested.split(",") if m.strip()]
    rows, coverage, errors = [], [], []
    for ev in payload:
        try:
            pid = str(ev["id"])
            away_raw, home_raw = ev["away_team"], ev["home_team"]
            books = ev.get("bookmakers") or []
            if not isinstance(books, list):
                raise C.MalformedPayload(f"event {pid}: bookmakers is not a list")
        except (KeyError, TypeError) as e:
            errors.append({"provider_event_id": str(ev.get("id")) if isinstance(ev, dict) else None,
                           "error": f"unreadable event: {e!r}"})
            continue
        commence = C.parse_utc(ev.get("commence_time"))
        away_key, err_a = resolve_team(league, away_raw)
        home_key, err_h = resolve_team(league, home_raw)
        identity_error = err_a or err_h
        event_id, gs_obs, status, game = join(league, away_key, home_key, commence, game_index)
        phase, basis = phase_for(game, commence, observed_at)
        common = {
            "record": "live_market_observation",
            "schema_version": C.SCHEMA_VERSION,
            "run_id": ctx["run_id"],
            "observed_at": ctx["observed_at"],
            "request_sent_at": ctx.get("request_sent_at"),
            "provider": "the-odds-api",
            "provider_http_date": ctx.get("provider_http_date"),
            "raw_ref": ctx.get("raw_ref"),
            "raw_sha256": ctx.get("raw_sha256"),
            "sport_key": SPORT_KEYS[league],
            "league": league,
            "provider_event_id": pid,
            "event_id": event_id,
            "gamestate_obs_id": gs_obs,
            "join_status": status,
            "identity_error": identity_error,
            "away_raw": away_raw, "home_raw": home_raw,
            "away_key": away_key, "home_key": home_key,
            "commence_time": C.iso_s(commence) if commence else None,
            "phase": phase, "phase_basis": basis, "is_live": phase == "live",
        }
        quoting = {m: [] for m in requested}
        for bk in books:
            book = bk.get("key")
            if not book:
                errors.append({"provider_event_id": pid, "error": "bookmaker with no key"})
                continue
            b_lu = bk.get("last_update")
            for m in bk.get("markets") or []:
                mk = m.get("key")
                if mk not in MARKETS_KNOWN:
                    continue                      # not requested; never invented
                outcomes = []
                for o in m.get("outcomes") or []:
                    price = o.get("price")
                    if price is None or o.get("name") is None:
                        continue
                    outcomes.append({
                        "name": o.get("name"),
                        "side": side_for(mk, o.get("name"), away_raw, home_raw),
                        "point": o.get("point"),
                        "price_american": price,
                    })
                if not outcomes:
                    continue
                m_lu = m.get("last_update")
                rows.append(dict(common, **{
                    "obs_id": C.obs_id(ctx["run_id"], "the-odds-api", pid, book, mk),
                    "book": book,
                    "book_tier": book_tier(book),
                    "market": mk,
                    "outcomes": outcomes,
                    "n_outcomes": len(outcomes),
                    "book_last_update": b_lu,
                    "market_last_update": m_lu,
                    "quote_age_s": _age_s(observed_at, m_lu or b_lu),
                    "availability": "quoted",
                }))
                if mk in quoting:
                    quoting[mk].append(book)
            # A requested market this book did not send is not invented here;
            # its absence is what the coverage row below records, per book.
        coverage.append({
            "record": "live_market_coverage",
            "schema_version": C.SCHEMA_VERSION,
            "obs_id": C.obs_id(ctx["run_id"], "coverage", pid),
            "run_id": ctx["run_id"],
            "observed_at": ctx["observed_at"],
            "league": league,
            "provider_event_id": pid,
            "event_id": event_id,
            "join_status": status,
            "phase": phase,
            "markets_requested": requested,
            "n_books": len(books),
            "books_present": sorted(b.get("key") for b in books if b.get("key")),
            "books_quoting": {m: sorted(v) for m, v in quoting.items()},
            # A book present on the event but not quoting a requested market is
            # what a suspended market looks like from outside. Named, per book.
            "books_not_quoting": {m: sorted(set(b.get("key") for b in books if b.get("key"))
                                            - set(v)) for m, v in quoting.items()},
        })
    return rows, coverage, errors


def quote_age_summary(rows):
    ages = sorted(r["quote_age_s"] for r in rows if r.get("quote_age_s") is not None)
    if not ages:
        return {"n": 0}
    return {"n": len(ages), "min_s": ages[0], "median_s": ages[len(ages) // 2],
            "p90_s": ages[int(len(ages) * 0.9) - 1 if len(ages) > 1 else 0],
            "max_s": ages[-1]}
