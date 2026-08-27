"""A poll cycle wrapped in the resilience machinery.

The important boundary: retries, throttling, quota checks and the circuit
breaker guard the PROVIDER CALL only. Once bytes are in hand, ingestion is
ordinary database work and is not retried at this layer -- the RPCs are already
idempotent, and re-running a partially applied batch behind a retry loop is how
you turn one bad poll into a stuck one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .resilience import (
    CircuitBreaker,
    CircuitOpenError,
    ProviderError,
    QuotaGuard,
    RateLimiter,
    RetryPolicy,
)
from .worker import IngestResult, ingest_odds, ingest_schedule


@dataclass
class CycleResult:
    provider: str
    skipped: bool = False
    skip_reason: Optional[str] = None
    retry_in_seconds: float = 0.0
    attempts: int = 1
    quota_remaining: Optional[int] = None
    schedule: Optional[IngestResult] = None
    odds: Optional[IngestResult] = None
    parse_errors: list = field(default_factory=list)

    def __str__(self) -> str:
        if self.skipped:
            return (f"{self.provider}: SKIPPED ({self.skip_reason})"
                    f"{f', retry in {self.retry_in_seconds}s' if self.retry_in_seconds else ''}")
        return (f"{self.provider}: {self.schedule} | {self.odds} | "
                f"attempts={self.attempts} quota={self.quota_remaining} "
                f"parse_errors={len(self.parse_errors)}")


def run_poll_cycle(
    conn,
    provider,
    *,
    retry: Optional[RetryPolicy] = None,
    limiter: Optional[RateLimiter] = None,
    quota: Optional[QuotaGuard] = None,
    breaker: Optional[CircuitBreaker] = None,
    failure_threshold: int = 5,
    cooldown_seconds: int = 300,
    raise_on_skip: bool = False,
) -> CycleResult:
    """One guarded schedule+odds cycle. What a cron tick runs."""
    retry = retry or RetryPolicy()
    limiter = limiter or RateLimiter()
    quota = quota or QuotaGuard()
    breaker = breaker or CircuitBreaker(
        conn, provider.name, failure_threshold, cooldown_seconds)

    result = CycleResult(provider=provider.name)

    # ---- 1. May we call at all? -------------------------------------------
    try:
        breaker.begin()
    except CircuitOpenError as exc:
        if raise_on_skip:
            raise
        result.skipped = True
        result.skip_reason = "CIRCUIT_OPEN"
        result.retry_in_seconds = exc.retry_in_seconds
        return result

    if hasattr(provider, "new_cycle"):
        provider.new_cycle()

    # ---- 2. The provider call, and only it, is retried ---------------------
    attempts = {"n": 1}

    def on_retry(attempt, delay, exc):
        attempts["n"] = attempt + 1

    try:
        if hasattr(provider, "prefetch"):
            limiter.acquire()
            retry.run(provider.prefetch, on_retry=on_retry)

        quota.check(getattr(provider, "quota_remaining", None))
    except ProviderError as exc:
        # Record the failure against the durable circuit before surfacing it.
        breaker.failure(f"{type(exc).__name__}: {exc}")
        raise

    breaker.success(
        getattr(provider, "quota_remaining", None),
        getattr(provider, "quota_used", None),
    )

    result.attempts = attempts["n"]
    result.quota_remaining = getattr(provider, "quota_remaining", None)

    # ---- 3. Ingestion: ordinary, idempotent database work ------------------
    result.schedule = ingest_schedule(conn, provider)
    result.odds = ingest_odds(conn, provider)
    result.parse_errors = list(getattr(provider, "last_parse_errors", []))

    return result


def feed_health(conn) -> dict:
    """One row an operator can alert on."""
    import json

    with conn.cursor() as cur:
        cur.execute("SELECT public.feed_health_summary_rpc()")
        row = cur.fetchone()
    value = row[0] if row else None
    return json.loads(value) if isinstance(value, str) else value
