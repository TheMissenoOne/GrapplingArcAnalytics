"""Professor projects, class focus specs, class objectives — the teaching plan as data.

Phase B of the professor product plan (owner-supplied 2026-09-12), Analytics half of the
FIRST SLICE (§8): a professor creates a project, attaches classes that carry a focus, states
one objective per class, and reads back evidence of what the exposed students actually
attempted. The spec this migration implements — every default validated against the code,
ACCEPTED or CHANGED with the reason — is ``docs/professor_projects/01_SPEC_FATIA_FINA.md``.

**Four tables, one write path each.** ``professor_projects`` / ``professor_project_classes`` /
``class_focus_specs`` / ``class_objectives`` get RLS with a SELECT policy for the group's
owner/professor and **no INSERT/UPDATE/DELETE policy and no such grant** — every write goes
through a SECURITY DEFINER RPC that validates staffhood inside its own body. Same shape
``session_video_jobs`` (0058) and ``class_guests`` (0060) already use, and for the same reason:
a client write policy would be a second door onto rows whose invariants (one project per class,
the ``focus_node_keys`` projection, the objective's target/kind agreement) live in the RPC.

**``class_sessions.focus_node_keys`` (0050) stays, as a projection.** It is read today by the
Web's ``classFocus.ts``/``curriculum.ts``/``ClassLive.tsx`` and by the students' own
``class_sessions_select_member`` policy, so it cannot become staff-only. ``class_focus_spec_upsert``
writes BOTH — the spec row (source of truth, staff-only, carries ``kind`` and
``sequence_steps``) and the column (compatibility projection, group-readable). An RPC, not a
trigger: the RPC is already the only writer, so a trigger would be a second mechanism doing the
same assignment, and a trigger on ``class_focus_specs`` could not cover the reverse direction
anyway (a professor editing ``focus_node_keys`` straight through 0050's
``class_sessions_update_owner_prof`` policy). Precedence, documented in the spec and owed to the
Web side: when a spec row exists it is the truth and the Web must edit focus through the RPC.

**``user_sessions.class_session_id`` becomes write-once.** It is the exposure denominator of
every metric here ("exposed = students attached to the class"), and 0023's
``user_sessions_owner_all`` is a ``FOR ALL`` owner policy — the owner could move the stamp to any
class afterwards, so the denominator was mutable by the very people it measures. Worse, it was
already being erased by accident: ``getUserSessionsSince`` selects ``id,data,updated_at,deleted_at``
and never pulls the column, while ``pushSessionsBatch`` sends ``class_session_id:
s.classSessionId ?? null`` on EVERY push, so any device whose local copy lost the field wipes a
recorded attendance. The guard is folded into the EXISTING ``guard_user_sessions_stale_write()``
(0019) rather than added as a second ``before update`` trigger: two triggers fire in name order,
``trg_user_sessions_class_lock`` sorts BEFORE ``trg_user_sessions_stale_write``, and a stale
racer carrying a different class id would then be rejected instead of silently skipped. One
function, one trigger, deterministic order.

Its three branches, and why each is what it is:

- ``OLD.class_session_id is null`` → anything passes. That is the QR stamp itself.
- ``NEW is null`` while ``OLD`` is not → **ignore the NULL, keep OLD** — unless the referenced
  class row is gone, in which case the NULL is the FK's own ``on delete set null`` action and
  must pass or the delete fails. This is the accidental-erasure fix above; raising here would
  turn a routine push into a permanently wedged sync.
- ``NEW`` is a DIFFERENT non-null value → **keep OLD, do not raise.** This is a deliberate
  departure from the "raise ``class_session_locked``" shape: ``syncEngine.ts`` pushes the whole
  changed set in one ``upsert`` and only advances its cursor after the push resolves
  (``sessionSync.ts:258`` → ``syncEngine.ts:211``/``:302``), so ONE rejected row fails the batch,
  the cursor never moves, and the next pass re-pushes the same offending row forever. A student
  who scans a second class QR onto the same session would wedge every session sync on their
  device, with no client-side recovery until an App release ships. The immutability the metric
  needs is "the first stamp is the one that counts", and keeping OLD delivers exactly that
  without a fail-closed trap. ``ponytail: silent lock — if the product later wants the student
  told, raise 'class_session_locked' here and ship the App handler in the SAME release, never
  before.``

**Privacy class: C (user cloud-synced private data), read side only.**
``professor_project_evidence`` is the one function that touches ``user_sessions``, and it reads
through ``can_read_member_row(us.owner_id, us.class_session_id)`` — the same predicate
``group_member_sessions()`` (0060) reads through — plus a ``group_members`` row in the project's
own group. It returns COUNTS and DATES, never text: no ``reflection``, no round ``notes``, no
``videoContext``, no free-text ``title``/``goal``, no label the student typed. A drop-in guest
(0060) is deliberately NOT in the exposed set: ``can_read_member_row`` would legitimately show
their one consented class session and nothing else, so they would read as "exposed, zero
attempts" — a fabricated gap built out of data the professor is not allowed to have. Requiring a
``group_members`` row keeps the guest out of both numerator and denominator.

**``public.normalize_node_key(text)`` is a FOURTH port of ``normalizeLabel``** (App
``graphSync.ts``, Web ``derive/normalizeLabel.ts``, Analytics ``names.py:_normalize_name``).
It has to exist: the plan forbids the Web from querying ``user_sessions`` on professor paths, so
the label→key match happens in SQL. ``collate "C"`` is load-bearing — under a
``en_US.UTF-8`` collation the range ``[a-z]`` is locale-ordered and would KEEP accented letters
that the JS/Python side strips, so ``Galvão`` must normalise to ``galvo`` (strip, not deaccent)
in all four. Root ``CLAUDE.md``'s "Node key" contract row gains this function.

Scope note (test coverage): this repo's suite runs against SQLite in-memory and never executes a
Postgres migration (0019's scope note). The guard is a source-scan,
``tests/test_professor_projects.py``, same shape as ``tests/test_attach_to_class_guest.py``.

Revision ID: 0062
Revises: 0061
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


# ── normalizeLabel, fourth port ───────────────────────────────────────────────────────────
# JS: lower → trim → strip [^a-z0-9 ] → collapse \s+. The trim moves to the END here because
# `btrim` defaults to spaces only, while JS `.trim()` eats every whitespace char; running it
# last over an already-stripped string gives the identical result for both.
_NORMALIZE_NODE_KEY = """
create or replace function public.normalize_node_key(p_label text)
returns text
language sql
immutable
strict
as $$
  select btrim(
    regexp_replace(
      regexp_replace(lower(p_label) collate "C", '[^a-z0-9 ]', '', 'g'),
      ' +', ' ', 'g'
    )
  );
