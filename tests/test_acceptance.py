"""OLP-M1 acceptance tests (section 33 of the implementation package)."""

import uuid
import harness as h

GRADER = "TEST_GRADER"


# -- helpers ------------------------------------------------------------------

def new_user(admin, name):
    return h.scalar(admin, "SELECT olp_test.create_user(%s)", (name,))


def open_chapter(uid):
    with h.connect_as("authenticated", uid) as c:
        return h.scalar(c, "SELECT public.open_chapter_rpc()")


def place(uid, chapter, snap, risk, key=None):
    key = key or str(uuid.uuid4())
    with h.connect_as("authenticated", uid) as c:
        return h.scalar(
            c,
            "SELECT public.place_ticket_rpc(%s, %s, %s, %s)",
            (chapter, snap, risk, key),
        )


def settle(ticket, result, key=None, source=GRADER):
    with h.connect_as("service_role") as c:
        return h.scalar(
            c,
            "SELECT public.settle_ticket_rpc(%s, %s::public.ticket_result_type, %s, %s)",
            (ticket, result, source, key or str(uuid.uuid4())),
        )


def correct(ticket, new_result, key=None, reason="SCORE_CORRECTION",
            text="graded against a corrected box score", by="test-admin"):
    with h.connect_as("service_role") as c:
        return h.scalar(
            c,
            """SELECT public.apply_settlement_correction_rpc(
                   %s, %s::public.ticket_result_type, %s, %s, %s, %s)""",
            (ticket, new_result, reason, text, key or str(uuid.uuid4()), by),
        )


def seed(admin, name="alice"):
    """user_id, chapter_id, event_id, snapshot_id -- via the real RPCs."""
    return h.row(admin, "SELECT * FROM olp_test.seed_ready_user(%s)", (name,))


def fresh_snapshot(admin, event_id, line=-3.0, price=-110, selection="DAL",
                   book="TESTBOOK", market="SPREAD", captured=None, in_play=False):
    return h.scalar(
        admin,
        """SELECT olp_test.add_snapshot(
               %s::uuid, %s::public.market_type, %s::text, %s::numeric, %s::int,
               %s::text, COALESCE(%s::timestamptz, NOW()), %s::boolean)""",
        (event_id, market, selection, line, price, book, captured, in_play),
    )


# =============================================================================
# M1-T01 .. M1-T03  -- settlement arithmetic
# =============================================================================

def t01_standard_win():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    assert h.balances(admin, ch) == (10000, 1000, 9000), h.balances(admin, ch)

    settle(ticket, "WIN")

    # -110 risking 1000 returns 909.09 profit; the stake is never debited.
    assert h.balances(admin, ch) == (10909.09, 0, 10909.09), h.balances(admin, ch)

    result = h.row(admin,
        "SELECT result, pnl FROM public.ticket_results WHERE ticket_id = %s", (ticket,))
    assert result == ("WIN", 909.09), result

    tx = h.row(admin,
        """SELECT transaction_type, amount FROM public.wallet_transactions
           WHERE ticket_id = %s""", (ticket,))
    assert tx == ("SETTLEMENT_WIN", 909.09), tx
    admin.close()


def t02_standard_loss():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    settle(ticket, "LOSS")

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (9000, 0, 9000), (settled, escrow, avail)

    tx = h.row(admin,
        """SELECT transaction_type, amount FROM public.wallet_transactions
           WHERE ticket_id = %s""", (ticket,))
    assert tx == ("SETTLEMENT_LOSS", -1000), tx
    admin.close()


def t03_push_writes_zero_transaction():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    settle(ticket, "PUSH")

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (10000, 0, 10000), (settled, escrow, avail)

    tx = h.row(admin,
        """SELECT transaction_type, amount FROM public.wallet_transactions
           WHERE ticket_id = %s""", (ticket,))
    assert tx == ("SETTLEMENT_PUSH", 0), tx

    n = h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id = %s", (ticket,))
    assert n == 1, f"expected exactly one settlement row, got {n}"
    admin.close()


# =============================================================================
# M1-T05  -- Sunday slate, static 1,000-unit sizing
# =============================================================================

