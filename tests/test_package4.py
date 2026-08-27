"""OLP-M1 Package #4 -- Market Intelligence.

Tests the pre-registered contract in PACKAGE4_PREREG.md. The load-bearing group
is same-line isolation: live data showed 64.3% of spread and 79.4% of total
event/selections carry more than one line at once, so any blending across lines
would quietly mix different wagers.
"""

import harness as h
from test_acceptance import new_user, open_chapter, place

SQL_Q = """SELECT olp_test.add_snapshot(%s::uuid, %s::public.market_type, %s::text,
           %s::numeric, %s::int, %s::text, NOW() - make_interval(secs => %s), %s::boolean)"""


def event(admin, src, home="DAL", away="PHI", starts_in="4 hours"):
    return h.scalar(admin, "SELECT olp_test.create_event(%s,%s,%s,%s::interval)",
                    (src, home, away, starts_in))


def quote(admin, ev, market, selection, line, price, book, age=0, in_play=False):
    return h.scalar(admin, SQL_Q, (ev, market, selection, line, price, book, age, in_play))


def rows_for(admin, ev, market=None, view="canonical_market"):
    sql = f"""SELECT selection, line, best_price, best_book, consensus_probability,
                     book_count, devig_book_count, modal_line, is_modal_line,
                     market_quality, quality_reasons
              FROM public.{view} WHERE event_id = %s"""
    params = [ev]
    if market:
        sql += " AND market_type = %s"
        params.append(market)
    return h.rows(admin, sql + " ORDER BY selection, line", tuple(params))


def two_sided(admin, ev, market, line, home_price, away_price, book,
              home="DAL", away="PHI", age=0):
    """Seed a correctly-paired two-sided quote from one book."""
    quote(admin, ev, market, home, line, home_price, book, age)
    away_line = None if line is None else (-line if market == "SPREAD" else line)
    quote(admin, ev, market, away, away_line, away_price, book, age)


# =============================================================================
# Same-line isolation
# =============================================================================

