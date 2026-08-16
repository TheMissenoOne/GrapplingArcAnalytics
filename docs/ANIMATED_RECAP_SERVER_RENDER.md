# Server-rendered animated recap — design note

**Status: idea, not built.** Nothing in this repo implements it. This exists so the decision and its
constraints are on paper rather than rediscovered.

## Why this is a note and not code

The App ships an animated recap **on the device** (`GrapplingArcApp`,
`src/screens/recap/RecapPlayer.tsx`): a story-style sequence of beats built with Reanimated, playing
offline with no backend. It plays; it does not produce a file.

A rendered *video* is a different product — something you can post — and it cannot run on the phone.
ManimGL is Python, needs an OpenGL context and shells out to FFmpeg to encode. That is a server, and
a server for this means a queue, a worker image, private object storage, signed URLs, an idempotency
key and a retry story, before anyone sees one frame. The static and on-device recaps deliver the
value without any of it, so this is deliberately deferred rather than half-built.

## What it would have to be

### Data — already solved, do not duplicate

`RecapData` is computed in the App (`src/services/recap/recapData.ts`) as a pure function with an
explicit rule: **nothing is fabricated** — a metric with no data behind it is absent, not zero. A
renderer must consume that record, never recompute it from the database. Two implementations of "what
happened last week" is how a video ends up disagreeing with the screen the athlete is looking at.

That means the App uploads the `RecapData` it already built. The worker renders what it is given.

### Storage — private, owner-scoped

Copy `alembic/versions/0030_user_projects.py` verbatim as the shape: `owner_id UUID` FK to
`profiles` with `ON DELETE CASCADE`, RLS enabled in the same revision, `revoke all from anon,
authenticated` then `grant` to `authenticated`, and one `for all to authenticated using (owner_id =
auth.uid()) with check (owner_id = auth.uid())` policy.

```
user_recaps
  id             uuid pk default gen_random_uuid()
  owner_id       uuid not null → profiles(id) on delete cascade
  period_type    text check (period_type in ('week','month'))
  period_start   date not null
  period_end     date not null
  timezone       text not null          -- the period was computed in LOCAL time; store which
  data           jsonb not null         -- the RecapData the App built
  data_hash      text not null          -- sha1 of the canonical JSON, per export/incremental.py
  render_version int  not null
  status         text check (status in ('queued','rendering','ready','failed'))
  storage_path   text
  duration_sec   numeric
  error_code     text
  created_at / updated_at  timestamptz default now()

  unique (owner_id, period_type, period_start, data_hash, render_version)
```

That unique constraint IS the idempotency: the same period, unchanged, at the same renderer version
never renders twice. `data_hash` follows `export/incremental.py:item_hash` — sha1 over
`json.dumps(sort_keys=True)` — and its correctness contract applies here too: the hash must cover
every input that affects the output, or a stale video is served for changed data.

A recap is **private user data** under the workspace rule in `CLAUDE.md`. It is derived entirely from
one athlete's own sessions and is delivered back to that athlete: allowed, and only that. It must
never inform a centroid, a ranking, the public site, or another user's anything. A sibling case in
`tests/test_private_data_boundary.py` should assert it.

Media goes to a private bucket keyed by owner — the App already writes
`session-videos/{owner_id}/…` (`GrapplingArcApp/src/services/videoUploadQueue.ts:140`), so follow
that path convention. **No permanent public URL**: the App asks for a signed one. Note that no
Python-side Storage client exists in this repo yet; that would be new.

### Worker

The closest precedent is `jobs/publish_pro_analytics.py` — a periodic, per-user, private-artifact
producer with `period_bounds()` refusing partial periods, a systemd timer, a `workflow_dispatch`
workflow defaulting to `dry_run: true`, and tests that cover upsert-same-period and
continue-after-one-user-failure. A render worker is the same shape with a longer job.

New in it: a `render` extra in `pyproject.toml` beside `cv` (manimgl pulls a heavy native/GL stack —
it does not belong in base, and CI only runs `uv sync --extra postgres`), a container with FFmpeg
and a GL context, and encoding. The only FFmpeg precedent here is
`poc/decision_vision/frame_stream.py`, which shells out with `shutil.which` preflight and a timeout
to *extract* one frame — the idiom to copy, not the capability.

Output: vertical 9:16 as the primary profile, optionally 1:1 for square shares. Fixed profiles, never
per-device resolutions.

### Failure

A failed render must never make the recap unavailable. `status='failed'` means the App shows the
static recap plus "animated recap unavailable", with retry where it makes sense — which is already
true today, since the static recap does not depend on this pipeline existing.

## The order to build it in, if it is ever built

1. `user_recaps` + RLS + the private-data boundary test. Nothing renders yet.
2. The App uploads `RecapData` on demand and polls `status`.
3. A CLI that renders one recap from a fixture to a local file — no queue, no storage. This is where
   the scenes get designed, and where "is this worth watching twice" gets answered before any
   infrastructure exists.
4. Only then: worker, bucket, signed URLs, scheduling.

Steps 1–3 are reversible. Step 4 is the one that costs money every month.
