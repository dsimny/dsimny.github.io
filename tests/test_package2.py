"""OLP-M1 Package #2 -- Market Ingestion & Event Lifecycle."""

import datetime as dt
import json
import sys
import pathlib
import uuid

import harness as h
from test_acceptance import new_user, open_chapter, place, seed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ingest import FixtureProvider, ScriptedProvider, EventRow, QuoteRow  # noqa: E402
from ingest import ingest_odds, ingest_schedule, poll_once  # noqa: E402

UTC = dt.timezone.utc


# -- helpers ------------------------------------------------------------------

def svc(sql, params=()):
    with h.connect_as("service_role") as c:
        return h.scalar(c, sql, params)


def as_json(v):
    return json.loads(v) if isinstance(v, str) else v


def seed_slate(admin, n=2, starts_in="3 hours"):
    return h.rows(admin,
                  "SELECT * FROM olp_test.seed_slate(%s, %s::interval)", (n, starts_in))


def quote(event_id, market="SPREAD", selection="HOME1", line=-3.0, price=-110,
          book="BOOK_A", captured=None, in_play=False, provider="TEST"):
    return svc(
        """SELECT public.ingest_market_snapshot_rpc(
               %s::uuid, %s::public.market_type, %s::text, %s::numeric, %s::int,
               %s::text, %s::text, %s::timestamptz, %s::boolean)""",
        (event_id, market, selection, line, price, book, provider, captured, in_play),
    )


def event_start(admin, event_id):
    return h.scalar(admin,
                    "SELECT current_scheduled_start FROM public.events WHERE id = %s",
                    (event_id,))


def original_start(admin, event_id):
    return h.scalar(admin,
                    "SELECT original_scheduled_start FROM public.events WHERE id = %s",
                    (event_id,))


def lifecycle(admin, event_id):
    return [r[0] for r in h.rows(
        admin,
        "SELECT action FROM public.event_lifecycle_log WHERE event_id=%s ORDER BY log_seq",
        (event_id,))]


# =============================================================================
# Ingestion
# =============================================================================

def t01_event_ingestion_is_idempotent():
    admin = h.connect(); h.reset(admin)
    start = h.scalar(admin, "SELECT NOW() + INTERVAL '3 hours'")

    first = as_json(svc(
        "SELECT public.ingest_event_rpc(%s,%s,%s,%s::timestamptz,'NFL','NFL','FEED')",
        ("E-1", "DAL", "PHI", start)))
    assert first["created"] is True and first["action"] == "INGESTED", first

    second = as_json(svc(
        "SELECT public.ingest_event_rpc(%s,%s,%s,%s::timestamptz,'NFL','NFL','FEED')",
        ("E-1", "DAL", "PHI", start)))
    assert second["created"] is False and second["action"] == "UNCHANGED", second
    assert second["event_id"] == first["event_id"]

    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 1
    # The initial schedule is a history fact; repeating the feed adds nothing.
    assert h.scalar(admin, "SELECT count(*) FROM public.event_schedule_history") == 1
    admin.close()


def t02_unchanged_quote_inside_refresh_window_is_skipped():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    before = h.scalar(admin, "SELECT count(*) FROM public.market_snapshots")
    assert quote(ev, price=-110) is None, "identical quote should be skipped"
    assert quote(ev, price=-110) is None
    after = h.scalar(admin, "SELECT count(*) FROM public.market_snapshots")
    assert after == before, (before, after)
    admin.close()


def t03_price_move_is_appended_and_history_preserved():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    moved = quote(ev, price=-118)
    assert moved is not None

    prices = [r[0] for r in h.rows(
        admin,
        """SELECT price FROM public.market_snapshots
           WHERE event_id=%s AND market_type='SPREAD' AND selection='HOME1'
             AND sportsbook='BOOK_A' ORDER BY ingest_seq""", (ev,))]
    assert prices == [-110, -118], prices   # the old quote is still there

    latest = h.scalar(admin,
        """SELECT price FROM public.market_snapshots
           WHERE event_id=%s AND market_type='SPREAD' AND selection='HOME1'
             AND sportsbook='BOOK_A'
           ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1""", (ev,))
    assert latest == -118
    admin.close()


