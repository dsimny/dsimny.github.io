"""Verify a LIVE database is a clean production install. Read-only.

    OLP_DATABASE_DIRECT_URL=... python scripts/verify_production_schema.py

P5-T68 proves the production manifest installs clean by building a scratch
database with CREATE DATABASE. **Hosted Supabase does not permit that**, so the
gate cannot run there in that form. This script asserts the same properties
against a database that already exists, which is what commissioning a hosted
project actually needs.

The assertion set lives here and is IMPORTED by P5-T68, so the test and the
live check cannot drift apart.

It writes nothing and creates nothing. Safe to run against production at any
time, including after activation.
"""
import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "db" / "migrations" / "production_manifest.txt"

# -----------------------------------------------------------------------------
# What a production install must and must not contain.
# -----------------------------------------------------------------------------
REQUIRED_RELATIONS = [
    "public.market_intelligence", "public.events", "public.market_snapshots",
    "model.beliefs", "model.formation_schedule", "model.formation_attempts",
    "model.experiments", "model.experiment_cohort", "model.experiment_runs",
    "model.formation_claims", "model.v01_ledger", "model.due_opportunities",
    "grading.belief_grades", "grading.wager_outcomes", "grading.evaluation_sample",
]

REQUIRED_FUNCTIONS = [
    ("model", "create_experiment"), ("model", "activate_experiment"),
    ("model", "advance_experiment"), ("model", "activated_at"),
    ("model", "schedule_v01"), ("model", "resolve_v01"),
    ("model", "claim_due_opportunities"), ("model", "record_ingestion_poll"),
    ("model", "start_experiment_run"), ("model", "v01_probability"),
    ("model", "form_belief"), ("model", "eligibility"),
    ("grading", "grade_belief"), ("grading", "standing_report"),
    ("grading", "calibration_bins"), ("grading", "calibration_report"),
]

FORBIDDEN_SCHEMAS = ["olp_test"]

# Capability, not name. An earlier draft flagged anything called *reset* and
# tripped on public.provider_reset_circuit_rpc -- a legitimate Package #3
# function. What matters is what a function can DO.
FORBIDDEN_BODY_PATTERNS = [
    ("%TRUNCATE%", "can TRUNCATE"),
    ("%olp_test%", "references the test schema"),
]


def _rows(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _scalar(conn, sql, params=()):
    r = _rows(conn, sql, params)
    return r[0][0] if r else None


def check(conn) -> list:
    """Run every production-install assertion. Returns a list of failures."""
    problems = []

    for schema in FORBIDDEN_SCHEMAS:
        if _scalar(conn, "SELECT count(*) FROM pg_namespace WHERE nspname = %s",
                   (schema,)):
            problems.append(f"schema {schema} exists in a production database")

    for pattern, what in FORBIDDEN_BODY_PATTERNS:
        found = _rows(conn, """
            SELECT n.nspname || '.' || p.proname FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND p.prosrc ILIKE %s""", (pattern,))
        for (name,) in found:
            problems.append(f"function {name} {what}")

    for obj in REQUIRED_RELATIONS:
        if not _scalar(conn, "SELECT to_regclass(%s) IS NOT NULL", (obj,)):
            problems.append(f"missing relation {obj}")

    for schema, name in REQUIRED_FUNCTIONS:
        if not _scalar(conn, """
            SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s AND p.proname = %s""", (schema, name)):
            problems.append(f"missing function {schema}.{name}")

    # The pre-registered parameters must be the ones the database will use.
    k = _scalar(conn, "SELECT model.v01_probability(0.6::numeric)")
    if k is None or abs(float(k) - 0.609691) > 1e-6:
        problems.append(f"v01_probability(0.6) = {k}, expected 0.609691 (k = 1.10)")

    return problems


def check_manifest_applied(conn) -> list:
    """If the migration ledger exists, every manifest entry must be in it."""
    if not _scalar(conn, "SELECT to_regclass('public.schema_migrations') IS NOT NULL"):
        return ["public.schema_migrations is absent -- this database was not "
                "installed by scripts/migrate.py"]
    names = [ln.strip() for ln in MANIFEST.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    applied = {r[0] for r in _rows(conn, "SELECT filename FROM public.schema_migrations")}
    missing = [n for n in names if n not in applied]
    extra = sorted(applied - set(names))
    problems = []
    if missing:
        problems.append(f"manifest entries never applied: {', '.join(missing)}")
    if extra:
        problems.append(f"applied but NOT in the manifest: {', '.join(extra)}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-ledger", action="store_true",
                    help="do not require public.schema_migrations to agree with "
                         "the manifest")
    args = ap.parse_args()

    url = os.environ.get("OLP_DATABASE_DIRECT_URL") or os.environ.get("OLP_DATABASE_URL")
    if not url:
        print("Set OLP_DATABASE_DIRECT_URL (preferred) or OLP_DATABASE_URL.",
              file=sys.stderr)
        return 2

    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        problems = check(conn)
        if not args.skip_ledger:
            problems += check_manifest_applied(conn)
        server = _scalar(conn, "SHOW server_version")

    host = url.split("@")[-1].split("/")[0]
    print(f"database : {host}")
    print(f"server   : PostgreSQL {server}")
    print(f"checked  : {len(REQUIRED_RELATIONS)} relations, "
          f"{len(REQUIRED_FUNCTIONS)} functions, "
          f"{len(FORBIDDEN_SCHEMAS)} forbidden schema(s), "
          f"{len(FORBIDDEN_BODY_PATTERNS)} forbidden capabilities")

    if problems:
        print(f"\nPRODUCTION SCHEMA CHECK: FAIL ({len(problems)})")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nPRODUCTION SCHEMA CHECK: PASS")
    print("  no olp_test schema; no function can TRUNCATE or reach the test "
          "schema; every Package #5 object present; k = 1.10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
