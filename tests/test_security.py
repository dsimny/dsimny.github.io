"""OLP-M1 security tests (section 35), plus M1-T29 / M1-T30."""

import uuid
import harness as h
from test_acceptance import seed, place, new_user, open_chapter


# =============================================================================
# SEC-T01 -- User A's chapter is not reachable by User B
# =============================================================================

def sec_t01_cross_user_placement_rejected():
    admin = h.connect()
    h.reset(admin)
    ua, cha, eva, snapa = seed(admin, "victim")
    ub = new_user(admin, "attacker")
    open_chapter(ub)

    h.expect_error(lambda: place(ub, cha, snapa, 500),
                   "CHAPTER_NOT_AVAILABLE", "SEC-T01")

    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 0
    assert h.scalar(admin, "SELECT count(*) FROM public.risk_reservations") == 0
    admin.close()


# =============================================================================
# SEC-T02 -- direct wallet_transactions INSERT  (also M1-T29)
# =============================================================================

def sec_t02_client_cannot_write_wallet_transaction():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    def direct_insert():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                """INSERT INTO public.wallet_transactions
                   (user_id, chapter_id, transaction_type, amount, idempotency_key)
                   VALUES (%s, %s, 'SETTLEMENT_WIN', 999999, gen_random_uuid())""",
                (u, ch))
    h.expect_error(direct_insert, "permission denied", "SEC-T02 insert")

    def direct_update():
        with h.connect_as("authenticated", u) as c:
            c.execute("UPDATE public.wallet_transactions SET amount = 999999")
    h.expect_error(direct_update, "permission denied", "SEC-T02 update")

    def direct_delete():
        with h.connect_as("authenticated", u) as c:
            c.execute("DELETE FROM public.wallet_transactions")
    h.expect_error(direct_delete, "permission denied", "SEC-T02 delete")

    assert h.balances(admin, ch) == (10000, 0, 10000)
    admin.close()


# =============================================================================
# SEC-T03 -- direct ticket_results INSERT
# =============================================================================

def sec_t03_client_cannot_write_ticket_result():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)
    ticket = place(u, ch, snap, 1000)

    def direct_insert():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                """INSERT INTO public.ticket_results
                   (ticket_id, result, pnl, grading_source, settlement_idempotency_key)
                   VALUES (%s, 'WIN', 999999, 'self', gen_random_uuid())""", (ticket,))
    h.expect_error(direct_insert, "permission denied", "SEC-T03 insert")

    def direct_adjustment():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                """INSERT INTO public.ticket_result_adjustments
                   (ticket_id, previous_effective_result, new_effective_result,
                    pnl_delta, reason_code, correction_idempotency_key, created_by)
                   VALUES (%s,'LOSS','WIN',999999,'self',gen_random_uuid(),'me')""",
                (ticket,))
    h.expect_error(direct_adjustment, "permission denied", "SEC-T03 adjustment")

    assert h.scalar(admin, "SELECT count(*) FROM public.ticket_results") == 0
    admin.close()


# =============================================================================
# SEC-T04 -- privileged RPCs are not reachable by `authenticated`
# =============================================================================

def sec_t04_client_cannot_call_privileged_rpcs():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)
    ticket = place(u, ch, snap, 1000)

    def call_settle():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                "SELECT public.settle_ticket_rpc(%s,'WIN'::public.ticket_result_type,'self',%s)",
                (ticket, str(uuid.uuid4())))
    h.expect_error(call_settle, "permission denied", "SEC-T04 settle")

    def call_correction():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                """SELECT public.apply_settlement_correction_rpc(
                       %s,'WIN'::public.ticket_result_type,'self','x',%s,'me')""",
                (ticket, str(uuid.uuid4())))
    h.expect_error(call_correction, "permission denied", "SEC-T04 correction")

    assert h.scalar(admin, "SELECT count(*) FROM public.ticket_results") == 0
    admin.close()


# =============================================================================
# SEC-T05 -- anonymous callers
# =============================================================================

