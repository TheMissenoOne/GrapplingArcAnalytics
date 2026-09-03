#!/usr/bin/env python
"""Batch worker for the video-pro pipeline: claim `session_video_jobs`, produce one
`session_video_analysis` row each, run by the dono manually (no cron, no server).

    uv run python -m scripts.video_jobs list
    uv run python -m scripts.video_jobs process --limit 10 [--job <uuid>] [--dry-run] [--keep-frames]
    uv run python -m scripts.video_jobs retry [--job <uuid> | --all-failed]

Fase 3 of the video-pro plan. Per job: claim (service-role `DATABASE_URL`, `for update skip
locked` on Postgres) -> download the round video from the `session-videos` bucket -> reuse
`scripts.video_frames.process` for motion segmentation + a frame sheet, drawing a context page
(kit/notes/round_kind + the owner's opt-in reference selfie from `user-media`, if
`profiles.face_consent_at` is set) -> `scripts.gemini_read_frames.read_frames` with
`docs/PROMPT_gemini_round_reading.md` (actors `you`/`partner`, `resets[]` for sequence
boundaries) -> `analysis.round_analysis` derives sequences/difficulty/highlights, purely, no
I/O -> `ffmpeg` cuts up to `MAX_CLIPS` highlight clips -> sheet + clips uploaded to
`user-media/{owner_id}/analysis/{job_id}/...` -> `session_video_analysis` written, job marked
`done`. Any exception along the way marks the job `failed` (`error`, capped) instead of
crashing the batch — the next job still gets a chance.

**Privacy (root `CLAUDE.md` / this repo's `CLAUDE.md`, "Public vs Private Data" — read before
touching this file).** Every row this script reads or writes is PRIVATE, owner-keyed, user-fed
footage. Purpose is exactly one thing: giving the SAME owner back their own round as
sequences/difficulty/highlights. Nothing here may ever write to `data/finetune`, a CV/vision
dataset, the public athlete corpus, an archetype centroid, an athlete's ELO, or the `site/`
export — there is no code path to any of those from this module, and none should be added.
Every DB query below is already scoped to one job's `owner_id`; the claim query itself never
crosses owners. Frames, the downloaded video and the selfie live only inside a
`TemporaryDirectory` for the run's own duration (D11) — the only durable artefacts are the
`session_video_analysis` row and the objects this script itself uploads.

**"Signed" download/upload, ponytail-simplified.** The service-role key already has full
Storage read/write (it bypasses RLS the same way `db.base.db_session`'s `DATABASE_URL` does),
so this talks to `/storage/v1/object/{bucket}/{path}` directly with `Authorization: Bearer
{SUPABASE_SECRET_KEY}` rather than minting a short-lived signed URL first — one hop, not two,
for a worker that already holds the secret. Swap in `/storage/v1/object/sign/...` if this ever
runs somewhere that must NOT hold the secret key directly.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.round_analysis import (  # noqa: E402
    build_highlights,
    build_sequences,
    derive_difficulty,
    difficulty_components,
)
from db.base import db_session  # noqa: E402
from db.models import Profile, SessionVideoAnalysis, SessionVideoJob  # noqa: E402
from scripts import gemini_read_frames, video_frames  # noqa: E402

logger = logging.getLogger("video_jobs")

SESSION_VIDEO_BUCKET = "session-videos"
USER_MEDIA_BUCKET = "user-media"
ROUND_PROMPT_PATH = REPO / "docs" / "PROMPT_gemini_round_reading.md"
DEFAULT_MODEL = gemini_read_frames.DEFAULT_MODEL
DEFAULT_THINKING = "high"
#: Highest-scoring highlights get a cut clip; the rest still ship in `highlights` (timestamps
#: only) so the App can show them without every one costing an ffmpeg pass + an upload.
MAX_CLIPS = 3


# ── Storage (httpx, service role) ───────────────────────────────────────────────────────────
def _storage_env() -> tuple[str, str]:
    return os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SECRET_KEY"]


def download_storage_object(bucket: str, path: str, dest: Path) -> None:
    url, key = _storage_env()
    resp = httpx.get(
        f"{url}/storage/v1/object/{bucket}/{path}",
        headers={"Authorization": f"Bearer {key}", "apikey": key},
        timeout=120.0,
    )
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)


def upload_storage_object(bucket: str, path: str, data: bytes, content_type: str) -> None:
    url, key = _storage_env()
    resp = httpx.post(
        f"{url}/storage/v1/object/{bucket}/{path}",
        headers={
            "Authorization": f"Bearer {key}",
            "apikey": key,
            "Content-Type": content_type,
            "x-upsert": "true",
        },
        content=data,
        timeout=120.0,
    )
    resp.raise_for_status()


# ── Queue ────────────────────────────────────────────────────────────────────────────────────
def claim_jobs(session: Session, limit: int, job_id: str | None = None) -> list[SessionVideoJob]:
    """Atomically flip up to `limit` `queued` jobs to `processing` (`attempts += 1`) and return
    them. `for update skip locked` only on Postgres (the dialect that supports it) — this repo's
    tests run SQLite in-memory, a single-connection environment where the clause has no
    counterpart and no purpose; a real batch run is a single `uv run` invocation too, so this is
    not a concurrent-worker guarantee today, only the shape one would need later.

    Filters go through mapped columns (`select`/`update`, not raw SQL with a bare string bind)
    so `id` gets the column's own type coercion on every dialect — a raw `:id` string param
    bypasses that and silently mismatches under this repo's SQLite test shim, whose `UUID`
    override strips hyphens on write but not on a plain bound string.
    """
    stmt = select(SessionVideoJob.id).where(SessionVideoJob.status == "queued")
    if job_id:
        stmt = stmt.where(SessionVideoJob.id == job_id)
    stmt = stmt.order_by(SessionVideoJob.created_at).limit(limit)
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    ids = [row[0] for row in session.execute(stmt)]
    if not ids:
        return []
    session.execute(
        update(SessionVideoJob)
        .where(SessionVideoJob.id.in_(ids))
        .values(status="processing", attempts=SessionVideoJob.attempts + 1, updated_at=datetime.now(UTC))
    )
    session.commit()
    return (
        session.query(SessionVideoJob)
        .filter(SessionVideoJob.id.in_(ids))
        .order_by(SessionVideoJob.created_at)
        .all()
    )


def list_jobs(session: Session, limit: int = 50) -> list[SessionVideoJob]:
    return (
        session.query(SessionVideoJob).order_by(SessionVideoJob.created_at.desc()).limit(limit).all()
    )


def retry_jobs(session: Session, job_id: str | None = None, all_failed: bool = False) -> int:
    if not job_id and not all_failed:
        raise ValueError("retry needs --job <uuid> or --all-failed")
    stmt = update(SessionVideoJob).where(SessionVideoJob.status == "failed")
    if job_id:
        stmt = stmt.where(SessionVideoJob.id == job_id)
    stmt = stmt.values(status="queued", updated_at=datetime.now(UTC))
    result = session.execute(stmt)
    session.commit()
    # ponytail: mypy's Session.execute overloads don't narrow to CursorResult through the
    # fluent Update chain above; rowcount exists at runtime on every UPDATE result.
    return getattr(result, "rowcount", 0) or 0


def _mark_failed(session: Session, job: SessionVideoJob, error: str) -> None:
    session.execute(
        update(SessionVideoJob)
        .where(SessionVideoJob.id == job.id)
        .values(status="failed", error=error[:500], updated_at=datetime.now(UTC))
    )
    session.commit()


def _mark_done(session: Session, job: SessionVideoJob) -> None:
    session.execute(
        update(SessionVideoJob)
        .where(SessionVideoJob.id == job.id)
        .values(status="done", error=None, updated_at=datetime.now(UTC))
    )
    session.commit()


# ── Per-job pipeline ─────────────────────────────────────────────────────────────────────────
@contextmanager
def _workdir(job_id: str, keep: bool) -> Iterator[Path]:
    """The run's own scratch space. Default: a `TemporaryDirectory` gone the instant this
    returns (D11 — no video/frame ever survives a run). `--keep-frames` is a local-debugging
    escape hatch ONLY — it keeps the same private machine's disk populated under
    `data/video_jobs_debug/`, never uploads or shares it; it does not relax D11 for anything
    that leaves this machine."""
    if keep:
        d = REPO / "data" / "video_jobs_debug" / job_id
        d.mkdir(parents=True, exist_ok=True)
        yield d
    else:
        with TemporaryDirectory(prefix=f"video_job_{job_id}_") as tmp:
            yield Path(tmp)


def _cut_clip(video_path: Path, start: float, end: float, dest: Path) -> None:
    """Same ffmpeg seek pattern as `scripts/frame_registrar.py:still` — `-ss` before `-i` for
    the fast keyframe seek, `-to` (not `-t`) because with input-side seeking ffmpeg reads `-to`
    against the ORIGINAL, untrimmed timeline too, matching `start`/`end` being video-absolute."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-ss", f"{start}", "-to", f"{end}", "-i", str(video_path),
            "-c", "copy", "-y", str(dest),
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(
            f"ffmpeg could not cut clip [{start},{end}]: {(r.stderr or '').strip()[-200:]}"
        )