def t05_sunday_slate_static_sizing():
    admin = h.connect()
    h.reset(admin)
    u, ch, _, _ = seed(admin)

    accepted = []
    for i in range(10):
        ev = h.scalar(admin, "SELECT olp_test.create_event(%s)", (f"SLATE-{i}",))
        snap = fresh_snapshot(admin, ev)
        accepted.append(place(u, ch, snap, 1000))

    assert len(accepted) == 10
    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (10000, 10000, 0), (settled, escrow, avail)

    # The 11th has no available capital left.
    ev = h.scalar(admin, "SELECT olp_test.create_event('SLATE-11')")
    snap = fresh_snapshot(admin, ev)
    h.expect_error(lambda: place(u, ch, snap, 1000),
                   "INSUFFICIENT_CAPITAL", "M1-T05 11th ticket")
    admin.close()


# =============================================================================
# M1-T06  -- stale snapshot
# =============================================================================

def t06_stale_snapshot_rejected():
    admin = h.connect()
    h.reset(admin)
    u, ch, _, _ = seed(admin)

    ev = h.scalar(admin, "SELECT olp_test.create_event('STALE-EVT')")
    ttl = h.scalar(admin, "SELECT snapshot_ttl_seconds FROM public.system_settings")
    stale = h.scalar(admin, "SELECT NOW() - make_interval(secs => %s)", (ttl + 60,))
    snap = fresh_snapshot(admin, ev, captured=stale)

    h.expect_error(lambda: place(u, ch, snap, 100),
                   "SNAPSHOT_STALE", "M1-T06")

    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 0
    admin.close()


# =============================================================================
# M1-T07  -- duplicate submission UUID
# =============================================================================

def t07_duplicate_submission_creates_one_ticket():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    key = str(uuid.uuid4())
    a = place(u, ch, snap, 1000, key=key)
    b = place(u, ch, snap, 1000, key=key)
    c = place(u, ch, snap, 1000, key=key)

    assert a == b == c, (a, b, c)
    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 1
    assert h.scalar(admin, "SELECT count(*) FROM public.risk_reservations") == 1

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (10000, 1000, 9000), (settled, escrow, avail)
    admin.close()


# =============================================================================
# M1-T08  -- post-kickoff
# =============================================================================

def t08_post_kickoff_rejected():
    admin = h.connect()
    h.reset(admin)
    u, ch, _, _ = seed(admin)

    # (a) scheduled start has passed
    ev = h.scalar(admin, "SELECT olp_test.create_event('KICKED-OFF')")
    snap = fresh_snapshot(admin, ev)
    with h.connect_as("service_role") as svc:
        svc.execute(
            "UPDATE public.events SET current_scheduled_start = NOW() - INTERVAL '5 minutes' WHERE id = %s",
            (ev,))
    h.expect_error(lambda: place(u, ch, snap, 100), "EVENT_STARTED", "M1-T08 past start")

    # (b) event flagged live
    ev2 = h.scalar(admin, "SELECT olp_test.create_event('LIVE-EVT')")
    snap2 = fresh_snapshot(admin, ev2)
    with h.connect_as("service_role") as svc:
        svc.execute("UPDATE public.events SET is_live = TRUE WHERE id = %s", (ev2,))
    h.expect_error(lambda: place(u, ch, snap2, 100), "EVENT_LIVE", "M1-T08 live")

    # (c) actual kickoff recorded
    ev3 = h.scalar(admin, "SELECT olp_test.create_event('STARTED-EVT')")
    snap3 = fresh_snapshot(admin, ev3)
    with h.connect_as("service_role") as svc:
        svc.execute("UPDATE public.events SET actual_start_time = NOW() WHERE id = %s", (ev3,))
    h.expect_error(lambda: place(u, ch, snap3, 100), "EVENT_STARTED", "M1-T08 actual start")

    # (d) event closed
    ev4 = h.scalar(admin, "SELECT olp_test.create_event('CLOSED-EVT')")
    snap4 = fresh_snapshot(admin, ev4)
    with h.connect_as("service_role") as svc:
        svc.execute("UPDATE public.events SET is_closed = TRUE WHERE id = %s", (ev4,))
    h.expect_error(lambda: place(u, ch, snap4, 100), "EVENT_CLOSED", "M1-T08 closed")

    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 0
    admin.close()


# =============================================================================
# M1-T10  -- correction can create DEFICIT without rewriting history
# =============================================================================

