"""Unified strategic ontology + Decision Space model.

Adds the canonical knowledge entities the "Strategic Evolution" specs (RF04-06, RF20)
and the Decision Space model (DS-01..05, DS-10, DS-11, DS-16) need. Position/Transition
stay as ``technique_nodes``/``graph_edges`` (reused, keyed by ``node_key`` ==
``analysis.names._normalize_name``); the composites below get their own typed tables and
soft-reference positions by ``node_key`` (JSONB arrays, no hard FK — the canonical library
is curated, not enforced at the row level).

New tables
- ``principles``  — invariant constraints; embeddable (``vector(768)``) for semantic search.
- ``intents``     — what a move aims to achieve.
- ``reactions``   — expected opponent responses (proto exists app-side as ``EdgeReaction``).
- ``dilemmas``    — decision forks (option_a/option_b) referencing principles; embeddable.
- ``systems``     — RF04: objective, entry positions, activation, expected responses,
  alternative paths, mastery criteria, + per-stage Decision-Space progression (DS-10).
- ``milestones``  — RF06 generic per-system ladder (conceptual → execution → dilemma →
  chaining → resistance → recovery); may carry a Decision-Space objective (DS-11).
- ``system_implementations`` — RF05 per-athlete OVERLAY (priorities/sequences/edge-emphasis/
  notes + milestone overrides) referencing the base system's nodes; no knowledge duplication.
- join tables ``system_principles`` / ``system_dilemmas``.

Altered
- ``technique_nodes`` gains ``decision_space JSONB`` (DS-01/04: offensive/defensive decision
  sets + expected reactions + constraints + attacker/defender scores) and ``ds_mode`` (DS-16:
  'expert' | 'learned' — same domain model, swappable value source; ships 'expert').

RLS: canonical knowledge is world-readable reference data (select to anon/authenticated);
writes go through the admin (service role bypasses RLS). pgvector extension + ``vector``
type come from 0006; ``search_path`` is set so the unqualified type resolves.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-29
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        set local search_path = public, extensions;

        -- ── Atoms ────────────────────────────────────────────────────────────
        create table if not exists public.principles (
            id          uuid primary key default gen_random_uuid(),
            key         text not null unique,            -- normalized slug
            name        text not null,
            description text,
            type        varchar(40),                     -- control | pressure | escape | ...
            embedding   vector(768),
            created_at  timestamptz not null default now(),
            updated_at  timestamptz not null default now()
        );

        create table if not exists public.intents (
            id          uuid primary key default gen_random_uuid(),
            key         text not null unique,
            name        text not null,
            description text,
            created_at  timestamptz not null default now()
        );

        create table if not exists public.reactions (
            id          uuid primary key default gen_random_uuid(),
            key         text not null unique,
            name        text not null,
            description text,
            created_at  timestamptz not null default now()
        );

        create table if not exists public.dilemmas (
            id            uuid primary key default gen_random_uuid(),
            key           text not null unique,
            name          text not null,
            situation     text,
            option_a      text,
            option_b      text,
            principle_keys jsonb not null default '[]'::jsonb,   -- soft refs → principles.key
            embedding     vector(768),
            created_at    timestamptz not null default now(),
            updated_at    timestamptz not null default now()
        );

        -- ── System (RF04) ────────────────────────────────────────────────────
        create table if not exists public.systems (
            id                       uuid primary key default gen_random_uuid(),
            key                      text not null unique,
            name                     text not null,
            objective                text,                            -- objetivo estratégico
            entry_positions          jsonb not null default '[]'::jsonb,  -- node_keys
            activation_conditions    jsonb not null default '[]'::jsonb,
            expected_opponent_responses jsonb not null default '[]'::jsonb,
            alternative_paths        jsonb not null default '[]'::jsonb,
            mastery_criteria         jsonb not null default '[]'::jsonb,
            ds_progression           jsonb not null default '[]'::jsonb,  -- DS-10 per-stage
            ds_mode                  varchar(10) not null default 'expert',
            created_at               timestamptz not null default now(),
            updated_at               timestamptz not null default now()
        );

        -- ── Milestones (RF06 / DS-11) — generic per-system ladder ─────────────
        create table if not exists public.milestones (
            id           uuid primary key default gen_random_uuid(),
            system_id    uuid not null references public.systems(id) on delete cascade,
            ordinal      integer not null default 0,
            -- conceptual|execution|dilemma|chaining|resistance|recovery
            kind         varchar(20) not null,
            name         text not null,
            description  text,
            ds_objective jsonb,                  -- nullable DS goal (DS-11)
            created_at   timestamptz not null default now(),
            unique (system_id, ordinal)
        );
        create index if not exists ix_milestones_system_id on public.milestones (system_id);

        -- ── Implementations (RF05) — per-athlete overlay ─────────────────────
        create table if not exists public.system_implementations (
            id                 uuid primary key default gen_random_uuid(),
            system_id          uuid not null references public.systems(id) on delete cascade,
            athlete_id         uuid not null references public.athletes(id) on delete cascade,
            name               text,
            -- {node_priorities, preferred_sequences, edge_emphasis, notes}
            overrides          jsonb not null default '{}'::jsonb,
            milestone_overrides jsonb not null default '[]'::jsonb,
            created_at         timestamptz not null default now(),
            updated_at         timestamptz not null default now(),
            unique (system_id, athlete_id)
        );
        create index if not exists ix_system_impl_system_id
          on public.system_implementations (system_id);
        create index if not exists ix_system_impl_athlete_id
          on public.system_implementations (athlete_id);

        -- ── Join tables ──────────────────────────────────────────────────────
        create table if not exists public.system_principles (
            system_id    uuid not null references public.systems(id) on delete cascade,
            principle_id uuid not null references public.principles(id) on delete cascade,
            primary key (system_id, principle_id)
        );
        create table if not exists public.system_dilemmas (
            system_id  uuid not null references public.systems(id) on delete cascade,
            dilemma_id uuid not null references public.dilemmas(id) on delete cascade,
            primary key (system_id, dilemma_id)
        );

        -- ── Decision Space on positions (DS-01/04/16) ────────────────────────
        alter table public.technique_nodes add column if not exists decision_space jsonb;
        alter table public.technique_nodes
          add column if not exists ds_mode varchar(10) not null default 'expert';

        -- ── pgvector ANN indexes (HNSW, cosine) — partial on backfilled rows ──
        create index if not exists idx_principles_embedding
          on public.principles using hnsw (embedding vector_cosine_ops)
          where embedding is not null;
        create index if not exists idx_dilemmas_embedding
          on public.dilemmas using hnsw (embedding vector_cosine_ops)
          where embedding is not null;

        -- ── RLS: canonical knowledge is world-readable reference data ─────────
        alter table public.principles            enable row level security;
        alter table public.intents               enable row level security;
        alter table public.reactions             enable row level security;
        alter table public.dilemmas              enable row level security;
        alter table public.systems               enable row level security;
        alter table public.milestones            enable row level security;
        alter table public.system_implementations enable row level security;
        alter table public.system_principles     enable row level security;
        alter table public.system_dilemmas       enable row level security;
        """
    )

    # Read-all policies (writes go through the service role, which bypasses RLS).
    for tbl in (
        "principles",
        "intents",
        "reactions",
        "dilemmas",
        "systems",
        "milestones",
        "system_implementations",
        "system_principles",
        "system_dilemmas",
    ):
        op.execute(
            f"""
            drop policy if exists {tbl}_read on public.{tbl};
            create policy {tbl}_read on public.{tbl}
              for select to anon, authenticated using (true);
            grant select on public.{tbl} to anon, authenticated;
            """
        )


def downgrade() -> None:
    op.execute(
        """
        alter table public.technique_nodes drop column if exists ds_mode;
        alter table public.technique_nodes drop column if exists decision_space;

        drop index if exists public.idx_dilemmas_embedding;
        drop index if exists public.idx_principles_embedding;

        drop table if exists public.system_dilemmas;
        drop table if exists public.system_principles;
        drop table if exists public.system_implementations;
        drop table if exists public.milestones;
        drop table if exists public.systems;
        drop table if exists public.dilemmas;
        drop table if exists public.reactions;
        drop table if exists public.intents;
        drop table if exists public.principles;
        """
    )
