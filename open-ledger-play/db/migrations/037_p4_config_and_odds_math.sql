-- =============================================================================
-- 037_p4_config_and_odds_math.sql -- Package #4 foundations
-- =============================================================================
-- Config and the pure odds mathematics the canonical market is built on.
-- Everything here is IMMUTABLE and side-effect free; Package #4 writes nothing.
--
-- Thresholds fixed at review against live data (2026-08-27, 272 events / 4,554
-- quotes). See PACKAGE4_PREREG.md.
-- =============================================================================

ALTER TABLE public.system_settings
    -- Advisory only: below this a market is flagged thin, never withheld.
    ADD COLUMN mi_min_book_count INT NOT NULL DEFAULT 3
        CHECK (mi_min_book_count >= 1),
    -- The executable floor. Set to 2, not 3: live NFL book coverage is heavily
    -- skewed, and a floor of 3 left only 77 of 272 events tradeable versus 260
    -- at 2 -- while 2-book markets measured TIGHTER than 3-4 book markets
    -- (median dispersion 0.0101 vs 0.0111), so the lower floor admits coverage
    -- without admitting structurally worse markets.
    ADD COLUMN mi_execution_min_book_count INT NOT NULL DEFAULT 2
        CHECK (mi_execution_min_book_count >= 2),
    ADD COLUMN mi_dispersion_wide_threshold NUMERIC(6,4) NOT NULL DEFAULT 0.0500
        CHECK (mi_dispersion_wide_threshold > 0),
    ADD COLUMN mi_outlier_min_books INT NOT NULL DEFAULT 4
        CHECK (mi_outlier_min_books >= 4),
    ADD COLUMN mi_outlier_probability_delta NUMERIC(6,4) NOT NULL DEFAULT 0.1000
        CHECK (mi_outlier_probability_delta > 0),
    ADD COLUMN mi_line_fragmentation_max INT NOT NULL DEFAULT 3
        CHECK (mi_line_fragmentation_max >= 1),
    ADD COLUMN mi_movement_epsilon NUMERIC(6,4) NOT NULL DEFAULT 0.0050
        CHECK (mi_movement_epsilon >= 0),
    ADD COLUMN mi_devig_method TEXT NOT NULL DEFAULT 'MULTIPLICATIVE'
        CHECK (mi_devig_method IN ('MULTIPLICATIVE', 'SHIN'));

-- The floor must never exceed the advisory threshold's intent of being the
-- *looser* of the two signals.
ALTER TABLE public.system_settings
    ADD CONSTRAINT ck_mi_floor_not_above_advisory
        CHECK (mi_execution_min_book_count <= mi_min_book_count);

CREATE TYPE public.market_quality AS ENUM ('OK', 'DEGRADED', 'UNUSABLE');

-- -----------------------------------------------------------------------------
-- Implied probability from American odds.
--
--   price >= +100 :  100 / (price + 100)
--   price <= -100 :  |price| / (|price| + 100)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_implied_probability(p_price INT)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT CASE
               WHEN p_price >= 100 THEN 100.0 / (p_price::numeric + 100.0)
               WHEN p_price <= -100 THEN abs(p_price)::numeric / (abs(p_price)::numeric + 100.0)
               ELSE NULL
           END;
$fn$;

-- -----------------------------------------------------------------------------
-- Fair American odds from a probability. The inverse of the above.
--
--   p >  0.5 :  -100 * p / (1 - p)      (favourite, negative)
--   p <= 0.5 :  +100 * (1 - p) / p      (underdog, positive)
--
-- The +100 / -100 boundary is genuinely ambiguous -- both mean even money -- so
-- it is resolved by convention rather than left to float. p outside (0,1) is
-- undefined and returns NULL rather than a fabricated number.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_fair_american(p_probability NUMERIC)
RETURNS INT
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT CASE
               WHEN p_probability <= 0 OR p_probability >= 1 THEN NULL
               WHEN p_probability > 0.5
                   THEN round(-100.0 * p_probability / (1.0 - p_probability))::int
               ELSE round(100.0 * (1.0 - p_probability) / p_probability)::int
           END;
$fn$;

-- -----------------------------------------------------------------------------
-- Multiplicative (proportional) de-vig for a two-way market.
--
--   booksum = p1 + p2        the overround; > 1 in a normal market
--   fair1   = p1 / booksum
--
-- Returns the FAIR probability of the FIRST argument. Requires both sides from
-- the SAME book -- pairing is the caller's responsibility and is the place
-- cross-line leakage would enter (see PACKAGE4_PREREG.md 3.4).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.olp_devig_multiplicative(
    p_own NUMERIC,
    p_partner NUMERIC
)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT CASE
               WHEN p_own <= 0 OR p_partner <= 0 THEN NULL
               WHEN (p_own + p_partner) <= 0 THEN NULL
               ELSE p_own / (p_own + p_partner)
           END;
$fn$;

-- The overround a book is charging on a two-way market, for diagnostics.
CREATE OR REPLACE FUNCTION public.olp_overround(p_own NUMERIC, p_partner NUMERIC)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT (p_own + p_partner) - 1.0;
$fn$;

REVOKE ALL ON FUNCTION public.olp_implied_probability(INT)      FROM PUBLIC;
REVOKE ALL ON FUNCTION public.olp_fair_american(NUMERIC)        FROM PUBLIC;
REVOKE ALL ON FUNCTION public.olp_devig_multiplicative(NUMERIC, NUMERIC) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.olp_overround(NUMERIC, NUMERIC)   FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.olp_implied_probability(INT)      TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.olp_fair_american(NUMERIC)        TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.olp_devig_multiplicative(NUMERIC, NUMERIC) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.olp_overround(NUMERIC, NUMERIC)   TO anon, authenticated, service_role;
