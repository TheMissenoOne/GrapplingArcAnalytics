# Gym Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A gym owner creates a group, students join it with a short code, and the professor who owns the group can read those students' training — rounds, techniques, ELO, videos — but never their notes.

**Architecture:** Two repos, two PRs. `GrapplingArcAnalytics` owns the schema, the RLS, the `join_group` function, the notes-stripping view and the storage policies; before any of that it folds the untracked `db/auth_setup.sql` into the alembic chain so there is one tracked history. `GrapplingArcApp` owns joining, leaving and uploading video. The professor's UI is piece C and is not in this plan.

**Tech Stack:** Python 3.12 + uv + alembic + SQLAlchemy 2 (`Mapped[...]`) + Postgres/Supabase (RLS, `SECURITY DEFINER`, Storage) on the Analytics side; React Native + Expo SDK 54 + TypeScript strict + `@supabase/supabase-js` + Jest on the App side.

**Spec:** `docs/superpowers/specs/2026-08-11-gym-groups-design.md` in this repo. Read it before Task 1.

## Global Constraints

- The role is spelled `professor`, never `teacher` — in the SQL check constraint, in the policies, in Python and in TypeScript.
- Everything about a session is visible to the professor **except notes**: the session's `reflection` and each round's `notes`. Photos never leave the device; only `MediaKind === 'video'` uploads.
- A student is told in plain words what joining shares, before joining.
- Migration revision IDs in this repo are manual 4-digit strings. Current head is `0022`; this plan claims `0023` and `0024`.
- Every migration writes both `upgrade()` and `downgrade()`. All policy/index DDL is idempotent: `drop policy if exists` before `create policy`, `create index if not exists`.
- `SECURITY DEFINER` functions must set `search_path` explicitly (see `0020_guard_search_path`).
- `db/models.py` and its migration land in the same commit.
- **No agent applies a migration.** Tasks 1 and 3 end at a written file; applying to prod Supabase is the human's step (Task 9).
- App: TypeScript strict, no `any`; async work only in `*ThunksLocal.ts` or a service; i18n keys added to BOTH `en.ts` and `pt-BR.ts` (parity is enforced by `localeParity.test.ts`).
- Offline-first is not negotiable: a student with no group and no network uses the app exactly as before.
- No new dependency in either repo. `@react-native-community/netinfo` (11.4.1) and `expo-file-system` (~19.0.23) are already in `package.json`.

---

## File Structure

**GrapplingArcAnalytics**
- `alembic/versions/0023_adopt_auth_setup.py` — create: the reconciled contents of `db/auth_setup.sql`, tracked.
- `db/auth_setup.sql` — delete (Task 1).
- `db/models.py` — modify: `Group`, `GroupMember`, `GroupInvite` models.
- `alembic/versions/0024_gym_groups.py` — create: tables, `join_group`, policies, `group_member_sessions` view, storage bucket + policies.
- `tests/test_db.py` — modify: round-trip test for the three new models.
- `scripts/group_admin.py` — create: create a group, mint an invite, print the roster.
- `tests/test_group_admin.py` — create.

**GrapplingArcApp**
- `src/services/groupService.ts` — create: `joinGroup`, `leaveGroup`, `getMyGroup`.
- `src/services/__tests__/groupService.test.ts` — create.
- `src/services/videoUploadQueue.ts` — create: enqueue, drain, storage path, size ceiling.
- `src/services/__tests__/videoUploadQueue.test.ts` — create.
- `src/services/sessionSync.ts` — modify: media items carry the storage path after upload.
- `src/components/UserScreen.tsx` — modify: join/leave section.
- `src/components/groups/JoinGroupSheet.tsx` — create: code field + the consent copy.
- `src/components/groups/__tests__/JoinGroupSheet.test.tsx` — create.
- `src/hooks/useSyncManager.ts` — modify: drain the upload queue alongside the session push.
- `src/i18n/translations/en.ts`, `src/i18n/translations/pt-BR.ts` — modify.

---

### Task 0: Drift check — the gate before any migration

**This task runs against prod and is the human's/orchestrator's, not a subagent's.** It decides what Task 1 must contain. Do not write `0023` before this output exists.

**Files:** none. Output is pasted into the PR body for `0023`.

- [ ] **Step 1: List every live policy, RLS flag, and the chain head**

Run each read-only query against prod (the `db-prober` pattern: `uv run python -` with `db.base.db_session`, SELECT only):

