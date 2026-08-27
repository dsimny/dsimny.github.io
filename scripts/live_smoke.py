#!/usr/bin/env python
"""One live call to The Odds API. Opt-in, and dry by default.

    export THE_ODDS_API_KEY=...
    python scripts/live_smoke.py                 # fetch + parse, write nothing
    python scripts/live_smoke.py --ingest        # ...and write to the database

Every invocation spends real request quota, which is why nothing in the test
suite calls it and why --ingest is not the default. The key is read from the
environment, never passed on the command line (argv is visible to other
processes) and never printed.

Cost: exactly one request per run, billed by The Odds API as
regions x markets. Trim --markets and --regions to spend less.
"""

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ingest import QuotaGuard, RateLimiter, RetryPolicy, run_poll_cycle  # noqa: E402
from ingest.providers import TheOddsApiProvider                          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", default="americanfootball_nfl")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--markets", default="h2h,spreads,totals")
    ap.add_argument("--bookmakers", default=None,
                    help="comma-separated book keys; fewer books, same cost, less noise")
    ap.add_argument("--ingest", action="store_true",
                    help="write to the database (needs OLP_DATABASE_URL)")
    ap.add_argument("--quota-reserve", type=int, default=25)
    args = ap.parse_args()

    if not os.environ.get("THE_ODDS_API_KEY"):
        print("THE_ODDS_API_KEY is not set.", file=sys.stderr)
        print("  export THE_ODDS_API_KEY=...   (do not pass it as an argument)",
              file=sys.stderr)
        return 2

    provider = TheOddsApiProvider(
        sport=args.sport, regions=args.regions,
        markets=args.markets, bookmakers=args.bookmakers)

    print(f"provider : {provider.name}")
    print(f"sport    : {args.sport}   regions={args.regions}   markets={args.markets}")
    print(f"mode     : {'INGEST (writes to the database)' if args.ingest else 'DRY RUN (no writes)'}")
    print("-" * 62)

    if not args.ingest:
        provider.prefetch()
        events = list(provider.fetch_schedule())
        quotes = list(provider.fetch_odds())

        print(f"quota remaining : {provider.quota_remaining}")
        print(f"quota used      : {provider.quota_used}")
        print(f"events parsed   : {len(events)}")
        print(f"quotes parsed   : {len(quotes)}")
        print(f"parse errors    : {len(provider.last_parse_errors)}")

        for err in provider.last_parse_errors[:5]:
            print(f"  ! {err['error']}")

        books = sorted({q.sportsbook for q in quotes})
        print(f"books           : {', '.join(books) if books else '(none)'}")

        for ev in events[:5]:
            n = sum(1 for q in quotes if q.source_event_id == ev.source_event_id)
            print(f"  {ev.scheduled_start:%Y-%m-%d %H:%M}Z  "
                  f"{ev.away_team} @ {ev.home_team}  ({n} quotes)")
        if len(events) > 5:
            print(f"  ... and {len(events) - 5} more")

        print("\nDry run only. Re-run with --ingest to write these to the database.")
        return 0

    db_url = os.environ.get("OLP_DATABASE_URL")
    if not db_url:
        print("OLP_DATABASE_URL is not set; --ingest needs a target database.",
              file=sys.stderr)
        return 2

    import psycopg

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

    print(result)
    for err in result.parse_errors[:5]:
        print(f"  ! {err['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