def _big_underdog_setup(admin, name="deficit_user"):
    """Chapter with one large winning ticket plus open exposure.

    A risks 1,000 at +5000 -> 50,000 profit. Once that WIN is corrected to a
    LOSS the chapter owes back 51,000, which is what drives it negative.
    """
    u = new_user(admin, name)
    ch = open_chapter(u)

    ev = h.scalar(admin, "SELECT olp_test.create_event('DOG-EVT')")
    snap = fresh_snapshot(admin, ev, line=None, price=5000, market="MONEYLINE")
    ticket_a = place(u, ch, snap, 1000)
    settle(ticket_a, "WIN")            # settled -> 60,000

    open_tickets = []
    for i in range(2):
        ev_i = h.scalar(admin, "SELECT olp_test.create_event(%s)", (f"DOG-OPEN-{i}",))
        snap_i = fresh_snapshot(admin, ev_i)
        open_tickets.append(place(u, ch, snap_i, 6000))   # 10% of 60,000

    return u, ch, ticket_a, open_tickets


def t10_correction_creates_deficit_without_rewriting_history():
    admin = h.connect()
    h.reset(admin)
    u, ch, ticket_a, _ = _big_underdog_setup(admin)

    before = h.row(admin,
        """SELECT id, result, pnl, settled_at FROM public.ticket_results
           WHERE ticket_id = %s""", (ticket_a,))
    assert before[1:3] == ("WIN", 50000), before

    correct(ticket_a, "LOSS", reason="GRADING_ERROR")

    # The ORIGINAL row is byte-for-byte untouched.
    after = h.row(admin,
        """SELECT id, result, pnl, settled_at FROM public.ticket_results
           WHERE ticket_id = %s""", (ticket_a,))
    assert after == before, f"original settlement was mutated: {before} -> {after}"

    # The correction is an appended fact.
    adj = h.row(admin,
        """SELECT previous_effective_result, new_effective_result, pnl_delta, reason_code
           FROM public.ticket_result_adjustments WHERE ticket_id = %s""", (ticket_a,))
    assert adj == ("WIN", "LOSS", -51000, "GRADING_ERROR"), adj

    eff = h.row(admin,
        """SELECT original_result, original_pnl, effective_result, effective_pnl, correction_count
           FROM public.ticket_effective_results WHERE ticket_id = %s""", (ticket_a,))
    assert eff == ("WIN", 50000, "LOSS", -1000, 1), eff

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (9000, 12000, -3000), (settled, escrow, avail)

    status = h.scalar(admin, "SELECT status FROM public.ledger_chapters WHERE id = %s", (ch,))
    assert status == "DEFICIT", status
    admin.close()


# =============================================================================
# M1-T11  -- 48-hour postponement void releases exposure
# =============================================================================

def t11_postponement_void_releases_exposure():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    assert h.balances(admin, ch)[1] == 1000

    history_before = h.scalar(admin,
        "SELECT count(*) FROM public.event_schedule_history WHERE event_id = %s", (ev,))

    with h.connect_as("service_role") as svc:
        svc.execute("SELECT set_config('olp.schedule_source', 'LEAGUE_FEED', false)")
        svc.execute("SELECT set_config('olp.schedule_reason', 'WEATHER_POSTPONEMENT', false)")
        svc.execute(
            """UPDATE public.events
               SET current_scheduled_start = current_scheduled_start + INTERVAL '48 hours'
               WHERE id = %s""", (ev,))

    history_after = h.scalar(admin,
        "SELECT count(*) FROM public.event_schedule_history WHERE event_id = %s", (ev,))
    assert history_after == history_before + 1, (history_before, history_after)

    logged = h.row(admin,
        """SELECT source, reason FROM public.event_schedule_history
           WHERE event_id = %s ORDER BY history_seq DESC LIMIT 1""", (ev,))
    assert logged == ("LEAGUE_FEED", "WEATHER_POSTPONEMENT"), logged

    # original_scheduled_start is a historical fact and must not have moved.
    orig, curr = h.row(admin,
        """SELECT original_scheduled_start, current_scheduled_start
           FROM public.events WHERE id = %s""", (ev,))
    assert curr > orig, (orig, curr)

    settle(ticket, "VOID", source="POSTPONEMENT")

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (10000, 0, 10000), (settled, escrow, avail)

    res_status = h.scalar(admin,
        "SELECT status FROM public.risk_reservations WHERE ticket_id = %s", (ticket,))
    assert res_status == "VOIDED", res_status

    tkt_status = h.scalar(admin, "SELECT status FROM public.tickets WHERE id = %s", (ticket,))
    assert tkt_status == "VOIDED", tkt_status

    tx = h.row(admin,
        "SELECT transaction_type, amount FROM public.wallet_transactions WHERE ticket_id = %s",
        (ticket,))
    assert tx == ("SETTLEMENT_VOID", 0), tx
    admin.close()


