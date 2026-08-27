"""A deterministic, entirely offline provider.

Package #1 section 32 requires that tests never depend on a live odds API. This
provider satisfies that and doubles as the reference implementation showing what
a real adapter has to produce.

Prices walk a fixed pseudo-random sequence seeded from the event id, so repeated
polls are reproducible run to run while still exercising the "price moved"
branch of ingestion.
"""

from __future__ import annotations

import datetime as _dt
import random
from typing import Iterable, List, Optional

from .provider import EventRow, OddsProvider, QuoteRow

BOOKS = ("BOOK_A", "BOOK_B")


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class FixtureProvider(OddsProvider):
    name = "FIXTURE"

    def __init__(
        self,
        events: int = 3,
        starts_in: _dt.timedelta = _dt.timedelta(hours=3),
        seed: int = 1729,
        books: Iterable[str] = BOOKS,
    ):
        self._events = events
        self._starts_in = starts_in
        self._seed = seed
        self._books = tuple(books)
        self._poll = 0
        self._schedule_override: dict[str, _dt.datetime] = {}

    # -- schedule -----------------------------------------------------------

    def _event_rows(self) -> List[EventRow]:
        base = _utcnow() + self._starts_in
        rows = []
        for i in range(1, self._events + 1):
            src = f"NFL-FIX-{i}"
            rows.append(
                EventRow(
                    source_event_id=src,
                    home_team=f"HOME{i}",
                    away_team=f"AWAY{i}",
                    scheduled_start=self._schedule_override.get(
                        src, base + _dt.timedelta(minutes=30 * (i - 1))
                    ),
                )
            )
        return rows

    def fetch_schedule(self) -> Iterable[EventRow]:
        return self._event_rows()

    def reschedule(self, source_event_id: str, new_start: _dt.datetime) -> None:
        """Make the feed report a moved start time on its next poll."""
        self._schedule_override[source_event_id] = new_start

    # -- odds ---------------------------------------------------------------

    def fetch_odds(self) -> Iterable[QuoteRow]:
        self._poll += 1
        now = _utcnow()
        quotes: List[QuoteRow] = []

        for ev in self._event_rows():
            rng = random.Random(f"{self._seed}:{ev.source_event_id}:{self._poll}")
            for book in self._books:
                drift = rng.choice([0, 0, 0, -5, 5])
                quotes.append(
                    QuoteRow(
                        source_event_id=ev.source_event_id,
                        market_type="SPREAD",
                        selection=ev.home_team,
                        line=-3.0,
                        price=-110 + drift,
                        sportsbook=book,
                        captured_at=now,
                    )
                )
                quotes.append(
                    QuoteRow(
                        source_event_id=ev.source_event_id,
                        market_type="MONEYLINE",
                        selection=ev.home_team,
                        price=-155 + drift,
                        sportsbook=book,
                        captured_at=now,
                    )
                )
        return quotes


class ScriptedProvider(OddsProvider):
    """Returns exactly the rows it is handed. For precise test scenarios."""

    name = "SCRIPTED"

    def __init__(
        self,
        events: Optional[List[EventRow]] = None,
        quotes: Optional[List[QuoteRow]] = None,
        name: str = "SCRIPTED",
    ):
        self.events = events or []
        self.quotes = quotes or []
        self.name = name

    def fetch_schedule(self) -> Iterable[EventRow]:
        return list(self.events)

    def fetch_odds(self) -> Iterable[QuoteRow]:
        return list(self.quotes)