def _majority_confidence(events: list[dict[str, Any]]) -> str | None:
    if not events:
        return None
    labels = [str(e.get("confidence", "")).lower() for e in events]
    return "high" if labels.count("high") * 2 >= len(labels) else "low"


def process_job(
    job: SessionVideoJob,
    session: Session,
    *,
    keep_frames: bool = False,
    prompt_path: Path = ROUND_PROMPT_PATH,
    model: str = DEFAULT_MODEL,
    thinking: str = DEFAULT_THINKING,
) -> None:
    """Download -> segment/frame -> read -> derive -> upload -> persist, for ONE job. Raises on
    any failure — `run_batch` is what turns that into a `failed` row; this function never
    catches, so a stub-injected failure in a test surfaces exactly like a real one."""
    with _workdir(job.id, keep_frames) as tmp:
        video_path = tmp / "video.mp4"
        download_storage_object(SESSION_VIDEO_BUCKET, job.storage_path, video_path)

        context: dict[str, Any] = dict(job.context or {})
        context["round_kind"] = job.round_kind
        profile = session.get(Profile, job.owner_id)
        if profile is not None and profile.face_consent_at is not None and profile.face_ref_path:
            selfie_path = tmp / "face.jpg"
            try:
                download_storage_object(USER_MEDIA_BUCKET, profile.face_ref_path, selfie_path)
                context["selfie_path"] = str(selfie_path)
            except httpx.HTTPStatusError:
                logger.warning(
                    "job %s: could not fetch selfie %s, continuing without it",
                    job.id, profile.face_ref_path,
                )

        out_dir = tmp / "analysis"
        decision = video_frames.process(video_path, out_dir, context=context)
        sheet_path = out_dir / "sheets" / f"{out_dir.name}.pdf"
        if not sheet_path.exists():
            raise RuntimeError(
                f"job {job.id}: no sheet produced ({decision.get('n_frames', 0)} frames)"
            )
        motion_doc = json.loads((out_dir / "motion.json").read_text(encoding="utf-8"))
        motion_doc.pop("video", None)  # worker temp path — never persist it

        prompt = gemini_read_frames.load_prompt(prompt_path)
        answer, _raw = gemini_read_frames.read_frames([sheet_path], prompt, model, thinking)
        events: list[dict[str, Any]] = list(answer.get("events") or [])
        resets: list[float] = list(answer.get("resets") or [])

        id_to_idx = {id(e): i for i, e in enumerate(events)}
        sequences = [
            {
                "sequenceId": i,
                "startTs": seq[0].get("ts") if seq else None,
                "endTs": seq[-1].get("ts") if seq else None,
                "eventIdx": [id_to_idx[id(ev)] for ev in seq],
            }
            for i, seq in enumerate(build_sequences(events, resets))
        ]
        difficulty = derive_difficulty(events, motion_doc)
        inputs = difficulty_components(events, motion_doc)
        highlights = build_highlights(events, motion_doc, k=5)

        clip_paths: list[str] = []
        for i, hl in enumerate(highlights[:MAX_CLIPS]):
            clip_local = tmp / f"clip_{i}.mp4"
            _cut_clip(video_path, hl["start"], hl["end"], clip_local)
            remote_path = f"{job.owner_id}/analysis/{job.id}/clip_{i}.mp4"
            upload_storage_object(USER_MEDIA_BUCKET, remote_path, clip_local.read_bytes(), "video/mp4")
            clip_paths.append(remote_path)

        pdf_remote_path = f"{job.owner_id}/analysis/{job.id}/sheet.pdf"
        upload_storage_object(
            USER_MEDIA_BUCKET, pdf_remote_path, sheet_path.read_bytes(), "application/pdf"
        )

        row = session.get(SessionVideoAnalysis, job.id)
        if row is None:
            row = SessionVideoAnalysis(job_id=job.id, owner_id=job.owner_id)
            session.add(row)
        row.session_id = job.session_id
        row.round_id = job.round_id
        row.motion = decision
        row.events = events
        row.sequences = sequences
        row.difficulty_derived = round(difficulty, 1)
        row.difficulty_inputs = inputs
        row.confidence = _majority_confidence(events)
        row.highlights = highlights
        row.pdf_path = pdf_remote_path
        row.clip_paths = clip_paths
        row.generated_at = datetime.now(UTC)
        session.commit()

    _mark_done(session, job)


