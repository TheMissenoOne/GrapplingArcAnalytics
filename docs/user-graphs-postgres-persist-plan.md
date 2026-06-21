# Plan: Persist User Graphs in PostgreSQL + Central Analytics + Admin Dashboard

## Context

Today every GrapplingArc user's "game map" (a graph of techniques/positions/concepts
with edges and ELO) lives **only on their phone** in AsyncStorage. The analytics repo
(`GrapplingArcAnalytics`) can already *parse* an exported bundle
(`schemas/app_types.py` → `UserBundle.from_json`) and the realtime FastAPI server
already builds athlete graphs in memory (`realtime/app.py` `/export` →
`analysis/athlete_graph.build_athlete_graph` → `app.state`, with an optional Qdrant
upsert). Nothing is durable or centralized, so cross-user analysis is impossible.

The goal: make a **central PostgreSQL the shared source of truth for user graphs**, so we can
(1) run population-scale analytics, (2) let admins hand-register pro-athlete matches to
build pro graphs, (3) cluster players into **archetypes**, and (4) ship **pre-built pro
graphs** as an acquisition hook. Decisions from the product owner: the app **reads/writes the
DB directly (no custom HTTP API)**; admin dashboard is **FastAPI + Jinja**; build a **full
vertical slice**.

## Feasibility verdict

**Feasible and a good fit** — the ingestion shape already exists (`UserBundle`,
`build_athlete_graph`, the `/export` accumulation pattern); Postgres is purely **additive**
to the parquet/CV pipeline. Two hard constraints shape the design:

1. **A phone cannot safely hold raw Postgres credentials.** "Direct DB, no API" is only
   secure via a **managed Postgres with a built-in data API + Row-Level Security + Auth —
   i.e. Supabase** (or PostgREST). The RN app uses `supabase-js` (feels like direct DB, no
   custom server); the analytics repo connects to the **same** Postgres with a privileged
   service role via SQLAlchemy. **This plan assumes Supabase-managed Postgres.**
2. **The app repo (`TheMissenoOne/GrapplingArcApp`) is NOT reachable in this session**
   (scope is only `grapplingarc` + `grapplingarcanalytics`; no `add_repo` tool available).
   So the in-app `supabase-js` client cannot be built here. Everything **analytics-side is
   buildable now**; the app-side client is specified as a contract and is a follow-up once
   the app repo is added to a session.

## Architecture

```
 RN app (supabase-js, RLS)  ─┐
                             ├──►  Supabase Postgres (shared)  ◄── SQLAlchemy service role
 admin (FastAPI+Jinja)  ─────┘            ▲     │                    (GrapplingArcAnalytics)
                                          │     └──► archetype clustering, aggregate analytics
 realtime CV server (optional persist) ───┘            │
                                                       └──► published pro graphs (app reads)
```

Analytics repo **owns the schema** (SQLAlchemy 2.0 models + Alembic migrations). One unified
`graphs` table keyed by `(owner_kind, owner_id)` holds **both** user and athlete graphs so
the same analytics/clustering code runs over both.

## Database schema (new, owned by analytics repo)

SQLAlchemy 2.0 declarative + Alembic. Mapped from existing dataclasses in
`schemas/app_types.py` and `analysis/athlete_graph.py`.

- **profiles** ← `UserAuth`: `id uuid PK` (= Supabase `auth.users.id`), `full_name`,
  `belt_rank`, `belt_degrees int`, `is_guest bool`, `archetype_id int FK`, timestamps.
- **athletes** (admin-managed pros): `id uuid PK`, `name`, `nickname`, `team`,
  `weight_class`, `belt`, `source` (`manual|bjjheroes|adcc`), `elo float`,
  `archetype_id int FK`, `is_published bool`, timestamps.
- **graphs** (unified) ← `Graph`: `id uuid PK`, `owner_kind enum('user','athlete')`,
  `owner_id uuid`, `user_elo float`, `schema_version int`, `archetype_id int FK`,
  `updated_at`, `synced_at`, `unique(owner_kind, owner_id)`.
