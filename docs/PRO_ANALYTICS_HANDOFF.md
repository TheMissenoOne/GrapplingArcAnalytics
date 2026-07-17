# Pro analytics M4 handoff

M4 adds Alembic revision `0021`:

- `profiles.is_pro` — `false` by default; only an admin/service-role process may grant it.
- `user_performance_snapshots` — owner/cadence/period unique snapshots.
- `athlete_dossiers` — one gated dossier per athlete.

No production mutation is automated by this repository. Human/orchestrator steps:

```bash
DATABASE_URL=<prod> uv run alembic upgrade head
psql "$DATABASE_URL" -f db/auth_setup.sql
uv run alembic history
```

Then verify against Postgres:

1. `alembic_version` is `0021`.
2. RLS is enabled on both new tables.
3. An authenticated free profile cannot select either table.
4. An authenticated Pro profile can select its own snapshots and athlete dossiers.
5. An authenticated client cannot update `profiles.is_pro`; admin/service-role can grant it.
6. A decreasing `updated_at` stale-write probe for existing sync tables still no-ops.

The publisher workflow is manual-only. Do not add its daily/weekly cron until the paired App M5
contract has passed end-to-end validation. The workflow's `production` environment must protect
`PROD_DATABASE_URL`; never commit a DSN or service key.

Repository-wide `uv run ruff check .` currently includes pre-existing findings in `scripts/` and
one unrelated exporter naming finding. CI hard-gates pytest and Ruff on the M4 files; the full lint
debt stays a separate cleanup task.