# =============================================================================
# M1-T12  -- an in-play quote can never be the closing quote
# =============================================================================

def t12_in_play_cannot_become_closing():
    admin = h.connect()
    h.reset(admin)
    ev = h.scalar(admin, "SELECT olp_test.create_event('CLOSING-EVT')")
    in_play = fresh_snapshot(admin, ev, in_play=True)
    pregame = fresh_snapshot(admin, ev, price=-115)

    def flag_in_play():
        with h.connect_as("service_role") as svc:
            svc.execute(
                "UPDATE public.market_snapshots SET is_closing_snapshot = TRUE WHERE id = %s",
                (in_play,))
    h.expect_error(flag_in_play, "ck_closing_snapshot_not_in_play", "M1-T12 update")

    def insert_both():
        with h.connect_as("service_role") as svc:
            svc.execute(
                """INSERT INTO public.market_snapshots
                   (event_id, market_type, selection, line, price, sportsbook,
                    source_provider, captured_at, is_in_play, is_closing_snapshot)
                   VALUES (%s,'SPREAD','DAL',-3,-110,'TESTBOOK','X',NOW(),TRUE,TRUE)""",
                (ev,))
    h.expect_error(insert_both, "ck_closing_snapshot_not_in_play", "M1-T12 insert")

    # Positive control: a pre-game quote CAN be flagged closing, exactly once.
    with h.connect_as("service_role") as svc:
        svc.execute(
            "UPDATE public.market_snapshots SET is_closing_snapshot = TRUE WHERE id = %s",
            (pregame,))
    assert h.scalar(admin,
        "SELECT is_closing_snapshot FROM public.market_snapshots WHERE id = %s", (pregame,))

    other = fresh_snapshot(admin, ev, price=-120)

    def second_closing():
        with h.connect_as("service_role") as svc:
            svc.execute(
                "UPDATE public.market_snapshots SET is_closing_snapshot = TRUE WHERE id = %s",
                (other,))
    h.expect_error(second_closing, "uq_closing_snapshot", "M1-T12 uniqueness")
    admin.close()


# =============================================================================
# M1-T13 / M1-T23  -- fresh but superseded
# =============================================================================

def t13_fresh_but_superseded_rejected():
    admin = h.connect()
    h.reset(admin)
    u, ch, _, _ = seed(admin)

    ev = h.scalar(admin, "SELECT olp_test.create_event('SUPERSEDED-EVT')")
    base = h.scalar(admin, "SELECT NOW() - INTERVAL '10 seconds'")
    old = fresh_snapshot(admin, ev, price=-110, captured=base)
    new = fresh_snapshot(admin, ev, price=-125)

    ttl = h.scalar(admin, "SELECT snapshot_ttl_seconds FROM public.system_settings")
    age = h.scalar(admin,
        "SELECT EXTRACT(EPOCH FROM (NOW() - captured_at)) FROM public.market_snapshots WHERE id = %s",
        (old,))
    assert age < ttl, f"fixture invalid: superseded quote is already stale ({age}s)"

    h.expect_error(lambda: place(u, ch, old, 100), "MARKET_MOVED", "M1-T13")

    # The current quote is placeable.
    ticket = place(u, ch, new, 100)
    assert ticket is not None
    assert h.scalar(admin,
        "SELECT accepted_price FROM public.tickets WHERE id = %s", (ticket,)) == -125
    admin.close()


def t23_moved_line_rejected_despite_ttl_freshness():
    admin = h.connect()
    h.reset(admin)
    u, ch, _, _ = seed(admin)

    ev, s1, s2 = h.row(admin, "SELECT * FROM olp_test.seed_game_a()")

    ttl = h.scalar(admin, "SELECT snapshot_ttl_seconds FROM public.system_settings")
    ages = h.row(admin,
        """SELECT EXTRACT(EPOCH FROM (NOW() - captured_at))
           FROM public.market_snapshots WHERE id = %s""", (s1,))
    assert ages[0] < ttl, f"fixture invalid: DAL -3 is stale ({ages[0]}s)"

    lines = h.row(admin,
        """SELECT (SELECT line FROM public.market_snapshots WHERE id = %s),
                  (SELECT line FROM public.market_snapshots WHERE id = %s)""", (s1, s2))
    assert lines == (-3.00, -3.50), lines

    h.expect_error(lambda: place(u, ch, s1, 100), "MARKET_MOVED", "M1-T23")
    assert place(u, ch, s2, 100) is not None
    admin.close()


# =============================================================================
# M1-T15  -- conflicting ordinary settlement
# =============================================================================

