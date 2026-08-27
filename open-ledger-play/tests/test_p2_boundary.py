"""OLP-M1 Package #2 -- boundary and concurrency suite.

Edge conditions at the exact seams where a real, unreliable provider will push:
ticket-relative postponement, the refresh/TTL boundaries in seconds, the kickoff
race, duplicate and crossed messages, and double-invoked lifecycle calls.

These exist to find problems here rather than after a live feed is attached.
"""

import threading
import uuid

import harness as h
from test_acceptance import new_user, open_chapter, place
from test_package2 import as_json, quote, seed_slate, svc


# -- helpers ------------------------------------------------------------------

def spread_snapshot(admin, event_id, book="BOOK_A"):
    return h.scalar(admin,
        """SELECT id FROM public.market_snapshots
           WHERE event_id=%s AND sportsbook=%s AND market_type='SPREAD'
           ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1""", (event_id, book))


def reschedule(event_id, new_start, reason="FEED"):
    return as_json(svc(
        "SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED',%s)",
        (event_id, new_start, reason)))


def start_plus(admin, event_id, hours):
    return h.scalar(admin,
        "SELECT original_scheduled_start + make_interval(hours => %s) FROM public.events WHERE id=%s",
        (hours, event_id))


def ticket_status(admin, ticket):
    return h.scalar(admin, "SELECT status FROM public.tickets WHERE id=%s", (ticket,))


def run_concurrently(n, build_conn, body):
    """n real threads, own connections, released together by a barrier."""
    barrier = threading.Barrier(n)
    out = [None] * n

    def wrapped(i):
        conn = build_conn(i)
        try:
            barrier.wait(timeout=30)
            out[i] = (True, body(i, conn))
        except Exception as exc:
            out[i] = (False, str(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return out


# =============================================================================
# Ticket-relative postponement
# =============================================================================

def b01_ticket_before_first_slip_absorbs_total_displacement():
    """Placed before any slip -> every later hour counts against it."""
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "early_bird"); ch = open_chapter(u)
    ticket = place(u, ch, spread_snapshot(admin, ev), 500)

    baseline = h.scalar(admin,
        "SELECT accepted_event_start FROM public.tickets WHERE id=%s", (ticket,))
    original = h.scalar(admin,
        "SELECT original_scheduled_start FROM public.events WHERE id=%s", (ev,))
    assert baseline == original, "first ticket should be bound to the original schedule"

    first = reschedule(ev, start_plus(admin, ev, 25))
    assert first["tickets_voided"] == 0 and first["tickets_retained"] == 1, first
    assert ticket_status(admin, ticket) == "ACCEPTED"

    second = reschedule(ev, start_plus(admin, ev, 50))
    assert second["tickets_voided"] == 1, second
    assert ticket_status(admin, ticket) == "VOIDED"
    assert h.balances(admin, ch) == (10000, 0, 10000)
    admin.close()
    return "25h retained, cumulative 50h voided"


def b02_ticket_after_first_slip_ignores_earlier_displacement():
    """Placed after a slip -> the earlier hours are not charged to it."""
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "late_comer"); ch = open_chapter(u)

    reschedule(ev, start_plus(admin, ev, 25))
    quote(ev, price=-112)                       # fresh quote on the new schedule
    ticket = place(u, ch, spread_snapshot(admin, ev), 500)

    bound = h.scalar(admin,
        "SELECT accepted_event_start FROM public.tickets WHERE id=%s", (ticket,))
    assert bound == start_plus(admin, ev, 25), "must bind to the slipped schedule"

    # Total displacement from ORIGINAL is now 50h, but only 25h of it is this
    # ticket's. Under the old event-relative rule this would have voided.
    out = reschedule(ev, start_plus(admin, ev, 50))
    assert out["cumulative_hours"] >= 48, out
    assert out["tickets_voided"] == 0, "earlier displacement must not count"
    assert out["tickets_retained"] == 1, out
    assert ticket_status(admin, ticket) == "ACCEPTED"
    assert h.balances(admin, ch) == (10000, 500, 9500)
    admin.close()
    return "cumulative 50h, ticket-relative 25h -> retained"


