#!/usr/bin/env python
"""One live call to The Odds API. Opt-in, and READ ONLY by default.

    $env:THE_ODDS_API_KEY="..."          # PowerShell
    export THE_ODDS_API_KEY=...          # bash

    python scripts/live_smoke.py                  # 1 request, no writes
    python scripts/live_smoke.py --polls 3 --interval 75
    python scripts/live_smoke.py --ingest         # writes (needs OLP_DATABASE_URL)

Every run spends real request quota, which is why nothing in the test suite
calls it and why --ingest is not the default.

The key is read from the environment, never accepted as an argument (argv is
visible to other processes), never printed, and never written to the database.
The full request URL is never printed either, because the key travels in the
query string.
"""

import argparse
import io
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ingest import QuotaGuard, RateLimiter, RetryPolicy, run_poll_cycle   # noqa: E402
from ingest.http import redact                                            # noqa: E402
from ingest.providers import TheOddsApiProvider                           # noqa: E402

TICK, CROSS = "PASS", "FAIL"


class Report:
    """Buffers output so the whole thing can be scanned for the key before it
    is ever shown. A leak check that runs after printing is not a check."""

    def __init__(self):
        self.buf = io.StringIO()

    def line(self, text=""):
        self.buf.write(str(text) + "\n")

    def kv(self, label, value, width=20):
        self.line(f"  {label:<{width}} {value}")

    def contents(self):
        return self.buf.getvalue()


def poll_once_readonly(provider, report, index=None, total=1):
    provider.new_cycle()
    provider.prefetch()

    events = list(provider.fetch_schedule())
    quotes = list(provider.fetch_odds())
    resp = provider.last_response

    header = "LIVE SMOKE - READ ONLY" if total == 1 else f"POLL {index}/{total} - READ ONLY"
    report.line(header)
    report.line("=" * 62)
    report.kv("HTTP", resp.status)
    report.kv("Events received", len(resp.body))
    report.kv("Events parsed", len(events))
    report.kv("Quotes parsed", len(quotes))
    report.kv("Parse errors", len(provider.last_parse_errors))
    report.line()
    report.line("Quota")
    report.kv("Used", resp.quota_used)
    report.kv("Remaining", resp.quota_remaining)
    report.kv("Last request", resp.quota_last)

    books = sorted({q.sportsbook for q in quotes})
    report.line()
    report.line(f"Bookmakers observed ({len(books)})")
    for book in books:
        report.line(f"  {book}")
    if not books:
        report.line("  (none)")

    markets = {q.market_type for q in quotes}
    report.line()
    report.line("Markets")
    for name in ("MONEYLINE", "SPREAD", "TOTAL"):
        report.kv(name.lower(), TICK if name in markets else "absent", 10)

    if events:
        ev = events[0]
        n = sum(1 for q in quotes if q.source_event_id == ev.source_event_id)
        report.line()
        report.line("Event sample")
        report.kv("provider_event_id", ev.source_event_id)
        report.kv("sport / league", f"{ev.sport} / {ev.league}")
        report.kv("commence_time", f"{ev.scheduled_start:%Y-%m-%d %H:%M:%S}Z")
        report.kv("home", ev.home_team)
        report.kv("away", ev.away_team)
        report.kv("quotes", n)

    if provider.last_parse_errors:
        report.line()
        report.line("Parse errors")
        for err in provider.last_parse_errors[:8]:
            report.line(f"  ! {err['error']}")

    return {"books": set(books), "events": {e.source_event_id for e in events},
            "markets": markets, "quota_remaining": resp.quota_remaining}