def t15_conflicting_settlement_raises():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    settle(ticket, "WIN")

    h.expect_error(lambda: settle(ticket, "LOSS"), "SETTLEMENT_CONFLICT", "M1-T15")

    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_results WHERE ticket_id = %s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id = %s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT result FROM public.ticket_results WHERE ticket_id = %s", (ticket,)) == "WIN"
    admin.close()


# =============================================================================
# M1-T17  -- a second current chapter is impossible
# =============================================================================

def t17_second_current_chapter_rejected():
    admin = h.connect()
    h.reset(admin)
    u = new_user(admin, "solo")
    first = open_chapter(u)

    h.expect_error(lambda: open_chapter(u), "CHAPTER_ALREADY_OPEN", "M1-T17")

    assert h.scalar(admin,
        "SELECT count(*) FROM public.ledger_chapters WHERE user_id = %s", (u,)) == 1

    # The database index -- not just the RPC -- is what forbids it.
    def direct_insert():
        with h.connect() as owner:
            owner.execute(
                """INSERT INTO public.ledger_chapters (user_id, chapter_number, status)
                   VALUES (%s, 2, 'ACTIVE')""", (u,))
    h.expect_error(direct_insert, "uq_one_current_chapter_per_user", "M1-T17 index")
    admin.close()


# =============================================================================
# M1-T19 / M1-T20  -- neutral settlement retries
# =============================================================================

def _neutral_retry(result_name, expected_tx, label):
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    first = settle(ticket, result_name)
    second = settle(ticket, result_name)          # different idempotency key
    third = settle(ticket, result_name, key=None)

    assert first == second == third, (first, second, third)
    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_results WHERE ticket_id = %s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id = %s", (ticket,)) == 1

    tx = h.row(admin,
        "SELECT transaction_type, amount FROM public.wallet_transactions WHERE ticket_id = %s",
        (ticket,))
    assert tx == (expected_tx, 0), tx

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (10000, 0, 10000), (settled, escrow, avail)
    admin.close()


def t19_push_retry_remains_one_settlement():
    _neutral_retry("PUSH", "SETTLEMENT_PUSH", "M1-T19")


def t20_void_retry_remains_one_settlement():
    _neutral_retry("VOID", "SETTLEMENT_VOID", "M1-T20")


# =============================================================================
# M1-T21  -- odds 0 (and any sub-100 magnitude) rejected
# =============================================================================

def t21_zero_odds_rejected():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, _ = seed(admin)

    for bad in (0, 1, 99, -99, -1, 50, -50):
        def insert_bad(price=bad):
            with h.connect() as owner:
                owner.execute(
                    """INSERT INTO public.market_snapshots
                       (event_id, market_type, selection, line, price, sportsbook,
                        source_provider, captured_at)
                       VALUES (%s,'SPREAD','DAL',-3,%s,'TESTBOOK','X',NOW())""",
                    (ev, price))
        h.expect_error(insert_bad, "market_snapshots_price_check", f"M1-T21 price={bad}")

    # Tickets carry the same floor independently of snapshots.
    def bad_ticket_price():
        with h.connect() as owner:
            owner.execute(
                """INSERT INTO public.tickets
                   (user_id, chapter_id, event_id, market_snapshot_id, market_type,
                    selection, accepted_price, accepted_sportsbook, snapshot_captured_at,
                    risk, potential_profit, submission_idempotency_key,
                    accepted_event_start)
                   SELECT u.id, c.id, %s, %s, 'SPREAD', 'DAL', 0, 'TESTBOOK', NOW(),
                          100, 90, gen_random_uuid(), NOW() + INTERVAL '3 hours'
                   FROM public.users u JOIN public.ledger_chapters c ON c.user_id = u.id
                   LIMIT 1""",
                (ev, fresh_snapshot(admin, ev, book="TICKETCHK")))
    h.expect_error(bad_ticket_price, "tickets_accepted_price_check", "M1-T21 ticket price")

    # Valid extremes still work.
    for good in (-100, 100, -110, 5000):
        assert fresh_snapshot(admin, ev, price=good, book=f"BOOK{good}") is not None
    admin.close()


# =============================================================================
# M1-T26 / M1-T27  -- corrections
# =============================================================================