```sql
select relname, relrowsecurity
from pg_class
where relnamespace = 'public'::regnamespace and relkind = 'r'
order by relname;

select schemaname, tablename, policyname, cmd, roles, qual, with_check
from pg_policies
where schemaname = 'public'
order by tablename, policyname;

select version_num from alembic_version;

select p.proname, p.prosecdef, p.proconfig
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
order by p.proname;
```

Expected: `version_num` = `0022`.

- [ ] **Step 2: Diff live against tracked**

Compare the `pg_policies` rows against the union of every `create policy` in `alembic/versions/*.py` plus `db/auth_setup.sql`. Write down three lists:

1. Live but untracked → **must** be folded into `0023`.
2. Tracked but not live → flag; do not silently drop it.
3. Live and tracked but with different `qual`/`with_check` → the live text wins for `0023`, and the difference is called out in the PR.

- [ ] **Step 3: Record the result in the PR body for Task 1**

Paste the three lists. If all three are empty, say so explicitly — "live == tracked, `0023` is a verbatim adoption" is itself the finding.

---

### Task 1: Revision 0023 — adopt the untracked policies

**Files:**
- Create: `alembic/versions/0023_adopt_auth_setup.py`
- Delete: `db/auth_setup.sql`

**Interfaces:**
- Consumes: the three lists from Task 0.
- Produces: chain head `0023`; `0024` sets `down_revision = "0023"`.

- [ ] **Step 1: Confirm the current head**

Run: `uv run alembic history | head -3`
Expected: a line reading `0021 -> 0022 (head), ...`.

- [ ] **Step 2: Create the revision file**

Run: `uv run alembic revision -m "adopt auth_setup" --rev-id 0023`

- [ ] **Step 3: Fill in `upgrade()` with the reconciled SQL**

Copy the contents of `db/auth_setup.sql` into `op.execute(...)` blocks, one block per numbered section of that file, applying the Task 0 reconciliation. Keep the file's own comments — they explain why each grant exists. The docstring must state what this revision is for:

```python
"""Adopt db/auth_setup.sql into the alembic chain.

The user-graph RLS, the profile auto-provision trigger and the id defaults were
hand-run in the Supabase SQL editor and lived only in ``db/auth_setup.sql`` — the
same untracked-policy split that let live and tracked diverge on 2026-06-25. This
revision takes ownership of them; the file is deleted in the same commit.

The SQL is idempotent (``drop policy if exists`` before every ``create policy``,
``create or replace function``), so applying this where the file has already been
run is a no-op. That is what makes adoption safe against a live database.

Scope note: the trigger on ``auth.users`` is included here, but creating it needs
privileges in the ``auth`` schema that the migration role may not hold. If the
apply fails on permissions, that one stanza moves to ``db/auth_users_trigger.sql``
and this docstring gets a pointer to it — everything in ``public`` stays here.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-11
"""
```

- [ ] **Step 4: Write `downgrade()`**

Drop every policy this revision creates, drop the trigger and function, and revert the id defaults:

```python
def downgrade() -> None:
    op.execute("drop trigger if exists on_auth_user_created on auth.users;")
    op.execute("drop function if exists public.handle_new_user();")
    # one line per policy created in upgrade(), same names:
    op.execute("drop policy if exists profiles_select_own on public.profiles;")
    # ... etc, mirroring upgrade() exactly ...
    op.execute("alter table public.graphs alter column id drop default;")
    op.execute("alter table public.graph_edges alter column id drop default;")
    op.execute("alter table public.profiles alter column id drop default;")
```

- [ ] **Step 5: Delete the file**

Run: `git rm db/auth_setup.sql`

Then grep for anything that still points at it:

Run: `grep -rn "auth_setup" --include="*.py" --include="*.md" --include="*.sql" . | grep -v "alembic/versions/0023"`
Expected: only doc references, which are updated to say the policies live in `0023`.

- [ ] **Step 6: Check the suite still passes**

Run: `uv run pytest && uv run ruff check .`
Expected: green. The suite does not execute the migration (SQLite in memory, no RLS) — this only proves nothing else broke.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/0023_adopt_auth_setup.py db/auth_setup.sql
git commit -m "feat(db): the auth policies join the migration chain

0023 takes ownership of what db/auth_setup.sql held — user-graph RLS, the
profile auto-provision trigger, the id defaults — and the file is deleted.
The SQL is idempotent, so applying it where it has already run is a no-op.

Written from the live pg_policies diff, not from the file, so an adopted
policy is what prod actually has."
```

---

### Task 2: The three models

**Files:**
- Modify: `db/models.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `Group`, `GroupMember`, `GroupInvite` — used by Task 4's admin script.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`, following the round-trip pattern the other tables use there:

