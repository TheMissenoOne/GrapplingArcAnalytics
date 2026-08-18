# `delete-account` — the trusted side of account deletion

## What it deletes, and in what order

| # | Stage | Why here |
|---|---|---|
| 1 | Every object under `session-videos/{uid}/` | A database `CASCADE` never touches Storage, and once the auth row is gone there is no owner left to attribute the orphans to. |
| 2 | `graphs where owner_kind='user' and owner_id = uid` | `graphs.owner_id` is **polymorphic** — an athlete id or a profile id depending on `owner_kind` — so a column foreign key is impossible and there is no cascade. Alembic 0023 already covers this with a `before delete on auth.users` trigger (`handle_user_delete`), which is live in production. This stage stays because that trigger fires on the auth delete, i.e. stage 3: if stage 3 fails the trigger never runs, and the graph would sit there after a deletion the caller was told had failed. Idempotent against the trigger. |
| 3 | `auth.admin.deleteUser(uid)` | Last. Cascades `profiles`, which cascades `user_sessions`, `user_projects`, `user_node_names`, `user_sync_meta`, `groups`, `group_members`, `group_invites`, `class_sessions`, `user_performance_snapshots`. |

Every stage is idempotent, so a retry after a network drop finishes the job instead of erroring on the parts that already succeeded.

**A partial deletion is reported as a failure** (HTTP 500 with `failedStage`). Telling someone their data is gone when it is not is worse than telling them the deletion failed, so the app must never claim success on anything but a 200.

## What it deliberately does not delete

- **Google Drive backups.** They live in the user's own Drive account and we have no authority over them. The privacy policy and the deletion page both say so; the app's confirmation screen repeats it.
- **`technique_nodes`.** Shared, curated, public vocabulary. Since alembic 0037 the user's own labels are in `graph_nodes` and go with the graph in stage 2.
- **Published athlete data.** Not the user's to delete.

## A consequence worth surfacing in the UI

Deleting the account of someone who **owns a group** cascades that group, its memberships, its invites and its class sessions. A professor deleting their account deletes their gym. That is correct — the group belongs to them — but it must not be a surprise.

## Authority

There is no `userId` parameter and no request field is read at all, so there is nothing a caller could send that would make this delete someone else's account. The uuid comes from `auth.getUser()` on the caller's own token. `verify_jwt = true` makes the gateway reject an unauthenticated call before the function runs; the function verifies again anyway, because "the gateway checked it" is a configuration fact and configuration drifts.

The service-role key is used only after that identity is established, and only with the uuid it produced.

**No server-side recent-authentication requirement** — see the `ponytail:` note in `index.ts` for the reasoning and the upgrade path.

## Tests

```bash
deno test supabase/functions/delete-account/
```

Pure-logic, no permissions, no network: the ordering and the failure semantics are the part that is easy to get wrong, so the effects are injected. Run in CI by the `edge-functions` job.

## Deploy

```bash
supabase functions deploy delete-account
```

`SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` are provided by the platform. The function refuses to run (HTTP 500, before touching anything) if any is missing.
