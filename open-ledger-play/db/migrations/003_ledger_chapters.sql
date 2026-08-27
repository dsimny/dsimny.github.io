-- =============================================================================
-- 003_ledger_chapters.sql -- Ledger chapters
-- =============================================================================

CREATE TABLE public.ledger_chapters (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL
                         REFERENCES public.users(id)
                         ON DELETE RESTRICT,
    chapter_number   INT NOT NULL CHECK (chapter_number > 0),
    starting_capital NUMERIC(12,2) NOT NULL DEFAULT 10000.00
                         CHECK (starting_capital > 0),
    status           public.chapter_status NOT NULL DEFAULT 'ACTIVE',
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ,
    close_reason     TEXT,
    UNIQUE (user_id, chapter_number)
);

-- At most ONE current (ACTIVE or DEFICIT) chapter per user, enforced by the
-- database rather than by application logic.
CREATE UNIQUE INDEX uq_one_current_chapter_per_user
    ON public.ledger_chapters (user_id)
    WHERE status IN ('ACTIVE', 'DEFICIT');

-- Terminal states must be closed; current states must not be.
ALTER TABLE public.ledger_chapters
    ADD CONSTRAINT ck_chapter_closure_coherent CHECK (
        (status IN ('ACTIVE', 'DEFICIT') AND closed_at IS NULL)
        OR
        (status IN ('BUST', 'COMPLETED', 'ADMIN_CLOSED') AND closed_at IS NOT NULL)
    );

CREATE INDEX idx_chapters_user_status
    ON public.ledger_chapters (user_id, status);

COMMENT ON COLUMN public.ledger_chapters.starting_capital IS
    'METADATA ONLY. Never add this to wallet_transactions sums when computing balance.';