def run_batch(limit: int, job_id: str | None, dry_run: bool, keep_frames: bool) -> None:
    if dry_run:
        with db_session() as session:
            candidates = [j for j in list_jobs(session, limit=limit) if j.status == "queued"]
        if job_id:
            candidates = [j for j in candidates if j.id == job_id]
        for j in candidates:
            logger.info(
                "[dry-run] would claim+process %s (owner=%s session=%s round=%s)",
                j.id, j.owner_id, j.session_id, j.round_id,
            )
        logger.info("[dry-run] %d candidate(s)", len(candidates))
        return

    with db_session() as session:
        jobs = claim_jobs(session, limit, job_id)
    if not jobs:
        logger.info("no queued jobs to claim")
        return
    logger.info("claimed %d job(s)", len(jobs))

    for claimed in jobs:
        with db_session() as session:
            job = session.get(SessionVideoJob, claimed.id)
            if job is None:
                continue
            try:
                process_job(job, session, keep_frames=keep_frames)
                logger.info("job %s done", job.id)
            except Exception as exc:  # noqa: BLE001 -- one bad job must not stop the batch
                logger.exception("job %s failed", job.id)
                _mark_failed(session, job, f"{type(exc).__name__}: {exc}")


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show recent jobs")

    p_process = sub.add_parser("process", help="claim and process queued jobs")
    p_process.add_argument("--limit", type=int, default=10)
    p_process.add_argument("--job", help="only this job id")
    p_process.add_argument("--dry-run", action="store_true")
    p_process.add_argument(
        "--keep-frames", action="store_true",
        help="keep the working directory under data/video_jobs_debug/<job-id> instead of "
             "deleting it (local debugging only — see _workdir's own docstring, D11)",
    )

    p_retry = sub.add_parser("retry", help="requeue failed jobs")
    p_retry.add_argument("--job", help="requeue this job id")
    p_retry.add_argument("--all-failed", action="store_true")

    a = ap.parse_args()

    if a.cmd == "list":
        with db_session() as session:
            for j in list_jobs(session):
                print(
                    f"{j.id}  {j.status:<10} attempts={j.attempts} owner={j.owner_id} "
                    f"session={j.session_id} round={j.round_id or '-'} kind={j.round_kind} "
                    f"created={j.created_at}"
                )
        return 0

    if a.cmd == "retry":
        with db_session() as session:
            n = retry_jobs(session, job_id=a.job, all_failed=a.all_failed)
        logger.info("requeued %d job(s)", n)
        return 0

    run_batch(a.limit, a.job, a.dry_run, a.keep_frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
