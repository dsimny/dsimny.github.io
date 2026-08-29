"""OLP-M1 Package #5 -- Model Layer.

Increment 1 is the trust boundary and nothing else. Package #5 must not be able
to bypass Package #4, and must not be able to see its own scoreboard. Every
later statistical claim rests on this, so it is proven before anything else is
built.

These tests attempt each read AS THE ACTUAL `olp_model` ROLE and require
PostgreSQL to refuse. Inspecting grants would only test our belief about the
grants; the database is the thing that decides.
"""

import threading
import time

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
    # the scoreboard proper -- Package #5's own grading surface
    "grading.wager_outcomes",
    "grading.belief_grades",
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
    kw["model_version"] = over.get("model_version", kw["model_version"])
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

    # via attempt_belief -- since 055 that is the only path a model has, so the
    # denominator cannot be bypassed
    model = h.connect_as("olp_model")
    bid = h.row(model, """
        SELECT * FROM model.attempt_belief(%s,%s,%s,%s::uuid,'MONEYLINE','DAL',
            NULL::numeric, 0.62::numeric, %s, 0.55::numeric, 0.68::numeric,
            'bootstrap')""",
        (M["model_id"], M["model_version"], M["feature_version"], ev,
         M["inputs_hash"]))[0]
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

    # ...but the sanctioned path works. Since 055 that path is attempt_belief;
    # form_belief is no longer reachable by the model at all.
    bid, reason = _attempt(model, ev, mid=M["model_id"], ver=M["model_version"])
    assert bid is not None and reason == "ELIGIBLE"
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



# =============================================================================
# Increment 3 -- grading primitives
# =============================================================================

def _resolve(admin, ev, sel, outcome, market="MONEYLINE", line=None):
    return h.scalar(admin, """
        SELECT grading.record_outcome(%s::uuid, %s, %s, %s::numeric, %s, 'test')""",
        (ev, market, sel, line, outcome))


def _grade(conn, belief_id):
    return h.scalar(conn, "SELECT grading.grade_belief(%s::uuid)", (belief_id,))


def _g(admin, belief_id, cols):
    return h.row(admin, f"SELECT {cols} FROM grading.belief_grades "
                        f"WHERE belief_id = %s", (belief_id,))


