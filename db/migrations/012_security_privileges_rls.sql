-- =============================================================================
-- 012_security_privileges_rls.sql -- Authorization boundary
-- =============================================================================
-- Principle: users may READ permitted ledger information but may never directly
-- author financial truth. All writes flow through SECURITY DEFINER RPCs.
--
-- Supabase ships permissive default grants to anon/authenticated on public
-- objects, so every mutation privilege is REVOKED explicitly rather than
-- assumed absent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Strip every direct mutation privilege on ledger-critical tables.
-- -----------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.tickets                    FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.risk_reservations          FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.wallet_transactions        FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.ticket_results             FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.ticket_result_adjustments  FROM anon, authenticated;

-- Chapter economics/status are equally off-limits to direct authenticated writes.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.ledger_chapters            FROM anon, authenticated;

-- Market + reference data is read-only to the API roles.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.events                     FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.event_schedule_history     FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.market_snapshots           FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.system_settings            FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.users                      FROM anon, authenticated;

-- Belt and braces: the same tables must not be writable via PUBLIC either.
REVOKE ALL ON public.tickets                   FROM PUBLIC;
REVOKE ALL ON public.risk_reservations         FROM PUBLIC;
REVOKE ALL ON public.wallet_transactions       FROM PUBLIC;
REVOKE ALL ON public.ticket_results            FROM PUBLIC;
REVOKE ALL ON public.ticket_result_adjustments FROM PUBLIC;
REVOKE ALL ON public.ledger_chapters           FROM PUBLIC;
REVOKE ALL ON public.events                    FROM PUBLIC;
REVOKE ALL ON public.event_schedule_history    FROM PUBLIC;
REVOKE ALL ON public.market_snapshots          FROM PUBLIC;
REVOKE ALL ON public.system_settings           FROM PUBLIC;
REVOKE ALL ON public.users                     FROM PUBLIC;

-- No client-side sequence poking (ingest_seq / adjustment_seq ordering).
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, PUBLIC;

-- -----------------------------------------------------------------------------
-- 2. Grant back READ ONLY.
-- -----------------------------------------------------------------------------
GRANT SELECT ON public.users                     TO authenticated;
GRANT SELECT ON public.ledger_chapters           TO authenticated;
GRANT SELECT ON public.wallet_transactions       TO authenticated;
GRANT SELECT ON public.tickets                   TO authenticated;
GRANT SELECT ON public.risk_reservations         TO authenticated;
GRANT SELECT ON public.ticket_results            TO authenticated;
GRANT SELECT ON public.ticket_result_adjustments TO authenticated;
GRANT SELECT ON public.system_settings           TO authenticated;

-- Public market data.
GRANT SELECT ON public.events                 TO anon, authenticated;
GRANT SELECT ON public.event_schedule_history TO anon, authenticated;
GRANT SELECT ON public.market_snapshots       TO anon, authenticated;

-- -----------------------------------------------------------------------------
-- 2b. service_role -- stated explicitly, never inherited.
--
-- Supabase's platform defaults (ALTER DEFAULT PRIVILEGES ... GRANT ALL ... TO
-- service_role) may or may not apply to these tables depending on how the
-- schema was created. Relying on that produces a different privilege set in
-- different environments, so the trusted-backend grants are revoked to a known
-- state and then granted deliberately.
--
-- The rule: service_role INGESTS MARKET DATA and READS the ledger. It does not
-- write financial truth directly -- settlement and corrections go through the
-- SECURITY DEFINER RPCs, which work regardless of these table grants.
-- -----------------------------------------------------------------------------
REVOKE ALL ON public.tickets                   FROM service_role;
REVOKE ALL ON public.risk_reservations         FROM service_role;
REVOKE ALL ON public.wallet_transactions       FROM service_role;
REVOKE ALL ON public.ticket_results            FROM service_role;
REVOKE ALL ON public.ticket_result_adjustments FROM service_role;
REVOKE ALL ON public.ledger_chapters           FROM service_role;
REVOKE ALL ON public.users                     FROM service_role;
REVOKE ALL ON public.events                    FROM service_role;
REVOKE ALL ON public.event_schedule_history    FROM service_role;
REVOKE ALL ON public.market_snapshots          FROM service_role;
REVOKE ALL ON public.system_settings           FROM service_role;

-- Ledger: READ ONLY.
GRANT SELECT ON public.users                     TO service_role;
GRANT SELECT ON public.ledger_chapters           TO service_role;
GRANT SELECT ON public.wallet_transactions       TO service_role;
GRANT SELECT ON public.tickets                   TO service_role;
GRANT SELECT ON public.risk_reservations         TO service_role;
GRANT SELECT ON public.ticket_results            TO service_role;
GRANT SELECT ON public.ticket_result_adjustments TO service_role;

-- Market data + event lifecycle: the trusted backend owns these (Package #2).
-- market_snapshots UPDATE exists solely so a pre-game quote can be flagged as
-- the closing line; the immutability trigger constrains what may actually move.
GRANT SELECT, INSERT, UPDATE ON public.events              TO service_role;
GRANT SELECT, INSERT         ON public.event_schedule_history TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.market_snapshots    TO service_role;
GRANT SELECT, UPDATE         ON public.system_settings     TO service_role;

-- -----------------------------------------------------------------------------
-- 3. Row Level Security -- read visibility only.
-- -----------------------------------------------------------------------------
ALTER TABLE public.users                     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_chapters           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wallet_transactions       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tickets                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.risk_reservations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ticket_results            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ticket_result_adjustments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.event_schedule_history    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_snapshots          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.system_settings           ENABLE ROW LEVEL SECURITY;

-- NOTE: FORCE ROW LEVEL SECURITY is deliberately NOT set. The table owner runs
-- the SECURITY DEFINER RPCs and must be able to write settlement rows for any
-- user. Authorization for those paths is enforced by EXECUTE grants + auth.uid().

CREATE POLICY p_users_select_self ON public.users
    FOR SELECT TO authenticated
    USING (id = auth.uid());

CREATE POLICY p_chapters_select_own ON public.ledger_chapters
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY p_wallet_select_own ON public.wallet_transactions
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY p_tickets_select_own ON public.tickets
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY p_reservations_select_own ON public.risk_reservations
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.tickets t
        WHERE t.id = risk_reservations.ticket_id
          AND t.user_id = auth.uid()
    ));

CREATE POLICY p_results_select_own ON public.ticket_results
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.tickets t
        WHERE t.id = ticket_results.ticket_id
          AND t.user_id = auth.uid()
    ));

CREATE POLICY p_adjustments_select_own ON public.ticket_result_adjustments
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.tickets t
        WHERE t.id = ticket_result_adjustments.ticket_id
          AND t.user_id = auth.uid()
    ));

-- Market data is public reference information.
CREATE POLICY p_events_read ON public.events
    FOR SELECT TO anon, authenticated USING (TRUE);

CREATE POLICY p_schedule_history_read ON public.event_schedule_history
    FOR SELECT TO anon, authenticated USING (TRUE);

CREATE POLICY p_snapshots_read ON public.market_snapshots
    FOR SELECT TO anon, authenticated USING (TRUE);

CREATE POLICY p_settings_read ON public.system_settings
    FOR SELECT TO authenticated USING (TRUE);
