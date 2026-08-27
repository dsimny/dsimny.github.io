"""Transport resilience: timeouts, retries, throttling, quota, circuit breaker.

All of this is INTEGRATION concern and lives in the worker. None of it belongs
in the database, which owns the domain rules. The one exception is circuit
state, which is persisted because the worker is a fresh process on every tick
(see migration 033).

Nothing here knows what a ticket, a chapter or a postponement is.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


# =============================================================================
# Errors
# =============================================================================

class ProviderError(Exception):
    """Base for anything that went wrong talking to a provider."""

    retryable = False


class TransientProviderError(ProviderError):
    """Worth retrying: 5xx, timeouts, connection resets."""

    retryable = True

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class RateLimitedError(TransientProviderError):
    """429. Retryable, but only after the server's own retry-after."""

    def __init__(self, message, retry_after=None, status=429):
        super().__init__(message, status=status)
        self.retry_after = retry_after


class PermanentProviderError(ProviderError):
    """4xx other than 429: a bad key or a bad request. Retrying cannot help."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class QuotaExhaustedError(PermanentProviderError):
    """The account's request allowance is spent. Not a transport fault."""


class CircuitOpenError(ProviderError):
    """The breaker refused the call before it was made."""

    def __init__(self, message, retry_in_seconds=0):
        super().__init__(message)
        self.retry_in_seconds = retry_in_seconds


class MalformedPayloadError(PermanentProviderError):
    """The response parsed as JSON but is not the shape we require."""


# =============================================================================
# Retry
# =============================================================================

@dataclass
class RetryPolicy:
    """Exponential backoff with FULL jitter.

    Full jitter (sleep uniformly in [0, backoff]) rather than equal jitter or
    none, because every worker instance retries on the same schedule otherwise
    and a recovering provider gets a synchronised thundering herd at exactly the
    moment it is least able to take one.
    """

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    # Injected so tests do not actually sleep.
    sleeper: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)

    def delay_for(self, attempt: int) -> float:
        """Full-jittered delay before `attempt` (1-based)."""
        ceiling = min(self.max_delay, self.base_delay * (self.multiplier ** (attempt - 1)))
        return self.rng.uniform(0, ceiling)

    def run(self, call: Callable[[], object], on_retry: Optional[Callable] = None):
        """Invoke `call`, retrying transient failures. Returns its result."""
        last = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return call()
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == self.max_attempts:
                    raise

                delay = self.delay_for(attempt)
                # A 429 that names its own retry-after wins: the server knows
                # better than our backoff curve does.
                server_hint = getattr(exc, "retry_after", None)
                if server_hint:
                    delay = max(delay, float(server_hint))

                if on_retry:
                    on_retry(attempt, delay, exc)
                self.sleeper(delay)
        raise last  # pragma: no cover


# =============================================================================
# Throttle
# =============================================================================

@dataclass
class RateLimiter:
    """Minimum spacing between calls, so we never become the abusive client."""

    min_interval: float = 0.0
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _last: Optional[float] = None

    def acquire(self) -> float:
        if self.min_interval <= 0:
            return 0.0
        now = self.clock()
        if self._last is None:
            self._last = now
            return 0.0
        wait = self.min_interval - (now - self._last)
        if wait > 0:
            self.sleeper(wait)
            self._last = self.clock()
            return wait
        self._last = now
        return 0.0


# =============================================================================
# Quota
# =============================================================================

@dataclass
class QuotaGuard:
    """Refuse to spend the last of a metered allowance on routine polling.

    The Odds API bills per request and reports what is left on every response.
    Reserving a floor means a quota-exhausting Sunday cannot leave the operator
    with no requests for the thing they actually need.
    """

    reserve: int = 25

    def check(self, remaining: Optional[int]) -> None:
        if remaining is None:
            return
        if remaining <= 0:
            raise QuotaExhaustedError(
                f"provider quota exhausted (remaining={remaining})")
        if remaining < self.reserve:
            raise QuotaExhaustedError(
                f"provider quota {remaining} is below the reserve of {self.reserve}; "
                "refusing routine polling")


# =============================================================================
# Circuit breaker, backed by the database
# =============================================================================

@dataclass
class CircuitBreaker:
    """Thin client over the provider_health RPCs.

    Deliberately holds no state of its own: two workers ticking concurrently
    must see one shared circuit, and a restarted worker must not forget that the
    provider is down.
    """

    conn: object
    provider: str
    failure_threshold: int = 5
    cooldown_seconds: int = 300

    def _scalar(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            value = row[0] if row else None
            return json.loads(value) if isinstance(value, str) else value

    def begin(self) -> dict:
        state = self._scalar(
            "SELECT public.provider_attempt_begin_rpc(%s, %s, %s)",
            (self.provider, self.failure_threshold, self.cooldown_seconds))
        if not state["allowed"]:
            raise CircuitOpenError(
                f"circuit open for {self.provider}: "
                f"{state['consecutive_failures']} consecutive failures, "
                f"retry in {state['retry_in_seconds']}s",
                retry_in_seconds=state["retry_in_seconds"])
        return state

    def success(self, quota_remaining=None, quota_used=None) -> dict:
        return self._scalar(
            "SELECT public.provider_attempt_success_rpc(%s, %s, %s)",
            (self.provider, quota_remaining, quota_used))

    def failure(self, error: str) -> dict:
        return self._scalar(
            "SELECT public.provider_attempt_failure_rpc(%s, %s, %s)",
            (self.provider, str(error)[:2000], self.failure_threshold))