def t14_a_resolved_belief_is_graded_from_stored_facts():
    """The positive path. Every input is a stored fact: the belief's own
    probability, its frozen formation baseline, and a recorded outcome."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev, prob=0.62)
    baseline = h.scalar(admin, "SELECT market_probability_at_formation "
                               "FROM model.beliefs WHERE belief_id=%s", (bid,))
    _resolve(admin, ev, "DAL", "WIN")
    _grade(admin, bid)

    outcome, status, mb, mll, kb, kll, bd = _g(
        admin, bid, "outcome::text, scoring_status::text, model_brier, "
                    "model_log_loss, market_brier, market_log_loss, brier_delta")
    assert (outcome, status) == ("WIN", "SCORED")
    assert abs(float(mb) - (0.62 - 1) ** 2) < 1e-7, mb
    assert abs(float(kb) - (float(baseline) - 1) ** 2) < 1e-7, (kb, baseline)
    assert abs(float(bd) - (float(mb) - float(kb))) < 1e-7
    admin.close()
    return f"graded WIN: model brier {float(mb):.4f} vs market {float(kb):.4f}"


def t15_scoring_rules_match_hand_computed_values():
    """Independently known values, not the implementation checked against
    itself."""
    admin = h.connect()
    # 1e-7 rather than 1e-9 throughout: grades are stored NUMERIC(12,8), so the
    # stored value is rounded and a tighter bound tests the storage precision
    # rather than the arithmetic.
    cases = [
        ("brier",    0.75, True,  0.0625),
        ("brier",    0.75, False, 0.5625),
        ("brier",    0.50, True,  0.25),
        ("brier",    0.10, False, 0.01),
        ("log_loss", 0.50, True,  0.6931471805599453),
        ("log_loss", 0.25, False, 0.2876820724517809),
        ("log_loss", 0.80, True,  0.2231435513142097),
        ("log_loss", 0.90, False, 2.302585092994046),
    ]
    for fn, p_, won, expected in cases:
        got = h.scalar(admin, f"SELECT grading.{fn}(%s::numeric, %s)", (p_, won))
        assert abs(float(got) - expected) < 1e-9, \
            f"{fn}({p_}, {won}) = {got}, expected {expected}"
    admin.close()
    return f"{len(cases)} hand-computed Brier and log-loss values matched"


def t22_the_grader_scores_forecasts_not_bets():
    """CORRECTED before implementation -- see PACKAGE5_PREREG.md section 12.

    The original pre-registered claim, that a losing 70% forecast outscores a
    winning 51% forecast, is FALSE on a single observation: the winner scores
    better under both rules, and should. That intuition is an AGGREGATE property
    and belongs to 053.

    What is true and load-bearing on one observation is side-indifference plus
    properness -- the grade depends on the forecast and the outcome, never on
    which side happened to win -- plus the structural half: no win-rate or
    profit field exists to grade on.
    """
    admin = h.connect()

    # side-indifference: 0.70 on a loser scores exactly as 0.30 on a winner
    for fn in ("brier", "log_loss"):
        a = h.scalar(admin, f"SELECT grading.{fn}(0.70::numeric, FALSE)")
        b = h.scalar(admin, f"SELECT grading.{fn}(0.30::numeric, TRUE)")
        assert abs(float(a) - float(b)) < 1e-12, f"{fn}: {a} != {b}"

    # properness: confident-and-wrong is punished harder
    worse = h.scalar(admin, "SELECT grading.brier(0.90::numeric, FALSE)")
    milder = h.scalar(admin, "SELECT grading.brier(0.60::numeric, FALSE)")
    assert float(worse) > float(milder)

    # and the honest direction of the original intuition, stated truthfully
    loser70 = float(h.scalar(admin, "SELECT grading.brier(0.70::numeric, FALSE)"))
    winner51 = float(h.scalar(admin, "SELECT grading.brier(0.51::numeric, TRUE)"))
    assert winner51 < loser70, (
        "on ONE observation the winner scores better -- if this ever flips, the "
        "scoring rule is not proper")

    # structural: nothing in the grade expresses bet accounting
    forbidden = {"won", "win", "is_win", "winner", "profit", "pnl", "roi",
                 "stake", "units", "net", "return", "hit_rate", "win_rate"}
    cols = {r[0] for r in h.rows(admin, """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='grading' AND table_name='belief_grades'""")}
    leaked = cols & forbidden
    assert not leaked, f"the grade record has bet-accounting fields: {leaked}"
    admin.close()
    return ("side-indifferent, proper, and no win-rate field; single-observation "
            "direction stated truthfully")


def t17_the_baseline_is_the_frozen_formation_probability():
    """Scoring a model against a market it never saw would not be grading it."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev, prob=0.62)
    frozen = float(h.scalar(admin, "SELECT market_probability_at_formation "
                                   "FROM model.beliefs WHERE belief_id=%s", (bid,)))

    # the market moves a long way AFTER formation
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -400, 320, bk)
    moved = float(h.scalar(admin, """
        SELECT consensus_probability FROM public.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,)))
    assert abs(moved - frozen) > 0.1, "fixture failed to move the market"

    _resolve(admin, ev, "DAL", "WIN")
    _grade(admin, bid)
    kb = float(_g(admin, bid, "market_brier")[0])

    assert abs(kb - (frozen - 1) ** 2) < 1e-7, "baseline is not the frozen value"
    assert abs(kb - (moved - 1) ** 2) > 1e-6, "baseline used the LATER market"
    admin.close()
    return f"baseline held at formation {frozen:.4f}, not the moved {moved:.4f}"


def t18_clv_is_observed_never_asserted():
    """A non-zero future CLV is recorded and changes nothing about validity."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev, prob=0.62)

    form = h.row(admin, """
        SELECT s.sportsbook, s.price, s.line FROM model.beliefs b
        JOIN public.market_snapshots s ON s.id = b.formation_snapshot_id
        WHERE b.belief_id = %s""", (bid,))
    # a closing quote at a materially different price, same book, same line
    admin.execute("""
        INSERT INTO public.market_snapshots
            (event_id, market_type, selection, line, price, sportsbook,
             source_provider, captured_at, is_in_play, is_closing_snapshot)
        VALUES (%s,'MONEYLINE','DAL',%s,-220,%s,'FIXTURE',NOW(),FALSE,TRUE)""",
        (ev, form[2], form[0]))

    _resolve(admin, ev, "DAL", "WIN")
    _grade(admin, bid)
    status, delta, mb = _g(admin, bid, "clv_status, clv_payout_delta, model_brier")

    assert status == "OBSERVED", status
    assert delta is not None and abs(float(delta)) > 0, "CLV recorded as zero"
    # ...and the probabilistic score is untouched by it
    assert abs(float(mb) - (0.62 - 1) ** 2) < 1e-9, \
        "a non-zero CLV changed the probabilistic score"
    admin.close()
    return f"CLV observed at {float(delta):+.4f} payout units; scores unaffected"


def t28_grading_cannot_rewrite_history():
    """Grading is append-only and cannot touch the belief it grades."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev, prob=0.62)
    before = h.row(admin, "SELECT model_probability, market_probability_at_formation "
                          "FROM model.beliefs WHERE belief_id=%s", (bid,))
    _resolve(admin, ev, "DAL", "WIN")
    _grade(admin, bid)

    after = h.row(admin, "SELECT model_probability, market_probability_at_formation "
                         "FROM model.beliefs WHERE belief_id=%s", (bid,))
    assert before == after, "grading mutated the belief"

    h.expect_error(lambda: admin.execute(
        "UPDATE grading.belief_grades SET model_brier = 0 WHERE belief_id=%s", (bid,)),
        "APPEND_ONLY_VIOLATION", "UPDATE a grade")
    h.expect_error(lambda: admin.execute(
        "DELETE FROM grading.belief_grades WHERE belief_id=%s", (bid,)),
        "APPEND_ONLY_VIOLATION", "DELETE a grade")
    h.expect_error(lambda: _grade(admin, bid),
        "uq_grade_per_belief", "grade the same belief twice")
    h.expect_error(lambda: admin.execute(
        "UPDATE grading.wager_outcomes SET outcome='LOSS'"),
        "APPEND_ONLY_VIOLATION", "rewrite a recorded outcome")
    admin.close()
    return "belief untouched; grades and outcomes both append-only"


def t29_an_unresolved_wager_cannot_be_graded():
    """A belief cannot be graded before the world has answered it."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev)
    h.expect_error(lambda: _grade(admin, bid), "WAGER_UNRESOLVED", "no outcome")

    # and the wrong wager's outcome does not count as this one's
    _resolve(admin, ev, "PHI", "WIN")
    h.expect_error(lambda: _grade(admin, bid), "WAGER_UNRESOLVED", "other selection")

    assert h.scalar(admin, "SELECT count(*) FROM grading.belief_grades") == 0
    admin.close()
    return "ungraded without an outcome for that exact wager"


def t30_push_and_void_are_excluded_not_silently_scored():
    """Pre-registered treatment. A probabilistic forecast can only be scored
    against a binary outcome, so PUSH and VOID are recorded and excluded rather
    than quietly entering the sample."""
    admin = h.connect(); h.reset(admin)
    out = []
    for src, outcome, expect in (("P5-PUSH", "PUSH", "EXCLUDED_PUSH"),
                                 ("P5-VOID", "VOID", "EXCLUDED_VOID")):
        ev = _seed_executable(admin, src)
        bid = _form(admin, ev, prob=0.62)
        _resolve(admin, ev, "DAL", outcome)
        _grade(admin, bid)
        status, mb, mll, kb = _g(admin, bid,
            "scoring_status::text, model_brier, model_log_loss, market_brier")
        assert status == expect, (outcome, status)
        assert mb is None and mll is None and kb is None, \
            f"{outcome} carries scores and would enter an aggregate"
        out.append(f"{outcome}->{status}")

    scored = h.scalar(admin, "SELECT count(*) FROM grading.belief_grades "
                             "WHERE scoring_status='SCORED'")
    assert scored == 0, "an excluded row is counted as scored"
    admin.close()
    return "; ".join(out) + "; scores NULL so they cannot reach an aggregate"


def t31_grading_permissions_point_one_way():
    """The grader may read beliefs and write grades. It may not write beliefs.
    The model may do neither."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _form(admin, ev)
    _resolve(admin, ev, "DAL", "WIN")

    grader = h.connect_as("olp_grader")
    with grader.cursor() as cur:                    # can read beliefs
        cur.execute("SELECT count(*) FROM model.beliefs"); cur.fetchall()
    _grade(grader, bid)                             # can write grades
    for label, sql in (
        ("INSERT belief", "INSERT INTO model.beliefs (model_id) VALUES ('x')"),
        ("UPDATE belief", "UPDATE model.beliefs SET model_probability = 0.5"),
        ("DELETE belief", "DELETE FROM model.beliefs")):
        try:
            with grader.cursor() as cur:
                cur.execute(sql)
        except psycopg.Error as exc:
            assert exc.sqlstate == INSUFFICIENT_PRIVILEGE, \
                f"grader {label} refused with {exc.sqlstate}, not a permission error"
            continue
        raise AssertionError(f"olp_grader performed {label}")
    grader.close()

    model = h.connect_as("olp_model")               # sees none of it
    for obj in ("grading.wager_outcomes", "grading.belief_grades"):
        _assert_refused(model, obj)
    model.close(); admin.close()
    return "grader reads beliefs and writes grades; cannot write beliefs; model refused both"



# =============================================================================
# Increment 4 -- calibration
# =============================================================================

def _tune(admin, **kw):
    """Lower the thresholds so a test can exercise the full pipeline without
    planting 500 beliefs. The shipped defaults are asserted separately by
    t33; this only ever narrows the sample, never the maths."""
    sets = ", ".join(f"{k} = {v}" for k, v in kw.items())
    admin.execute(f"UPDATE grading.calibration_config SET {sets} WHERE id")


def _market(admin, src, home_price, away_price):
    """One executable moneyline market at a chosen price, two books."""
    ev = event(admin, src)
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, home_price, away_price, bk)
    return ev


def _plant(admin, src, prob, outcome, home_price=-150, away_price=130,
           model_id="planted", version="1"):
    """Form one belief and grade it against a chosen outcome."""
    ev = _market(admin, src, home_price, away_price)
    bid = _form(admin, ev, prob=prob, model_id=model_id, model_version=version)
    _resolve(admin, ev, "DAL", outcome)
    _grade(admin, bid)
    return bid


def t16_bins_are_equal_count_and_wilson_matches_hand_values():
    """Equal-count bins, not fixed 10%-wide buckets, and a Wilson interval
    checked against an independently computed value."""
    admin = h.connect(); h.reset(admin)

    # Wilson 95% CI for 8/10, computed independently in Python:
    #   z^2 = 3.8416, denom = 1.38416
    #   centre = (0.8 + 3.8416/20) / 1.38416
    #   margin = (1.96/1.38416) * sqrt(0.8*0.2/10 + 3.8416/400)
    # -> [0.4901568467, 0.9433190520]. My first hand value was wrong at the
    # sixth decimal place; the implementation was right.
    lo = float(h.scalar(admin, "SELECT grading.wilson_low(8::bigint, 10::bigint, 1.96)"))
    hi = float(h.scalar(admin, "SELECT grading.wilson_high(8::bigint, 10::bigint, 1.96)"))
    assert abs(lo - 0.4901568467) < 1e-7, lo
    assert abs(hi - 0.9433190520) < 1e-7, hi
    # bounded to [0,1] at the extremes. Note the Wilson bound at 0/5 is NEAR
    # zero, not exactly zero -- that is the interval being honest about a small
    # sample, and the clamp guarantees the range, not the endpoint.
    for s_, n_, lo_ok, hi_ok in ((0, 5, True, False), (5, 5, False, True)):
        a = float(h.scalar(admin, f"SELECT grading.wilson_low({s_}::bigint, {n_}::bigint, 1.96)"))
        b = float(h.scalar(admin, f"SELECT grading.wilson_high({s_}::bigint, {n_}::bigint, 1.96)"))
        assert 0.0 <= a <= b <= 1.0, (s_, n_, a, b)
        if lo_ok:
            assert a < 0.01 and b > 0.4, (a, b)   # 0/5 says "could still be 40%"
        if hi_ok:
            assert b > 0.99 and a < 0.6, (a, b)   # 5/5 says "could be as low as 57%"

    # equal-count binning: 12 beliefs, 3 bins -> 4 each, split by probability
    # Deliberately CLUSTERED. An evenly spread fixture gives 4/4/4 under fixed
    # WIDTH bucketing too, so it cannot tell the two schemes apart -- a negative
    # control caught exactly that. Here fixed-width would give 8/1/3.
    _tune(admin, min_sample=12, bin_count=3, min_bin_count=3)
    probs = [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.60, 0.70, 0.80, 0.90]
    for i, pr in enumerate(probs):
        _plant(admin, f"T16-{i}", pr, "WIN" if i % 2 == 0 else "LOSS")

    bins = h.rows(admin, """
        SELECT bin, n, mean_predicted FROM grading.calibration_bins('planted','1')""")
    assert [r[1] for r in bins] == [4, 4, 4], (
        f"bins are not equal-count: {bins}. Fixed-width bucketing would give "
        "8/1/3 on this clustered fixture.")
    means = [float(r[2]) for r in bins]
    assert means[0] < means[1] < means[2], means
    admin.close()
    return f"Wilson [{lo:.4f}, {hi:.4f}] matched; 3 bins of 4, ordered by probability"


def t23_a_single_bad_bin_fails_despite_a_good_weighted_average():
    """The weighted average alone is not sufficient. A model can look fine
    overall while being badly wrong in one region of the probability space.

    This needs at least three bins to construct at all: with B equal bins and a
    single bad bin of error e, the weighted error is e/B, so two bins can never
    hold e above 7.5pp while the average stays under 3pp. Five bins of six.
    """
    admin = h.connect(); h.reset(admin)

    def build(tag, last_p, last_wins):
        _tune(admin, min_sample=30, bin_count=5, min_bin_count=6)
        # four perfectly calibrated bins: predicted == observed exactly
        plan = []
        for k, pr in ((1, 1/6), (2, 2/6), (3, 0.5), (4, 4/6)):
            plan += [(round(pr, 6), i < k) for i in range(6)]
        plan += [(last_p, i < last_wins) for i in range(6)]
        for i, (pr, won) in enumerate(plan):
            _plant(admin, f"{tag}-{i}", pr, "WIN" if won else "LOSS")
        return h.row(admin, "SELECT * FROM grading.calibration_report('planted','1')")

    # top bin predicted 0.78, observed 4/6 = 0.667 -> 11.3pp out
    n, eligible, werr, worst, wbin, status = build("T23a", 0.78, 4)
    assert worst is not None, (
        "no worst-bin error was reported -- the per-bin rule is not being "
        "evaluated at all, so only the weighted average is in force")
    werr, worst = float(werr), float(worst)
    assert n == 30 and eligible is True, (n, eligible)
    assert werr <= 0.03, f"fixture failed: weighted error {werr:.4f} is not inside 3pp"
    assert worst > 0.075, f"fixture failed: worst bin {worst:.4f} is not outside 7.5pp"
    assert status == "DEGRADED", (
        f"weighted error {werr:.4f} passed and a bin at {worst:.4f} was ignored")

    # control: same shape, top bin predicted 0.70 -> 3.3pp out, inside the bound
    h.reset(admin)
    _, _, werr2, worst2, _, status2 = build("T23b", 0.70, 4)
    assert float(worst2) <= 0.075 and status2 == "CALIBRATED", (worst2, status2)

    admin.close()
    return (f"weighted {werr:.4f} inside 3pp but a bin at {worst:.4f} -> DEGRADED; "
            f"same shape with the bin at {float(worst2):.4f} -> CALIBRATED")


def t21_a_model_can_be_calibrated_and_still_add_nothing():
    """The state the architecture must be able to express.

    A model that always says ~0.5 into a market that knows which side is which
    is calibrated and strictly worse than the market. If that cannot be
    represented, the system quietly pressures everyone toward a flattering
    conclusion.

    The fixture is fiddly for an instructive reason. A first attempt gave every
    sharp favourite the same jitter, so equal-count binning by MODEL probability
    put all ten favourites in one bin, which then observed 0.8 against a
    predicted 0.499 -- a 30pp calibration error. That is the binning doing its
    job: a model whose 0.5s are secretly 0.8s is NOT calibrated. To be genuinely
    calibrated at 0.5 each bin must hold a 50/50 mix.
    """
    admin = h.connect(); h.reset(admin)
    _tune(admin, min_sample=20, bin_count=2, min_bin_count=5)

    # ten sharp favourites, 8 win; ten sharp dogs, 2 win. Jitter assigns half of
    # each group to each bin, so every bin is a 50/50 mix and observes 0.5.
    for i in range(10):
        _plant(admin, f"T21-H{i}", 0.499 if i < 5 else 0.501,
               "WIN" if (i < 4 or 5 <= i < 9) else "LOSS",
               home_price=-600, away_price=500)
    for i in range(10):
        _plant(admin, f"T21-L{i}", 0.499 if i < 5 else 0.501,
               "WIN" if i in (0, 5) else "LOSS",
               home_price=500, away_price=-600)

    cal = h.row(admin, "SELECT * FROM grading.calibration_report('planted','1')")
    st  = h.row(admin, "SELECT * FROM grading.standing_report('planted','1')")
    calibration_status, werr, bss, standing = cal[5], float(cal[2]), float(st[3]), st[7]

    assert calibration_status == "CALIBRATED", (calibration_status, cal)
    assert bss < 0, (
        f"Brier skill score came back {bss}; the model should be strictly worse "
        "than the market here, so either the fixture or standing_report is wrong")
    assert standing == "RESEARCH", standing
    admin.close()
    return (f"CALIBRATED (weighted error {werr:.4f}) and RESEARCH together; "
            f"Brier skill score {bss:+.3f}")


def t32_win_rate_and_probabilistic_quality_can_disagree():
    """The aggregate form of the principle P5-T22 was originally trying to
    state, and the form that is actually true.

    Ranking by win rate and ranking by probabilistic quality are different
    orderings. A badly calibrated model can win far more often and still be the
    worse forecaster -- which is why the grader has no win-rate field to rank on.
    """
    admin = h.connect(); h.reset(admin)
    _tune(admin, min_sample=10, bin_count=2, min_bin_count=3)

    # LUCKY: says 0.52 every time; wins 9 of 10.
    for i in range(10):
        _plant(admin, f"T32-A{i}", 0.52, "WIN" if i < 9 else "LOSS",
               model_id="lucky", version="1")
    # HONEST: says 0.30 every time; wins 3 of 10 -- perfectly calibrated.
    for i in range(10):
        _plant(admin, f"T32-B{i}", 0.30, "WIN" if i < 3 else "LOSS",
               model_id="honest", version="1")

    def stats(mid):
        n, mb, kb, bss, ml, kl, lli, st = h.row(
            admin, "SELECT * FROM grading.standing_report(%s,'1')", (mid,))
        wins = h.scalar(admin, """
            SELECT count(*) FILTER (WHERE g.outcome='WIN')::numeric / count(*)
            FROM grading.belief_grades g JOIN model.beliefs b
              ON b.belief_id = g.belief_id WHERE b.model_id = %s""", (mid,))
        return float(wins), float(mb)

    lucky_wr, lucky_brier = stats("lucky")
    honest_wr, honest_brier = stats("honest")

    assert lucky_wr > honest_wr, (lucky_wr, honest_wr)
    assert honest_brier < lucky_brier, (
        f"fixture failed: the honest model ({honest_brier:.4f}) did not beat the "
        f"lucky one ({lucky_brier:.4f}) on Brier")
    admin.close()
    return (f"win rate ranks lucky first ({lucky_wr:.0%} vs {honest_wr:.0%}); "
            f"Brier ranks honest first ({honest_brier:.4f} vs {lucky_brier:.4f})")


def t33_the_shipped_thresholds_are_the_pre_registered_ones():
    """The tests above lower the sample size to stay fast, and they mutate the
    config row to do it. So this asserts the COLUMN DEFAULTS -- what actually
    ships -- which no test can tune, rather than whatever the row happens to
    hold. A convenience tweak must not be able to become the contract.
    """
    admin = h.connect(); h.reset(admin)
    defaults = dict(h.rows(admin, """
        SELECT column_name, column_default FROM information_schema.columns
        WHERE table_schema='grading' AND table_name='calibration_config'"""))
    expected = {"min_sample": "500", "bin_count": "10",
                "weighted_error_max": "0.0300", "bin_error_max": "0.0750",
                "wilson_z": "1.96"}
    for col, want in expected.items():
        got = (defaults.get(col) or "").split("::")[0].strip()
        assert got == want, f"{col} ships as {got}, pre-registered as {want}"

    # restore the shipped values, then confirm one graded belief makes no claim
    admin.execute("""
        UPDATE grading.calibration_config SET
            min_sample=500, bin_count=10, weighted_error_max=0.0300,
            bin_error_max=0.0750, wilson_z=1.96, min_bin_count=30 WHERE id""")
    _plant(admin, "T33", 0.60, "WIN")
    cal = h.row(admin, "SELECT * FROM grading.calibration_report('planted','1')")
    st  = h.row(admin, "SELECT * FROM grading.standing_report('planted','1')")
    assert cal[1] is False and cal[5] == "PROVISIONAL", cal
    assert st[7] == "RESEARCH", st
    admin.close()
    return ("defaults ship as N=500, 10 bins, 3pp / 7.5pp, z=1.96; "
            "one graded belief is PROVISIONAL / RESEARCH")



# =============================================================================
# Increment 5 -- the null producer
# =============================================================================

def _null(conn, ev, sel="DAL", line=None, version="1.0.0"):
    return h.scalar(conn, """
        SELECT model.null_model_belief(%s::uuid, %s, %s, %s::numeric, %s)""",
        (ev, "MONEYLINE", sel, line, version))


def _assert_zero_divergence(admin, belief_id):
    """The null identity. EXACT, not close enough."""
    p_model, p_market, delta = h.row(admin, """
        SELECT model_probability, market_probability_at_formation, probability_delta
        FROM model.belief_deltas WHERE belief_id = %s""", (belief_id,))
    assert p_model == p_market, (
        f"null model said {p_model}, market said {p_market} -- not an exact "
        "reproduction")
    assert float(delta) == 0.0, f"probability_delta is {delta}, not exactly zero"
    return p_model


def t34_the_null_producer_reproduces_the_market_exactly():
    """Zero divergence at formation is an identity, and the binding and input
    hash are stamped by the ordinary path with nothing special about them."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)

    model = h.connect_as("olp_model")      # ordinary model authority, nothing more
    bid = _null(model, ev)
    model.close()

    p = _assert_zero_divergence(admin, bid)

    mkt, snap, hsh, lo, hi, meth, mid, ver = h.row(admin, """
        SELECT b.market_probability_at_formation, b.formation_snapshot_id,
               b.market_input_hash, b.lower_bound, b.upper_bound,
               b.uncertainty_method, b.model_id, b.model_version
        FROM model.beliefs b WHERE b.belief_id = %s""", (bid,))
    exec_snap, recomputed = h.row(admin, """
        SELECT executable_snapshot_id, md5(to_jsonb(t)::text)
        FROM model_input.market_intelligence t
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,))

    assert snap == exec_snap, "binding is not the ordinary executable anchor"
    assert hsh == recomputed, "input hash is not the ordinary stamped value"
    # it states no interval, because it has no uncertainty model
    assert (lo, hi, meth) == (None, None, None), (lo, hi, meth)
    assert (mid, ver) == ("null", "1.0.0")
    admin.close()
    return f"null belief p={p} == market, delta exactly 0, ordinary binding and hash"


def t35_the_null_producer_grades_and_calibrates_through_the_ordinary_path():
    """No special grader path and no special calibration path. It goes through
    052 and 053 exactly as any model would, and comes out AT_PARITY -- which is
    the honest verdict for a model that reproduces the market."""
    admin = h.connect(); h.reset(admin)
    _tune(admin, min_sample=20, bin_count=2, min_bin_count=5)

    # twenty markets; the market is well calibrated, so the null model is too
    for i in range(10):
        ev = _market(admin, f"T35-H{i}", -600, 500)
        bid = _null(admin, ev)
        _resolve(admin, ev, "DAL", "WIN" if i < 8 else "LOSS")
        _grade(admin, bid)
    for i in range(10):
        ev = _market(admin, f"T35-L{i}", 500, -600)
        bid = _null(admin, ev)
        _resolve(admin, ev, "DAL", "WIN" if i < 2 else "LOSS")
        _grade(admin, bid)

    # graded by 052 with no special casing: model and market scores identical
    same = h.scalar(admin, """
        SELECT count(*) FROM grading.belief_grades
        WHERE scoring_status='SCORED'
          AND (model_brier IS DISTINCT FROM market_brier
            OR model_log_loss IS DISTINCT FROM market_log_loss
            OR brier_delta <> 0 OR log_loss_delta <> 0)""")
    assert same == 0, f"{same} graded null beliefs diverged from the market"

    st = h.row(admin, "SELECT * FROM grading.standing_report('null','1.0.0')")
    n, bss, lli, standing = st[0], float(st[3]), float(st[6]), st[7]
    assert n == 20, n
    assert bss == 0.0 and lli == 0.0, (bss, lli)
    assert standing == "AT_PARITY", standing

    cal = h.row(admin, "SELECT * FROM grading.calibration_report('null','1.0.0')")
    assert cal[0] == 20 and cal[5] in ("CALIBRATED", "DEGRADED"), cal
    admin.close()
    return (f"graded through 052 (brier_delta 0 on all {n}); 053 gives "
            f"BSS {bss}, standing {standing}, calibration {cal[5]}")


def t36_a_planted_clv_does_not_disturb_the_null_identity():
    """Zero divergence at FORMATION is asserted. Zero future CLV is not, because
    the closing probability moves -- an empirical expectation, not a fact."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    bid = _null(admin, ev)

    form = h.row(admin, """
        SELECT s.sportsbook, s.line FROM model.beliefs b
        JOIN public.market_snapshots s ON s.id = b.formation_snapshot_id
        WHERE b.belief_id = %s""", (bid,))
    admin.execute("""
        INSERT INTO public.market_snapshots
            (event_id, market_type, selection, line, price, sportsbook,
             source_provider, captured_at, is_in_play, is_closing_snapshot)
        VALUES (%s,'MONEYLINE','DAL',%s,-260,%s,'FIXTURE',NOW(),FALSE,TRUE)""",
        (ev, form[1], form[0]))

    _resolve(admin, ev, "DAL", "WIN")
    _grade(admin, bid)
    status, delta, bd = h.row(admin, """
        SELECT clv_status, clv_payout_delta, brier_delta
        FROM grading.belief_grades WHERE belief_id = %s""", (bid,))

    assert status == "OBSERVED" and float(delta) != 0.0, (status, delta)
    _assert_zero_divergence(admin, bid)        # identity survives untouched
    assert float(bd) == 0.0, "a non-zero CLV moved the model-vs-market score"
    admin.close()
    return f"CLV observed at {float(delta):+.4f}; formation identity still exact"


def t37_a_perturbed_null_must_fail_the_identity():
    """Proves the proof. 'Null' means EXACT market reproduction, not close
    enough -- so a producer offset by a single thousandth must be rejected by
    the same assertion that accepts the real one."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)

    real = _null(admin, ev)
    _assert_zero_divergence(admin, real)       # the genuine article passes

    # the same market, one thousandth off
    market_p = float(h.scalar(admin, """
        SELECT consensus_probability FROM model_input.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,)))
    for bk in ("bookA", "bookB"):               # move it so a second belief is allowed
        two_sided(admin, ev, "MONEYLINE", None, -152, 132, bk)
    market_p2 = float(h.scalar(admin, """
        SELECT consensus_probability FROM model_input.market_intelligence
        WHERE event_id=%s AND market_type='MONEYLINE' AND selection='DAL'""", (ev,)))
    perturbed = _form(admin, ev, prob=round(market_p2 + 0.001, 6),
                      model_id="null", model_version="1.0.0")

    try:
        _assert_zero_divergence(admin, perturbed)
    except AssertionError:
        admin.close()
        return (f"exact reproduction accepted (p={market_p}); +0.001 offset "
                "rejected by the same assertion")
    raise AssertionError(
        "a producer offset by 0.001 satisfied the zero-divergence proof -- "
        "'null' has been allowed to mean 'close enough'")


