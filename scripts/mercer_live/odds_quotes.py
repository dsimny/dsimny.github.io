#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1: live market quotes from The Odds API.

TWO RECORD KINDS PER RESPONSE:
  market_quote   one per (event, book, market, outcome) — the raw quote, with
                 the book's and the market's own last_update kept verbatim,
                 and the phase it was observed in.
  market_event   one per event — books returned, markets offered per book,
                 join status, phase. An event that came back with ZERO books is
                 still evidence (it happened on 2026-09-13: CLE @ JAX, in
                 progress, no books) and only an event-level record can carry it.

THE PHASE LABEL IS THE POINT OF THIS MODULE. The Odds API returns pregame and
in-play events in the same response with no flag to tell them apart, and the
pregame pipeline's captures therefore hold live quotes with no label (audit
section 18). Here every quote carries `quote_phase` and `phase_basis`:

    pregame              game state says pre, or (unjoined) commence_time is
                         still in the future
    live                 game state says in
    post                 game state says post
    commenced_unjoined   commence_time has passed and no game state joined, so
                         live and post cannot be told apart — NOT pregame
    unknown              no commence_time at all — NOT pregame

    phase_basis          game_state | commence_time | none

A quote may be treated as PREGAME only when its phase is exactly "pregame".
Every other label fails closed toward "not pregame", so this stream can never
contaminate a closing-line computation even if someone later reads it by
mistake. When game state and the provider clock disagree (state says in, but
commence_time is a minute ahead of observed_at), game state wins: it is the
observation of the game itself, the clock is the provider's schedule.

CONSENSUS IS NOT COMPUTED HERE. Individual book quotes are what is stored
(preregistration section 8). De-vigging is a derivation for later packages,
against the intact quotes.

COST. The /odds endpoint bills markets x regions per call, independent of how
many events come back. The reading is taken from the response headers BEFORE
the status check so a 401/429 — the readings that matter most — survive.
"""
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402
import identity                                                   # noqa: E402

API = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

PHASE_PREGAME = "pregame"
PHASE_LIVE = "live"
PHASE_POST = "post"
PHASE_COMMENCED_UNJOINED = "commenced_unjoined"
PHASE_UNKNOWN = "unknown"
PHASES = (PHASE_PREGAME, PHASE_LIVE, PHASE_POST, PHASE_COMMENCED_UNJOINED, PHASE_UNKNOWN)

TOTAL_OUTCOMES = {"over": "Over", "under": "Under"}


class OddsResponse:
    """What the caller needs from one HTTP round trip, headers included."""
    def __init__(self, status_code, headers, body, sent_at, received_at):
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.sent_at = sent_at
        self.received_at = received_at

    def credit_reading(self, markets, regions, source):
        def _int(name):
            try:
                return int(self.headers.get(name))
            except (TypeError, ValueError):
                return None
        return {"remaining": _int("x-requests-remaining"),
                "used": _int("x-requests-used"),
                "last_call_cost": _int("x-requests-last"),
                "markets": markets, "regions": regions,
                "http_status": self.status_code,
                "source": source,
                "read_utc": common.iso(self.received_at)}


def fetch_odds(sport, api_key, markets, regions, commence_from=None,
               commence_to=None, timeout=30, session=None):
    """One GET. Returns OddsResponse; raises requests.RequestException on
    transport failure. Never logs the key. commence_from/to are aware datetimes
    or None; when given they are sent as ISO-8601 Z so the provider can filter
    the response to the live window (the client filters again regardless)."""
    cfg = common.sport_cfg(sport)
    params = {"apiKey": api_key, "regions": regions, "markets": markets,
              "oddsFormat": "american", "dateFormat": "iso"}
    if commence_from is not None:
        params["commenceTimeFrom"] = common.iso(commence_from)
    if commence_to is not None:
        params["commenceTimeTo"] = common.iso(commence_to)
    s = session or requests
    sent = common.now_utc()
    r = s.get(API.format(sport_key=cfg["odds_sport_key"]), params=params, timeout=timeout)
    received = common.now_utc()
    try:
        body = r.json()
    except ValueError:
        body = None
    return OddsResponse(r.status_code, dict(r.headers), body, sent, received)


def classify_phase(commence_utc, observed_at, joined_state):
    """(phase, basis). Game state wins when present; the provider clock is the
    fallback; no clock at all is 'unknown' and never pregame."""
    if joined_state is not None:
        st = joined_state.get("status_state")
        if st == "pre":
            return PHASE_PREGAME, "game_state"
        if st == "in":
            return PHASE_LIVE, "game_state"
        if st == "post":
            return PHASE_POST, "game_state"
        # joined, but the state record itself had no usable state: fall through
    if commence_utc is None:
        return PHASE_UNKNOWN, "none"
    if commence_utc > observed_at:
        return PHASE_PREGAME, "commence_time"
    return PHASE_COMMENCED_UNJOINED, "commence_time"


def _price(v):
    """American price -> int, or None when not a usable price."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        p = int(v)
    except (TypeError, ValueError):
        return None
    if float(v) != p or -100 < p < 100:
        return None
    return p