- **graph_nodes** ← `GraphNode`: `id uuid PK`, `graph_id FK`, `node_key text`
  (stable key = normalized label via `analysis/names.py`, NOT the app's local node id),
  `label`, `type` (technique/position/concept), `node_type`, `computed_elo float`,
  `usage_count int`, `trend`, `unique(graph_id, node_key)`.
- **graph_edges** ← `GraphEdge`: `id uuid PK`, `graph_id FK`, `edge_key`, `source_key`,
  `target_key`, `elo float`, `setup`, `unique(graph_id, edge_key)`.
- **athlete_matches** (admin entry → feeds graph build): `id uuid PK`, `athlete_id FK`,
  `opponent_name`, `event`, `year int`, `weight_class`, `win_type`, `stage`, `submission`,
  `won bool`, `sequence jsonb` (ordered `RoundEntry`-shaped events), `created_by uuid`,
  `created_at`.
- **archetypes**: `id serial PK`, `name`, `description`, `centroid jsonb`,
  `feature_version text`, `created_at`.
- **bundle_imports** (provenance/audit): `id uuid PK`, `owner_id`, `raw jsonb`, `created_at`.

**RLS policies (Supabase):** a user reads/writes only their own rows
(`owner_kind='user' AND owner_id = auth.uid()`); `is_published` athlete graphs are
world-readable; all admin writes go through the service role (bypasses RLS).

## Build steps (analytics repo — executable now)

1. **DB package** — `db/base.py` (engine/session from `DATABASE_URL`), `db/models.py`
   (the tables above), `db/repository.py` (`upsert_graph_from_bundle`, `register_match`,
   `upsert_athlete`, `graphs_for_clustering`, `save_archetypes`, `publish_athlete`),
   `db/ingest.py` (CLI: `python -m db.ingest bundle.json`, reuses `UserBundle.from_json`).
2. **Migrations** — `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_init.py`.
3. **Deps** — add `[project.optional-dependencies] postgres = ["sqlalchemy>=2.0",
   "alembic>=1.13", "psycopg[binary]>=3.2"]` in `pyproject.toml`. Core stays light.
4. **Archetype recognition** — `analysis/archetype.py`: `graph_feature_vector(graph)`
   (node-type distribution: guard/pass/sweep/submission/takedown/control share + edge
   density + avg computed_elo bucket + offense/defense ratio; reuse the vectorization idea
   from `analysis/similarity.py:fighter_vectors`), `fit_archetypes(vectors, k)` (sklearn
   `KMeans`, already a dep), `assign_archetype(vec, centroids)`. Persist centroids →
   `archetypes`, labels → `graphs.archetype_id`. Reuse `analysis/similarity.top_similar`
   for "your game ≈ pro X" matchmaking.
5. **Pro-graph export** — `export/athlete_graph_export.py`: inverse of
   `UserBundle.from_json` (nodes with nested `data:{computedElo,type,usageCount,trend}`,
   edges with `data:{elo,setup}`). Because the app reads the DB directly, "export" mainly
   means athlete graphs live in `graph_nodes/graph_edges` under `owner_kind='athlete'`; add
   a `published_athlete_graphs` SQL view the app SELECTs. Also emit the app-shaped JSON for
   parity with `export/tech_library.py`.
6. **Admin dashboard** — new `admin/` FastAPI app (kept separate from the public CV server),
   `admin/server.py`, `admin/auth.py` (cookie session, admin password from env),
   `admin/templates/*.html` (Jinja). Routes: list/create athletes; **register match** form
   (reuse the node vocab picker `cv/vocab_map.load_app_nodes` + `realtime/app.py:_node_options`)
   → `repository.register_match` → `build_athlete_graph` → persist → recompute ELO via
   `analysis/elo_calibration.compute_adcc_elo`; view athlete graph; **analytics** page
   (reuse `analysis/technique_freq.py` + archetype distribution); buttons to **recompute
   archetypes** and **publish** an athlete graph.
7. **Realtime loop closure (optional)** — when `DATABASE_URL` is set, have
   `realtime/app.py` `/export` + `/capture` also persist via `db.repository` instead of only
   `app.state`, so the CV server and DB agree.
8. **Tests** — `tests/test_db.py`, `tests/test_archetype.py`, `tests/test_admin.py`. Gate DB
   tests behind the `postgres` extra; use a SQLite fallback for model/round-trip tests and
   (optionally) testcontainers Postgres for migration/RLS-free integration.

## App-side contract (GrapplingArcApp — BLOCKED pending repo access)

On dev branch `claude/user-graphs-postgres-persist-4o1j63`, once the repo is added:
add `supabase-js`; on graph change, upsert into `graphs/graph_nodes/graph_edges` scoped to
`auth.uid()`; read `published_athlete_graphs` for the "pro game" feature; map the local
`UserAuth.id` to the Supabase auth user id. No raw PG creds in the client — Supabase anon
key + RLS only.

## Keep existing systems working

Postgres is **additive**: the parquet ETL (`pipelines/`), `analysis/*`,
`export/tech_library.py`, and the realtime CV server are untouched and keep passing. The new
deps are an **optional extra**, so default installs and the existing `uv run pytest` stay
green.

## Gaps in current architecture (make this harder)

1. **App repo not reachable here** → app-side client can't be built this session.
2. **Mobile-direct-Postgres is unsafe without Supabase/PostgREST + RLS** (the core caveat).
3. **Identity:** app `UserAuth.id` must equal Supabase `auth.uid()`; current bundles use
   local/guest ids — need an identity mapping + guest-account handling.
4. **Node-id stability:** app `GraphNode.id` is local/ephemeral; clustering and merges need a
   stable `node_key` (normalized label via `analysis/names.py`), not the raw id.
5. **No auth anywhere today** (the CV server is open); dashboard + DB need real auth + RLS.
6. **Realtime server keeps graphs in `app.state`** — a second source of truth that must be
   reconciled with Postgres.
7. **Privacy/consent:** centralizing personal game-maps + clustering changes the privacy
   posture (today storage is local-only). `PRIVACY_POLICY.md` must be updated and a
   consent + data-deletion (GDPR) path added before turning on sync.
8. **Three schema mirrors** (app TS types, `schemas/app_types.py`, SQLAlchemy models) — the
   DB schema should become the single source of truth to avoid drift.

## Opportunities this unlocks

1. **Cross-user analytics** (impossible while siloed on phones): meta trends, belt-level
   benchmarks, technique popularity at population scale.
2. **Archetype matchmaking:** "your game is a 78% match to <pro>" via existing
   `similarity.top_similar`.
3. **Pre-built pro graphs as an acquisition hook** — athletes find their game already mapped.
4. **Population/archetype priors fed back into the realtime CV server** (today priors are
   per-athlete, in-memory only).
5. **Live benchmarking** — the TODO `export/benchmark_results.py` becomes a server-side,
   always-fresh feature instead of a one-off JSON.
6. **Admin-curated athlete data** complements scraped BJJ Heroes / ADCC datasets → richer
   training signal for similarity and archetypes.

## Verification

- `alembic upgrade head` against an ephemeral Postgres → all tables exist.
- `python -m db.ingest <fixture_bundle_with_graph>.json` → rows in
  `graphs/graph_nodes/graph_edges`; round-trip via `export/athlete_graph_export.py` matches
  the input graph.
- Admin: run `admin/server.py`, create an athlete, register a match with a known `sequence`,
  assert persisted nodes/edges equal `build_athlete_graph()` output; publish; query the
  `published_athlete_graphs` view.
- Archetype: seed N synthetic graphs → `fit_archetypes(k=4)` → labels persisted →
  `assign_archetype` of a new graph is stable.
- `uv run pytest && uv run ruff check . && uv run mypy .` green; confirm parquet ETL and the
  realtime server still import/run unchanged.

## Representative files

`pyproject.toml` (extra) · `db/{base,models,repository,ingest}.py` ·
`alembic/…` · `analysis/archetype.py` · `export/athlete_graph_export.py` ·
`admin/{server,auth}.py` + `admin/templates/*.html` · `realtime/app.py` (optional persist) ·
`tests/test_{db,archetype,admin}.py` · `PRIVACY_POLICY.md` (consent/deletion).