def t38_no_special_case_exists_for_the_null_model():
    """Structural. If any grading or calibration routine names the null model,
    every result it produces about that model is suspect."""
    admin = h.connect(); h.reset(admin)

    defs = h.rows(admin, """
        SELECT n.nspname || '.' || p.proname, pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname IN ('grading', 'model')
          AND p.proname NOT IN ('null_model_belief')""")
    assert defs, "no functions found -- this test would pass vacuously"
    for name, src in defs:
        low = src.lower()
        for needle in ("null_model", "'null'", "passthrough"):
            assert needle not in low, (
                f"{name} references {needle!r} -- the null model has a "
                "special case in the measurement system")

    # and nothing in the schema is shaped around it
    cols = h.rows(admin, """
        SELECT table_schema||'.'||table_name||'.'||column_name
        FROM information_schema.columns
        WHERE table_schema IN ('model','grading')
          AND (column_name ILIKE '%%null_model%%' OR column_name ILIKE '%%baseline%%'
            OR column_name ILIKE '%%reference_model%%')""")
    assert not cols, f"schema carries null-model-specific columns: {cols}"
    admin.close()
    return f"{len(defs)} grading/model routines checked, none names the null model"



# =============================================================================
# Increment 6 -- the eligibility ledger (the denominator)
# =============================================================================

