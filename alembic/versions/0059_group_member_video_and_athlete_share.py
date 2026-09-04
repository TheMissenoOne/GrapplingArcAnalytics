"""Two more student opt-ins, both consent-gated, both self-only writes — Sprint 5 of the Web plan.

Same shape as ``trains_here`` (0055): a plain boolean on ``group_members``, defaulted false, and
a SECURITY DEFINER RPC that writes only the caller's own row (``profile_id = auth.uid()``), so
neither needs a role check — anyone in the gym may say whether THEY personally opt in. Both are
read through a SECURITY DEFINER projection RPC in the ``group_member_rating``/``group_member_
graph_edges`` mold (0054, tightened by 0057's ``gm.group_id = p_group_id`` scoping): the caller
must be a professor sharing THIS group with the target student (``shares_group_as_professor(
p_profile_id)`` plus ``gm.group_id = p_group_id``), and the row's own share flag must be true.

**1. Video analysis (`share_video_analysis` / `group_member_video_analysis`).** The student's own
`session_video_analysis` rows (0058) — private, owner-only SELECT, no client write path at all —
projected to the group's staff ONLY once the student flips this flag. Projects exactly
`session_id, round_id, generated_at, events, sequences, difficulty_derived, confidence,
highlights`. Deliberately NEVER `pdf_path`/`clip_paths`/`motion`: `user-media` stays owner-only by
the decision already recorded in 0042/0058 (D9) — opening it to the professor here would reverse
that decision through a side door. `events`/`sequences`/`highlights` carry `ts`, so this is also
what unlocks the Web's timeline sync (`site/timeline.js`, vendorized, per the UI plan's §5.1).

**2. Athlete link (`share_athlete_link` / `group_member_athlete`).** `profiles.athlete_id` (0051)
plus the linked athlete's public `name`, once the student opts in. `athletes` has no `slug`
column — the site derives it at export time via `export/match_breakdown.py:slugify(name)`, which
is prose-processing logic (lowercasing, non-alnum collapse), not a stored value or something a
SQL projection should re-derive. ``ponytail: project ``name`` only; the Web can either link by
name (search) or port `slugify` client-side if it wants a direct `grapple-<slug>.html` href —
adding a generated/stored `slug` column is a separate decision if that friction turns out to
matter.`` No `nickname`/`team`/`weight_class` — the roster only needs "which public athlete is
this student", not a scouting card; widen the projection in its own revision if that's wanted.

Both RPCs read `p.athlete_id`/`sva.*` through a *join*, not a stored copy — an athlete link
change (admin re-link) or a fresh video analysis both show up immediately with no extra work.

Revision ID: 0059
Revises: 0058
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


_SET_SHARE_VIDEO_ANALYSIS = """
create or replace function public.set_share_video_analysis(p_group_id uuid, p_share boolean)
returns void
language sql
security definer
set search_path = public
as $$
  update public.group_members
  set share_video_analysis = p_share
  where group_id = p_group_id and profile_id = auth.uid();
$$;
"""

_SET_SHARE_ATHLETE_LINK = """
create or replace function public.set_share_athlete_link(p_group_id uuid, p_share boolean)
returns void
language sql
security definer
set search_path = public
as $$
  update public.group_members
  set share_athlete_link = p_share
  where group_id = p_group_id and profile_id = auth.uid();
$$;
"""

_GROUP_MEMBER_VIDEO_ANALYSIS = """
create or replace function public.group_member_video_analysis(p_group_id uuid, p_profile_id uuid)
returns table (
  session_id text,
  round_id text,
  generated_at timestamptz,
  events jsonb,
  sequences jsonb,
  difficulty_derived numeric(4,1),
  confidence text,
  highlights jsonb
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    sva.session_id, sva.round_id, sva.generated_at,
    sva.events, sva.sequences, sva.difficulty_derived, sva.confidence, sva.highlights
  from public.group_members gm
  join public.session_video_analysis sva on sva.owner_id = p_profile_id
  where gm.group_id = p_group_id
    and gm.profile_id = p_profile_id
    and gm.share_video_analysis
    and shares_group_as_professor(p_profile_id);
$$;
"""

_GROUP_MEMBER_ATHLETE = """
create or replace function public.group_member_athlete(p_group_id uuid, p_profile_id uuid)
returns table (
  athlete_id uuid,
  name text
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select a.id as athlete_id, a.name
  from public.group_members gm
  join public.profiles p on p.id = gm.profile_id
  join public.athletes a on a.id = p.athlete_id
  where gm.group_id = p_group_id
    and gm.profile_id = p_profile_id
    and gm.share_athlete_link
    and shares_group_as_professor(p_profile_id);
$$;
"""

_SELF_SETTERS = (
    "public.set_share_video_analysis(uuid, boolean)",
    "public.set_share_athlete_link(uuid, boolean)",
)

_PROJECTIONS = (
    "public.group_member_video_analysis(uuid, uuid)",
    "public.group_member_athlete(uuid, uuid)",
)


def upgrade() -> None:
    op.add_column(
        "group_members",
        sa.Column("share_video_analysis", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "group_members",
        sa.Column("share_athlete_link", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.execute(_SET_SHARE_VIDEO_ANALYSIS)
    op.execute(_SET_SHARE_ATHLETE_LINK)
    op.execute(_GROUP_MEMBER_VIDEO_ANALYSIS)
    op.execute(_GROUP_MEMBER_ATHLETE)

    for fn in (*_SELF_SETTERS, *_PROJECTIONS):
        # Belt-and-braces per 0028/0032/0045/0054/0055: revoking from PUBLIC alone does not
        # remove Supabase's default anon grant.
        op.execute(f"revoke all on function {fn} from public;")
        op.execute(f"revoke all on function {fn} from anon;")
        op.execute(f"grant execute on function {fn} to authenticated;")


def downgrade() -> None:
    for fn in (*_PROJECTIONS, *_SELF_SETTERS):
        op.execute(f"drop function if exists {fn};")

    op.drop_column("group_members", "share_athlete_link")
    op.drop_column("group_members", "share_video_analysis")
