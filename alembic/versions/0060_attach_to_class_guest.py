"""Drop-in: a NON-member attends one class by QR, and the professor sees only that class.

The owner's binding decision of 2026-09-03 (item 5): *"Não-membro em aula via QR ⇒ confirmação
explícita antes de entrar; professor vê só aquela aula/sessão + análise dela, nada do histórico."*

0054 measured the gap and deliberately left it open ("Deliberately NOT extended to the
'non-member scans a class QR' case … Building an actual drop-in … is a new ``attach_to_class``
shape, App-side QR UX, and its own consent copy — out of scope for this revision"). This is that
revision.

**Why a new RPC instead of loosening ``attach_to_class`` (0026).** ``attach_to_class`` refuses
anyone who is not ``is_group_member`` and returns ONE error for "bad token" and "not a member"
alike, precisely so it can never be used to probe which gyms exist. Relaxing that check would
delete the property, silently, for the member path too. ``attach_to_class`` is therefore not
touched by this revision at all — a member's flow is byte-identical to yesterday, and the guest
flow is a second, explicitly-consented door. The two RPCs are mutually exclusive by construction:
``attach_to_class`` requires membership, ``attach_to_class_guest`` refuses it (``already_member``)
so the App falls back to the member call rather than writing a bogus guest row.

**Consent is the row.** ``class_guests`` exists to record that a specific person confirmed, at a
specific moment, that this specific class's staff may see this one session. ``consent_at`` is NOT
NULL: unlike ``group_members.consent_at`` (0054), where NULL legitimately means "predates the
column", there is no such thing as a guest row that predates consent — the row is created by the
RPC, in the same statement as the consent. No declined state: a decline never calls the RPC.

**Error codes are distinguishable here, and that is not a regression of 0026's rule.** 0026
conflates its two failures to hide *group membership* from a scanner. That question does not
exist on this path — the caller is by definition not a member, and they already hold the token.
What the App needs to say ("essa aula já encerrou" vs "código inválido") is worth more than
hiding the liveness of a 128-bit random token from whoever already possesses it.

**The read scope — one predicate, not one edit per RPC.** Every professor-facing projection
built since 0045 (``group_member_names``, ``group_member_rating``, ``group_member_graph_edges``,
``group_member_video_analysis``, ``group_member_athlete``) starts ``from public.group_members
gm`` and requires a row for the target profile. A guest has no such row, so all five already
return zero rows for a guest — no history, no graph, no rating, no roster entry — with no change
here. **That is the whole "nada do histórico" half of the decision, and it is enforced by their
existing shape, not by anything this migration adds.** They are deliberately NOT touched.

The one exception is ``group_member_sessions()`` (0032, narrowed by 0044), which is not
group_members-shaped: it scans ``user_sessions`` under ``shares_group_as_professor(us.owner_id)``.
That is the single function that must learn about guests, and it learns through
``can_read_member_row(p_profile, p_class_session)`` — first disjunct byte-identical to the
predicate it replaces, so the member path cannot drift; second disjunct true only for a row whose
``class_session_id`` is exactly the class the guest consented to, read by that class's own staff.
A guest's other sessions have a different (or NULL) ``class_session_id`` and stay invisible.

(The suggested ``can_read_member_row(p_group, p_profile, p_class_session)`` shape could not be
used as-is: ``group_member_sessions()`` takes no arguments and returns rows across every group
the caller teaches in, so there is no ``p_group`` to pass. The group is derived from the class
session instead, which is stricter — it is the class's OWN group, not one the caller names.)

``is_class_session_staff(p_class_session)`` is the one new access question ("am I owner/professor
of the group this class belongs to"), built the 0025/0026 way — SECURITY DEFINER, ``stable``,
``search_path`` pinned — and used in all three places that ask it: the RLS policy on
``class_guests``, ``can_read_member_row``, and ``class_session_guests``. No policy in this
revision queries ``group_members`` (or any RLS-protected table) directly; that is the mistake
0025 exists to have fixed.

**Not built, on purpose:**

- *Round-video analysis for a guest.* ``group_member_video_analysis`` (0059) gates on
  ``gm.share_video_analysis``, a per-membership opt-in the student flips themselves. A guest has
  no membership row and therefore no way to opt in, so a guest's video analysis stays private —
  the same answer 0059's ``default false`` gives a member who never flipped it. Adding
  ``class_guests.share_video_analysis`` plus a setter would be a new consent surface with no
  screen to set it from; add it the day the App grows that toggle.
- *A guest in the roster.* ``group_member_names`` is the roster and a guest is not a member;
  ``class_session_guests(p_class_session_id)`` exposes them for the class-live view only, and
  projects ``full_name``/``consent_at`` only — no belt (0057), no rating, no athlete link.
- *An index on ``class_guests.profile_id``.* ``ponytail: the PK (class_session_id, profile_id)
  serves every read this revision adds; the only profile_id-keyed scan is the owner's own-row RLS
  check on a table that holds one row per drop-in. Add the index when a gym has enough drop-ins
  for a seq scan to show up.``

Privacy class: **C, user cloud-synced private data** — same class 0044/0045/0054 assigned. This
revision WIDENS a read (a professor gains sight of a non-member's session), which is exactly why
the widening is bounded by a row the subject created themselves, scoped to one class session, and
reversible by nothing less than the ``class_guests`` row's existence.

Scope note (test coverage): this repo's suite runs against SQLite in-memory and never executes a
Postgres migration (0019's scope note). The guard is a source-scan —
``tests/test_attach_to_class_guest.py``, same shape as ``tests/test_session_video_analysis.py``.

Revision ID: 0060
Revises: 0059
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


# ── access question: am I staff of the group this class belongs to? ───────────────────────
# SECURITY DEFINER so it reads class_sessions/groups with RLS bypassed and terminates —
# 0025's rule: a policy never queries a protected table itself.
_IS_CLASS_SESSION_STAFF = """
create or replace function public.is_class_session_staff(p_class_session uuid)
returns boolean
language sql
stable
security definer
set search_path to 'public'
as $$
  select exists (
    select 1
    from public.class_sessions cs
    where cs.id = p_class_session
      and is_group_owner_or_professor(cs.group_id)
  );
