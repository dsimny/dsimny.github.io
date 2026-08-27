# OLP-M1 Package #1 — Rollback Notes

Migrations 001–019 are additive and were authored to be reversed in strict
reverse order. Nothing in this package performs a destructive change to
pre-existing data, so a rollback is a teardown rather than a data migration.

**Before rolling back anything in 003–010 on a live database:** those tables
hold financial history. Dropping them destroys the ledger. Take a verified dump
first. In practice only 011–019 (functions, views, grants, fixtures) should ever
be rolled back in an environment that has real tickets in it.

## Reversibility at a glance

| Migration | Reversible | Data loss on rollback |
|---|---|---|
| 001 extensions + enums + settings | yes | `system_settings` row |
| 002 users | yes | all profiles |
| 003 ledger_chapters | yes | **all chapters** |
| 004 wallet_transactions | yes | **entire balance history** |
| 005 events + schedule history | yes | events + audit trail |
| 006 market_snapshots | yes | quote history |
| 007 tickets | yes | **all tickets** |
| 008 risk_reservations | yes | escrow records |
| 009 ticket_results | yes | **all settlements** |
| 010 result adjustments | yes | correction audit |
| 011 wallet FK + helpers | yes | none |
| 012 privileges + RLS | yes | none (see caution) |
| 013–017 RPCs | yes | none |
| 018 views | yes | none |
| 019 fixtures | yes | test data only |

## Per-migration rollback

```sql
-- 019
DROP SCHEMA IF EXISTS olp_test CASCADE;

-- 018
DROP VIEW IF EXISTS public.ticket_effective_results;
DROP VIEW IF EXISTS public.chapter_balances;

-- 017
DROP FUNCTION IF EXISTS public.declare_bankruptcy_rpc();

-- 016
DROP FUNCTION IF EXISTS public.apply_settlement_correction_rpc(
    UUID, public.ticket_result_type, TEXT, TEXT, UUID, TEXT);

-- 015
DROP FUNCTION IF EXISTS public.settle_ticket_rpc(
    UUID, public.ticket_result_type, TEXT, UUID);

-- 014
DROP FUNCTION IF EXISTS public.place_ticket_rpc(UUID, UUID, NUMERIC, UUID);

-- 013
DROP FUNCTION IF EXISTS public.open_chapter_rpc();
```

### 012 — privileges and RLS

CAUTION: rolling this back removes the authorization boundary. Only do so as
part of a full teardown, never on a live database that still serves clients.

```sql
DROP POLICY IF EXISTS p_settings_read           ON public.system_settings;
DROP POLICY IF EXISTS p_snapshots_read          ON public.market_snapshots;
DROP POLICY IF EXISTS p_schedule_history_read   ON public.event_schedule_history;
DROP POLICY IF EXISTS p_events_read             ON public.events;
DROP POLICY IF EXISTS p_adjustments_select_own  ON public.ticket_result_adjustments;
DROP POLICY IF EXISTS p_results_select_own      ON public.ticket_results;
DROP POLICY IF EXISTS p_reservations_select_own ON public.risk_reservations;
DROP POLICY IF EXISTS p_tickets_select_own      ON public.tickets;
DROP POLICY IF EXISTS p_wallet_select_own       ON public.wallet_transactions;
DROP POLICY IF EXISTS p_chapters_select_own     ON public.ledger_chapters;
DROP POLICY IF EXISTS p_users_select_self       ON public.users;

ALTER TABLE public.system_settings           DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_snapshots          DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.event_schedule_history    DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.events                    DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.ticket_result_adjustments DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.ticket_results            DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.risk_reservations         DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.tickets                   DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.wallet_transactions       DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_chapters           DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.users                     DISABLE ROW LEVEL SECURITY;
```

The REVOKEs in 012 are not automatically restored. If you need Supabase's
permissive defaults back, re-grant explicitly — do not assume.

### 011

```sql
DROP FUNCTION IF EXISTS public.olp_settlement_tx_type(public.ticket_result_type);
DROP FUNCTION IF EXISTS public.olp_result_pnl(public.ticket_result_type, NUMERIC, NUMERIC);
DROP FUNCTION IF EXISTS public.olp_american_profit(NUMERIC, INT);
ALTER TABLE public.wallet_transactions
    DROP CONSTRAINT IF EXISTS fk_wallet_transaction_ticket;
```

### 010 → 002 — table teardown

Drop in this order (reverse dependency). Every one of these destroys financial
history; see the caution at the top.

