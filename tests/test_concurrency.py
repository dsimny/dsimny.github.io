"""OLP-M1 concurrency tests (sections 33 + 34).

Every worker below opens its OWN TCP connection to the server and waits on a
barrier before firing. There is no for-loop or sequential-await faking anywhere
in this file -- the contention against the ledger_chapters row is real.
"""

import threading
import uuid
import harness as h
from test_acceptance import seed, place, settle, correct, new_user, open_chapter, fresh_snapshot


def run_concurrently(n, worker):
    """Fire `worker(i)` on n real threads, released together by a barrier.

    Returns a list of (ok, value_or_error) in worker order.
    """
    barrier = threading.Barrier(n)
    results = [None] * n

    def wrapped(i):
        try:
            conn = h.connect_as(*worker.connection_for(i))
        except Exception as exc:               # pragma: no cover
            results[i] = (False, f"connect failed: {exc}")
            return
        try:
            barrier.wait(timeout=30)
            results[i] = (True, worker(i, conn))
        except Exception as exc:
            results[i] = (False, str(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results


# =============================================================================
# M1-T04 -- 15 true concurrent placements cannot overdraw
# =============================================================================

def t04_concurrent_placements_cannot_overdraw():
    admin = h.connect()
    h.reset(admin)
    u, ch, _, _ = seed(admin, "slate_racer")

    # One event/snapshot per worker so nothing but capital is contended.
    snaps = []
    for i in range(15):
        ev = h.scalar(admin, "SELECT olp_test.create_event(%s)", (f"RACE-{i}",))
        snaps.append(fresh_snapshot(admin, ev))

    def worker(i, conn):
        return h.scalar(
            conn,
            "SELECT public.place_ticket_rpc(%s, %s, %s, %s)",
            (ch, snaps[i], 1000, str(uuid.uuid4())),
        )
    worker.connection_for = lambda i: ("authenticated", u)

    results = run_concurrently(15, worker)

    accepted = [v for ok, v in results if ok]
    rejected = [v for ok, v in results if not ok]

    settled, escrow, avail = h.balances(admin, ch)
    ticket_count = h.scalar(admin, "SELECT count(*) FROM public.tickets")
    active_res = h.scalar(admin,
        "SELECT count(*) FROM public.risk_reservations WHERE status = 'ACTIVE'")

    assert settled == 10000, settled
    assert len(accepted) <= 10, f"accepted {len(accepted)} of 15 (max 10)"
    assert ticket_count == len(accepted), (ticket_count, len(accepted))
    assert active_res == len(accepted), (active_res, len(accepted))
    assert escrow <= 10000, escrow
    assert avail >= 0, f"OVERDRAW: available capital went negative ({avail})"
    assert escrow == 1000 * len(accepted), (escrow, len(accepted))

    # With 15 x 1,000 against 10,000 the correct answer is exactly 10.
    assert len(accepted) == 10, f"expected exactly 10 accepted, got {len(accepted)}"
    assert len(rejected) == 5, f"expected exactly 5 rejected, got {len(rejected)}"
    for err in rejected:
        assert "INSUFFICIENT_CAPITAL" in err, err
    admin.close()
    return f"{len(accepted)} accepted / {len(rejected)} rejected, escrow {escrow}, available {avail}"


# =============================================================================
# M1-T09 -- concurrent grading creates one settlement
# =============================================================================

def t09_concurrent_grading_creates_one_settlement():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin, "grade_racer")
    ticket = place(u, ch, snap, 1000)

    def worker(i, conn):
        return h.scalar(
            conn,
            "SELECT public.settle_ticket_rpc(%s,'WIN'::public.ticket_result_type,%s,%s)",
            (ticket, f"GRADER-{i}", str(uuid.uuid4())),
        )
    worker.connection_for = lambda i: ("service_role", None)

    results = run_concurrently(8, worker)
    ok_values = {v for ok, v in results if ok}
    failures = [v for ok, v in results if not ok]

    assert not failures, f"unexpected failures: {failures}"
    assert len(ok_values) == 1, f"graders disagreed on the settlement id: {ok_values}"

    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_results WHERE ticket_id = %s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id = %s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.risk_reservations WHERE ticket_id = %s AND status='ACTIVE'"
        , (ticket,)) == 0

    assert h.balances(admin, ch) == (10909.09, 0, 10909.09), h.balances(admin, ch)
    admin.close()
    return "8 concurrent graders -> 1 settlement, 1 wallet transaction"


