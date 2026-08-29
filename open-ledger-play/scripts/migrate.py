"""Apply migrations to a persistent database. NON-DESTRUCTIVE.

    python scripts/migrate.py                 # apply what is missing
    python scripts/migrate.py --plan          # show what would run, change nothing
    python scripts/migrate.py --with-fixtures # include test scaffolding (NEVER in production)

This is NOT tests/harness.py. `harness.migrate()` does `DROP SCHEMA public
CASCADE` and exists to give every test run a clean slate; pointing it at a
persistent database would destroy it. This script only ever adds.

FIXTURE MIGRATIONS ARE SKIPPED. Any file whose name contains "fixtures" builds
test scaffolding -- including `olp_test.reset()`, which TRUNCATEs every table in
the database. A skip list maintained by hand drifts; a naming convention the
deployer can read does not.

CHECKSUMS ARE RECORDED AND VERIFIED. This project corrects migrations in place
while no persistent environment exists (PACKAGE5_PREREG section 11.1) -- 051 and
058 both were. The moment a persistent database exists that rule expires, and
the failure mode it leaves behind is silent: an already-applied file is edited,
nothing re-runs, and the database quietly diverges from the code that claims to
describe it. So an edited file that has already been applied is a REFUSAL, not a
no-op.
"""
import argparse
import hashlib
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"

LEDGER = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by  TEXT NOT NULL DEFAULT current_user
);
COMMENT ON TABLE public.schema_migrations IS
    'What has actually been applied to THIS database, with a checksum of the '
    'file as applied. An edited-after-the-fact migration is refused rather '
    'than silently skipped.';
"""


def is_fixture(path: pathlib.Path) -> bool:
    return "fixture" in path.name.lower()


def checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true",
                    help="report what would run and exit without changing anything")
    ap.add_argument("--with-fixtures", action="store_true",
                    help="also apply test scaffolding; NEVER use in production")
    args = ap.parse_args()

    url = os.environ.get("OLP_DATABASE_URL")
    if not url:
        print("OLP_DATABASE_URL is not set.", file=sys.stderr)
        return 2

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print(f"no migrations found under {MIGRATIONS}", file=sys.stderr)
        return 2
    wanted = [f for f in files if args.with_fixtures or not is_fixture(f)]
    skipped = [f for f in files if f not in wanted]

    import psycopg
    with psycopg.connect(url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(LEDGER)
            cur.execute("SELECT filename, checksum FROM public.schema_migrations")
            applied = dict(cur.fetchall())
        conn.commit()

        # ---- refuse on drift before applying anything ----------------------
        drifted = [f.name for f in wanted
                   if f.name in applied and applied[f.name] != checksum(f)]
        if drifted:
            print("REFUSED: these migrations were edited after being applied to "
                  "this database:", file=sys.stderr)
            for name in drifted:
                print(f"  {name}", file=sys.stderr)
            print("\nThe database and the code now disagree and re-running will "
                  "not fix it. Correcting a shipped migration in place is only "
                  "safe while no persistent environment exists.", file=sys.stderr)
            return 3

        pending = [f for f in wanted if f.name not in applied]

        print(f"database   : {url.split('@')[-1]}")
        print(f"migrations : {len(files)} on disk, {len(applied)} already applied")
        if skipped:
            print(f"skipped    : {len(skipped)} fixture migration(s) -- "
                  f"{', '.join(f.name for f in skipped)}")
        if not pending:
            print("nothing to apply")
            return 0
        print(f"pending    : {len(pending)}")
        for f in pending:
            print(f"  {f.name}")
        if args.plan:
            print("\n--plan: nothing was changed")
            return 0

        # ---- apply, each in its own transaction with its ledger row --------
        for f in pending:
            try:
                with conn.cursor() as cur:
                    cur.execute(f.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO public.schema_migrations (filename, checksum) "
                        "VALUES (%s, %s)", (f.name, checksum(f)))
                conn.commit()
                print(f"  applied {f.name}")
            except Exception as exc:                           # noqa: BLE001
                conn.rollback()
                print(f"  FAILED {f.name}: {str(exc).splitlines()[0]}",
                      file=sys.stderr)
                print("rolled back; earlier migrations remain applied",
                      file=sys.stderr)
                return 1

    print(f"applied {len(pending)} migration(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