def t04_unchanged_quote_is_refreshed_before_it_outlives_the_ttl():
    """The interaction that makes de-duplication safe.

    A market whose price has not moved must still be re-recorded before the
    newest quote ages past place_ticket_rpc's TTL -- otherwise a perfectly valid
    line becomes unplaceable simply because nobody moved it.
    """
    admin = h.connect(); h.reset(admin)
    refresh, ttl = h.row(admin,
        "SELECT snapshot_refresh_seconds, snapshot_ttl_seconds FROM public.system_settings")
    assert refresh < ttl, (refresh, ttl)

    start = h.scalar(admin, "SELECT NOW() + INTERVAL '3 hours'")
    ev = as_json(svc(
        "SELECT public.ingest_event_rpc('REFRESH','HOME','AWAY',%s::timestamptz,'NFL','NFL','TEST')",
        (start,)))["event_id"]

    # The only quote for this market is nearly TTL-old: still placeable, but
    # well past the refresh window.
    aged = h.scalar(admin, "SELECT NOW() - make_interval(secs => %s)", (ttl - 10,))
    first = quote(ev, selection="HOME", price=-110, captured=aged)
    assert first is not None

    # The feed polls again and reports the SAME price. Skipping here would let
    # the quote age out of the TTL and make a valid line unplaceable.
    refreshed = quote(ev, selection="HOME", price=-110)
    assert refreshed is not None, "unchanged quote past the refresh window must be re-recorded"
    assert refreshed != first

    newest_age = h.scalar(admin,
        """SELECT floor(extract(epoch FROM (NOW() - captured_at)))
           FROM public.market_snapshots
           WHERE event_id=%s AND sportsbook='BOOK_A' AND market_type='SPREAD'
           ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1""", (ev,))
    assert newest_age < refresh, f"newest quote is {newest_age}s old"

    # The payoff: placement still works, at the same unchanged price.
    u = new_user(admin, "refreshed"); ch = open_chapter(u)
    ticket = place(u, ch, refreshed, 100)
    assert ticket is not None
    assert h.scalar(admin,
        "SELECT accepted_price FROM public.tickets WHERE id=%s", (ticket,)) == -110
    admin.close()


def t05_refresh_window_must_stay_inside_ttl():
    admin = h.connect(); h.reset(admin)

    def bad():
        with h.connect() as owner:
            owner.execute(
                "UPDATE public.system_settings SET snapshot_refresh_seconds = snapshot_ttl_seconds")
    h.expect_error(bad, "ck_refresh_inside_ttl", "M2-T05 equal")

    def worse():
        with h.connect() as owner:
            owner.execute("UPDATE public.system_settings SET snapshot_refresh_seconds = 9999")
    h.expect_error(worse, "ck_refresh_inside_ttl", "M2-T05 greater")
    admin.close()


def t06_invalid_quotes_rejected():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    h.expect_error(lambda: quote(ev, price=0), "INVALID_PRICE", "M2-T06 zero odds")
    h.expect_error(lambda: quote(ev, price=-99), "INVALID_PRICE", "M2-T06 -99")
    h.expect_error(lambda: quote(ev, market="MONEYLINE", line=-3.0, price=-150),
                   "INVALID_LINE", "M2-T06 moneyline with line")
    h.expect_error(lambda: quote(ev, market="TOTAL", line=None, price=-110),
                   "INVALID_LINE", "M2-T06 total without line")

    future = h.scalar(admin, "SELECT NOW() + INTERVAL '1 hour'")
    h.expect_error(lambda: quote(ev, price=-125, captured=future),
                   "INVALID_CAPTURE_TIME", "M2-T06 future quote")
    admin.close()


def t07_batch_survives_one_bad_row():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    rows = [
        {"event_id": str(ev), "market_type": "SPREAD", "selection": "HOME1",
         "line": "-3.5", "price": -105, "sportsbook": "BOOK_A",
         "source_provider": "TEST", "is_in_play": False},
        {"event_id": str(ev), "market_type": "SPREAD", "selection": "HOME1",
         "line": "-3.5", "price": 0, "sportsbook": "BOOK_B",          # bad
         "source_provider": "TEST", "is_in_play": False},
        {"event_id": str(ev), "market_type": "MONEYLINE", "selection": "HOME1",
         "line": None, "price": -162, "sportsbook": "BOOK_A",
         "source_provider": "TEST", "is_in_play": False},
    ]
    out = as_json(svc("SELECT public.ingest_market_snapshots_rpc(%s::jsonb, NULL)",
                      (json.dumps(rows),)))

    assert out["written"] == 2, out
    assert out["failed"] == 1, out
    assert len(out["errors"]) == 1 and "INVALID_PRICE" in out["errors"][0]["error"], out
    admin.close()