def b03_mixed_cohorts_are_adjudicated_separately():
    """One reschedule voids the old cohort and retains the new one."""
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u_a = new_user(admin, "cohort_a"); ch_a = open_chapter(u_a)
    u_b = new_user(admin, "cohort_b"); ch_b = open_chapter(u_b)

    ticket_a = place(u_a, ch_a, spread_snapshot(admin, ev), 500)   # bound to original

    reschedule(ev, start_plus(admin, ev, 25))
    quote(ev, price=-113)
    ticket_b = place(u_b, ch_b, spread_snapshot(admin, ev), 500)   # bound to +25h

    out = reschedule(ev, start_plus(admin, ev, 50))
    assert out["tickets_voided"] == 1 and out["tickets_retained"] == 1, out
    assert ticket_status(admin, ticket_a) == "VOIDED"   # displaced 50h
    assert ticket_status(admin, ticket_b) == "ACCEPTED" # displaced 25h
    assert h.balances(admin, ch_a) == (10000, 0, 10000)
    assert h.balances(admin, ch_b) == (10000, 500, 9500)

    # A third slip finally catches the later cohort too.
    out2 = reschedule(ev, start_plus(admin, ev, 80))
    assert out2["tickets_voided"] == 1, out2
    assert ticket_status(admin, ticket_b) == "VOIDED"
    assert h.balances(admin, ch_b) == (10000, 0, 10000)
    admin.close()
    return "A voided at 50h, B retained then voided at 80h"


def b04_postponement_racing_placement_is_deterministic():
    """A placement in flight during a postponement cannot survive wrongly.

    Only two outcomes are legitimate:
      placement first  -> it holds the OLD schedule and the sweep voids it
      reschedule first -> it binds to the NEW schedule, displacement 0, retained
    The illegal outcome is an ACCEPTED ticket still bound to the old schedule.

    COVERAGE NOTE: released from a barrier, the reschedule wins essentially every
    time -- placement does an idempotency lookup and a snapshot read before it
    reaches the event lock, so it arrives second. Observed 12/12 as
    "reschedule won" on PostgreSQL 17.6. The converse ordering is covered
    deterministically by B01 and B12, which place first and then postpone. This
    test's value is that the ILLEGAL outcome is unreachable either way.
    """
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "racer"); ch = open_chapter(u)
    snap = spread_snapshot(admin, ev)
    new_start = start_plus(admin, ev, 49)
    original = h.scalar(admin,
        "SELECT original_scheduled_start FROM public.events WHERE id=%s", (ev,))

    def build(i):
        return h.connect_as("service_role") if i == 0 else h.connect_as("authenticated", u)

    def body(i, conn):
        if i == 0:
            return ("reschedule", h.scalar(
                conn, "SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED','RACE')",
                (ev, new_start)))
        return ("placement", h.scalar(
            conn, "SELECT public.place_ticket_rpc(%s,%s,%s,%s)",
            (ch, snap, 500, str(uuid.uuid4()))))

    results = run_concurrently(2, build, body)
    resched_ok = any(ok and v[0] == "reschedule" for ok, v in results)
    assert resched_ok, f"reschedule must not fail: {results}"

    rows = h.rows(admin,
        "SELECT id, status, accepted_event_start FROM public.tickets WHERE event_id=%s", (ev,))

    settled, escrow, avail = h.balances(admin, ch)
    assert avail >= 0 and settled == 10000, (settled, escrow, avail)

    if not rows:
        outcome = "placement rejected outright"
        assert escrow == 0, escrow
    else:
        assert len(rows) == 1, rows
        _, status, bound = rows[0]
        if bound == original:
            # Placed against the abandoned schedule: it must have been swept.
            assert status == "VOIDED", "ticket bound to the old schedule survived a 49h postponement"
            assert escrow == 0, escrow
            outcome = "placement won, then correctly voided"
        else:
            assert bound == new_start, bound
            assert status == "ACCEPTED", status
            assert escrow == 500, escrow
            outcome = "reschedule won, ticket bound to the new schedule"

    admin.close()
    return outcome


# =============================================================================
# Refresh and TTL boundaries, in seconds
# =============================================================================

