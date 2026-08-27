-- =============================================================================
-- 035_p3_fixtures.sql -- Package #3 fixture support (development only)
-- =============================================================================
-- provider_health is DURABLE BY DESIGN: an open circuit has to survive a worker
-- restart or the breaker is decorative. That same durability leaks state across
-- tests -- a circuit tripped by an outage test stays open and silently skips
-- every cycle in the tests that follow, which reads as unrelated failures.
--
-- The reset must therefore clear it explicitly.
-- =============================================================================

CREATE OR REPLACE FUNCTION olp_test.reset()
RETURNS VOID
LANGUAGE plpgsql
AS $fn$
BEGIN
    TRUNCATE
        public.provider_health,
        public.event_lifecycle_log,
        public.ingestion_runs,
        public.ticket_result_adjustments,
        public.ticket_results,
        public.risk_reservations,
        public.wallet_transactions,
        public.tickets,
        public.market_snapshots,
        public.event_schedule_history,
        public.events,
        public.ledger_chapters,
        public.users
    RESTART IDENTITY CASCADE;

    -- auth.users is never truncated: on Supabase it is owned by
    -- supabase_auth_admin and CASCADE would reach auth.refresh_tokens, whose
    -- sequence `postgres` does not own. Scoped DELETE instead.
    DELETE FROM auth.users WHERE email LIKE '%@olp.test';

    -- Restore shipped policy defaults in case a test tuned them.
    UPDATE public.system_settings
       SET snapshot_ttl_seconds     = 120,
           snapshot_refresh_seconds = 60,
           max_ticket_fraction      = 0.1000,
           min_viable_wager         = 100.00,
           default_starting_capital = 10000.00,
           postponement_void_hours  = 48
     WHERE id = TRUE;
END;
$fn$;
