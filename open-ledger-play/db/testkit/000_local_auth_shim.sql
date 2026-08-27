-- =============================================================================
-- 000_local_auth_shim.sql   *** TEST HARNESS ONLY -- NEVER APPLY TO SUPABASE ***
-- =============================================================================
-- Supabase provides the `auth` schema, `auth.users`, `auth.uid()` and the
-- `anon` / `authenticated` / `service_role` roles out of the box.
-- A bare PostgreSQL instance does not. This shim reproduces those objects with
-- the same semantics so the ACTUAL migrations (001+) can be exercised locally,
-- unmodified, including RLS + auth.uid() + role grants.
--
-- auth.uid() below is a faithful copy of Supabase's implementation.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS auth;

DO $$
DECLARE r TEXT;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT', r);
        END IF;
    END LOOP;
END $$;

-- Supabase's service_role carries BYPASSRLS; mirror it so RLS behaviour matches.
ALTER ROLE service_role BYPASSRLS;

GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;

CREATE TABLE IF NOT EXISTS auth.users (
    id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE
);

-- Mirrors Supabase's auth.uid(): reads the JWT subject out of the request GUCs.
CREATE OR REPLACE FUNCTION auth.uid()
RETURNS UUID
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(
        NULLIF(current_setting('request.jwt.claim.sub', true), ''),
        (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
    )::uuid
$$;

CREATE OR REPLACE FUNCTION auth.role()
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(
        NULLIF(current_setting('request.jwt.claim.role', true), ''),
        (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role')
    )::text
$$;

-- Supabase grants schema usage to the API roles; replicate so RLS is what gates.
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