def sec_t05_anonymous_cannot_place():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    def anon_place():
        with h.connect_as("anon") as c:
            c.execute("SELECT public.place_ticket_rpc(%s,%s,%s,%s)",
                      (ch, snap, 1000, str(uuid.uuid4())))
    h.expect_error(anon_place, "permission denied", "SEC-T05 anon place")

    def anon_open():
        with h.connect_as("anon") as c:
            c.execute("SELECT public.open_chapter_rpc()")
    h.expect_error(anon_open, "permission denied", "SEC-T05 anon open")

    # An `authenticated` connection carrying no JWT subject is refused by the
    # RPC itself rather than being treated as some default user.
    def no_jwt():
        with h.connect_as("authenticated", None) as c:
            c.execute("SELECT public.place_ticket_rpc(%s,%s,%s,%s)",
                      (ch, snap, 1000, str(uuid.uuid4())))
    h.expect_error(no_jwt, "AUTH_REQUIRED", "SEC-T05 missing jwt")

    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 0
    admin.close()


# =============================================================================
# M1-T30 -- client cannot directly create an accepted ticket
# =============================================================================

def t30_client_cannot_create_ticket():
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)

    def direct_ticket():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                """INSERT INTO public.tickets
                   (user_id, chapter_id, event_id, market_snapshot_id, market_type,
                    selection, accepted_line, accepted_price, accepted_sportsbook,
                    snapshot_captured_at, risk, potential_profit,
                    submission_idempotency_key, status)
                   VALUES (%s,%s,%s,%s,'SPREAD','DAL',-3,-110,'TESTBOOK',NOW(),
                           1000, 999999, gen_random_uuid(), 'ACCEPTED')""",
                (u, ch, ev, snap))
    h.expect_error(direct_ticket, "permission denied", "M1-T30 insert")

    def direct_reservation():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                """INSERT INTO public.risk_reservations (ticket_id, chapter_id, amount)
                   VALUES (gen_random_uuid(), %s, 1)""", (ch,))
    h.expect_error(direct_reservation, "permission denied", "M1-T30 reservation")

    # Nor can a user mutate their own chapter's economics or status.
    def direct_chapter():
        with h.connect_as("authenticated", u) as c:
            c.execute(
                "UPDATE public.ledger_chapters SET starting_capital = 999999 WHERE id = %s",
                (ch,))
    h.expect_error(direct_chapter, "permission denied", "M1-T30 chapter")

    assert h.scalar(admin, "SELECT count(*) FROM public.tickets") == 0
    admin.close()


# =============================================================================
# Append-only enforcement survives even a trusted-role mistake
# =============================================================================

def sec_append_only_holds_for_service_role():
    """Defence in depth: grants stop service_role, triggers stop the owner.

    Layer 1 -- service_role holds no write privilege on ledger tables at all,
    so it cannot even attempt the mutation.
    Layer 2 -- the table OWNER, which no grant can restrain, is still refused by
    the append-only triggers. This is the layer that matters if a future
    migration or an admin session tries to rewrite settled history.
    """
    admin = h.connect()
    h.reset(admin)
    u, ch, ev, snap = seed(admin)
    ticket = place(u, ch, snap, 1000)

    with h.connect_as("service_role") as c:
        h.scalar(c,
            "SELECT public.settle_ticket_rpc(%s,'WIN'::public.ticket_result_type,'g',%s)",
            (ticket, str(uuid.uuid4())))

    # ---- Layer 1: service_role has no direct write on ledger tables ----------
    for sql, params, label in [
        ("UPDATE public.ticket_results SET result = 'LOSS' WHERE ticket_id = %s",
         (ticket,), "service_role result update"),
        ("DELETE FROM public.wallet_transactions WHERE ticket_id = %s",
         (ticket,), "service_role wallet delete"),
        ("UPDATE public.tickets SET risk = 1 WHERE id = %s",
         (ticket,), "service_role ticket edit"),
        ("UPDATE public.ledger_chapters SET starting_capital = 1 WHERE id = %s",
         (ch,), "service_role chapter edit"),
    ]:
        def attempt(s=sql, p=params):
            with h.connect_as("service_role") as c:
                c.execute(s, p)
        h.expect_error(attempt, "permission denied", label)

    # ---- Layer 2: the owner itself is refused by the triggers ---------------
    def owner_update_result():
        with h.connect() as owner:
            owner.execute("UPDATE public.ticket_results SET result = 'LOSS' WHERE ticket_id = %s",
                          (ticket,))
    h.expect_error(owner_update_result, "APPEND_ONLY_VIOLATION", "owner result update")

    def owner_delete_wallet():
        with h.connect() as owner:
            owner.execute("DELETE FROM public.wallet_transactions WHERE ticket_id = %s", (ticket,))
    h.expect_error(owner_delete_wallet, "APPEND_ONLY_VIOLATION", "owner wallet delete")

    def owner_edit_economics():
        with h.connect() as owner:
            owner.execute("UPDATE public.tickets SET risk = 1 WHERE id = %s", (ticket,))
    h.expect_error(owner_edit_economics, "IMMUTABLE_TICKET", "owner ticket edit")

    def owner_delete_adjustmentless_result():
        with h.connect() as owner:
            owner.execute("DELETE FROM public.ticket_results WHERE ticket_id = %s", (ticket,))
    h.expect_error(owner_delete_adjustmentless_result, "APPEND_ONLY_VIOLATION", "owner result delete")

    # service_role DOES ingest market data -- so here the trigger, not the
    # grant, is what protects the quote history.
    def svc_rewrite_snapshot():
        with h.connect_as("service_role") as c:
            c.execute("UPDATE public.market_snapshots SET price = -101 WHERE id = %s", (snap,))
    h.expect_error(svc_rewrite_snapshot, "IMMUTABLE_SNAPSHOT", "service_role snapshot edit")

    # The original settlement is intact after all of that.
    assert h.row(admin,
        "SELECT result, pnl FROM public.ticket_results WHERE ticket_id = %s",
        (ticket,)) == ("WIN", 909.09)
    admin.close()


