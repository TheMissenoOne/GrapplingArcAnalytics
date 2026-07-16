"""Pin ``search_path`` on the stale-write guard function (advisor lint 0011).

Supabase's security advisor flags ``guard_user_sessions_stale_write`` (added in 0019) for a
role-mutable ``search_path`` (lint 0011, ``function_search_path_mutable``). The risk is low here —
the function is NOT ``SECURITY DEFINER`` and references no schema objects at all (it only compares
``NEW.updated_at``/``OLD.updated_at`` and returns ``NEW``/``NULL``), so there is no search-path
hijack / privilege-escalation path. But an unpinned ``search_path`` is a standing best-practice
warning, so we close it the tracked way (a migration, keeping live == alembic source) rather than a
hand ``alter`` against prod.

``set search_path = ''`` (empty) is safe precisely because the body resolves no unqualified names.

Scope note (mirrors 0019): this repo's pytest runs against SQLite in-memory, which never creates the
Postgres function, so this migration is validated in Postgres (advisor re-check), not pytest.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter function public.guard_user_sessions_stale_write() set search_path = '';"
    )


def downgrade() -> None:
    op.execute(
        "alter function public.guard_user_sessions_stale_write() reset search_path;"
    )
