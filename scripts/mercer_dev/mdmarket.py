#!/usr/bin/env python3
"""
D.J. Mercer Developmental Cohorts - market quotes, event identity, freshness.

Pure functions over data already fetched. Network access lives in mercer_dev.py.

QUOTE SHAPE (normalised from The Odds API /events/{id}/odds):
    {"source": "the-odds-api", "odds_event_id": str, "sport": "nfl"|"ncaaf",
     "home": str, "away": str, "commence_time": "...Z", "captured_utc": "...Z",
     "books": {book: {"last_update": "...Z",
                      "markets": {"moneyline": {team: price, ...},
                                  "spread": {team: [point, price], ...},
                                  "total": {"over": [point, price], "under": [...]}},
                      "market_updates": {market: "...Z"}}}}

IDENTITY IS THE PROVIDER EVENT ID, NEVER THE KICKOFF. The September incident
split one game into two when the provider rewrote commence_time after the game
started. Here the Odds API event id and the ESPN event id are both carried and
checked, and kickoff is compared only to detect a schedule change.
"""
import math
import os
import statistics
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "football"))

import mdcore                                        # noqa: E402

# Imported, not redeclared: the same US-regulated set every football study uses.
from price_test import TIER1                         # noqa: E402

API_MARKET = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}


class QuoteError(ValueError):
    pass


def normalize_event_odds(payload, sport, captured_utc):
    """Odds API event-odds JSON -> quote dict. Raises on anything malformed."""
    if not isinstance(payload, dict) or not payload.get("id"):
        raise QuoteError("odds payload has no event id")
    home, away = payload.get("home_team"), payload.get("away_team")
    if not home or not away:
        raise QuoteError("odds payload has no teams")
    books = {}
    for bk in payload.get("bookmakers") or []:
        key = bk.get("key")
        if not key:
            raise QuoteError("bookmaker without a key")
        markets, updates = {}, {}
        for m in bk.get("markets") or []:
            name = API_MARKET.get(m.get("key"))
            if not name:
                continue
            outs = m.get("outcomes") or []
            if name == "moneyline":
                prices = {o["name"]: o["price"] for o in outs
                          if o.get("name") in (home, away) and _finite(o.get("price"))}
                if set(prices) == {home, away}:
                    markets[name] = prices
            elif name == "spread":
                pts = {o["name"]: [float(o["point"]), o["price"]] for o in outs
                       if o.get("name") in (home, away) and _finite(o.get("price"))
                       and _finite(o.get("point"))}
                if set(pts) == {home, away}:
                    markets[name] = pts
            else:
                pts = {o["name"].lower(): [float(o["point"]), o["price"]] for o in outs
                       if str(o.get("name", "")).lower() in ("over", "under")
                       and _finite(o.get("price")) and _finite(o.get("point"))}
                if set(pts) == {"over", "under"}:
                    markets[name] = pts
            if name in markets and m.get("last_update"):
                updates[name] = m["last_update"]
        if markets:
            books[key] = {"last_update": bk.get("last_update"), "markets": markets,
                          "market_updates": updates}
    return {"source": "the-odds-api", "odds_event_id": payload["id"], "sport": sport,
            "home": home, "away": away, "commence_time": payload.get("commence_time"),
            "captured_utc": captured_utc, "books": books}


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def team_key(sport, name):
    """Odds API / ESPN display name -> comparison key for that sport."""
    if sport == "nfl":
        import teams
        return teams.from_name(name, source="mercer-dev")
    import espn_ncaaf
    return espn_ncaaf.key_for(name)


def espn_keys(sport, event):
    if sport == "nfl":
        return event["home"], event["away"]
    return event.get("home_key") or team_key("ncaaf", event["home_name"]), \
        event.get("away_key") or team_key("ncaaf", event["away_name"])