def leakage_check(provider, report, rendered):
    """Verify the key is absent from the URL, this report, and a real exception."""
    key = provider.api_key
    url = (f"{provider.base_url}/sports/{provider.sport}/odds"
           f"?apiKey={key}&regions={provider.regions}")

    url_ok = key not in redact(url)
    stdout_ok = key not in rendered

    from ingest.http import _classify
    err = _classify(401, {}, f"rejected apiKey={key}", redact(url))
    exception_ok = key not in redact(str(err))

    report.line()
    report.line("Credential leakage check")
    report.kv("URL", TICK if url_ok else CROSS, 10)
    report.kv("stdout", TICK if stdout_ok else CROSS, 10)
    report.kv("exception", TICK if exception_ok else CROSS, 10)
    return url_ok and stdout_ok and exception_ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", default="americanfootball_nfl")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--markets", default="h2h,spreads,totals")
    ap.add_argument("--bookmakers", default=None,
                    help="comma-separated book keys; narrows the response")
    ap.add_argument("--polls", type=int, default=1,
                    help="repeat the read-only poll N times to check key stability")
    ap.add_argument("--interval", type=float, default=75.0,
                    help="seconds between polls (default 75)")
    ap.add_argument("--ingest", action="store_true",
                    help="write to the database (needs OLP_DATABASE_URL)")
    ap.add_argument("--quota-reserve", type=int, default=25)
    args = ap.parse_args()

    if not os.environ.get("THE_ODDS_API_KEY"):
        print("THE_ODDS_API_KEY is not set.", file=sys.stderr)
        print('  PowerShell:  $env:THE_ODDS_API_KEY="..."', file=sys.stderr)
        print("  bash:        export THE_ODDS_API_KEY=...", file=sys.stderr)
        print("  (never pass it as a command-line argument)", file=sys.stderr)
        return 2

    provider = TheOddsApiProvider(
        sport=args.sport, regions=args.regions,
        markets=args.markets, bookmakers=args.bookmakers)

    if args.ingest:
        return run_ingest(provider, args)

    report = Report()
    report.line(f"provider  {provider.name}")
    report.line(f"sport     {args.sport}   regions={args.regions}   markets={args.markets}")
    report.line(f"cost      1 request per poll x {args.polls} poll(s)")
    report.line()

    seen = []
    try:
        for i in range(1, args.polls + 1):
            if i > 1:
                report.line()
                report.line(f"-- waiting {args.interval:.0f}s --")
                time.sleep(args.interval)
            seen.append(poll_once_readonly(provider, report, i, args.polls))
    except Exception as exc:
        print(report.contents())
        print(f"FAILED: {type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        return 1

    if args.polls > 1:
        report.line()
        report.line("Stability across polls")
        report.line("=" * 62)
        books = [s["books"] for s in seen]
        events = [s["events"] for s in seen]
        stable_books = all(b == books[0] for b in books)
        common_events = set.intersection(*events) if events else set()
        report.kv("bookmaker keys", TICK if stable_books else "CHANGED")
        if not stable_books:
            for i, b in enumerate(books, 1):
                report.line(f"    poll {i}: {sorted(b)}")
        report.kv("events in common", f"{len(common_events)} of {len(events[0])}")
        report.kv("quota consumed", seen[0]["quota_remaining"] - seen[-1]["quota_remaining"]
                  if None not in (seen[0]["quota_remaining"], seen[-1]["quota_remaining"])
                  else "unknown")

    rendered = report.contents()
    clean = leakage_check(provider, report, rendered)

    report.line()
    report.line("DATABASE WRITES: 0")

    out = report.contents()
    # Belt and braces: refuse to print anything containing the key.
    if provider.api_key in out:
        print("ABORTED: the report contained the API key and was not printed.",
              file=sys.stderr)
        return 1

    print(out)
    if not clean:
        print("Credential leakage check FAILED.", file=sys.stderr)
        return 1
    print("Read-only. Re-run with --ingest to write these to the database.")
    return 0


def run_ingest(provider, args) -> int:
    db_url = os.environ.get("OLP_DATABASE_URL")
    if not db_url:
        print("OLP_DATABASE_URL is not set; --ingest needs a target database.",
              file=sys.stderr)
        return 2

    import psycopg

    print("LIVE SMOKE - INGEST (writes to the database)")
    print("=" * 62)

    with psycopg.connect(db_url) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET ROLE service_role")

        result = run_poll_cycle(
            conn, provider,
            retry=RetryPolicy(max_attempts=4),
            limiter=RateLimiter(min_interval=1.0),
            quota=QuotaGuard(reserve=args.quota_reserve),
        )

    print(redact(str(result)))
    for err in result.parse_errors[:8]:
        print(f"  ! {err['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