def t08_quotes_for_unknown_or_closed_events_rejected():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    h.expect_error(lambda: quote(str(uuid.uuid4())), "EVENT_NOT_FOUND", "M2-T08 unknown")

    svc("SELECT public.close_event_rpc(%s,'TEST')", (ev,))
    h.expect_error(lambda: quote(ev, price=-140), "EVENT_CLOSED", "M2-T08 closed")
    admin.close()


def t09_event_identity_mismatch_rejected():
    admin = h.connect(); h.reset(admin)
    start = h.scalar(admin, "SELECT NOW() + INTERVAL '3 hours'")
    svc("SELECT public.ingest_event_rpc('E-9','DAL','PHI',%s::timestamptz,'NFL','NFL','FEED')",
        (start,))

    def collide():
        svc("SELECT public.ingest_event_rpc('E-9','NYG','WAS',%s::timestamptz,'NFL','NFL','FEED')",
            (start,))
    h.expect_error(collide, "EVENT_IDENTITY_MISMATCH", "M2-T09")
    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 1
    admin.close()


# =============================================================================
# Schedule changes and postponement
# =============================================================================

def t10_minor_reschedule_does_not_void():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "resched"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    ticket = place(u, ch, snap, 500)

    new_start = h.scalar(admin,
        "SELECT original_scheduled_start + INTERVAL '2 hours' FROM public.events WHERE id=%s", (ev,))
    out = as_json(svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED','TV_WINDOW')",
                      (ev, new_start)))

    assert out["action"] == "RESCHEDULED", out
    assert out["tickets_voided"] == 0, out
    assert h.scalar(admin, "SELECT status FROM public.tickets WHERE id=%s", (ticket,)) == "ACCEPTED"
    assert h.balances(admin, ch) == (10000, 500, 9500)
    assert h.scalar(admin,
        "SELECT count(*) FROM public.event_schedule_history WHERE event_id=%s", (ev,)) == 2
    assert "RESCHEDULED" in lifecycle(admin, ev)
    admin.close()


def t11_postponement_beyond_threshold_voids_open_tickets():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "postponed"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    ticket = place(u, ch, snap, 1000)
    assert h.balances(admin, ch) == (10000, 1000, 9000)

    new_start = h.scalar(admin,
        "SELECT original_scheduled_start + INTERVAL '49 hours' FROM public.events WHERE id=%s", (ev,))
    out = as_json(svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED','WEATHER')",
                      (ev, new_start)))

    assert out["action"] == "POSTPONED", out
    assert out["tickets_voided"] == 1, out
    assert h.scalar(admin, "SELECT status FROM public.tickets WHERE id=%s", (ticket,)) == "VOIDED"
    assert h.balances(admin, ch) == (10000, 0, 10000), "stake must be returned"
    assert h.scalar(admin,
        "SELECT status FROM public.risk_reservations WHERE ticket_id=%s", (ticket,)) == "VOIDED"
    assert h.row(admin,
        "SELECT transaction_type, amount FROM public.wallet_transactions WHERE ticket_id=%s",
        (ticket,)) == ("SETTLEMENT_VOID", 0)
    assert "TICKETS_VOIDED" in lifecycle(admin, ev)
    admin.close()