```python
def test_group_membership_round_trips(session):
    prof = Profile(id="11111111-1111-1111-1111-111111111111", full_name="Professor")
    student = Profile(id="22222222-2222-2222-2222-222222222222", full_name="Aluno")
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=prof.id, name="Gracie Barra")
    session.add_all([prof, student, group])
    session.add_all([
        GroupMember(group_id=group.id, profile_id=prof.id, role="professor"),
        GroupMember(group_id=group.id, profile_id=student.id, role="student"),
    ])
    session.commit()

    roles = {m.profile_id: m.role for m in session.query(GroupMember).filter_by(group_id=group.id)}
    assert roles == {prof.id: "professor", student.id: "student"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_db.py::test_group_membership_round_trips -v`
Expected: FAIL — `NameError: name 'Group' is not defined`.

- [ ] **Step 3: Add the models**

In `db/models.py`, next to the other tables:

```python
class Group(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    # 'professor', not 'teacher' — the word the product uses, in the data too.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("role in ('owner','professor','student')", name="ck_group_members_role"),
        Index("idx_group_members_profile", "profile_id"),
    )


class GroupInvite(Base):
    __tablename__ = "group_invites"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Add `CheckConstraint` and `Index` to the existing `sqlalchemy` import line if they are not already there.

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS, and every other test in the file still green.

- [ ] **Step 5: Commit** (with Task 3 — models and migration land together, so do not commit yet if you are running the tasks back to back; otherwise commit and amend in Task 3.)

---

### Task 3: Revision 0024 — the whole groups domain

**Files:**
- Create: `alembic/versions/0024_gym_groups.py`

**Interfaces:**
- Consumes: `0023` as `down_revision`; the models from Task 2.
- Produces: `join_group(invite_code text)` returning `(group_id uuid, group_name text)` — called by Task 5's `groupService.joinGroup`. Storage bucket id `session-videos`, object path `{owner_id}/{session_id}/{media_id}.mp4` — used by Task 7.

- [ ] **Step 1: Create the revision file**

Run: `uv run alembic revision -m "gym groups" --rev-id 0024`

- [ ] **Step 2: Tables**

```python
def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_id", UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "group_members",
        sa.Column("group_id", UUID(as_uuid=False), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("profile_id", UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role in ('owner','professor','student')", name="ck_group_members_role"),
    )
    op.create_index("idx_group_members_profile", "group_members", ["profile_id"])
    op.create_table(
        "group_invites",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("group_id", UUID(as_uuid=False), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
```

- [ ] **Step 3: `join_group`**

```python
    op.execute("""
    create or replace function public.join_group(invite_code text)
    returns table (group_id uuid, group_name text)
    language plpgsql
    security definer
    set search_path = public
    as $$
    declare
      target_group uuid;
    begin
      select gi.group_id into target_group
      from public.group_invites gi
      where gi.code = invite_code
        and gi.revoked_at is null
        and gi.expires_at > now();

      -- One error for unknown, expired and revoked alike: a wrong code must not
      -- reveal whether a group exists.
      if target_group is null then
        raise exception 'invalid_invite' using errcode = 'P0001';
      end if;

      insert into public.group_members (group_id, profile_id, role)
      values (target_group, auth.uid(), 'student')
      on conflict (group_id, profile_id) do nothing;

      return query
        select g.id, g.name from public.groups g where g.id = target_group;
    end;
    $$;
    """)
    op.execute("revoke all on function public.join_group(text) from public;")
    op.execute("grant execute on function public.join_group(text) to authenticated;")
```

- [ ] **Step 4: Policies**

```python
    op.execute("alter table public.groups enable row level security;")
    op.execute("alter table public.group_members enable row level security;")
    op.execute("alter table public.group_invites enable row level security;")

    op.execute("drop policy if exists groups_select_member on public.groups;")
    op.execute("""
    create policy groups_select_member on public.groups for select to authenticated
    using (exists (select 1 from public.group_members m
                   where m.group_id = groups.id and m.profile_id = auth.uid()));
    """)

    op.execute("drop policy if exists groups_insert_own on public.groups;")
    op.execute("""
    create policy groups_insert_own on public.groups for insert to authenticated
    with check (owner_id = auth.uid());
    """)

    op.execute("drop policy if exists group_members_select_same_group on public.group_members;")
    op.execute("""
    create policy group_members_select_same_group on public.group_members for select to authenticated
    using (exists (select 1 from public.group_members me
                   where me.group_id = group_members.group_id and me.profile_id = auth.uid()));
    """)

    -- the student leaves; the owner removes
    op.execute("drop policy if exists group_members_delete_self_or_owner on public.group_members;")
    op.execute("""
    create policy group_members_delete_self_or_owner on public.group_members for delete to authenticated
    using (profile_id = auth.uid()
           or exists (select 1 from public.groups g
                      where g.id = group_members.group_id and g.owner_id = auth.uid()));
    """)

    op.execute("drop policy if exists group_invites_owner_all on public.group_invites;")
    op.execute("""
    create policy group_invites_owner_all on public.group_invites for all to authenticated
    using (exists (select 1 from public.groups g
                   where g.id = group_invites.group_id and g.owner_id = auth.uid()))
    with check (exists (select 1 from public.groups g
                        where g.id = group_invites.group_id and g.owner_id = auth.uid()));
    """)
```

No `insert` policy on `group_members`: joining goes through `join_group` only.

Note: the `--` comment line above is illustrative prose; in the real file use a Python `#` comment, not SQL, outside the `op.execute` strings.

- [ ] **Step 5: The view — this is where the notes are stripped**

```python
    op.execute("""
    create or replace view public.group_member_sessions with (security_invoker = true) as
    select
      us.id,
      us.owner_id,
      us.updated_at,
      jsonb_set(
        us.data - 'reflection',
        '{rounds}',
        coalesce(
          (select jsonb_agg(r - 'notes') from jsonb_array_elements(us.data->'rounds') r),
          '[]'::jsonb
        )
      ) as data
    from public.user_sessions us
    where exists (
      select 1
      from public.group_members me
      join public.group_members them using (group_id)
      where me.profile_id = auth.uid()
        and me.role in ('owner','professor')
        and them.profile_id = us.owner_id
    );
    """)
    op.execute("grant select on public.group_member_sessions to authenticated;")
```

Both levels matter: the session's `reflection` and every round's own `notes`. `RoundSnapshot` in the App does not declare `notes`, but `RoundSheet` writes it — strip only `reflection` and the diary leaks through the rounds.

- [ ] **Step 6: Storage**

```python
    op.execute("""
    insert into storage.buckets (id, name, public)
    values ('session-videos', 'session-videos', false)
    on conflict (id) do nothing;
    """)

    op.execute("drop policy if exists session_videos_owner_write on storage.objects;")
    op.execute("""
    create policy session_videos_owner_write on storage.objects for all to authenticated
    using (bucket_id = 'session-videos'
           and (storage.foldername(name))[1] = auth.uid()::text)
    with check (bucket_id = 'session-videos'
                and (storage.foldername(name))[1] = auth.uid()::text);
    """)

    op.execute("drop policy if exists session_videos_professor_read on storage.objects;")
    op.execute("""
    create policy session_videos_professor_read on storage.objects for select to authenticated
    using (bucket_id = 'session-videos' and exists (
      select 1 from public.group_members me
      join public.group_members them using (group_id)
      where me.profile_id = auth.uid()
        and me.role in ('owner','professor')
        and them.profile_id::text = (storage.foldername(name))[1]
    ));
    """)
```

- [ ] **Step 7: `downgrade()`**

```python
def downgrade() -> None:
    op.execute("drop policy if exists session_videos_professor_read on storage.objects;")
    op.execute("drop policy if exists session_videos_owner_write on storage.objects;")
    op.execute("delete from storage.buckets where id = 'session-videos';")
    op.execute("drop view if exists public.group_member_sessions;")
    op.execute("drop function if exists public.join_group(text);")
    op.drop_table("group_invites")
    op.drop_index("idx_group_members_profile", table_name="group_members")
    op.drop_table("group_members")
    op.drop_table("groups")
```

- [ ] **Step 8: Read it end to end**

Two things a green suite cannot catch: every `create policy` has its `drop policy if exists` immediately before it, and `downgrade()` actually reverses `upgrade()`.

- [ ] **Step 9: Verify and commit**

Run: `uv run pytest && uv run ruff check .`
Expected: green.

```bash
git add db/models.py tests/test_db.py alembic/versions/0024_gym_groups.py
git commit -m "feat(db): a gym gets a group, and the professor reads training without the diary

groups, group_members and group_invites, plus the two things that make the
boundary structural: join_group() as SECURITY DEFINER, because RLS cannot
validate an invite code without letting a client enumerate codes, and the
group_member_sessions view, which is the professor's ONLY read path.

The view strips two levels of notes — the session's reflection and each round's
own notes. RoundSnapshot does not declare the second one; RoundSheet writes it.

Video lives in a private bucket keyed by owner id, so leaving the group revokes
the video with the same policy that revokes the rows."
```

---

### Task 4: Admin script — the interim professor

**Files:**
- Create: `scripts/group_admin.py`
- Test: `tests/test_group_admin.py`

**Interfaces:**
- Consumes: the models from Task 2.
- Produces: `create_group(owner_id, name)`, `mint_invite(group_id, created_by, days=7)`, `roster(group_id)`.

- [ ] **Step 1: Write the failing test**

```python
from scripts.group_admin import mint_invite, roster


def test_mint_invite_is_readable_and_expires(session):
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=PROF_ID, name="GB")
    session.add(group)
    session.commit()

    invite = mint_invite(session, group.id, PROF_ID, days=7)

    assert invite.code.startswith("GA-")
    assert len(invite.code) == 9              # "GA-" + 6
    assert set(invite.code[3:]) <= set("ACDEFGHJKMNPQRTUVWXY34679")   # no 0/O, 1/I/L, S/5, 8/B
    assert (invite.expires_at - invite.created_at_or_now()).days == 7
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_group_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.group_admin'`.

- [ ] **Step 3: Write the script**

```python
"""Create a group, mint an invite, print the roster.

The professor's real UI is piece C (the web app). Until it exists, this is how a
gym gets a group and a code to hand out.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from db.models import Group, GroupInvite, GroupMember

# Characters a person can read off a whiteboard and type without a second try:
# no 0/O, no 1/I/L, no 5/S, no 8/B.
_ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"


def _code() -> str:
    return "GA-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def create_group(session, owner_id: str, name: str) -> Group:
    group = Group(owner_id=owner_id, name=name)
    session.add(group)
    session.flush()
    session.add(GroupMember(group_id=group.id, profile_id=owner_id, role="owner"))
    session.commit()
    return group


def mint_invite(session, group_id: str, created_by: str, days: int = 7) -> GroupInvite:
    invite = GroupInvite(
        code=_code(),
        group_id=group_id,
        created_by=created_by,
        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
    )
    session.add(invite)
    session.commit()
    return invite


def roster(session, group_id: str) -> list[tuple[str, str]]:
    rows = session.query(GroupMember).filter_by(group_id=group_id).all()
    return [(m.profile_id, m.role) for m in rows]
```

Drop the `created_at_or_now()` helper from the test if the model has no such column — assert against `datetime.now(timezone.utc)` with a tolerance instead:

```python
    assert 6 < (invite.expires_at - datetime.now(timezone.utc)).days <= 7
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/test_group_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/group_admin.py tests/test_group_admin.py
git commit -m "feat(admin): mint a group invite until the web view exists"
```

---

### Task 5: App — the group service

**Files:**
- Create: `src/services/groupService.ts`
- Test: `src/services/__tests__/groupService.test.ts`

**Interfaces:**
- Consumes: `join_group(invite_code text)` from Task 3.
- Produces: `joinGroup(code: string): Promise<JoinResult>`, `leaveGroup(groupId: string): Promise<void>`, `getMyGroup(): Promise<MyGroup | null>`, `type MyGroup = { id: string; name: string; role: GroupRole }`, `type GroupRole = 'owner' | 'professor' | 'student'`.

- [ ] **Step 1: Write the failing test**

Follow the mock shape `src/services/__tests__/sessionSync.test.ts` already uses:

```typescript
const mockRpc = jest.fn();
const mockFrom = jest.fn();
let mockConfigured = true;

jest.mock('../supabaseClient', () => ({
  isSupabaseConfigured: jest.fn(() => mockConfigured),
  getSupabaseClient: jest.fn(() => ({ rpc: mockRpc, from: mockFrom })),
}));

import { joinGroup, InvalidInviteError } from '../groupService';

describe('joinGroup', () => {
  beforeEach(() => { mockConfigured = true; mockRpc.mockReset(); });

  it('returns the group a valid code opens', async () => {
    mockRpc.mockResolvedValue({ data: [{ group_id: 'g1', group_name: 'Gracie Barra' }], error: null });

    await expect(joinGroup('GA-7K2M9')).resolves.toEqual({ id: 'g1', name: 'Gracie Barra' });
    expect(mockRpc).toHaveBeenCalledWith('join_group', { invite_code: 'GA-7K2M9' });
  });

  it('reports an invalid code without leaking whether the group exists', async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: 'invalid_invite', code: 'P0001' } });

    await expect(joinGroup('GA-XXXXX')).rejects.toBeInstanceOf(InvalidInviteError);
  });

  it('refuses to join with no cloud configured instead of pretending', async () => {
    mockConfigured = false;

    await expect(joinGroup('GA-7K2M9')).rejects.toThrow();
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `npx jest src/services/__tests__/groupService.test.ts`
Expected: FAIL — cannot find module `../groupService`.

- [ ] **Step 3: Write the service**

```typescript
/**
 * Groups — a gym's students under one professor.
 *
 * Joining is an RPC, not an insert: RLS cannot validate an invite code without
 * giving the client enough read access to enumerate codes, so `join_group` runs
 * SECURITY DEFINER on the server and the client never touches group_members
 * on the way in. Leaving IS a plain delete — ordinary RLS expresses "my own row".
 */

import { getSupabaseClient, isSupabaseConfigured } from './supabaseClient';

export type GroupRole = 'owner' | 'professor' | 'student';
export interface MyGroup { id: string; name: string; role: GroupRole }

export class InvalidInviteError extends Error {
  constructor() { super('invalid_invite'); this.name = 'InvalidInviteError'; }
}

export async function joinGroup(code: string): Promise<{ id: string; name: string }> {
  if (!isSupabaseConfigured()) throw new Error('cloud_not_configured');
  const { data, error } = await getSupabaseClient().rpc('join_group', { invite_code: code.trim() });
  if (error) throw new InvalidInviteError();
  const row = Array.isArray(data) ? data[0] : data;
  if (!row) throw new InvalidInviteError();
  return { id: row.group_id, name: row.group_name };
}

export async function leaveGroup(groupId: string, profileId: string): Promise<void> {
  if (!isSupabaseConfigured()) throw new Error('cloud_not_configured');
  const { error } = await getSupabaseClient()
    .from('group_members')
    .delete()
    .eq('group_id', groupId)
    .eq('profile_id', profileId);
  if (error) throw error;
}

export async function getMyGroup(profileId: string): Promise<MyGroup | null> {
  if (!isSupabaseConfigured()) return null;
  const { data, error } = await getSupabaseClient()
    .from('group_members')
    .select('role, group_id, groups(name)')
    .eq('profile_id', profileId)
    .limit(1);
  if (error || !data?.length) return null;
  const row = data[0] as { role: GroupRole; group_id: string; groups: { name: string } | null };
  return { id: row.group_id, name: row.groups?.name ?? '', role: row.role };
}
```

- [ ] **Step 4: Run it and watch it pass**

Run: `npx jest src/services/__tests__/groupService.test.ts`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/services/groupService.ts src/services/__tests__/groupService.test.ts
git commit -m "feat(groups): join by code, leave by row"
```

---

### Task 6: App — joining, and saying what it shares

**Files:**
- Create: `src/components/groups/JoinGroupSheet.tsx`
- Test: `src/components/groups/__tests__/JoinGroupSheet.test.tsx`
- Modify: `src/components/UserScreen.tsx`, `src/i18n/translations/en.ts`, `src/i18n/translations/pt-BR.ts`

**Interfaces:**
- Consumes: `joinGroup`, `leaveGroup`, `getMyGroup` from Task 5.

- [ ] **Step 1: Write the failing test**

The consent copy is the point of this component, so the test asserts it:

```typescript
it('names what the professor will see AND what stays private, before joining', () => {
  const { getByText } = render(<JoinGroupSheet visible onJoined={jest.fn()} onClose={jest.fn()} />);

  expect(getByText(/rounds/i)).toBeTruthy();
  expect(getByText(/v[ií]deos/i)).toBeTruthy();
  expect(getByText(/reflex/i)).toBeTruthy();   // "não vê: sua reflexão e suas notas"
});

it('does not call joinGroup until the student confirms', () => {
  const { getByText } = render(<JoinGroupSheet visible onJoined={jest.fn()} onClose={jest.fn()} />);
  fireEvent.changeText(getByTestId('invite-code'), 'GA-7K2M9');
  expect(mockJoinGroup).not.toHaveBeenCalled();
  fireEvent.press(getByText(/entrar/i));
  expect(mockJoinGroup).toHaveBeenCalledWith('GA-7K2M9');
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `npx jest src/components/groups/__tests__/JoinGroupSheet.test.tsx`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Add the i18n keys to BOTH files**

`en.ts`, under a new `groups` section:

```typescript
  groups: {
    join: "Join a group",
    codeLabel: "Invite code",
    codePlaceholder: "GA-XXXXXX",
    consentTitle: "What your professor will see",
    consentShared: "Your rounds, techniques, ELO and the videos you record.",
    consentPrivate: "Not your reflection, and not the notes you write on a round.",
    confirm: "Join",
    leave: "Leave group",
    invalidCode: "This code is not valid.",
    needsAccount: "Joining a group needs an account.",
    needsConnection: "You need a connection to join a group.",
  },
```

`pt-BR.ts`, same keys:

```typescript
  groups: {
    join: "Entrar em um grupo",
    codeLabel: "Código do convite",
    codePlaceholder: "GA-XXXXXX",
    consentTitle: "O que seu professor vai ver",
    consentShared: "Seus rounds, técnicas, ELO e os vídeos que você gravar.",
    consentPrivate: "Não vê sua reflexão, nem as notas que você escreve no round.",
    confirm: "Entrar",
    leave: "Sair do grupo",
    invalidCode: "Esse código não é válido.",
    needsAccount: "Entrar em um grupo exige uma conta.",
    needsConnection: "Você precisa de conexão para entrar em um grupo.",
  },
```

- [ ] **Step 4: Write the sheet**

Use the design-system components the app already has (`Card`, `Button`, `IconButton`) — not raw `View`/`Text` styling. The sheet renders, in order: title, code input (`testID="invite-code"`), the two consent lines (`consentShared` then `consentPrivate`), then the confirm button. A guest sees `needsAccount` and no input. On `InvalidInviteError`, show `invalidCode` under the field and keep the sheet open.

- [ ] **Step 5: Run it and watch it pass**

Run: `npx jest src/components/groups/__tests__/JoinGroupSheet.test.tsx`
Expected: PASS.

- [ ] **Step 6: Wire it into the account screen**

In `src/components/UserScreen.tsx`, add a group row: with no group, a button opening the sheet; with a group, the group's name and a `leave` button that calls `leaveGroup` and clears local state.

- [ ] **Step 7: Full suite + parity**

Run: `npm test && npx tsc --noEmit`
Expected: green, `localeParity.test.ts` included.

- [ ] **Step 8: Commit**

```bash
git add src/components/groups src/components/UserScreen.tsx src/i18n/translations
git commit -m "feat(groups): a student joins knowing exactly what is shared"
```

---

### Task 7: App — the video upload queue

**Files:**
- Create: `src/services/videoUploadQueue.ts`
- Test: `src/services/__tests__/videoUploadQueue.test.ts`
- Modify: `src/services/sessionSync.ts`, `src/hooks/useSyncManager.ts`

**Interfaces:**
- Consumes: bucket `session-videos`, path `{owner_id}/{session_id}/{media_id}.mp4` from Task 3.
- Produces: `enqueueSessionVideos(session: SessionState, ownerId: string): Promise<void>`, `drainUploadQueue(ownerId: string): Promise<UploadResult[]>`, `MAX_VIDEO_BYTES`.

- [ ] **Step 1: Write the failing tests**

```typescript
it('enqueues videos and ignores photos', async () => {
  await enqueueSessionVideos(sessionWith([
    { id: 'm1', kind: 'video', url: 'file:///v.mp4', size: 10_000 },
    { id: 'm2', kind: 'image', url: 'file:///p.jpg', size: 10_000 },
  ]), 'u1');

  expect((await readQueue()).map((i) => i.mediaId)).toEqual(['m1']);
});

it('uploads to a path the storage policy accepts', async () => {
  await enqueueSessionVideos(sessionWith([{ id: 'm1', kind: 'video', url: 'file:///v.mp4', size: 10 }]), 'u1');
  await drainUploadQueue('u1');

  expect(mockUpload).toHaveBeenCalledWith('u1/s-1/m1.mp4', expect.anything(), expect.anything());
});

it('declines a video over the ceiling instead of failing silently', async () => {
  await enqueueSessionVideos(sessionWith([
    { id: 'm1', kind: 'video', url: 'file:///big.mp4', size: MAX_VIDEO_BYTES + 1 },
  ]), 'u1');

  const [result] = await drainUploadQueue('u1');
  expect(result.status).toBe('too_large');
  expect(mockUpload).not.toHaveBeenCalled();
});

it('keeps the item queued when there is no connection', async () => {
  mockNetInfo.mockResolvedValue({ isConnected: false, isInternetReachable: false });
  await enqueueSessionVideos(sessionWith([{ id: 'm1', kind: 'video', url: 'file:///v.mp4', size: 10 }]), 'u1');

  await drainUploadQueue('u1');

  expect(mockUpload).not.toHaveBeenCalled();
  expect(await readQueue()).toHaveLength(1);
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `npx jest src/services/__tests__/videoUploadQueue.test.ts`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Write the queue**

Persist the queue with the app's shared storage engine (`AsyncStorage` from `src/utils/offlineStorage`, the same one the rest of the app uses — not a private facade). Upload with `expo-file-system`'s upload against the Supabase Storage REST endpoint, or `supabase.storage.from('session-videos').upload(path, blob)` if the file is read into memory under the ceiling. Read connectivity through `@react-native-community/netinfo` (already a dependency).

Rules: only `kind === 'video'`; path `${ownerId}/${sessionId}/${mediaId}.mp4`; over `MAX_VIDEO_BYTES` returns `{ status: 'too_large' }` and leaves the item marked so the UI can say why; no connection leaves the item queued; automatic drain only on an unmetered connection, while an explicit "enviar agora" drains regardless.

- [ ] **Step 4: Run them and watch them pass**

Run: `npx jest src/services/__tests__/videoUploadQueue.test.ts`
Expected: PASS, 4 tests.

- [ ] **Step 5: Keep local URIs out of the cloud**

In `sessionSync.ts`, `stripMediaForSync` keeps stripping `file://` URIs. What changes is the order: a media item syncs with the storage path only after its upload succeeds. Add a test asserting a queued-but-not-uploaded video still leaves nothing in the synced payload.

- [ ] **Step 6: Drain alongside the existing sync**

In `useSyncManager.ts`, call `drainUploadQueue` where the session push already runs. Nothing in the UI awaits it.

- [ ] **Step 7: Full suite**

Run: `npm test && npx tsc --noEmit && npx eslint src`
Expected: green, eslint 0 errors.

- [ ] **Step 8: Commit**

```bash
git add src/services/videoUploadQueue.ts src/services/__tests__/videoUploadQueue.test.ts src/services/sessionSync.ts src/hooks/useSyncManager.ts
git commit -m "feat(groups): the round's video reaches the professor, the photo never leaves"
```

---

### Task 8: Two PRs, cross-referenced

- [ ] **Step 1: Analytics PR against `main`**

Body includes: the Task 0 drift lists, the exact apply command with the DSN redacted, the §7 drift-check queries ready to paste, and the RLS checklist from Task 9. Title: "Gym groups: adopt the auth policies, then build the domain".

- [ ] **Step 2: App PR against `development`**

Body links the Analytics PR and states the dependency: it cannot work until `0024` is live, because it calls `join_group` and writes to a bucket that does not exist yet.

---

### Task 9: Apply — human/orchestrator only

**No agent runs any of this.**

- [ ] **Step 1: Apply `0023`**

```bash
cd GrapplingArcAnalytics
DATABASE_URL=<prod DSN — never pasted into chat or logs> uv run alembic upgrade 0023
```

- [ ] **Step 2: Re-run the Task 0 queries + the Supabase advisor**

Expected: `alembic_version` = `0023`; the policy set is unchanged from before the apply (adoption is a no-op on a database that already ran the file); advisor reports 0 ERROR.

- [ ] **Step 3: Apply `0024`**

```bash
DATABASE_URL=<prod DSN> uv run alembic upgrade head
```

- [ ] **Step 4: Run the RLS checklist, record the output in the PR**

1. A student selecting another student's `user_sessions` gets 0 rows.
2. A professor selecting `group_member_sessions` gets rows whose `data` has no `reflection` key and whose every round has no `notes` key.
3. A professor selecting `user_sessions` directly gets 0 rows.
4. After a student deletes their `group_members` row, that professor gets 0 rows from the view and cannot read the student's objects in `session-videos`.
5. `select join_group('GA-NOPE')` and `select join_group('<a revoked code>')` fail with the same error.

- [ ] **Step 5: Merge the App PR once `0024` is live.**

---

## Self-Review

**Spec coverage:** Component 0 → Tasks 0, 1. Component 1 (schema) → Tasks 2, 3. Component 2 (`join_group`) → Task 3 step 3, Task 5. Component 3 (view, notes stripped at both levels) → Task 3 step 5, Task 9 check 2. Component 4 (video) → Task 3 step 6, Task 7. Component 5 (app joining, consent copy, guest, offline) → Task 6. Component 6 (interim admin) → Task 4. Testing section → Tasks 2, 4, 5, 6, 7 and Task 9's checklist. Apply order → Task 9.

**Type consistency:** `join_group(invite_code text)` returns `(group_id, group_name)` in Task 3 and is read as `row.group_id` / `row.group_name` in Task 5. `MyGroup`/`GroupRole` are declared once in Task 5 and used in Task 6. The storage path `{owner_id}/{session_id}/{media_id}.mp4` is written in Task 3's policy and asserted verbatim in Task 7's test. The role string is `professor` everywhere.

**Known gap, stated rather than hidden:** no task proves the RLS automatically. There is no local Postgres and the pytest suite runs SQLite, which has no RLS. Task 9's checklist is the proof, and it is a human running queries — not a green suite.
