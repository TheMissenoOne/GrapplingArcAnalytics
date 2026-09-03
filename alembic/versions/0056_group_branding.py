"""Group branding: logo, description, accent color — the academy is customizable.

**The columns.** ``groups.logo_url``/``description``/``accent_color`` — all nullable, no
migration of existing rows needed. ``accent_color`` gets a CHECK (``#rrggbb``, case-insensitive
hex) because it lands straight in CSS on the Web side; a malformed value there is a rendering
bug, not just an ugly one, so the constraint is cheaper than trusting every future writer.

**Who can write it.** Storage (below) is explicitly owner/professor per the product ask ("the
academy is customizable" — a professor runs the room day to day, not just the owner). The
``groups`` table itself had **no UPDATE policy at all** before this migration (checked 0025-0053:
every existing group-level write IS owner-gated — ``group_invites_owner_all``, ``set_member_role``,
``transfer_group_ownership``. Group-scoped CONTENT tables — class plans, instructionals — use
``is_group_owner_or_professor``, but those are rows ABOUT the group, not the ``groups`` row
itself.) Orchestrator decision (2026-09-04): keep ``groups`` UPDATE **owner-only**, consistent
with every other write on this table — ``groups_update_owner`` gates on ``owner_id = auth.uid()``
directly, the same shape ``group_invites_owner_all`` uses, no helper needed since ``owner_id`` is
a column on the row being checked. A professor administers CONTENT (instructionals, class plans,
now the ``gym-logos`` bucket), never the ``groups`` row itself. The Web settings form
(``BrandingSettings``) is owner-only to match.

**Storage.** ``gym-logos`` — public (read is the whole point: the logo renders on every screen
that shows the gym), 1 MB cap, ``image/*`` only, both expressed at the bucket level so a bad
upload never reaches an object policy at all. Write (insert/update/delete) is owner/professor of
the group named by the path's first segment (``<group_id>/logo.<ext>``), same shape as
0050's ``instructional-media`` policies. No SELECT policy — a public bucket serves objects
through Supabase's unauthenticated public-URL route without going through `storage.objects` RLS,
same as every other public Supabase Storage bucket; the private buckets (0024/0042/0050) needed a
SELECT policy for exactly the opposite reason, they are NOT public.

**Not done here — deliberately left for the orchestrator, out of this migration's scope
(alembic/versions/db/models.py/tests only):** ``supabase/functions/delete-account/index.ts``'s
``PRIVATE_BUCKETS``/``GROUP_KEYED_BUCKETS`` lists (see that file's own docstring — "add a bucket
to the schema, add it here in the same change") should gain ``gym-logos`` in
``GROUP_KEYED_BUCKETS`` the same way ``instructional-media`` is, so
``tests/test_private_buckets_parity.py`` stays green. That edit is out of this migration's scope.

Revision ID: 0056
Revises: 0055
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

_ACCENT_COLOR_CHECK = "ck_groups_accent_color_hex"


def upgrade() -> None:
    # ── columns ────────────────────────────────────────────────────────────
    op.add_column("groups", sa.Column("logo_url", sa.Text(), nullable=True))
    op.add_column("groups", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("groups", sa.Column("accent_color", sa.Text(), nullable=True))
    op.create_check_constraint(
        _ACCENT_COLOR_CHECK,
        "groups",
        "accent_color is null or accent_color ~ '^#[0-9a-fA-F]{6}$'",
    )

    # ── RLS: groups gains its first UPDATE policy, owner-only ────────────────
    # Same shape as group_invites_owner_all (0024) — owner_id is a column on THIS row, so no
    # helper function is needed, just the direct comparison every other groups-row write uses.
    op.execute("drop policy if exists groups_update_owner on public.groups;")
    op.execute("""
    create policy groups_update_owner on public.groups for update to authenticated
    using (owner_id = auth.uid())
    with check (owner_id = auth.uid());
    """)

    # ── storage: gym-logos, public read, owner/professor write ──────────────
    op.execute("""
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('gym-logos', 'gym-logos', true, 1048576, array['image/*'])
    on conflict (id) do update set
      public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;
    """)

    op.execute("drop policy if exists gym_logos_owner_prof_insert on storage.objects;")
    op.execute("""
    create policy gym_logos_owner_prof_insert on storage.objects for insert to authenticated
    with check (bucket_id = 'gym-logos'
                and public.is_group_owner_or_professor(((storage.foldername(name))[1])::uuid));
    """)

    op.execute("drop policy if exists gym_logos_owner_prof_update on storage.objects;")
    op.execute("""
    create policy gym_logos_owner_prof_update on storage.objects for update to authenticated
    using (bucket_id = 'gym-logos'
           and public.is_group_owner_or_professor(((storage.foldername(name))[1])::uuid))
    with check (bucket_id = 'gym-logos'
                and public.is_group_owner_or_professor(((storage.foldername(name))[1])::uuid));
    """)

    op.execute("drop policy if exists gym_logos_owner_prof_delete on storage.objects;")
    op.execute("""
    create policy gym_logos_owner_prof_delete on storage.objects for delete to authenticated
    using (bucket_id = 'gym-logos'
           and public.is_group_owner_or_professor(((storage.foldername(name))[1])::uuid));
    """)


def downgrade() -> None:
    op.execute("drop policy if exists gym_logos_owner_prof_delete on storage.objects;")
    op.execute("drop policy if exists gym_logos_owner_prof_update on storage.objects;")
    op.execute("drop policy if exists gym_logos_owner_prof_insert on storage.objects;")
    # Bucket itself is NOT dropped — same reasoning as 0042/0050: a downgrade must not be a
    # data-destroying operation. An empty policy-less bucket left behind costs nothing.

    op.execute("drop policy if exists groups_update_owner on public.groups;")

    op.drop_constraint(_ACCENT_COLOR_CHECK, "groups", type_="check")
    op.drop_column("groups", "accent_color")
    op.drop_column("groups", "description")
    op.drop_column("groups", "logo_url")