def t26_correction_retry_idempotent():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    settle(ticket, "WIN")

    key = str(uuid.uuid4())
    a = correct(ticket, "LOSS", key=key)
    b = correct(ticket, "LOSS", key=key)
    c = correct(ticket, "LOSS", key=key)

    assert a == b == c, (a, b, c)
    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_result_adjustments WHERE ticket_id = %s",
        (ticket,)) == 1
    assert h.scalar(admin,
        """SELECT count(*) FROM public.wallet_transactions
           WHERE ticket_id = %s AND transaction_type = 'SETTLEMENT_CORRECTION'""",
        (ticket,)) == 1

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (9000, 0, 9000), (settled, escrow, avail)
    admin.close()


def t27_multiple_corrections_derive_effective_result():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    ticket = place(u, ch, snap, 1000)
    settle(ticket, "WIN")                                # +909.09 -> 10,909.09
    correct(ticket, "LOSS", reason="GRADING_ERROR")      # delta -1909.09 -> 9,000
    correct(ticket, "PUSH", reason="LINE_VOIDED")        # delta +1000.00 -> 10,000

    eff = h.row(admin,
        """SELECT original_result, original_pnl, effective_result,
                  effective_pnl, correction_count
           FROM public.ticket_effective_results WHERE ticket_id = %s""", (ticket,))
    assert eff == ("WIN", 909.09, "PUSH", 0, 2), eff

    # Original settlement untouched.
    assert h.row(admin,
        "SELECT result, pnl FROM public.ticket_results WHERE ticket_id = %s",
        (ticket,)) == ("WIN", 909.09)

    # The adjustment chain is contiguous and ordered by adjustment_seq.
    chain = h.rows(admin,
        """SELECT previous_effective_result, new_effective_result, pnl_delta
           FROM public.ticket_result_adjustments
           WHERE ticket_id = %s ORDER BY adjustment_seq""", (ticket,))
    assert len(chain) == 2, chain
    assert chain[0] == ("WIN", "LOSS", -1909.09), chain[0]
    assert chain[1] == ("LOSS", "PUSH", 1000.00), chain[1]

    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (10000, 0, 10000), (settled, escrow, avail)

    # A no-op correction is refused rather than silently absorbed.
    h.expect_error(lambda: correct(ticket, "PUSH"), "CORRECTION_NO_CHANGE", "M1-T27 no-op")
    admin.close()


# =============================================================================
# M1-T31 / section 36  -- balance baseline
# =============================================================================

def t31_chapter_starts_at_exactly_10000():
    admin = h.connect()
    h.reset(admin)
    u = new_user(admin, "baseline")
    ch = open_chapter(u)

    starting = h.scalar(admin,
        "SELECT starting_capital FROM public.ledger_chapters WHERE id = %s", (ch,))
    assert starting == 10000, starting

    opens = h.row(admin,
        """SELECT count(*), COALESCE(SUM(amount),0) FROM public.wallet_transactions
           WHERE chapter_id = %s AND transaction_type = 'CHAPTER_OPEN'""", (ch,))
    assert opens == (1, 10000), opens

    settled, escrow, avail = h.balances(admin, ch)
    assert settled == 10000, settled
    assert settled != 20000, "double-counting bug: starting_capital was added to the sum"
    assert avail == 10000, avail

    # A second opening credit is structurally impossible.
    def second_open():
        with h.connect() as owner:
            owner.execute(
                """INSERT INTO public.wallet_transactions
                   (user_id, chapter_id, transaction_type, amount, idempotency_key)
                   VALUES (%s, %s, 'CHAPTER_OPEN', 10000, gen_random_uuid())""", (u, ch))
    h.expect_error(second_open, "uq_chapter_open_transaction", "M1-T31 double credit")
    admin.close()


# =============================================================================
# M1-T32 / section 37  -- deterministic ordering on equal timestamps
# =============================================================================

def t32_equal_timestamps_ordered_by_ingest_seq():
    admin = h.connect()
    h.reset(admin)
    u, ch, _, _ = seed(admin)

    ev = h.scalar(admin, "SELECT olp_test.create_event('TIE-EVT')")
    ts = h.scalar(admin, "SELECT NOW()")
    first = fresh_snapshot(admin, ev, price=-110, captured=ts)
    second = fresh_snapshot(admin, ev, price=-115, captured=ts)

    seqs = h.row(admin,
        """SELECT (SELECT ingest_seq FROM public.market_snapshots WHERE id = %s),
                  (SELECT ingest_seq FROM public.market_snapshots WHERE id = %s)""",
        (first, second))
    assert seqs[1] > seqs[0], seqs

    query = """
        SELECT id FROM public.market_snapshots
        WHERE event_id = %s AND market_type = 'SPREAD' AND selection = 'DAL'
          AND sportsbook = 'TESTBOOK' AND is_in_play = FALSE AND captured_at <= NOW()
        ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1
    """
    winners = {h.scalar(admin, query, (ev,)) for _ in range(25)}
    assert winners == {second}, f"non-deterministic ordering: {winners}"

    # And the RPC agrees: the lower ingest_seq is superseded.
    h.expect_error(lambda: place(u, ch, first, 100), "MARKET_MOVED", "M1-T32 rpc")
    assert place(u, ch, second, 100) is not None
    admin.close()