def t09b_concurrent_conflicting_grading():
    """Two graders disagree at the same instant: one wins, one raises conflict."""
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin, "conflict_racer")
    ticket = place(u, ch, snap, 1000)

    outcomes = ["WIN", "LOSS", "WIN", "LOSS", "PUSH", "VOID"]

    def worker(i, conn):
        return h.scalar(
            conn,
            "SELECT public.settle_ticket_rpc(%s,%s::public.ticket_result_type,%s,%s)",
            (ticket, outcomes[i], "GRADER", str(uuid.uuid4())),
        )
    worker.connection_for = lambda i: ("service_role", None)

    results = run_concurrently(len(outcomes), worker)
    failures = [v for ok, v in results if not ok]

    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_results WHERE ticket_id = %s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id = %s", (ticket,)) == 1
    assert failures, "expected at least one SETTLEMENT_CONFLICT"
    for err in failures:
        assert "SETTLEMENT_CONFLICT" in err, err
    admin.close()
    return f"{len(failures)} conflicts raised, 1 settlement stands"


# =============================================================================
# M1-T16 -- correction and placement serialize on the chapter
# =============================================================================

def t16_correction_placement_race_serializes():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin, "race_mixed")

    ticket_a = place(u, ch, snap, 1000)
    settle(ticket_a, "WIN")                                # settled -> 10,909.09
    assert h.balances(admin, ch) == (10909.09, 0, 10909.09)

    ev_b = h.scalar(admin, "SELECT olp_test.create_event('RACE-MIXED-B')")
    snap_b = fresh_snapshot(admin, ev_b)
    correction_key = str(uuid.uuid4())

    def worker(i, conn):
        if i == 0:
            return ("correction", h.scalar(
                conn,
                """SELECT public.apply_settlement_correction_rpc(
                       %s,'LOSS'::public.ticket_result_type,'GRADING_ERROR','x',%s,'admin')""",
                (ticket_a, correction_key)))
        return ("placement", h.scalar(
            conn,
            "SELECT public.place_ticket_rpc(%s,%s,%s,%s)",
            (ch, snap_b, 1000, str(uuid.uuid4()))))

    worker.connection_for = lambda i: ("service_role", None) if i == 0 else ("authenticated", u)

    results = run_concurrently(2, worker)
    outcomes = {}
    for ok, v in results:
        if ok:
            outcomes[v[0]] = ("ok", v[1])
        else:
            outcomes["failed"] = ("err", v)

    # The correction has no capital precondition, so it must always succeed.
    correction = next((v for ok, v in results if ok and v[0] == "correction"), None)
    assert correction is not None, f"correction must not fail: {results}"

    placement = next((v for ok, v in results if ok and v[0] == "placement"), None)
    placement_err = next((v for ok, v in results if not ok), None)

    # BOTH serialization orders are legitimate, and they give different answers:
    #
    #   placement first -> max ticket is 10% of 10,909.09 = 1,090.90, so a 1,000
    #                      ticket is accepted; the correction then lands.
    #   correction first -> settled drops to 9,000, max ticket becomes 900.00,
    #                      and the 1,000 ticket is correctly refused.
    #
    # What must hold either way is that the correction applied exactly once, no
    # update was lost, and available capital never went negative.
    settled, escrow, avail = h.balances(admin, ch)
    assert settled == 9000, f"lost update -- settled is {settled}, expected 9000"
    assert avail >= 0, f"available capital went negative ({avail})"

    if placement is not None:
        order = "placement won the race"
        assert placement_err is None, placement_err
        assert (escrow, avail) == (1000, 8000), (escrow, avail)
        assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 2
    else:
        order = "correction won the race"
        assert placement_err is not None and "TICKET_SIZE_LIMIT" in placement_err, placement_err
        assert (escrow, avail) == (0, 9000), (escrow, avail)
        assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 1

    # Applied exactly once, and the original settlement is untouched either way.
    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_result_adjustments WHERE ticket_id = %s",
        (ticket_a,)) == 1
    assert h.scalar(admin,
        """SELECT count(*) FROM public.wallet_transactions
           WHERE ticket_id = %s AND transaction_type = 'SETTLEMENT_CORRECTION'""",
        (ticket_a,)) == 1
    assert h.scalar(admin,
        "SELECT result FROM public.ticket_results WHERE ticket_id = %s", (ticket_a,)) == "WIN"
    admin.close()
    return f"{order}; settled 9000.00, escrow {escrow}, available {avail}"