def t01_different_lines_are_separate_rows():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T01")
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookA")
    two_sided(admin, ev, "SPREAD", -3.5, -105, -115, "bookB")

    dal = [r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"]
    assert len(dal) == 2, f"expected two rows, one per line: {dal}"
    assert sorted(float(r[1]) for r in dal) == [-3.5, -3.0], dal
    for r in dal:
        assert r[5] == 1, f"each line must count only its own book: {r}"
    admin.close()
    return "-3.0 and -3.5 stayed separate"


def t02_best_price_never_crosses_lines():
    """The -3.5 book pays more. It must NOT become best price at -3."""
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T02")
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookA")
    two_sided(admin, ev, "SPREAD", -3.0, -108, -112, "bookB")
    two_sided(admin, ev, "SPREAD", -3.5, +140, -170, "bookRICH")   # far better payout

    by_line = {float(r[1]): r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"}
    assert by_line[-3.0][2] == -108, f"best at -3 leaked from -3.5: {by_line[-3.0]}"
    assert by_line[-3.0][3] == "bookB"
    assert by_line[-3.5][2] == 140 and by_line[-3.5][3] == "bookRICH"
    admin.close()
    return "+140 at -3.5 never contaminated -3.0"


def t03_consensus_uses_only_same_line_books():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T03")
    for bk in ("bookA", "bookB", "bookC"):
        two_sided(admin, ev, "SPREAD", -3.0, -110, -110, bk)
    two_sided(admin, ev, "SPREAD", -3.5, -400, +300, "bookSKEW")   # wildly different

    by_line = {float(r[1]): r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"}
    assert by_line[-3.0][5] == 3, by_line[-3.0]
    assert abs(float(by_line[-3.0][4]) - 0.5) < 0.0001, \
        f"-3.0 consensus moved by a -3.5 book: {by_line[-3.0][4]}"
    admin.close()
    return "skewed -3.5 book left -3.0 consensus at 0.5"


def t04_totals_do_not_merge_across_lines():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T04")
    two_sided(admin, ev, "TOTAL", 44.5, -110, -110, "bookA", home="OVER", away="UNDER")
    two_sided(admin, ev, "TOTAL", 45.0, -108, -112, "bookB", home="OVER", away="UNDER")

    over = [r for r in rows_for(admin, ev, "TOTAL") if r[0] == "OVER"]
    assert len(over) == 2, over
    assert all(r[5] == 1 for r in over), f"totals merged across lines: {over}"
    admin.close()


def t05_moneyline_null_line_groups_correctly():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T05")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookA")

    ml = [r for r in rows_for(admin, ev, "MONEYLINE") if r[0] == "DAL"]
    assert len(ml) == 1 and ml[0][1] is None, ml
    assert ml[0][5] == 2, f"moneyline books not grouped: {ml}"
    assert ml[0][8] is True, "NULL line must count as its own modal line"
    admin.close()


# =============================================================================
# Cross-line leakage canary and partner rules
# =============================================================================

def t06_leakage_canary_is_detectable():
    """Constructed so a leak would change the answer materially."""
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T06")
    two_sided(admin, ev, "SPREAD", -3.0, -200, +170, "bookA")
    two_sided(admin, ev, "SPREAD", -3.0, -205, +175, "bookB")
    two_sided(admin, ev, "SPREAD", -7.0, +250, -300, "bookFAR")

    by_line = {float(r[1]): r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"}
    p3 = float(by_line[-3.0][4])
    p7 = float(by_line[-7.0][4])
    assert p3 > 0.60, p3
    assert p7 < 0.32, p7
    # A blend would land between the two; neither row may.
    assert not (0.35 < p3 < 0.58), f"-3.0 consensus looks blended: {p3}"
    assert by_line[-3.0][5] == 2 and by_line[-7.0][5] == 1
    admin.close()
    return f"-3.0 p={p3:.3f} vs -7.0 p={p7:.3f}, no blend"


def t07_spread_pairs_at_negated_line_only():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T07")
    quote(admin, ev, "SPREAD", "DAL", -3.0, -110, "bookMISS")
    quote(admin, ev, "SPREAD", "PHI",  3.5, -110, "bookMISS")     # near miss

    dal = [r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"][0]
    assert dal[6] == 0, f"a -3/+3.5 near miss was paired: {dal}"
    assert dal[4] is None
    assert "NO_DEVIG_PAIR" in dal[10], dal[10]

    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookOK")
    dal = [r for r in rows_for(admin, ev, "SPREAD")
           if r[0] == "DAL" and float(r[1]) == -3.0][0]
    assert dal[6] == 1, f"exactly one book should pair: {dal}"
    admin.close()
    return "near miss refused, correct pair accepted"


def t08_totals_pair_at_same_line_only():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T08")
    quote(admin, ev, "TOTAL", "OVER", 44.5, -110, "bookMISS")
    quote(admin, ev, "TOTAL", "UNDER", 45.0, -110, "bookMISS")

    over = [r for r in rows_for(admin, ev, "TOTAL") if r[0] == "OVER"][0]
    assert over[6] == 0 and "NO_DEVIG_PAIR" in over[10], over
    admin.close()


# =============================================================================
# Modal line -- determinism and INDEPENDENCE from order and recency
# =============================================================================

def _modal_of(admin, ev, market="TOTAL", selection="OVER"):
    return h.scalar(admin,
        """SELECT DISTINCT modal_line FROM public.canonical_market
           WHERE event_id=%s AND market_type=%s AND selection=%s""",
        (ev, market, selection))


def t09_modal_invariant_to_order_and_recency():
    """The property review asked to lock in.

    A 2-vs-2 tie must resolve identically under: reversed insertion order,
    reversed bookmaker order, and one tied line refreshed later than the other.
    """
    admin = h.connect(); h.reset(admin)
    results = {}

    # (a) 44.5 first
    h.reset(admin)
    ev = event(admin, "MODAL-A")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "TOTAL", 44.5, -110, -110, bk, home="OVER", away="UNDER")
    for bk in ("bookC", "bookD"):
        two_sided(admin, ev, "TOTAL", 45.0, -108, -112, bk, home="OVER", away="UNDER")
    results["insertion 44.5 first"] = _modal_of(admin, ev)

    # (b) 45.0 first -- reversed insertion order
    h.reset(admin)
    ev = event(admin, "MODAL-B")
    for bk in ("bookC", "bookD"):
        two_sided(admin, ev, "TOTAL", 45.0, -108, -112, bk, home="OVER", away="UNDER")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "TOTAL", 44.5, -110, -110, bk, home="OVER", away="UNDER")
    results["insertion 45.0 first"] = _modal_of(admin, ev)

    # (c) reversed bookmaker order -- alphabetically later books on the low line
    h.reset(admin)
    ev = event(admin, "MODAL-C")
    for bk in ("bookY", "bookZ"):
        two_sided(admin, ev, "TOTAL", 44.5, -110, -110, bk, home="OVER", away="UNDER")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "TOTAL", 45.0, -108, -112, bk, home="OVER", away="UNDER")
    results["books reversed"] = _modal_of(admin, ev)

    # (d) 45.0 refreshed LATER than 44.5 -- recency must not matter
    h.reset(admin)
    ev = event(admin, "MODAL-D")
    for bk in ("bookA", "bookB"):
        two_sided(admin, ev, "TOTAL", 44.5, -110, -110, bk, home="OVER", away="UNDER", age=50)
    for bk in ("bookC", "bookD"):
        two_sided(admin, ev, "TOTAL", 45.0, -108, -112, bk, home="OVER", away="UNDER", age=0)
    results["45.0 refreshed later"] = _modal_of(admin, ev)

    distinct = {float(v) for v in results.values()}
    assert distinct == {44.5}, f"modal line was not invariant: {results}"
    admin.close()
    return "44.50 under all four permutations"


def t10_modal_reflects_book_count_not_line_value():
    """line ASC is only a tie-break. Book count must dominate it."""
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "MODAL-COUNT")
    two_sided(admin, ev, "TOTAL", 44.5, -110, -110, "bookA", home="OVER", away="UNDER")
    for bk in ("bookB", "bookC", "bookD"):
        two_sided(admin, ev, "TOTAL", 45.0, -108, -112, bk, home="OVER", away="UNDER")
    assert float(_modal_of(admin, ev)) == 45.0, "book count must beat line ASC"
    admin.close()