def b05_refresh_boundary_59_60_61():
    """Unchanged quote: skipped below the refresh window, appended at or above."""
    admin = h.connect(); h.reset(admin)
    refresh = h.scalar(admin, "SELECT snapshot_refresh_seconds FROM public.system_settings")
    assert refresh == 60, refresh

    observed = {}
    for age in (59, 60, 61):
        start = h.scalar(admin, "SELECT NOW() + INTERVAL '3 hours'")
        ev = as_json(svc(
            """SELECT public.ingest_event_rpc(%s,'HOME','AWAY',%s::timestamptz,
                                              'NFL','NFL','TEST')""",
            (f"REFRESH-{age}", start)))["event_id"]

        aged = h.scalar(admin, "SELECT NOW() - make_interval(secs => %s)", (age,))
        assert quote(ev, selection="HOME", price=-110, captured=aged) is not None

        # Same price again, now.
        result = quote(ev, selection="HOME", price=-110)
        observed[age] = "APPENDED" if result is not None else "SKIPPED"

    assert observed == {59: "SKIPPED", 60: "APPENDED", 61: "APPENDED"}, observed
    admin.close()
    return "59 skipped, 60/61 appended"


def b06_executable_ttl_boundary_119_120_121():
    """Placement TTL is inclusive: age <= TTL is executable, age > TTL is not.

    Exactly-120 cannot be asserted end to end -- wall-clock advances by a few
    milliseconds between stamping captured_at and the RPC evaluating it, which
    pushes a nominal 120 to 120.00x and over the line. The boundary is therefore
    pinned from both sides: 119 executable, 121 stale, plus a sub-second probe
    just inside 120 to prove the comparison is inclusive rather than strict.
    """
    admin = h.connect(); h.reset(admin)
    ttl = h.scalar(admin, "SELECT snapshot_ttl_seconds FROM public.system_settings")
    assert ttl == 120, ttl

    u = new_user(admin, "ttl_probe"); ch = open_chapter(u)
    outcomes = {}

    for label, secs in (("119", 119.0), ("just_inside_120", 119.75), ("121", 121.0)):
        start = h.scalar(admin, "SELECT NOW() + INTERVAL '3 hours'")
        ev = as_json(svc(
            """SELECT public.ingest_event_rpc(%s,'HOME','AWAY',%s::timestamptz,
                                              'NFL','NFL','TEST')""",
            (f"TTL-{label}", start)))["event_id"]

        aged = h.scalar(admin, "SELECT NOW() - make_interval(secs => %s)", (secs,))
        snap = quote(ev, selection="HOME", price=-110, captured=aged)
        assert snap is not None

        try:
            place(u, ch, snap, 100)
            outcomes[label] = "EXECUTABLE"
        except Exception as exc:
            assert "SNAPSHOT_STALE" in str(exc), exc
            outcomes[label] = "STALE"

    assert outcomes == {
        "119": "EXECUTABLE",
        "just_inside_120": "EXECUTABLE",
        "121": "STALE",
    }, outcomes
    admin.close()
    return "119 and 119.75 executable, 121 stale"


# =============================================================================
# Kickoff race
# =============================================================================

def b07_kickoff_and_quote_race_cannot_create_executable_price():
    """No quote may end up executable, or be the closing line, after kickoff."""
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "kickoff_racer"); ch = open_chapter(u)

    def build(i):
        return h.connect_as("service_role")

    def body(i, conn):
        if i == 0:
            return ("kickoff", h.scalar(
                conn, "SELECT public.mark_event_live_rpc(%s, NULL, 'RACE')", (ev,)))
        return ("quote", h.scalar(
            conn,
            """SELECT public.ingest_market_snapshot_rpc(
                   %s::uuid,'SPREAD','HOME1',-3.0,-133,'BOOK_A','RACE',NOW(),false)""",
            (ev,)))

    results = run_concurrently(2, build, body)
    kickoff_ok = any(ok and v[0] == "kickoff" for ok, v in results)
    assert kickoff_ok, f"kickoff must not fail: {results}"

    actual_start = h.scalar(admin,
        "SELECT actual_start_time FROM public.events WHERE id=%s", (ev,))
    assert actual_start is not None

    # No closing snapshot may post-date kickoff, and none may be in-play.
    bad_closing = h.scalar(admin,
        """SELECT count(*) FROM public.market_snapshots
           WHERE event_id=%s AND is_closing_snapshot
             AND (captured_at > %s OR is_in_play)""", (ev, actual_start))
    assert bad_closing == 0, "a closing line was taken at or after kickoff"

    # No pre-game quote may exist at or after kickoff at all.
    late_pregame = h.scalar(admin,
        """SELECT count(*) FROM public.market_snapshots
           WHERE event_id=%s AND is_in_play = FALSE AND captured_at >= %s""",
        (ev, actual_start))
    assert late_pregame == 0, "a pre-game quote is dated at or after kickoff"

    # Nothing on this event is offered or placeable any more.
    assert h.scalar(admin,
        "SELECT count(*) FROM public.current_market_board WHERE event_id=%s AND is_placeable",
        (ev,)) == 0

    any_snap = spread_snapshot(admin, ev)
    h.expect_error(lambda: place(u, ch, any_snap, 100), "EVENT_", "B07 placement after kickoff")
    admin.close()
    return "closing line <= kickoff, nothing executable afterwards"


