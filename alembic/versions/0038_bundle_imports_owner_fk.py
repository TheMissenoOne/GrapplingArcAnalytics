"""The audit row that outlived the person it was about, and the column nobody ever wrote.

Auditing what survives an account deletion turned up three `public` tables with a uuid owner
column and no foreign key:

  - ``graphs.owner_id`` — polymorphic (athlete id or profile id, per ``owner_kind``), so a column
    FK is impossible. Already handled: 0023's ``handle_user_delete`` trigger on
    ``before delete on auth.users`` deletes the user's graph, and it is live in production.
  - ``matches.created_by`` — curation provenance, not user data. 893 rows, every one NULL.
  - ``bundle_imports.owner_id`` — genuinely unhandled.

**What ``bundle_imports`` actually is.** An audit trail of admin-side ingestion. One row per
``python -m db.ingest <bundle.json>``, written by ``upsert_graph_from_bundle``
(``db/repository.py``, the single writer, under the comment "Audit log"). It records that a
bundle was ingested for an owner and when. Zero rows in production because nobody has run that
CLI against it — it is an offline operator path, not something the app calls.

So the row is small: an owner id and a timestamp. It is still a record ABOUT an identified
person, it has no operational value once that person and their graph are gone, and it was the
one owner column a deletion left behind. Hence the FK, cascading — an audit row that outlives
its subject is a retention decision, and nobody has made one.

**``raw`` is dropped.** The column has existed since 0001 and has never been written by
anything: the only writer passes ``owner_id`` alone. Nothing reads it either. It is a dead
column — and not an innocent one, because what it was shaped to hold is a whole user bundle,
which would make it the single most sensitive column in this schema the moment anyone started
filling it, with no policy and no thought given to it. A dead column that dangerous is worth
removing rather than documenting. Restoring it is one line if the audit trail ever needs a
payload; deciding then is better than inheriting the shape now.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

_FK = "bundle_imports_owner_id_fkey"


def upgrade() -> None:
    # Nullable column, so CASCADE removes the row rather than orphaning it. SET NULL would keep
    # an audit entry that no longer records who it was about, which is not an audit entry.
    op.execute(f"alter table public.bundle_imports drop constraint if exists {_FK};")
    op.execute(
        f"""
        alter table public.bundle_imports
          add constraint {_FK}
          foreign key (owner_id) references public.profiles(id) on delete cascade;
        """
    )
    op.drop_column("bundle_imports", "raw")


def downgrade() -> None:
    op.add_column("bundle_imports", sa.Column("raw", postgresql.JSONB, nullable=True))
    op.execute(f"alter table public.bundle_imports drop constraint if exists {_FK};")
