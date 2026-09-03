"""Video-analysis pipeline contract: enqueue a round/session video, store the worker's read.

Fase 0 of the video-pro plan (App: `services/videoUploadQueue.ts` -> Analytics:
`session_video_jobs` / `session_video_analysis` -> `scripts/video_jobs.py`, later fases).

**Two tables, one job/result split**, same shape as `user_performance_snapshots` /
`athlete_dossiers` (0021): `session_video_jobs` is the queue row a client can create (through
the `enqueue_video_job` RPC only — no client INSERT/UPDATE policy on the table itself);
`session_video_analysis` is the worker's 1:1 output, written only by the service-role
connection the batch script (`scripts/video_jobs.py`, Fase 3) uses, which bypasses RLS
entirely. Neither table gets a client-facing write policy — the RPC is the only door in for a
job, and there is no client door in for a result.

**No FK from `session_video_jobs.session_id` to `user_sessions.id`, deliberately.** The app is
offline-first; the session that owns a round may not have synced yet when its video enqueues.
`owner_id -> profiles(id) on delete cascade` already covers account deletion.
`ponytail: this is a coarse device-generated key, not sessions' cascade; if the video pipeline
ever needs to join against the actual session row, backfill a nullable FK once sessions are
known to always precede their video's enqueue, don't force it here.`

**Ownership proof for the RPC is the storage path prefix** — `split_part(storage_path, '/',
1) = auth.uid()::text` — the identical rule the `session-videos` bucket policy (0024) already
enforces at the object layer; the RPC just re-checks it before trusting the caller's claimed
`storage_path` for the row it's about to own.

**RLS is owner-only SELECT, no `is_pro` predicate** — unlike `user_performance_snapshots`
(0021/0023), where the policy re-checks `profiles.is_pro` on every read. The Pro gate here is
upstream, at upload time (`videoUploadQueue.ts`'s drain gate — `file-sync-is-pro`): a job can
only exist because a Pro upload succeeded. Gating the read too would mean an entitlement lapse
revokes access to an analysis the user already paid to generate — see the video-pro plan, D9.

**`profiles.face_ref_path` / `face_consent_at`** extend 0023's per-column grant list (0051's
own convention: a column added after 0023 is implicitly ungranted to `authenticated` until
named explicitly) so the owner can set and revoke their own selfie consent without any new RLS
policy — `profiles_update_own` (0023) already gates the row to `id = auth.uid()`. NULL
`face_consent_at` is the only source of truth the worker reads before touching the selfie
object; nothing here uploads or reads storage.

**No bucket added.** Video stays in `session-videos` (0024, unchanged path); the selfie/PDF/
clips this pipeline later writes to `user-media` (0042) — both already in `PRIVATE_BUCKETS`
(`supabase/functions/delete-account/index.ts`), so `tests/test_private_buckets_parity.py`
needs no change (nothing here inserts into `storage.buckets`).

Privacy class: **private**, user-fed footage (root `CLAUDE.md` / this repo's `CLAUDE.md`
Public vs Private Data section). Every column here is owner-keyed; nothing this table feeds is
an aggregate, a centroid, an ELO input, or a site/export artifact. `analysis/round_analysis.py`
and `scripts/video_jobs.py` (Fase 3) carry the same docstring reminder at the point they read
these rows.

Scope note (test coverage): source-scan for the RLS/RPC/grant shape (same reason 0045/0054/
0055/0057 are source-scanned — this suite runs SQLite in-memory and never executes Postgres
DDL), plus a SQLite round-trip for the two tables' plain columns via `db/models.py`.
`tests/test_session_video_analysis.py`.

Revision ID: 0058
Revises: 0057
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


_ENQUEUE_VIDEO_JOB = """
create or replace function public.enqueue_video_job(
  p_session_id text,
  p_round_id text,
  p_media_id text,
  p_storage_path text,
  p_round_kind text,
  p_context jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id uuid;
begin
  -- The first path segment is the same proof of ownership the session-videos bucket
  -- policy (0024) already enforces at the object layer.
  if split_part(p_storage_path, '/', 1) <> auth.uid()::text then
    raise exception 'not_owner' using errcode = 'P0001';
  end if;
  if p_round_kind not in ('round', 'full_session') then
    raise exception 'bad_round_kind' using errcode = 'P0001';
  end if;

  insert into public.session_video_jobs
    (owner_id, session_id, round_id, media_id, storage_path, round_kind, context)
  values
    (auth.uid(), p_session_id, p_round_id, p_media_id, p_storage_path, p_round_kind,
     coalesce(p_context, '{}'::jsonb))
  on conflict on constraint uq_session_video_jobs_owner_media
    do update set updated_at = now()
  returning id into v_id;

  return v_id;
end;
$$;
"""


def upgrade() -> None:
    # ── profiles: opt-in selfie reference for actor identification ──────────────────
    op.add_column("profiles", sa.Column("face_ref_path", sa.Text(), nullable=True))
    op.add_column(
        "profiles", sa.Column("face_consent_at", sa.DateTime(timezone=True), nullable=True)
    )

    # ── session_video_jobs ────────────────────────────────────────────────────────────
    op.create_table(
        "session_video_jobs",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("round_id", sa.Text(), nullable=True),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("round_kind", sa.Text(), nullable=False, server_default="round"),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("context", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "round_kind IN ('round', 'full_session')", name="ck_session_video_jobs_round_kind"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'done', 'failed')",
            name="ck_session_video_jobs_status",
        ),
        sa.UniqueConstraint("owner_id", "media_id", name="uq_session_video_jobs_owner_media"),
    )
    # Partial — the worker's claim query. Raw SQL, not modeled (0051's convention: a partial
    # index has no db/models.py representation).
    op.execute(
        "create index if not exists ix_session_video_jobs_queued "
        "on public.session_video_jobs (status, created_at) where status = 'queued'"
    )

    # ── session_video_analysis — 1:1 with the job, keyed by job_id ──────────────────
    op.create_table(
        "session_video_analysis",
        sa.Column(
            "job_id",
            UUID(as_uuid=False),
            sa.ForeignKey("session_video_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("round_id", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("motion", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("events", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sequences", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("difficulty_derived", sa.Numeric(4, 1), nullable=True),
        sa.Column(
            "difficulty_inputs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("highlights", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("pdf_path", sa.Text(), nullable=True),
        sa.Column("clip_paths", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_session_video_analysis_owner_session",
        "session_video_analysis",
        ["owner_id", "session_id"],
    )

    # ── RLS: owner-only SELECT, no is_pro predicate (D9) — write is RPC/service-role only ──
    op.execute(
        """
        alter table public.session_video_jobs     enable row level security;
        alter table public.session_video_analysis enable row level security;

        drop policy if exists session_video_jobs_owner_select on public.session_video_jobs;
        create policy session_video_jobs_owner_select on public.session_video_jobs
          for select using (owner_id = auth.uid());

        drop policy if exists session_video_analysis_owner_select
          on public.session_video_analysis;
        create policy session_video_analysis_owner_select on public.session_video_analysis
          for select using (owner_id = auth.uid());
        """
    )

    # ── Grants ─────────────────────────────────────────────────────────────────────────
    op.execute(
        "grant select on public.session_video_jobs, public.session_video_analysis "
        "to authenticated;"
    )
    # Extends 0023's per-column list on profiles (0051's convention) — owner sets/revokes
    # their own selfie consent; no policy change needed, profiles_update_own already gates
    # the row.
    op.execute("grant update (face_ref_path, face_consent_at) on public.profiles to authenticated;")

    # ── RPC: the only client write path onto session_video_jobs ────────────────────────
    op.execute(_ENQUEUE_VIDEO_JOB)
    op.execute(
        "revoke all on function public.enqueue_video_job"
        "(text, text, text, text, text, jsonb) from public;"
    )
    op.execute(
        "revoke all on function public.enqueue_video_job"
        "(text, text, text, text, text, jsonb) from anon;"
    )
    op.execute(
        "grant execute on function public.enqueue_video_job"
        "(text, text, text, text, text, jsonb) to authenticated;"
    )


def downgrade() -> None:
    op.execute(
        "drop function if exists public.enqueue_video_job"
        "(text, text, text, text, text, jsonb);"
    )

    op.execute(
        "revoke update (face_ref_path, face_consent_at) on public.profiles from authenticated;"
    )
    op.execute(
        "revoke select on public.session_video_jobs, public.session_video_analysis "
        "from authenticated;"
    )

    op.execute(
        "drop policy if exists session_video_analysis_owner_select "
        "on public.session_video_analysis;"
    )
    op.execute(
        "drop policy if exists session_video_jobs_owner_select on public.session_video_jobs;"
    )

    op.drop_index("ix_session_video_analysis_owner_session", table_name="session_video_analysis")
    op.drop_table("session_video_analysis")

    op.execute("drop index if exists ix_session_video_jobs_queued;")
    op.drop_table("session_video_jobs")

    op.drop_column("profiles", "face_consent_at")
    op.drop_column("profiles", "face_ref_path")