def _attempt(conn, ev, sel="DAL", line=None, prob=0.62, mid="planted", ver="1"):
    return h.row(conn, """
        SELECT * FROM model.attempt_belief(%s, %s, 'f', %s::uuid, 'MONEYLINE',
                                           %s, %s::numeric, %s::numeric, 'h')""",
        (mid, ver, ev, sel, line, prob))


def _elig(admin, ev, sel="DAL", line=None):
    return h.scalar(admin, """
        SELECT model.eligibility(%s::uuid, 'MONEYLINE', %s, %s::numeric)""",
        (ev, sel, line))


def t39_every_attempt_is_recorded_with_a_reason():
    """The denominator. Without it a model can look well calibrated partly
    because the system quietly filtered out the hard cases."""
    admin = h.connect(); h.reset(admin)

    # ELIGIBLE -- two books, fresh, pre-kickoff
    ok = _seed_executable(admin, "T39-OK")
    # NO_EXECUTABLE_MARKET -- one book only, fails closed below the floor
    one = event(admin, "T39-ONE")
    two_sided(admin, one, "MONEYLINE", None, -150, 130, "bookA")
    # STALE -- quotes older than the TTL
    old = event(admin, "T39-STALE")
    for bk in ("bookA", "bookB"):
        two_sided(admin, old, "MONEYLINE", None, -150, 130, bk, age=400)
    # POST_KICKOFF
    late = event(admin, "T39-LATE", starts_in="4 hours")
    for bk in ("bookA", "bookB"):
        two_sided(admin, late, "MONEYLINE", None, -150, 130, bk)
    admin.execute("UPDATE public.events SET is_live = TRUE WHERE id = %s", (late,))
    # NO_MARKET_ROW -- a wager nobody quotes
    ghost = _seed_executable(admin, "T39-GHOST")

    cases = [(ok, "DAL", "ELIGIBLE"), (one, "DAL", "NO_EXECUTABLE_MARKET"),
             (old, "DAL", "STALE"), (late, "DAL", "POST_KICKOFF"),
             (ghost, "NOBODY", "NO_MARKET_ROW")]

    model = h.connect_as("olp_model")
    for ev, sel, expect in cases:
        bid, reason = _attempt(model, ev, sel)
        assert reason == expect, f"{expect}: got {reason}"
        assert (bid is not None) == (expect == "ELIGIBLE"), (expect, bid)
    model.close()

    rows = dict(h.rows(admin, """
        SELECT reason::text, count(*) FROM model.formation_attempts GROUP BY 1"""))
    assert rows == {"ELIGIBLE": 1, "NO_EXECUTABLE_MARKET": 1, "STALE": 1,
                    "POST_KICKOFF": 1, "NO_MARKET_ROW": 1}, rows

    # the rejected rows carry market context so exclusions can be characterised
    ctx = h.scalar(admin, """
        SELECT count(*) FROM model.formation_attempts
        WHERE reason = 'NO_EXECUTABLE_MARKET' AND market_quality IS NOT NULL""")
    assert ctx == 1, "a rejected attempt recorded no market context"
    admin.close()
    return f"5 reasons recorded: {sorted(rows)}"