def sec_views_inherit_rls():
    """The derived views must not become a read side-channel around RLS."""
    admin = h.connect()
    h.reset(admin)
    ua, cha, eva, snapa = seed(admin, "alice_rls")
    ub = new_user(admin, "bob_rls")
    chb = open_chapter(ub)

    ticket = place(ua, cha, snapa, 1000)
    with h.connect_as("service_role") as svc:
        h.scalar(svc,
            "SELECT public.settle_ticket_rpc(%s,'WIN'::public.ticket_result_type,'g',%s)",
            (ticket, str(uuid.uuid4())))

    with h.connect_as("authenticated", ub) as bob:
        # Bob sees exactly one chapter -- his own -- and none of Alice's rows.
        assert h.scalar(bob, "SELECT count(*) FROM public.chapter_balances") == 1
        assert h.scalar(bob,
            "SELECT chapter_id FROM public.chapter_balances") == chb
        assert h.scalar(bob,
            "SELECT count(*) FROM public.chapter_balances WHERE chapter_id = %s",
            (cha,)) == 0
        assert h.scalar(bob, "SELECT count(*) FROM public.ticket_effective_results") == 0
        assert h.scalar(bob, "SELECT count(*) FROM public.tickets") == 0
        assert h.scalar(bob, "SELECT count(*) FROM public.ticket_results") == 0
        # His only wallet row is his own chapter's opening credit.
        wallet = h.rows(bob, "SELECT user_id, transaction_type FROM public.wallet_transactions")
        assert wallet == [(ub, "CHAPTER_OPEN")], wallet

    with h.connect_as("authenticated", ua) as alice:
        assert h.scalar(alice, "SELECT count(*) FROM public.chapter_balances") == 1
        assert h.row(alice,
            "SELECT effective_result, effective_pnl FROM public.ticket_effective_results"
        ) == ("WIN", 909.09)

    # anon reaches none of it at all.
    for obj in ("public.tickets", "public.wallet_transactions",
                "public.ticket_results", "public.chapter_balances",
                "public.ticket_effective_results"):
        def read(o=obj):
            with h.connect_as("anon") as c:
                c.execute(f"SELECT count(*) FROM {o}")
        h.expect_error(read, "permission denied", f"anon read {obj}")
    admin.close()


SECURITY = [
    ("SEC-T01", "User A cannot place against User B's chapter", sec_t01_cross_user_placement_rejected),
    ("SEC-T02", "Authenticated INSERT wallet_transactions denied", sec_t02_client_cannot_write_wallet_transaction),
    ("SEC-T03", "Authenticated INSERT ticket_results denied", sec_t03_client_cannot_write_ticket_result),
    ("SEC-T04", "Authenticated call to settle_ticket_rpc denied", sec_t04_client_cannot_call_privileged_rpcs),
    ("SEC-T05", "Anonymous placement requires authentication", sec_t05_anonymous_cannot_place),
]

SECURITY_EXTRA = [
    ("M1-T29", "Client cannot directly write wallet transaction", sec_t02_client_cannot_write_wallet_transaction),
    ("M1-T30", "Client cannot directly create accepted ticket", t30_client_cannot_create_ticket),
    ("SEC-X01", "Append-only holds even for service_role", sec_append_only_holds_for_service_role),
    ("SEC-X02", "Derived views inherit RLS (no read side-channel)", sec_views_inherit_rls),
]
