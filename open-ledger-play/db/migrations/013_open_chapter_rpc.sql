-- =============================================================================
-- 013_open_chapter_rpc.sql
-- =============================================================================
-- Creates a chapter AND its economic baseline atomically.
-- Takes NO user id: identity comes from auth.uid() only.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.open_chapter_rpc()
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_user_id          UUID;
    v_chapter_id       UUID;
    v_chapter_number   INT;
    v_starting_capital NUMERIC(12,2);
BEGIN
    -- 1. Identity ------------------------------------------------------------
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'AUTH_REQUIRED: an authenticated identity is required'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Serialize chapter creation per user. Also proves the profile exists.
    PERFORM 1 FROM public.users WHERE id = v_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PROFILE_NOT_FOUND: no public.users row for this identity'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- 2. No current chapter may already exist --------------------------------
    IF EXISTS (
        SELECT 1 FROM public.ledger_chapters
        WHERE user_id = v_user_id
          AND status IN ('ACTIVE', 'DEFICIT')
    ) THEN
        RAISE EXCEPTION 'CHAPTER_ALREADY_OPEN: close the current chapter first'
            USING ERRCODE = 'unique_violation';
    END IF;

    -- 3. Next chapter number -------------------------------------------------
    SELECT COALESCE(MAX(chapter_number), 0) + 1
      INTO v_chapter_number
      FROM public.ledger_chapters
     WHERE user_id = v_user_id;

    SELECT default_starting_capital
      INTO v_starting_capital
      FROM public.system_settings
     WHERE id = TRUE;

    -- 4. Chapter -------------------------------------------------------------
    INSERT INTO public.ledger_chapters (
        user_id, chapter_number, starting_capital, status
    )
    VALUES (
        v_user_id, v_chapter_number, v_starting_capital, 'ACTIVE'
    )
    RETURNING id, starting_capital
         INTO v_chapter_id, v_starting_capital;

    -- 5. The single opening credit. Amount is read back from the chapter row,
    --    so CHAPTER_OPEN can never disagree with starting_capital.
    --    starting_capital itself is NEVER summed into a balance.
    INSERT INTO public.wallet_transactions (
        user_id, chapter_id, ticket_id, transaction_type, amount, idempotency_key
    )
    VALUES (
        v_user_id, v_chapter_id, NULL, 'CHAPTER_OPEN', v_starting_capital,
        gen_random_uuid()
    );

    RETURN v_chapter_id;

EXCEPTION
    -- Loser of a true concurrent race against uq_one_current_chapter_per_user.
    WHEN unique_violation THEN
        IF SQLERRM LIKE 'CHAPTER_ALREADY_OPEN%' THEN
            RAISE;
        END IF;
        RAISE EXCEPTION 'CHAPTER_ALREADY_OPEN: a current chapter already exists'
            USING ERRCODE = 'unique_violation';
END;
$fn$;

REVOKE ALL     ON FUNCTION public.open_chapter_rpc() FROM PUBLIC;
REVOKE ALL     ON FUNCTION public.open_chapter_rpc() FROM anon;
GRANT  EXECUTE ON FUNCTION public.open_chapter_rpc() TO authenticated;