$$;
"""

# ── access question: am I staff of the group this project belongs to? ─────────────────────
# 0025's rule: a policy never queries an RLS-protected table itself. Same shape as
# is_class_session_staff (0060), one table over.
_IS_PROJECT_STAFF = """
create or replace function public.is_project_staff(p_project_id uuid)
returns boolean
language sql
stable
security definer
set search_path to 'public'
as $$
  select exists (
    select 1
    from public.professor_projects pp
    where pp.id = p_project_id
      and is_group_owner_or_professor(pp.group_id)
  );
$$;
"""

# ── project CRUD ──────────────────────────────────────────────────────────────────────────
# Update is a FULL REPLACE of name/description/starts_at: the Web sends the current values it
# is not changing. Documented in the spec so nobody discovers it by nulling a description.
_PROJECT_UPSERT = """
create or replace function public.professor_project_upsert(
  p_group_id uuid,
  p_name text,
  p_project_id uuid default null,
  p_description text default null,
  p_starts_at timestamptz default null
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_group uuid;
  v_id uuid;
begin
  if p_name is null or btrim(p_name) = '' then
    raise exception 'empty_name' using errcode = 'P0001';
  end if;

  if p_project_id is null then
    if not public.is_group_owner_or_professor(p_group_id) then
      raise exception 'not_group_staff' using errcode = 'P0001';
    end if;

    insert into public.professor_projects (group_id, created_by, name, description, starts_at)
    values (p_group_id, auth.uid(), btrim(p_name), p_description, p_starts_at)
    returning id into v_id;

    return v_id;
  end if;

  select pp.group_id into v_group
  from public.professor_projects pp
  where pp.id = p_project_id;

  if v_group is null then
    raise exception 'project_not_found' using errcode = 'P0001';
  end if;

  if not public.is_group_owner_or_professor(v_group) then
    raise exception 'not_group_staff' using errcode = 'P0001';
  end if;

  update public.professor_projects
  set name = btrim(p_name),
      description = p_description,
      starts_at = p_starts_at,
      updated_at = now()
  where id = p_project_id;

  return p_project_id;
end;
$$;
"""

# `completed_at` is stamped once and cleared on any reopen — the lifecycle is
# active/paused/completed and reopening is explicit (plan, "Produto").
_PROJECT_SET_STATUS = """
create or replace function public.professor_project_set_status(
  p_project_id uuid,
  p_status text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_status not in ('active', 'paused', 'completed') then
    raise exception 'invalid_status' using errcode = 'P0001';
  end if;

  if not exists (select 1 from public.professor_projects where id = p_project_id) then
    raise exception 'project_not_found' using errcode = 'P0001';
  end if;

  if not public.is_project_staff(p_project_id) then
    raise exception 'not_group_staff' using errcode = 'P0001';
  end if;

  update public.professor_projects
  set status = p_status,
      completed_at = case
        when p_status = 'completed' then coalesce(completed_at, now())
        else null
      end,
      updated_at = now()
  where id = p_project_id;
end;
$$;
"""

# One call attaches, re-attaches (moves a class between projects) and detaches
# (`p_project_id => null`) — the unique identity of the link is the CLASS, so there is nothing
# a separate detach function would express that this cannot. A completed project is immutable:
# neither its classes nor a class leaving it may change without reopening it first.
_PROJECT_ATTACH_CLASS = """
create or replace function public.professor_project_attach_class(
  p_class_session_id uuid,
  p_project_id uuid default null,
  p_position integer default 0
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_project_group uuid;
  v_project_status text;
  v_class_group uuid;
  v_current_status text;
begin
  if not public.is_class_session_staff(p_class_session_id) then
    raise exception 'not_class_staff' using errcode = 'P0001';
  end if;

  select pp.status into v_current_status
  from public.professor_project_classes ppc
  join public.professor_projects pp on pp.id = ppc.project_id
  where ppc.class_session_id = p_class_session_id;

  if v_current_status = 'completed' then
    raise exception 'project_not_active' using errcode = 'P0001';
  end if;

  if p_project_id is null then
    delete from public.professor_project_classes
    where class_session_id = p_class_session_id;
    return;
  end if;

  select pp.group_id, pp.status into v_project_group, v_project_status
  from public.professor_projects pp
  where pp.id = p_project_id;

  if v_project_group is null then
    raise exception 'project_not_found' using errcode = 'P0001';
  end if;

  if not public.is_group_owner_or_professor(v_project_group) then
    raise exception 'not_group_staff' using errcode = 'P0001';
  end if;

  if v_project_status <> 'active' then
    raise exception 'project_not_active' using errcode = 'P0001';
  end if;

  -- No FK can express "same group" (it would need group_id denormalised onto the link row),
  -- so the RPC is where that invariant lives. ponytail: denormalise group_id onto
  -- professor_project_classes the day something other than this function writes it.
  select cs.group_id into v_class_group
  from public.class_sessions cs
  where cs.id = p_class_session_id;

  if v_class_group is distinct from v_project_group then
    raise exception 'group_mismatch' using errcode = 'P0001';
  end if;

  insert into public.professor_project_classes (project_id, class_session_id, position)
  values (p_project_id, p_class_session_id, coalesce(p_position, 0))
  on conflict (class_session_id) do update
    set project_id = excluded.project_id,
        position = excluded.position;
end;
$$;
"""

# ── the class's focus, and the compatibility projection ───────────────────────────────────
# `node_keys` is normalised here and is THE column every evidence read uses, whatever the
# kind — that is what makes a new focus kind additive: it must populate node_keys with the
# items whose attempts count, and nothing downstream changes.
_FOCUS_SPEC_UPSERT = """
create or replace function public.class_focus_spec_upsert(
  p_class_session_id uuid,
  p_kind text,
  p_node_keys text[] default '{}',
  p_sequence_steps jsonb default '[]'::jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_keys text[];
begin
  if p_kind not in ('move_set', 'target_state', 'sequence') then
    raise exception 'invalid_kind' using errcode = 'P0001';
  end if;

  if not public.is_class_session_staff(p_class_session_id) then
    raise exception 'not_class_staff' using errcode = 'P0001';
  end if;

  if p_kind = 'sequence' then
    select coalesce(array_agg(distinct k), '{}') into v_keys
    from (
      select public.normalize_node_key(e ->> 'node_key') as k
      from jsonb_array_elements(coalesce(p_sequence_steps, '[]'::jsonb)) e
    ) s
    where k is not null and k <> '';
  else
    select coalesce(array_agg(distinct k), '{}') into v_keys
    from (
      select public.normalize_node_key(x) as k
      from unnest(coalesce(p_node_keys, '{}')) x
    ) s
    where k is not null and k <> '';
  end if;

  if cardinality(v_keys) = 0 then
    raise exception 'empty_focus' using errcode = 'P0001';
  end if;

  insert into public.class_focus_specs
    (class_session_id, kind, node_keys, sequence_steps)
  values
    (p_class_session_id, p_kind, v_keys, coalesce(p_sequence_steps, '[]'::jsonb))
  on conflict (class_session_id) do update
    set kind = excluded.kind,
        node_keys = excluded.node_keys,
        sequence_steps = excluded.sequence_steps,
        updated_at = now();

  -- Compatibility projection (0050): the students' own read and every existing Web surface
  -- keep working off the column. The spec row is the source of truth when it exists.
  update public.class_sessions
  set focus_node_keys = v_keys
  where id = p_class_session_id;
end;
$$;
"""

# Empty `p_node_keys` is legitimate and means "whatever this class's focus is" — the evidence
# function falls back to the focus spec, so an objective cannot drift away from what was taught.
_OBJECTIVE_UPSERT = """
create or replace function public.class_objective_upsert(
  p_class_session_id uuid,
  p_kind text,
  p_objective_id uuid default null,
  p_node_keys text[] default '{}',
  p_sequence_steps jsonb default '[]'::jsonb,
  p_target_count integer default 3,
  p_target_percent double precision default null,
  p_baseline_days integer default 14,
  p_followup_days integer default 14
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_keys text[];
  v_id uuid;
  v_owner uuid;
begin
  if p_kind not in
     ('explore_move', 'reach_state', 'explore_sequence', 'increase_exploration') then
    raise exception 'invalid_kind' using errcode = 'P0001';
  end if;

  if not public.is_class_session_staff(p_class_session_id) then
    raise exception 'not_class_staff' using errcode = 'P0001';
  end if;

  if p_kind = 'increase_exploration' then
    if p_target_percent is null or p_target_percent <= 0 then
      raise exception 'invalid_target' using errcode = 'P0001';
    end if;
  elsif p_target_count is null or p_target_count < 1 then
    raise exception 'invalid_target' using errcode = 'P0001';
  end if;

  if coalesce(p_baseline_days, 0) < 1 or coalesce(p_baseline_days, 0) > 365
     or coalesce(p_followup_days, 0) < 1 or coalesce(p_followup_days, 0) > 365 then
    raise exception 'invalid_window' using errcode = 'P0001';
  end if;

  select coalesce(array_agg(distinct k), '{}') into v_keys
  from (
    select public.normalize_node_key(x) as k
    from unnest(coalesce(p_node_keys, '{}')) x
  ) s
  where k is not null and k <> '';

  if p_objective_id is null then
    insert into public.class_objectives (
      class_session_id, kind, node_keys, sequence_steps,
      target_count, target_percent, baseline_days, followup_days
    )
    values (
      p_class_session_id, p_kind, v_keys, coalesce(p_sequence_steps, '[]'::jsonb),
      p_target_count, p_target_percent, p_baseline_days, p_followup_days
    )
    returning id into v_id;

    return v_id;
  end if;

  select co.class_session_id into v_owner
  from public.class_objectives co
  where co.id = p_objective_id;

  if v_owner is distinct from p_class_session_id then
    raise exception 'objective_not_found' using errcode = 'P0001';
  end if;

  update public.class_objectives
  set kind = p_kind,
      node_keys = v_keys,
      sequence_steps = coalesce(p_sequence_steps, '[]'::jsonb),
      target_count = p_target_count,
      target_percent = p_target_percent,
      baseline_days = p_baseline_days,
      followup_days = p_followup_days,
      updated_at = now()
  where id = p_objective_id;

  return p_objective_id;
end;
$$;
"""

# ── the two reads ─────────────────────────────────────────────────────────────────────────
# Plain SELECT under the policy would answer everything here EXCEPT the aggregates, which is
# the only reason this function exists: one round trip per group instead of one per project.
_PROJECT_LIST = """
create or replace function public.professor_project_list(p_group_id uuid)
returns table (
  id uuid,
  name text,
  description text,
  status text,
  starts_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz,
  updated_at timestamptz,
  class_count integer,
  classes_delivered integer,
  first_class_at timestamptz,
  last_class_at timestamptz
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    pp.id,
    pp.name,
    pp.description,
    pp.status,
    pp.starts_at,
    pp.completed_at,
    pp.created_at,
    pp.updated_at,
    count(ppc.class_session_id)::integer as class_count,
    count(cs.id) filter (where cs.starts_at <= now())::integer as classes_delivered,
    min(cs.starts_at) as first_class_at,
    max(cs.starts_at) as last_class_at
  from public.professor_projects pp
  left join public.professor_project_classes ppc on ppc.project_id = pp.id
  left join public.class_sessions cs on cs.id = ppc.class_session_id
  where pp.group_id = p_group_id
    and is_group_owner_or_professor(p_group_id)
  group by pp.id
  order by pp.created_at desc;
$$;
"""

# The evidence rows, and NOTHING else: counts and dates per (class, student, focus node key).
# No text column of any kind leaves this function — see the module docstring's privacy note and
# `tests/test_professor_projects.py::test_evidence_never_projects_private_session_text`.
#
# Kind-agnostic on purpose: explore_move reads `attempts`, reach_state reads
# `successful_attempts`, increase_exploration reads `baseline_attempts` vs `attempts`, and
# persistence reads `sessions_with_attempt >= 2`. All four are pure derivations in the Web over
# these same rows, which is what "additive later" means here.
#
# ponytail: explore_sequence is NOT answerable from per-node counts (it needs the ORDER of
# entries inside a round). It is the one objective kind this shape cannot serve; when it ships,
# add a `sequence_matches integer` column here (same scan, one more lateral over
# `data->'rounds'->'entries'` partitioned by `sequenceId`) rather than a second RPC.
#
# ponytail: `can_read_member_row` is called per candidate session row; the scan is already
# narrowed to (roster ∩ exposed) × one window per class, which is gym-sized. If a group ever
# makes this slow, hoist the predicate into a CTE the same way 0060's own note describes.
_PROJECT_EVIDENCE = """
create or replace function public.professor_project_evidence(p_project_id uuid)
returns table (
  class_session_id uuid,
  class_starts_at timestamptz,
  objective_id uuid,
  profile_id uuid,
  node_key text,
  window_start timestamptz,
  window_end timestamptz,
  attempts integer,
  successful_attempts integer,
  sessions_with_attempt integer,
  first_attempt_at timestamptz,
  last_attempt_at timestamptz,
  baseline_attempts integer,
  baseline_sessions_with_attempt integer,
  sessions_in_window integer
)
language sql
stable
security definer
set search_path to 'public'
as $$
  with proj as (
    select pp.id, pp.group_id
    from public.professor_projects pp
    where pp.id = p_project_id
      and is_group_owner_or_professor(pp.group_id)
  ),
  cls as (
    select
      cs.id,
      cs.group_id,
      cs.starts_at,
      date_trunc('day', cs.starts_at) as window_start,
      coalesce(obj.baseline_days, 14) as baseline_days,
      coalesce(obj.followup_days, 14) as followup_days,
      obj.id as objective_id,
      coalesce(
        nullif(obj.node_keys, '{}'::text[]), sp.node_keys, cs.focus_node_keys
      ) as node_keys
    from proj
    join public.professor_project_classes ppc on ppc.project_id = proj.id
    join public.class_sessions cs on cs.id = ppc.class_session_id
    left join public.class_focus_specs sp on sp.class_session_id = cs.id
    left join lateral (
      select o.*
      from public.class_objectives o
      where o.class_session_id = cs.id
      order by o.created_at desc, o.id desc
      limit 1
    ) obj on true
  ),
  focus as (
    select
      cls.id as class_session_id,
      cls.starts_at,
      cls.objective_id,
      cls.window_start,
      cls.window_start + make_interval(days => cls.followup_days) as window_end,
      cls.window_start - make_interval(days => cls.baseline_days) as baseline_start,
      k as node_key
    from cls
    cross join lateral unnest(cls.node_keys) as k
  ),
  exposed as (
    select distinct cls.id as class_session_id, us.owner_id as profile_id
    from cls
    join public.user_sessions us on us.class_session_id = cls.id
    join public.group_members gm
      on gm.group_id = cls.group_id and gm.profile_id = us.owner_id
    where us.deleted_at is null
      and can_read_member_row(us.owner_id, us.class_session_id)
  ),
  sess as (
    select e.class_session_id, e.profile_id, us.id as session_id, t.session_at, us.data
    from exposed e
    join cls c on c.id = e.class_session_id
    join public.user_sessions us
      on us.owner_id = e.profile_id
     and us.deleted_at is null
     and us.data is not null
    cross join lateral (
      select coalesce(
        case
          when us.data ->> 'createdAt' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
          then (us.data ->> 'createdAt')::timestamptz
        end,
        us.updated_at
      ) as session_at
    ) t
    where can_read_member_row(us.owner_id, us.class_session_id)
      and t.session_at >= c.window_start - make_interval(days => c.baseline_days)
      and t.session_at <  c.window_start + make_interval(days => c.followup_days)
  ),
  hits as (
    select
      s.class_session_id,
      s.profile_id,
      s.session_id,
      s.session_at,
      coalesce(
        nullif(entry ->> 'nodeKey', ''),
        public.normalize_node_key(entry ->> 'label')
      ) as node_key,
      coalesce((entry ->> 'successful')::boolean, true) as successful
    from sess s
    cross join lateral jsonb_array_elements(
      case when jsonb_typeof(s.data -> 'rounds') = 'array'
           then s.data -> 'rounds' else '[]'::jsonb end
    ) r
    cross join lateral jsonb_array_elements(
      case when jsonb_typeof(r -> 'entries') = 'array'
           then r -> 'entries' else '[]'::jsonb end
    ) entry
  )
  select
    f.class_session_id,
    f.starts_at as class_starts_at,
    f.objective_id,
    e.profile_id,
    f.node_key,
    f.window_start,
    f.window_end,
    count(h.session_id) filter (
      where h.session_at >= f.window_start and h.session_at < f.window_end
    )::integer as attempts,
    count(h.session_id) filter (
      where h.session_at >= f.window_start and h.session_at < f.window_end and h.successful
    )::integer as successful_attempts,
    count(distinct h.session_id) filter (
      where h.session_at >= f.window_start and h.session_at < f.window_end
    )::integer as sessions_with_attempt,
    min(h.session_at) filter (
      where h.session_at >= f.window_start and h.session_at < f.window_end
    ) as first_attempt_at,
    max(h.session_at) filter (
      where h.session_at >= f.window_start and h.session_at < f.window_end
    ) as last_attempt_at,
    count(h.session_id) filter (
      where h.session_at >= f.baseline_start and h.session_at < f.window_start
    )::integer as baseline_attempts,
    count(distinct h.session_id) filter (
      where h.session_at >= f.baseline_start and h.session_at < f.window_start
    )::integer as baseline_sessions_with_attempt,
    (
      select count(distinct s2.session_id)
      from sess s2
      where s2.profile_id = e.profile_id
        and s2.class_session_id = f.class_session_id
        and s2.session_at >= f.window_start
        and s2.session_at < f.window_end
    )::integer as sessions_in_window
  from focus f
  join exposed e on e.class_session_id = f.class_session_id
  left join hits h
    on h.class_session_id = f.class_session_id
   and h.profile_id = e.profile_id
   and h.node_key = f.node_key
  group by
    f.class_session_id, f.starts_at, f.objective_id, e.profile_id, f.node_key,
    f.window_start, f.window_end, f.baseline_start
  order by f.starts_at, e.profile_id, f.node_key;
$$;
"""

# ── 0019's guard, with the class-stamp lock folded in ─────────────────────────────────────
# Everything above the new block is 0019 verbatim; the stale-write skip MUST stay first so a
# losing racer is dropped before the lock ever looks at its class id.
_GUARD_FN_UP = """
create or replace function public.guard_user_sessions_stale_write()
returns trigger language plpgsql as $$
begin
  -- Bug 4: drop a losing racer's stale overwrite (older updated_at than the row on the
  -- server). Return NULL to skip the UPDATE, keeping the newer OLD row. Equal timestamps
  -- pass through (idempotent re-write). A stale tombstone is dropped the same way — the
  -- check is on updated_at alone, so deleted_at needs no ordering of its own.
  if NEW.updated_at < OLD.updated_at then
    return null;
  end if;

  -- 0062: class_session_id is write-once. It is the exposure denominator of every professor
  -- project metric, and the owner-scoped FOR ALL policy (0023) would otherwise let the
  -- measured party move or erase their own attendance.
  if OLD.class_session_id is not null
     and NEW.class_session_id is distinct from OLD.class_session_id then
    if NEW.class_session_id is null
       and not exists (
         select 1 from public.class_sessions cs where cs.id = OLD.class_session_id
       ) then
      -- The class row is gone: this NULL is the FK's own `on delete set null` action, not a
      -- client push. Let it through or the delete fails.
      return NEW;
    end if;
    -- Any other change — a client NULL from a device that never pulled the column, or a
    -- second QR scan onto the same session — keeps the FIRST stamp, silently. See the
    -- module docstring for why this does not raise.
    NEW.class_session_id := OLD.class_session_id;
  end if;

  return NEW;
end;
$$;
"""

# 0019's body, restored verbatim on downgrade.
_GUARD_FN_DOWN = """
create or replace function public.guard_user_sessions_stale_write()
returns trigger language plpgsql as $$
begin
  -- Bug 4: drop a losing racer's stale overwrite (older updated_at than the row on the
  -- server). Return NULL to skip the UPDATE, keeping the newer OLD row. Equal timestamps
  -- pass through (idempotent re-write). A stale tombstone is dropped the same way — the
  -- check is on updated_at alone, so deleted_at needs no ordering of its own.
  if NEW.updated_at < OLD.updated_at then
    return null;
  end if;
  return NEW;
end;
$$;
"""

_NEW_FUNCTIONS = (
    "public.normalize_node_key(text)",
    "public.is_project_staff(uuid)",
    "public.professor_project_upsert(uuid, text, uuid, text, timestamptz)",
    "public.professor_project_set_status(uuid, text)",
    "public.professor_project_attach_class(uuid, uuid, integer)",
    "public.class_focus_spec_upsert(uuid, text, text[], jsonb)",
    "public.class_objective_upsert("
    "uuid, text, uuid, text[], jsonb, integer, double precision, integer, integer)",
    "public.professor_project_list(uuid)",
    "public.professor_project_evidence(uuid)",
)

# (table, select-policy predicate) — SELECT is the only verb any of these tables grants.
_TABLES = (
    ("professor_projects", "public.is_group_owner_or_professor(group_id)"),
    ("professor_project_classes", "public.is_project_staff(project_id)"),
    ("class_focus_specs", "public.is_class_session_staff(class_session_id)"),
    ("class_objectives", "public.is_class_session_staff(class_session_id)"),
)


def upgrade() -> None:
    # ── 1. tables ─────────────────────────────────────────────────────────
    op.create_table(
        "professor_projects",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "group_id",
            UUID(as_uuid=False),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status in ('active','paused','completed')", name="ck_professor_projects_status"
        ),
    )
    op.create_index("idx_professor_projects_group", "professor_projects", ["group_id"])

    # PK is the CLASS, not the pair: "a class belongs to 0 or 1 project" IS the primary key,
    # so no second unique index and no way to write the violating row. A partial index on
    # "0 or 1 ACTIVE project" is not expressible — `status` lives on the other table — and
    # denormalising it here to buy that would need a trigger to keep it true. Moving a class
    # between projects is the same upsert; a completed project refuses both ends of the move.
    op.create_table(
        "professor_project_classes",
        sa.Column(
            "class_session_id",
            UUID(as_uuid=False),
            sa.ForeignKey("class_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=False),
            sa.ForeignKey("professor_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "idx_professor_project_classes_project", "professor_project_classes", ["project_id"]
    )

    op.create_table(
        "class_focus_specs",
        sa.Column(
            "class_session_id",
            UUID(as_uuid=False),
            sa.ForeignKey("class_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "node_keys", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column(
            "sequence_steps", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind in ('move_set','target_state','sequence')", name="ck_class_focus_specs_kind"
        ),
    )

    op.create_table(
        "class_objectives",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "class_session_id",
            UUID(as_uuid=False),
            sa.ForeignKey("class_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "node_keys", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column(
            "sequence_steps", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("target_count", sa.Integer(), nullable=True),
        sa.Column("target_percent", sa.Float(), nullable=True),
        sa.Column("baseline_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("followup_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind in ('explore_move','reach_state','explore_sequence','increase_exploration')",
            name="ck_class_objectives_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'increase_exploration' and target_percent is not null)"
            " or (kind <> 'increase_exploration' and target_count is not null)",
            name="ck_class_objectives_target",
        ),
    )
    op.create_index("idx_class_objectives_class", "class_objectives", ["class_session_id"])

    # ── 2. helpers, then the RPCs that use them ───────────────────────────
    op.execute(_NORMALIZE_NODE_KEY)
    op.execute(_IS_PROJECT_STAFF)
    op.execute(_PROJECT_UPSERT)
    op.execute(_PROJECT_SET_STATUS)
    op.execute(_PROJECT_ATTACH_CLASS)
    op.execute(_FOCUS_SPEC_UPSERT)
    op.execute(_OBJECTIVE_UPSERT)
    op.execute(_PROJECT_LIST)
    op.execute(_PROJECT_EVIDENCE)

    for fn in _NEW_FUNCTIONS:
        # 0028's lesson, repeated by 0032/0045/0050/0054/0059/0060: revoking from PUBLIC alone
        # does not remove Supabase's default anon grant.
        op.execute(f"revoke all on function {fn} from public;")
        op.execute(f"revoke all on function {fn} from anon;")
        op.execute(f"grant execute on function {fn} to authenticated;")

    # ── 3. RLS — read for the group's staff, write only through the RPCs ──
    for table, predicate in _TABLES:
        op.execute(f"alter table public.{table} enable row level security;")
        op.execute(f"revoke all on public.{table} from anon, authenticated;")
        op.execute(f"grant select on public.{table} to authenticated;")

        op.execute(f"drop policy if exists {table}_select_staff on public.{table};")
        op.execute(f"""
        create policy {table}_select_staff on public.{table} for select to authenticated
        using ({predicate});
        """)

    # ── 4. the exposure denominator stops being mutable ───────────────────
    op.execute(_GUARD_FN_UP)


def downgrade() -> None:
    op.execute(_GUARD_FN_DOWN)

    for table, _predicate in _TABLES:
        op.execute(f"drop policy if exists {table}_select_staff on public.{table};")

    for fn in _NEW_FUNCTIONS:
        op.execute(f"drop function if exists {fn};")

    op.drop_index("idx_class_objectives_class", table_name="class_objectives")
    op.drop_table("class_objectives")
    op.drop_table("class_focus_specs")
    op.drop_index(
        "idx_professor_project_classes_project", table_name="professor_project_classes"
    )
    op.drop_table("professor_project_classes")
    op.drop_index("idx_professor_projects_group", table_name="professor_projects")
    op.drop_table("professor_projects")

    # NOT reverted: whatever `class_focus_spec_upsert` projected onto
    # `class_sessions.focus_node_keys`. That column is 0050's and predates this revision; the
    # values in it are the professor's own focus, still correct, still read by the Web. A
    # downgrade that blanked them would destroy data this revision only ever copied.