def t40_the_model_cannot_bypass_the_ledger():
    """If the model could still reach form_belief directly the denominator would
    be incomplete by construction -- worse than having none, because it would
    still look like one."""
    admin = h.connect(); h.reset(admin)
    ev = _seed_executable(admin)
    model = h.connect_as("olp_model")
    try:
        with model.cursor() as cur:
            cur.execute("""SELECT model.form_belief('m','1','f',%s::uuid,
                'MONEYLINE','DAL',NULL,0.6::numeric,'h')""", (ev,))
    except psycopg.Error as exc:
        assert exc.sqlstate == INSUFFICIENT_PRIVILEGE, exc.sqlstate
    else:
        raise AssertionError("olp_model reached form_belief directly")

    bid, reason = _attempt(model, ev)        # the sanctioned path still works
    assert bid is not None and reason == "ELIGIBLE"
    model.close()
    assert h.scalar(admin, "SELECT count(*) FROM model.formation_attempts") == 1
    admin.close()
    return "form_belief refused (42501); attempt_belief works and logs"


def t41_the_two_eligibility_evaluations_agree():
    """`attempt_belief` decides from `model.eligibility`; `form_belief` keeps its
    own guards as defence in depth. Two evaluations that can drift are a
    liability, so their agreement is asserted rather than assumed."""
    admin = h.connect(); h.reset(admin)
    fixtures = []
    ok = _seed_executable(admin, "T41-OK");                     fixtures.append((ok, "DAL"))
    one = event(admin, "T41-ONE")
    two_sided(admin, one, "MONEYLINE", None, -150, 130, "bookA"); fixtures.append((one, "DAL"))
    old = event(admin, "T41-STALE")
    for bk in ("bookA", "bookB"):
        two_sided(admin, old, "MONEYLINE", None, -150, 130, bk, age=400)
    fixtures.append((old, "DAL"))
    ghost = _seed_executable(admin, "T41-GHOST");               fixtures.append((ghost, "NOBODY"))

    checked = 0
    for ev, sel in fixtures:
        reason = _elig(admin, ev, sel)
        raised = False
        try:
            _form(admin, ev, sel=sel, prob=0.6)
        except psycopg.Error:
            raised = True
        assert raised == (reason != "ELIGIBLE"), (
            f"eligibility says {reason} but form_belief "
            f"{'raised' if raised else 'accepted'} -- the two evaluations have drifted")
        checked += 1
    admin.close()
    return f"{checked} fixtures: form_belief raises exactly when eligibility is not ELIGIBLE"


def t42_the_evaluation_population_is_describable():
    """The point of the ledger: included and excluded can be compared, so a
    calibration result can be labelled honestly as describing the
    EXECUTION-ELIGIBLE population rather than 'the market'."""
    admin = h.connect(); h.reset(admin)
    for i in range(6):
        _attempt(admin, _seed_executable(admin, f"T42-OK{i}"))
    for i in range(4):
        one = event(admin, f"T42-ONE{i}")
        two_sided(admin, one, "MONEYLINE", None, -150, 130, "bookA")
        _attempt(admin, one)

    total, eligible = h.row(admin, """
        SELECT count(*), count(*) FILTER (WHERE reason='ELIGIBLE')
        FROM model.formation_attempts""")
    beliefs = h.scalar(admin, "SELECT count(*) FROM model.beliefs")
    assert (total, eligible, beliefs) == (10, 6, 6), (total, eligible, beliefs)

    # the ledger, not the belief table, is what a denominator is computed from
    rate = float(eligible) / float(total)
    assert abs(rate - 0.6) < 1e-9
    admin.close()
    return f"{eligible}/{total} attempts eligible ({rate:.0%}); exclusions retained"



# =============================================================================
# Increment 7 -- the v0.1 formation lifecycle
# =============================================================================

def _event_at(admin, src, kickoff_in, home="DAL", away="PHI"):
    return h.scalar(admin, "SELECT olp_test.create_event(%s,%s,%s,%s::interval)",
                    (src, home, away, kickoff_in))


def _sched(admin, **kw):
    return h.scalar(admin, "SELECT model.schedule_v01()")


def t43_the_v01_transform_is_the_pre_registered_one():
    """k = 1.10, and the two properties recorded before the data arrives:
    p = 0.5 is a fixed point, and k = 1 reproduces the market exactly."""
    admin = h.connect()
    # hand-computed from logit(p_v01) = 1.10 * logit(p)
    # Values computed independently in Python at 40-digit precision and
    # confirmed identical in PostgreSQL numeric. An earlier version of this test
    # carried constants transcribed from a one-decimal display rather than
    # derived, and was wrong in the sixth place.
    for p_, expect in ((0.50, 0.500000), (0.55, 0.554961), (0.60, 0.609691),
                       (0.70, 0.717486), (0.80, 0.821262), (0.95, 0.962272)):
        got = float(h.scalar(admin, "SELECT model.v01_probability(%s::numeric)", (p_,)))
        assert abs(got - expect) < 1e-6, (p_, got, expect)

    # 0.5 is a fixed point at any k -- pick'em markets contribute nothing
    for k in (1.0, 1.10, 2.0, 0.5):
        assert float(h.scalar(admin,
            "SELECT model.v01_probability(0.5::numeric, %s::numeric)", (k,))) == 0.5

    # k = 1 nests the null model exactly
    for p_ in (0.2, 0.437, 0.83):
        assert float(h.scalar(admin,
            "SELECT model.v01_probability(%s::numeric, 1::numeric)", (p_,))) == p_
    admin.close()
    return "k=1.10 matches hand values; 0.5 fixed at any k; k=1 is the identity"


def t44_the_schedule_creates_opportunities_before_the_model_runs():
    """The producer no longer decides when to attempt. The schedule decides an
    opportunity exists, one row per event x selection, once."""
    admin = h.connect(); h.reset(admin)
    inside  = _event_at(admin, "T44-IN",  "24 hours")
    early   = _event_at(admin, "T44-FAR", "40 hours")   # target still ahead
    late    = _event_at(admin, "T44-SOON", "2 hours")   # target long past

    n = _sched(admin)
    assert n == 2, f"expected 2 scheduled selections for one event, got {n}"

    rows = h.rows(admin, """
        SELECT event_id, selection_key, market_type::text, window_seconds
        FROM model.formation_schedule ORDER BY selection_key""")
    assert {r[0] for r in rows} == {inside}, "scheduled an event outside the window"
    assert [r[1] for r in rows] == ["DAL", "PHI"]
    assert all(r[2] == "MONEYLINE" and r[3] == 3600 for r in rows)

    assert _sched(admin) == 0, "scheduling twice created duplicate opportunities"
    admin.close()
    return f"1 event in window -> 2 opportunities; {early is not None and late is not None}"


def t45_every_scheduled_opportunity_terminates_exactly_once():
    """scheduled = formed + ineligible. No opportunity may vanish."""
    admin = h.connect(); h.reset(admin)
    ev = _event_at(admin, "T45", "24 hours")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    _sched(admin)

    for sid in [r[0] for r in h.rows(admin,
            "SELECT schedule_id FROM model.formation_schedule")]:
        h.scalar(admin, "SELECT model.resolve_v01(%s::uuid)", (sid,))
        h.expect_error(
            lambda sid=sid: h.scalar(admin, "SELECT model.resolve_v01(%s::uuid)", (sid,)),
            "ALREADY_RESOLVED", "resolving twice")

    sched, formed, ineligible, unresolved = h.row(admin, """
        SELECT count(*), count(*) FILTER (WHERE belief_formed),
               count(*) FILTER (WHERE NOT belief_formed AND NOT unresolved),
               count(*) FILTER (WHERE unresolved)
        FROM model.v01_ledger""")
    assert unresolved == 0, f"{unresolved} opportunities vanished"
    assert sched == formed + ineligible, (sched, formed, ineligible)
    assert formed == 2, formed
    admin.close()
    return f"{sched} scheduled = {formed} formed + {ineligible} ineligible; 0 unresolved"