def t12_cumulative_shift_crosses_threshold():
    """Two sub-threshold moves that together exceed it must still void."""
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "cumulative"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    place(u, ch, snap, 800)

    first = h.scalar(admin,
        "SELECT original_scheduled_start + INTERVAL '25 hours' FROM public.events WHERE id=%s", (ev,))
    out1 = as_json(svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED',NULL)", (ev, first)))
    assert out1["action"] == "RESCHEDULED", out1
    assert h.balances(admin, ch)[1] == 800, "first move is under threshold"

    second = h.scalar(admin,
        "SELECT original_scheduled_start + INTERVAL '50 hours' FROM public.events WHERE id=%s", (ev,))
    out2 = as_json(svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED',NULL)", (ev, second)))
    assert out2["action"] == "POSTPONED", out2
    assert out2["tickets_voided"] == 1, out2
    assert h.balances(admin, ch) == (10000, 0, 10000)
    admin.close()


def t13_large_shift_earlier_also_voids():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1, starts_in="7 days")[0][0]
    u = new_user(admin, "earlier"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    place(u, ch, snap, 700)

    earlier = h.scalar(admin,
        "SELECT original_scheduled_start - INTERVAL '49 hours' FROM public.events WHERE id=%s", (ev,))
    out = as_json(svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED','MOVED_UP')",
                      (ev, earlier)))

    assert out["action"] == "POSTPONED", out
    assert out["tickets_voided"] == 1, out
    assert h.balances(admin, ch) == (10000, 0, 10000)
    admin.close()


def t14_started_or_closed_event_cannot_be_rescheduled():
    admin = h.connect(); h.reset(admin)
    evs = seed_slate(admin, 2)
    live_ev, closed_ev = evs[0][0], evs[1][0]

    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (live_ev,))
    later = h.scalar(admin, "SELECT NOW() + INTERVAL '5 hours'")
    h.expect_error(
        lambda: svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED',NULL)", (live_ev, later)),
        "EVENT_STARTED", "M2-T14 live")

    svc("SELECT public.close_event_rpc(%s,'TEST')", (closed_ev,))
    h.expect_error(
        lambda: svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED',NULL)", (closed_ev, later)),
        "EVENT_CLOSED", "M2-T14 closed")
    admin.close()


def t15_noop_reschedule_writes_no_history():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    before = h.scalar(admin,
        "SELECT count(*) FROM public.event_schedule_history WHERE event_id=%s", (ev,))

    same = event_start(admin, ev)
    out = as_json(svc("SELECT public.reschedule_event_rpc(%s,%s::timestamptz,'FEED',NULL)", (ev, same)))
    assert out["action"] == "UNCHANGED", out

    after = h.scalar(admin,
        "SELECT count(*) FROM public.event_schedule_history WHERE event_id=%s", (ev,))
    assert after == before, (before, after)
    admin.close()


# =============================================================================
# Closing lines
# =============================================================================

def t16_closing_line_is_captured_per_book():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    # Each book drifts to its own final number.
    quote(ev, book="BOOK_A", price=-120)
    quote(ev, book="BOOK_B", price=-104)

    captured = svc("SELECT public.capture_closing_line_rpc(%s,'TEST')", (ev,))
    assert captured == 4, captured   # SPREAD + MONEYLINE, two books

    closing = {(r[0], r[1]): r[2] for r in h.rows(
        admin,
        """SELECT sportsbook, market_type, price FROM public.market_snapshots
           WHERE event_id=%s AND is_closing_snapshot ORDER BY sportsbook, market_type""",
        (ev,))}
    assert closing[("BOOK_A", "SPREAD")] == -120, closing
    assert closing[("BOOK_B", "SPREAD")] == -104, closing
    admin.close()


def t17_in_play_quote_never_becomes_closing():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    quote(ev, book="BOOK_A", price=-120)
    quote(ev, book="BOOK_A", price=-300, in_play=True)   # arrives later, in-play

    svc("SELECT public.capture_closing_line_rpc(%s,'TEST')", (ev,))

    closing_price, closing_in_play = h.row(admin,
        """SELECT price, is_in_play FROM public.market_snapshots
           WHERE event_id=%s AND sportsbook='BOOK_A' AND market_type='SPREAD'
             AND is_closing_snapshot""", (ev,))
    assert closing_price == -120, "closing line must be the last PRE-GAME quote"
    assert closing_in_play is False
    admin.close()


def t18_closing_line_capture_is_idempotent():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    first = svc("SELECT public.capture_closing_line_rpc(%s,'TEST')", (ev,))
    assert first == 4
    before = h.rows(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND is_closing_snapshot
           ORDER BY ingest_seq""", (ev,))

    second = svc("SELECT public.capture_closing_line_rpc(%s,'TEST')", (ev,))
    assert second == 0, "already-captured groups must be left alone"

    after = h.rows(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND is_closing_snapshot
           ORDER BY ingest_seq""", (ev,))
    assert before == after
    admin.close()


