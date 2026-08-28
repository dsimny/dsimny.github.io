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
from test_package4 import event, two_sided

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



# =============================================================================
# Increment 2 -- immutable belief storage and formation binding
# =============================================================================

M = dict(model_id="null-v0", model_version="0.1.0", feature_version="mkt-0.1.0",
         inputs_hash="deadbeef")


def _seed_executable(admin, src="P5-BELIEF", books=("bookA", "bookB")):
    """An executable moneyline market -- two books, so it clears the execution
    floor and is not UNUSABLE."""
    ev = event(admin, src)
    for bk in books:
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    return ev


def _form(conn, ev, sel="DAL", prob=0.62, **over):
    kw = dict(M); kw.update(over)
    return h.scalar(conn, """
        SELECT model.form_belief(%s, %s, %s, %s::uuid, %s,
                                 %s, %s::numeric, %s::numeric, %s,
                                 %s::numeric, %s::numeric, %s)""",
        (kw["model_id"], kw["model_version"], kw["feature_version"], ev,
         "MONEYLINE", sel, kw.get("line"), prob, kw["inputs_hash"],
         kw.get("lower"), kw.get("upper"), kw.get("method")))


def t12_belief_is_formed_prospectively_and_bound_by_the_database():
    """The model proposes; the database determines.

    The model supplies a probability and its own provenance. It does NOT supply
    the market side -- `formation_snapshot_id` and `market_probability_at_formation`
    are resolved from Package #4 and stamped. A model able to choose its own
    formation market probability could manufacture an edge that no later grading
    would detect.
    """
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)

    model = h.connect_as("olp_model")
    bid = _form(model, ev, lower=0.55, upper=0.68, method="bootstrap")
    model.close()
    assert bid is not None

    row = h.row(admin, """
        SELECT b.model_probability, b.lower_bound, b.upper_bound,
               b.uncertainty_method, b.market_probability_at_formation,
               b.formation_snapshot_id, b.formed_at IS NOT NULL,
               s.event_id, s.market_type::text, s.selection, s.line
        FROM model.beliefs b
        JOIN public.market_snapshots s ON s.id = b.formation_snapshot_id
        WHERE b.belief_id = %s""", (bid,))

    mkt = h.row(admin, """
        SELECT consensus_probability, executable_snapshot_id
        FROM public.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,))

    assert float(row[0]) == 0.62
    assert (float(row[1]), float(row[2])) == (0.55, 0.68) and row[3] == "bootstrap"
    # stamped by the database, matching Package #4 exactly
    assert row[4] == mkt[0], f"formation probability {row[4]} != market {mkt[0]}"
    assert row[5] == mkt[1], "bound snapshot is not the executable observation"
    assert row[6] is True
    # and the snapshot really is this wager
    assert (row[7], row[8], row[9], row[10]) == (ev, "MONEYLINE", "DAL", None)

    admin.close()
    return f"market side stamped from Package #4 (p={row[4]}), snapshot bound"


def t13_binding_must_describe_the_same_wager():
    """A foreign key proves the snapshot EXISTS. This proves it is the snapshot
    for THIS wager -- a Chiefs belief cannot be bound to a Ravens observation."""
    admin = h.connect(); h.reset(admin)
    ev1 = _seed_executable(admin, "P5-BIND-1")
    ev2 = _seed_executable(admin, "P5-BIND-2", books=("bookC", "bookD"))

    snap2 = h.scalar(admin, """
        SELECT executable_snapshot_id FROM public.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev2,))
    snap1_phi = h.scalar(admin, """
        SELECT executable_snapshot_id FROM public.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='PHI'""", (ev1,))

    INS = """INSERT INTO model.beliefs
        (model_id, model_version, feature_version, event_id, market_type,
         selection_key, line, model_probability,
         market_probability_at_formation, formation_snapshot_id,
         market_input_hash, formed_at, inputs_hash)
        VALUES ('m','1','f',%s,'MONEYLINE',%s,NULL,0.6,0.5,%s,'hash',NOW(),'h')"""

    for label, ev, sel, snap in (
            ("wrong event",     ev1, "DAL", snap2),
            ("wrong selection", ev1, "DAL", snap1_phi)):
        h.expect_error(
            lambda ev=ev, sel=sel, snap=snap: admin.execute(INS, (ev, sel, snap)),
            "BELIEF_BINDING_MISMATCH", label)

    admin.close()
    return "wrong-event and wrong-selection bindings both refused"