def t46_the_model_is_invoked_only_after_eligibility():
    """The load-bearing inversion. The model must never see its probability and
    then decide whether the opportunity qualified."""
    admin = h.connect(); h.reset(admin)
    ok  = _event_at(admin, "T46-OK",  "24 hours")
    thin = _event_at(admin, "T46-THIN", "24 hours")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ok, "MONEYLINE", None, -150, 130, bk)
    two_sided(admin, thin, "MONEYLINE", None, -150, 130, "bookA")   # one book
    _sched(admin)
    for sid in [r[0] for r in h.rows(admin,
            "SELECT schedule_id FROM model.formation_schedule")]:
        h.scalar(admin, "SELECT model.resolve_v01(%s::uuid)", (sid,))

    # the ineligible ones terminated with a reason and produced NO belief
    reasons = dict(h.rows(admin, """
        SELECT reason::text, count(*) FROM model.formation_attempts GROUP BY 1"""))
    assert reasons.get("ELIGIBLE") == 2, reasons
    assert reasons.get("NO_EXECUTABLE_MARKET") == 2, reasons
    assert h.scalar(admin, "SELECT count(*) FROM model.beliefs") == 2

    # the formed beliefs carry the v0.1 transform, not the raw market
    for mkt, mdl in h.rows(admin, """
            SELECT market_probability_at_formation, model_probability
            FROM model.beliefs"""):
        expect = float(h.scalar(admin,
            "SELECT model.v01_probability(%s::numeric)", (mkt,)))
        assert abs(float(mdl) - expect) < 1e-9, (mkt, mdl, expect)
        assert float(mdl) != float(mkt), "v0.1 emitted the market unchanged"
    admin.close()
    return "eligible -> model invoked and sharpened; ineligible -> no model output at all"


def t47_a_missed_window_is_a_collection_failure_not_a_market_one():
    """NO_WINDOW_CAPTURE is kept distinct from every market reason. A market may
    have been perfectly executable and the ingestion system simply failed to look
    inside the window -- conflating those would corrupt any analysis of
    missingness."""
    admin = h.connect(); h.reset(admin)
    ev = _event_at(admin, "T47", "24 hours")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    _sched(admin)
    sid = h.scalar(admin, "SELECT schedule_id FROM model.formation_schedule LIMIT 1")

    # the market is fine; only the clock has moved outside the window
    assert h.scalar(admin, """
        SELECT model.eligibility(event_id,'MONEYLINE',selection_key,line)
        FROM model.formation_schedule WHERE schedule_id=%s""", (sid,)) == "ELIGIBLE"
    admin.execute("""
        INSERT INTO model.formation_schedule (model_id, model_version, event_id,
            market_type, selection_key, line, target_formation_at, window_seconds)
        SELECT 'v01','shifted',event_id,market_type,selection_key,line,
               NOW() - INTERVAL '3 hours', 3600
        FROM model.formation_schedule WHERE schedule_id=%s""", (sid,))
    missed = h.scalar(admin, """
        SELECT schedule_id FROM model.formation_schedule WHERE model_version='shifted'""")

    reason = h.scalar(admin, "SELECT model.resolve_v01(%s::uuid)", (missed,))
    assert reason == "NO_WINDOW_CAPTURE", reason
    row = h.row(admin, """
        SELECT belief_id, seconds_from_target, selected_observation_at
        FROM model.formation_attempts WHERE schedule_id=%s""", (missed,))
    assert row[0] is None, "a belief was formed outside the window"
    assert row[1] > 3600, f"seconds_from_target {row[1]} does not show the miss"
    assert row[2] is None
    admin.close()
    return f"executable market, missed window -> NO_WINDOW_CAPTURE at {row[1]}s from target"


def t48_both_clocks_are_recorded_and_can_disagree():
    """seconds_from_target and seconds_to_kickoff answer different questions. If
    a game is rescheduled they diverge, and only both together show whether the
    horizon actually held."""
    admin = h.connect(); h.reset(admin)
    ev = _event_at(admin, "T48", "24 hours")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    _sched(admin)
    sid = h.scalar(admin, "SELECT schedule_id FROM model.formation_schedule LIMIT 1")

    # the game is pushed back three hours AFTER the opportunity was scheduled
    admin.execute("""UPDATE public.events
                     SET current_scheduled_start = current_scheduled_start
                         + INTERVAL '3 hours' WHERE id = %s""", (ev,))
    h.scalar(admin, "SELECT model.resolve_v01(%s::uuid)", (sid,))

    frm, kick, target = h.row(admin, """
        SELECT seconds_from_target, seconds_to_kickoff, target_formation_at
        FROM model.formation_attempts WHERE schedule_id=%s""", (sid,))
    assert abs(frm) <= 3600, f"seconds_from_target {frm} is outside the window"
    assert kick > 24 * 3600 + 2 * 3600, (
        f"seconds_to_kickoff {kick} did not follow the reschedule")
    admin.close()
    return (f"from_target {frm}s (horizon held) vs to_kickoff {kick}s "
            "(game moved) -- the divergence is visible")


def t49_the_schedule_is_immutable(admin=None):
    """A schedule that can be edited after the fact is not a denominator."""
    admin = h.connect(); h.reset(admin)
    ev = _event_at(admin, "T49", "24 hours")
    _sched(admin)
    sid = h.scalar(admin, "SELECT schedule_id FROM model.formation_schedule LIMIT 1")
    h.expect_error(lambda: admin.execute(
        "UPDATE model.formation_schedule SET target_formation_at = NOW()"),
        "APPEND_ONLY_VIOLATION", "UPDATE a scheduled opportunity")
    h.expect_error(lambda: admin.execute(
        "DELETE FROM model.formation_schedule WHERE schedule_id=%s", (sid,)),
        "APPEND_ONLY_VIOLATION", "DELETE a scheduled opportunity")
    assert h.scalar(admin, "SELECT count(*) FROM model.formation_schedule") == 2
    admin.close()
    return "scheduled opportunities cannot be edited or removed"



# =============================================================================
# 057 -- the v0.1 experiment runner contract
# =============================================================================

def _run(admin, worker="runner-1"):
    return h.scalar(admin, "SELECT model.start_experiment_run(%s)", (worker,))


def _claim(conn, run_id, worker="runner-1", lease=600, limit=1000):
    return [r[0] for r in h.rows(conn, """
        SELECT model.claim_due_opportunities(%s::uuid, %s, %s::int, %s::int)""",
        (run_id, worker, lease, limit))]


def _due_market(admin, src, kickoff="24 hours"):
    """One event whose T-24h target is live now, with an executable board."""
    ev = _event_at(admin, src, kickoff)
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    return ev


def t50_overlapping_workers_receive_disjoint_claims():
    """Two cron invocations overlap. The database decides who owns an
    opportunity -- not process timing, and not an application-side lock."""
    admin = h.connect(); h.reset(admin)
    for i in range(4):
        _due_market(admin, f"T50-{i}", f"24 hours {i} minutes")
    assert _sched(admin) == 8

    a, b = h.connect(), h.connect()
    run_a, run_b = _run(a, "worker-A"), _run(b, "worker-B")
    got_a = _claim(a, run_a, "worker-A", limit=4)
    got_b = _claim(b, run_b, "worker-B")

    assert set(got_a) & set(got_b) == set(), (
        f"both workers claimed {set(got_a) & set(got_b)}")
    assert set(got_a) | set(got_b) == {r[0] for r in h.rows(admin,
        "SELECT schedule_id FROM model.formation_schedule")}, (
        "an opportunity was claimed by nobody")
    assert len(got_a) == 4 and len(got_b) == 4, (len(got_a), len(got_b))

    # claimed_count is the runner's own accounting of the same fact
    assert h.scalar(admin,
        "SELECT claimed_count FROM model.experiment_runs WHERE run_id=%s::uuid",
        (run_a,)) == 4
    a.close(); b.close(); admin.close()

    # -- the genuine race ----------------------------------------------------
    # Everything above is served by the "skip actively claimed" filter, which
    # only works because the first worker had already COMMITTED. The lease guard
    # on ON CONFLICT is what handles the case that filter cannot see: two
    # workers whose snapshots were both taken before either committed. Cron
    # invocations overlapping by a few milliseconds land exactly there, so it is
    # provoked deliberately rather than assumed unreachable.
    admin = h.connect(); h.reset(admin)
    _due_market(admin, "T50-RACE")
    _sched(admin)
    run_x, run_y = _run(admin, "X"), _run(admin, "Y")

    x = h.connect(autocommit=False)
    y = h.connect(autocommit=False)
    out = {}

    def claim_y():
        try:
            with y.cursor() as cur:
                cur.execute("""SELECT model.claim_due_opportunities(
                                   %s::uuid,'Y',600,1000)""", (run_y,))
                out["y"] = [r[0] for r in cur.fetchall()]
            y.commit()
        except Exception as exc:            # noqa: BLE001 -- reported, not hidden
            out["y_error"] = exc

    with x.cursor() as cur:
        cur.execute("SELECT model.claim_due_opportunities(%s::uuid,'X',600,1000)",
                    (run_x,))
        out["x"] = [r[0] for r in cur.fetchall()]

    t = threading.Thread(target=claim_y)
    t.start()
    time.sleep(0.4)          # Y is now blocked on the primary key X is holding
    x.commit()
    t.join(20)
    assert not t.is_alive(), "claim deadlocked"
    assert "y_error" not in out, out.get("y_error")

    assert len(out["x"]) == 2, out["x"]
    assert out["y"] == [], (
        f"the second worker stole a live lease: {out['y']} -- both cron "
        f"invocations would now fire an ingestion cycle for the same board")
    assert h.scalar(admin, """
        SELECT count(DISTINCT worker) FROM model.formation_claims""") == 1
    x.close(); y.close(); admin.close()
    return ("8 opportunities partitioned 4/4; and in a true snapshot race the "
            "live lease held (X 2, Y 0)")


