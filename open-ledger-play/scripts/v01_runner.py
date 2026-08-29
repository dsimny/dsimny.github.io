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
import json
import os
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ingest import QuotaGuard, RateLimiter, RetryPolicy, run_poll_cycle   # noqa: E402
from ingest.http import redact                                           # noqa: E402
from ingest.providers import TheOddsApiProvider                          # noqa: E402


MARKET = "h2h"          # v0.1 is moneyline-only; 1 credit per call, not 3

# Objects that must exist for the deployed code to be running against the
# schema it expects. Probed at activation: deploying 058 code onto a 057
# database would otherwise activate an experiment whose cohort view is missing.
SCHEMA_MARKERS = {
    "057": ["model.experiment_runs", "model.formation_claims"],
    "058": ["model.experiments", "model.experiment_cohort",
            "grading.evaluation_sample"],
}


def _git_head() -> str:
    """The commit this runner was deployed from.

    In a container there is no .git -- the commit is baked at build time
    (OLP_DEPLOYMENT_COMMIT). Falling back to a checkout is for running the
    runner directly from a working tree.
    """
    baked = os.environ.get("OLP_DEPLOYMENT_COMMIT")
    if baked:
        return baked.strip()
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(pathlib.Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip() or None
    except Exception:                                          # noqa: BLE001
        return None


def _fn_default(conn, schema: str, name: str, arg: str):
    """Read a function's shipped default straight from the catalogue.

    k, the horizon and the window are pre-registered parameters. Reporting them
    from a constant in this file would record what the runner BELIEVES; reading
    them from pg_proc records what the database will actually do.
    """
    import re
    sig = _scalar(conn, """
        SELECT pg_get_function_arguments(p.oid) FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %s AND p.proname = %s LIMIT 1""", (schema, name))
    if not sig:
        return None
    m = re.search(rf"\b{re.escape(arg)}\b[^,]*?DEFAULT\s+([^,]+)", sig)
    return m.group(1).strip() if m else None


def _schema_state(conn) -> tuple:
    """Which migrations the FILES claim, and which the DATABASE actually has."""
    mig_dir = pathlib.Path(__file__).resolve().parent.parent / "db" / "migrations"
    # Fixture migrations are excluded, exactly as scripts/migrate.py excludes
    # them. Counting 059_p5_fixtures.sql here would make files_max permanently
    # exceed what any production database can verify, and --activate would
    # refuse forever on a mismatch that is not one.
    files = sorted(f.name[:3] for f in mig_dir.glob("*.sql")
                   if "fixture" not in f.name.lower()) if mig_dir.is_dir() else []
    files_max = files[-1] if files else None

    verified = []
    for version, objects in sorted(SCHEMA_MARKERS.items()):
        present = all(
            _scalar(conn, "SELECT to_regclass(%s) IS NOT NULL", (obj,))
            for obj in objects)
        if present:
            verified.append(version)
    return files_max, (verified[-1] if verified else None)


def _provenance(conn, args) -> dict:
    files_max, db_version = _schema_state(conn)
    return {
        "source_commit":     args.source_commit,
        "deployment_commit": args.deployment_commit or _git_head(),
        "schema_version":    db_version,
        "schema_files_max":  files_max,
        "model":             f"v01/{args.model_version}",
        "k":                 _fn_default(conn, "model", "v01_probability", "k"),
        "formation_target":  _fn_default(conn, "model", "schedule_v01", "p_horizon"),
        "window_seconds":    _fn_default(conn, "model", "schedule_v01", "p_window_secs"),
        "market":            MARKET,
    }


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
    total, unresolved = conn.execute("""
        SELECT count(*), count(*) FILTER (WHERE unresolved)
        FROM model.v01_ledger""").fetchone()
    print(json.dumps({
        "job": "v01.schedule", "worker": _worker_name(), "status": "OK",
        "created": added, "scheduled_total": total, "unresolved": unresolved,
        "provider_polls": 0, "provider_credits_used": 0,
        "at": _iso(_scalar(conn, "SELECT NOW()")),
    }, default=str))
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

    Returns 0 on a clean cycle, non-zero on a cycle that needs a human. The
    cycle record is emitted as one JSON line so a run can be audited from the
    job log without opening PostgreSQL.
    """
    worker = _worker_name()
    cycle = {"job": "v01.resolve", "worker": worker, "dry_run": dry_run}

    run_id = _scalar(conn, "SELECT model.start_experiment_run(%s)", (worker,))
    cycle["run_id"] = str(run_id)
    cycle["started_at"] = _iso(_scalar(conn,
        "SELECT started_at FROM model.experiment_runs WHERE run_id=%s::uuid",
        (run_id,)))

    cycle["opportunities_due"] = _scalar(conn,
        "SELECT count(*) FROM model.due_opportunities WHERE NOT actively_claimed")

    with conn.cursor() as cur:
        cur.execute("SELECT model.claim_due_opportunities(%s::uuid, %s)",
                    (run_id, worker))
        claimed = [r[0] for r in cur.fetchall()]
    cycle["opportunities_claimed"] = len(claimed)

    if not claimed:
        # The common case by a wide margin: a five-minute tick with nothing due.
        # It must cost nothing, and the log must make that unambiguous.
        _scalar(conn, "SELECT model.finish_experiment_run(%s::uuid)", (run_id,))
        return _finish(conn, cycle, run_id, rc=0)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FILTER (WHERE inside_window),
                   count(*) FILTER (WHERE NOT inside_window)
            FROM model.due_opportunities WHERE schedule_id = ANY(%s)""",
            (claimed,))
        in_window, late = cur.fetchone()
    cycle["claimed_inside_window"] = in_window
    cycle["claimed_past_window"] = late

    if dry_run:
        cycle["note"] = "dry run: no provider call, nothing resolved, claims lapse"
        return _finish(conn, cycle, run_id, rc=0)

    # ---- ONE targeted capture, through the ordinary Package #3 path --------
    if provider is None and not os.environ.get("THE_ODDS_API_KEY"):
        cycle["errors"] = ["THE_ODDS_API_KEY is not set"]
        cycle["note"] = ("work is due but no API key is configured; nothing "
                         "resolved, claims will lapse for the next tick")
        return _finish(conn, cycle, run_id, rc=1)

    _scalar(conn, "SELECT model.record_ingestion_poll(%s::uuid)", (run_id,))
    provider = provider or TheOddsApiProvider(sport=sport, markets=MARKET)
    try:
        poll = run_poll_cycle(conn, provider, retry=RetryPolicy(),
                              limiter=RateLimiter(), quota=QuotaGuard())
    except Exception as exc:                                   # noqa: BLE001
        # Claims lapse with their lease and the next ordinary five-minute tick
        # retries. Nothing is resolved, so no opportunity is spent on a failed
        # capture -- which is why the external scheduler must NOT retry.
        cycle["errors"] = [redact(str(exc)).splitlines()[0]]
        cycle["note"] = "ingestion failed; opportunities left unresolved for the next tick"
        return _finish(conn, cycle, run_id, rc=1)

    cycle["provider"] = provider.name
    cycle["quota_remaining"] = getattr(provider, "quota_remaining", None)
    cycle["parse_errors"] = len(getattr(poll, "parse_errors", []) or [])

    # ---- terminate every claimed opportunity against that fresh state ------
    tally, errors, beliefs = {}, [], []
    for sid in claimed:
        try:
            reason = _scalar(conn, "SELECT model.resolve_v01(%s::uuid, %s::uuid)",
                             (sid, run_id))
        except Exception as exc:                               # noqa: BLE001
            # ALREADY_RESOLVED means another worker won the race. That is the
            # record-level guarantee working, not an incident.
            if "ALREADY_RESOLVED" in str(exc):
                reason = "ALREADY_RESOLVED"
            else:
                reason = "ERROR"
                errors.append(redact(str(exc)).splitlines()[0])
        tally[reason] = tally.get(reason, 0) + 1

    with conn.cursor() as cur:
        cur.execute("""SELECT belief_id FROM model.formation_attempts
                        WHERE experiment_run_id = %s::uuid
                          AND belief_id IS NOT NULL""", (run_id,))
        beliefs = [str(r[0]) for r in cur.fetchall()]

    cycle["outcomes"] = tally
    cycle["formed"] = tally.get("ELIGIBLE", 0)
    cycle["no_window_capture"] = tally.get("NO_WINDOW_CAPTURE", 0)
    cycle["ineligible"] = sum(
        n for r, n in tally.items()
        if r not in ("ELIGIBLE", "NO_WINDOW_CAPTURE", "ALREADY_RESOLVED", "ERROR"))
    cycle["belief_ids"] = beliefs
    cycle["belief_count"] = len(beliefs)
    if errors:
        cycle["errors"] = errors

    _scalar(conn, "SELECT model.finish_experiment_run(%s::uuid)", (run_id,))
    return _finish(conn, cycle, run_id, rc=1 if errors else 0)