def t19_quotes_after_kickoff_are_excluded():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    quote(ev, book="BOOK_A", price=-118)

    # Kickoff now; capture uses the real start time as the cutoff.
    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))

    closing_at = h.scalar(admin,
        """SELECT captured_at FROM public.market_snapshots
           WHERE event_id=%s AND sportsbook='BOOK_A' AND market_type='SPREAD'
             AND is_closing_snapshot""", (ev,))
    kickoff = h.scalar(admin, "SELECT actual_start_time FROM public.events WHERE id=%s", (ev,))
    assert closing_at <= kickoff, (closing_at, kickoff)
    admin.close()


def t20_kickoff_captures_closing_lines_automatically():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    out = as_json(svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,)))
    assert out["closing_lines_captured"] == 4, out
    assert h.scalar(admin,
        "SELECT count(*) FROM public.market_snapshots WHERE event_id=%s AND is_closing_snapshot",
        (ev,)) == 4
    assert lifecycle(admin, ev) == ["INGESTED", "CLOSING_LINE_CAPTURED", "KICKED_OFF"]
    admin.close()


# =============================================================================
# Lifecycle
# =============================================================================

def t21_mark_live_is_idempotent():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    first = as_json(svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,)))
    second = as_json(svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,)))
    assert first["action"] == "KICKED_OFF" and second["action"] == "ALREADY_LIVE", (first, second)
    assert second["closing_lines_captured"] == 0
    assert h.scalar(admin,
        "SELECT count(*) FROM public.market_snapshots WHERE event_id=%s AND is_closing_snapshot",
        (ev,)) == 4
    admin.close()


def t22_close_event_reports_ungraded_and_is_idempotent():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "closer"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    place(u, ch, snap, 400)

    out = as_json(svc("SELECT public.close_event_rpc(%s,'TEST')", (ev,)))
    assert out["action"] == "CLOSED", out
    assert out["ungraded_tickets"] == 1, out
    assert out["closing_lines_captured"] == 4, out

    again = as_json(svc("SELECT public.close_event_rpc(%s,'TEST')", (ev,)))
    assert again["action"] == "ALREADY_CLOSED", again
    admin.close()


def t23_cancel_event_voids_and_closes():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "cancelled"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    ticket = place(u, ch, snap, 900)

    out = as_json(svc("SELECT public.cancel_event_rpc(%s,'ABANDONED','TEST')", (ev,)))
    assert out["action"] == "CANCELLED" and out["tickets_voided"] == 1, out
    assert h.scalar(admin, "SELECT is_closed FROM public.events WHERE id=%s", (ev,)) is True
    assert h.scalar(admin, "SELECT status FROM public.tickets WHERE id=%s", (ticket,)) == "VOIDED"
    assert h.balances(admin, ch) == (10000, 0, 10000)
    assert "CANCELLED" in lifecycle(admin, ev)
    admin.close()


def t24_placement_blocked_once_event_is_live():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "toolate"); ch = open_chapter(u)
    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))

    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))
    h.expect_error(lambda: place(u, ch, snap, 100), "EVENT_LIVE", "M2-T24")
    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 0
    admin.close()


# =============================================================================
# Board and closing-line value
# =============================================================================

def t25_board_agrees_with_placement_rpc():
    """Anything the board calls placeable must actually be placeable."""
    admin = h.connect(); h.reset(admin)
    seed_slate(admin, 2)
    u = new_user(admin, "board"); ch = open_chapter(u)

    rows = h.rows(admin,
        "SELECT snapshot_id FROM public.current_market_board WHERE is_placeable ORDER BY snapshot_id")
    assert len(rows) == 8, len(rows)   # 2 events x 2 markets x 2 books

    for (snapshot_id,) in rows:
        assert place(u, ch, snapshot_id, 100) is not None

    # And the converse: once an event goes live the board must stop calling its
    # quotes placeable, and the RPC must agree.
    ev = h.scalar(admin, "SELECT id FROM public.events ORDER BY created_at LIMIT 1")
    live_snap = h.scalar(admin,
        """SELECT snapshot_id FROM public.current_market_board
           WHERE event_id=%s AND is_placeable LIMIT 1""", (ev,))
    assert live_snap is not None

    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))

    still_placeable = h.scalar(admin,
        """SELECT count(*) FROM public.current_market_board
           WHERE event_id=%s AND is_placeable""", (ev,))
    assert still_placeable == 0, "board still offering a live event"
    h.expect_error(lambda: place(u, ch, live_snap, 100), "EVENT_LIVE", "M2-T25 live rejection")
    admin.close()


