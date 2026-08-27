-- =============================================================================
-- 001_extensions_enums.sql -- Extensions + enum vocabulary
-- =============================================================================

-- pgcrypto is the architecture-mandated source of gen_random_uuid().
-- On PostgreSQL 13+ gen_random_uuid() is ALSO in core, so a build without the
-- contrib module still satisfies the UUID-default contract. We attempt the
-- extension (the Supabase path) and hard-assert the function either way.
DO $$
BEGIN
    BEGIN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS pgcrypto';
    EXCEPTION
        WHEN feature_not_supported OR undefined_file OR insufficient_privilege THEN
            RAISE NOTICE 'pgcrypto unavailable; relying on core gen_random_uuid() (PG13+)';
    END;

    -- Fail the migration loudly rather than let UUID defaults silently break.
    PERFORM gen_random_uuid();
END $$;

CREATE TYPE public.chapter_status AS ENUM (
    'ACTIVE',
    'DEFICIT',
    'BUST',
    'COMPLETED',
    'ADMIN_CLOSED'
);

CREATE TYPE public.ticket_status AS ENUM (
    'ACCEPTED',
    'CLOSED',
    'SETTLED',
    'VOIDED'
);

CREATE TYPE public.reservation_status AS ENUM (
    'ACTIVE',
    'RELEASED',
    'VOIDED'
);

CREATE TYPE public.transaction_type AS ENUM (
    'CHAPTER_OPEN',
    'SETTLEMENT_WIN',
    'SETTLEMENT_LOSS',
    'SETTLEMENT_PUSH',
    'SETTLEMENT_VOID',
    'SETTLEMENT_CORRECTION'
);

CREATE TYPE public.market_type AS ENUM (
    'MONEYLINE',
    'SPREAD',
    'TOTAL'
);

CREATE TYPE public.ticket_result_type AS ENUM (
    'WIN',
    'LOSS',
    'PUSH',
    'VOID'
);

-- -----------------------------------------------------------------------------
-- Server-side configuration. The architecture calls for a "configured TTL" and
-- an initial MIN_VIABLE_WAGER of 100 LC; both live here so no policy number is
-- hard-coded into a client or duplicated across RPCs.
-- Single-row table, pinned by a CHECK.
-- -----------------------------------------------------------------------------
CREATE TABLE public.system_settings (
    id                       BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    snapshot_ttl_seconds     INT           NOT NULL DEFAULT 120
                                 CHECK (snapshot_ttl_seconds > 0),
    max_ticket_fraction      NUMERIC(5,4)  NOT NULL DEFAULT 0.1000
                                 CHECK (max_ticket_fraction > 0 AND max_ticket_fraction <= 1),
    min_viable_wager         NUMERIC(12,2) NOT NULL DEFAULT 100.00
                                 CHECK (min_viable_wager > 0),
    default_starting_capital NUMERIC(12,2) NOT NULL DEFAULT 10000.00
                                 CHECK (default_starting_capital > 0)
);

INSERT INTO public.system_settings (id) VALUES (TRUE);
