-- Minimal Supabase-shaped scaffold for the CI migrations smoke test.
--
-- The alembic revisions target a Supabase database and reference objects the
-- platform provisions, not ones the migrations create: the anon/authenticated/
-- service_role roles, and auth.uid()/auth.users. A vanilla pgvector image has
-- none of them, so `alembic upgrade head` dies at 0003 on a missing role.
--
-- This recreates only the objects the migrations actually reference. It is not a
-- Supabase emulator and must not grow into one -- if a future migration needs
-- more of the platform, that is the signal to run the real Supabase CLI stack in
-- CI instead of extending this file.
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN;

CREATE SCHEMA IF NOT EXISTS auth;

-- Supabase installs extensions into their own schema rather than public, and
-- revision 0006 creates pgvector there explicitly.
CREATE SCHEMA IF NOT EXISTS extensions;

CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);

-- Supabase derives this from the request JWT. In CI there is no request, so it
-- returns NULL: RLS policies still compile and are exercised for syntax, they
-- just never match a row. Migration correctness is what this job checks.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULL::uuid $$;

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
