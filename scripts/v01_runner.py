"""Model v0.1 experiment runner -- the orchestrator between cron and ingestion.

    cron                 the clock
    THIS                 the orchestrator      (Package #5)
    ingest.run_poll_cycle   the data collector  (Package #3, called unchanged)

Package #3 knows nothing about this experiment and must stay that way. This
script does not poll on a schedule of its own, does not decide what a market
means, and does not compute a probability. It answers one question -- "is a
pre-registered T-24h capture due right now?" -- and if so it refreshes the board
ONCE and asks the database to resolve every opportunity that refresh serves.

Two jobs, both idempotent, both safe to run more often than needed:

    python scripts/v01_runner.py --schedule      # hourly
    python scripts/v01_runner.py --resolve       # every 5 minutes

Environment:
    OLP_DATABASE_URL     required for both
    THE_ODDS_API_KEY     required for --resolve (never passed as an argument,
                         never written to the database, never logged)

--resolve spends ONE provider call per cycle, and only when work is actually
due. An idle tick costs nothing. Add --dry-run to see what would happen without
spending a credit or terminating anything.
"""
import argparse
import os
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ingest import QuotaGuard, RateLimiter, RetryPolicy, run_poll_cycle   # noqa: E402
from ingest.http import redact                                           # noqa: E402
from ingest.providers import TheOddsApiProvider                          # noqa: E402


def _worker_name() -> str:
    return f"v01-runner@{socket.gethostname()}:{os.getpid()}"


def _connect():
    url = os.environ.get("OLP_DATABASE_URL")
    if not url:
        print("OLP_DATABASE_URL is not set.", file=sys.stderr)
        raise SystemExit(2)
    import psycopg
    conn = psycopg.connect(url)
    conn.autocommit = True
    return conn


def _scalar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def do_schedule(conn) -> int:
    """Create T-24h opportunities for events entering the horizon.

    Costs nothing -- no provider call. Runs hourly so an opportunity exists well
    before its target time, and is a no-op for anything already scheduled.
    """
    added = _scalar(conn, "SELECT model.schedule_v01()")
    print(f"scheduled {added} new opportunit{'y' if added == 1 else 'ies'}")
    return added


def do_resolve(conn, dry_run: bool = False, sport: str = "americanfootball_nfl",
               provider=None) -> int:
    """Claim everything due, refresh the board ONCE, terminate each opportunity.

    The single poll is the point. Sixteen games x two selections is one board
    refresh, and model.experiment_runs.ck_one_poll_per_cycle refuses a second
    within the cycle, so this cannot silently degrade into one call per wager.

    `provider` is injectable so this exact code path can be exercised against a
    fixture feed. There is deliberately no --fixture flag: a test mode reachable
    from the command line is a test mode that eventually runs in production.
    """
    worker = _worker_name()
    run_id = _scalar(conn, "SELECT model.start_experiment_run(%s)", (worker,))

    with conn.cursor() as cur:
        cur.execute("""SELECT model.claim_due_opportunities(%s::uuid, %s)""",
                    (run_id, worker))
        claimed = [r[0] for r in cur.fetchall()]

    if not claimed:
        _scalar(conn, "SELECT model.finish_experiment_run(%s::uuid)", (run_id,))
        print("nothing due; no provider call made")
        return 0

    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FILTER (WHERE inside_window),
                   count(*) FILTER (WHERE NOT inside_window)
            FROM model.due_opportunities WHERE schedule_id = ANY(%s)""",
            (claimed,))
        in_window, late = cur.fetchone()
    print(f"claimed {len(claimed)} opportunities "
          f"({in_window} inside window, {late} past window)")

    if dry_run:
        print("--dry-run: no provider call, nothing resolved, claims will lapse")
        return 0

    # ---- ONE targeted capture, through the ordinary Package #3 path --------
    _scalar(conn, "SELECT model.record_ingestion_poll(%s::uuid)", (run_id,))
    provider = provider or TheOddsApiProvider(sport=sport, markets="h2h")
    try:
        cycle = run_poll_cycle(conn, provider, retry=RetryPolicy(),
                               limiter=RateLimiter(), quota=QuotaGuard())
    except Exception as exc:                                   # noqa: BLE001
        # The claims lapse with their lease and the next cycle retries. Nothing
        # is resolved, so no opportunity is spent on a failed capture.
        print(f"ingestion failed, opportunities left unresolved: "
              f"{redact(str(exc))}", file=sys.stderr)
        return 1
    print(f"poll: {cycle}")

    # ---- terminate every claimed opportunity against that fresh state ------
    tally = {}
    for sid in claimed:
        try:
            reason = _scalar(conn, "SELECT model.resolve_v01(%s::uuid, %s::uuid)",
                             (sid, run_id))
        except Exception as exc:                               # noqa: BLE001
            # ALREADY_RESOLVED means another worker won the race. That is the
            # record-level guarantee doing its job, not an error to escalate.
            reason = "ALREADY_RESOLVED" if "ALREADY_RESOLVED" in str(exc) \
                else f"ERROR: {redact(str(exc)).splitlines()[0]}"
        tally[reason] = tally.get(reason, 0) + 1

    _scalar(conn, "SELECT model.finish_experiment_run(%s::uuid)", (run_id,))
    for reason, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {reason}")

    formed = tally.get("ELIGIBLE", 0)
    print(f"{len(claimed)} opportunities terminated on 1 provider call; "
          f"{formed} beliefs formed")
    return 0


def show_status(conn) -> int:
    due, claimed_now = _scalar(conn, """
        SELECT count(*) FROM model.due_opportunities"""), _scalar(conn, """
        SELECT count(*) FROM model.due_opportunities WHERE actively_claimed""")
    print(f"due now: {due} ({claimed_now} actively claimed)")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT reason::text, count(*) FROM model.formation_attempts
             WHERE model_id = 'v01' GROUP BY 1 ORDER BY 2 DESC""")
        rows = cur.fetchall()
    if rows:
        print("terminal outcomes so far:")
        for reason, n in rows:
            print(f"  {n:>4}  {reason}")
    sched, formed, ineligible, unresolved = 0, 0, 0, 0
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*), count(*) FILTER (WHERE belief_formed),
                   count(*) FILTER (WHERE NOT belief_formed AND NOT unresolved),
                   count(*) FILTER (WHERE unresolved)
            FROM model.v01_ledger""")
        sched, formed, ineligible, unresolved = cur.fetchone()
    print(f"ledger: {sched} scheduled = {formed} formed + {ineligible} ineligible"
          f" + {unresolved} still open")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--schedule", action="store_true",
                   help="create upcoming T-24h opportunities (hourly, free)")
    g.add_argument("--resolve", action="store_true",
                   help="capture and terminate what is due (every 5 min, <=1 credit)")
    g.add_argument("--status", action="store_true",
                   help="report the ledger without changing anything")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --resolve: claim and report, but do not poll or resolve")
    ap.add_argument("--sport", default="americanfootball_nfl")
    args = ap.parse_args()

    if args.resolve and not args.dry_run and not os.environ.get("THE_ODDS_API_KEY"):
        print("THE_ODDS_API_KEY is not set; --resolve needs it to capture.",
              file=sys.stderr)
        return 2

    conn = _connect()
    try:
        if args.schedule:
            do_schedule(conn)
            return 0
        if args.status:
            return show_status(conn)
        return do_resolve(conn, dry_run=args.dry_run, sport=args.sport)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
