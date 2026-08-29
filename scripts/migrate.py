"""Apply migrations to a persistent database. NON-DESTRUCTIVE.

    OLP_DATABASE_DIRECT_URL=... python scripts/migrate.py          # apply
    OLP_DATABASE_DIRECT_URL=... python scripts/migrate.py --plan   # dry run

USES THE DIRECT CONNECTION, NOT THE POOLER. Migrations run multi-statement DDL,
take locks and depend on session-scoped behaviour; a transaction pooler is not
the right place for that and this stack has not been proven pooler-safe for DDL.
Pointing this at a :6543 pooler endpoint is refused outright. The runner is the
opposite -- short-lived pooled connections, see scripts/v01_runner.py.

This is NOT tests/harness.py. `harness.migrate()` does `DROP SCHEMA public
CASCADE` and exists to give every test run a clean slate; pointing it at a
persistent database would destroy it. This script only ever adds.

THE MANIFEST IS THE PRODUCTION SET. db/migrations/production_manifest.txt lists
the exact ordered migrations a hosted database receives. Test scaffolding lives
in tests/sql/ and is not reachable from here at all -- olp_test, reset(),
fixture factories and destructive helpers cannot be installed by following the
documented deploy path, because this script has no way to name them.

A migration on disk but absent from the manifest is a REFUSAL, not a silent
skip: it is either a production migration someone forgot to list, or test
scaffolding in the wrong directory.

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
MANIFEST = MIGRATIONS / "production_manifest.txt"

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


def production_set() -> list:
    names = [ln.strip() for ln in MANIFEST.read_text(encoding="utf-8").splitlines()]
    return [n for n in names if n and not n.startswith("#")]


def checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true",
                    help="report what would run and exit without changing anything")
    args = ap.parse_args()

    # Schema administration uses the DIRECT (session) connection. Migrations
    # run multi-statement DDL, take locks and rely on session-scoped behaviour;
    # a transaction pooler is not the right place for any of that, and this
    # stack has not been proven pooler-safe for DDL. The runner uses the pooler
    # (OLP_DATABASE_URL); this does not.
    url = os.environ.get("OLP_DATABASE_DIRECT_URL")
    if not url:
        url = os.environ.get("OLP_DATABASE_URL")
        if url:
            print("warning: OLP_DATABASE_DIRECT_URL is not set; falling back to "
                  "OLP_DATABASE_URL.", file=sys.stderr)
    if not url:
        print("Set OLP_DATABASE_DIRECT_URL (preferred) or OLP_DATABASE_URL.",
              file=sys.stderr)
        return 2

    host = url.split("@")[-1].split("/")[0]
    if ":6543" in host or "pooler." in host:
        print(f"REFUSED: {host} looks like a TRANSACTION POOLER endpoint. "
              f"Migrations need the direct/session connection -- pooling changes "
              f"session-scoped behaviour that DDL depends on. Set "
              f"OLP_DATABASE_DIRECT_URL to the direct connection string.",
              file=sys.stderr)
        return 2

    names = production_set()
    if not names:
        print(f"{MANIFEST} lists no migrations", file=sys.stderr)
        return 2
    on_disk = {f.name for f in MIGRATIONS.glob("*.sql")}
    unlisted = sorted(on_disk - set(names))
    if unlisted:
        print(f"REFUSED: migrations present but not in the manifest: "
              f"{', '.join(unlisted)}", file=sys.stderr)
        print("Add them to production_manifest.txt, or move them to tests/sql/ "
              "if they are test scaffolding.", file=sys.stderr)
        return 3
    missing = [n for n in names if n not in on_disk]
    if missing:
        print(f"REFUSED: manifest lists missing files: {', '.join(missing)}",
              file=sys.stderr)
        return 3
    wanted = [MIGRATIONS / n for n in names]

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
        print(f"manifest   : {len(wanted)} production migrations, "
              f"{len(applied)} already applied")
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
