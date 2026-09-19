"""Technique study digest table (Pro) + close the ``matches`` grant leak.

Two independent changes, bundled because both surfaced from the same 2026-09-18 exploration
(``docs`` plan "ESTUDO SOB MEDIDA A PARTIR DE UM PROBLEMA"):

1. ``technique_studies`` — one row per corpus ``node_key`` (frequency/centrality/next-moves/
   counters/chains/video refs), batch-published weekly by ``jobs.publish_technique_studies``
   from the PUBLIC competition corpus only (``matches`` + ``athletes``, ``owner_kind='athlete'``
   graphs). No user data anywhere in the payload — see root CLAUDE.md "Public vs Private Data".
   Mirrors ``athlete_dossiers`` (0021): text PK instead of a FK'd UUID (the corpus label space
   is not the same axis as ``technique_nodes.node_key`` identity — a study can exist for a
   label with no shared-library row yet), no FK anywhere else, ``schema_version``/``payload``/
   ``generated_at``. RLS + the ``technique_studies_pro_select`` policy + grant are copied
   verbatim from ``athlete_dossiers_pro_select`` (0023:200-219) — same shape, same "batch
   writer is service-role, entitled reader only" contract.

2. ``matches`` — a prod probe run the same day (2026-09-18, orchestrator, read-only) found RLS
   already ENABLED on ``matches`` (deny-all: zero policies, most likely the same untracked
   2026-06-25 hardening pass 0023's docstring describes for ``ensure_rls``) but ``anon`` and
   ``authenticated`` STILL held table-level INSERT/SELECT/UPDATE/DELETE grants left over from
   before RLS was turned on — a privilege that does nothing today only because no policy grants
   a matching row, and would silently reopen the whole table the moment anyone adds one without
   checking grants too. Nothing in the App or the site ever reads ``matches`` over PostgREST
   (the App never queries it at all; the public site is generated ahead-of-time by
   ``export/site_data.py`` over a service-role connection) — verified by the same probe, so this
   is a dead grant, not a used one. ``revoke`` here is enable-row-level-security's actual
   enforcement point: RLS with zero policies is deny-all ONLY once nobody still holds a raw
   grant that RLS is gating. No policy is created — the service-role publisher bypasses RLS by
   design, and nothing else should ever read this table directly.

   (The plan also named ``athlete_matches`` for the same closure — a second probe query found
   that table does not exist in this schema at all, so there is nothing to revoke there.)

   ``downgrade()`` intentionally does **not** re-grant ``matches`` back to ``anon``/
   ``authenticated`` — the grant was dead weight before this migration existed and undoing this
   revision must not reintroduce the leak it closes. Downgrading only drops
   ``technique_studies``.

Revision ID: 0067
Revises: 0066
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technique_studies",
        sa.Column("node_key", sa.Text(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        """
        alter table public.technique_studies enable row level security;

        drop policy if exists technique_studies_pro_select on public.technique_studies;
        create policy technique_studies_pro_select on public.technique_studies
          for select using (
            exists (
              select 1 from public.profiles p where p.id = auth.uid() and p.is_pro = true
            )
          );

        grant select on public.technique_studies to authenticated;
        """
    )
    op.execute(
        """
        -- Dead grant closure (see module docstring §2) — RLS was already enabled+policy-less
        -- (deny-all) on this table before this revision; this revokes the leftover table-level
        -- privileges that RLS's deny-all depends on nobody still holding.
        revoke insert, select, update, delete on public.matches from anon, authenticated;
        """
    )


def downgrade() -> None:
    op.execute("revoke select on public.technique_studies from authenticated;")
    op.execute(
        "drop policy if exists technique_studies_pro_select on public.technique_studies;"
    )
    op.drop_table("technique_studies")
    # matches' grants are deliberately NOT restored — see module docstring §2.
