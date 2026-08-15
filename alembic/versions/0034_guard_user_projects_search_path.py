"""Pin the projects stale-write guard's search_path, the way 0020 did for sessions.

0030 copied ``guard_user_sessions_stale_write`` to make the projects equivalent and copied it
from its ORIGINAL 0019 form — before 0020 pinned its ``search_path``. So the advisor picked up
a fresh ``0011_function_search_path_mutable`` WARN on ``guard_user_projects_stale_write`` the
moment 0030 landed: a defect this repo had already found and fixed once, reintroduced by
copying the wrong version of the thing.

A mutable ``search_path`` on a function is a hijack surface — a caller who can set their own
``search_path`` chooses which schema's objects the body resolves against. This body only
compares two ``NEW``/``OLD`` fields and resolves nothing, so there is nothing here to hijack
today; the pin matters because the next edit to that body might reference a table, and by then
nobody will remember it was unpinned.

``''`` rather than ``'public'``, matching 0020: an empty search_path forces every reference in
the body to be schema-qualified, which is the stricter form and the one already in use here.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter function public.guard_user_projects_stale_write() set search_path = '';"
    )


def downgrade() -> None:
    op.execute(
        "alter function public.guard_user_projects_stale_write() reset search_path;"
    )