def b08_post_kickoff_pregame_quote_is_refused():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))

    h.expect_error(lambda: quote(ev, price=-140),
                   "POST_KICKOFF_PREGAME_QUOTE", "B08 pre-game after kickoff")

    # In-play pricing is still legitimate after kickoff.
    assert quote(ev, price=-400, in_play=True) is not None

    # ...and can never become the closing line.
    assert h.scalar(admin,
        """SELECT count(*) FROM public.market_snapshots
           WHERE event_id=%s AND is_in_play AND is_closing_snapshot""", (ev,)) == 0
    admin.close()


# =============================================================================
# Duplicate and crossed provider messages
# =============================================================================

def b09_duplicate_and_crossed_messages_are_idempotent():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    before = h.scalar(admin, "SELECT count(*) FROM public.market_snapshots")

    # Must be at least as new as the quote seed_slate already wrote, or this row
    # never becomes the current one and de-duplication has nothing to match.
    ts = h.scalar(admin, "SELECT NOW()")
    rows = [{
        "event_id": str(ev), "market_type": "SPREAD", "selection": "HOME1",
        "line": "-3.0", "price": -117, "sportsbook": "BOOK_A",
        "source_provider": "TEST", "captured_at": ts.isoformat(), "is_in_play": False,
    }]
    import json as _json

    first = as_json(svc("SELECT public.ingest_market_snapshots_rpc(%s::jsonb, NULL)",
                        (_json.dumps(rows),)))
    assert first["written"] == 1, first

    # The very same message delivered twice more.
    for _ in range(2):
        again = as_json(svc("SELECT public.ingest_market_snapshots_rpc(%s::jsonb, NULL)",
                            (_json.dumps(rows),)))
        assert again["written"] == 0 and again["skipped"] == 1, again

    assert h.scalar(admin, "SELECT count(*) FROM public.market_snapshots") == before + 1

    # Crossed delivery: an older message arriving after a newer one.
    newer = quote(ev, price=-121)
    assert newer is not None
    crossed = [dict(rows[0], price=-119,
                    captured_at=h.scalar(admin, "SELECT NOW() - INTERVAL '20 seconds'").isoformat())]
    out = as_json(svc("SELECT public.ingest_market_snapshots_rpc(%s::jsonb, NULL)",
                      (_json.dumps(crossed),)))
    assert out["failed"] == 0, out

    current = h.scalar(admin,
        """SELECT id FROM public.market_snapshots
           WHERE event_id=%s AND market_type='SPREAD' AND selection='HOME1'
             AND sportsbook='BOOK_A'
           ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1""", (ev,))
    assert current == newer, "a crossed older message displaced the current quote"

    # Duplicate event messages stay idempotent too.
    start = h.scalar(admin, "SELECT current_scheduled_start FROM public.events WHERE id=%s", (ev,))
    for _ in range(3):
        r = as_json(svc(
            """SELECT public.ingest_event_rpc('NFL-SLATE-1','HOME1','AWAY1',
                                              %s::timestamptz,'NFL','NFL','TEST')""",
            (start,)))
        assert r["action"] == "UNCHANGED", r
    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 1
    admin.close()
    return "duplicates skipped, crossed message kept as history only"


