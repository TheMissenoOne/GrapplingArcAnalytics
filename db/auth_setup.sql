-- ============================================================================
-- Supabase Auth wiring for user graph data.
-- Run this in the Supabase SQL editor (or psql via DATABASE_URL) ONCE.
-- Idempotent: safe to re-run. Complements the existing athlete-graph RLS.
--
-- Model: every signed-in app user (email OR Google-bridged) has a Supabase
-- session whose auth.uid() is the owner_id for their personal graph. The app
-- writes graphs/graph_edges (+ the shared technique_nodes library) with
-- owner_kind='user', owner_id=uid. (graph_nodes was dropped in alembic 0007.)
-- ============================================================================

-- ── 0. DB-level id defaults (PK default was Python/SQLAlchemy-only) ──────────
-- The app inserts via PostgREST, which never supplies id, so the column needs a
-- server-side default or every client insert fails NOT-NULL on id.
alter table public.graphs       alter column id set default gen_random_uuid();
alter table public.graph_edges  alter column id set default gen_random_uuid();
alter table public.profiles     alter column id set default gen_random_uuid();

-- ── 1. Auto-provision a profile row per auth user ───────────────────────────
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, full_name, is_guest)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name'),
    false
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ── 2. profiles RLS — a user sees/edits only their own row ──────────────────
alter table public.profiles enable row level security;

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select using (id = auth.uid());

drop policy if exists profiles_insert_own on public.profiles;
create policy profiles_insert_own on public.profiles
  for insert with check (id = auth.uid());

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update using (id = auth.uid()) with check (id = auth.uid());

-- ── 3. graphs — a user owns their own 'user' graph (read + write) ───────────
alter table public.graphs enable row level security;

drop policy if exists graphs_user_select on public.graphs;
create policy graphs_user_select on public.graphs
  for select using (owner_kind = 'user' and owner_id = auth.uid());

drop policy if exists graphs_user_insert on public.graphs;
create policy graphs_user_insert on public.graphs
  for insert with check (owner_kind = 'user' and owner_id = auth.uid());

drop policy if exists graphs_user_update on public.graphs;
create policy graphs_user_update on public.graphs
  for update using (owner_kind = 'user' and owner_id = auth.uid())
  with check (owner_kind = 'user' and owner_id = auth.uid());

-- ── 4. graph_edges — gated through the owning graph ─────────────────────────
alter table public.graph_edges enable row level security;

drop policy if exists graph_edges_user_all on public.graph_edges;
create policy graph_edges_user_all on public.graph_edges
  for all
  using (exists (
    select 1 from public.graphs g
    where g.id = graph_edges.graph_id and g.owner_kind = 'user' and g.owner_id = auth.uid()
  ))
  with check (exists (
    select 1 from public.graphs g
    where g.id = graph_edges.graph_id and g.owner_kind = 'user' and g.owner_id = auth.uid()
  ));

-- ── 5. Grants (RLS still applies; anon/authenticated need table privileges) ──
grant usage on schema public to anon, authenticated;
grant select, insert, update, delete on public.graphs, public.graph_edges to authenticated;
grant select, insert, update on public.profiles to authenticated;

-- ── 6. Cascade deletes ──────────────────────────────────────────────────────
-- edges follow their graph.
alter table public.graph_edges drop constraint if exists graph_edges_graph_id_fkey;
alter table public.graph_edges add constraint graph_edges_graph_id_fkey
  foreign key (graph_id) references public.graphs(id) on delete cascade;

-- profiles.id is always an auth user id → real FK, cascade on user delete.
alter table public.profiles drop constraint if exists profiles_id_fkey;
alter table public.profiles add constraint profiles_id_fkey
  foreign key (id) references auth.users(id) on delete cascade;

-- graphs.owner_id is polymorphic (user→auth.users, athlete→athletes), so a column
-- FK is impossible. Clean a deleted user's graph via a trigger (nodes/edges then
-- cascade through the graph FK above).
create or replace function public.handle_user_delete()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  delete from public.graphs where owner_kind = 'user' and owner_id = old.id;
  return old;
end;
$$;

drop trigger if exists on_auth_user_deleted on auth.users;
create trigger on_auth_user_deleted
  before delete on auth.users
  for each row execute function public.handle_user_delete();

-- ── 7. Lock SECURITY DEFINER trigger fns off the REST RPC surface ───────────
-- Postgres grants EXECUTE to PUBLIC by default, so anon/authenticated could call
-- these as /rest/v1/rpc/<fn> (Supabase lint 0028/0029). They only ever run as
-- triggers (owner privileges), so revoke the public grant. Triggers still fire.
revoke execute on function public.handle_new_user()    from public;
revoke execute on function public.handle_user_delete() from public;