$$;
"""

# ── the one predicate group_member_sessions() reads through ───────────────────────────────
# First disjunct: byte-identical to 0044's predicate, applied to the same column. Second:
# a guest row, and ONLY for the class session it names.
#
# ponytail: the guest disjunct is evaluated per session row that fails the member check
# (SQL `or` has no guaranteed short-circuit), i.e. a PK probe into a table holding one row
# per drop-in. If class_guests ever grows past that, hoist the guest rows into a CTE in
# group_member_sessions() instead of a per-row function call.
_CAN_READ_MEMBER_ROW = """
create or replace function public.can_read_member_row(p_profile uuid, p_class_session uuid)
returns boolean
language sql
stable
security definer
set search_path to 'public'
as $$
  select shares_group_as_professor(p_profile)
      or (
        is_class_session_staff(p_class_session)
        and exists (
          select 1
          from public.class_guests cg
          where cg.class_session_id = p_class_session
            and cg.profile_id = p_profile
        )
      );
$$;
"""

# ── the drop-in itself ────────────────────────────────────────────────────────────────────
# Same return shape as attach_to_class (0026) so the App's handler is the same code path:
# it validates the token, records consent, and hands back the class — it does NOT write
# user_sessions. Stamping `class_session_id` stays the client's own write under the
# owner-scoped `user_sessions_owner_all` policy (0023), exactly as the member flow does.
_ATTACH_TO_CLASS_GUEST = """
create or replace function public.attach_to_class_guest(p_token text)
returns table (class_id uuid, class_title text)
language plpgsql
security definer
set search_path = public
as $$
declare
  target_class public.class_sessions%rowtype;
begin
  select cs.* into target_class
  from public.class_sessions cs
  where cs.join_token = p_token;

  if target_class.id is null then
    raise exception 'bad_token' using errcode = 'P0001';
  end if;

  if target_class.token_expires_at is not null and target_class.token_expires_at <= now() then
    raise exception 'class_closed' using errcode = 'P0001';
  end if;

  -- Members keep attach_to_class: it is the consented path they already agreed to when they
  -- joined the gym, and a guest row for a member would put them in class_session_guests as
  -- if they were a visitor.
  if public.is_group_member(target_class.group_id) then
    raise exception 'already_member' using errcode = 'P0001';
  end if;

  -- Re-scanning the same class is idempotent and keeps the FIRST consent timestamp: the
  -- consent that matters is the one given before entering.
  insert into public.class_guests (class_session_id, profile_id, consent_at)
  values (target_class.id, auth.uid(), now())
  on conflict (class_session_id, profile_id) do nothing;

  return query
    select target_class.id, target_class.title;
