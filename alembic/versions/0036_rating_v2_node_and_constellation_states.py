"""Rating V2 node + constellation state — the three tables 0035 deliberately deferred.

Wave 7 of ``docs/rating_v2/02_PLANO_DE_EXECUCAO.md``, continued. ``0035`` shipped only
``rating_engine_runs``/``athlete_rating_states_v2`` (global per-athlete state) because
wave 5 measured the node layer as unsupportable (median 1 bout/node, calibration
untestable in 34/36 sweep cells — ADR-03) and wave 6 left the constellation detector
coexisting with, not replacing, ``athlete_systems.py`` (ADR-08). Neither of those
findings changed: this migration still only creates a place to WRITE shadow state, not
a decision to read it anywhere. No consumer, no cutover.

Three tables, per ``docs/rating_v2/01_DECISOES.md`` §wave-7 shape:

- ``athlete_node_rating_states_v2`` — one row per (run, athlete, node_key): the athlete's
  Glicko-2 state for one technique node at the end of that run. ``bouts_observed`` and
  ``occurrences`` are DELIBERATELY separate columns, not one count — a node can appear
  more than once in a single bout's event sequence, and the plan keeps "how many bouts
  touched this node" distinct from "how many times the node fired" (see wave 5 sweep
  input in ``analysis/rating_v2/``). ``node_key`` is the canonical identity from
  ``analysis/names.py:_normalize_name`` (== ``technique_nodes.node_key``), never a
  display label — this is the same char-for-char contract the App's ``normalizeLabel()``
  depends on, even though this table isn't read by the App today. Following the
  ``graph_edges.source_key``/``target_key`` precedent already in this schema, ``node_key``
  is plain ``text``, not a real FK to ``technique_nodes.node_key`` — this repo doesn't
  enforce that edge today either, so a new table shouldn't invent a stricter rule alone.
  ``constellation_fingerprint`` is nullable and UNENFORCED (no FK) — the constellation
  layer producing that fingerprint is being built in a parallel change at the time of
  this migration; wiring the real value is a follow-up once that lands, not invented here.
  ``first_seen_at``/``last_seen_at`` are nullable for the same reason ``0035``'s
  ``last_active_at`` is nullable: ``matches`` has no per-bout date yet (ADR-04 debt, only
  ``year``), so a writer with nothing more precise than a year leaves these NULL rather
  than fabricate a day.

- ``athlete_constellations_v2`` — one row per (run, athlete, fingerprint): a detected,
  guaranteed-connected community (``analysis/constellations/detect.py``) plus its
  bootstrap-stability summary (``analysis/constellations/stability.py``). ``fingerprint``
  is opaque here — however the constellation layer ends up deriving a stable identifier
  for "this same cluster of nodes" is that module's decision, not this migration's.

- ``athlete_constellation_members_v2`` — one row per (run, athlete, fingerprint,
  node_key): the member nodes of one constellation with their in-constellation PageRank.
  No DB-level FK from this table to ``athlete_constellations_v2`` — the existing schema's
  convention (see ``graph_edges`` above) is application-enforced referential integrity for
  cross-table node/composite identity, not a Postgres constraint; this migration follows
  that convention rather than adding a composite FK found nowhere else in ``db/models.py``.

Deliberately NOT touched: ``rating_engine_runs``, ``athlete_rating_states_v2`` (0035,
unchanged), and nothing in the App-facing contract surface (``graphs``/``graph_edges``/
``technique_nodes``/``published_athlete_graphs``) — this is not a cross-module PR.

RLS: same as 0035 and for the same reason — **enabled, no policy, deny by default.** V1
is still production, no consumer has migrated (wave 8), and these are shadow numbers a
scraper could otherwise present as "the GrapplingArc rating" before any product decision
exists about node- or constellation-level ratings specifically (which, per ADR-03/ADR-08
above, don't even have a settled methodology yet — a stronger reason to keep them dark
than the global state 0035 already gates). Service-role replay writes bypass RLS; the
read policy is wave 8's job, alongside whatever consumer justifies it.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-17
"""

from __future__ import annotations

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        set local search_path = public, extensions;

        create table if not exists public.athlete_node_rating_states_v2 (
            run_id uuid not null
              references public.rating_engine_runs(id) on delete cascade,
            athlete_id uuid not null
              references public.athletes(id) on delete cascade,
            node_key text not null,
            rating double precision not null,
            rating_deviation double precision not null,
            volatility double precision not null,
            bouts_observed integer not null default 0,
            occurrences integer not null default 0,
            first_seen_at timestamptz,
            last_seen_at timestamptz,
            constellation_fingerprint text,
            primary key (run_id, athlete_id, node_key)
        );

        -- athlete_id: cross-run "this athlete's node history" queries (dossier use case).
        -- node_key: cross-athlete "who else has this node" queries (per-node comparison
        -- use case from the plan's own list) — the PK leads with run_id, so neither is
        -- covered by the PK index alone once run_id isn't part of the filter.
        create index if not exists ix_athlete_node_rating_states_v2_athlete_id
          on public.athlete_node_rating_states_v2 (athlete_id);
        create index if not exists ix_athlete_node_rating_states_v2_node_key
          on public.athlete_node_rating_states_v2 (node_key);

        create table if not exists public.athlete_constellations_v2 (
            run_id uuid not null
              references public.rating_engine_runs(id) on delete cascade,
            athlete_id uuid not null
              references public.athletes(id) on delete cascade,
            fingerprint text not null,
            hub_node_key text not null,
            member_count integer not null,
            internal_edge_count integer not null,
            support_bouts integer not null default 0,
            modularity double precision not null,
            stability_mean double precision not null,
            stability_p10 double precision not null,
            summary_json jsonb not null default '{}'::jsonb,
            primary key (run_id, athlete_id, fingerprint)
        );

        create index if not exists ix_athlete_constellations_v2_athlete_id
          on public.athlete_constellations_v2 (athlete_id);

        create table if not exists public.athlete_constellation_members_v2 (
            run_id uuid not null
              references public.rating_engine_runs(id) on delete cascade,
            athlete_id uuid not null
              references public.athletes(id) on delete cascade,
            fingerprint text not null,
            node_key text not null,
            pagerank double precision not null,
            weighted_pagerank double precision not null,
            degree integer not null,
            primary key (run_id, athlete_id, fingerprint, node_key)
        );

        create index if not exists ix_athlete_constellation_members_v2_node_key
          on public.athlete_constellation_members_v2 (node_key);

        -- RLS LIGADA, NENHUMA POLICY — nega por padrão. Mesmo raciocínio da 0035: sombra,
        -- sem consumidor migrado (wave 8), leitura pública prematura de um número sem
        -- decisão de produto (aqui, sem sequer metodologia fechada — ADR-03/ADR-08).
        alter table public.athlete_node_rating_states_v2 enable row level security;
        alter table public.athlete_constellations_v2 enable row level security;
        alter table public.athlete_constellation_members_v2 enable row level security;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        drop index if exists public.ix_athlete_constellation_members_v2_node_key;
        drop table if exists public.athlete_constellation_members_v2;

        drop index if exists public.ix_athlete_constellations_v2_athlete_id;
        drop table if exists public.athlete_constellations_v2;

        drop index if exists public.ix_athlete_node_rating_states_v2_athlete_id;
        drop index if exists public.ix_athlete_node_rating_states_v2_node_key;
        drop table if exists public.athlete_node_rating_states_v2;
        """
    )