# =============================================================================
# M1-T17 (concurrent) -- a second current chapter cannot be raced into existence
# =============================================================================

def t17_concurrent_chapter_open_creates_one():
    admin = h.connect()
    h.reset(admin)
    u = new_user(admin, "racer_open")

    def worker(i, conn):
        return h.scalar(conn, "SELECT public.open_chapter_rpc()")
    worker.connection_for = lambda i: ("authenticated", u)

    results = run_concurrently(10, worker)
    accepted = {v for ok, v in results if ok}
    failures = [v for ok, v in results if not ok]

    assert len(accepted) == 1, f"expected exactly one chapter, got {accepted}"
    assert len(failures) == 9, f"expected 9 rejections, got {len(failures)}"
    for err in failures:
        assert "CHAPTER_ALREADY_OPEN" in err, err

    assert h.scalar(admin,
        "SELECT count(*) FROM public.ledger_chapters WHERE user_id = %s", (u,)) == 1
    assert h.scalar(admin, "SELECT count(*) FROM public.wallet_transactions") == 1

    ch = accepted.pop()
    assert h.balances(admin, ch) == (10000, 0, 10000)
    admin.close()
    return "10 concurrent opens -> 1 chapter, 1 CHAPTER_OPEN transaction"


# =============================================================================
# Concurrent duplicate submissions of the SAME idempotency key
# =============================================================================

def t07_concurrent_duplicate_key_creates_one_ticket():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin, "dupe_racer")
    key = str(uuid.uuid4())

    def worker(i, conn):
        return h.scalar(
            conn, "SELECT public.place_ticket_rpc(%s,%s,%s,%s)", (ch, snap, 1000, key))
    worker.connection_for = lambda i: ("authenticated", u)

    results = run_concurrently(10, worker)
    failures = [v for ok, v in results if not ok]
    ids = {v for ok, v in results if ok}

    assert not failures, f"duplicate submissions should be idempotent, got: {failures}"
    assert len(ids) == 1, f"expected one ticket id, got {ids}"
    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 1
    assert h.scalar(admin, "SELECT count(*) FROM public.risk_reservations") == 1
    assert h.balances(admin, ch) == (10000, 1000, 9000)
    admin.close()
    return "10 concurrent identical submissions -> 1 ticket"


CONCURRENCY = [
    ("M1-T04", "15 true concurrent placements cannot overdraw", t04_concurrent_placements_cannot_overdraw),
    ("M1-T07c", "Concurrent duplicate submission key -> one ticket", t07_concurrent_duplicate_key_creates_one_ticket),
    ("M1-T09", "Concurrent grading creates one settlement", t09_concurrent_grading_creates_one_settlement),
    ("M1-T09b", "Concurrent conflicting grading raises conflict", t09b_concurrent_conflicting_grading),
    ("M1-T16", "Correction/placement race serializes on chapter", t16_correction_placement_race_serializes),
    ("M1-T17c", "Concurrent chapter opens create exactly one", t17_concurrent_chapter_open_creates_one),
]
