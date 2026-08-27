-- =============================================================================
-- 006_market_snapshots.sql -- Immutable quote history
-- =============================================================================
-- Snapshots are historical facts. They are never overwritten. ingest_seq gives
-- a total order that survives identical captured_at values -- random UUIDs are
-- NEVER used as a chronology mechanism.

CREATE TABLE public.market_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ingest_seq          BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    event_id            UUID NOT NULL
                            REFERENCES public.events(id)
                            ON DELETE RESTRICT,
    market_type         public.market_type NOT NULL,
    selection           TEXT NOT NULL,
    line                NUMERIC(7,2),
    price               INT NOT NULL
                            CHECK (price <= -100 OR price >= 100),
    sportsbook          TEXT NOT NULL,
    source_provider     TEXT NOT NULL,
    captured_at         TIMESTAMPTZ NOT NULL,
    is_in_play          BOOLEAN NOT NULL DEFAULT FALSE,
    is_closing_snapshot BOOLEAN NOT NULL DEFAULT FALSE
);

-- An in-play quote can never be the closing line. Structural, not procedural.
ALTER TABLE public.market_snapshots
    ADD CONSTRAINT ck_closing_snapshot_not_in_play CHECK (
        NOT (is_closing_snapshot AND is_in_play)
    );

-- MONEYLINE carries no line; SPREAD and TOTAL must.
ALTER TABLE public.market_snapshots
    ADD CONSTRAINT ck_line_presence CHECK (
        (market_type = 'MONEYLINE' AND line IS NULL)
        OR
        (market_type IN ('SPREAD', 'TOTAL') AND line IS NOT NULL)
    );

CREATE INDEX idx_snapshots_lookup
    ON public.market_snapshots (
        event_id,
        market_type,
        selection,
        sportsbook,
        captured_at DESC,
        ingest_seq DESC
    );

-- Exactly one closing quote per event/market/selection/book.
CREATE UNIQUE INDEX uq_closing_snapshot
    ON public.market_snapshots (
        event_id,
        market_type,
        selection,
        sportsbook
    )
    WHERE is_closing_snapshot = TRUE;

-- Historical quotes are immutable; the only permitted transition is flagging a
-- pre-existing, non-in-play snapshot as the closing one.
CREATE OR REPLACE FUNCTION public.olp_guard_snapshot_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'APPEND_ONLY_VIOLATION: market_snapshots cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF (NEW.id, NEW.ingest_seq, NEW.event_id, NEW.market_type, NEW.selection,
        NEW.line, NEW.price, NEW.sportsbook, NEW.source_provider,
        NEW.captured_at, NEW.is_in_play)
       IS DISTINCT FROM
       (OLD.id, OLD.ingest_seq, OLD.event_id, OLD.market_type, OLD.selection,
        OLD.line, OLD.price, OLD.sportsbook, OLD.source_provider,
        OLD.captured_at, OLD.is_in_play)
    THEN
        RAISE EXCEPTION
            'IMMUTABLE_SNAPSHOT: only is_closing_snapshot may be set on an existing quote'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.is_closing_snapshot AND NOT NEW.is_closing_snapshot THEN
        RAISE EXCEPTION 'IMMUTABLE_SNAPSHOT: a closing quote cannot be un-flagged'
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_snapshots_immutable
    BEFORE UPDATE OR DELETE ON public.market_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.olp_guard_snapshot_update();
