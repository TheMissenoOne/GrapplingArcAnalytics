"""scripts/round_audit.py -- no real video/ffmpeg/Gemini call in most tests. Every I/O seam
(video_frames, gemini_read_frames, ffmpeg cut) is stubbed the same way tests/test_video_jobs.py
stubs video_jobs.process_job. A few tests (normalize, cv2 cut fallback) use a real tiny
synthetic video, same technique as tests/test_video_frames.py -- no fixture asset to rot, and
this repo's own system ffmpeg has no HEVC decoder, so cv2 is the only thing that can actually
decode a real owner round in this environment (measured against
data/video/owner/rounds/IMG_7501.MOV, not simulated).
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

import scripts.round_audit as round_audit

FPS = 10.0


def _write_synthetic_video(path: Path, n_frames: int, w: int, h: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
    rng = np.random.RandomState(7)
    for _ in range(n_frames):
        vw.write(rng.randint(0, 255, (h, w, 3), dtype=np.uint8))
    vw.release()


# ── slug ─────────────────────────────────────────────────────────────────────────────────────
def test_slug_lowercases_and_strips_unsafe_chars():
    assert round_audit._slug(Path("IMG_0042 (Round 1).MOV")) == "img-0042-round-1"


def test_slug_never_empty():
    assert round_audit._slug(Path("!!!.mov")) == "video"


# ── rotation parsing (fake ffprobe JSON, no subprocess) ─────────────────────────────────────
def test_parse_ffprobe_doc_reads_rotation_from_side_data():
    doc = {
        "streams": [{
            "width": 1920, "height": 1080,
            "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90.0}],
            "tags": {},
        }],
        "format": {"duration": "12.500000"},
    }
    probe = round_audit.parse_ffprobe_doc(doc)
    assert probe.width == 1920
    assert probe.height == 1080
    assert probe.rotation == 270  # -90 % 360
    assert probe.duration == 12.5


def test_parse_ffprobe_doc_falls_back_to_tags_rotate():
    doc = {"streams": [{"width": 640, "height": 360, "tags": {"rotate": "90"}}]}
    probe = round_audit.parse_ffprobe_doc(doc)
    assert probe.rotation == 90


def test_parse_ffprobe_doc_no_rotation_tag_is_zero():
    doc = {"streams": [{"width": 640, "height": 360, "tags": {}}], "format": {}}
    probe = round_audit.parse_ffprobe_doc(doc)
    assert probe.rotation == 0
    assert probe.duration == 0.0


def test_cv2_reads_rotated_true_when_decoded_shape_already_swapped():
    probe = round_audit.VideoProbe(duration=1.0, width=1920, height=1080, rotation=90)
    assert round_audit.cv2_reads_rotated(1080, 1920, probe) is True  # cv2 already rotated it
    assert round_audit.cv2_reads_rotated(1920, 1080, probe) is False  # cv2 ignored the tag


def test_cv2_reads_rotated_no_swap_needed_for_0_or_180():
    probe = round_audit.VideoProbe(duration=1.0, width=1920, height=1080, rotation=180)
    assert round_audit.cv2_reads_rotated(1920, 1080, probe) is True


# ── resume logic (frames) ───────────────────────────────────────────────────────────────────
def _write_video_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-video-bytes")


def _write_minimal_pdf(path: Path) -> None:
    """A real, 1-page, pypdf-readable PDF -- `_pdf_page_count` needs one, `b"%PDF-fake"` is not
    a valid stream and raises inside pypdf before the fake `read_frames` is even reached."""
    from reportlab.pdfgen.canvas import Canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = Canvas(str(path))
    c.drawString(72, 72, "fixture")
    c.save()


def _fake_video_frames_process(video_path, out_dir, *, sampling="fixed", target_count=None,
                               **_kwargs):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sheets").mkdir(parents=True, exist_ok=True)
    (out_dir / "sheets" / f"{out_dir.name}.pdf").write_bytes(b"%PDF-fake")
    (out_dir / "motion.json").write_text(
        json.dumps({"records": [{"t": 1.0, "diff_raw": 0.5, "diff_residual": 0.5}]}),
        encoding="utf-8",
    )
    (out_dir / "decision.json").write_text(
        json.dumps({"n_frames": 4, "camera_moving": False, "method": "adaptive_arc_length",
                    "duration_seconds": 42.0}),
        encoding="utf-8",
    )
    return {"n_frames": 4, "method": "adaptive_arc_length"}


def test_cmd_frames_resume_skips_existing_sheet(tmp_path, monkeypatch):
    in_dir = tmp_path / "rounds"
    _write_video_stub(in_dir / "round1.mov")
    out_root = tmp_path / "out"
    monkeypatch.setattr(round_audit, "OUT_ROOT", out_root)
    # no rotation tag -> prepare_source never calls ffprobe/cv2
    monkeypatch.setattr(round_audit, "probe_video",
                        lambda p: round_audit.VideoProbe(1.0, 100, 100, 0))
    calls: list[str] = []

    def fake_process(video_path, out_dir, **kwargs):
        calls.append(str(out_dir))
        return _fake_video_frames_process(video_path, out_dir, **kwargs)

    monkeypatch.setattr(round_audit.video_frames, "process", fake_process)

    round_audit.cmd_frames(in_dir, target_count=None, force=False)
    assert len(calls) == 1
    assert (out_root / "round1" / "sheets" / "round1.pdf").exists()

    round_audit.cmd_frames(in_dir, target_count=None, force=False)  # resume: sheet exists
    assert len(calls) == 1  # not called again

    round_audit.cmd_frames(in_dir, target_count=None, force=True)  # --force: redo
    assert len(calls) == 2


# ── AUDIT.md rendering from a fixture read.json/analysis.json ──────────────────────────────
def test_render_audit_md_from_fixture(tmp_path):
    read_doc = {
        "bout": {"identity_discriminator": "you = black rashguard", "notes": "hard roll"},
        "events": [
            {"ts": 5, "actor": "you", "label": "Double Leg Takedown", "type": "takedown",
             "successful": True, "confidence": "high", "note": ""},
            {"ts": 40, "actor": "partner", "label": "Armbar", "type": "submission",
             "successful": False, "confidence": "low", "note": "defended"},
        ],
        "source": "gemini_read_frames (gemini-pro-latest, thinking=high, 2026-09-16)",
        "usage": {"prompt": 1000, "candidates": 200, "thoughts": 300, "total": 1500},
    }
    analysis = {
        "sequences": [{"sequenceId": 0, "startTs": 5, "endTs": 40, "eventIdx": [0, 1]}],
        "difficulty": 4.5,
        "difficulty_inputs": {"control_share_you": 0.6, "sub_for": 1.0},
        "highlights": [{"start": 2.0, "end": 9.0, "label": "Double Leg Takedown", "score": 2.1}],
    }
    decision = {"duration_seconds": 90.0, "n_frames": 12, "method": "adaptive_arc_length",
               "camera_moving": False}

    md = round_audit.render_audit_md("round1.mov", read_doc, analysis, decision, tmp_path)

    assert "# Round audit — round1.mov" in md
    assert "Double Leg Takedown" in md
    assert "Armbar" in md
    assert "you = black rashguard" in md
    assert "4.5 / 10" in md
    assert "control_share_you" in md
    assert "verdict: ____" in md
    assert "total tokens: 1500" in md
    # no sidecar files on disk -> no dangling links
    assert "HIGHLIGHTS.md" not in md
    assert "motion_highlights.png" not in md


def test_render_audit_md_links_highlights_sidecar_when_present(tmp_path):
    (tmp_path / "highlights").mkdir()
    (tmp_path / "highlights" / "HIGHLIGHTS.md").write_text("x", encoding="utf-8")
    (tmp_path / "motion_highlights.png").write_bytes(b"x")
    md = round_audit.render_audit_md("r.mov", {"events": []}, {}, {}, tmp_path)
    assert "highlights/HIGHLIGHTS.md" in md
    assert "motion_highlights.png" in md


# ── highlights: HIGHLIGHTS.md renders components ────────────────────────────────────────────
def test_render_highlights_md_shows_component_breakdown():
    records = [{
        "rank": 1,
        "highlight": {
            "start": 2.0, "end": 9.0, "label": "Armbar", "score": 2.35,
            "components": {"success": 1.0, "confidence": 1.0, "motion_peak": 0.2, "markov": 0.15},
        },
        "event": {"actor": "you", "type": "submission", "successful": True, "confidence": "high"},
        "clip_path": Path("data/video/owner/out/r1/highlights/1_0002_armbar.mp4"),
    }]
    md = round_audit.render_highlights_md("r1.mov", records)
    assert "Armbar" in md
    assert "| success | 1.0 |" in md
    assert "| motion_peak | 0.2 |" in md
    assert "| markov | 0.15 |" in md
    assert "actor: you" in md
    assert "1_0002_armbar.mp4" in md


def test_render_highlights_md_empty_list():
    md = round_audit.render_highlights_md("r1.mov", [])
    assert "no highlights" in md


# ── clip paths stay under data/video/ (privacy) ─────────────────────────────────────────────
def test_write_highlight_clips_paths_under_hl_dir(tmp_path, monkeypatch):
    hl_dir = tmp_path / "data" / "video" / "owner" / "out" / "r1" / "highlights"
    cuts: list[tuple[float, float, Path]] = []

    def fake_cut_clip(video_path, start, end, dest):
        cuts.append((start, end, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"clip")

    monkeypatch.setattr(round_audit, "_cut_clip", fake_cut_clip)
    highlights = [
        {"start": 2.0, "end": 9.0, "label": "Double Leg Takedown", "score": 2.1,
         "components": {"success": 1.0, "confidence": 1.0, "motion_peak": 0.1, "markov": 1.0}},
    ]
    events = [{"ts": 5.0, "label": "Double Leg Takedown", "actor": "you", "type": "takedown",
              "successful": True, "confidence": "high"}]

    records = round_audit.write_highlight_clips(
        highlights, events, tmp_path / "data" / "video" / "owner" / "rounds" / "r1.mov", hl_dir
    )

    assert len(records) == 1
    clip_path = records[0]["clip_path"]
    assert "data/video/" in str(clip_path).replace("\\", "/")
    assert clip_path.exists()
    assert records[0]["event"]["label"] == "Double Leg Takedown"


def test_default_paths_live_under_data_video():
    assert "data/video/owner/rounds" in str(round_audit.DEFAULT_IN_DIR).replace("\\", "/")
    assert "data/video/owner/out" in str(round_audit.OUT_ROOT).replace("\\", "/")


def test_cmd_highlights_writes_under_data_video_tree(tmp_path, monkeypatch):
    """End-to-end through cmd_highlights with OUT_ROOT pointed at a fake data/video tree --
    every artefact this command writes must resolve under it."""
    fake_out_root = tmp_path / "data" / "video" / "owner" / "out"
    monkeypatch.setattr(round_audit, "OUT_ROOT", fake_out_root)

    in_dir = tmp_path / "data" / "video" / "owner" / "rounds"
    _write_video_stub(in_dir / "r1.mov")
    out_dir = fake_out_root / "r1"
    out_dir.mkdir(parents=True)
    (out_dir / "analysis.json").write_text(json.dumps({
        "highlights": [{"start": 2.0, "end": 9.0, "label": "Armbar", "score": 2.1,
                        "components": {"success": 1.0, "confidence": 1.0,
                                       "motion_peak": 0.1, "markov": 1.0}}],
    }), encoding="utf-8")
    (out_dir / "read.json").write_text(json.dumps({
        "events": [{"ts": 5.0, "label": "Armbar", "actor": "you", "type": "submission",
                    "successful": True, "confidence": "high"}],
    }), encoding="utf-8")
    (out_dir / "motion.json").write_text(json.dumps({
        "records": [{"t": 1.0, "diff_raw": 0.4, "diff_residual": 0.4}],
    }), encoding="utf-8")

    cuts: list[Path] = []

    def fake_cut_clip(video_path, start, end, dest):
        cuts.append(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"clip")

    monkeypatch.setattr(round_audit, "_cut_clip", fake_cut_clip)

    round_audit.cmd_highlights(in_dir, force=False)

    assert cuts and all("data/video" in str(p).replace("\\", "/") for p in cuts)
    md_path = out_dir / "highlights" / "HIGHLIGHTS.md"
    assert md_path.exists()
    assert "data/video" in str(md_path).replace("\\", "/")
    overlay = out_dir / "motion_highlights.png"
    assert overlay.exists()
    assert "data/video" in str(overlay).replace("\\", "/")


# ── --only filter (repeatable) ──────────────────────────────────────────────────────────────
def test_filter_videos_only_none_returns_everything():
    videos = [Path("a.mov"), Path("b.mov")]
    assert round_audit._filter_videos(videos, None) == videos


def test_filter_videos_only_keeps_matching_slugs():
    videos = [Path("round1.mov"), Path("round2.mov"), Path("round3.mov")]
    kept = round_audit._filter_videos(videos, ["round1", "round3"])
    assert kept == [Path("round1.mov"), Path("round3.mov")]


def test_cmd_frames_only_processes_a_single_slug(tmp_path, monkeypatch):
    in_dir = tmp_path / "rounds"
    _write_video_stub(in_dir / "round1.mov")
    _write_video_stub(in_dir / "round2.mov")
    monkeypatch.setattr(round_audit, "OUT_ROOT", tmp_path / "out")
    monkeypatch.setattr(round_audit, "probe_video",
                        lambda p: round_audit.VideoProbe(1.0, 100, 100, 0))
    processed: list[str] = []

    def fake_process(video_path, out_dir, **kwargs):
        processed.append(out_dir.name)
        return _fake_video_frames_process(video_path, out_dir, **kwargs)

    monkeypatch.setattr(round_audit.video_frames, "process", fake_process)

    round_audit.cmd_frames(in_dir, target_count=None, force=False, only=["round2"])

    assert processed == ["round2"]


def test_cmd_read_only_lets_each_round_carry_its_own_you(tmp_path, monkeypatch):
    """The whole point of --only on `read`: two calls, two different --you kit texts, each
    touching exactly one slug's read.json."""
    in_dir = tmp_path / "rounds"
    _write_video_stub(in_dir / "round1.mov")
    _write_video_stub(in_dir / "round2.mov")
    out_root = tmp_path / "out"
    monkeypatch.setattr(round_audit, "OUT_ROOT", out_root)
    for slug in ("round1", "round2"):
        (out_root / slug / "sheets").mkdir(parents=True)
        _write_minimal_pdf(out_root / slug / "sheets" / f"{slug}.pdf")

    monkeypatch.setattr(round_audit.gemini_read_frames, "load_prompt", lambda p: "PROMPT")
    prompts_sent: list[str] = []

    def fake_read_frames(sheets, prompt, model, thinking):
        prompts_sent.append(prompt)
        return {"bout": {}, "events": [], "resets": []}, object()

    monkeypatch.setattr(round_audit.gemini_read_frames, "read_frames", fake_read_frames)

    round_audit.cmd_read(in_dir, "black rashguard", "m", "high", dry_run=False, only=["round1"])
    round_audit.cmd_read(in_dir, "blue gi", "m", "high", dry_run=False, only=["round2"])

    assert len(prompts_sent) == 2
    assert "black rashguard" in prompts_sent[0]
    assert "blue gi" not in prompts_sent[0]
    assert "blue gi" in prompts_sent[1]
    assert (out_root / "round1" / "read.json").exists()
    assert (out_root / "round2" / "read.json").exists()