end;
$$;
"""

# ── who dropped in to this class — for the class-live view only ───────────────────────────
_CLASS_SESSION_GUESTS = """
create or replace function public.class_session_guests(p_class_session_id uuid)
returns table (
  profile_id uuid,
  full_name text,
  consent_at timestamptz
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select cg.profile_id, p.full_name, cg.consent_at
  from public.class_guests cg
  join public.profiles p on p.id = cg.profile_id
  where cg.class_session_id = p_class_session_id
    and is_class_session_staff(p_class_session_id);
$$;
"""

# ── group_member_sessions(): 0044's body with the predicate routed through the helper ─────
# Everything else — the projection, the `reflection`/`notes` stripping, the `deleted_at`
# filter from 0044 — is byte-identical. `create or replace` keeps the existing grants.
_SESSIONS_FN_UP = """
create or replace function public.group_member_sessions()
returns table (
  id text,
  owner_id uuid,
  class_session_id uuid,
  updated_at timestamptz,
  data jsonb
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    us.id,
    us.owner_id,
    us.class_session_id,
    us.updated_at,
    jsonb_set(
      us.data - 'reflection',
      '{rounds}',
      coalesce(
        (select jsonb_agg(r.value - 'notes')
           from jsonb_array_elements(us.data -> 'rounds') r),
        '[]'::jsonb
      )
    ) as data
  from public.user_sessions us
  where can_read_member_row(us.owner_id, us.class_session_id)
    and us.deleted_at is null;
$$;
"""

# 0044's body, restored verbatim on downgrade.
_SESSIONS_FN_DOWN = """
create or replace function public.group_member_sessions()
returns table (
  id text,
  owner_id uuid,
  class_session_id uuid,
  updated_at timestamptz,
  data jsonb
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    us.id,
    us.owner_id,
    us.class_session_id,
    us.updated_at,
    jsonb_set(
      us.data - 'reflection',
      '{rounds}',
      coalesce(
        (select jsonb_agg(r.value - 'notes')
           from jsonb_array_elements(us.data -> 'rounds') r),
        '[]'::jsonb
      )
    ) as data
  from public.user_sessions us
  where shares_group_as_professor(us.owner_id)
    and us.deleted_at is null;
$$;
"""

_NEW_FUNCTIONS = (
    "public.is_class_session_staff(uuid)",
    "public.can_read_member_row(uuid, uuid)",
    "public.attach_to_class_guest(text)",
    "public.class_session_guests(uuid)",
)


def upgrade() -> None:
    # ── 1. the consent row ────────────────────────────────────────────────
    op.create_table(
        "class_guests",
        sa.Column(
            "class_session_id",
            UUID(as_uuid=False),
            sa.ForeignKey("class_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "profile_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # ── 2. helpers, then the RPCs that use them ───────────────────────────
    op.execute(_IS_CLASS_SESSION_STAFF)
    op.execute(_CAN_READ_MEMBER_ROW)
    op.execute(_ATTACH_TO_CLASS_GUEST)
    op.execute(_CLASS_SESSION_GUESTS)

    for fn in _NEW_FUNCTIONS:
        # Belt-and-braces per 0028/0032/0045/0054/0059: revoking from PUBLIC alone does not
        # remove Supabase's default anon grant.
        op.execute(f"revoke all on function {fn} from public;")
        op.execute(f"revoke all on function {fn} from anon;")
        op.execute(f"grant execute on function {fn} to authenticated;")

    # ── 3. RLS on class_guests — read only, and only these two readers ────
    # No insert/update/delete policy and no insert/update/delete grant: the ONLY writer is
    # attach_to_class_guest (SECURITY DEFINER, runs as the function owner). Same shape 0058
    # used for session_video_jobs.
    op.execute("alter table public.class_guests enable row level security;")
    op.execute("revoke all on public.class_guests from anon, authenticated;")
    op.execute("grant select on public.class_guests to authenticated;")

    op.execute("drop policy if exists class_guests_select_self_or_staff on public.class_guests;")
    op.execute(
        """
        create policy class_guests_select_self_or_staff on public.class_guests for select
        to authenticated
        using (profile_id = auth.uid() or public.is_class_session_staff(class_session_id));
        """
    )

    # ── 4. the one professor read that must learn about guests ────────────
    op.execute(_SESSIONS_FN_UP)


def downgrade() -> None:
    op.execute(_SESSIONS_FN_DOWN)

    op.execute("drop policy if exists class_guests_select_self_or_staff on public.class_guests;")

    # can_read_member_row is dropped BEFORE is_class_session_staff only for readability; the
    # sessions function above no longer references either by the time these run.
    op.execute("drop function if exists public.class_session_guests(uuid);")
    op.execute("drop function if exists public.attach_to_class_guest(text);")
    op.execute("drop function if exists public.can_read_member_row(uuid, uuid);")
    op.execute("drop function if exists public.is_class_session_staff(uuid);")

    # Drops every consent row with it — which is the correct direction: without the table
    # there is no scope to honour, so leaving the rows would leave a claim nothing enforces.
    op.drop_table("class_guests")