# =============================================================================
# Odds arithmetic
# =============================================================================

def t11_probability_round_trip_within_quantisation():
    """American odds are integers, so the round-trip is exact only to the
    granularity of one integer step. That is a property of the notation, not
    implementation slop -- so the tolerance is stated, not hidden."""
    admin = h.connect(); h.reset(admin)
    worst = h.scalar(admin, """
        SELECT max(abs(back - p)) FROM (
          SELECT p, public.olp_implied_probability(public.olp_fair_american(p)) AS back
          FROM generate_series(0.02, 0.98, 0.005) p) x""")
    assert worst is not None and float(worst) < 0.0015, f"worst round-trip error {worst}"

    # Monotonic: higher probability must never yield a longer price.
    bad = h.scalar(admin, """
        SELECT count(*) FROM (
          SELECT p, public.olp_fair_american(p) AS px,
                 lag(public.olp_fair_american(p)) OVER (ORDER BY p) AS prev
          FROM generate_series(0.05, 0.95, 0.01) p) x
        WHERE prev IS NOT NULL
          AND public.olp_american_profit(1, px) > public.olp_american_profit(1, prev)""")
    assert bad == 0, f"{bad} non-monotonic steps"
    admin.close()
    return f"worst error {float(worst):.5f}, within one integer step"