def b10_late_historical_quote_never_replaces_current():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "history"); ch = open_chapter(u)

    current = quote(ev, price=-124)
    late = h.scalar(admin,
        "SELECT olp_test.append_backdated_quote(%s,'SPREAD','HOME1','BOOK_A', INTERVAL '45 seconds')", (ev,))

    assert h.scalar(admin,
        """SELECT id FROM public.market_snapshots
           WHERE event_id=%s AND market_type='SPREAD' AND selection='HOME1'
             AND sportsbook='BOOK_A'
           ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1""", (ev,)) == current

    assert h.scalar(admin,
        """SELECT snapshot_id FROM public.current_market_board
           WHERE event_id=%s AND market_type='SPREAD' AND sportsbook='BOOK_A'""",
        (ev,)) == current

    h.expect_error(lambda: place(u, ch, late, 100), "MARKET_MOVED", "B10 stale placement")
    assert place(u, ch, current, 100) is not None
    admin.close()


# =============================================================================
# Double-invoked lifecycle calls
# =============================================================================

def b11_double_cancel_cannot_double_release_escrow():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "double_cancel"); ch = open_chapter(u)
    ticket = place(u, ch, spread_snapshot(admin, ev), 750)

    first = as_json(svc("SELECT public.cancel_event_rpc(%s,'ABANDONED','TEST')", (ev,)))
    assert first["tickets_voided"] == 1, first
    after_first = h.balances(admin, ch)

    for _ in range(3):
        again = as_json(svc("SELECT public.cancel_event_rpc(%s,'ABANDONED','TEST')", (ev,)))
        assert again["action"] == "ALREADY_CLOSED" and again["tickets_voided"] == 0, again

    assert h.balances(admin, ch) == after_first == (10000, 0, 10000)
    assert h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id=%s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_results WHERE ticket_id=%s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.risk_reservations WHERE ticket_id=%s AND status='ACTIVE'",
        (ticket,)) == 0
    admin.close()
    return "escrow released exactly once across 4 cancels"


def b12_double_postpone_cannot_double_release_escrow():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "double_postpone"); ch = open_chapter(u)
    ticket = place(u, ch, spread_snapshot(admin, ev), 600)

    target = start_plus(admin, ev, 49)
    first = reschedule(ev, target)
    assert first["tickets_voided"] == 1, first

    # Same target again: a no-op, not a second void.
    repeat = reschedule(ev, target)
    assert repeat["action"] == "UNCHANGED" and repeat["tickets_voided"] == 0, repeat

    # A further slip finds nothing left to void.
    further = reschedule(ev, start_plus(admin, ev, 72))
    assert further["tickets_voided"] == 0, further

    assert h.balances(admin, ch) == (10000, 0, 10000)
    assert h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id=%s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_results WHERE ticket_id=%s", (ticket,)) == 1
    admin.close()
    return "one void across repeat and subsequent postponements"


def b13_concurrent_void_paths_settle_once():
    """Cancel and postpone racing on the same event must not double-settle."""
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "void_race"); ch = open_chapter(u)
    ticket = place(u, ch, spread_snapshot(admin, ev), 800)
    target = start_plus(admin, ev, 49)

    def build(i):
        return h.connect_as("service_role")

    def body(i, conn):
        if i == 0:
            return h.scalar(conn,
                "SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED','RACE')",
                (ev, target))
        return h.scalar(conn,
            "SELECT public.cancel_event_rpc(%s,'ABANDONED','RACE')", (ev,))

    run_concurrently(2, build, body)

    assert h.scalar(admin,
        "SELECT count(*) FROM public.ticket_results WHERE ticket_id=%s", (ticket,)) == 1
    assert h.scalar(admin,
        "SELECT count(*) FROM public.wallet_transactions WHERE ticket_id=%s", (ticket,)) == 1
    assert ticket_status(admin, ticket) == "VOIDED"
    assert h.balances(admin, ch) == (10000, 0, 10000)
    admin.close()
    return "one settlement, one wallet row"