def t06_beliefs_cannot_be_rewritten():
    """Append-only as a database property. A later model version adds a row; it
    never edits an earlier one, or the grading dataset is contaminated over time
    without anyone intentionally cheating."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev)

    h.expect_error(
        lambda: admin.execute(
            "UPDATE model.beliefs SET model_probability = 0.99 WHERE belief_id = %s",
            (bid,)),
        "APPEND_ONLY_VIOLATION", "UPDATE")
    h.expect_error(
        lambda: admin.execute("DELETE FROM model.beliefs WHERE belief_id = %s", (bid,)),
        "APPEND_ONLY_VIOLATION", "DELETE")

    assert h.scalar(admin, "SELECT model_probability FROM model.beliefs "
                           "WHERE belief_id=%s", (bid,)) is not None
    admin.close()
    return "UPDATE and DELETE both refused; the row stands"


def t24_impossible_beliefs_are_refused():
    """Probability and interval invariants, in SQL rather than in later code."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)

    cases = [
        ("probability 0",        dict(prob=0)),
        ("probability 1",        dict(prob=1)),
        ("probability above 1",  dict(prob=1.4)),
        ("negative probability", dict(prob=-0.2)),
        ("inverted interval",    dict(prob=0.6, lower=0.7, upper=0.5, method="boot")),
        ("point outside interval", dict(prob=0.9, lower=0.5, upper=0.6, method="boot")),
        ("bounds without method", dict(prob=0.6, lower=0.5, upper=0.7)),
        ("method without bounds", dict(prob=0.6, method="boot")),
        ("upper above 1",        dict(prob=0.6, lower=0.5, upper=1.2, method="boot")),
    ]
    for label, kw in cases:
        prob = kw.pop("prob")
        try:
            _form(admin, ev, prob=prob, **kw)
        except psycopg.Error:
            continue
        raise AssertionError(f"{label}: accepted, but must be refused")

    assert h.scalar(admin, "SELECT count(*) FROM model.beliefs") == 0
    admin.close()
    return f"{len(cases)} impossible beliefs refused"


def t25_a_moved_market_earns_a_new_belief():
    """Append-only must not mean one prediction forever.

    A new market observation is a new fact, so a second prospective belief is
    allowed and expected. A second belief against the SAME observation is a
    duplicate, not a new claim -- determinism (P5-T04) says the same inputs and
    version must give the same answer.
    """
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    first = _form(admin, ev, prob=0.62)

    h.expect_error(lambda: _form(admin, ev, prob=0.71),
                   "uq_belief_identity", "same observation twice")

    # the market moves: both books requote, so a new executable snapshot exists
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -175, 150, bk)
    second = _form(admin, ev, prob=0.71)

    rows = h.rows(admin, """
        SELECT model_probability, formation_snapshot_id FROM model.beliefs
        WHERE event_id=%s ORDER BY formed_at, model_probability""", (ev,))
    assert len(rows) == 2, rows
    assert rows[0][1] != rows[1][1], "both beliefs bound to the same observation"
    assert first != second
    admin.close()
    return "duplicate refused; a moved market earned a second belief"


