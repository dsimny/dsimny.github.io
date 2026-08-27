-- =============================================================================
-- 002_users.sql -- Profile bound to the Supabase auth identity
-- =============================================================================
-- The authoritative identity is auth.users. public.users carries only product
-- data; email is deliberately NOT duplicated here.

CREATE TABLE public.users (
    id         UUID PRIMARY KEY
                   REFERENCES auth.users(id)
                   ON DELETE RESTRICT,
    username   TEXT UNIQUE NOT NULL
                   CHECK (length(btrim(username)) BETWEEN 1 AND 40),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.users IS
    'Product profile. Identity is auth.users; user_id is never accepted from RPC input.';
