"""scripts/round_audit.py's `benchmark` subcommand -- truth-source priority, resume-safety,
output files. No real DB/ffmpeg/Gemini; every I/O seam is stubbed, same convention as
tests/test_round_audit.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.round_audit as round_audit


def _write_video_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-video-bytes")


def _write_read_json(out_dir: Path, events: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "read.json").write_text(json.dumps({"events": events}), encoding="utf-8")


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]],
) -> tuple[Path, Path, str]:
    in_dir = tmp_path / "rounds"
    out_root = tmp_path / "out"
    monkeypatch.setattr(round_audit, "OUT_ROOT", out_root)
    video_path = in_dir / "IMG_0001.mov"
    _write_video_stub(video_path)
    slug = round_audit._slug(video_path)
    _write_read_json(out_root / slug, events)
    return in_dir, out_root, slug


def test_prefers_events_corrected_over_truth_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_dir, out_root, slug = _setup(
        tmp_path, monkeypatch, [{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True}]
    )
    (out_root / slug / "events_corrected.json").write_text(
        json.dumps([{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True,
                    "verdict": "correct"}]),
        encoding="utf-8",
    )
    other_truth = tmp_path / "other_truth.json"
    other_truth.write_text(json.dumps({"events": []}), encoding="utf-8")

    rc = round_audit.cmd_benchmark(in_dir, other_truth, 5.0, False, False, only=[slug])
    assert rc == 0
    doc = json.loads((out_root / slug / "benchmark.json").read_text())
    assert doc["truth_source"] == "events_corrected.json"
    assert doc["n_matched"] == 1
    assert (out_root / slug / "BENCHMARK.md").exists()
    assert (out_root / "BENCHMARK.md").exists()


def test_uses_truth_flag_when_no_events_corrected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_dir, out_root, slug = _setup(
        tmp_path, monkeypatch, [{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True}]
    )
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(
        json.dumps({"events": [{"ts": 11.0, "label": "armbar", "actor": "you", "successful": True}],
                   "resets": []}),
        encoding="utf-8",
    )

    rc = round_audit.cmd_benchmark(in_dir, truth_path, 5.0, False, False, only=[slug])
    assert rc == 0
    doc = json.loads((out_root / slug / "benchmark.json").read_text())
    assert doc["truth_source"] == str(truth_path)
    assert doc["n_matched"] == 1


def test_from_sessions_pulls_via_owner_truth_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_dir, out_root, slug = _setup(
        tmp_path, monkeypatch, [{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True}]
    )

    class _FakeSession:
        pass

    class _FakeCtx:
        def __enter__(self) -> _FakeSession:
            return _FakeSession()

        def __exit__(self, *a: object) -> None:
            return None

    import analysis.reference_owner as reference_owner
    import db.base as db_base
    import scripts.owner_truth_pull as owner_truth_pull

    monkeypatch.setattr(db_base, "db_session", lambda: _FakeCtx())
    monkeypatch.setattr(reference_owner, "reference_owner_ids", lambda session: ["owner-1"])
    monkeypatch.setattr(
        owner_truth_pull, "pull_owner_sessions",
        lambda owner_id: [{"rounds": []}] if owner_id == "owner-1" else [],
    )
    monkeypatch.setattr(
        owner_truth_pull, "find_truth_for_slug",
        lambda sessions_data, s: (
            {"events": [{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True}]}
            if s == slug else None
        ),
    )

    rc = round_audit.cmd_benchmark(in_dir, None, 5.0, True, False, only=[slug])
    assert rc == 0
    doc = json.loads((out_root / slug / "benchmark.json").read_text())
    assert doc["truth_source"].startswith("user_sessions")
    assert doc["n_matched"] == 1


def test_no_truth_source_skips_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_dir, out_root, slug = _setup(tmp_path, monkeypatch, [])
    rc = round_audit.cmd_benchmark(in_dir, None, 5.0, False, False, only=[slug])
    assert rc == 0
    assert not (out_root / slug / "benchmark.json").exists()
    assert not (out_root / "BENCHMARK.md").exists()


def test_missing_read_json_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_dir = tmp_path / "rounds"
    out_root = tmp_path / "out"
    monkeypatch.setattr(round_audit, "OUT_ROOT", out_root)
    video_path = in_dir / "IMG_0002.mov"
    _write_video_stub(video_path)
    slug = round_audit._slug(video_path)

    rc = round_audit.cmd_benchmark(in_dir, None, 5.0, False, False, only=[slug])
    assert rc == 0
    assert not (out_root / slug / "benchmark.json").exists()


def test_resume_safe_skips_existing_benchmark_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_dir, out_root, slug = _setup(
        tmp_path, monkeypatch, [{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True}]
    )
    (out_root / slug / "events_corrected.json").write_text(
        json.dumps([{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True}]),
        encoding="utf-8",
    )
    round_audit.cmd_benchmark(in_dir, None, 5.0, False, False, only=[slug])
    bench_path = out_root / slug / "benchmark.json"
    original = bench_path.read_text()
    # rewrite events_corrected.json to something that WOULD score differently if re-run
    (out_root / slug / "events_corrected.json").write_text(
        json.dumps([{"ts": 999.0, "label": "triangle choke", "actor": "you", "successful": True}]),
        encoding="utf-8",
    )
    round_audit.cmd_benchmark(in_dir, None, 5.0, False, False, only=[slug])  # no --force
    assert bench_path.read_text() == original
