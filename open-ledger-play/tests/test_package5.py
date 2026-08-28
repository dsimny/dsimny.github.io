"""OLP-M1 Package #5 -- Model Layer.

Increment 1 is the trust boundary and nothing else. Package #5 must not be able
to bypass Package #4, and must not be able to see its own scoreboard. Every
later statistical claim rests on this, so it is proven before anything else is
built.

These tests attempt each read AS THE ACTUAL `olp_model` ROLE and require
PostgreSQL to refuse. Inspecting grants would only test our belief about the
grants; the database is the thing that decides.
"""

import psycopg

import harness as h

# What the model is permitted to see.
ALLOWED = [
    "model_input.market_intelligence",
    "model_input.events",
    "model_input.event_schedule_history",
]

# Package #4 internals and every second definition of observed market reality.
DENIED_MARKET = [
    "public.market_snapshots",
    "public.canonical_market",
    "public.executable_market",
    "public.market_movement",
    "public.market_intelligence",
    "public.current_market_board",
    "public.system_settings",
]

# The scoreboard: outcomes, grading, CLV, exposure, and the operational surface.
DENIED_SCOREBOARD = [
    "public.ticket_results",
    "public.ticket_result_adjustments",
    "public.ticket_effective_results",
    "public.ticket_closing_line_value",
    "public.tickets",
    "public.wallet_transactions",
    "public.ledger_chapters",
    "public.chapter_balances",
    "public.risk_reservations",
    "public.ingestion_runs",
    "public.provider_health",
    "public.market_feed_health",
    "public.event_lifecycle_log",
    "public.users",
]

INSUFFICIENT_PRIVILEGE = "42501"


def _assert_exists(admin, obj):
    """Guard against a vacuous pass.

    A denial test that only asserts 'an error was raised' would pass just as
    happily against a misspelled table, proving nothing. So every denied object
    is first shown to exist and to be readable BY THE OWNER.
    """
    schema, name = obj.split(".", 1)
    found = h.scalar(admin, """
        SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r','v')""",
        (schema, name))
    assert found == 1, f"{obj} does not exist -- this test would pass vacuously"
    h.scalar(admin, f"SELECT count(*) FROM {obj}")


def _assert_refused(model_conn, obj):
    """The read must fail with a PostgreSQL PERMISSION error -- not an empty
    result, not a filtered view, not a missing relation."""
    try:
        with model_conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {obj} LIMIT 1")
            rows = cur.fetchall()
    except psycopg.Error as exc:
        code = exc.sqlstate
        assert code == INSUFFICIENT_PRIVILEGE, (
            f"{obj}: refused, but with SQLSTATE {code}, not a permission error "
            f"({INSUFFICIENT_PRIVILEGE}). {exc}")
        return
    raise AssertionError(
        f"{obj}: olp_model READ IT and got {len(rows)} row(s). The trust "
        f"boundary is open.")


def t01_model_role_cannot_reach_behind_package_4():
    """The model reads Package #4's answer and has no path to its inputs.

    Package #4's views are security_invoker, so a grant on market_intelligence
    alone would still require the caller to hold SELECT on market_snapshots --
    which is exactly what must not happen. The model therefore reads through
    `model_input`, and every raw and intermediate market object is refused.
    """
    admin = h.connect()
    for obj in DENIED_MARKET:
        _assert_exists(admin, obj)

    model = h.connect_as("olp_model")

    # The permitted surface works. Without this the denials below would be
    # satisfied by a role that simply cannot read anything at all.
    for obj in ALLOWED:
        with model.cursor() as cur:
            cur.execute(f"SELECT * FROM {obj} LIMIT 1")
            cur.fetchall()

    for obj in DENIED_MARKET:
        _assert_refused(model, obj)

    model.close(); admin.close()
    return (f"{len(ALLOWED)} permitted reads succeeded; "
            f"{len(DENIED_MARKET)} market objects refused (42501)")


def t02_model_role_cannot_see_its_own_scoreboard():
    """The grading system can see the model. The model cannot see the grading
    system.

    Settled outcomes, grading, calibration, CLV, standings and prior
    performance are all refused. A model reading its own history opens a
    feedback channel that survives good intentions -- the prospective test stops
    being clean while continuing to look clean.
    """
    admin = h.connect()
    for obj in DENIED_SCOREBOARD:
        _assert_exists(admin, obj)

    model = h.connect_as("olp_model")
    for obj in DENIED_SCOREBOARD:
        _assert_refused(model, obj)

    model.close(); admin.close()
    return f"{len(DENIED_SCOREBOARD)} scoreboard objects refused (42501)"


def t02b_model_role_cannot_write_anywhere():
    """Read-only, and proven by attempting the write."""
    admin = h.connect()
    ev = h.scalar(admin, "SELECT olp_test.create_event('P5W','A','B',INTERVAL '4 hours')")
    model = h.connect_as("olp_model")
    attempts = [
        ("INSERT INTO public.market_snapshots (event_id, market_type, selection, "
         "line, price, sportsbook, source_provider, captured_at, is_in_play) "
         f"VALUES ('{ev}','SPREAD','A',-3.0,-110,'x','FIXTURE',NOW(),FALSE)"),
        "UPDATE public.events SET home_team = 'X'",
        "DELETE FROM public.market_snapshots",
        "CREATE TABLE public.p5_should_not_exist (x INT)",
    ]
    for sql in attempts:
        try:
            with model.cursor() as cur:
                cur.execute(sql)
        except psycopg.Error as exc:
            assert exc.sqlstate == INSUFFICIENT_PRIVILEGE, (
                f"write refused with SQLSTATE {exc.sqlstate}, not a permission "
                f"error: {exc}")
            continue
        raise AssertionError(f"olp_model performed a write: {sql[:60]}")
    model.close(); admin.close()
    return f"{len(attempts)} write attempts refused (42501)"


PACKAGE5 = [
    ("P5-T01", "Model cannot reach behind Package #4",
     t01_model_role_cannot_reach_behind_package_4),
    ("P5-T02", "Model cannot see its own scoreboard",
     t02_model_role_cannot_see_its_own_scoreboard),
    ("P5-T02b", "Model role is read-only",
     t02b_model_role_cannot_write_anywhere),
]
