"""Take back the three privileges RLS cannot gate.

A Supabase project ships with ``ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO anon,
authenticated``. So every table a migration creates is granted all seven privileges to both
roles the moment it exists, and RLS is what stands between them and the data.

For SELECT, INSERT, UPDATE and DELETE that is the intended arrangement and it works — a table
with RLS on and no matching policy denies the row. But three of the seven are not row-level
concepts at all, so no policy can touch them:

  - **TRUNCATE** empties a table outright. RLS is not consulted.
  - **REFERENCES** lets a role point a foreign key at the table, which can be used to probe for
    the existence of values it cannot read.
  - **TRIGGER** lets a role attach a trigger, i.e. run code on someone else's writes.

0030 and 0031 already knew this — both revoke everything before granting the four verbs back,
and 0031's comment names TRUNCATE specifically. What neither could do is fix the tables created
before them. Measured against production: **33 tables** still carry all three.

**Not an open door today.** PostgREST exposes GET/POST/PATCH/DELETE and nothing else — there is
no request that reaches TRUNCATE, and the anon key does not grant a Postgres connection. This is
a privilege nobody should hold rather than a hole anybody can walk through, and the honest fix
is to stop holding it, not to rely on the API surface staying narrow forever.

**Deliberately surgical.** Only the three ungatable privileges are revoked, schema-wide.
SELECT/INSERT/UPDATE/DELETE are left exactly as they are on every table, because those are the
ones RLS governs and re-deriving the correct verb set per table is how a working app gets broken
by a hardening change. Nothing about what any client can read or write changes here.

The default is fixed too, so a table added next month does not quietly reintroduce all three.
That only covers objects created by the role running this migration, which is the role the
migrations run as — the platform's own defaults for other roles are outside a migration's reach,
which is why the revoke above is written to be re-runnable rather than assumed unnecessary.

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

_UNGATABLE = "truncate, references, trigger"
_ROLES = "anon, authenticated"


def upgrade() -> None:
    op.execute(f"revoke {_UNGATABLE} on all tables in schema public from {_ROLES};")
    op.execute(
        f"alter default privileges in schema public revoke {_UNGATABLE} on tables from {_ROLES};"
    )


def downgrade() -> None:
    # Restoring a privilege that nothing needs would be an odd thing to want, but a downgrade
    # that does not reverse its upgrade is worse than one that does.
    op.execute(
        f"alter default privileges in schema public grant {_UNGATABLE} on tables to {_ROLES};"
    )
    op.execute(f"grant {_UNGATABLE} on all tables in schema public to {_ROLES};")