def _finish(conn, cycle, run_id, rc: int) -> int:
    """Complete the cycle record from the database and emit it.

    provider_polls, unresolved and the credit count are read back from
    PostgreSQL rather than counted in Python: the job log should report what the
    database actually recorded, not what this process believes it did.
    """
    polls, resolved, finished = conn.execute("""
        SELECT ingestion_polls, resolved_count, finished_at
        FROM model.experiment_runs WHERE run_id = %s::uuid""",
        (run_id,)).fetchone()

    cycle["finished_at"] = _iso(finished)
    cycle["provider_polls"] = polls
    cycle["provider_credits_used"] = polls          # h2h/us = 1 credit per call
    cycle["resolved"] = resolved
    cycle["unresolved"] = _scalar(conn,
        "SELECT count(*) FROM model.v01_ledger WHERE unresolved")

    # ---- the loud condition ------------------------------------------------
    # ck_one_poll_per_cycle already makes this unreachable at the database
    # level. It is asserted again here because the two answer different
    # questions: the constraint protects integrity, the job log makes a
    # violation VISIBLE without anyone opening PostgreSQL. If this ever fires,
    # the constraint has been dropped or bypassed and the cycle is a failure
    # regardless of how well the rest of it went.
    if polls is not None and polls > 1:
        cycle["status"] = "FAILED"
        cycle["alert"] = (f"PROVIDER_POLLS_EXCEEDED: {polls} polls in one cycle. "
                          f"One poll must serve every simultaneously due "
                          f"opportunity; polling per wager turns one board "
                          f"refresh into dozens of provider calls.")
        rc = max(rc, 2)
    else:
        cycle["status"] = "FAILED" if rc else "OK"

    print(json.dumps(cycle, default=str))
    return rc