# =============================================================================
# M1-T33 / M1-T34 / section 38  -- bankruptcy
# =============================================================================

def t33_insolvent_deficit_chapter_can_bust():
    admin = h.connect()
    h.reset(admin)
    u, ch, ticket_a, open_tickets = _big_underdog_setup(admin, "bust_user")

    # Clear all exposure first: both open tickets lose.
    for t in open_tickets:
        settle(t, "LOSS")
    assert h.balances(admin, ch) == (48000, 0, 48000), h.balances(admin, ch)

    correct(ticket_a, "LOSS", reason="GRADING_ERROR")
    settled, escrow, avail = h.balances(admin, ch)
    assert (settled, escrow, avail) == (-3000, 0, -3000), (settled, escrow, avail)
    assert h.scalar(admin,
        "SELECT status FROM public.ledger_chapters WHERE id = %s", (ch,)) == "DEFICIT"

    original_results = h.rows(admin,
        "SELECT ticket_id, result, pnl FROM public.ticket_results ORDER BY ticket_id")

    with h.connect_as("authenticated", u) as c:
        busted = h.scalar(c, "SELECT public.declare_bankruptcy_rpc()")
    assert busted == ch

    st, closed, reason = h.row(admin,
        "SELECT status, closed_at, close_reason FROM public.ledger_chapters WHERE id = %s", (ch,))
    assert st == "BUST", st
    assert closed is not None
    assert reason == "DEFICIT_INSOLVENT", reason

    # Chapter N+1 opens cleanly at a full 10,000.
    ch2 = open_chapter(u)
    assert ch2 != ch
    assert h.scalar(admin,
        "SELECT chapter_number FROM public.ledger_chapters WHERE id = %s", (ch2,)) == 2
    assert h.balances(admin, ch2)[0] == 10000

    # Prior chapter history is untouched.
    assert h.rows(admin,
        "SELECT ticket_id, result, pnl FROM public.ticket_results ORDER BY ticket_id"
    ) == original_results
    assert h.balances(admin, ch) == (-3000, 0, -3000)
    admin.close()


def t34_active_chapter_under_min_wager_can_bust():
    admin = h.connect()
    h.reset(admin)
    u = new_user(admin, "grinder")
    ch = open_chapter(u)
    ev = h.scalar(admin, "SELECT olp_test.create_event('GRIND-EVT')")

    min_wager = h.scalar(admin, "SELECT min_viable_wager FROM public.system_settings")

    # Lose the maximum-sized ticket over and over until no viable wager remains.
    guard = 0
    while True:
        settled, escrow, avail = h.balances(admin, ch)
        if settled < min_wager:
            break
        guard += 1
        assert guard < 200, "bankroll decay did not converge"
        risk = h.scalar(admin,
            "SELECT max_ticket_size FROM public.chapter_balances WHERE chapter_id = %s", (ch,))
        snap = fresh_snapshot(admin, ev)
        t = place(u, ch, snap, risk)
        settle(t, "LOSS")

    settled, escrow, avail = h.balances(admin, ch)
    assert escrow == 0, escrow
    assert 0 <= avail < min_wager, (avail, min_wager)
    assert h.scalar(admin,
        "SELECT status FROM public.ledger_chapters WHERE id = %s", (ch,)) == "ACTIVE"

    with h.connect_as("authenticated", u) as c:
        h.scalar(c, "SELECT public.declare_bankruptcy_rpc()")

    st, closed, reason = h.row(admin,
        "SELECT status, closed_at, close_reason FROM public.ledger_chapters WHERE id = %s", (ch,))
    assert (st, reason) == ("BUST", "BANKROLL_DEPLETED"), (st, reason)
    assert closed is not None

    ch2 = open_chapter(u)
    assert h.balances(admin, ch2)[0] == 10000
    admin.close()


