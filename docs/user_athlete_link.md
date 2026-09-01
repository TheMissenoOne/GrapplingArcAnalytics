# User ↔ athlete link, and importing an offline bundle as sessions

## Why sessions, not a graph push

The App never pulls its graph from the server — it only PUSHES it, transactionally, via
`rpc('replace_user_graph', ...)` (alembic 0037). What the App DOES pull is `user_sessions`
(`sessionSync.ts:getUserSessionsSince`, filtered on `updated_at >= last_sync_at` from
`user_sync_meta`), which it merges locally and — whenever the merge actually changes something —
replays end to end through `reprocessAllSessions()` to rebuild the graph (`syncEngine.ts`).

So a competition history can only reach a user's on-device map as `user_sessions` rows. Every row
this flow writes needs `updated_at = now()`, or a `last_sync_at` newer than the row (as it will be
for any account that has already synced once) makes the App's incremental pull skip it silently.

## Why the link lives on `profiles`, not `athletes`

`profiles` is RLS owner-only (alembic 0023). `athletes` is read publicly. Alembic 0051 adds
`profiles.athlete_id -> athletes.id` (nullable, `ON DELETE SET NULL`, partial-unique) — the
world-readable table learns nothing about which private account it's linked to. Same one-way
shape as `graph_nodes.canonical_node_key` (0037): private may point at public, never the reverse.

A client cannot self-link: 0023's grants list `authenticated`'s writable `profiles` columns
explicitly, and `athlete_id` is not on either list — only an admin/service-role session can set
it (`profiles_select_own`'s table-level SELECT grant already covers the new column for reading).

## Applying the migration

```bash
cd GrapplingArcAnalytics
DATABASE_URL=<prod DSN, never pasted into chat/logs> uv run alembic upgrade head
```

## `scripts/import_user_bundle.py`

```bash
uv run python -m scripts.import_user_bundle --profile <uuid> --bundle <path> [--athlete <uuid>] [--dry-run]
```

- Always: every session in the bundle's `sessions[]` (deduped by `id`, keeping the LAST
  occurrence) is written to `user_sessions`, `data` passed through as-is.
- `--athlete`: also sets `profiles.athlete_id`, and converts every match
  `db.repository.get_matches_for_athlete` returns for that athlete into a synthetic session —
  **except** a bout `analysis.attribution.bout_flags(...)["role_reliable"]` marks False (most
  commonly `one_sided`: every recorded event filed under one athlete, so "you" vs "your
  opponent" can't be trusted). Skipped bouts are reported in the summary, never guessed at.
- `--dry-run` prints the same summary (sessions found/deduped, matches converted/skipped, labels
  with no match in `data/taxonomy/library_lookup.json`) and opens no write transaction. Without
  `--athlete`, the dry-run needs no `DATABASE_URL` at all — the dump-only path never touches
  the DB.

## Post-apply drift check (skill `supabase-schema-migration` §7)

```sql
select relname, relrowsecurity
from pg_class
where relnamespace = 'public'::regnamespace and relkind = 'r'
order by relname;

select schemaname, tablename, policyname, cmd, roles
from pg_policies
where schemaname = 'public'
order by tablename, policyname;

select version_num from alembic_version;
```

Nothing in 0051 changes RLS or grants (`profiles` already has both, unchanged) — the check here
is that `alembic_version` reads `0051` and `profiles.athlete_id` exists, not a policy diff.
