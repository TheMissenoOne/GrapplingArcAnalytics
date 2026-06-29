"""Archetype Target vs Emergent (RF01).

Splits the ``archetypes`` catalog into two kinds sharing one table:
- ``emergent`` (default) — computed clusters from ``analysis.archetype.run_archetype_pipeline``
  (deviance feature v3); rewritten on every recompute (``clear_archetypes`` scopes to emergent).
- ``target`` — author-defined catalog (admin ``/admin/archetypes``), curated, never auto-deleted.

``signature_types`` holds the emphasized node types (e.g. ["submission","control"]) — for
emergent rows it mirrors the centroid's dominant deviance dims; for target rows the author picks
them. Both feed the ontology seed (Grapple Like X / archetype picker, RF02/RF12/RF15).

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-29
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        set local search_path = public, extensions;

        alter table public.archetypes
            add column if not exists kind varchar(10) not null default 'emergent',
            add column if not exists signature_types jsonb not null default '[]'::jsonb,
            add column if not exists key text;

        -- Target archetypes are world-readable reference data (writes via admin/service role).
        -- archetypes RLS mirrors the other ontology tables; add a read-all policy if absent.
        do $$
        begin
            if not exists (
                select 1 from pg_policies
                where schemaname = 'public' and tablename = 'archetypes'
                  and policyname = 'archetypes_read_all'
            ) then
                execute 'alter table public.archetypes enable row level security';
                execute 'create policy archetypes_read_all on public.archetypes
                         for select to anon, authenticated using (true)';
            end if;
        end $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        set local search_path = public, extensions;
        drop policy if exists archetypes_read_all on public.archetypes;
        alter table public.archetypes
            drop column if exists signature_types,
            drop column if exists kind,
            drop column if exists key;
        """
    )