def identity_problems(sport, quote, event, artifact_event):
    """Every reason the quote, the ESPN event and the approved artifact disagree."""
    out = []
    if str(event.get("espn_event_id")) != str(artifact_event["espn_event_id"]):
        out.append("ESPN event id differs from the approved artifact")
    if quote["odds_event_id"] != artifact_event["odds_event_id"]:
        out.append("odds event id differs from the approved artifact")
    try:
        qh, qa = team_key(sport, quote["home"]), team_key(sport, quote["away"])
        eh, ea = espn_keys(sport, event)
    except Exception as exc:                          # unknown team name
        return out + [f"team identity unresolved: {type(exc).__name__}"]
    if (qh, qa) != (eh, ea):
        out.append("odds teams do not match ESPN home/away")
    if (quote["home"], quote["away"]) != (artifact_event["home"], artifact_event["away"]):
        out.append("teams differ from the approved artifact")
    return out


def fresh_books(quote, market, max_age_min, now):
    """Tier-1 books quoting `market` whose quote is no older than max_age_min.

    Per-market timestamps are preferred; when a provider omits them the book
    timestamp is used AND the fallback is reported, never hidden.
    """
    cap = mdcore.parse_utc(quote["captured_utc"])
    out, used_fallback = {}, False
    for book, b in quote["books"].items():
        if book not in TIER1 or market not in b["markets"]:
            continue
        stamp = b.get("market_updates", {}).get(market)
        if not stamp:
            stamp, used_fallback = b.get("last_update"), True
        t = mdcore.parse_utc(stamp)
        if not t or not cap or t > cap + timedelta(seconds=60):
            continue
        if (cap - t) > timedelta(minutes=max_age_min) or (now - cap) > timedelta(minutes=max_age_min):
            continue
        out[book] = b["markets"][market]
    return out, used_fallback


def side_price(market, quote_market, selection, line):
    """(price, point) for a selection in one book's market, or (None, None)."""
    if market == "moneyline":
        return quote_market.get(selection), None
    key = selection.lower() if market == "total" else selection
    v = quote_market.get(key)
    if not v:
        return None, None
    return v[1], v[0]


def other_side(market, quote, selection):
    if market == "moneyline" or market == "spread":
        return quote["away"] if selection == quote["home"] else quote["home"]
    return "under" if selection.lower() == "over" else "over"


def consensus(quote, market, selection, line, max_age_min, now):
    """Reference probability for the selection at exactly `line`.

    Median of per-book proportional de-vig over fresh Tier-1 books quoting the
    SAME point. A book on a different number is counted for coverage but never
    interpolated into the probability.
    """
    books, fallback = fresh_books(quote, market, max_age_min, now)
    opp = other_side(market, quote, selection)
    probs, at_line, prices = [], [], {}
    for book, qm in books.items():
        p_sel, pt_sel = side_price(market, qm, selection, line)
        p_opp, pt_opp = side_price(market, qm, opp, line)
        if p_sel is None or p_opp is None:
            continue
        if market != "moneyline" and (pt_sel is None or abs(pt_sel - float(line)) > 1e-9):
            continue
        i_s, i_o = mdcore.implied(p_sel), mdcore.implied(p_opp)
        probs.append(i_s / (i_s + i_o))
        at_line.append(book)
        prices[book] = p_sel
    best_book = None
    if prices:
        # Best price for the bettor = lowest implied probability; ties -> book name.
        best_book = sorted(prices, key=lambda b: (mdcore.implied(prices[b]), b))[0]
    return {"n_books_market": len(books), "n_books_line": len(at_line),
            "reference_probability": round(statistics.median(probs), 6) if probs else None,
            "best_price": prices.get(best_book), "best_book": best_book,
            "prices": prices, "timestamp_fallback": fallback}


def price_not_worse(fresh, approved):
    """True when `fresh` pays at least as much as `approved` (American odds)."""
    return mdcore.implied(fresh) <= mdcore.implied(approved) + 1e-12
