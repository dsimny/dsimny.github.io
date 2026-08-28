"""
OLP-M1 test harness.

Boots a real PostgreSQL server, applies the local auth shim, then applies
migrations 001-019 UNMODIFIED, then the test-only service grants.

Every connection returned here is an independent TCP connection to the server,
which is what the concurrency requirements demand -- there is no in-process
faking anywhere in this suite.
"""

import os
import pathlib
from decimal import Decimal

import psycopg

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"
TESTKIT = ROOT / "db" / "testkit"

_SERVER = None
_URI = None

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


def external_target() -> bool:
    """True when pointed at a database we did not boot ourselves."""
    return bool(os.environ.get("OLP_DATABASE_URL"))


def _guard_destructive_target(uri: str) -> None:
    """Refuse to run this suite against anything that isn't local.

    migrate() drops and rebuilds `public`, and olp_test.reset() truncates every
    table including auth.users. That is fine against a throwaway local stack and
    catastrophic against a hosted project, so the host is checked rather than
    trusted. Override deliberately with OLP_ALLOW_REMOTE=1.
    """
    if os.environ.get("OLP_ALLOW_REMOTE") == "1":
        return

    from urllib.parse import urlparse

    host = (urlparse(uri).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise RuntimeError(
            f"REFUSING TO RUN: OLP_DATABASE_URL points at non-local host {host!r}.\n"
            "This suite DROPS SCHEMA public and TRUNCATES auth.users. Point it at a\n"
            "local Supabase stack (supabase start) or a throwaway database. If you\n"
            "genuinely mean it, set OLP_ALLOW_REMOTE=1."
        )



def _guard_captured_data() -> None:
    """Refuse to drop a schema that holds a real provider capture.

    The host check above cannot see this case: a live ingest lands in the SAME
    local Supabase stack the suite runs against, so pointing at localhost is no
    longer evidence the data is disposable. This was learned the direct way --
    a 272-event / 4,560-quote captured slate, mid-investigation, was destroyed by
    running the suite against the database holding it.

    Anything whose source_provider is not 'FIXTURE' was written by a real
    provider adapter and is not reproducible without spending API credits.
    Override deliberately with OLP_ALLOW_WIPE_LIVE=1.
    """
    if os.environ.get("OLP_ALLOW_WIPE_LIVE") == "1":
        return
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT count(*), count(DISTINCT event_id)
                FROM public.market_snapshots
                WHERE source_provider <> 'FIXTURE'""")
            quotes, events = cur.fetchone()
    except Exception:
        return          # no schema, no table, nothing to protect

    if quotes:
        raise RuntimeError(
            "REFUSING TO MIGRATE: this database holds a real provider "
            f"capture ({quotes} quotes across {events} events with "
            "source_provider <> 'FIXTURE'). migrate() DROPS SCHEMA public, "
            "which would destroy it, and re-capturing costs API credits. "
            "Point the suite at a different database, or set "
            "OLP_ALLOW_WIPE_LIVE=1 if you really mean it."
        )


def db_uri() -> str:
    """Boot (once) and return the connection URI.

    Set OLP_DATABASE_URL to run the suite against an existing PostgreSQL or
    Supabase instance instead of the bundled server.
    """
    global _SERVER, _URI
    if _URI:
        return _URI

    env = os.environ.get("OLP_DATABASE_URL")
    if env:
        _guard_destructive_target(env)
        _URI = env
        return _URI

    import pgserver

    # Keep the 60MB cluster out of the repo by default; override with OLP_PGDATA.
    datadir = os.environ.get("OLP_PGDATA")
    if not datadir:
        import tempfile
        datadir = str(pathlib.Path(tempfile.gettempdir()) / "olp_m1_pgdata")
    pathlib.Path(datadir).mkdir(parents=True, exist_ok=True)
    _SERVER = pgserver.get_server(datadir)
    _URI = _SERVER.get_uri()
    return _URI


def connect(autocommit: bool = True) -> psycopg.Connection:
    """A fresh, independent connection with owner (superuser) rights."""
    conn = psycopg.connect(db_uri())
    conn.autocommit = autocommit
    return conn


def connect_as(role: str, user_id=None, autocommit: bool = True) -> psycopg.Connection:
    """A fresh connection acting as `role` with `user_id` as the JWT subject.

    This is how a PostgREST/Supabase request arrives: the pooled connection
    assumes the API role and carries the JWT claims in request GUCs, which is
    exactly what auth.uid() reads.
    """
    conn = psycopg.connect(db_uri())
    conn.autocommit = autocommit
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('request.jwt.claim.sub', %s, false)",
            (str(user_id) if user_id else "",),
        )
        cur.execute(f"SET ROLE {role}")
    return conn


def _apply(cur, path: pathlib.Path) -> None:
    cur.execute(path.read_text(encoding="utf-8"))


def migrate(verbose: bool = False) -> list:
    """Drop and rebuild the schema from scratch. Returns applied file names.

    Against a real Supabase stack the `auth` schema, auth.uid() and the API
    roles already exist and are the platform's own -- the testkit shim is skipped
    entirely and `auth` is never dropped. Migrations 001-019 are identical in
    both modes.
    """
    applied = []
    external = external_target()

    _guard_captured_data()

    with connect() as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS olp_test CASCADE")
        # Package #5's schemas. Dropped like public so each run rebuilds the
        # trust boundary from scratch; the olp_model ROLE is cluster-wide and
        # survives, which is why migration 050 revokes before it grants.
        cur.execute("DROP SCHEMA IF EXISTS model CASCADE")
        cur.execute("DROP SCHEMA IF EXISTS model_input CASCADE")
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE")

        if not external:
            # Only ever safe on the throwaway server we booted ourselves.
            cur.execute("DROP SCHEMA IF EXISTS auth CASCADE")

        cur.execute("CREATE SCHEMA public")

        if external:
            # Supabase's own default grants on a fresh `public`.
            cur.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role")
        else:
            _apply(cur, TESTKIT / "000_local_auth_shim.sql")
            applied.append("testkit/000_local_auth_shim.sql")

        for path in sorted(MIGRATIONS.glob("*.sql")):
            _apply(cur, path)
            applied.append(path.name)
            if verbose:
                print(f"  applied {path.name}")

    return applied


def reset(conn=None) -> None:
    """Truncate all transactional state between tests."""
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT olp_test.reset()")
    finally:
        if own:
            conn.close()


# -- assertion helpers --------------------------------------------------------

class Failure(AssertionError):
    pass


def expect_error(fn, fragment: str, label: str = ""):
    """Assert that `fn()` raises a database error containing `fragment`."""
    try:
        fn()
    except psycopg.Error as exc:
        text = str(exc)
        if fragment.lower() not in text.lower():
            raise Failure(
                f"{label}: expected error containing {fragment!r}, got: {text.strip()[:300]}"
            )
        return text
    raise Failure(f"{label}: expected error containing {fragment!r}, but the call succeeded")


def _eq(actual, expected):
    """Compare a DB value to a Python literal without float rounding lies.

    NUMERIC(12,2) comes back as Decimal. Decimal('10909.09') != 10909.09 as a
    float, because that float is not exactly 10909.09 -- so money literals in
    tests are promoted through str() to an exact Decimal.
    """
    if isinstance(actual, Decimal) and isinstance(expected, (int, float)):
        return actual == Decimal(str(expected))
    return actual == expected


class Row(tuple):
    """Tuple whose equality is money-aware (see _eq)."""

    def __eq__(self, other):
        if isinstance(other, (tuple, list)) and len(other) == len(self):
            return all(_eq(a, b) for a, b in zip(self, other))
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = tuple.__hash__


def scalar(conn, sql: str, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
        return r[0] if r else None


def row(conn, sql: str, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
        return Row(r) if r is not None else None


def rows(conn, sql: str, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [Row(r) for r in cur.fetchall()]


def balances(conn, chapter_id):
    """(settled, escrow, available) straight from the authoritative view."""
    return row(
        conn,
        """
        SELECT settled_balance, escrowed_risk, available_capital
        FROM public.chapter_balances WHERE chapter_id = %s
        """,
        (chapter_id,),
    )
