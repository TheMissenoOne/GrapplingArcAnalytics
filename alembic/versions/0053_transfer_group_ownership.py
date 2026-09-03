"""Ownership transfer: the only way `groups.owner_id` moves after `create_group()`.

**The gap.** `set_member_role` (0050) deliberately never sets `'owner'` — its own docstring
scopes that out as "a different, harder decision" and defers it to its own migration. Until
now there was no such migration: an academy owner (retiring, selling the gym, handing off to
a head professor) had no path to leave without either staying `owner_id` forever or an admin
hand-editing `groups`/`group_members` via `scripts/group_admin.py` outside RLS.

**`transfer_group_ownership(p_group_id, p_new_owner)`** — SECURITY DEFINER, one transaction:

1. `not_owner` — caller must BE the group's current owner (`groups.owner_id = auth.uid()`),
   same gate `set_member_role` already uses. No delegated transfers.
2. `same_owner` — `p_new_owner = auth.uid()` is a no-op request, rejected rather than silently
   succeeding (avoids a redundant `group_members` write and a confusing "transferred to
   myself" log line).
3. `not_a_member` — `p_new_owner` must already be IN the group (`group_members`). Ownership
   moves to someone already trusted with a role in this academy, never to an arbitrary
   profile id; no separate invite-and-transfer-in-one-step.

Then: `groups.owner_id = p_new_owner`; the new owner's `group_members.role` → `'owner'`; the
outgoing owner's row → `'professor'` (demoted, not removed — same person who ran the academy
almost certainly still teaches there; if not, a follow-up `set_member_role`/removal is a
separate decision this function doesn't make for them). Every downstream policy gated on
`groups.owner_id` (`group_invites_owner_all`, `group_members_delete_self_or_owner`, class
planning/instructional writes, `set_member_role` itself) re-reads `owner_id` live, so the new
owner inherits full control the instant this commits — no RLS policy needed here, same reason
`create_group`/`set_member_role` needed none.

`grant execute to authenticated` only (never `anon`), same pattern as every SECURITY DEFINER
function since 0024.

**Closing 0052's named gap, in the same migration that adds ownership transfer.** 0052's
docstring flagged that `set_member_role` let an owner demote their OWN membership row
(`p_profile_id = auth.uid()`) via the student/professor path — accidentally stepping down
without ever making anyone else `'owner'`, leaving the group ownerless in practice (a `groups`
row with an `owner_id` who is no longer `role='owner'` in `group_members`). That gap is a
one-line guard, added here because ownership transfer is precisely the feature that makes it
safe to close: an owner who genuinely wants to step back now has a real path
(`transfer_group_ownership`) instead of the accidental one this guard removes.

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_TRANSFER_FN = "public.transfer_group_ownership(uuid, uuid)"


def upgrade() -> None:
    # ── transfer_group_ownership — owner only, target must already be a member ──
    op.execute("""
    create or replace function public.transfer_group_ownership(
      p_group_id uuid, p_new_owner uuid
    )
    returns void
    language plpgsql
    security definer
    set search_path = public
    as $$
    begin
      if not exists (
        select 1 from public.groups g
        where g.id = p_group_id and g.owner_id = auth.uid()
      ) then
        raise exception 'not_owner' using errcode = 'P0001';
      end if;

      if p_new_owner = auth.uid() then
        raise exception 'same_owner' using errcode = 'P0001';
      end if;

      if not exists (
        select 1 from public.group_members gm
        where gm.group_id = p_group_id and gm.profile_id = p_new_owner
      ) then
        raise exception 'not_a_member' using errcode = 'P0001';
      end if;

      update public.groups set owner_id = p_new_owner where id = p_group_id;

      update public.group_members set role = 'owner'
      where group_id = p_group_id and profile_id = p_new_owner;

      update public.group_members set role = 'professor'
      where group_id = p_group_id and profile_id = auth.uid();
    end;
    $$;
    """)

    op.execute(f"revoke all on function {_TRANSFER_FN} from public;")
    op.execute(f"revoke all on function {_TRANSFER_FN} from anon;")
    op.execute(f"grant execute on function {_TRANSFER_FN} to authenticated;")

    # ── set_member_role — close 0052's named gap: owner can't touch their own row here.
    # Signature unchanged from 0050, so CREATE OR REPLACE keeps its existing grants
    # (execute to authenticated, revoked from public/anon) — no re-grant needed.
    op.execute("""
    create or replace function public.set_member_role(
      p_group_id uuid, p_profile_id uuid, p_role text
    )
    returns void
    language plpgsql
    security definer
    set search_path = public
    as $$
    begin
      if p_role not in ('professor', 'student') then
        raise exception 'invalid_role' using errcode = 'P0001';
      end if;

      if not exists (
        select 1 from public.groups g
        where g.id = p_group_id and g.owner_id = auth.uid()
      ) then
        raise exception 'not_group_owner' using errcode = 'P0001';
      end if;

      if p_profile_id = auth.uid() then
        raise exception 'cannot_change_own_role' using errcode = 'P0001';
      end if;

      update public.group_members
      set role = p_role
      where group_id = p_group_id and profile_id = p_profile_id;
    end;
    $$;
    """)


def downgrade() -> None:
    op.execute(f"drop function if exists {_TRANSFER_FN};")

    # Revert set_member_role to 0050's body — no self-role guard.
    op.execute("""
    create or replace function public.set_member_role(
      p_group_id uuid, p_profile_id uuid, p_role text
    )
    returns void
    language plpgsql
    security definer
    set search_path = public
    as $$
    begin
      if p_role not in ('professor', 'student') then
        raise exception 'invalid_role' using errcode = 'P0001';
      end if;

      if not exists (
        select 1 from public.groups g
        where g.id = p_group_id and g.owner_id = auth.uid()
      ) then
        raise exception 'not_group_owner' using errcode = 'P0001';
      end if;

      update public.group_members
      set role = p_role
      where group_id = p_group_id and profile_id = p_profile_id;
    end;
    $$;
    """)
