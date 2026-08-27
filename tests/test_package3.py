"""OLP-M1 Package #3 -- The Odds API integration and resilience.

Nothing in this file touches the network. Every provider response is either a
recorded v4 payload or a fake transport, which is what lets the failure modes
that matter -- 429, 500, timeout, malformed body, outage, quota exhaustion --
be exercised deliberately rather than waited for.
"""

import datetime as dt
import json
import pathlib
import random
import sys
import uuid

import harness as h
from test_acceptance import new_user, open_chapter, place

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ingest import (                                                  # noqa: E402
    CircuitBreaker, CircuitOpenError, HttpResponse, MalformedPayloadError,
    PermanentProviderError, QuotaExhaustedError, QuotaGuard, RateLimitedError,
    RateLimiter, RetryPolicy, TransientProviderError, redact, run_poll_cycle,
    feed_health,
)
from ingest.http import _classify                                     # noqa: E402
from ingest.providers import TheOddsApiProvider                       # noqa: E402

UTC = dt.timezone.utc
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
FAKE_KEY = "test-key-do-not-use-abc123"


# -- fakes --------------------------------------------------------------------

class FakeTransport:
    """Serves scripted responses; records every call it was asked to make."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def ok(body, remaining=450, used=50):
    return HttpResponse(
        status=200, body=body,
        headers={"x-requests-remaining": str(remaining), "x-requests-used": str(used)})


def sample_payload():
    return json.loads((FIXTURES / "the_odds_api_v4_sample.json").read_text(encoding="utf-8"))


def live_payload(events=2, minutes_ahead=180, price=-110):
    """v4-shaped payload with times relative to now, for end-to-end tests."""
    now = dt.datetime.now(UTC)
    start = now + dt.timedelta(minutes=minutes_ahead)
    stamp = now.isoformat().replace("+00:00", "Z")
    out = []
    for i in range(1, events + 1):
        out.append({
            "id": f"live-evt-{i}",
            "sport_key": "americanfootball_nfl",
            "commence_time": (start + dt.timedelta(minutes=30 * i)).isoformat().replace("+00:00", "Z"),
            "home_team": f"Home {i}",
            "away_team": f"Away {i}",
            "bookmakers": [{
                "key": book, "title": book.title(), "last_update": stamp,
                "markets": [
                    {"key": "h2h", "last_update": stamp, "outcomes": [
                        {"name": f"Home {i}", "price": price},
                        {"name": f"Away {i}", "price": 100 - price}]},
                    {"key": "spreads", "last_update": stamp, "outcomes": [
                        {"name": f"Home {i}", "price": price, "point": -3.0},
                        {"name": f"Away {i}", "price": price, "point": 3.0}]},
                ]} for book in ("draftkings", "fanduel")],
        })
    return out


def provider(*responses, **kw):
    return TheOddsApiProvider(
        api_key=FAKE_KEY, transport=FakeTransport(*responses), **kw)


def no_sleep_retry(**kw):
    kw.setdefault("sleeper", lambda _s: None)
    kw.setdefault("rng", random.Random(7))
    return RetryPolicy(**kw)


# =============================================================================
# Parsing the v4 payload
# =============================================================================

def t01_v4_payload_maps_to_domain_rows():
    p = provider(ok(sample_payload()))

    events = list(p.fetch_schedule())
    assert len(events) == 2, events
    texans = events[0]
    assert texans.source_event_id == "e912304de2b2ce35b473ce2ecd3d1502"
    assert texans.home_team == "Houston Texans"
    assert texans.away_team == "New Orleans Saints"
    assert texans.scheduled_start == dt.datetime(2026, 9, 14, 17, 0, tzinfo=UTC)

    quotes = list(p.fetch_odds())
    by_key = {(q.source_event_id[:6], q.market_type, q.selection, q.sportsbook): q
              for q in quotes}

    ml = by_key[("e91230", "MONEYLINE", "Houston Texans", "draftkings")]
    assert ml.price == -110 and ml.line is None

    sp = by_key[("e91230", "SPREAD", "Houston Texans", "draftkings")]
    assert (sp.price, sp.line) == (-110, -2.5)

    over = by_key[("e91230", "TOTAL", "OVER", "draftkings")]
    assert (over.price, over.line) == (-112, 42.5), "totals normalise to OVER/UNDER"

    dog = by_key[("b7c1a0", "MONEYLINE", "Dallas Cowboys", "draftkings")]
    assert dog.price == 135, "positive american odds survive intact"

    # A market we do not model is ignored, not an error.
    assert not any(q.market_type not in ("MONEYLINE", "SPREAD", "TOTAL") for q in quotes)
    assert p.last_parse_errors == [], p.last_parse_errors

    # Per-market last_update becomes captured_at.
    assert sp.captured_at == dt.datetime(2026, 9, 14, 12, 10, 31, tzinfo=UTC)


def t02_one_http_call_serves_schedule_and_odds():
    """Quota discipline: /odds already carries the fixtures."""
    p = provider(ok(sample_payload()))
    list(p.fetch_schedule())
    list(p.fetch_odds())
    list(p.fetch_odds())
    assert len(p.transport.calls) == 1, p.transport.calls

    url, params = p.transport.calls[0]
    assert url.endswith("/sports/americanfootball_nfl/odds")
    assert params["oddsFormat"] == "american"
    assert params["apiKey"] == FAKE_KEY

    p.new_cycle()
    list(p.fetch_schedule())
    assert len(p.transport.calls) == 2, "new_cycle must drop the cache"


def t03_malformed_rows_skipped_not_fatal():
    payload = sample_payload()
    payload.append({"id": "broken-1", "home_team": "X"})               # no teams/time
    payload[0]["bookmakers"][0]["markets"][1]["outcomes"].append(
        {"name": "Ghost", "price": None})                              # no price
    payload[0]["bookmakers"][0]["markets"][2]["outcomes"].append(
        {"name": "Over", "price": -110})                               # totals w/o point

    p = provider(ok(payload))
    events = list(p.fetch_schedule())
    quotes = list(p.fetch_odds())

    assert len(events) == 2, "the good events survive"
    assert len(quotes) >= 9, len(quotes)
    assert len(p.last_parse_errors) == 3, p.last_parse_errors
    joined = " ".join(e["error"] for e in p.last_parse_errors)
    assert "missing" in joined or "no price" in joined or "no point" in joined, joined


def t04_timestamps_are_utc_aware():
    p = provider(ok(sample_payload()))
    for ev in p.fetch_schedule():
        assert ev.scheduled_start.tzinfo is not None
    for q in p.fetch_odds():
        assert q.captured_at is None or q.captured_at.tzinfo is not None

    # The domain row refuses naive datetimes outright.
    from ingest import EventRow
    try:
        EventRow("x", "H", "A", dt.datetime(2026, 1, 1))
        raise AssertionError("naive datetime should be rejected")
    except ValueError as exc:
        assert "timezone-aware" in str(exc)


# =============================================================================
# Error taxonomy
# =============================================================================

def t05_http_status_classification():
    assert isinstance(_classify(429, {"retry-after": "12"}, "", "u"), RateLimitedError)
    assert _classify(429, {"retry-after": "12"}, "", "u").retry_after == 12.0
    assert _classify(500, {}, "", "u").retryable is True
    assert _classify(503, {}, "", "u").retryable is True
    assert isinstance(_classify(401, {}, "", "u"), PermanentProviderError)
    assert _classify(401, {}, "", "u").retryable is False
    assert _classify(403, {}, "", "u").retryable is False
    assert _classify(422, {}, "", "u").retryable is False
    assert _classify(404, {}, "", "u").retryable is False


def t06_transport_failures_are_transient():
    import socket
    import urllib.error
    from ingest.http import HttpTransport

    transport = HttpTransport(timeout=0.01)
    for boom in (urllib.error.URLError("dns"), socket.timeout("slow"),
                 ConnectionResetError("reset")):
        def fake_urlopen(*a, **k):
            raise boom
        import ingest.http as mod
        original = mod.urllib.request.urlopen
        mod.urllib.request.urlopen = fake_urlopen
        try:
            transport.get_json("https://example.invalid/x", {"apiKey": FAKE_KEY})
            raise AssertionError("should have raised")
        except TransientProviderError as exc:
            assert FAKE_KEY not in str(exc)
        finally:
            mod.urllib.request.urlopen = original


def t07_non_json_body_is_malformed():
    import io
    import ingest.http as mod
    from ingest.http import HttpTransport

    class FakeResp:
        status = 200
        headers = {}
        def read(self): return b"<html>maintenance</html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    original = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = lambda *a, **k: FakeResp()
    try:
        HttpTransport().get_json("https://example.invalid/x", {"apiKey": FAKE_KEY})
        raise AssertionError("should have raised")
    except MalformedPayloadError as exc:
        assert FAKE_KEY not in str(exc)
    finally:
        mod.urllib.request.urlopen = original


def t08_non_array_body_is_malformed():
    p = provider(ok({"message": "Usage plan exceeded"}))
    try:
        p.prefetch()
        raise AssertionError("should have raised")
    except MalformedPayloadError as exc:
        assert "array" in str(exc)


def t09_api_key_is_never_leaked():
    url = f"https://api.the-odds-api.com/v4/x?apiKey={FAKE_KEY}&regions=us"
    assert FAKE_KEY not in redact(url)
    assert "REDACTED" in redact(url)
    for variant in (f"api_key={FAKE_KEY}", f"token={FAKE_KEY}", f"APIKEY={FAKE_KEY}"):
        assert FAKE_KEY not in redact(variant), variant

    # And through a real classification path.
    err = _classify(401, {}, f"apiKey={FAKE_KEY} rejected", "https://x?apiKey=" + FAKE_KEY)
    assert FAKE_KEY not in redact(str(err))


def t10_missing_key_fails_fast():
    import os
    saved = os.environ.pop("THE_ODDS_API_KEY", None)
    try:
        TheOddsApiProvider(transport=FakeTransport(ok([])))
        raise AssertionError("should have refused to construct")
    except PermanentProviderError as exc:
        assert "THE_ODDS_API_KEY" in str(exc)
    finally:
        if saved is not None:
            os.environ["THE_ODDS_API_KEY"] = saved


# =============================================================================
# Retry
# =============================================================================

def t11_transient_failures_are_retried_then_surfaced():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise TransientProviderError("502 bad gateway")

    policy = no_sleep_retry(max_attempts=4)
    try:
        policy.run(flaky)
        raise AssertionError("should have raised")
    except TransientProviderError:
        pass
    assert calls["n"] == 4, calls


def t12_permanent_failures_are_not_retried():
    calls = {"n": 0}

    def bad_key():
        calls["n"] += 1
        raise PermanentProviderError("401 unauthorised")

    try:
        no_sleep_retry(max_attempts=5).run(bad_key)
        raise AssertionError("should have raised")
    except PermanentProviderError:
        pass
    assert calls["n"] == 1, "a bad key must not be retried; it only burns quota"


def t13_backoff_is_bounded_and_jittered():
    policy = no_sleep_retry(base_delay=0.5, multiplier=2.0, max_delay=8.0)
    for attempt, ceiling in ((1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (9, 8.0)):
        samples = [policy.delay_for(attempt) for _ in range(200)]
        assert all(0 <= s <= ceiling for s in samples), (attempt, min(samples), max(samples))
        assert max(samples) > ceiling * 0.5, "full jitter should span the range"


def t14_server_retry_after_wins_over_backoff():
    slept = []
    policy = RetryPolicy(max_attempts=2, base_delay=0.01, max_delay=0.02,
                         sleeper=slept.append, rng=random.Random(1))
    calls = {"n": 0}

    def limited():
        calls["n"] += 1
        raise RateLimitedError("429", retry_after=9.0)

    try:
        policy.run(limited)
    except RateLimitedError:
        pass
    assert slept == [9.0], slept


def t15_success_after_a_blip():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientProviderError("503")
        return "ok"

    assert no_sleep_retry(max_attempts=5).run(flaky) == "ok"
    assert calls["n"] == 3


# =============================================================================
# Quota and throttling
# =============================================================================

def t16_quota_guard_reserves_headroom():
    guard = QuotaGuard(reserve=25)
    guard.check(None)      # unknown quota is not an error
    guard.check(100)

    for bad in (24, 1, 0, -1):
        try:
            guard.check(bad)
            raise AssertionError(f"{bad} should have been refused")
        except QuotaExhaustedError as exc:
            assert not exc.retryable, "quota exhaustion is not a transport blip"


def t17_rate_limiter_spaces_calls():
    clock = {"t": 0.0}
    slept = []

    def sleeper(s):
        slept.append(s)
        clock["t"] += s

    limiter = RateLimiter(min_interval=2.0, sleeper=sleeper, clock=lambda: clock["t"])
    assert limiter.acquire() == 0.0          # first call is free
    assert limiter.acquire() == 2.0          # immediately after -> waits
    clock["t"] += 5.0
    assert limiter.acquire() == 0.0          # enough time has passed
    assert slept == [2.0], slept


# =============================================================================
# Circuit breaker (durable)
# =============================================================================

def t18_circuit_opens_after_threshold():
    admin = h.connect(); h.reset(admin)
    with h.connect_as("service_role") as conn:
        cb = CircuitBreaker(conn, "PROV_A", failure_threshold=3, cooldown_seconds=300)
        for i in range(2):
            state = cb.failure(f"boom {i}")
            assert state["circuit"] == "CLOSED", state
        state = cb.failure("boom 3")
        assert state["circuit"] == "OPEN" and state["tripped"] is True, state

    assert h.row(admin,
        "SELECT circuit, consecutive_failures FROM public.provider_health WHERE provider='PROV_A'"
    ) == ("OPEN", 3)
    admin.close()


def t19_open_circuit_refuses_before_calling():
    admin = h.connect(); h.reset(admin)
    with h.connect_as("service_role") as conn:
        cb = CircuitBreaker(conn, "PROV_B", failure_threshold=2, cooldown_seconds=3600)
        cb.failure("a"); cb.failure("b")
        try:
            cb.begin()
            raise AssertionError("open circuit must refuse")
        except CircuitOpenError as exc:
            assert exc.retry_in_seconds > 0
    admin.close()


def t20_half_open_probe_then_close():
    admin = h.connect(); h.reset(admin)
    with h.connect_as("service_role") as conn:
        cb = CircuitBreaker(conn, "PROV_C", failure_threshold=1, cooldown_seconds=0)
        cb.failure("down")
        assert h.scalar(admin,
            "SELECT circuit FROM public.provider_health WHERE provider='PROV_C'") == "OPEN"

        state = cb.begin()          # cooldown 0 -> probe allowed
        assert state["circuit"] == "HALF_OPEN" and state["reason"] == "PROBE", state

        cb.success(quota_remaining=400, quota_used=100)
        row = h.row(admin,
            """SELECT circuit, consecutive_failures, quota_remaining
               FROM public.provider_health WHERE provider='PROV_C'""")
        assert row == ("CLOSED", 0, 400), row
    admin.close()


def t21_failed_probe_reopens_immediately():
    admin = h.connect(); h.reset(admin)
    with h.connect_as("service_role") as conn:
        cb = CircuitBreaker(conn, "PROV_D", failure_threshold=10, cooldown_seconds=0)
        cb.failure("1")
        h.connect().execute(
            "UPDATE public.provider_health SET circuit='OPEN', opened_at=NOW() WHERE provider='PROV_D'")

        assert cb.begin()["circuit"] == "HALF_OPEN"
        state = cb.failure("probe failed")
        # One failed probe re-opens, even though 2 < threshold of 10.
        assert state["circuit"] == "OPEN", state
    admin.close()


def t22_circuit_state_survives_a_restart():
    """A fresh process must not forget the provider is down."""
    admin = h.connect(); h.reset(admin)
    with h.connect_as("service_role") as conn:
        CircuitBreaker(conn, "PROV_E", failure_threshold=2, cooldown_seconds=3600).failure("x")
        CircuitBreaker(conn, "PROV_E", failure_threshold=2, cooldown_seconds=3600).failure("y")

    # Brand new object, brand new connection -- as a cron tick would be.
    with h.connect_as("service_role") as conn2:
        try:
            CircuitBreaker(conn2, "PROV_E", failure_threshold=2, cooldown_seconds=3600).begin()
            raise AssertionError("restarted worker forgot the open circuit")
        except CircuitOpenError:
            pass
    admin.close()


# =============================================================================
# End-to-end cycles
# =============================================================================

def t23_full_cycle_ingests_a_real_payload():
    admin = h.connect(); h.reset(admin)
    p = provider(ok(live_payload(events=2), remaining=480, used=20))

    with h.connect_as("service_role") as conn:
        result = run_poll_cycle(conn, p, retry=no_sleep_retry())

    assert not result.skipped, result
    assert result.schedule.events_created == 2, result.schedule
    # 2 events x 2 markets x 2 outcomes x 2 books
    assert result.odds.snapshots_written == 16, result.odds
    assert result.odds.snapshots_failed == 0, result.odds.errors
    assert result.quota_remaining == 480
    assert len(p.transport.calls) == 1, "one HTTP call for the whole cycle"

    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 2
    books = {r[0] for r in h.rows(admin, "SELECT DISTINCT sportsbook FROM public.market_snapshots")}
    assert books == {"draftkings", "fanduel"}, books

    assert h.row(admin,
        """SELECT circuit, quota_remaining FROM public.provider_health
           WHERE provider='THE_ODDS_API'""") == ("CLOSED", 480)
    admin.close()
    return "2 events, 16 quotes, 1 HTTP call"


def t24_transient_blip_is_absorbed():
    admin = h.connect(); h.reset(admin)
    p = provider(TransientProviderError("502"), TransientProviderError("502"),
                 ok(live_payload(events=1)))

    with h.connect_as("service_role") as conn:
        result = run_poll_cycle(conn, p, retry=no_sleep_retry(max_attempts=5))

    assert not result.skipped and result.attempts == 3, result
    assert result.schedule.events_created == 1
    assert h.scalar(admin,
        "SELECT circuit FROM public.provider_health WHERE provider='THE_ODDS_API'") == "CLOSED"
    admin.close()
    return "recovered on attempt 3"


def t25_outage_trips_breaker_and_writes_nothing():
    admin = h.connect(); h.reset(admin)
    p = provider(TransientProviderError("connection refused"))

    with h.connect_as("service_role") as conn:
        for _ in range(2):
            try:
                run_poll_cycle(conn, p, retry=no_sleep_retry(max_attempts=2),
                               failure_threshold=2, cooldown_seconds=3600)
            except TransientProviderError:
                pass

        # Circuit is now open: the next cycle is refused WITHOUT calling out.
        before = len(p.transport.calls)
        result = run_poll_cycle(conn, p, retry=no_sleep_retry(max_attempts=2),
                                failure_threshold=2, cooldown_seconds=3600)
        assert result.skipped and result.skip_reason == "CIRCUIT_OPEN", result
        assert result.retry_in_seconds > 0
        assert len(p.transport.calls) == before, "open circuit still called the provider"

    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 0
    assert h.scalar(admin, "SELECT count(*) FROM public.market_snapshots") == 0
    admin.close()
    return "breaker tripped, zero writes, provider not called"


def t26_quota_exhaustion_stops_ingestion():
    admin = h.connect(); h.reset(admin)
    p = provider(ok(live_payload(events=1), remaining=3, used=497))

    with h.connect_as("service_role") as conn:
        try:
            run_poll_cycle(conn, p, retry=no_sleep_retry(), quota=QuotaGuard(reserve=25))
            raise AssertionError("should have refused on quota")
        except QuotaExhaustedError as exc:
            assert "reserve" in str(exc)

    # Nothing ingested, and the failure is recorded against the provider.
    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 0
    assert h.scalar(admin,
        "SELECT consecutive_failures FROM public.provider_health WHERE provider='THE_ODDS_API'") == 1
    admin.close()


def t27_parse_errors_surface_without_losing_the_poll():
    admin = h.connect(); h.reset(admin)
    payload = live_payload(events=2)
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = None
    payload.append({"id": "no-teams"})

    p = provider(ok(payload))
    with h.connect_as("service_role") as conn:
        result = run_poll_cycle(conn, p, retry=no_sleep_retry())

    assert len(result.parse_errors) == 2, result.parse_errors
    assert result.schedule.events_created == 2, "good events still ingested"
    assert result.odds.snapshots_written >= 14, result.odds
    admin.close()
    return f"{len(result.parse_errors)} parse errors, poll still landed"


def t28_dead_feed_fails_closed_and_is_visible():
    """The system already refuses to trade on stale prices. Prove it, and prove
    the operator can tell the difference between a dead feed and a quiet day."""
    admin = h.connect(); h.reset(admin)
    ttl = h.scalar(admin, "SELECT snapshot_ttl_seconds FROM public.system_settings")

    # Two events. The second one's feed stopped reporting more than a TTL ago --
    # its last successful poll is all it has. Back-dating extra rows would NOT
    # model this: ordering is captured_at DESC, so a stale row never becomes the
    # current quote, and snapshots are immutable so the fresh ones cannot be
    # removed. A dark feed is an event whose NEWEST quote is old.
    payload = live_payload(events=2)
    gone_dark = (dt.datetime.now(UTC)
                 - dt.timedelta(seconds=ttl + 60)).isoformat().replace("+00:00", "Z")
    for book in payload[1]["bookmakers"]:
        book["last_update"] = gone_dark
        for market in book["markets"]:
            market["last_update"] = gone_dark

    p = provider(ok(payload))
    with h.connect_as("service_role") as conn:
        run_poll_cycle(conn, p, retry=no_sleep_retry())

    live_event = h.scalar(admin,
        "SELECT id FROM public.events WHERE source_event_id='live-evt-1'")
    dark_event = h.scalar(admin,
        "SELECT id FROM public.events WHERE source_event_id='live-evt-2'")

    # The live event still trades.
    u = new_user(admin, "dead_feed"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT snapshot_id FROM public.current_market_board
           WHERE event_id=%s AND is_placeable LIMIT 1""", (live_event,))
    assert snap is not None, "the healthy event should still be placeable"
    assert place(u, ch, snap, 100) is not None

    # The dark one fails CLOSED: nothing offered, and placement refused.
    assert h.scalar(admin,
        """SELECT count(*) FROM public.current_market_board
           WHERE event_id=%s AND is_placeable""", (dark_event,)) == 0

    newest_dark = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s
           ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1""", (dark_event,))
    h.expect_error(lambda: place(u, ch, newest_dark, 100),
                   "SNAPSHOT_STALE", "T28 fail closed")

    # And it is visible rather than silent.
    with h.connect_as("service_role") as conn:
        health = feed_health(conn)
    assert health["open_events"] == 2, health
    assert health["dark_events"] == 1, health
    assert health["stalest_quote_age_seconds"] >= ttl, health
    assert health["providers"][0]["circuit"] == "CLOSED", health

    rows = {r[0]: r[1] for r in h.rows(admin,
        "SELECT event_id, is_dark FROM public.market_feed_health")}
    assert rows[live_event] is False and rows[dark_event] is True, rows
    admin.close()
    return "healthy event trades, dark event refused and reported"


def t29_repeated_cycles_are_idempotent():
    admin = h.connect(); h.reset(admin)
    payload = live_payload(events=2)
    p = provider(ok(payload))

    with h.connect_as("service_role") as conn:
        first = run_poll_cycle(conn, p, retry=no_sleep_retry())
        second = run_poll_cycle(conn, p, retry=no_sleep_retry())
        third = run_poll_cycle(conn, p, retry=no_sleep_retry())

    assert first.odds.snapshots_written == 16, first.odds
    for later in (second, third):
        assert later.schedule.events_created == 0, later.schedule
        assert later.odds.snapshots_written == 0, "unchanged prices must be skipped"
        assert later.odds.snapshots_skipped == 16, later.odds

    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 2
    assert h.scalar(admin, "SELECT count(*) FROM public.market_snapshots") == 16
    admin.close()
    return "3 cycles -> 2 events, 16 quotes"


# =============================================================================
# Authorization
# =============================================================================

def t30_clients_cannot_touch_provider_health():
    admin = h.connect(); h.reset(admin)
    u = new_user(admin, "prov_snoop")

    def read():
        with h.connect_as("authenticated", u) as c:
            c.execute("SELECT count(*) FROM public.provider_health")
    h.expect_error(read, "permission denied", "T30 read provider_health")

    for sql in ("SELECT public.provider_attempt_begin_rpc('X',5,300)",
                "SELECT public.provider_attempt_success_rpc('X',1,1)",
                "SELECT public.provider_attempt_failure_rpc('X','e',5)",
                "SELECT public.provider_reset_circuit_rpc('X')",
                "SELECT public.feed_health_summary_rpc()"):
        def call(s=sql):
            with h.connect_as("authenticated", u) as c:
                c.execute(s)
        h.expect_error(call, "permission denied", f"T30 {s_short(sql)}")

    def read_health():
        with h.connect_as("authenticated", u) as c:
            c.execute("SELECT count(*) FROM public.market_feed_health")
    h.expect_error(read_health, "permission denied", "T30 market_feed_health")
    admin.close()


def s_short(sql):
    return sql.split("(")[0][-30:]


def t31_rate_limit_without_retry_after_falls_back_to_backoff():
    """The v4 docs have a 429 section but do NOT document a Retry-After header.

    So the header must be treated as a bonus, never a requirement: with it
    absent, backoff has to stand on its own jittered curve.
    """
    slept = []
    policy = RetryPolicy(max_attempts=3, base_delay=1.0, multiplier=2.0,
                         max_delay=8.0, sleeper=slept.append, rng=random.Random(3))

    def limited():
        raise RateLimitedError("429 Too Many Requests")      # no retry_after

    try:
        policy.run(limited)
        raise AssertionError("should have surfaced after max attempts")
    except RateLimitedError as exc:
        assert exc.retry_after is None, "fixture should carry no header"
        assert exc.retryable is True

    assert len(slept) == 2, slept
    assert 0 <= slept[0] <= 1.0, slept          # ceiling base * 2^0
    assert 0 <= slept[1] <= 2.0, slept          # ceiling base * 2^1
    assert all(s == s for s in slept)           # never NaN from float(None)

    # A malformed Retry-After must not crash the classifier either.
    for bad in ("", "soon", None):
        err = _classify(429, {"retry-after": bad} if bad is not None else {}, "", "u")
        assert err.retry_after is None, bad
        assert err.retryable is True


def t32_quota_last_header_is_read():
    """x-requests-last is the per-call cost; it is how you learn what a given
    regions x markets combination actually bills."""
    p = provider(HttpResponse(status=200, body=sample_payload(), headers={
        "x-requests-remaining": "497", "x-requests-used": "3", "x-requests-last": "6"}))
    p.prefetch()
    assert (p.quota_remaining, p.quota_used, p.quota_last) == (497, 3, 6)

    # Absent header degrades to None rather than guessing.
    p2 = provider(HttpResponse(status=200, body=sample_payload(), headers={}))
    p2.prefetch()
    assert (p2.quota_remaining, p2.quota_used, p2.quota_last) == (None, None, None)


def t33_live_smoke_report_renders_offline():
    """The smoke script is what gets pointed at production and spends a real
    request. A rendering bug discovered live wastes that request, so the report
    path is exercised here against the recorded payload."""
    import os
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import live_smoke

    saved = os.environ.get("THE_ODDS_API_KEY")
    os.environ["THE_ODDS_API_KEY"] = "SECRET-KEY-abc123XYZ"
    try:
        p = TheOddsApiProvider(transport=FakeTransport(HttpResponse(
            status=200, body=sample_payload(),
            headers={"x-requests-remaining": "499", "x-requests-used": "1",
                     "x-requests-last": "1"})))

        report = live_smoke.Report()
        summary = live_smoke.poll_once_readonly(p, report)
        rendered = report.contents()
        clean = live_smoke.leakage_check(p, report, rendered)
        out = report.contents()

        assert clean is True, "leakage check should pass on a clean report"
        assert os.environ["THE_ODDS_API_KEY"] not in out, "THE KEY LEAKED INTO THE REPORT"

        for expected in ("LIVE SMOKE - READ ONLY", "HTTP", "Events received",
                         "Parse errors", "Quota", "Last request",
                         "Bookmakers observed", "Markets", "Event sample",
                         "Credential leakage check"):
            assert expected in out, expected

        assert summary["books"] == {"draftkings", "fanduel"}, summary
        assert summary["markets"] == {"MONEYLINE", "SPREAD", "TOTAL"}, summary
        assert summary["quota_remaining"] == 499
        # No full URL is ever rendered.
        assert "apiKey=" not in out or "REDACTED" in out
    finally:
        if saved is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = saved


PACKAGE3 = [
    ("P3-T01", "v4 payload maps to domain rows", t01_v4_payload_maps_to_domain_rows),
    ("P3-T02", "One HTTP call serves schedule and odds", t02_one_http_call_serves_schedule_and_odds),
    ("P3-T03", "Malformed rows skipped, not fatal", t03_malformed_rows_skipped_not_fatal),
    ("P3-T04", "Timestamps are UTC-aware", t04_timestamps_are_utc_aware),
    ("P3-T05", "HTTP status classification", t05_http_status_classification),
    ("P3-T06", "Transport failures are transient", t06_transport_failures_are_transient),
    ("P3-T07", "Non-JSON body is malformed", t07_non_json_body_is_malformed),
    ("P3-T08", "Non-array body is malformed", t08_non_array_body_is_malformed),
    ("P3-T09", "API key is never leaked", t09_api_key_is_never_leaked),
    ("P3-T10", "Missing key fails fast", t10_missing_key_fails_fast),
    ("P3-T11", "Transient failures retried then surfaced", t11_transient_failures_are_retried_then_surfaced),
    ("P3-T12", "Permanent failures are not retried", t12_permanent_failures_are_not_retried),
    ("P3-T13", "Backoff is bounded and jittered", t13_backoff_is_bounded_and_jittered),
    ("P3-T14", "Server retry-after wins over backoff", t14_server_retry_after_wins_over_backoff),
    ("P3-T15", "Success after a blip", t15_success_after_a_blip),
    ("P3-T16", "Quota guard reserves headroom", t16_quota_guard_reserves_headroom),
    ("P3-T17", "Rate limiter spaces calls", t17_rate_limiter_spaces_calls),
    ("P3-T18", "Circuit opens after threshold", t18_circuit_opens_after_threshold),
    ("P3-T19", "Open circuit refuses before calling", t19_open_circuit_refuses_before_calling),
    ("P3-T20", "Half-open probe then close", t20_half_open_probe_then_close),
    ("P3-T21", "Failed probe reopens immediately", t21_failed_probe_reopens_immediately),
    ("P3-T22", "Circuit state survives a restart", t22_circuit_state_survives_a_restart),
    ("P3-T23", "Full cycle ingests a real payload", t23_full_cycle_ingests_a_real_payload),
    ("P3-T24", "Transient blip is absorbed", t24_transient_blip_is_absorbed),
    ("P3-T25", "Outage trips breaker and writes nothing", t25_outage_trips_breaker_and_writes_nothing),
    ("P3-T26", "Quota exhaustion stops ingestion", t26_quota_exhaustion_stops_ingestion),
    ("P3-T27", "Parse errors surface without losing the poll", t27_parse_errors_surface_without_losing_the_poll),
    ("P3-T28", "Dead feed fails closed and is visible", t28_dead_feed_fails_closed_and_is_visible),
    ("P3-T29", "Repeated cycles are idempotent", t29_repeated_cycles_are_idempotent),
    ("P3-T30", "Clients cannot touch provider health", t30_clients_cannot_touch_provider_health),
    ("P3-T31", "429 without Retry-After falls back to backoff", t31_rate_limit_without_retry_after_falls_back_to_backoff),
    ("P3-T32", "x-requests-last header is read", t32_quota_last_header_is_read),
    ("P3-T33", "Live smoke report renders offline", t33_live_smoke_report_renders_offline),
]