def t26_model_cannot_write_beliefs_directly():
    """The model may PROPOSE a belief. Only the database may record one."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    snap = h.scalar(admin, """
        SELECT executable_snapshot_id FROM public.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,))

    model = h.connect_as("olp_model")
    for label, sql, params in (
        ("INSERT", """INSERT INTO model.beliefs
             (model_id, model_version, feature_version, event_id, market_type,
              selection_key, line, model_probability,
              market_probability_at_formation, formation_snapshot_id,
              market_input_hash, formed_at, inputs_hash)
             VALUES ('m','1','f',%s,'MONEYLINE','DAL',NULL,0.99,0.5,%s,'hash',NOW(),'h')""",
         (ev, snap)),
        ("UPDATE", "UPDATE model.beliefs SET model_probability = 0.99", ()),
        ("DELETE", "DELETE FROM model.beliefs", ()),
    ):
        try:
            with model.cursor() as cur:
                cur.execute(sql, params)
        except psycopg.Error as exc:
            assert exc.sqlstate == INSUFFICIENT_PRIVILEGE, (
                f"{label} refused with {exc.sqlstate}, not a permission error")
            continue
        raise AssertionError(f"olp_model performed a direct {label} on beliefs")

    # ...but the sanctioned path works
    bid = _form(model, ev)
    assert bid is not None
    model.close(); admin.close()
    return "direct INSERT/UPDATE/DELETE refused (42501); form_belief() works"



def t27_anchor_and_input_hash_prove_different_things():
    """Two proofs, deliberately not one.

    `formation_snapshot_id` says which executable quote anchored the belief and
    whether it has since been superseded. It is an ANCHOR -- market_intelligence
    encodes consensus, dispersion, book counts and movement derived from many
    quotes, so calling one snapshot the belief's full provenance would overstate
    what it proves.

    `market_input_hash` says what market-intelligence state the model actually
    received, so a later reconstruction of the input surface shows up as a
    mismatch instead of being absorbed.
    """
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev)

    stored, anchor = h.row(admin, """
        SELECT market_input_hash, formation_snapshot_id
        FROM model.beliefs WHERE belief_id = %s""", (bid,))

    # the anchor is Package #4's executable observation, nothing invented
    exec_snap = h.scalar(admin, """
        SELECT executable_snapshot_id FROM public.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,))
    assert anchor == exec_snap, "anchor is not the executable observation"

    # the hash is over the surface the MODEL sees, and is reproducible
    recomputed = h.scalar(admin, """
        SELECT md5(to_jsonb(t)::text) FROM model_input.market_intelligence t
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,))
    assert stored == recomputed, (
        f"stored input hash {stored} != recomputed {recomputed} -- the hash is "
        "not over model_input.market_intelligence")

    # the model cannot influence it: its own declared inputs_hash is separate
    declared = h.scalar(admin, "SELECT inputs_hash FROM model.beliefs WHERE belief_id=%s", (bid,))
    assert declared == M["inputs_hash"] and declared != stored

    # a changed market surface changes the hash
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -175, 150, bk)
    bid2 = _form(admin, ev, prob=0.71)
    stored2 = h.scalar(admin, "SELECT market_input_hash FROM model.beliefs WHERE belief_id=%s", (bid2,))
    assert stored2 != stored, "the market moved but the input hash did not change"

    admin.close()
    return "anchor = executable observation; input hash reproducible and moves with the market"


PACKAGE5 = [
    ("P5-T01", "Model cannot reach behind Package #4",
     t01_model_role_cannot_reach_behind_package_4),
    ("P5-T02", "Model cannot see its own scoreboard",
     t02_model_role_cannot_see_its_own_scoreboard),
    ("P5-T02b", "Model role is read-only",
     t02b_model_role_cannot_write_anywhere),
    ("P5-T12", "Belief formed prospectively, bound by the database",
     t12_belief_is_formed_prospectively_and_bound_by_the_database),
    ("P5-T13", "Binding must describe the same wager",
     t13_binding_must_describe_the_same_wager),
    ("P5-T06", "Beliefs cannot be rewritten",
     t06_beliefs_cannot_be_rewritten),
    ("P5-T24", "Impossible beliefs are refused",
     t24_impossible_beliefs_are_refused),
    ("P5-T25", "A moved market earns a new belief",
     t25_a_moved_market_earns_a_new_belief),
    ("P5-T26", "Model cannot write beliefs directly",
     t26_model_cannot_write_beliefs_directly),
    ("P5-T27", "Anchor and input hash prove different things",
     t27_anchor_and_input_hash_prove_different_things),
]