def t34_out_of_order_quote_never_becomes_current():
    """A late-arriving older quote is kept as history but must not win.

    Every read orders by captured_at DESC, ingest_seq DESC, so a row that
    arrives late with an older timestamp is recorded without ever becoming the
    executable price.
    """
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]

    current = quote(ev, book="BOOK_A", price=-125)
    assert current is not None

    # A straggler from the provider, timestamped before the current quote.
    late = h.scalar(admin,
        "SELECT olp_test.age_quote(%s,'SPREAD','HOME1','BOOK_A', INTERVAL '30 seconds')", (ev,))
    assert late is not None

    newest = h.scalar(admin,
        """SELECT id FROM public.market_snapshots
           WHERE event_id=%s AND market_type='SPREAD' AND selection='HOME1'
             AND sportsbook='BOOK_A'
           ORDER BY captured_at DESC, ingest_seq DESC LIMIT 1""", (ev,))
    assert newest == current, "a back-dated arrival must not become the current quote"

    board_snap = h.scalar(admin,
        """SELECT snapshot_id FROM public.current_market_board
           WHERE event_id=%s AND market_type='SPREAD' AND sportsbook='BOOK_A'""", (ev,))
    assert board_snap == current

    # It is still retained as history, not discarded.
    assert h.scalar(admin,
        "SELECT count(*) FROM public.market_snapshots WHERE id=%s", (late,)) == 1

    # And placing against the straggler is refused as superseded.
    u = new_user(admin, "straggler"); ch = open_chapter(u)
    h.expect_error(lambda: place(u, ch, late, 100), "MARKET_MOVED", "M2-T34")
    admin.close()


def t26_same_book_clv_computed():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "clv"); ch = open_chapter(u)

    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    ticket = place(u, ch, snap, 500)          # taken at -110

    quote(ev, book="BOOK_A", price=-130)      # BOOK_A closes worse
    quote(ev, book="BOOK_B", price=-101)      # BOOK_B closes better (must be ignored)
    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))

    row = h.row(admin,
        """SELECT accepted_price, closing_price, line_moved, beat_close
           FROM public.ticket_closing_line_value WHERE ticket_id=%s""", (ticket,))
    assert row is not None, "no CLV row produced"
    accepted, closing, line_moved, beat = row
    assert accepted == -110, accepted
    assert closing == -130, "must compare against the SAME book's close"
    assert line_moved is False
    assert beat is True, "taking -110 before it closed -130 beat the close"
    admin.close()


def t27_clv_is_null_when_the_line_moved():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "clvline"); ch = open_chapter(u)

    snap = h.scalar(admin,
        """SELECT id FROM public.market_snapshots WHERE event_id=%s AND sportsbook='BOOK_A'
           AND market_type='SPREAD' ORDER BY ingest_seq DESC LIMIT 1""", (ev,))
    ticket = place(u, ch, snap, 500)          # -3.0 at -110

    quote(ev, book="BOOK_A", line=-3.5, price=-110)   # the number moved
    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))

    accepted_line, closing_line, line_moved, beat = h.row(admin,
        """SELECT accepted_line, closing_line, line_moved, beat_close
           FROM public.ticket_closing_line_value WHERE ticket_id=%s""", (ticket,))
    assert (accepted_line, closing_line) == (-3.0, -3.5), (accepted_line, closing_line)
    assert line_moved is True
    assert beat is None, "at a different number it is a different bet, not a better price"
    admin.close()


# =============================================================================
# Worker
# =============================================================================

def t28_worker_full_poll_cycle():
    admin = h.connect(); h.reset(admin)
    provider = FixtureProvider(events=3)

    with h.connect_as("service_role") as conn:
        sched, odds = poll_once(conn, provider)

    assert sched.events_created == 3, sched
    assert odds.snapshots_written == 12, odds     # 3 events x 2 markets x 2 books
    assert odds.snapshots_failed == 0, odds.errors

    # Second poll: unchanged prices are skipped rather than re-recorded.
    with h.connect_as("service_role") as conn:
        sched2, odds2 = poll_once(conn, provider)
    assert sched2.events_created == 0, sched2
    assert odds2.snapshots_written + odds2.snapshots_skipped == 12, odds2
    assert odds2.snapshots_skipped > 0, "unchanged quotes should be skipped"

    runs = h.rows(admin,
        "SELECT kind, status FROM public.ingestion_runs ORDER BY run_seq")
    assert [tuple(r) for r in runs] == [
        ("SCHEDULE", "SUCCEEDED"), ("ODDS", "SUCCEEDED"),
        ("SCHEDULE", "SUCCEEDED"), ("ODDS", "SUCCEEDED")], runs
    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 3
    admin.close()