def t51_one_poll_serves_the_whole_slate():
    """Sixteen games x two moneyline selections is ONE board refresh, not 32
    provider calls. Enforced by CHECK rather than by care."""
    admin = h.connect(); h.reset(admin)
    for i in range(16):
        _due_market(admin, f"T51-{i}", f"24 hours {i} minutes")
    assert _sched(admin) == 32

    run = _run(admin)
    claimed = _claim(admin, run)
    assert len(claimed) == 32, len(claimed)

    h.scalar(admin, "SELECT model.record_ingestion_poll(%s::uuid)", (run,))
    for sid in claimed:
        h.scalar(admin, "SELECT model.resolve_v01(%s::uuid, %s::uuid)", (sid, run))
    h.scalar(admin, "SELECT model.finish_experiment_run(%s::uuid)", (run,))

    polls, resolved, per_poll = h.row(admin, """
        SELECT ingestion_polls, resolved_count, opportunities_per_poll
        FROM model.runner_efficiency WHERE run_id = %s::uuid""", (run,))
    assert polls == 1, f"{polls} polls for one board refresh"
    assert resolved == 32, resolved
    assert float(per_poll) == 32.0, per_poll

    # a second poll inside the same cycle is refused, not merely discouraged
    h.expect_error(
        lambda: h.scalar(admin,
                         "SELECT model.record_ingestion_poll(%s::uuid)", (run,)),
        "ONE_POLL_PER_CYCLE", "polling twice in one cycle")

    assert h.scalar(admin, """
        SELECT count(*) FROM model.formation_attempts
        WHERE experiment_run_id = %s::uuid""", (run,)) == 32
    admin.close()
    return "32 opportunities resolved on 1 poll; second poll refused"


def t52_expired_leases_recover_crashed_work():
    """A worker that dies holding claims must not park them forever. The lease
    expires and the work returns to the pool -- with no reaper job to write,
    schedule, or forget to run."""
    admin = h.connect(); h.reset(admin)
    _due_market(admin, "T52")
    _sched(admin)

    dead = _run(admin, "worker-crashed")
    held = _claim(admin, dead, "worker-crashed", lease=600)
    assert len(held) == 2

    # while the lease is live, nobody else may take the work
    live = _run(admin, "worker-live")
    assert _claim(admin, live, "worker-live") == [], "stole a live lease"

    # expire it exactly as a crash would, by letting the clock pass it
    h.scalar(admin, """
        UPDATE model.formation_claims
           SET lease_expires_at = NOW() - INTERVAL '1 second' RETURNING 1""")

    rescuer = _run(admin, "worker-rescue")
    recovered = _claim(admin, rescuer, "worker-rescue")
    assert set(recovered) == set(held), (recovered, held)
    assert h.scalar(admin, """
        SELECT count(*) FROM model.formation_claims
        WHERE worker = 'worker-rescue'""") == 2

    for sid in recovered:
        assert h.scalar(admin, "SELECT model.resolve_v01(%s::uuid, %s::uuid)",
                        (sid, rescuer)) == "ELIGIBLE"
    assert h.scalar(admin,
        "SELECT count(*) FROM model.v01_ledger WHERE unresolved") == 0
    admin.close()
    return "live lease held; expired lease recovered by a second worker; 0 unresolved"


def t53_claims_are_not_what_protects_the_record():
    """The load-bearing guarantee. Even with the claim mechanism removed
    entirely, two overlapping workers cannot produce two beliefs or two
    denominator entries for one opportunity.

    NEGATIVE CONTROL: the claim table is wiped mid-flight so both workers
    believe they own the row. If duplicate protection lived in the lease rather
    than in the attempt uniqueness, this test would fail."""
    admin = h.connect(); h.reset(admin)
    _due_market(admin, "T53")
    _sched(admin)
    sid = h.scalar(admin, "SELECT schedule_id FROM model.formation_schedule LIMIT 1")

    a, b = h.connect(), h.connect()
    run_a, run_b = _run(a, "A"), _run(b, "B")
    _claim(a, run_a, "A")
    h.scalar(admin, "DELETE FROM model.formation_claims RETURNING 1")
    _claim(b, run_b, "B")

    assert h.scalar(a, "SELECT model.resolve_v01(%s::uuid, %s::uuid)",
                    (sid, run_a)) == "ELIGIBLE"
    h.expect_error(
        lambda: h.scalar(b, "SELECT model.resolve_v01(%s::uuid, %s::uuid)",
                         (sid, run_b)),
        "ALREADY_RESOLVED", "second worker resolving the same opportunity")

    assert h.scalar(admin, """
        SELECT count(*) FROM model.formation_attempts
        WHERE schedule_id=%s::uuid""", (sid,)) == 1, "duplicate denominator entry"
    assert h.scalar(admin, "SELECT count(*) FROM model.beliefs") == 1, "duplicate belief"
    a.close(); b.close(); admin.close()
    return "claims disabled mid-flight; still exactly 1 attempt and 1 belief"


def t54_resolution_is_terminal_and_spends_the_claim():
    """A resolved opportunity leaves the work list for good. If it did not, the
    runner would offer it again every five minutes until kickoff."""
    admin = h.connect(); h.reset(admin)
    _due_market(admin, "T54")
    _sched(admin)
    run = _run(admin)
    claimed = _claim(admin, run)
    assert h.scalar(admin, "SELECT count(*) FROM model.due_opportunities") == 2

    for sid in claimed:
        h.scalar(admin, "SELECT model.resolve_v01(%s::uuid, %s::uuid)", (sid, run))

    assert h.scalar(admin, "SELECT count(*) FROM model.due_opportunities") == 0, (
        "a resolved opportunity is still being offered as work")
    assert h.scalar(admin, "SELECT count(*) FROM model.formation_claims") == 0, (
        "claim outlived the opportunity it protected")
    assert _claim(admin, _run(admin, "later"), "later") == []
    admin.close()
    return "2 resolved -> 0 due, 0 claims, nothing re-offered"


def t55_attempts_are_attributable_and_still_immutable():
    """experiment_run_id is an INPUT to the attempt insert, not an annotation
    applied afterwards -- because formation_attempts is append-only and an
    after-the-fact stamp would be blocked. That is the correct failure, so it is
    asserted here rather than assumed."""
    admin = h.connect(); h.reset(admin)
    _due_market(admin, "T55")
    _sched(admin)
    run = _run(admin)
    sid = _claim(admin, run)[0]
    h.scalar(admin, "SELECT model.resolve_v01(%s::uuid, %s::uuid)", (sid, run))

    got = h.scalar(admin, """
        SELECT experiment_run_id FROM model.formation_attempts
        WHERE schedule_id = %s::uuid""", (sid,))
    assert str(got) == str(run), (got, run)

    # the append-only rule that forced that design still holds
    h.expect_error(
        lambda: h.scalar(admin, """
            UPDATE model.formation_attempts SET experiment_run_id = NULL
             WHERE schedule_id = %s::uuid RETURNING 1""", (sid,)),
        "APPEND_ONLY_VIOLATION", "re-stamping a resolved attempt")

    # the 056 signature still works, and reads as 'resolved outside a cycle'
    other = h.scalar(admin, "SELECT schedule_id FROM model.due_opportunities")
    h.scalar(admin, "SELECT model.resolve_v01(%s::uuid)", (other,))
    assert h.scalar(admin, """
        SELECT experiment_run_id FROM model.formation_attempts
        WHERE schedule_id = %s::uuid""", (other,)) is None
    admin.close()
    return "run id stamped at insert; UPDATE still refused; 1-arg form still valid"