def test_identity_note_carries_the_generic_filming_rule_and_the_kit():
    note = round_audit.IDENTITY_NOTE_TMPL.format(you="black rashguard")
    assert "standing in the background facing the camera" in note
    assert "set up the phone and walks away" in note
    assert "black rashguard" in note


# ── report --only doesn't truncate the index ────────────────────────────────────────────────
def test_write_index_readme_includes_every_existing_audit_not_just_this_run(tmp_path, monkeypatch):
    out_root = tmp_path / "out"
    monkeypatch.setattr(round_audit, "OUT_ROOT", out_root)
    for slug in ("round1", "round2", "round3"):
        (out_root / slug).mkdir(parents=True)
        (out_root / slug / "AUDIT.md").write_text("x", encoding="utf-8")

    round_audit.write_index_readme()

    readme = (out_root / "README.md").read_text(encoding="utf-8")
    for slug in ("round1", "round2", "round3"):
        assert slug in readme


# ── normalize_video (cv2-only, no ffmpeg decode) ────────────────────────────────────────────
def test_normalize_video_rotates_and_swaps_dimensions(tmp_path):
    src = tmp_path / "src.mp4"
    _write_synthetic_video(src, n_frames=6, w=64, h=32)  # landscape source
    probe = round_audit.VideoProbe(duration=0.6, width=64, height=32, rotation=90)
    out_path = tmp_path / "normalized.mp4"

    round_audit.normalize_video(src, out_path, probe)

    assert out_path.exists()
    cap = cv2.VideoCapture(str(out_path))
    ok, frame = cap.read()
    n = 0
    while ok:
        n += 1
        ok, frame2 = cap.read()
        if ok:
            frame = frame2
    cap.release()
    h, w = frame.shape[:2]
    assert (w, h) == (32, 64)  # swapped -- portrait output from a landscape source
    assert n == 6