def t34b_bankruptcy_blocked_while_viable_or_exposed():
    """Guard rails around the bankruptcy preconditions."""
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin, "viable")

    def declare():
        with h.connect_as("authenticated", u) as c:
            return h.scalar(c, "SELECT public.declare_bankruptcy_rpc()")

    h.expect_error(declare, "CHAPTER_STILL_VIABLE", "bankruptcy while solvent")

    # Open exposure is checked first: a chapter with live tickets can never be
    # abandoned, regardless of how much capital is left.
    place(u, ch, snap, 1000)
    h.expect_error(declare, "OPEN_EXPOSURE", "bankruptcy with open exposure")

    assert h.scalar(admin,
        "SELECT status FROM public.ledger_chapters WHERE id = %s", (ch,)) == "ACTIVE"
    admin.close()


# =============================================================================
# M1-T35  -- cross-user chapter access
# =============================================================================

def t35_cannot_place_against_another_users_chapter():
    admin = h.connect()
    h.reset(admin)
    ua, cha, eva, snapa = seed(admin, "user_a")
    ub = new_user(admin, "user_b")
    chb = open_chapter(ub)

    h.expect_error(lambda: place(ub, cha, snapa, 1000),
                   "CHAPTER_NOT_AVAILABLE", "M1-T35")

    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 0
    assert h.scalar(admin, "SELECT count(*) FROM public.risk_reservations") == 0
    assert h.balances(admin, cha) == (10000, 0, 10000)
    assert h.balances(admin, chb) == (10000, 0, 10000)

    # User B also cannot even SEE user A's chapter through RLS.
    with h.connect_as("authenticated", ub) as c:
        visible = h.scalar(c,
            "SELECT count(*) FROM public.ledger_chapters WHERE id = %s", (cha,))
    assert visible == 0, "RLS leak: user B can read user A's chapter"
    admin.close()


ACCEPTANCE = [
    ("M1-T01", "Standard WIN", t01_standard_win),
    ("M1-T02", "Standard LOSS", t02_standard_loss),
    ("M1-T03", "PUSH writes zero transaction", t03_push_writes_zero_transaction),
    ("M1-T05", "Sunday slate static 1,000-unit sizing", t05_sunday_slate_static_sizing),
    ("M1-T06", "Stale snapshot rejected", t06_stale_snapshot_rejected),
    ("M1-T07", "Duplicate submission UUID creates one ticket", t07_duplicate_submission_creates_one_ticket),
    ("M1-T08", "Post-kickoff ticket rejected", t08_post_kickoff_rejected),
    ("M1-T10", "Correction creates DEFICIT without rewriting history", t10_correction_creates_deficit_without_rewriting_history),
    ("M1-T11", "48-hour postponement void releases exposure", t11_postponement_void_releases_exposure),
    ("M1-T12", "In-play quote cannot become closing quote", t12_in_play_cannot_become_closing),
    ("M1-T13", "Fresh-but-superseded snapshot rejected", t13_fresh_but_superseded_rejected),
    ("M1-T15", "Conflicting ordinary settlement raises conflict", t15_conflicting_settlement_raises),
    ("M1-T17", "Second current chapter rejected", t17_second_current_chapter_rejected),
    ("M1-T19", "PUSH retry remains one settlement", t19_push_retry_remains_one_settlement),
    ("M1-T20", "VOID retry remains one settlement", t20_void_retry_remains_one_settlement),
    ("M1-T21", "Odds 0 rejected", t21_zero_odds_rejected),
    ("M1-T23", "Moved line rejected despite TTL freshness", t23_moved_line_rejected_despite_ttl_freshness),
    ("M1-T26", "Correction retry idempotent", t26_correction_retry_idempotent),
    ("M1-T27", "Multiple corrections derive correct effective result", t27_multiple_corrections_derive_effective_result),
    ("M1-T31", "Chapter starts at exactly 10,000, never 20,000", t31_chapter_starts_at_exactly_10000),
    ("M1-T32", "Equal timestamp snapshots ordered by ingest_seq", t32_equal_timestamps_ordered_by_ingest_seq),
    ("M1-T33", "Insolvent DEFICIT chapter can become BUST", t33_insolvent_deficit_chapter_can_bust),
    ("M1-T34", "ACTIVE chapter under 100 LC with no exposure can BUST", t34_active_chapter_under_min_wager_can_bust),
    ("M1-T34b", "Bankruptcy blocked while viable or exposed", t34b_bankruptcy_blocked_while_viable_or_exposed),
    ("M1-T35", "User cannot place against another user's chapter", t35_cannot_place_against_another_users_chapter),
]
