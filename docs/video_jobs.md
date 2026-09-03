# Video-pro batch worker (`scripts/video_jobs.py`)

Fase 3 of the video-pro plan (App upload -> `session_video_jobs` -> this worker ->
`session_video_analysis` -> App review). Run by the dono, by hand, on his own machine — no
cron, no server, no public endpoint. Reuses `scripts/video_frames.py` (segmentation + sheet)
and `scripts/gemini_read_frames.py` (the model call) rather than re-implementing either.

    uv run python -m scripts.video_jobs list
    uv run python -m scripts.video_jobs process --limit 10 [--job <uuid>] [--dry-run] [--keep-frames]
    uv run python -m scripts.video_jobs retry [--job <uuid> | --all-failed]

## Privacy (read this before running against prod)

Every row this script touches is **PRIVATE** — user-fed training footage (root `CLAUDE.md` /
this repo's `CLAUDE.md`, "Public vs Private Data"). The whole pipeline exists for exactly one
purpose: handing ONE owner back their OWN round as sequences/difficulty/highlights. Nothing
here may ever reach `data/finetune`, a CV/vision dataset, the public athlete corpus, an
archetype centroid, an athlete's ELO, or the `site/` export — there is no code path from this
module to any of those, and none should be added. Frames, the downloaded video and the
selfie live only inside a `TemporaryDirectory` for the run's own duration; by the time
`process_job` returns, the only durable artefacts are the `session_video_analysis` row and the
objects the run itself uploaded to `user-media/{owner_id}/analysis/{job_id}/...`.

## Flow, per job

1. **Claim.** `select ... where status = 'queued' ... for update skip locked` (Postgres only —
   the clause is skipped on SQLite, which this repo's tests run against and which has no
   locking story of its own), flips to `processing`, `attempts += 1`.
2. **Download.** The round video, from `session-videos/{storage_path}` (unchanged bucket/path,
   D1 of the plan). The owner's reference selfie too, from `user-media/{face_ref_path}`, but
   **only** when `profiles.face_consent_at is not null` — a missing selfie or missing consent
   is a normal state, not an error; the reader just falls back to the text
   `identity_discriminator` (kit colour, etc).
3. **Segment + sheet.** `scripts.video_frames.process(video, out_dir, context=...)` — the same
   camera-moving/static-scene decision the standalone script uses, plus a `context` page (kit,
   notes, `round_kind`, the selfie thumbnail if present).
4. **Read.** `scripts.gemini_read_frames.read_frames([sheet], prompt, model, thinking)` with
   `docs/PROMPT_gemini_round_reading.md` — actors are `you`/`partner`, never a name; the answer
   carries `events[]` and a separate `resets[]` (video-absolute seconds where the pair fully
   separated and restarted).
5. **Derive** (`analysis/round_analysis.py`, pure, no I/O):
   - `build_sequences(events, resets)` — one group per `sequenceId`.
   - `derive_difficulty(events, motion)` / `difficulty_components(events, motion)` — see below.
   - `build_highlights(events, motion, k=5)` — ranked clip windows.
6. **Clip + upload.** Up to `MAX_CLIPS` (3) top highlights get cut with `ffmpeg -ss <start> -to
   <end> -i video -c copy` (same seek pattern as `scripts/frame_registrar.py:still`) and
   uploaded to `user-media/{owner_id}/analysis/{job_id}/clip_{i}.mp4`; the sheet PDF goes to
   `.../sheet.pdf`.
7. **Persist + mark done.** One `session_video_analysis` row (`job_id` PK, `owner_id` copied
   straight from the job — write never crosses owners), job flipped to `done`.

Any exception in steps 2-7 marks the job `failed` (`error`, capped to 500 chars, `attempts`
already incremented at claim time) and `run_batch` moves on to the next job — one bad job
never stops a batch.

## The difficulty rule (transparent, NOT calibrated)

`analysis/round_analysis.py:derive_difficulty` — 0 (you dominated) .. 10 (partner dominated
and finished you), 5 is even:

```
control_share_you = fraction of round time the most recent STATE event (guard/control, via
                     analysis.taxonomy_kind.kind_of_entry) belongs to "you"
sub_for/against    = successful "submission" events, by actor
pos_for/against    = successful sweep|pass|takedown events, by actor

difficulty = clamp(0, 10,
      5.0
    + 3.0 * (1 - 2 * control_share_you)
    + 1.5 * (sub_against - sub_for)
    + 0.5 * (pos_against - pos_for))
```

**The four coefficients (5.0 / 3.0 / 1.5 / 0.5) are a first, transparent guess — not measured
against real rounds.** They live at the top of the one function that uses them
(`ponytail:` comment on `derive_difficulty`), and every intermediate term is stored back to
`session_video_analysis.difficulty_inputs` (via `difficulty_components`, same inputs, no
private video re-read needed) precisely so a future re-fit against user-confirmed manual
difficulty ratings never needs to touch a video again. See the video-pro plan's "Decisões
abertas #2" for the two proposed re-fit paths.

## Highlights

`build_highlights` scores each event as `successful (1.0/0.3) + confidence (high 1.0 / low
0.4) + normalised motion peak in [ts-2, ts+3] (from motion.json) + Markov action weight
(analysis/markov_weights.py, "global" block, mean-1 by construction)`, sorts descending, keeps
the top `k`. Each kept item is a clip window `{start, end, label, score}` — `start`/`end` are
exactly the ffmpeg cut boundaries (`[ts-3, ts+4]`, clamped to 0 at the low end).

## What's stubbed in `tests/test_video_jobs.py`

Storage (`download_storage_object`/`upload_storage_object`), `video_frames.process`,
`gemini_read_frames.read_frames`/`load_prompt`, and `_cut_clip` are all monkeypatched — no
network, no real video, no ffmpeg binary required to run the suite. DB is SQLite in-memory
(same fixture shape as `tests/test_db.py`). Both the `done` and `failed` flows are exercised,
plus that the working directory left over from a run does not survive it (D11) and that the
persisted `session_video_analysis.owner_id` always matches the job's own owner.

## Depends on 0058 being applied

`alembic/versions/0058_session_video_analysis.py` (already on disk and, per the coordinator,
applied to prod) is the schema contract this script codes against: `session_video_jobs` /
`session_video_analysis` / `profiles.face_ref_path` / `profiles.face_consent_at`. Column
names here were checked against that file directly, not assumed from the plan doc.
