-- =============================================================================
-- 017_declare_bankruptcy_rpc.sql
-- =============================================================================
-- The escape hatch out of a dead chapter. Ownership is taken from auth.uid();
-- the caller never names a user. Operates on the caller's CURRENT chapter.
--
-- Preconditions (all required):
--   status is ACTIVE or DEFICIT
--   active escrow = 0            (no outstanding exposure may be abandoned)
--   available capital < MIN_VIABLE_WAGER
-- =============================================================================

CREATE OR REPLACE FUNCTION public.declare_bankruptcy_rpc()
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_user_id     UUID;
    v_chapter_id  UUID;
    v_status      public.chapter_status;
    v_settled     NUMERIC(12,2);
    v_escrow      NUMERIC(12,2);
    v_available   NUMERIC(12,2);
    v_min_wager   NUMERIC(12,2);
    v_reason      TEXT;
BEGIN
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'AUTH_REQUIRED: an authenticated identity is required'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Lock the caller's current chapter.
    SELECT id, status
      INTO v_chapter_id, v_status
      FROM public.ledger_chapters
     WHERE user_id = v_user_id
       AND status IN ('ACTIVE', 'DEFICIT')
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'NO_CURRENT_CHAPTER: nothing to declare bankruptcy on';
    END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_escrow
      FROM public.risk_reservations
     WHERE chapter_id = v_chapter_id
       AND status     = 'ACTIVE';

    IF v_escrow <> 0 THEN
        RAISE EXCEPTION
            'OPEN_EXPOSURE: settle or void all open tickets before declaring bankruptcy';
    END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_settled
      FROM public.wallet_transactions
     WHERE chapter_id = v_chapter_id;

    v_available := v_settled - v_escrow;

    SELECT min_viable_wager INTO v_min_wager
      FROM public.system_settings
     WHERE id = TRUE;

    IF v_available >= v_min_wager THEN
        RAISE EXCEPTION
            'CHAPTER_STILL_VIABLE: available capital still supports a minimum wager';
    END IF;

    v_reason := CASE v_status
                    WHEN 'DEFICIT' THEN 'DEFICIT_INSOLVENT'
                    ELSE                'BANKROLL_DEPLETED'
                END;

    UPDATE public.ledger_chapters
       SET status       = 'BUST',
           closed_at    = NOW(),
           close_reason = v_reason
     WHERE id = v_chapter_id;

    -- The partial unique index now permits a fresh chapter via open_chapter_rpc.
    RETURN v_chapter_id;
END;
$fn$;

REVOKE ALL     ON FUNCTION public.declare_bankruptcy_rpc() FROM PUBLIC;
REVOKE ALL     ON FUNCTION public.declare_bankruptcy_rpc() FROM anon;
GRANT  EXECUTE ON FUNCTION public.declare_bankruptcy_rpc() TO authenticated;