def t12_devig_sums_to_one_and_is_hand_checkable():
    admin = h.connect(); h.reset(admin)
    fair, over = h.row(admin, """
        SELECT public.olp_devig_multiplicative(
                 public.olp_implied_probability(-110), public.olp_implied_probability(-110)),
               public.olp_overround(
                 public.olp_implied_probability(-110), public.olp_implied_probability(-110))""")
    assert abs(float(fair) - 0.5) < 1e-9, fair
    assert abs(float(over) - 0.047619) < 1e-5, over

    ev = event(admin, "T12")
    for bk in ("bookA", "bookB", "bookC"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    ps = [float(r[4]) for r in rows_for(admin, ev, "MONEYLINE")]
    assert abs(sum(ps) - 1.0) < 1e-6, f"consensus probabilities sum to {sum(ps)}"
    admin.close()
    return "fair=0.5 on -110/-110, overround 4.76%, sides sum to 1"


def t13_best_price_uses_payout_not_numeric_order():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T13")
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookA")
    two_sided(admin, ev, "SPREAD", -3.0, -105, -115, "bookB")
    two_sided(admin, ev, "SPREAD", -3.0,  100, -120, "bookC")
    dal = [r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"][0]
    assert dal[2] == 100 and dal[3] == "bookC", \
        f"+100 must beat -105 and -110 on payout: {dal}"
    admin.close()


def t14_median_resists_a_single_extreme_book():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T14")
    for bk in ("bookA", "bookB", "bookC"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    two_sided(admin, ev, "MONEYLINE", None, -1000, 700, "bookWILD")
    dal = [r for r in rows_for(admin, ev, "MONEYLINE") if r[0] == "DAL"][0]
    assert 0.57 < float(dal[4]) < 0.63, f"median dragged by one book: {dal[4]}"
    admin.close()
    return f"consensus held at {float(dal[4]):.4f} despite a -1000 book"


# =============================================================================
# Quality and gating
# =============================================================================

def t15_reason_codes_accumulate_and_are_reachable():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T15")
    # 2 books (LOW_BOOK_COUNT) + one unpairable (PARTIAL_DEVIG_COVERAGE)
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookA")
    quote(admin, ev, "SPREAD", "DAL", -3.0, -140, "bookLONE")
    dal = [r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"][0]
    reasons = set(dal[10])
    assert {"LOW_BOOK_COUNT", "PARTIAL_DEVIG_COVERAGE"} <= reasons, reasons
    assert len(reasons) >= 2, "reasons must accumulate, not collapse"
    assert dal[9] == "DEGRADED", dal[9]
    admin.close()
    return f"carried {sorted(reasons)}"


def t16_single_book_fails_closed():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T16")
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookONLY")
    dal = [r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"][0]
    assert dal[9] == "UNUSABLE" and "SINGLE_BOOK" in dal[10], dal
    assert h.scalar(admin,
        "SELECT count(*) FROM public.executable_market WHERE event_id=%s", (ev,)) == 0, \
        "a one-book market reached the executable surface"
    assert h.scalar(admin,
        "SELECT count(*) FROM public.canonical_market WHERE event_id=%s", (ev,)) > 0, \
        "canonical must still show it -- quality is advisory there"
    admin.close()
    return "UNUSABLE in canonical, absent from executable"


def t17_stale_books_excluded_on_the_shared_ttl():
    admin = h.connect(); h.reset(admin)
    ttl = h.scalar(admin, "SELECT snapshot_ttl_seconds FROM public.system_settings")
    ev = event(admin, "T17")
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookFRESH")
    two_sided(admin, ev, "SPREAD", -3.0, -105, -115, "bookSTALE", age=ttl + 60)
    dal = [r for r in rows_for(admin, ev, "SPREAD") if r[0] == "DAL"][0]
    assert dal[5] == 1, f"stale book still counted: {dal}"

    # No second freshness constant exists anywhere in Package #4: exactly one
    # TTL column, the one Package #1 defined.
    ttl_cols = [r[0] for r in h.rows(admin, """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='system_settings'
          AND (column_name LIKE '%%ttl%%' OR column_name LIKE '%%stale%%')""")]
    assert ttl_cols == ['snapshot_ttl_seconds'], ttl_cols
    admin.close()


def t18_outliers_only_removed_with_enough_books():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T18-three")
    for bk, ph, pa in [("bookA", -150, 130), ("bookB", -155, 135),
                       ("bookWILD", -2000, 1500)]:
        two_sided(admin, ev, "MONEYLINE", None, ph, pa, bk)
    dal = [r for r in rows_for(admin, ev, "MONEYLINE") if r[0] == "DAL"][0]
    assert h.scalar(admin,
        "SELECT outliers_excluded FROM public.canonical_market WHERE event_id=%s AND selection='DAL'",
        (ev,)) == 0, "removed an outlier with only 3 books"

    ev2 = event(admin, "T18-five", starts_in="5 hours")
    for bk, ph, pa in [("bookA", -150, 130), ("bookB", -155, 135), ("bookC", -148, 128),
                       ("bookD", -152, 132), ("bookWILD", -2000, 1500)]:
        two_sided(admin, ev2, "MONEYLINE", None, ph, pa, bk)
    excl = h.scalar(admin,
        "SELECT outliers_excluded FROM public.canonical_market WHERE event_id=%s AND selection='DAL'",
        (ev2,))
    assert excl == 1, f"expected one outlier removed at 5 books, got {excl}"
    admin.close()
    return "3 books -> 0 removed, 5 books -> 1 removed"


# =============================================================================
# Execution gating -- parity with place_ticket_rpc
# =============================================================================

def t19_every_executable_snapshot_is_accepted_by_the_rpc():
    """The surface and the RPC cannot be allowed to disagree."""
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T19")
    for bk, ph, pa in [("bookA", -110, -110), ("bookB", -105, -115), ("bookC", 100, -120)]:
        two_sided(admin, ev, "SPREAD", -3.0, ph, pa, bk)
        two_sided(admin, ev, "MONEYLINE", None, -150 - len(bk), 130, bk)

    snaps = [r[0] for r in h.rows(admin,
        "SELECT executable_snapshot_id FROM public.market_intelligence WHERE is_executable")]
    assert len(snaps) >= 4, snaps

    u = new_user(admin, "exec_parity"); ch = open_chapter(u)
    for s in snaps:
        assert place(u, ch, s, 50) is not None, f"RPC refused an executable snapshot {s}"
    admin.close()
    return f"{len(snaps)} executable snapshots, all accepted"


def t20_superseded_snapshot_never_executable():
    """A book that moved off a line must vanish from that line's executable row,
    because place_ticket_rpc's freshness check ignores line."""
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T20")
    two_sided(admin, ev, "SPREAD", -3.0, -110, -110, "bookA")
    two_sided(admin, ev, "SPREAD", -3.0, -112, -108, "bookB")
    two_sided(admin, ev, "SPREAD", -3.0, -102, -118, "bookMOVER", age=40)  # best, but old line
    two_sided(admin, ev, "SPREAD", -3.5, -102, -118, "bookMOVER", age=0)    # moved here

    canon = h.row(admin, """SELECT best_price, best_book, book_count FROM public.canonical_market
                            WHERE event_id=%s AND selection='DAL' AND line=-3.0""", (ev,))
    assert canon[0] == -102 and canon[1] == "bookMOVER", canon

    ex = h.row(admin, """SELECT best_price, best_book, executable_book_count
                         FROM public.executable_market
                         WHERE event_id=%s AND selection='DAL' AND line=-3.0""", (ev,))
    assert ex is not None, "the -3.0 row should still be executable via the other books"
    assert ex[1] != "bookMOVER", f"a superseded book was offered: {ex}"
    assert ex[2] == 2, ex

    u = new_user(admin, "superseded"); ch = open_chapter(u)
    stale_snap = h.scalar(admin, """SELECT id FROM public.market_snapshots
        WHERE event_id=%s AND sportsbook='bookMOVER' AND line=-3.0 AND selection='DAL'""", (ev,))
    h.expect_error(lambda: place(u, ch, stale_snap, 50), "MARKET_MOVED", "T20 parity")
    assert place(u, ch, h.scalar(admin, """SELECT executable_snapshot_id
        FROM public.market_intelligence WHERE event_id=%s AND selection='DAL'
          AND line=-3.0 AND is_executable""", (ev,)), 50) is not None
    admin.close()
    return "canonical offers bookMOVER, executable correctly does not"


def t21_live_event_absent_from_executable():
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T21")
    for bk, ph, pa in [("bookA", -110, -110), ("bookB", -105, -115)]:
        two_sided(admin, ev, "SPREAD", -3.0, ph, pa, bk)
    assert h.scalar(admin,
        "SELECT count(*) FROM public.executable_market WHERE event_id=%s", (ev,)) > 0

    with h.connect_as("service_role") as svc:
        h.scalar(svc, "SELECT public.mark_event_live_rpc(%s, NULL, 'TEST')", (ev,))

    assert h.scalar(admin,
        "SELECT count(*) FROM public.executable_market WHERE event_id=%s", (ev,)) == 0, \
        "a live event stayed executable"
    admin.close()


def t22_opening_equals_current_on_a_single_observation_slate():
    """Guards the one duplicated pipeline: market_movement re-implements the
    partner rule, so on a slate with exactly one observation per book the two
    must agree exactly."""
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T22")
    for bk in ("bookA", "bookB", "bookC"):
        two_sided(admin, ev, "MONEYLINE", None, -150, 130, bk)
    r = h.row(admin, """SELECT opening_probability, current_probability,
                               probability_movement, movement_direction
                        FROM public.market_movement
                        WHERE event_id=%s AND selection='DAL'""", (ev,))
    assert r[0] == r[1], f"opening and current pipelines disagree: {r}"
    assert float(r[2]) == 0.0 and r[3] == "FLAT", r
    admin.close()
    return "both pipelines agree exactly"



# =============================================================================
# Line movement vs price movement (pre-registered as P4-T09; the modal-invariance
# additions shifted the numbering, so it lands here)
# =============================================================================

def t23_line_movement_is_not_price_movement():
    """A pure -3 -> -3.5 line move must show up as line_movement and NOT as
    probability_movement on either line. Four books open at -3; three move to
    -3.5 at identical prices, so nothing about the PRICE changed."""
    admin = h.connect(); h.reset(admin)
    ev = event(admin, "T23")
    books = ["bookA", "bookB", "bookC", "bookD"]
    for bk in books:                                   # opening: 4 books at -3.0
        two_sided(admin, ev, "SPREAD", -3.0, -110, -110, bk, age=400)
    for bk in books[:3]:                               # 3 of them move to -3.5
        two_sided(admin, ev, "SPREAD", -3.5, -110, -110, bk, age=0)

    mv = {(r[0], float(r[1])): r for r in h.rows(admin, """
        SELECT selection, line, opening_modal_line, modal_line, line_movement,
               probability_movement, movement_direction
        FROM public.market_movement WHERE event_id = %s AND market_type='SPREAD'
        ORDER BY selection, line""", (ev,))}

    # The stale -3.0 rows are gone from the canonical surface; only -3.5 remains.
    assert set(mv) == {("DAL", -3.5), ("PHI", 3.5)}, sorted(mv)

    dal, phi = mv[("DAL", -3.5)], mv[("PHI", 3.5)]
    assert float(dal[2]) == -3.0 and float(dal[3]) == -3.5, dal
    assert float(dal[4]) == -0.5, f"DAL line movement not reported: {dal}"
    assert float(phi[4]) == 0.5, f"PHI line movement did not mirror: {phi}"

    # ...and the price did not move, on either side.
    for r in (dal, phi):
        assert float(r[5]) == 0, f"a pure line move leaked into price: {r}"
        assert r[6] == "FLAT", r
    admin.close()
    return "-3.0 -> -3.5 read as line_movement -0.5/+0.5, price FLAT"


# =============================================================================
# Live-shape (pre-registered as P4-T21)
# =============================================================================

def t24_live_shape_row_identity_and_bounded_time():
    """At the shape of a real slate, the canonical surface must contain exactly
    one row per distinct fresh (event, market, selection, line) -- no dropped
    rows, no duplicates from the LATERAL partner or the modal join -- and the
    query a model actually issues must stay fast.

    NOTE for anyone benchmarking this by hand: the slate is seeded at NOW() and
    ages out of snapshot_ttl_seconds while you measure. Run the identity check
    first and keep the whole measurement inside the TTL window, or the views
    correctly return nothing and it looks like a bug.
    """
    import time
    admin = h.connect(); h.reset(admin)

    admin.execute("""
        SELECT olp_test.create_event('LS-'||g, 'H'||g, 'A'||g, INTERVAL '4 hours')
        FROM generate_series(1, 272) g""")

    # Books per event 2..5, and 2 lines per selection on two thirds of events --
    # the fragmentation the live census measured (64.3% spread / 79.4% total).
    admin.execute("""
        WITH ev AS (
            SELECT id, home_team, away_team,
                   substring(source_event_id from 4)::int AS seq
            FROM public.events
        ),
        spec AS (
            SELECT e.id, e.seq, m.mt, sel.s, sel.is_home, b.n,
                   (b.n % (CASE WHEN e.seq % 3 = 0 THEN 1 ELSE 2 END)) AS frag
            FROM ev e
            CROSS JOIN LATERAL (VALUES ('MONEYLINE'::public.market_type),
                                       ('SPREAD'), ('TOTAL')) m(mt)
            CROSS JOIN LATERAL (
                SELECT CASE WHEN m.mt = 'TOTAL' THEN 'OVER' ELSE e.home_team END, TRUE
                UNION ALL
                SELECT CASE WHEN m.mt = 'TOTAL' THEN 'UNDER' ELSE e.away_team END, FALSE
            ) sel(s, is_home)
            CROSS JOIN LATERAL generate_series(1, 2 + (e.seq % 4)) b(n)
        )
        INSERT INTO public.market_snapshots (
            event_id, market_type, selection, line, price,
            sportsbook, source_provider, captured_at, is_in_play)
        SELECT id, mt, s,
               CASE mt
                   WHEN 'MONEYLINE' THEN NULL
                   WHEN 'SPREAD' THEN (CASE WHEN is_home THEN -1 ELSE 1 END)
                                      * (3.0 + 0.5 * frag)
                   ELSE 44.5 + 0.5 * frag
               END,
               -110, 'book'||n, 'FIXTURE', NOW(), FALSE
        FROM spec""")
    admin.execute("ANALYZE public.market_snapshots")

    events = h.scalar(admin, "SELECT count(*) FROM public.events")
    quotes = h.scalar(admin, "SELECT count(*) FROM public.market_snapshots")
    assert events == 272 and quotes >= 4552, f"{events} events / {quotes} quotes"

    # The access pattern a model actually uses: one game, the full contract.
    ev = h.scalar(admin, "SELECT id FROM public.events ORDER BY source_event_id LIMIT 1")
    t0 = time.time()
    per_event = h.scalar(
        admin, "SELECT count(*) FROM public.market_intelligence WHERE event_id = %s", (ev,))
    single = time.time() - t0
    assert per_event > 0, "the per-event contract returned nothing"
    assert single < 5, f"single-event market_intelligence took {single:.1f}s"

    # Row identity: exactly one canonical row per distinct fresh key.
    t0 = time.time()
    canon = h.scalar(admin, "SELECT count(*) FROM public.canonical_market")
    board = time.time() - t0
    expected = h.scalar(admin, """
        SELECT count(*) FROM (
            SELECT DISTINCT s.event_id, s.market_type, s.selection, s.line
            FROM public.market_snapshots s
            JOIN public.events e ON e.id = s.event_id
            CROSS JOIN public.system_settings c
            WHERE c.id AND s.is_in_play = FALSE AND e.is_closed = FALSE
              AND s.captured_at <= NOW()
              AND NOW() - s.captured_at
                  <= make_interval(secs => c.snapshot_ttl_seconds)) d""")
    assert canon == expected, f"canonical {canon} != distinct keys {expected}"
    admin.close()
    return (f"{events} events / {quotes} quotes -> {canon} rows; "
            f"one event {single*1000:.0f}ms, full board {board:.1f}s")


PACKAGE4 = [
    ("P4-T01", "Different lines are separate rows", t01_different_lines_are_separate_rows),
    ("P4-T02", "Best price never crosses lines", t02_best_price_never_crosses_lines),
    ("P4-T03", "Consensus uses only same-line books", t03_consensus_uses_only_same_line_books),
    ("P4-T04", "Totals do not merge across lines", t04_totals_do_not_merge_across_lines),
    ("P4-T05", "Moneyline NULL line groups correctly", t05_moneyline_null_line_groups_correctly),
    ("P4-T06", "Cross-line leakage canary is detectable", t06_leakage_canary_is_detectable),
    ("P4-T07", "Spread pairs at negated line only", t07_spread_pairs_at_negated_line_only),
    ("P4-T08", "Totals pair at same line only", t08_totals_pair_at_same_line_only),
    ("P4-T09", "Modal invariant to order and recency", t09_modal_invariant_to_order_and_recency),
    ("P4-T10", "Modal reflects book count, not line value", t10_modal_reflects_book_count_not_line_value),
    ("P4-T11", "Probability round-trip within quantisation", t11_probability_round_trip_within_quantisation),
    ("P4-T12", "De-vig sums to one, hand-checkable", t12_devig_sums_to_one_and_is_hand_checkable),
    ("P4-T13", "Best price uses payout, not numeric order", t13_best_price_uses_payout_not_numeric_order),
    ("P4-T14", "Median resists a single extreme book", t14_median_resists_a_single_extreme_book),
    ("P4-T15", "Reason codes accumulate and are reachable", t15_reason_codes_accumulate_and_are_reachable),
    ("P4-T16", "Single book fails closed", t16_single_book_fails_closed),
    ("P4-T17", "Stale books excluded on the shared TTL", t17_stale_books_excluded_on_the_shared_ttl),
    ("P4-T18", "Outliers only removed with enough books", t18_outliers_only_removed_with_enough_books),
    ("P4-T19", "Every executable snapshot accepted by the RPC", t19_every_executable_snapshot_is_accepted_by_the_rpc),
    ("P4-T20", "Superseded snapshot never executable", t20_superseded_snapshot_never_executable),
    ("P4-T21", "Live event absent from executable", t21_live_event_absent_from_executable),
    ("P4-T22", "Opening equals current on single-observation slate", t22_opening_equals_current_on_a_single_observation_slate),
    ("P4-T23", "Line movement is not price movement", t23_line_movement_is_not_price_movement),
    ("P4-T24", "Live-shape row identity and bounded time", t24_live_shape_row_identity_and_bounded_time),
]
