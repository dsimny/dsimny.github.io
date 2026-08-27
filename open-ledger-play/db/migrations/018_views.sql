-- =============================================================================
-- 018_views.sql -- Server-authoritative derived state
-- =============================================================================
-- The frontend consumes these. It does NOT reproduce its own accounting.
--
-- Both views are security_invoker, so the caller's RLS decides row visibility:
-- a user sees only their own ledger through them.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Derived balance.
--   settled   = SUM(wallet_transactions.amount)
--   escrow    = SUM(ACTIVE risk_reservations.amount)
--   available = settled - escrow
-- starting_capital is exposed for display ONLY and is never part of the sum.
-- -----------------------------------------------------------------------------
CREATE VIEW public.chapter_balances
WITH (security_invoker = true) AS
SELECT
    c.id                                            AS chapter_id,
    c.user_id,
    c.chapter_number,
    c.status,
    c.starting_capital,
    COALESCE(w.settled_balance, 0)::numeric(12,2)   AS settled_balance,
    COALESCE(r.escrowed_risk, 0)::numeric(12,2)     AS escrowed_risk,
    (COALESCE(w.settled_balance, 0)
     - COALESCE(r.escrowed_risk, 0))::numeric(12,2) AS available_capital,
    LEAST(
        GREATEST(round(s.max_ticket_fraction * COALESCE(w.settled_balance, 0), 2), 0),
        GREATEST(COALESCE(w.settled_balance, 0) - COALESCE(r.escrowed_risk, 0), 0)
    )::numeric(12,2)                                AS max_ticket_size
FROM public.ledger_chapters c
CROSS JOIN public.system_settings s
LEFT JOIN LATERAL (
    SELECT SUM(wt.amount) AS settled_balance
    FROM public.wallet_transactions wt
    WHERE wt.chapter_id = c.id
) w ON TRUE
LEFT JOIN LATERAL (
    SELECT SUM(rr.amount) AS escrowed_risk
    FROM public.risk_reservations rr
    WHERE rr.chapter_id = c.id
      AND rr.status = 'ACTIVE'
) r ON TRUE
WHERE s.id = TRUE;

COMMENT ON VIEW public.chapter_balances IS
    'Authoritative derived balance. settled_balance is SUM(wallet_transactions) '
    'ONLY -- starting_capital is never added to it.';

-- -----------------------------------------------------------------------------
-- Effective result = original settlement + ordered corrections.
-- The original row is preserved intact and reported alongside.
-- -----------------------------------------------------------------------------
CREATE VIEW public.ticket_effective_results
WITH (security_invoker = true) AS
SELECT
    t.id                                                  AS ticket_id,
    t.user_id,
    t.chapter_id,
    res.result                                            AS original_result,
    res.pnl                                               AS original_pnl,
    res.settled_at                                        AS originally_settled_at,
    COALESCE(latest.new_effective_result, res.result)     AS effective_result,
    (res.pnl + COALESCE(agg.total_delta, 0))::numeric(12,2) AS effective_pnl,
    COALESCE(agg.correction_count, 0)::int                AS correction_count,
    agg.last_corrected_at
FROM public.tickets t
JOIN public.ticket_results res
    ON res.ticket_id = t.id
LEFT JOIN LATERAL (
    -- Deterministic: last correction by adjustment_seq, never by timestamp alone.
    SELECT a.new_effective_result
    FROM public.ticket_result_adjustments a
    WHERE a.ticket_id = t.id
    ORDER BY a.adjustment_seq DESC
    LIMIT 1
) latest ON TRUE
LEFT JOIN LATERAL (
    SELECT COUNT(*)          AS correction_count,
           SUM(a.pnl_delta)  AS total_delta,
           MAX(a.created_at) AS last_corrected_at
    FROM public.ticket_result_adjustments a
    WHERE a.ticket_id = t.id
) agg ON TRUE;

COMMENT ON VIEW public.ticket_effective_results IS
    'Original result and P/L preserved verbatim; effective_result/effective_pnl '
    'derived by replaying ticket_result_adjustments in adjustment_seq order.';

REVOKE ALL ON public.chapter_balances          FROM PUBLIC, anon;
REVOKE ALL ON public.ticket_effective_results  FROM PUBLIC, anon;
GRANT SELECT ON public.chapter_balances         TO authenticated;
GRANT SELECT ON public.ticket_effective_results TO authenticated;