```sql
DROP TABLE IF EXISTS public.ticket_result_adjustments;
DROP TABLE IF EXISTS public.ticket_results;
DROP TABLE IF EXISTS public.risk_reservations;
DROP TABLE IF EXISTS public.tickets;              -- drop wallet FK first (011)
DROP TABLE IF EXISTS public.market_snapshots;
DROP TABLE IF EXISTS public.event_schedule_history;
DROP TABLE IF EXISTS public.events;
DROP TABLE IF EXISTS public.wallet_transactions;
DROP TABLE IF EXISTS public.ledger_chapters;
DROP TABLE IF EXISTS public.users;

DROP FUNCTION IF EXISTS public.olp_guard_reservation_update();
DROP FUNCTION IF EXISTS public.olp_freeze_ticket_economics();
DROP FUNCTION IF EXISTS public.olp_guard_snapshot_update();
DROP FUNCTION IF EXISTS public.olp_log_schedule_change();
DROP FUNCTION IF EXISTS public.olp_freeze_event_origin();
DROP FUNCTION IF EXISTS public.olp_block_mutation();
```

### 001

```sql
DROP TABLE IF EXISTS public.system_settings;
DROP TYPE IF EXISTS public.ticket_result_type;
DROP TYPE IF EXISTS public.market_type;
DROP TYPE IF EXISTS public.transaction_type;
DROP TYPE IF EXISTS public.reservation_status;
DROP TYPE IF EXISTS public.ticket_status;
DROP TYPE IF EXISTS public.chapter_status;
-- pgcrypto is intentionally NOT dropped: other schemas may depend on it.
```

## Forward-only alternatives

Two changes are worth making forward rather than by rollback:

- **Tuning policy numbers** (snapshot TTL, max ticket fraction, minimum viable
  wager, starting capital) is an `UPDATE public.system_settings` — no migration
  and no rollback needed.
- **Changing an RPC's behaviour** is a `CREATE OR REPLACE FUNCTION` in a new
  migration. All five RPCs are written as `CREATE OR REPLACE`, so replacing one
  never requires dropping it first and never disturbs its grants.


---

# Package #2 rollback (migrations 020–029)

Package #2 is additive and touches no financial table, so rolling it back
removes ingestion and lifecycle capability without endangering the ledger. The
only data lost is market/event provenance.

| Migration | Reversible | Data loss on rollback |
|---|---|---|
| 020 config + provenance | yes | ingestion runs, event lifecycle audit |
| 021–027 RPCs + privileges | yes | none |
| 028 views | yes | none |
| 029 fixtures | yes | test data only |

```sql
-- 029  (also restores the Package #1 reset(); re-apply 019 afterwards if you
--       still want fixtures)
DROP FUNCTION IF EXISTS olp_test.age_quote(UUID, public.market_type, TEXT, TEXT, INTERVAL);
DROP FUNCTION IF EXISTS olp_test.seed_slate(INT, INTERVAL);

-- 028
DROP VIEW IF EXISTS public.ticket_closing_line_value;
DROP VIEW IF EXISTS public.current_market_board;
DROP POLICY IF EXISTS p_settings_read_anon ON public.system_settings;
REVOKE SELECT ON public.system_settings FROM anon;

-- 027
DROP FUNCTION IF EXISTS public.finish_ingestion_run_rpc(UUID, public.ingestion_status, TEXT, INT);
DROP FUNCTION IF EXISTS public.start_ingestion_run_rpc(TEXT, public.ingestion_kind);

-- 026
DROP FUNCTION IF EXISTS public.cancel_event_rpc(UUID, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.close_event_rpc(UUID, TEXT);
DROP FUNCTION IF EXISTS public.mark_event_live_rpc(UUID, TIMESTAMPTZ, TEXT);

-- 025
DROP FUNCTION IF EXISTS public.capture_closing_line_rpc(UUID, TEXT);

-- 024
DROP FUNCTION IF EXISTS public.ingest_market_snapshots_rpc(JSONB, UUID);
DROP FUNCTION IF EXISTS public.ingest_market_snapshot_rpc(
    UUID, public.market_type, TEXT, NUMERIC, INT, TEXT, TEXT, TIMESTAMPTZ, BOOLEAN);

-- 023
DROP FUNCTION IF EXISTS public.ingest_event_rpc(TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, TEXT, TEXT);

-- 022
DROP FUNCTION IF EXISTS public.reschedule_event_rpc(UUID, TIMESTAMPTZ, TEXT, TEXT);

-- 021
DROP FUNCTION IF EXISTS public.void_event_tickets_rpc(UUID, TEXT, TEXT);

-- 020
DROP FUNCTION IF EXISTS public.olp_log_lifecycle(UUID, public.event_lifecycle_action, TEXT, JSONB);
DROP TABLE IF EXISTS public.event_lifecycle_log;
DROP TABLE IF EXISTS public.ingestion_runs;
DROP TYPE  IF EXISTS public.event_lifecycle_action;
DROP TYPE  IF EXISTS public.ingestion_status;
DROP TYPE  IF EXISTS public.ingestion_kind;
ALTER TABLE public.system_settings
    DROP CONSTRAINT IF EXISTS ck_refresh_inside_ttl,
    DROP COLUMN IF EXISTS snapshot_refresh_seconds,
    DROP COLUMN IF EXISTS postponement_void_hours;
```

Note: tickets voided by a postponement stay voided. Those are settled financial
facts under Package #1's append-only rules and are not reversed by dropping the
code that triggered them.