def t29_worker_reports_unknown_events_without_losing_good_rows():
    admin = h.connect(); h.reset(admin)
    start = dt.datetime.now(UTC) + dt.timedelta(hours=3)

    known = EventRow("W-1", "HOME", "AWAY", start)
    provider = ScriptedProvider(
        events=[known],
        quotes=[
            QuoteRow("W-1", "SPREAD", "HOME", -110, "BOOK_A", line=-3.0),
            QuoteRow("W-NOPE", "SPREAD", "HOME", -110, "BOOK_A", line=-3.0),
        ],
    )

    with h.connect_as("service_role") as conn:
        ingest_schedule(conn, provider)
        odds = ingest_odds(conn, provider)

    assert odds.snapshots_written == 1, odds
    assert odds.snapshots_failed == 1, odds
    assert any("UNKNOWN_EVENT" in e["error"] for e in odds.errors), odds.errors
    admin.close()


def t30_failed_run_is_recorded():
    admin = h.connect(); h.reset(admin)

    class Boom(ScriptedProvider):
        def fetch_schedule(self):
            raise RuntimeError("provider exploded")

    provider = Boom(name="BOOM")
    with h.connect_as("service_role") as conn:
        try:
            ingest_schedule(conn, provider)
            raise AssertionError("should have propagated the provider error")
        except RuntimeError as exc:
            assert "provider exploded" in str(exc)

    status, err = h.row(admin,
        "SELECT status, error_text FROM public.ingestion_runs ORDER BY run_seq DESC LIMIT 1")
    assert status == "FAILED", status
    assert "provider exploded" in err, err
    admin.close()


# =============================================================================
# Security
# =============================================================================

def t31_clients_cannot_ingest():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "nosy")
    start = h.scalar(admin, "SELECT NOW() + INTERVAL '3 hours'")

    attempts = [
        ("SELECT public.ingest_event_rpc('X','A','B',%s::timestamptz,'NFL','NFL','X')", (start,)),
        ("""SELECT public.ingest_market_snapshot_rpc(%s::uuid,'SPREAD','HOME1',-3.0,-110,
                                                     'BOOK_A','X',NOW(),false)""", (ev,)),
        ("SELECT public.reschedule_event_rpc(%s::uuid, NOW() + INTERVAL '9 days','X',NULL)", (ev,)),
        ("SELECT public.capture_closing_line_rpc(%s::uuid,'X')", (ev,)),
        ("SELECT public.mark_event_live_rpc(%s::uuid, NULL, 'X')", (ev,)),
        ("SELECT public.close_event_rpc(%s::uuid,'X')", (ev,)),
        ("SELECT public.cancel_event_rpc(%s::uuid,'r','X')", (ev,)),
        ("SELECT public.void_event_tickets_rpc(%s::uuid,'r','X')", (ev,)),
    ]
    for sql, params in attempts:
        def attempt(s=sql, p=params):
            with h.connect_as("authenticated", u) as c:
                c.execute(s, p)
        h.expect_error(attempt, "permission denied", f"M2-T31 {sql.split('(')[0][-32:]}")

    assert h.scalar(admin, "SELECT count(*) FROM public.events") == 1
    admin.close()


def t32_operational_tables_are_not_client_readable():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    svc("SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))
    u = new_user(admin, "reader")

    for table in ("public.ingestion_runs", "public.event_lifecycle_log"):
        def read(t=table):
            with h.connect_as("authenticated", u) as c:
                c.execute(f"SELECT count(*) FROM {t}")
        h.expect_error(read, "permission denied", f"M2-T32 {table}")

    # Market data itself stays public, as Package #1 established.
    with h.connect_as("authenticated", u) as c:
        assert h.scalar(c, "SELECT count(*) FROM public.events") == 1
        assert h.scalar(c, "SELECT count(*) FROM public.event_schedule_history") >= 1
        assert h.scalar(c, "SELECT count(*) FROM public.current_market_board") >= 0
    admin.close()


def t33_clients_cannot_write_market_data_directly():
    admin = h.connect(); h.reset(admin)
    ev = seed_slate(admin, 1)[0][0]
    u = new_user(admin, "forger")

    def forge_quote():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                """INSERT INTO public.market_snapshots
                   (event_id, market_type, selection, line, price, sportsbook,
                    source_provider, captured_at)
                   VALUES (%s,'SPREAD','HOME1',-3,500,'BOOK_A','forged',NOW())""", (ev,))
    h.expect_error(forge_quote, "permission denied", "M2-T33 snapshot")

    def move_kickoff():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                "UPDATE public.events SET current_scheduled_start = NOW() + INTERVAL '9 days' WHERE id=%s",
                (ev,))
    h.expect_error(move_kickoff, "permission denied", "M2-T33 event")
    admin.close()


