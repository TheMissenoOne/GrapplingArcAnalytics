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

``PUBLIC`` is included in the revoke as well as ``anon`` and ``authenticated``. A privilege held
by ``PUBLIC`` is held by every role, so hardening the two named ones while leaving a ``PUBLIC``
grant in place would be a check that reports success and protects nothing. Production has no
such grant today (verified) — this is so it cannot arrive later unnoticed. The table owner keeps
everything regardless of ``PUBLIC``, so nothing operational depends on it.

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
_REVOKE_FROM = "anon, authenticated, public"


def upgrade() -> None:
    op.execute(f"revoke {_UNGATABLE} on all tables in schema public from {_REVOKE_FROM};")
    op.execute(
        f"alter default privileges in schema public revoke {_UNGATABLE} on tables from {_ROLES};"
    )


def downgrade() -> None:
    # Only the DEFAULT is restored, deliberately.
    #
    # A schema-wide `grant` here would not reverse this revision — it would overshoot it. 0030
    # and 0031 had already revoked these privileges from `user_projects` and `user_node_names`
    # before 0039 existed, so granting to "all tables" would hand them back privileges they did
    # not have at the moment this revision ran, and a downgrade that opens something the upgrade
    # never closed is worse than one that leaves the hardening in place.
    #
    # Restoring the exact prior state would mean recording every table's grants in the upgrade
    # and replaying them, which is a lot of machinery to un-do a change with no operational
    # effect. The tables stay hardened; new ones go back to inheriting the platform default.
    op.execute(
        f"alter default privileges in schema public grant {_UNGATABLE} on tables to {_ROLES};"
    )
