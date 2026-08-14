"""Scope synced training-session identity to its owner.

The device-generated session ``id`` is not globally unique: distinct users can legitimately
produce the same value. Preserve every existing row while changing the primary key from ``id``
to ``(owner_id, id)`` so PostgreSQL upserts, SQLAlchemy identity lookups, and the App's sync
contract share the same identity.

The existing ``idx_user_sessions_owner_updated`` index, owner-only RLS policy, LWW trigger, and
the notes-stripping group-member view all remain unchanged. No policy, grant, view, or trigger is
created, dropped, or altered here. The primary-key index is replaced as part of the constraint
change. Downgrade refuses to collapse two owner-scoped rows with the same ``id`` into the old
global identity, preventing silent data loss.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1
            from pg_constraint
            where conrelid = 'public.user_sessions'::regclass
              and contype = 'p'
              and pg_get_constraintdef(oid) = 'PRIMARY KEY (owner_id, id)'
          ) then
            return;
          end if;

          alter table public.user_sessions
            drop constraint if exists user_sessions_pkey;
          alter table public.user_sessions
            add constraint user_sessions_pkey primary key (owner_id, id);
        end
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (
            select id
            from public.user_sessions
            group by id
            having count(*) > 1
          ) then
            raise exception
              'cannot restore global user_sessions.id primary key while owner-scoped '
              'duplicate ids exist'
              using hint = 'Remove or reconcile duplicate ids before downgrading revision 0030.';
          end if;

          if exists (
            select 1
            from pg_constraint
            where conrelid = 'public.user_sessions'::regclass
              and contype = 'p'
              and pg_get_constraintdef(oid) = 'PRIMARY KEY (id)'
          ) then
            return;
          end if;

          alter table public.user_sessions
            drop constraint if exists user_sessions_pkey;
          alter table public.user_sessions
            add constraint user_sessions_pkey primary key (id);
        end
        $$;
        """
    )
