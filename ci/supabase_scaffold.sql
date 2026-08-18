-- Minimal Supabase-shaped scaffold for the CI migrations smoke test.
--
-- The alembic revisions target a Supabase database and reference objects the
-- platform provisions, not ones the migrations create: the anon/authenticated/
-- service_role roles, auth.uid()/auth.users, and a small slice of storage.
-- A vanilla pgvector image has none of them.
--
-- This recreates only the objects the migrations actually reference. It is not a
-- Supabase emulator and must not grow into one -- if a future migration needs
-- materially more of the platform, that is the signal to run the real Supabase
-- CLI stack in CI instead of extending this file.
-- No `CREATE ROLE IF NOT EXISTS` in Postgres — guard each one so this stays
-- safe to run more than once against the same database (CI gets a fresh one
-- every run, but a local dev DB doesn't).
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN;
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS auth;

-- Supabase installs extensions into their own schema rather than public, and
-- revision 0006 creates pgvector there explicitly.
CREATE SCHEMA IF NOT EXISTS extensions;

CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);

-- Supabase's own definition, not a stub.
--
-- This used to return NULL unconditionally, with a comment saying RLS policies
-- were exercised "for syntax" only and never matched a row. That made the whole
-- policy layer untestable: a policy that denies everyone passes a syntax check
-- exactly as well as one that denies the right people.
--
-- The real function reads the claim PostgREST sets per request, so a test can
-- become a specific user with `set local request.jwt.claims` + `set local role
-- authenticated`, and the policies then evaluate for real. With no claim set it
-- still returns NULL, which is what it does for an anonymous request — so this
-- is strictly more faithful, not a CI-only behaviour. Migrations are unaffected:
-- they run as the table owner, which bypasses RLS either way.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
      SELECT COALESCE(
        NULLIF(current_setting('request.jwt.claim.sub', true), ''),
        (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
      )::uuid
    $$;

-- Revisions 0024/0025 manage the private `session-videos` bucket and policies
-- on storage.objects. Recreate only the columns/functions those revisions name.
CREATE SCHEMA IF NOT EXISTS storage;

CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY,
    name text NOT NULL,
    public boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS storage.objects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket_id text NOT NULL REFERENCES storage.buckets(id),
    name text NOT NULL
);

ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;

-- Supabase Storage exposes foldername(text) as a text[] helper. The migration
-- only reads element [1], so splitting the object path is sufficient for CI to
-- compile the RLS expressions without emulating Storage runtime behavior.
CREATE OR REPLACE FUNCTION storage.foldername(name text) RETURNS text[]
    LANGUAGE sql IMMUTABLE
    AS $$ SELECT string_to_array(name, '/') $$;

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

-- Supabase's default privileges, which are the reason half the migrations revoke.
--
-- A Supabase project ships with ALTER DEFAULT PRIVILEGES granting ALL on every new
-- table in `public` to `anon` and `authenticated`. So a table created by a migration
-- is world-writable the moment it exists, and RLS is the only thing between it and an
-- anonymous request — except for TRUNCATE, which RLS cannot gate at all. That is why
-- 0030 and 0031 `revoke all from anon, authenticated` before granting the four verbs
-- back, and they say so in their comments.
--
-- Without this line CI built a database with NO grants on those tables, so every
-- revoke was a no-op and a table that forgot one looked identical to a table that
-- did it right. Reproducing the default is what makes the revokes testable.
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA storage TO anon, authenticated, service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated;
