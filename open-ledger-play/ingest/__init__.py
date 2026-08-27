"""Market ingestion for Open Ledger Play.

Package #2 defines the provider seam and the ingestion worker.
Package #3 adds a real provider (The Odds API) and the resilience layer.

The database owns every domain rule; this package only moves data into it, and
survives a provider that misbehaves while doing so.
"""

from .provider import EventRow, OddsProvider, QuoteRow
from .fixture_provider import FixtureProvider, ScriptedProvider
from .worker import IngestResult, ingest_odds, ingest_schedule, poll_once
from .resilience import (
    CircuitBreaker,
    CircuitOpenError,
    MalformedPayloadError,
    PermanentProviderError,
    ProviderError,
    QuotaExhaustedError,
    QuotaGuard,
    RateLimitedError,
    RateLimiter,
    RetryPolicy,
    TransientProviderError,
)
from .http import HttpResponse, HttpTransport, redact
from .resilient import CycleResult, feed_health, run_poll_cycle

__all__ = [
    "EventRow", "QuoteRow", "OddsProvider",
    "FixtureProvider", "ScriptedProvider",
    "IngestResult", "ingest_schedule", "ingest_odds", "poll_once",
    "RetryPolicy", "RateLimiter", "QuotaGuard", "CircuitBreaker",
    "ProviderError", "TransientProviderError", "PermanentProviderError",
    "RateLimitedError", "QuotaExhaustedError", "CircuitOpenError",
    "MalformedPayloadError",
    "HttpTransport", "HttpResponse", "redact",
    "run_poll_cycle", "CycleResult", "feed_health",
]
