"""scripts/video_jobs.py -- SQLite in-memory DB, Storage/Gemini/ffmpeg fully stubbed.

No network, no real video, no real DB. Mirrors tests/test_db.py's SQLite-compat fixture.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scripts.video_jobs as video_jobs
from db.models import Profile, SessionVideoAnalysis, SessionVideoJob


@pytest.fixture()
def engine():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 -- registers all ORM models with Base.metadata
    from db.base import Base

    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, checkfirst=True)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


def _make_job(session: Session, *, owner_id: str | None = None) -> SessionVideoJob:
    owner_id = owner_id or str(uuid.uuid4())
    session.add(Profile(id=owner_id, full_name="Test Owner"))
    job = SessionVideoJob(
        owner_id=owner_id,
        session_id="sess-1",
        round_id="round-1",
        media_id="media-1",
        storage_path=f"{owner_id}/sess-1/media-1.mp4",
        round_kind="round",
        context={"kit": "black rashguard", "notes": "hard roll"},
    )
    session.add(job)
    session.commit()
    return job


def _stub_pipeline(monkeypatch, *, gemini_error: Exception | None = None):
    """Patches every I/O seam process_job touches: Storage download/upload, video_frames,
    gemini_read_frames, ffmpeg. Returns the recorded downloads/uploads for assertions."""
    downloads: list[str] = []
    uploads: list[str] = []

    def fake_download(bucket: str, path: str, dest) -> None:
        downloads.append(f"{bucket}/{path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-bytes")

    def fake_upload(bucket: str, path: str, data: bytes, content_type: str) -> None:
        uploads.append(f"{bucket}/{path}")

    def fake_video_frames_process(video_path, out_dir, *, context=None, **_kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "sheets").mkdir(parents=True, exist_ok=True)
        (out_dir / "sheets" / f"{out_dir.name}.pdf").write_bytes(b"%PDF-fake")
        (out_dir / "motion.json").write_text(
            json.dumps({"records": [{"t": 30.0, "diff_raw": 1.0}]}), encoding="utf-8"
        )
        return {"n_frames": 6, "camera_moving": False, "method": "static_scene_windows"}

    def fake_read_frames(sheets, prompt, model, thinking):
        if gemini_error is not None:
            raise gemini_error
        answer = {
            "bout": {"round_kind": "round"},
            "events": [
                {"ts": 5, "type": "takedown", "label": "Double Leg Takedown", "actor": "you",
                 "successful": True, "confidence": "high"},
                {"ts": 40, "type": "submission", "label": "Armbar", "actor": "you",
                 "successful": True, "confidence": "high"},
            ],
            "resets": [],
        }
        return answer, object()

    def fake_cut_clip(video_path, start, end, dest) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"clip-bytes")

    monkeypatch.setattr(video_jobs, "download_storage_object", fake_download)
    monkeypatch.setattr(video_jobs, "upload_storage_object", fake_upload)
    monkeypatch.setattr(video_jobs.video_frames, "process", fake_video_frames_process)
    monkeypatch.setattr(video_jobs.gemini_read_frames, "read_frames", fake_read_frames)
    monkeypatch.setattr(video_jobs.gemini_read_frames, "load_prompt", lambda path: "prompt")
    monkeypatch.setattr(video_jobs, "_cut_clip", fake_cut_clip)
    return downloads, uploads


# ── claim_jobs ───────────────────────────────────────────────────────────────────────────────
def test_claim_jobs_marks_processing_and_increments_attempts(session):
    job = _make_job(session)
    claimed = video_jobs.claim_jobs(session, limit=10)
    assert [j.id for j in claimed] == [job.id]
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1


def test_claim_jobs_ignores_non_queued(session):
    _make_job(session)
    video_jobs.claim_jobs(session, limit=10)  # first claim flips it to processing
    assert video_jobs.claim_jobs(session, limit=10) == []


# ── process_job: done path ──────────────────────────────────────────────────────────────────
def test_process_job_happy_path_writes_analysis_and_marks_done(session, monkeypatch):
    job = _make_job(session)
    downloads, uploads = _stub_pipeline(monkeypatch)
    claimed = video_jobs.claim_jobs(session, limit=10)[0]

    video_jobs.process_job(claimed, session)

    refreshed = session.get(SessionVideoJob, job.id)
    assert refreshed.status == "done"
    assert refreshed.error is None

    analysis = session.get(SessionVideoAnalysis, job.id)
    assert analysis is not None
    assert analysis.owner_id == job.owner_id  # write never crosses owners
    assert analysis.session_id == job.session_id
    assert len(analysis.events) == 2
    assert analysis.difficulty_derived is not None
    assert 0.0 <= float(analysis.difficulty_derived) <= 10.0
    assert analysis.pdf_path == f"{job.owner_id}/analysis/{job.id}/sheet.pdf"
    assert len(analysis.clip_paths) >= 1

    assert any(job.storage_path in d for d in downloads)
    assert any(analysis.pdf_path in u for u in uploads)


def test_process_job_cleans_up_its_working_directory(session, monkeypatch):
    _make_job(session)
    _stub_pipeline(monkeypatch)
    claimed = video_jobs.claim_jobs(session, limit=10)[0]

    seen_dest = {}

    def fake_download(bucket, path, dest):
        seen_dest["path"] = dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    monkeypatch.setattr(video_jobs, "download_storage_object", fake_download)
    video_jobs.process_job(claimed, session)

    assert "path" in seen_dest
    assert not seen_dest["path"].parent.exists()  # TemporaryDirectory gone (D11)


# ── process_job: failed path ────────────────────────────────────────────────────────────────
def test_process_job_failure_marks_job_failed_with_error(session, monkeypatch):
    job = _make_job(session)
    _stub_pipeline(monkeypatch, gemini_error=RuntimeError("gemini exploded"))
    claimed = video_jobs.claim_jobs(session, limit=10)[0]

    with pytest.raises(RuntimeError):
        video_jobs.process_job(claimed, session)

    # run_batch is what turns the raise into a failed row; exercise that path here.
    video_jobs._mark_failed(session, claimed, "RuntimeError: gemini exploded")

    refreshed = session.get(SessionVideoJob, job.id)
    assert refreshed.status == "failed"
    assert "gemini exploded" in refreshed.error
    assert session.get(SessionVideoAnalysis, job.id) is None


@contextmanager
def _session_ctx(session):
    yield session


def test_run_batch_marks_failed_without_crashing(session, monkeypatch):
    _make_job(session)
    _stub_pipeline(monkeypatch, gemini_error=RuntimeError("gemini exploded"))
    monkeypatch.setattr(video_jobs, "db_session", lambda: _session_ctx(session))

    video_jobs.run_batch(limit=10, job_id=None, dry_run=False, keep_frames=False)

    row = session.query(SessionVideoJob).one()
    assert row.status == "failed"
    assert "gemini exploded" in row.error


# ── retry_jobs ───────────────────────────────────────────────────────────────────────────────
def test_retry_jobs_requeues_only_failed(session):
    job = _make_job(session)
    job.status = "failed"
    session.commit()
    other = SessionVideoJob(
        owner_id=job.owner_id, session_id="sess-2", media_id="media-2",
        storage_path=f"{job.owner_id}/sess-2/media-2.mp4", status="done",
    )
    session.add(other)
    session.commit()

    n = video_jobs.retry_jobs(session, all_failed=True)

    assert n == 1
    assert session.get(SessionVideoJob, job.id).status == "queued"
    assert session.get(SessionVideoJob, other.id).status == "done"


def test_retry_jobs_needs_a_target():
    with pytest.raises(ValueError):
        video_jobs.retry_jobs(object(), job_id=None, all_failed=False)  # type: ignore[arg-type]
