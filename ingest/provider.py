"""Provider boundary for schedule and odds ingestion.

No provider has been chosen yet, so this is the seam a real feed drops into.
Everything downstream of these two dataclasses is provider-agnostic: the worker
and the database RPCs never learn who supplied the data.

To add a real provider (The Odds API, SportsDataIO, a league feed), subclass
`OddsProvider` and map its payload onto `EventRow` / `QuoteRow`. Nothing in
`worker.py` or in any migration needs to change.
"""

from __future__ import annotations

import abc
import datetime as _dt
from dataclasses import dataclass
from typing import Iterable, Optional

MARKET_TYPES = ("MONEYLINE", "SPREAD", "TOTAL")


@dataclass(frozen=True)
class EventRow:
    """One scheduled fixture, normalised."""

    source_event_id: str
    home_team: str
    away_team: str
    scheduled_start: _dt.datetime
    sport: str = "NFL"
    league: str = "NFL"

    def __post_init__(self):
        if not self.source_event_id.strip():
            raise ValueError("source_event_id is required")
        if self.scheduled_start.tzinfo is None:
            raise ValueError(
                f"scheduled_start for {self.source_event_id} must be timezone-aware; "
                "naive datetimes silently shift the whole slate"
            )


@dataclass(frozen=True)
class QuoteRow:
    """One price, from one book, for one selection."""

    source_event_id: str
    market_type: str
    selection: str
    price: int
    sportsbook: str
    line: Optional[float] = None
    captured_at: Optional[_dt.datetime] = None
    is_in_play: bool = False

    def __post_init__(self):
        if self.market_type not in MARKET_TYPES:
            raise ValueError(f"unknown market_type {self.market_type!r}")
        if -100 < self.price < 100:
            raise ValueError(
                f"american odds must be <= -100 or >= 100, got {self.price}"
            )
        if self.market_type == "MONEYLINE" and self.line is not None:
            raise ValueError("MONEYLINE carries no line")
        if self.market_type in ("SPREAD", "TOTAL") and self.line is None:
            raise ValueError(f"{self.market_type} requires a line")
        if self.captured_at is not None and self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")


class OddsProvider(abc.ABC):
    """A source of schedules and prices."""

    #: Recorded against every snapshot and ingestion run.
    name: str = "UNKNOWN"

    @abc.abstractmethod
    def fetch_schedule(self) -> Iterable[EventRow]:
        """Upcoming fixtures."""

    @abc.abstractmethod
    def fetch_odds(self) -> Iterable[QuoteRow]:
        """Current prices, keyed back to fixtures by source_event_id."""