PACKAGE2 = [
    ("M2-T01", "Event ingestion is idempotent", t01_event_ingestion_is_idempotent),
    ("M2-T02", "Unchanged quote inside refresh window skipped", t02_unchanged_quote_inside_refresh_window_is_skipped),
    ("M2-T03", "Price move appended, history preserved", t03_price_move_is_appended_and_history_preserved),
    ("M2-T04", "Unchanged quote refreshed before TTL expiry", t04_unchanged_quote_is_refreshed_before_it_outlives_the_ttl),
    ("M2-T05", "Refresh window must stay inside TTL", t05_refresh_window_must_stay_inside_ttl),
    ("M2-T06", "Invalid quotes rejected", t06_invalid_quotes_rejected),
    ("M2-T07", "Batch survives one bad row", t07_batch_survives_one_bad_row),
    ("M2-T08", "Quotes for unknown/closed events rejected", t08_quotes_for_unknown_or_closed_events_rejected),
    ("M2-T09", "Event identity mismatch rejected", t09_event_identity_mismatch_rejected),
    ("M2-T10", "Minor reschedule does not void", t10_minor_reschedule_does_not_void),
    ("M2-T11", "Postponement voids open tickets", t11_postponement_beyond_threshold_voids_open_tickets),
    ("M2-T12", "Cumulative shift crosses threshold", t12_cumulative_shift_crosses_threshold),
    ("M2-T13", "Large shift earlier also voids", t13_large_shift_earlier_also_voids),
    ("M2-T14", "Started/closed event cannot be rescheduled", t14_started_or_closed_event_cannot_be_rescheduled),
    ("M2-T15", "No-op reschedule writes no history", t15_noop_reschedule_writes_no_history),
    ("M2-T16", "Closing line captured per book", t16_closing_line_is_captured_per_book),
    ("M2-T17", "In-play quote never becomes closing", t17_in_play_quote_never_becomes_closing),
    ("M2-T18", "Closing-line capture is idempotent", t18_closing_line_capture_is_idempotent),
    ("M2-T19", "Quotes after kickoff excluded", t19_quotes_after_kickoff_are_excluded),
    ("M2-T20", "Kickoff captures closing lines", t20_kickoff_captures_closing_lines_automatically),
    ("M2-T21", "mark_event_live is idempotent", t21_mark_live_is_idempotent),
    ("M2-T22", "close_event reports ungraded, idempotent", t22_close_event_reports_ungraded_and_is_idempotent),
    ("M2-T23", "cancel_event voids and closes", t23_cancel_event_voids_and_closes),
    ("M2-T24", "Placement blocked once live", t24_placement_blocked_once_event_is_live),
    ("M2-T25", "Board agrees with placement RPC", t25_board_agrees_with_placement_rpc),
    ("M2-T26", "Same-book CLV computed", t26_same_book_clv_computed),
    ("M2-T27", "CLV null when the line moved", t27_clv_is_null_when_the_line_moved),
    ("M2-T28", "Worker full poll cycle", t28_worker_full_poll_cycle),
    ("M2-T29", "Worker reports unknown events", t29_worker_reports_unknown_events_without_losing_good_rows),
    ("M2-T30", "Failed run is recorded", t30_failed_run_is_recorded),
    ("M2-T31", "Clients cannot ingest", t31_clients_cannot_ingest),
    ("M2-T32", "Operational tables not client-readable", t32_operational_tables_are_not_client_readable),
    ("M2-T33", "Clients cannot write market data", t33_clients_cannot_write_market_data_directly),
    ("M2-T34", "Out-of-order quote never becomes current", t34_out_of_order_quote_never_becomes_current),
]
