-- =============================================================================
-- 04_p3_staleness_fixtures.sql -- TEST HARNESS ONLY. NEVER INSTALLED IN PRODUCTION.
-- =============================================================================
-- This file lives in tests/sql/ and is not in db/migrations/production_manifest.txt.
-- Nothing here may be depended on by a production migration; a migration whose
-- installation needs olp_test couples executable product state to the test
-- environment, which is what made 057 un-deployable.
--
-- Applied by tests/harness.py AFTER the full production manifest:
--
--     production manifest  ->  tests/sql/*  ->  run tests
--
-- Production does only the first step.
-- =============================================================================

-- =============================================================================
-- 036_p3_staleness_fixtures.sql -- encode a lesson as a guard rail
-- =============================================================================
-- THE REGRESSION RULE:
--   Never model feed staleness by inserting an older timestamp when a newer
--   immutable observation already exists.
--
-- This mistake has been made twice (M2-T04, P3-T28). It is seductive because it
-- looks like it should work, and it fails SILENTLY: the test passes through the
-- setup, asserts on unchanged behaviour, and reports something untrue.
--
-- Why it cannot work: every read orders by captured_at DESC, ingest_seq DESC,
-- so a back-dated row never becomes the current quote -- and snapshots are
-- immutable, so the fresh rows cannot be removed to make room for it. A stale
-- market is one whose NEWEST observation is old.
--
-- Rather than write that down and hope, the fixture now refuses.
-- =============================================================================

-- Renamed from olp_test.age_quote. The old name implied it aged the market; it
-- does not, and could not. It appends a back-dated observation -- exactly what a
-- late-arriving provider message looks like -- and that is what it is now called.
DROP FUNCTION IF EXISTS olp_test.age_quote(UUID, public.market_type, TEXT, TEXT, INTERVAL);

CREATE OR REPLACE FUNCTION olp_test.append_backdated_quote(
    p_event_id   UUID,
    p_market     public.market_type,
    p_selection  TEXT,
    p_sportsbook TEXT,
    p_age        INTERVAL
)
RETURNS UUID
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_last public.market_snapshots%ROWTYPE;
    v_id   UUID;
BEGIN
    SELECT * INTO v_last
      FROM public.market_snapshots
     WHERE event_id = p_event_id AND market_type = p_market
       AND selection = p_selection AND sportsbook = p_sportsbook
     ORDER BY captured_at DESC, ingest_seq DESC
     LIMIT 1;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no quote to back-date for % % %',
            p_market, p_selection, p_sportsbook;
    END IF;

    INSERT INTO public.market_snapshots (
        event_id, market_type, selection, line, price,
        sportsbook, source_provider, captured_at, is_in_play
    )
    VALUES (
        p_event_id, p_market, p_selection, v_last.line, v_last.price,
        p_sportsbook, 'FIXTURE', NOW() - p_age, FALSE
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- The guard rail itself.
--
-- READ THIS BEFORE REACHING FOR IT: you cannot make an existing fresh market
-- stale. Snapshots are immutable so the fresh row cannot be removed, and any row
-- you insert to "age" the market is either NEWER (making it fresher) or OLDER
-- (in which case it never becomes current). There is no third option. The
-- function is named for the thing people try to do, so that the attempt lands
-- here and is explained rather than silently doing nothing.
--
-- It therefore ASSERTS rather than mutates:
--   newest quote already >= p_age old  -> success, returns that snapshot
--   newest quote is fresher            -> raises, with the honest alternative
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.make_current_quote_stale(
    p_event_id   UUID,
    p_market     public.market_type,
    p_selection  TEXT,
    p_sportsbook TEXT,
    p_age        INTERVAL
)
RETURNS UUID
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_last   public.market_snapshots%ROWTYPE;
    v_target TIMESTAMPTZ := NOW() - p_age;
BEGIN
    SELECT * INTO v_last
      FROM public.market_snapshots
     WHERE event_id = p_event_id AND market_type = p_market
       AND selection = p_selection AND sportsbook = p_sportsbook
     ORDER BY captured_at DESC, ingest_seq DESC
     LIMIT 1;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'STALENESS_FIXTURE_MISUSE: no quote exists for % % % on this event; '
            'seed the market first', p_market, p_selection, p_sportsbook;
    END IF;

    IF v_last.captured_at > v_target THEN
        RAISE EXCEPTION
            'STALENESS_FIXTURE_MISUSE: the current quote (captured_at %) is fresher '
            'than the % you asked for, and it CANNOT be aged. Reads order by '
            'captured_at DESC and snapshots are immutable, so a back-dated row '
            'never becomes current and the fresh row cannot be removed. Build the '
            'market stale from the start instead: olp_test.seed_stale_market().',
            v_last.captured_at, p_age;
    END IF;

    -- Already at least this stale. Nothing to do, and nothing dishonest done.
    RETURN v_last.id;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- The honest way to build a dark market: an event whose only observations are
-- older than the TTL, as if the feed stopped reporting.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION olp_test.seed_stale_market(
    p_source_id  TEXT,
    p_age        INTERVAL DEFAULT INTERVAL '5 minutes',
    p_starts_in  INTERVAL DEFAULT INTERVAL '3 hours',
    p_sportsbook TEXT DEFAULT 'BOOK_A'
)
RETURNS TABLE (event_id UUID, snapshot_id UUID)
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_res   JSONB;
    v_event UUID;
    v_snap  UUID;
    v_home  TEXT := 'HOME_' || upper(replace(p_source_id, '-', '_'));
BEGIN
    v_res := public.ingest_event_rpc(
        p_source_id, v_home, 'AWAY_X', NOW() + p_starts_in, 'NFL', 'NFL', 'FIXTURE');
    v_event := (v_res ->> 'event_id')::uuid;

    v_snap := public.ingest_market_snapshot_rpc(
        v_event, 'SPREAD', v_home, -3.0, -110,
        p_sportsbook, 'FIXTURE', NOW() - p_age, FALSE);

    RETURN QUERY SELECT v_event, v_snap;
END;
$fn$;
