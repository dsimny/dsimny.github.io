"""The Odds API (the-odds-api.com) v4 adapter.

Maps the documented v4 payload onto Package #2's `EventRow` / `QuoteRow`. No
migration and no line of the Package #2 worker changes to accommodate it -- that
was the point of the provider seam.

QUOTA. The v4 `/odds` response already contains the fixtures, so fetching
schedule and prices as two calls would bill twice for the same data. One call
per cycle is cached and served to both `fetch_schedule()` and `fetch_odds()`;
`new_cycle()` drops the cache. Requests are billed per region x market, so the
`markets` and `regions` you pass are a cost decision, not just a filter.

PARSING IS TOLERANT, NOT SILENT. A malformed event or outcome is skipped and
recorded in `last_parse_errors` rather than discarding the whole slate -- one
bad row must not cost a poll. The worker writes those errors to the run.

CAPTURED_AT IS OUR FETCH TIME, NOT THE FEED'S last_update.
Corrected after the first live ingest (2026-08-27). v4 reports one `last_update`
per bookmaker -- when THAT BOOK last moved its prices -- and on a real NFL slate
those ran 122-221 seconds behind the fetch. Using them as `captured_at` made
every one of 4,552 quotes arrive already past the 120s placement TTL: the board
was 100% unplaceable.

Worse, it silently defeated the refresh mechanism. A book that does not move its
price reports an unchanged `last_update`, so the de-duplication gap computed as
`new - previous = 0`, the quote was skipped as "fresh", and it could never be
re-recorded. A stable market would have gone dark permanently.

The TTL asks how long since WE confirmed a price. We confirm every quote at
fetch time, so that is what `captured_at` records. The feed's own timestamp is
kept on QuoteRow.provider_updated_at as provenance and is not persisted.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Iterable, List, Optional

from ..http import HttpTransport, redact
from ..provider import EventRow, OddsProvider, QuoteRow
from ..resilience import MalformedPayloadError, PermanentProviderError

UTC = _dt.timezone.utc

BASE_URL = "https://api.the-odds-api.com/v4"

# v4 market key -> our market_type
MARKET_MAP = {
    "h2h": "MONEYLINE",
    "spreads": "SPREAD",
    "totals": "TOTAL",
}


def _parse_iso(value: str) -> _dt.datetime:
    """v4 emits RFC3339 with a trailing Z."""
    if not value:
        raise ValueError("missing timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class TheOddsApiProvider(OddsProvider):
    name = "THE_ODDS_API"

    def __init__(
        self,
        api_key: Optional[str] = None,
        sport: str = "americanfootball_nfl",
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
        bookmakers: Optional[str] = None,
        transport: Optional[HttpTransport] = None,
        base_url: str = BASE_URL,
    ):
        # Read from the environment by default. The key is never a parameter the
        # caller has to hard-code, and never gets written to the database.
        self.api_key = api_key or os.environ.get("THE_ODDS_API_KEY")
        if not self.api_key:
            raise PermanentProviderError(
                "THE_ODDS_API_KEY is not set; export it or pass api_key=")

        self.sport = sport
        self.regions = regions
        self.markets = markets
        self.bookmakers = bookmakers
        self.base_url = base_url.rstrip("/")
        self.transport = transport or HttpTransport()

        self._cache: Optional[list] = None
        self._fetched_at: Optional[_dt.datetime] = None
        self.last_response = None
        self.last_parse_errors: List[dict] = []

    # -- cycle control ------------------------------------------------------

    def new_cycle(self) -> None:
        """Drop the cached payload so the next fetch hits the provider."""
        self._cache = None
        self._fetched_at = None
        self.last_parse_errors = []

    @property
    def fetched_at(self) -> Optional[_dt.datetime]:
        """When the cached payload was observed. Shared by every quote in it, so
        one poll yields one consistent observation time."""
        return self._fetched_at

    @property
    def quota_remaining(self) -> Optional[int]:
        return self.last_response.quota_remaining if self.last_response else None

    @property
    def quota_used(self) -> Optional[int]:
        return self.last_response.quota_used if self.last_response else None

    @property
    def quota_last(self) -> Optional[int]:
        return self.last_response.quota_last if self.last_response else None

    # -- transport ----------------------------------------------------------

    def _fetch(self) -> list:
        if self._cache is not None:
            return self._cache

        params = {
            "apiKey": self.api_key,
            "regions": self.regions,
            "markets": self.markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        if self.bookmakers:
            params["bookmakers"] = self.bookmakers

        response = self.transport.get_json(
            f"{self.base_url}/sports/{self.sport}/odds", params)

        if not isinstance(response.body, list):
            raise MalformedPayloadError(
                f"expected a JSON array of events, got {type(response.body).__name__}")

        # One observation time for the whole payload: every quote in this poll
        # was confirmed at the same instant.
        self._fetched_at = _dt.datetime.now(UTC)
        self.last_response = response
        self._cache = response.body
        return self._cache

    def prefetch(self) -> None:
        """Force the network call. The resilience layer retries THIS, not the
        database work that follows it."""
        self._fetch()

    # -- OddsProvider -------------------------------------------------------

    def fetch_schedule(self) -> Iterable[EventRow]:
        rows = []
        for raw in self._fetch():
            try:
                rows.append(EventRow(
                    source_event_id=self._require(raw, "id"),
                    home_team=self._require(raw, "home_team"),
                    away_team=self._require(raw, "away_team"),
                    scheduled_start=_parse_iso(self._require(raw, "commence_time")),
                    sport="NFL",
                    league="NFL",
                ))
            except Exception as exc:
                self._note(raw, f"event: {exc}")
        return rows

    def fetch_odds(self) -> Iterable[QuoteRow]:
        quotes: List[QuoteRow] = []

        for raw in self._fetch():
            event_id = raw.get("id")
            if not event_id:
                self._note(raw, "odds: event has no id")
                continue

            for book in raw.get("bookmakers") or []:
                book_key = book.get("key")
                if not book_key:
                    self._note(book, "odds: bookmaker has no key")
                    continue

                book_update = book.get("last_update")

                for market in book.get("markets") or []:
                    market_type = MARKET_MAP.get(market.get("key"))
                    if market_type is None:
                        continue        # a market we do not model; not an error

                    # Per-market timestamp when present -- it is closer to the
                    # truth than the bookmaker-level one.
                    stamp = market.get("last_update") or book_update

                    for outcome in market.get("outcomes") or []:
                        try:
                            quotes.append(self._outcome_to_quote(
                                event_id, market_type, book_key, stamp, outcome))
                        except Exception as exc:
                            self._note(outcome,
                                       f"odds[{event_id}/{book_key}/{market_type}]: {exc}")

        return quotes

    # -- internals ----------------------------------------------------------

    def _outcome_to_quote(self, event_id, market_type, book_key, stamp, outcome) -> QuoteRow:
        name = outcome.get("name")
        if not name:
            raise ValueError("outcome has no name")

        price = outcome.get("price")
        if price is None:
            raise ValueError("outcome has no price")
        price = int(price)

        point = outcome.get("point")
        line = None if market_type == "MONEYLINE" else point
        if market_type in ("SPREAD", "TOTAL") and line is None:
            raise ValueError(f"{market_type} outcome has no point")

        # Totals are OVER/UNDER; sides are the team name as the same feed spells
        # it in home_team/away_team, so selections and events always agree.
        selection = name.upper() if market_type == "TOTAL" else name

        # THE observation time is ours, not the feed's. See the module docstring.
        provider_updated_at = _parse_iso(stamp) if stamp else None

        return QuoteRow(
            source_event_id=event_id,
            market_type=market_type,
            selection=selection,
            price=price,
            sportsbook=book_key,
            line=None if line is None else float(line),
            captured_at=self._fetched_at or _dt.datetime.now(UTC),
            provider_updated_at=provider_updated_at,
        )

    @staticmethod
    def _require(raw: dict, field: str):
        value = raw.get(field)
        if value in (None, ""):
            raise ValueError(f"missing {field}")
        return value

    def _note(self, raw, message: str) -> None:
        self.last_parse_errors.append({
            "error": redact(message),
            "fragment": redact(str(raw)[:200]),
        })