def _point(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return "invalid"


def outcome_key(sport, market, name, away_key, home_key, away_raw, home_raw):
    """Map an outcome name to this sport's identity key (h2h/spreads) or to
    Over/Under (totals). Returns None when it matches nothing — never guesses."""
    if name is None:
        return None
    if market == "totals":
        return TOTAL_OUTCOMES.get(str(name).strip().lower())
    if name == away_raw:
        return away_key
    if name == home_raw:
        return home_key
    return None


def parse_odds(body, sport, observed_at, run_id, state_index, markets_requested):
    """(quote_records, event_records, errors) for one response body.

    state_index: identity.index_by_canonical() of THIS tick's game_state
    records, or {} when none were captured (every event is then unjoined).
    """
    quotes, events, errors = [], [], []
    if not isinstance(body, list):
        return [], [], [{"provider_event_id": None,
                         "reason": f"odds payload is not a list ({type(body).__name__})"}]
    cfg = common.sport_cfg(sport)
    for ev in body:
        if not isinstance(ev, dict):
            errors.append({"provider_event_id": None, "reason": "event is not an object"})
            continue
        pid = ev.get("id")
        away_raw, home_raw = ev.get("away_team"), ev.get("home_team")
        commence = common.parse_utc(ev.get("commence_time"))
        if pid in (None, "") or not away_raw or not home_raw:
            errors.append({"provider_event_id": str(pid) if pid else None,
                           "reason": "event missing id or team names"})
            continue
        pid = str(pid)
        ident_status, away_key, home_key, cid = "resolved", None, None, None
        try:
            away_key = identity.team_key(sport, away_raw, "odds_name")
            home_key = identity.team_key(sport, home_raw, "odds_name")
            cid = identity.canonical_event_id(sport, commence, away_key, home_key)
        except identity.IdentityError as e:
            ident_status = "unresolved"
            errors.append({"provider_event_id": pid, "reason": f"identity: {e}"[:300]})

        jstatus, st = identity.join_status(cid if ident_status == "resolved" else None,
                                           state_index, commence)
        phase, basis = classify_phase(commence, observed_at, st)

        books = ev.get("bookmakers") if isinstance(ev.get("bookmakers"), list) else []
        offered = {}          # book -> [markets offered]
        n_quotes = 0
        for bk in books:
            if not isinstance(bk, dict) or not bk.get("key"):
                errors.append({"provider_event_id": pid, "reason": "bookmaker without key"})
                continue
            book = str(bk["key"])
            book_lu = bk.get("last_update")
            offered.setdefault(book, [])
            for m in bk.get("markets") or []:
                if not isinstance(m, dict) or m.get("key") not in common.MARKETS:
                    continue            # a market we do not capture; not an error
                mkey = m["key"]
                offered[book].append(mkey)
                m_lu = m.get("last_update")
                if ident_status != "resolved":
                    continue            # no identity -> no quote rows (event row carries it)
                for o in m.get("outcomes") or []:
                    if not isinstance(o, dict):
                        errors.append({"provider_event_id": pid,
                                       "reason": f"{book}/{mkey}: outcome not an object"})
                        continue
                    okey = outcome_key(sport, mkey, o.get("name"), away_key, home_key,
                                       away_raw, home_raw)
                    price = _price(o.get("price"))
                    point = _point(o.get("point"))
                    if okey is None or price is None or point == "invalid" \
                            or (mkey == "h2h" and point is not None) \
                            or (mkey != "h2h" and point is None):
                        errors.append({"provider_event_id": pid,
                                       "reason": f"{book}/{mkey}: malformed outcome "
                                                 f"name={o.get('name')!r} price={o.get('price')!r} "
                                                 f"point={o.get('point')!r}"})
                        continue
                    lu = common.parse_utc(m_lu) or common.parse_utc(book_lu)
                    age = (observed_at - lu).total_seconds() if lu else None
                    quotes.append({
                        "kind": common.KIND_MARKET_QUOTE,
                        "schema_version": common.SCHEMA_VERSION,
                        "run_id": run_id,
                        "observed_at": common.iso(observed_at),
                        "source": common.MARKET_SOURCE,
                        "sport": sport, "league": cfg["league"],
                        "provider_event_id": pid,
                        "canonical_event_id": cid,
                        "home": home_key, "away": away_key,
                        "home_raw": home_raw, "away_raw": away_raw,
                        "commence_time": common.iso(commence) if commence else None,
                        "book": book,
                        "market": mkey,
                        "outcome": okey,
                        "outcome_raw": o.get("name"),
                        "point": point,
                        "price": price,
                        # Provider timestamps, VERBATIM, plus the parsed age.
                        "provider_book_last_update": book_lu,
                        "provider_market_last_update": m_lu,
                        "provider_timestamp": m_lu or book_lu,
                        "quote_age_seconds": round(age, 3) if age is not None else None,
                        "age_basis": ("market_last_update" if common.parse_utc(m_lu)
                                      else "book_last_update" if common.parse_utc(book_lu)
                                      else None),
                        "quote_phase": phase,
                        "phase_basis": basis,
                        "join_status": jstatus,
                        "game_state_observation_id": st.get("observation_id") if st else None,
                        "observation_id": common.sha256_hex(
                            common.KIND_MARKET_QUOTE, run_id, common.MARKET_SOURCE,
                            sport, pid, book, mkey, okey, point),
                    })
                    n_quotes += 1
        events.append({
            "kind": common.KIND_MARKET_EVENT,
            "schema_version": common.SCHEMA_VERSION,
            "run_id": run_id,
            "observed_at": common.iso(observed_at),
            "source": common.MARKET_SOURCE,
            "sport": sport, "league": cfg["league"],
            "provider_event_id": pid,
            "canonical_event_id": cid,
            "identity_status": ident_status,
            "home": home_key, "away": away_key,
            "home_raw": home_raw, "away_raw": away_raw,
            "commence_time": common.iso(commence) if commence else None,
            "markets_requested": markets_requested,
            "n_books": len(offered),
            "no_quotes": len(offered) == 0,
            "markets_offered": {b: sorted(set(ms)) for b, ms in sorted(offered.items())},
            "books_per_market": {m: sum(1 for ms in offered.values() if m in ms)
                                 for m in common.MARKETS},
            "n_quotes": n_quotes,
            "quote_phase": phase,
            "phase_basis": basis,
            "join_status": jstatus,
            "game_state_observation_id": st.get("observation_id") if st else None,
            "observation_id": common.sha256_hex(
                common.KIND_MARKET_EVENT, run_id, common.MARKET_SOURCE, sport, pid),
        })
    return quotes, events, errors


def is_pregame(record):
    """The ONLY way a Mercer Live quote may be read as pregame."""
    return record.get("quote_phase") == PHASE_PREGAME
