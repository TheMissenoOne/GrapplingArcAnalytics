"""Edge → shared technique_nodes FK + denormalized owner_kind (vector-space split).

Wires ``graph_edges`` to the shared ``technique_nodes`` library (0004): the
edge endpoints (``source_key``/``target_key``) become foreign keys into the
canonical vocabulary, so edges can no longer reference a technique that does
not exist in the shared library. Also denormalizes ``owner_kind`` ('user' |
'athlete') from the owning graph onto each edge, so the athlete vs user
edge/transition vector spaces (the pgvector plan) are separable by a partial
index without a join to ``graphs``.

Idempotent + additive:
- ``owner_kind`` is added nullable and backfilled from ``graphs.owner_kind``; a
  BEFORE INSERT/UPDATE trigger keeps it in sync (the app upserts edges WITHOUT
  owner_kind, so it is derived server-side from ``graph_id``). The trigger is
  SECURITY INVOKER — a user can read their own ``graphs`` row via RLS, so the
  derivation works for user syncs; the athlete publisher runs as service_role.
- Any edge endpoint missing from ``technique_nodes`` is first backfilled
  (``source='user'``) so the FKs validate; FKs are added ``NOT VALID`` then
  validated to avoid a long table lock on a full rewrite.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- ── Denormalized owner_kind (athlete vs user vector-space split) ────
        alter table public.graph_edges
          add column if not exists owner_kind varchar(10);

        update public.graph_edges e
        set owner_kind = g.owner_kind
        from public.graphs g
        where e.graph_id = g.id
          and e.owner_kind is distinct from g.owner_kind;

        -- Keep owner_kind in sync with the parent graph. The app upserts edges
        -- without owner_kind, so derive it server-side from graph_id. SECURITY
        -- INVOKER (default): a user can read their own graphs row via RLS.
        create or replace function public.graph_edges_set_owner_kind()
        returns trigger language plpgsql
        set search_path = ''  -- pin: avoid a role-mutable search_path (lint 0011)
        as $fn$
        begin
          select g.owner_kind into new.owner_kind
          from public.graphs g
          where g.id = new.graph_id;
          return new;
        end;
        $fn$;

        drop trigger if exists trg_graph_edges_owner_kind on public.graph_edges;
        create trigger trg_graph_edges_owner_kind
          before insert or update of graph_id on public.graph_edges
          for each row execute function public.graph_edges_set_owner_kind();

        create index if not exists idx_graph_edges_owner_kind
          on public.graph_edges (owner_kind);

        -- ── FK: edge endpoints → shared technique library ───────────────────
        -- Backfill any dangling endpoint keys so the FKs can validate.
        insert into public.technique_nodes (node_key, label, type, node_type, source)
        select k.key, k.key, 'technique', '', 'user'
        from (
          select source_key as key from public.graph_edges
          union
          select target_key       from public.graph_edges
        ) k
        on conflict (node_key) do nothing;

        do $do$
        begin
          if not exists (
            select 1 from pg_constraint where conname = 'graph_edges_source_key_fkey'
          ) then
            alter table public.graph_edges
              add constraint graph_edges_source_key_fkey
              foreign key (source_key) references public.technique_nodes (node_key)
              not valid;
          end if;
          if not exists (
            select 1 from pg_constraint where conname = 'graph_edges_target_key_fkey'
          ) then
            alter table public.graph_edges
              add constraint graph_edges_target_key_fkey
              foreign key (target_key) references public.technique_nodes (node_key)
              not valid;
          end if;
        end
        $do$;

        alter table public.graph_edges validate constraint graph_edges_source_key_fkey;
        alter table public.graph_edges validate constraint graph_edges_target_key_fkey;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        alter table public.graph_edges drop constraint if exists graph_edges_target_key_fkey;
        alter table public.graph_edges drop constraint if exists graph_edges_source_key_fkey;
        drop index if exists public.idx_graph_edges_owner_kind;
        drop trigger if exists trg_graph_edges_owner_kind on public.graph_edges;
        drop function if exists public.graph_edges_set_owner_kind();
        alter table public.graph_edges drop column if exists owner_kind;
        """
    )