def test_normalize_video_no_rotation_keeps_dimensions(tmp_path):
    src = tmp_path / "src.mp4"
    _write_synthetic_video(src, n_frames=3, w=64, h=32)
    probe = round_audit.VideoProbe(duration=0.3, width=64, height=32, rotation=0)
    out_path = tmp_path / "normalized.mp4"

    round_audit.normalize_video(src, out_path, probe)

    cap = cv2.VideoCapture(str(out_path))
    ok, frame = cap.read()
    cap.release()
    assert ok
    h, w = frame.shape[:2]
    assert (w, h) == (64, 32)


# ── _cut_clip_with_fallback: cv2 fallback when the ffmpeg stream-copy path fails ────────────
def test_cut_clip_with_fallback_uses_cv2_when_ffmpeg_cut_fails(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    _write_synthetic_video(src, n_frames=int(FPS) * 3, w=48, h=48)  # 3s @ 10fps

    def raising_cut_clip(video_path, start, end, dest):
        raise RuntimeError("ffmpeg could not cut clip: simulated stream-copy failure")

    monkeypatch.setattr(round_audit, "_cut_clip", raising_cut_clip)

    dest = tmp_path / "clip.mp4"
    round_audit._cut_clip_with_fallback(src, 0.5, 2.0, dest)

    assert dest.exists()
    cap = cv2.VideoCapture(str(dest))
    ok, _ = cap.read()
    cap.release()
    assert ok  # the cv2-written fallback clip actually decodes back


def test_cut_clip_cv2_raises_when_window_has_no_frames(tmp_path):
    src = tmp_path / "src.mp4"
    _write_synthetic_video(src, n_frames=5, w=32, h=32)  # 0.5s of footage @ 10fps
    dest = tmp_path / "clip.mp4"
    try:
        round_audit._cut_clip_cv2(src, 10.0, 12.0, dest)  # window past the end of the file
        raised = False
    except RuntimeError:
        raised = True
    assert raised


# ── transcode: real cv2 decode -> real ffmpeg libopenh264 encode, tiny synthetic source ────
def test_transcode_h264_writes_a_playable_720p_mp4(tmp_path):
    src = tmp_path / "src.mp4"
    _write_synthetic_video(src, n_frames=6, w=64, h=32)  # landscape, well under 720p
    out_path = tmp_path / "video.mp4"

    round_audit._transcode_h264(src, out_path)

    assert out_path.exists() and out_path.stat().st_size > 0
    cap = cv2.VideoCapture(str(out_path))
    ok, frame = cap.read()
    cap.release()
    assert ok  # ffmpeg's own encode decodes back fine
    h, w = frame.shape[:2]
    assert (w, h) == (720, 360)  # longest side always scaled to 720, aspect kept


def test_cmd_transcode_resume_skips_existing_video(tmp_path, monkeypatch):
    fake_out_root = tmp_path / "out"
    monkeypatch.setattr(round_audit, "OUT_ROOT", fake_out_root)
    in_dir = tmp_path / "rounds"
    _write_video_stub(in_dir / "r1.mov")
    out_dir = fake_out_root / "r1"
    out_dir.mkdir(parents=True)
    (out_dir / "video.mp4").write_bytes(b"already-transcoded")

    calls = []
    monkeypatch.setattr(round_audit, "_transcode_h264",
                        lambda source, out_path: calls.append(out_path))
    monkeypatch.setattr(round_audit, "prepare_source", lambda video_path, out_dir: video_path)

    round_audit.cmd_transcode(in_dir, force=False)

    assert calls == []  # resume-safe: never re-transcoded
    assert (out_dir / "video.mp4").read_bytes() == b"already-transcoded"
