"""Rating V2 persistence — shadow tables, parallel to V1, no existing column touched.

Wave 7 of ``docs/rating_v2/02_PLANO_DE_EXECUCAO.md``: gives the Glicko-2 replay
(``analysis/rating_v2/``) a place to persist a run without going anywhere near
``athletes.elo``/``rank_elo`` or any V1 surface. Two tables only:

- ``rating_engine_runs`` — one row per replay invocation: which ``engine_version``
  + config produced a snapshot, and its status. ``rating_engine_runs.engine_version``
  is a required read key (ADR-02 in ``docs/rating_v2/01_DECISOES.md``), never audit
  decoration — any query against the state table without a ``run_id`` is a defect.
- ``athlete_rating_states_v2`` — one row per (run, athlete): rating/RD/volatility at
  the end of that run, keyed ``(run_id, athlete_id)``.

Deliberately NOT created here, despite the wave-7 entry in the plan doc listing them:
``athlete_node_rating_states_v2``, ``athlete_constellations_v2``,
``athlete_constellation_members_v2``. Orchestrator decision (2026-08-17): wave 5 found
the node layer has a median of 1 bout per node and calibration untestable in 34/36
cells, and wave 6 did not clear the shared detector to replace the existing one.
Persisting those layers now would hand production status to numbers just measured as
unsupportable. They land in their own migration once out of shadow.

Both tables hold athlete (public/competitive) data only — Glicko-2 replay input is
``matches``/``athletes``, never a user session or user graph (root CLAUDE.md's
public/private data rule).

RLS deliberately diverges from the world-readable athlete-reference pattern
(``0004``/``0012``/``0013``): **RLS enabled with no policy at all**, so nothing is
readable through ``anon``/``authenticated``. These hold shadow ratings — V1 is still
the production engine and no consumer has migrated (wave 8). A world-readable shadow
number can be scraped and presented as "the GrapplingArc rating" before any product
decision exists about it, and before the presentation rule (always relative + %, never
a raw rating) has been reworked for RD. The service-role replay job bypasses RLS and
writes fine; the read policy lands in wave 8 alongside the consumer that justifies it.

Not read by the App (no ``graphs``/``graph_edges``/``technique_nodes``/
``published_athlete_graphs`` touched) — not a cross-module contract change.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-17
"""

from __future__ import annotations

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        set local search_path = public, extensions;

        create table if not exists public.rating_engine_runs (
            id uuid primary key default gen_random_uuid(),
            engine_version text not null,
            config_json jsonb not null default '{}'::jsonb,
            source_hash text,
            started_at timestamptz not null default now(),
            completed_at timestamptz,
            status text not null default 'running',
            notes text,
            constraint ck_rating_engine_runs_status
              check (status in ('running', 'completed', 'failed'))
        );

        create table if not exists public.athlete_rating_states_v2 (
            run_id uuid not null
              references public.rating_engine_runs(id) on delete cascade,
            athlete_id uuid not null
              references public.athletes(id) on delete cascade,
            rating double precision not null,
            rating_deviation double precision not null,
            volatility double precision not null,
            periods integer not null default 0,
            bout_count integer not null default 0,
            last_active_at timestamptz,
            created_at timestamptz not null default now(),
            primary key (run_id, athlete_id)
        );

        -- Só athlete_id: a PK (run_id, athlete_id) já indexa run_id como coluna líder,
        -- então um índice dedicado a run_id seria peso morto em escrita sem consulta própria.
        create index if not exists ix_athlete_rating_states_v2_athlete_id
          on public.athlete_rating_states_v2 (athlete_id);

        -- RLS LIGADA, NENHUMA POLICY — nega por padrão.
        --
        -- Estas tabelas guardam rating de SOMBRA: a V1 continua sendo a engine de produção e
        -- nenhum consumidor foi migrado (wave 8). Publicar por `anon` agora deixaria um número
        -- não validado consultável por qualquer um, e passível de ser apresentado como "o rating
        -- do GrapplingArc" antes de existir decisão de produto sobre isso — inclusive contra a
        -- regra de apresentação (sempre relativo + %, nunca rating bruto).
        --
        -- O replay escreve via service-role, que ignora RLS. A policy de leitura entra na wave 8,
        -- junto do consumidor que a justificar — não antes.
        alter table public.rating_engine_runs enable row level security;
        alter table public.athlete_rating_states_v2 enable row level security;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        drop index if exists public.ix_athlete_rating_states_v2_athlete_id;

        drop table if exists public.athlete_rating_states_v2;
        drop table if exists public.rating_engine_runs;
        """
    )
