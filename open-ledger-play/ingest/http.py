"""Minimal HTTP transport, stdlib only.

Its whole job is to turn wire outcomes into the error taxonomy in
resilience.py, so that everything above it can reason about "retryable" without
knowing anything about HTTP.

The API key travels in the query string (The Odds API takes it that way), so
every string this module can emit -- error messages, repr, logs -- is passed
through `redact()` first. A key that leaks into an exception ends up in logs,
and logs end up in places keys should not be.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .resilience import (
    MalformedPayloadError,
    PermanentProviderError,
    RateLimitedError,
    TransientProviderError,
)

_SECRET_PARAMS = ("apiKey", "api_key", "key", "token")
_REDACTION = "***REDACTED***"


def redact(text: str) -> str:
    """Strip anything that looks like a credential out of a string."""
    if not text:
        return text
    out = str(text)
    for param in _SECRET_PARAMS:
        out = re.sub(rf"({re.escape(param)}=)[^&\s\"']+", rf"\1{_REDACTION}", out,
                     flags=re.IGNORECASE)
    return out


@dataclass
class HttpResponse:
    status: int
    body: object
    headers: dict = field(default_factory=dict)

    def _int_header(self, name) -> Optional[int]:
        raw = self.headers.get(name) or self.headers.get(name.lower())
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @property
    def quota_remaining(self) -> Optional[int]:
        return self._int_header("x-requests-remaining")

    @property
    def quota_used(self) -> Optional[int]:
        return self._int_header("x-requests-used")

    @property
    def quota_last(self) -> Optional[int]:
        """Cost of the most recent call. Billed as regions x markets, so this is
        how you find out what a given parameter set actually costs."""
        return self._int_header("x-requests-last")


class HttpTransport:
    """Injectable so tests can substitute a fake without touching the network."""

    def __init__(self, timeout: float = 10.0, user_agent: str = "open-ledger-play/1.0"):
        self.timeout = timeout
        self.user_agent = user_agent

    def get_json(self, url: str, params: Optional[dict] = None) -> HttpResponse:
        full = url
        if params:
            full = f"{url}?{urllib.parse.urlencode(params)}"
        safe = redact(full)

        request = urllib.request.Request(full, headers={"User-Agent": self.user_agent})

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                status = resp.status
        except urllib.error.HTTPError as exc:
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            body = ""
            try:
                body = exc.read().decode("utf-8")[:500]
            except Exception:
                pass
            raise _classify(exc.code, headers, redact(body), safe) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            # No response at all: DNS, refused, reset, timed out. Always worth
            # another go.
            raise TransientProviderError(
                f"transport failure for {safe}: {redact(str(exc))}") from None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedPayloadError(
                f"non-JSON response from {safe}: {redact(str(exc))}") from None

        return HttpResponse(status=status, body=parsed, headers=headers)


def _classify(status: int, headers: dict, body: str, safe_url: str):
    """Map an HTTP status onto the retry taxonomy."""
    if status == 429:
        retry_after = headers.get("retry-after")
        try:
            retry_after = float(retry_after) if retry_after else None
        except ValueError:
            retry_after = None
        return RateLimitedError(
            f"rate limited by {safe_url}: {body}", retry_after=retry_after)

    if status in (401, 403):
        # A bad or unauthorised key. Retrying burns quota and never succeeds.
        return PermanentProviderError(
            f"authentication rejected ({status}) for {safe_url}: {body}", status=status)

    if status == 422:
        return PermanentProviderError(
            f"provider rejected the request ({status}) for {safe_url}: {body}",
            status=status)

    if 500 <= status < 600:
        return TransientProviderError(
            f"provider server error ({status}) for {safe_url}: {body}", status=status)

    if 400 <= status < 500:
        return PermanentProviderError(
            f"provider client error ({status}) for {safe_url}: {body}", status=status)

    return TransientProviderError(
        f"unexpected status {status} for {safe_url}: {body}", status=status)
