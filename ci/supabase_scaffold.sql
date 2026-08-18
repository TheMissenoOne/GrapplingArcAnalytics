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

-- Supabase derives this from the request JWT. In CI there is no request, so it
-- returns NULL: RLS policies still compile and are exercised for syntax, they
-- just never match a row. Migration correctness is what this job checks.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULL::uuid $$;

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
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA storage TO anon, authenticated, service_role;
