"""Strict, prospective market observations. Never selects or grades a play.

Legacy fp-v0.4 arithmetic remains in market.py for faithful settlement/replay.
New research must explicitly use this version and preserve the source capture.
"""
from datetime import datetime, timezone
import math
import statistics

VERSION = "market-input-v1"
SPORT_KEYS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}


class InvalidMarket(ValueError):
    pass


def utc(value):
    if not isinstance(value, str):
        raise InvalidMarket("missing timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidMarket("invalid timestamp") from exc
    if stamp.tzinfo is None:
        raise InvalidMarket("timestamp requires timezone")
    return stamp.astimezone(timezone.utc)


def probability(price):
    if (isinstance(price, bool) or not isinstance(price, (int, float))
            or not math.isfinite(price) or abs(price) < 100):
        raise InvalidMarket("invalid American moneyline")
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def event_at(snapshot, sport, event_id, away, home, kickoff, asof,
             window_hours=(18, 30)):
    """No name-only join, no schedule guessing and no future capture leakage."""
    if sport not in SPORT_KEYS or snapshot.get("sport_key") != SPORT_KEYS[sport]:
        raise InvalidMarket("sport routing mismatch")
    if snapshot.get("capture_role") != "scheduled" or snapshot.get("tier") != "A":
        raise InvalidMarket("not scheduled first-party evidence")
    captured, decision, kick = utc(snapshot.get("captured_utc")), utc(asof), utc(kickoff)
    if captured > decision or captured >= kick:
        raise InvalidMarket("capture after decision or kickoff")
    if not window_hours[0] <= (kick - captured).total_seconds() / 3600 <= window_hours[1]:
        raise InvalidMarket("capture outside declared observation window")
    if not event_id or not away or not home or away == home:
        raise InvalidMarket("missing event identity")
    matches = [e for e in snapshot.get("events", []) if e.get("odds_api_event_id") == event_id]
    if len(matches) != 1:
        raise InvalidMarket("event ID missing or repeated")
    ev = matches[0]
    if ((ev.get("away_raw"), ev.get("home_raw")) != (away, home)
            or utc(ev.get("commence_time")) != kick):
        raise InvalidMarket("event identity or schedule changed")
    # A second ID for the same fixture is also ambiguous, not extra coverage.
    if sum((e.get("away_raw"), e.get("home_raw")) == (away, home)
           for e in snapshot.get("events", [])) != 1:
        raise InvalidMarket("fixture has multiple event identities")
    return ev, captured


def quotes(ev, captured, max_age_seconds=900):
    """Return complete valid two-way markets and explicit rejection reasons.

Market timestamps are mandatory. Older snapshots that lack them remain useful
legacy evidence but cannot silently pass as validated prospective observations.
"""
    if not isinstance(captured, datetime) or captured.tzinfo is None:
        raise InvalidMarket("capture requires timezone")
    away, home = ev.get("away_raw"), ev.get("home_raw")
    if not away or not home or away == home:
        raise InvalidMarket("invalid outcome identities")
    accepted, rejected, seen = {}, {}, set()
    for bk in ev.get("books", []):
        book = bk.get("book")
        if not isinstance(book, str) or not book:
            raise InvalidMarket("missing bookmaker identity")
        if book in seen:
            raise InvalidMarket("duplicate bookmaker")
        seen.add(book)
        try:
            stamp = utc((bk.get("market_last_update") or {}).get("h2h"))
            age = (captured - stamp).total_seconds()
            if not 0 <= age <= max_age_seconds:
                raise InvalidMarket("stale or future market timestamp")
            outcomes = (bk.get("markets") or {}).get("h2h")
            if (not isinstance(outcomes, list) or len(outcomes) != 2
                    or {o.get("name") for o in outcomes} != {away, home}):
                raise InvalidMarket("moneyline must contain exactly both teams")
            if any(o.get("point") is not None for o in outcomes):
                raise InvalidMarket("spread/total point on moneyline")
            named = {o['name']: o.get('price') for o in outcomes}
            for p in named.values():
                probability(p)
            accepted[book] = named
        except InvalidMarket as exc:
            rejected[book] = str(exc)
    return accepted, rejected


def consensus(q, away, home, min_books=5):
    """Median implied probabilities, then proportional de-vig; no edge claim."""
    if away == home or len(q) < min_books:
        raise InvalidMarket("insufficient complete markets")
    if any(set(v) != {away, home} for v in q.values()):
        raise InvalidMarket("incomplete or mismatched market")
    ia = statistics.median(probability(v[away]) for v in q.values())
    ih = statistics.median(probability(v[home]) for v in q.values())
    return {"input_version": VERSION, "fair_away": ia / (ia + ih),
            "fair_home": ih / (ia + ih), "overround_pts": 100 * (ia + ih - 1),
            "n_books": len(q), "recommendation": "PASS - research observation only"}