def _iso(ts):
    return ts.isoformat() if ts is not None else None


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


def do_declare(conn, args) -> int:
    """Create the experiment in DRAFT.

    Opportunities can be scheduled and resolved against a DRAFT experiment and
    none of them count -- which is exactly what the deployment shakedown needs.
    schedule_v01 refuses to create an opportunity with no experiment, because
    such an opportunity could never belong to a cohort.
    """
    try:
        eid = _scalar(conn, "SELECT model.create_experiment('v01', %s, %s)",
                      (args.model_version, args.note))
    except Exception as exc:                                   # noqa: BLE001
        print(json.dumps({"job": "v01.declare", "status": "REFUSED",
                          "error": str(exc).splitlines()[0]}, default=str))
        return 1
    print(json.dumps({"job": "v01.declare", "status": "OK",
                      "model": f"v01/{args.model_version}",
                      "experiment_id": str(eid), "experiment_status": "DRAFT"},
                     default=str))
    return 0


def do_activate(conn, args) -> int:
    """The one-time act that makes the experiment prospective.

    Everything formed at or after this instant belongs to the pre-registered
    sample; everything before it does not. The database refuses a second
    activation and refuses to move this one, so the deployment shakedown
    described in MODEL_V0_1_PREREG.md section 11.2 cannot leak into the sample
    and a disappointing month cannot be excluded after the fact.
    """
    if not args.by:
        print("--activate requires --by (who is activating)", file=sys.stderr)
        return 2
    if not args.source_commit:
        print("--activate requires --source-commit (the commit that established "
              "the evidence gate; it cannot be derived on the deploy host)",
              file=sys.stderr)
        return 2

    prov = _provenance(conn, args)

    # Refuse rather than activate into an ambiguous state. Activation is a
    # one-time, un-movable act; doing it against a schema that is not the one
    # the deployed code expects would stamp a boundary nobody can correct.
    problems = [k for k in ("deployment_commit", "schema_version", "k",
                            "formation_target", "window_seconds")
                if not prov.get(k)]
    if prov["schema_version"] != prov["schema_files_max"]:
        problems.append(
            f"schema mismatch: files ship {prov['schema_files_max']}, database "
            f"verifies {prov['schema_version']}")
    if problems:
        print(json.dumps({"job": "v01.activate", "status": "REFUSED",
                          "problems": problems, "provenance": prov},
                         default=str))
        return 2

    args.note = json.dumps(prov, sort_keys=True)
    try:
        at = _scalar(conn, "SELECT model.activate_experiment('v01', %s, %s, %s)",
                     (args.model_version, args.by, args.note))
    except Exception as exc:                                   # noqa: BLE001
        print(json.dumps({"job": "v01.activate", "status": "REFUSED",
                          "error": str(exc).splitlines()[0]}, default=str))
        return 1
    print(json.dumps({"job": "v01.activate", "status": "OK",
                      "model": f"v01/{args.model_version}",
                      "activated_at": _iso(at), "activated_by": args.by,
                      "provenance": prov}, default=str))
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
    g.add_argument("--declare", action="store_true",
                   help="create the experiment in DRAFT; required before scheduling")
    g.add_argument("--activate", action="store_true",
                   help="declare the experiment prospective; ONE TIME, never undone")
    ap.add_argument("--by", help="with --activate: who is activating")
    ap.add_argument("--note", help="with --declare: why")
    ap.add_argument("--source-commit",
                    help="with --activate: the source-project commit that "
                         "established the evidence gate")
    ap.add_argument("--deployment-commit",
                    help="with --activate: overrides the deployed HEAD if the "
                         "runner is not running from a git checkout")
    ap.add_argument("--model-version", default="0.1.0")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --resolve: claim and report, but do not poll or resolve")
    ap.add_argument("--sport", default="americanfootball_nfl")
    args = ap.parse_args()

    if args.resolve and not args.dry_run and not os.environ.get("THE_ODDS_API_KEY"):
        # A warning, not a refusal. A cycle with nothing due makes no provider
        # call, so it must still succeed -- otherwise a missing key turns every
        # idle five-minute tick into a red cron job. The refusal happens where
        # the provider is actually constructed, which is the moment it matters.
        print("warning: THE_ODDS_API_KEY is not set; a cycle with work due will "
              "fail when it tries to capture.", file=sys.stderr)

    conn = _connect()
    try:
        if args.schedule:
            do_schedule(conn)
            return 0
        if args.declare:
            return do_declare(conn, args)
        if args.activate:
            return do_activate(conn, args)
        if args.status:
            return show_status(conn)
        return do_resolve(conn, dry_run=args.dry_run, sport=args.sport)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