def t56_a_missed_window_stays_on_the_work_list():
    """The denominator must not leak exactly the games the collector failed on.
    An opportunity whose window has closed stays visible as work and terminates
    as NO_WINDOW_CAPTURE."""
    admin = h.connect(); h.reset(admin)
    ev = _due_market(admin, "T56")

    # the scheduled capture never executed: its target passed three hours ago.
    # Inserted directly rather than by editing a scheduled row -- the schedule is
    # append-only and staying inside that rule is the point.
    sid = h.scalar(admin, """
        INSERT INTO model.formation_schedule
            (model_id, model_version, event_id, market_type, selection_key,
             line, target_formation_at, window_seconds)
        VALUES ('v01','0.1.0',%s::uuid,'MONEYLINE','DAL',NULL,
                NOW() - INTERVAL '3 hours', 3600)
        RETURNING schedule_id""", (ev,))

    due = h.rows(admin, """
        SELECT schedule_id, inside_window, seconds_from_target
        FROM model.due_opportunities""")
    assert len(due) == 1, "a missed opportunity vanished from the work list"
    assert due[0][1] is False, "a 3h-late capture reported itself inside the window"
    assert 10700 < due[0][2] < 10900, due[0][2]

    run = _run(admin)
    assert h.scalar(admin, "SELECT model.resolve_v01(%s::uuid, %s::uuid)",
                    (sid, run)) == "NO_WINDOW_CAPTURE"
    assert h.scalar(admin, """
        SELECT belief_id FROM model.formation_attempts
        WHERE schedule_id=%s::uuid""", (sid,)) is None, (
        "formed a belief outside the pre-registered window")
    admin.close()
    return "missed capture still offered as work; NO_WINDOW_CAPTURE, no belief"


def t57_the_runner_is_an_operator_not_the_model():
    """The work queue is operator-only. The producer cannot claim work, spend a
    provider credit, or terminate an opportunity -- and cannot even READ the
    pending queue, because advance sight of which opportunities are about to
    arrive is foreknowledge of the shape of its own denominator."""
    admin = h.connect(); h.reset(admin)
    _due_market(admin, "T57")
    _sched(admin)
    for obj in ("model.due_opportunities", "model.experiment_runs",
                "model.formation_claims", "model.runner_efficiency"):
        _assert_exists(admin, obj)
    sid = h.scalar(admin, "SELECT schedule_id FROM model.formation_schedule LIMIT 1")
    run = _run(admin)

    # The grader CAN read the queue. Without this the denial below would pass
    # just as happily against a view that is broken for everyone -- which is
    # exactly how the first draft of this migration shipped: olp_model was
    # granted the view but not the formation_claims it joins, so the "grant"
    # was an unusable read dressed up as an allowance.
    grader = h.connect_as("olp_grader")
    assert h.scalar(grader, "SELECT count(*) FROM model.due_opportunities") == 2
    assert h.scalar(grader, """
        SELECT count(*) FROM model.runner_efficiency WHERE run_id = %s::uuid""",
        (run,)) == 1
    grader.close()

    model = h.connect_as("olp_model")
    # ...and the producer still sees the denominator it is entitled to (056)
    assert h.scalar(model, "SELECT count(*) FROM model.v01_ledger") == 2

    for label, sql, params in (
        ("read queue", "SELECT count(*) FROM model.due_opportunities",      ()),
        ("read runs",  "SELECT count(*) FROM model.experiment_runs",        ()),
        ("claim",   "SELECT model.claim_due_opportunities(%s::uuid,'m')", (run,)),
        ("poll",    "SELECT model.record_ingestion_poll(%s::uuid)",       (run,)),
        ("resolve", "SELECT model.resolve_v01(%s::uuid, %s::uuid)",       (sid, run)),
        ("start",   "SELECT model.start_experiment_run('m')",             ()),
        ("write",   "INSERT INTO model.formation_claims(schedule_id,worker,"
                    "lease_expires_at) VALUES (%s::uuid,'m',NOW())",      (sid,)),
    ):
        try:
            with model.cursor() as cur:
                cur.execute(sql, params)
        except psycopg.Error as exc:
            assert exc.sqlstate == INSUFFICIENT_PRIVILEGE, (label, exc.sqlstate)
        else:
            raise AssertionError(f"olp_model performed runner action: {label}")
    model.close(); admin.close()
    return ("grader reads the queue; producer refused on all 7 runner actions "
            "(42501) while keeping its 056 ledger view")


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
    ("P5-T14", "Resolved belief graded from stored facts",
     t14_a_resolved_belief_is_graded_from_stored_facts),
    ("P5-T15", "Scoring rules match hand-computed values",
     t15_scoring_rules_match_hand_computed_values),
    ("P5-T22", "Grader scores forecasts, not bets",
     t22_the_grader_scores_forecasts_not_bets),
    ("P5-T17", "Baseline is the frozen formation probability",
     t17_the_baseline_is_the_frozen_formation_probability),
    ("P5-T18", "CLV is observed, never asserted",
     t18_clv_is_observed_never_asserted),
    ("P5-T28", "Grading cannot rewrite history",
     t28_grading_cannot_rewrite_history),
    ("P5-T29", "Unresolved wager cannot be graded",
     t29_an_unresolved_wager_cannot_be_graded),
    ("P5-T30", "Push and void excluded, not silently scored",
     t30_push_and_void_are_excluded_not_silently_scored),
    ("P5-T31", "Grading permissions point one way",
     t31_grading_permissions_point_one_way),
    ("P5-T16", "Equal-count bins and Wilson intervals",
     t16_bins_are_equal_count_and_wilson_matches_hand_values),
    ("P5-T23", "One bad bin fails a good weighted average",
     t23_a_single_bad_bin_fails_despite_a_good_weighted_average),
    ("P5-T21", "Calibrated and still adding nothing",
     t21_a_model_can_be_calibrated_and_still_add_nothing),
    ("P5-T32", "Win rate and probabilistic quality disagree",
     t32_win_rate_and_probabilistic_quality_can_disagree),
    ("P5-T33", "Shipped thresholds are the pre-registered ones",
     t33_the_shipped_thresholds_are_the_pre_registered_ones),
    ("P5-T34", "Null producer reproduces the market exactly",
     t34_the_null_producer_reproduces_the_market_exactly),
    ("P5-T35", "Null grades and calibrates through the ordinary path",
     t35_the_null_producer_grades_and_calibrates_through_the_ordinary_path),
    ("P5-T36", "Planted CLV does not disturb the null identity",
     t36_a_planted_clv_does_not_disturb_the_null_identity),
    ("P5-T37", "A perturbed null must fail the identity",
     t37_a_perturbed_null_must_fail_the_identity),
    ("P5-T38", "No special case exists for the null model",
     t38_no_special_case_exists_for_the_null_model),
    ("P5-T39", "Every attempt recorded with a reason",
     t39_every_attempt_is_recorded_with_a_reason),
    ("P5-T40", "Model cannot bypass the ledger",
     t40_the_model_cannot_bypass_the_ledger),
    ("P5-T41", "The two eligibility evaluations agree",
     t41_the_two_eligibility_evaluations_agree),
    ("P5-T42", "The evaluation population is describable",
     t42_the_evaluation_population_is_describable),
    ("P5-T43", "The v0.1 transform is the pre-registered one",
     t43_the_v01_transform_is_the_pre_registered_one),
    ("P5-T44", "Schedule creates opportunities before the model runs",
     t44_the_schedule_creates_opportunities_before_the_model_runs),
    ("P5-T45", "Every opportunity terminates exactly once",
     t45_every_scheduled_opportunity_terminates_exactly_once),
    ("P5-T46", "Model invoked only after eligibility",
     t46_the_model_is_invoked_only_after_eligibility),
    ("P5-T47", "Missed window is a collection failure",
     t47_a_missed_window_is_a_collection_failure_not_a_market_one),
    ("P5-T48", "Both clocks recorded and can disagree",
     t48_both_clocks_are_recorded_and_can_disagree),
    ("P5-T49", "The schedule is immutable",
     t49_the_schedule_is_immutable),
    ("P5-T50", "Overlapping workers receive disjoint claims",
     t50_overlapping_workers_receive_disjoint_claims),
    ("P5-T51", "One poll serves the whole slate",
     t51_one_poll_serves_the_whole_slate),
    ("P5-T52", "Expired leases recover crashed work",
     t52_expired_leases_recover_crashed_work),
    ("P5-T53", "Claims are not what protects the record",
     t53_claims_are_not_what_protects_the_record),
    ("P5-T54", "Resolution is terminal and spends the claim",
     t54_resolution_is_terminal_and_spends_the_claim),
    ("P5-T55", "Attempts are attributable and still immutable",
     t55_attempts_are_attributable_and_still_immutable),
    ("P5-T56", "A missed window stays on the work list",
     t56_a_missed_window_stays_on_the_work_list),
    ("P5-T57", "The runner is an operator, not the model",
     t57_the_runner_is_an_operator_not_the_model),
]