def b14_staleness_fixture_refuses_the_recurring_mistake():
    """The regression rule, enforced rather than documented.

    Modelling a dead feed by inserting an older row is seductive and silently
    wrong: reads order by captured_at DESC so a back-dated row never becomes
    current, and snapshots are immutable so the fresh ones cannot be removed.
    This mistake was made twice (M2-T04, P3-T28). The fixture now refuses it.
    """
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    # A fresh observation exists, so this CANNOT make the current quote stale.
    def wrong():
        h.scalar(admin,
            """SELECT olp_test.make_current_quote_stale(
                   %s,'SPREAD','HOME1','BOOK_A', INTERVAL '5 minutes')""", (ev,))
    err = h.expect_error(wrong, "STALENESS_FIXTURE_MISUSE", "B14 guard")
    assert "captured_at DESC" in err and "seed_stale_market" in err, err
    assert "CANNOT be aged" in err, err

    # Back-dating is still available -- under a name that says what it does.
    late = h.scalar(admin,
        """SELECT olp_test.append_backdated_quote(
               %s,'SPREAD','HOME1','BOOK_A', INTERVAL '5 minutes')""", (ev,))
    assert late is not None
    assert h.scalar(admin,
        """SELECT count(*) FROM public.current_market_board
           WHERE event_id=%s AND is_placeable""", (ev,)) > 0,         "back-dating must NOT have made the market dark"

    # The honest construction: a market whose newest observation is old.
    dark_ev, dark_snap = h.row(admin,
        "SELECT * FROM olp_test.seed_stale_market('DARK-1', INTERVAL '5 minutes')")
    assert h.scalar(admin,
        """SELECT count(*) FROM public.current_market_board
           WHERE event_id=%s AND is_placeable""", (dark_ev,)) == 0,         "seed_stale_market should produce a dark market"

    u = new_user(admin, "guardrail"); ch = open_chapter(u)
    h.expect_error(lambda: place(u, ch, dark_snap, 100),
                   "SNAPSHOT_STALE", "B14 dark market unplaceable")

    # On a genuinely quiet market it asserts successfully and returns the
    # existing snapshot -- it never fabricates one.
    confirmed = h.scalar(admin,
        """SELECT olp_test.make_current_quote_stale(
               %s,'SPREAD','HOME_DARK_1','BOOK_A', INTERVAL '3 minutes')""", (dark_ev,))
    assert confirmed == dark_snap, (confirmed, dark_snap)
    assert h.scalar(admin,
        "SELECT count(*) FROM public.market_snapshots WHERE event_id=%s", (dark_ev,)) == 1,         "asserting staleness must not insert anything"

    # Asking for MORE staleness than the market actually has is refused too.
    def overreach():
        h.scalar(admin,
            """SELECT olp_test.make_current_quote_stale(
                   %s,'SPREAD','HOME_DARK_1','BOOK_A', INTERVAL '90 minutes')""", (dark_ev,))
    h.expect_error(overreach, "STALENESS_FIXTURE_MISUSE", "B14 overreach")
    admin.close()
    return "misuse refused, honest construction works"


BOUNDARY = [
    ("B01", "Ticket before first slip absorbs total displacement", b01_ticket_before_first_slip_absorbs_total_displacement),
    ("B02", "Ticket after first slip ignores earlier displacement", b02_ticket_after_first_slip_ignores_earlier_displacement),
    ("B03", "Mixed cohorts adjudicated separately", b03_mixed_cohorts_are_adjudicated_separately),
    ("B04", "Postponement racing placement is deterministic", b04_postponement_racing_placement_is_deterministic),
    ("B05", "Refresh boundary 59/60/61", b05_refresh_boundary_59_60_61),
    ("B06", "Executable TTL boundary 119/120/121", b06_executable_ttl_boundary_119_120_121),
    ("B07", "Kickoff/quote race cannot create executable price", b07_kickoff_and_quote_race_cannot_create_executable_price),
    ("B08", "Post-kickoff pre-game quote refused", b08_post_kickoff_pregame_quote_is_refused),
    ("B09", "Duplicate and crossed messages idempotent", b09_duplicate_and_crossed_messages_are_idempotent),
    ("B10", "Late historical quote never replaces current", b10_late_historical_quote_never_replaces_current),
    ("B11", "Double cancel cannot double-release escrow", b11_double_cancel_cannot_double_release_escrow),
    ("B12", "Double postpone cannot double-release escrow", b12_double_postpone_cannot_double_release_escrow),
    ("B13", "Concurrent void paths settle once", b13_concurrent_void_paths_settle_once),
    ("B14", "Staleness fixture refuses the recurring mistake", b14_staleness_fixture_refuses_the_recurring_mistake),
]
