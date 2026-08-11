"""Adopt db/auth_setup.sql into the alembic chain.

The user-graph RLS, the profile auto-provision trigger and the id defaults were
hand-run in the Supabase SQL editor and lived only in ``db/auth_setup.sql`` — the
same untracked-policy split that let live and tracked diverge on 2026-06-25. This
revision takes ownership of them; the file is deleted in the same commit.

The SQL is idempotent (``drop policy if exists`` before every ``create policy``,
``create or replace function``), so applying this where the file has already been
run is a no-op. That is what makes adoption safe against a live database.

Written from the Task 0 drift check against live ``pg_policies``/``pg_class``/
``pg_proc``, not blindly from the file: live == tracked for everything the file
already held (no TRACKED-BUT-NOT-LIVE, no BOTH-BUT-DIFFERENT), so those sections
are copied verbatim. One thing was live but untracked anywhere —
``public.rls_auto_enable()`` and the event trigger ``ensure_rls`` that calls it on
every ``ddl_command_end`` — almost certainly a leftover of the 2026-06-25
hardening pass, and it is why no table in ``public`` can be created without RLS.
It is adopted here byte-for-byte from ``pg_get_functiondef`` (section 8 below),
because approximating a security-relevant function body from a description is
how drift starts again. It also means the ``groups``/``group_members``/
``group_invites`` tables created in 0024 get RLS enabled twice — once by this
trigger, once by 0024's own explicit ``alter table ... enable row level
security``. Both are idempotent; 0024 keeps its explicit statements regardless,
because a migration must not depend on an event trigger already being installed
to be correct. The six other live event triggers (``pgrst_ddl_watch``,
``pgrst_drop_watch``, ``issue_graphql_placeholder``, ``issue_pg_cron_access``,
``issue_pg_net_access``, ``issue_pg_graphql_access``) are Supabase platform
objects, not product objects — they are excluded from this revision on purpose.

Scope note: two stanzas need elevated privileges the migration role may not
hold — the trigger on ``auth.users`` (section 1, needs privileges in the
``auth`` schema) and the event trigger ``ensure_rls`` (section 8, ``create
event trigger`` needs the same class of elevated privilege). Both are attempted
here with the rest. If the apply fails on permissions, that one stanza (and
only it) moves to a small named SQL file with a pointer comment left in its
place — ``db/auth_users_trigger.sql`` for section 1, ``db/ensure_rls_event_
trigger.sql`` for section 8 — and everything in ``public`` otherwise stays
tracked here.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 0. DB-level id defaults (PK default was Python/SQLAlchemy-only) ──────
    # The app inserts via PostgREST, which never supplies id, so the column
    # needs a server-side default or every client insert fails NOT-NULL on id.
    op.execute(
        """
        alter table public.graphs       alter column id set default gen_random_uuid();
        alter table public.graph_edges  alter column id set default gen_random_uuid();
        alter table public.profiles     alter column id set default gen_random_uuid();
        """
    )

    # ── 1. Auto-provision a profile row per auth user ────────────────────────
    # Needs privileges in the auth schema — see scope note above.
    op.execute(
        """
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
        """
    )

    # ── 2. profiles RLS — a user sees/edits only their own row ───────────────
    op.execute(
        """
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

        -- ``is_pro`` is admin-granted. RLS owns rows, not fields, so use column
        -- privileges to prevent an authenticated user from promoting their own profile.
        revoke insert, update on public.profiles from authenticated;
        grant insert (id, full_name, belt_rank, belt_degrees, is_guest, archetype_id)
          on public.profiles to authenticated;
        grant update (full_name, belt_rank, belt_degrees, is_guest, archetype_id)
          on public.profiles to authenticated;
        grant select on public.profiles to authenticated;
        """
    )

    # ── 3. graphs — a user owns their own 'user' graph (read + write) ────────
    op.execute(
        """
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
        """
    )

    # ── 4. graph_edges — gated through the owning graph ──────────────────────
    op.execute(
        """
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
        """
    )

    # ── 4b. user_sessions / user_sync_meta — real FK to profiles(id), owner-only ──
    # (alembic 0017/0018). No owner_kind predicate needed (unlike graphs' polymorphic
    # owner_id) — cascade delete is automatic via the FK, no trigger needed.
    op.execute(
        """
        alter table public.user_sessions enable row level security;

        drop policy if exists user_sessions_owner_all on public.user_sessions;
        create policy user_sessions_owner_all on public.user_sessions
          for all using (owner_id = auth.uid())
          with check (owner_id = auth.uid());

        alter table public.user_sync_meta enable row level security;

        drop policy if exists user_sync_meta_owner_all on public.user_sync_meta;
        create policy user_sync_meta_owner_all on public.user_sync_meta
          for all using (owner_id = auth.uid())
          with check (owner_id = auth.uid());
        """
    )

    # ── 4c. Pro payloads — service writer, entitled reader only ──────────────
    op.execute(
        """
        alter table public.user_performance_snapshots enable row level security;

        drop policy if exists user_performance_snapshots_pro_owner_select
          on public.user_performance_snapshots;
        create policy user_performance_snapshots_pro_owner_select
          on public.user_performance_snapshots
          for select using (
            owner_id = auth.uid()
            and exists (
              select 1 from public.profiles p where p.id = auth.uid() and p.is_pro = true
            )
          );

        alter table public.athlete_dossiers enable row level security;

        drop policy if exists athlete_dossiers_pro_select on public.athlete_dossiers;
        create policy athlete_dossiers_pro_select on public.athlete_dossiers
          for select using (
            exists (
              select 1 from public.profiles p where p.id = auth.uid() and p.is_pro = true
            )
          );
        """
    )

    # ── 5. Grants (RLS still applies; anon/authenticated need table privileges) ──
    op.execute(
        """
        grant usage on schema public to anon, authenticated;
        grant select, insert, update, delete on public.graphs, public.graph_edges to authenticated;
        grant select, insert, update, delete
          on public.user_sessions, public.user_sync_meta to authenticated;
        grant select on public.user_performance_snapshots, public.athlete_dossiers to authenticated;
        """
    )

    # ── 6. Cascade deletes ────────────────────────────────────────────────────
    op.execute(
        """
        -- edges follow their graph.
        alter table public.graph_edges drop constraint if exists graph_edges_graph_id_fkey;
        alter table public.graph_edges add constraint graph_edges_graph_id_fkey
          foreign key (graph_id) references public.graphs(id) on delete cascade;

        -- profiles.id is always an auth user id -> real FK, cascade on user delete.
        alter table public.profiles drop constraint if exists profiles_id_fkey;
        alter table public.profiles add constraint profiles_id_fkey
          foreign key (id) references auth.users(id) on delete cascade;

        -- graphs.owner_id is polymorphic (user->auth.users, athlete->athletes), so a column
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
        """
    )

    # ── 7. Lock SECURITY DEFINER trigger fns off the REST RPC surface ────────
    # Postgres grants EXECUTE to PUBLIC by default, so anon/authenticated could call
    # these as /rest/v1/rpc/<fn> (Supabase lint 0028/0029). They only ever run as
    # triggers (owner privileges), so revoke the public grant. Triggers still fire.
    op.execute(
        """
        revoke execute on function public.handle_new_user()    from public;
        revoke execute on function public.handle_user_delete() from public;
        """
    )

    # ── 8. rls_auto_enable — live but untracked anywhere until this revision ─
    # Fires on every CREATE TABLE in ``public`` and turns RLS on (fail-closed).
    # Almost certainly a leftover of the 2026-06-25 hardening; adopted byte-for-byte
    # from ``pg_get_functiondef`` (not reproduced from memory/description) because this
    # is exactly the class of object an approximation would let drift again. Needs the
    # same elevated privilege as section 1's auth.users trigger — see scope note above.
    op.execute(
        """
        create or replace function public.rls_auto_enable()
        returns event_trigger
        language plpgsql
        security definer
        set search_path = 'pg_catalog'
        as $function$
        declare
          cmd record;
        begin
          for cmd in
            select *
            from pg_event_trigger_ddl_commands()
            where command_tag in ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
              and object_type in ('table','partitioned table')
          loop
             if cmd.schema_name is not null and cmd.schema_name in ('public')
                and cmd.schema_name not in ('pg_catalog','information_schema')
                and cmd.schema_name not like 'pg_toast%' and cmd.schema_name not like 'pg_temp%' then
              begin
                execute format('alter table if exists %s enable row level security', cmd.object_identity);
                raise log 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
              exception
                when others then
                  raise log 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
              end;
             else
                raise log 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
             end if;
          end loop;
        end;
        $function$;

        drop event trigger if exists ensure_rls;
        create event trigger ensure_rls
          on ddl_command_end execute function public.rls_auto_enable();
        """
    )


def downgrade() -> None:
    # 8
    op.execute("drop event trigger if exists ensure_rls;")
    op.execute("drop function if exists public.rls_auto_enable();")

    # 7 — restore the pre-revision default (EXECUTE granted to PUBLIC).
    op.execute("grant execute on function public.handle_new_user()    to public;")
    op.execute("grant execute on function public.handle_user_delete() to public;")

    # 6
    op.execute("drop trigger if exists on_auth_user_deleted on auth.users;")
    op.execute("drop function if exists public.handle_user_delete();")
    op.execute("alter table public.profiles drop constraint if exists profiles_id_fkey;")
    op.execute(
        "alter table public.graph_edges drop constraint if exists graph_edges_graph_id_fkey;"
    )
    op.execute(
        "alter table public.graph_edges add constraint graph_edges_graph_id_fkey "
        "foreign key (graph_id) references public.graphs(id);"
    )

    # 5
    op.execute(
        "revoke select on public.user_performance_snapshots, "
        "public.athlete_dossiers from authenticated;"
    )
    op.execute(
        "revoke select, insert, update, delete "
        "on public.user_sessions, public.user_sync_meta from authenticated;"
    )
    op.execute(
        "revoke select, insert, update, delete "
        "on public.graphs, public.graph_edges from authenticated;"
    )
    op.execute("revoke usage on schema public from anon, authenticated;")

    # 4c
    op.execute("drop policy if exists athlete_dossiers_pro_select on public.athlete_dossiers;")
    op.execute(
        "drop policy if exists user_performance_snapshots_pro_owner_select "
        "on public.user_performance_snapshots;"
    )

    # 4b
    op.execute("drop policy if exists user_sync_meta_owner_all on public.user_sync_meta;")
    op.execute("drop policy if exists user_sessions_owner_all on public.user_sessions;")

    # 4
    op.execute("drop policy if exists graph_edges_user_all on public.graph_edges;")

    # 3
    op.execute("drop policy if exists graphs_user_update on public.graphs;")
    op.execute("drop policy if exists graphs_user_insert on public.graphs;")
    op.execute("drop policy if exists graphs_user_select on public.graphs;")

    # 2
    op.execute("revoke select on public.profiles from authenticated;")
    op.execute(
        "revoke update (full_name, belt_rank, belt_degrees, is_guest, archetype_id) "
        "on public.profiles from authenticated;"
    )
    op.execute(
        "revoke insert (id, full_name, belt_rank, belt_degrees, is_guest, archetype_id) "
        "on public.profiles from authenticated;"
    )
    op.execute("grant insert, update on public.profiles to authenticated;")
    op.execute("drop policy if exists profiles_update_own on public.profiles;")
    op.execute("drop policy if exists profiles_insert_own on public.profiles;")
    op.execute("drop policy if exists profiles_select_own on public.profiles;")

    # 1
    op.execute("drop trigger if exists on_auth_user_created on auth.users;")
    op.execute("drop function if exists public.handle_new_user();")

    # 0
    op.execute("alter table public.profiles     alter column id drop default;")
    op.execute("alter table public.graph_edges  alter column id drop default;")
    op.execute("alter table public.graphs       alter column id drop default;")
