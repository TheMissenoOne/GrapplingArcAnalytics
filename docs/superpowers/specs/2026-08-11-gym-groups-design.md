# Gym Groups — professor, student, invite, shared video

## Purpose

Give a gym a group its students join, and give the professor who owns that group a real view of what those students train — including the videos they record.

This is piece **B** of three. Piece A (Map node graph + session flowchart) is done. Piece C (the web app, user and professor profiles) depends on this one: B builds the domain and the app-side join; C builds the professor's screens.

Cross-module by construction: the schema, RLS and storage policies live in **GrapplingArcAnalytics**; joining a group and uploading video live in **GrapplingArcApp**. Two pull requests, one per repo, referencing each other. Never one PR spanning both.

## Approved Decisions

- A group is flat: one owner, many members. "Turma" is a label on a session, not an entity. No gym→class hierarchy in this cut.
- Roles are `owner`, `professor`, `student`. The word is `professor`, in the database enum, in the policies and in the code — not `teacher`.
- Joining a group shares **everything about a session except notes**: rounds, techniques, ELO, graph, goal, title, and videos are visible to the professor; the session's `reflection` and each round's `notes` are not. Photos never leave the device.
- The student is told, in plain words, what joining shares — before joining, not in a terms document.
- Invites are short codes (`GA-7K2M9`), typed into the app.
- The professor's first-cut view is a member list with drill-down into one student. It is built in piece C; B ships no professor UI. Until C exists, invites and roster come from an admin script in Analytics.
- Video upload is in scope for B.
- Leaving the group revokes access to everything, video included, through the same policies.
- The alembic/`db/auth_setup.sql` split is fixed **inside this piece**, before B's own policies are written. One tracked history for schema and policy both.

## Architecture

```
GrapplingArcAnalytics                          GrapplingArcApp
  groups / group_members / group_invites         "Entrar em um grupo" (code + consent)
  join_group(code)  SECURITY DEFINER             leave group
  view group_member_sessions  (notes stripped)   video upload queue -> Storage
  storage bucket session-videos + policies       stripMediaForSync keeps local URIs out
  admin script: create group, mint invite, roster
```

The professor never reads `user_sessions`. They read one view, and the view is what removes the notes. A client cannot opt out of that.

## Component 0 — Adopt the untracked policies first

Today RLS lives in two places: `alembic/versions/0003_athlete_graph_rls.py` (tracked, applied by `alembic upgrade`) and `db/auth_setup.sql` (174 lines, 11 policies, 4 functions/triggers, hand-run in the Supabase SQL editor). That split is what let live and tracked diverge on 2026-06-25. B does not add a third place; it removes the second.

**Revision `0023` adopts `db/auth_setup.sql` into the alembic chain**, then the file is deleted. The SQL is already idempotent (`drop policy if exists` before every `create policy`, `create or replace function`), so applying it where it has already been run is a no-op — that is what makes adoption safe on a live database.

Order matters, and the first step is not writing code:

1. **Diff live against the file before adopting anything.** Run the §7 drift-check queries (`pg_policies`, `pg_class.relrowsecurity`, `alembic_version`) and compare against the union of `0003` + `auth_setup.sql`. Adopting a stale file would codify the wrong state into the chain and make the drift permanent instead of fixing it. Anything live-but-untracked gets folded into `0023`; anything tracked-but-missing gets flagged before the migration is written.
2. `0023` carries the reconciled truth, verbatim and idempotent, with a `downgrade()` that drops what it created — the convention every migration in this repo follows.
3. `db/auth_setup.sql` is deleted in the same commit. A file that is no longer the source of truth but still readable is how the next drift starts.

**The one part that may not move: the `auth.users` trigger.** `handle_new_user()` and its trigger on `auth.users` are why the file was split off in the first place — creating a trigger in the `auth` schema needs privileges the migration role may not have. The migration attempts it with the rest; the apply is a human step, so if it fails on permissions the fallback is explicit and small: that single stanza returns to a file named for exactly what it is (`db/auth_users_trigger.sql`), with a pointer comment in `0023` saying why it could not be tracked. Everything in `public` — the 11 policies, the grants, the id defaults — moves regardless.

This is verifiable only against a real Postgres. There is no local Postgres and no preview-branch tooling in this repo, and the pytest suite runs SQLite in memory, which has no RLS. So `0023` ships with the drift-check queries in the PR body and is applied by a human, per the handoff gate.

## Component 1 — Schema

```sql
groups        (id uuid pk, owner_id uuid -> profiles(id) on delete cascade,
               name text not null, created_at timestamptz default now())

group_members (group_id uuid -> groups(id) on delete cascade,
               profile_id uuid -> profiles(id) on delete cascade,
               role text not null check (role in ('owner','professor','student')),
               joined_at timestamptz default now(),
               primary key (group_id, profile_id))

group_invites (code text primary key,
               group_id uuid -> groups(id) on delete cascade,
               created_by uuid -> profiles(id),
               expires_at timestamptz not null,
               revoked_at timestamptz)
```

The invite is its own table rather than a column on `groups` so a code can expire or be rotated without touching the group or its members. Index `group_members(profile_id)` — every policy below starts from "which groups is this user in".

Everything lands in revision **`0024`** — tables, `db/models.py` models, the `join_group` function, the policies, the view and the storage rules. After Component 0 there is no second place to put them. `0024` depends on `0023`; B's own policies are never written into a file outside the chain.

Current head is `0022`, so B claims `0023` and `0024`. `db/models.py` and the migration land in the same commit, per this repo's rule that a model change without a migration passes SQLite tests while prod lacks the column.

## Component 2 — Joining is a function, not an insert

Row-level security cannot validate an invite code without giving the client enough read access to enumerate codes. So the student never holds `insert` on `group_members`. Instead:

```sql
create function join_group(invite_code text) returns table (group_id uuid, group_name text)
language plpgsql security definer set search_path = public as $$ ... $$;
```

It validates that the code exists, is not revoked and has not expired; inserts `(group_id, auth.uid(), 'student')` if the caller is not already a member; and returns the group so the app can name it in the confirmation. An unknown, expired or revoked code returns the same "invalid code" error — a wrong code must not reveal whether a group exists.

`set search_path = public` is not optional on a `SECURITY DEFINER` function; migration `0020_guard_search_path` exists in this repo for exactly this class of bug.

Leaving is a plain delete of the caller's own row, which ordinary RLS can express.

## Component 3 — What the professor can read

```sql
create view group_member_sessions with (security_invoker = true) as
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
from user_sessions us
where exists (
  select 1
  from group_members me
  join group_members them using (group_id)
  where me.profile_id = auth.uid()
    and me.role in ('owner','professor')
    and them.profile_id = us.owner_id
);
```

Two levels of notes are stripped, not one. The session's `reflection` is the obvious one. The second is easy to miss: **every round carries its own `notes`** — written in `RoundSheet` (`notes` at line 49, saved at line 120) — even though `RoundSnapshot` in `src/types/session.ts` does not declare the field. Strip only `reflection` and the student's diary leaks through the rounds.

`goal` and `title` stay visible, by decision: they are what the student was training for, which is the professor's business.

Grants: the professor role gets `select` on the view only. `user_sessions` keeps its owner-only policies untouched — no policy is added there, so there is no path to the raw row.

## Component 4 — Video

Bucket `session-videos`, private. Object path `{owner_id}/{session_id}/{media_id}.mp4`, so the first path segment is the owner and the storage policies are a prefix comparison:

- insert/update/delete: `(storage.foldername(name))[1] = auth.uid()::text`.
- select: the owner, or a professor who shares a group with the profile named by that first segment — the same `exists` clause the view uses.

Leaving a group therefore revokes video access with no extra bookkeeping.

App side:

- Only `MediaKind === 'video'` uploads. Photos never leave the device.
- The upload queue hangs off the existing `useSyncManager`; it is not a second sync engine. Saving a session enqueues; the queue drains when there is a connection; nothing in the UI ever waits on an upload.
- Automatic upload only on an unmetered connection. Otherwise the item waits with an explicit "enviar agora". A per-video size ceiling applies; above it the app declines the upload and says why rather than failing silently.
- `stripMediaForSync` keeps doing its job — `file://` URIs must never reach the cloud. The order inverts: upload the file first, then sync the media item carrying the storage path.
- Deleting a session deletes its objects.

## Component 5 — App: joining

On the account screen: **Entrar em um grupo** → code field → a confirmation sheet that states, in words, what becomes readable (rounds, techniques, ELO, videos) and what does not (reflection, round notes, photos) → `join_group(code)`. Once joined, the screen shows the group name and **Sair do grupo**.

- Guests cannot join. A group needs an account; the app offers the upgrade path it already has.
- Joining requires a connection. Offline, the app says so — it does not queue a join.
- i18n: en and pt-BR, parity enforced.
- Nothing about groups changes the offline-first contract. A student with no group, or no network, uses the app exactly as before.

## Component 6 — Interim professor path

Until piece C ships, an admin script in Analytics creates a group, mints an invite code, and prints the roster. It reuses the existing admin tooling rather than adding a service.

## Testing

- **App (jest).** The join service (valid code, expired code, already a member, offline); the confirmation copy naming both what is shared and what is not; the upload queue (enqueue on save, drain on connection, skip over the size ceiling, never block the UI); `stripMediaForSync` still removes local URIs after the upload path lands; i18n parity.
- **Analytics (pytest).** Table shape round-trips through `db/models.py`, as `tests/test_db.py` already does for other tables.
- **RLS — by real query, not by green suite.** This repo's pytest runs against SQLite in memory and does not execute Postgres policies (the scope note in `0017_user_sessions.py` says so). The policies are therefore verified by running, against a real database, a checklist that must be recorded in the PR: a student cannot select another student's sessions; a professor selecting `group_member_sessions` gets rows with no `reflection` and no round `notes`; a professor has no select on `user_sessions`; a former member's professor loses both rows and video objects after the student leaves; an invalid code and a valid code for a group that exists return the same error shape.
- **Adoption (`0023`) is verified twice, before and after.** Before: the `pg_policies` / `relrowsecurity` / `alembic_version` diff that decides what `0023` must contain. After: the same queries again, plus the Supabase advisor (0 ERROR expected, and any new WARN treated as a drift signal — the advisor is what caught the 2026-06-25 incident). The union of `alembic/versions/*.py` must then account for every live policy, with no second file left to consult.

## Apply order

`0023` and `0024` are applied by a human, in order, and the drift check runs between them — adopting and then immediately adding new policies in one pass would make an adoption mistake indistinguishable from a B mistake.

1. Run the drift-check queries. Reconcile. Write `0023` from what is actually live.
2. Apply `0023`. Re-run the queries + advisor. Delete `db/auth_setup.sql` in the same PR.
3. Apply `0024`. Run the RLS checklist above.
4. The App PR (join + video upload) lands after `0024` is live, since it calls `join_group` and writes to the bucket.

## Out of Scope

- Any professor UI. That is piece C.
- Gym→class hierarchy, attendance, scheduling, billing.
- Aggregate class graph (the merged "what the gym trains" view) — considered and deferred; the member list with drill-down is the first cut.
- Photo sharing.
- Setting up a local/preview Postgres so RLS could be tested automatically. It stays a known gap; B verifies policies by recorded real queries instead.
