#!/usr/bin/env python
"""Full-round audit over the owner's OWN training footage — a manual, no-DB variant of
``scripts/video_jobs.py`` for reviewing 5 `.mov` files at once instead of running the Pro
worker's queue.

    uv run python -m scripts.round_audit frames  --in data/video/owner/rounds [--target-count N]
    uv run python -m scripts.round_audit read    --in data/video/owner/rounds --you "<kit description>" [--only <slug>]... [--model gemini-pro-latest] [--thinking high]
    uv run python -m scripts.round_audit highlights --in data/video/owner/rounds
    uv run python -m scripts.round_audit report  --in data/video/owner/rounds

Per input file (slug = the filename, safe-cased): ``frames`` segments + builds a sheet PDF,
``read`` sends it to Gemini and derives sequences/difficulty/highlights, ``highlights`` cuts
the top clips with a score breakdown and a motion-curve overlay, ``report`` renders one
Markdown audit per round plus an index. Every command is resume-safe — it skips a slug whose
output already exists unless ``--force``. ``--only <slug>`` (repeatable, every subcommand)
restricts a run to specific rounds -- the owner's kit differs per video, so ``read`` is meant
to be run once per round with its own ``--you``:
``round_audit read --only round1 --you "black rashguard"`` then
``round_audit read --only round2 --you "blue gi"``.

Privacy class: **PRIVATE** (root ``CLAUDE.md`` / this repo's ``CLAUDE.md``, "Public vs Private
Data"). Every output of every command here lands under ``data/video/owner/out/<slug>/``
(gitignored) — this is the owner's OWN footage, reviewed for the owner's OWN benefit. Nothing
this module produces may ever reach ``data/frame_pdf/``, ``data/finetune``, a CV/vision
dataset, the athlete corpus, an archetype centroid, an athlete's ELO, or the ``site/`` export —
there is no code path from here to any of those, and none should ever be added. Unlike
``scripts/video_jobs.py`` this script never touches the DB, a Storage bucket or a job row — it
is a purely local review tool over local files, reusing the SAME segmentation
(``scripts.video_frames``), reading (``scripts.gemini_read_frames``) and derivation
(``analysis.round_analysis``) the Pro worker uses.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.round_analysis import (  # noqa: E402
    HIGHLIGHT_WINDOW_BEFORE_S,
    build_highlights,
    build_sequences,
    derive_difficulty,
    difficulty_components,
)
from scripts import gemini_read_frames, video_frames  # noqa: E402
from scripts.frame_pdf import hhmmss  # noqa: E402

# ponytail: reuse video_jobs' own ffmpeg seek-flag shape (fast keyframe -ss before -i, -to
# against the original timeline) instead of a second copy of it here.
from scripts.video_jobs import _cut_clip  # noqa: E402

logger = logging.getLogger("round_audit")

ROUND_PROMPT_PATH = REPO / "docs" / "PROMPT_gemini_round_reading.md"
DATA_ROOT = REPO / "data" / "video" / "owner"
DEFAULT_IN_DIR = DATA_ROOT / "rounds"
OUT_ROOT = DATA_ROOT / "out"
#: Higher-quality than scripts.video_jobs's own DEFAULT_MODEL (gemini-3.6-flash) --
#: deliberate, this is a hand-run audit of 5 files, not a per-round batch worker. See the
#: gemini_read_frames.read_frames signature for what a caller can override.
DEFAULT_MODEL = "gemini-pro-latest"
DEFAULT_THINKING = "high"
_VIDEO_GLOBS = ("*.mov", "*.MOV", "*.mp4", "*.MP4")

#: cv2's own rotate flag per container ``rotate`` tag value -- used to bake the tag into pixels
#: by hand when cv2's OWN read path does not already do it (see ``normalize_video``).
_CV2_ROTATE_FOR_ROTATION = {
    90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}

#: The text fallback this flow always uses -- ``frames`` builds no context page (no --you at
#: that stage), so there is no selfie/kit row on the sheet itself for the model to read. This
#: is appended straight onto the prompt at `read` time instead, same idea as
#: gemini_read_frames.PAGE_NOTICE. The first sentence is the owner's general filming
#: convention (2026-09-16): whoever set the phone down and stepped back is "partner", not
#: "you" -- true regardless of who is closer to camera or which way either one is facing once
#: the round starts.
IDENTITY_NOTE_TMPL = (
    "\n\nIdentity discriminator for this round (no reference photo or context page in this "
    "flow -- this sentence is the text fallback the sheet's own context page would otherwise "
    "carry). General rule: at the start of the video, 'you' is the athlete standing in the "
    "background facing the camera; 'partner' is the one who set up the phone and walks away "
    "from it. In addition, 'you' is wearing: {you}. 'partner' is the other person in every "
    "frame, always, no matter their belt or who is winning."
)


# ── slug + discovery ─────────────────────────────────────────────────────────────────────────
def _slug(video_path: Path) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", video_path.stem.lower()).strip("-")
    return s or "video"


def _find_videos(in_dir: Path) -> list[Path]:
    return sorted({p for pat in _VIDEO_GLOBS for p in in_dir.glob(pat)})


def _filter_videos(videos: list[Path], only: list[str] | None) -> list[Path]:
    """``--only <slug>`` (repeatable, every subcommand) -- e.g. so ``read`` can run once per
    round with a kit description specific to that round. ``None``/empty -> no filtering."""
    if not only:
        return videos
    wanted = set(only)
    return [v for v in videos if _slug(v) in wanted]


# ── rotation ─────────────────────────────────────────────────────────────────────────────────
@dataclass
class VideoProbe:
    duration: float
    width: int
    height: int
    rotation: int  # normalized to [0, 360)


def _rotation_from_stream(stream: dict[str, Any]) -> int:
    """``rotation`` (degrees, may be negative) lives in one of two places depending on the
    ffprobe/ffmpeg build: a ``side_data_list`` "Display Matrix" entry (modern mov/iPhone
    footage), or the older ``tags.rotate`` string. First one found wins; ``0`` if neither is
    present (progressive/no rotation tag)."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"]))) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        return int(round(float(tag))) % 360
    return 0


def parse_ffprobe_doc(doc: dict[str, Any]) -> VideoProbe:
    """Pure -- ``doc`` is ``json.loads`` of ``ffprobe -show_streams -show_format``'s stdout.
    Never touches a filesystem or a subprocess, so a fake fixture exercises it directly."""
    streams = doc.get("streams") or [{}]
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    rotation = _rotation_from_stream(stream)
    duration_raw = (doc.get("format") or {}).get("duration") or stream.get("duration")
    duration = float(duration_raw) if duration_raw is not None else 0.0
    return VideoProbe(duration=duration, width=width, height=height, rotation=rotation)


def probe_video(video_path: Path) -> VideoProbe:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration:stream_tags=rotate:"
                              "stream_side_data_list:format=duration",
            "-of", "json", str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return parse_ffprobe_doc(json.loads(r.stdout))


def cv2_reads_rotated(decoded_w: int, decoded_h: int, probe: VideoProbe) -> bool:
    """Pure decision: does a decoded frame's own shape already match the DISPLAY-rotated
    presentation (cv2's ``CAP_PROP_ORIENTATION_AUTO`` applied the container tag itself), or the
    raw coded shape (cv2 ignored it)? ``probe.width``/``height`` are ffprobe's CODED dims,
    always pre-rotation."""
    if probe.rotation % 180 == 0:
        return True  # nothing to swap either way -- the tag (if any) is a no-op on dimensions
    return (decoded_w, decoded_h) == (probe.height, probe.width)


def _cv2_already_handles_rotation(video_path: Path, probe: VideoProbe) -> bool:
    if probe.rotation % 180 == 0:
        return True
    cap = cv2.VideoCapture(str(video_path))
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        return False  # unknown -- normalize rather than risk a silently sideways sheet
    h, w = frame.shape[:2]
    return cv2_reads_rotated(w, h, probe)


def normalize_video(video_path: Path, out_path: Path, probe: VideoProbe) -> None:
    """Bakes the container's rotation tag into pixels once, by hand, via cv2 -- NOT ffmpeg.

    Measured on this machine's own system ffmpeg build against the owner's real HEVC .MOV
    rounds: ``Decoding requested, but no decoder found for: hevc`` (this repo's ffmpeg package
    is built with ``--disable-decoder=h264,hevc,vc1,vvc``), so any ``-vf`` filter chain -- which
    needs to decode every frame -- fails before writing one. cv2 decodes the same files fine
    (``opencv-python`` bundles its own ffmpeg build with HEVC support -- this is exactly what
    ``video_frames.process`` already relies on for the frame sheet itself), so this function
    reads every frame, rotates it in memory (only reached when
    ``_cv2_already_handles_rotation`` returned False -- i.e. cv2's own read did NOT
    auto-rotate), and re-muxes with ``cv2.VideoWriter``. No downscale -- the ffmpeg 720p resize
    this replaced was a bandwidth nicety, not a correctness requirement for local review; add
    ``cv2.resize`` back per-frame if a real round proves too slow/large.
    """
    rotate_flag = _CV2_ROTATE_FOR_ROTATION.get(probe.rotation)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"normalize: cannot open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer: cv2.VideoWriter | None = None
    n_written = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if rotate_flag is not None:
                frame = cv2.rotate(frame, rotate_flag)
            if writer is None:
                h, w = frame.shape[:2]
                # cv2-stubs omits VideoWriter_fourcc (real attribute, same call
                # tests/test_video_frames.py already uses) -- mypy attr-defined gap, not ours.
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
                writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
            writer.write(frame)
            n_written += 1
    finally:
        cap.release()
        if writer is not None:
            writer.release()
    if n_written == 0 or not out_path.exists():
        raise RuntimeError(f"normalize: cv2 wrote no frames for {video_path}")


def prepare_source(video_path: Path, out_dir: Path) -> Path:
    """The video ``video_frames.process`` should actually read: ``video_path`` unchanged when
    there is no rotation tag or cv2 already respects it, else a once-built
    ``out_dir/normalized.mp4``. Resume-safe: a second call for the same slug reuses the file
    already on disk instead of re-encoding."""
    norm_path = out_dir / "normalized.mp4"
    if norm_path.exists():
        return norm_path
    probe = probe_video(video_path)
    if probe.rotation % 180 == 0:
        return video_path
    if _cv2_already_handles_rotation(video_path, probe):
        logger.info("%s: rotation=%d already respected by cv2, no normalize needed",
                    video_path.name, probe.rotation)
        return video_path
    logger.info("%s: rotation=%d ignored by cv2, normalizing -> %s",
                video_path.name, probe.rotation, norm_path)
    normalize_video(video_path, norm_path, probe)
    return norm_path


# ── frames ───────────────────────────────────────────────────────────────────────────────────
def cmd_frames(in_dir: Path, target_count: int | None, force: bool,
              only: list[str] | None = None) -> int:
    videos = _filter_videos(_find_videos(in_dir), only)
    if not videos:
        logger.warning("no .mov/.mp4 under %s (after --only filter)", in_dir)
        return 0
    for video_path in videos:
        slug = _slug(video_path)
        out_dir = OUT_ROOT / slug
        sheet_path = out_dir / "sheets" / f"{slug}.pdf"
        if sheet_path.exists() and not force:
            logger.info("%s: sheet exists, skip (--force to redo)", slug)
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        source = prepare_source(video_path, out_dir)
        decision = video_frames.process(
            source, out_dir, sampling="adaptive", target_count=target_count
        )
        logger.info("%s: %d frames (%s) -> %s", slug, decision.get("n_frames", 0),
                    decision.get("method"), sheet_path)
    return 0


# ── read ─────────────────────────────────────────────────────────────────────────────────────
def _pdf_page_count(pdf_path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


def cmd_read(in_dir: Path, you: str, model: str, thinking: str, dry_run: bool,
            only: list[str] | None = None) -> int:
    videos = _filter_videos(_find_videos(in_dir), only)
    if not videos:
        logger.warning("no .mov/.mp4 under %s (after --only filter)", in_dir)
        return 0
    prompt = gemini_read_frames.load_prompt(ROUND_PROMPT_PATH) + IDENTITY_NOTE_TMPL.format(you=you)
    for video_path in videos:
        slug = _slug(video_path)
        out_dir = OUT_ROOT / slug
        sheet_path = out_dir / "sheets" / f"{slug}.pdf"
        read_path = out_dir / "read.json"
        analysis_path = out_dir / "analysis.json"
        if not sheet_path.exists():
            logger.warning("%s: no sheet yet, run `frames` first, skip", slug)
            continue
        if read_path.exists() and analysis_path.exists():
            logger.info("%s: already read, skip", slug)
            continue

        n_pages = _pdf_page_count(sheet_path)
        logger.info("%s: sheet has %d page(s) to send (model=%s, thinking=%s)",
                    slug, n_pages, model, thinking)
        if dry_run:
            continue

        answer, raw = gemini_read_frames.read_frames([sheet_path], prompt, model, thinking)
        answer["usage"] = gemini_read_frames.usage_totals(getattr(raw, "usage_metadata", None))
        read_path.write_text(json.dumps(answer, indent=2), encoding="utf-8")

        events: list[dict[str, Any]] = list(answer.get("events") or [])
        resets: list[float] = list(answer.get("resets") or [])
        motion_path = out_dir / "motion.json"
        motion_doc = (
            json.loads(motion_path.read_text(encoding="utf-8")) if motion_path.exists() else {}
        )

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
        analysis_path.write_text(
            json.dumps(
                {
                    "sequences": sequences,
                    "difficulty": round(difficulty, 1),
                    "difficulty_inputs": inputs,
                    "highlights": highlights,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("%s: %d events, difficulty=%.1f", slug, len(events), difficulty)
    return 0


# ── highlights (demo of the highlight engine) ───────────────────────────────────────────────
def _label_slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or "event"


def _match_event(hl: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A highlight window carries no ``ts``/``actor``/``type`` of its own (``build_highlights``
    only returns the clip window) -- recover the event it scored by re-deriving its own ``ts``
    (``start + HIGHLIGHT_WINDOW_BEFORE_S``, the same offset the highlight was built with) and
    picking the closest same-label event, or the closest event overall if no label matches."""
    guess_ts = float(hl.get("start", 0.0)) + HIGHLIGHT_WINDOW_BEFORE_S
    label = hl.get("label")
    candidates = [e for e in events if str(e.get("label", "")) == label]
    pool = candidates or events
    if not pool:
        return None
    return min(pool, key=lambda e: abs(float(e.get("ts", 0.0)) - guess_ts))


def _cut_clip_cv2(source_video: Path, start: float, end: float, dest: Path) -> None:
    """Fallback for :func:`_cut_clip_with_fallback` -- decodes the window's own frames (cv2
    handles HEVC fine, see ``normalize_video``'s docstring) and re-muxes with
    ``cv2.VideoWriter``. No audio track (a clip's own audio is not something this review needs)
    and no keyframe snapping -- the window is exact, at the cost of a full decode instead of a
    remux."""
    cap = cv2.VideoCapture(str(source_video))
    if not cap.isOpened():
        raise RuntimeError(f"cv2 cut: cannot open {source_video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start) * 1000.0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    writer: cv2.VideoWriter | None = None
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 > end:
                break
            if writer is None:
                h, w = frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
                writer = cv2.VideoWriter(str(dest), fourcc, fps, (w, h))
            writer.write(frame)
    finally:
        cap.release()
        if writer is not None:
            writer.release()
    if writer is None or not dest.exists():
        raise RuntimeError(f"cv2 cut: no frames in [{start},{end}] of {source_video}")


def _cut_clip_with_fallback(source_video: Path, start: float, end: float, dest: Path) -> None:
    """``_cut_clip`` (ffmpeg stream copy, ``scripts/video_jobs.py``) first -- a remux needs no
    decoder and is measured working on this repo's own HEVC .MOV rounds even mid-GOP (ffmpeg
    snaps both ends to the nearest reachable keyframe rather than erroring, verified against
    ``data/video/owner/rounds/IMG_7501.MOV`` at the start, middle and tail of the file, and on
    a sub-second window). Falls back to :func:`_cut_clip_cv2` only if that ever raises -- e.g. a
    GOP structure with no keyframe reachable at all in a very short window."""
    try:
        _cut_clip(source_video, start, end, dest)
        return
    except RuntimeError as exc:
        logger.warning("stream-copy cut failed for %s [%.2f,%.2f] (%s), falling back to cv2",
                       source_video.name, start, end, exc)
    _cut_clip_cv2(source_video, start, end, dest)


def write_highlight_clips(
    highlights: list[dict[str, Any]], events: list[dict[str, Any]], source_video: Path,
    hl_dir: Path,
) -> list[dict[str, Any]]:
    """Cuts one clip per highlight (rank order), from ``source_video`` (the normalized/rotated
    copy when one exists, so portrait comes out right), via
    :func:`_cut_clip_with_fallback`. Returns per-clip records ready for
    :func:`render_highlights_md`."""
    hl_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for i, hl in enumerate(highlights):
        rank = i + 1
        event = _match_event(hl, events)
        mm, ss = divmod(int(float(hl.get("start", 0.0))), 60)
        name = f"{rank}_{mm:02d}{ss:02d}_{_label_slug(str(hl.get('label', '')))}.mp4"
        dest = hl_dir / name
        _cut_clip_with_fallback(source_video, float(hl["start"]), float(hl["end"]), dest)
        records.append({"rank": rank, "highlight": hl, "event": event, "clip_path": dest})
    return records


def render_highlights_md(video_name: str, records: list[dict[str, Any]]) -> str:
    lines = [f"# Highlights — {video_name}", ""]
    if not records:
        lines.append("(no highlights)")
        return "\n".join(lines) + "\n"
    for r in records:
        hl = r["highlight"]
        event = r["event"] or {}
        comp = hl.get("components") or {}
        also = hl.get("also") or []
        title = " → ".join([str(hl.get("label", "-")), *also])
        lines += [
            f"## {r['rank']}. {hhmmss(float(hl.get('start', 0.0)))}–"
            f"{hhmmss(float(hl.get('end', 0.0)))} — {title}",
            "",
            f"- actor: {event.get('actor', '?')}",
            f"- type: {event.get('type', '?')}",
            f"- successful: {event.get('successful', '?')}",
            f"- confidence: {event.get('confidence', '?')}",
            f"- score: {hl.get('score', '-')}",
            f"- clip: `{r['clip_path'].name}`",
            "",
            "| component | value |",
            "|---|---|",
            f"| success | {comp.get('success', '-')} |",
            f"| confidence | {comp.get('confidence', '-')} |",
            f"| motion_peak | {comp.get('motion_peak', '-')} |",
            f"| markov | {comp.get('markov', '-')} |",
            "",
        ]
    return "\n".join(lines) + "\n"


def plot_highlight_overlay(
    motion_doc: dict[str, Any], records: list[dict[str, Any]], out_path: Path,
) -> None:
    """``motion.png`` with the highlight windows shaded + ranked -- same series
    ``video_frames.plot_motion`` draws (``diff_residual``, falling back to ``diff_raw``), one
    axis instead of three since this is an overlay, not the segmentation decision."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    recs = motion_doc.get("records") or []
    ts = [float(r.get("t", 0.0)) for r in recs]
    vals = [
        float(r["diff_residual"]) if r.get("diff_residual") is not None
        else float(r.get("diff_raw", 0.0))
        for r in recs
    ]
    fig, ax = plt.subplots(figsize=(11, 3.5))
    if ts:
        ax.plot(ts, vals, color="tab:orange", linewidth=0.8)
    peak = max(vals, default=1.0) or 1.0
    for r in records:
        hl = r["highlight"]
        start, end = float(hl.get("start", 0.0)), float(hl.get("end", 0.0))
        ax.axvspan(start, end, color="tab:red", alpha=0.2)
        ax.text((start + end) / 2, peak * 1.03, f"#{r['rank']}", ha="center", fontsize=8,
               color="tab:red")
    ax.set_xlabel("t (s)")
    ax.set_ylabel("motion")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def cmd_highlights(in_dir: Path, force: bool, only: list[str] | None = None) -> int:
    videos = _filter_videos(_find_videos(in_dir), only)
    if not videos:
        logger.warning("no .mov/.mp4 under %s (after --only filter)", in_dir)
        return 0
    for video_path in videos:
        slug = _slug(video_path)
        out_dir = OUT_ROOT / slug
        analysis_path = out_dir / "analysis.json"
        read_path = out_dir / "read.json"
        hl_dir = out_dir / "highlights"
        md_path = hl_dir / "HIGHLIGHTS.md"
        if not analysis_path.exists():
            logger.warning("%s: no analysis.json, run `read` first, skip", slug)
            continue
        if md_path.exists() and not force:
            logger.info("%s: highlights already built, skip (--force to redo)", slug)
            continue

        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        events = (
            json.loads(read_path.read_text(encoding="utf-8")).get("events", [])
            if read_path.exists() else []
        )
        motion_path = out_dir / "motion.json"
        motion_doc = (
            json.loads(motion_path.read_text(encoding="utf-8")) if motion_path.exists() else {}
        )
        source = out_dir / "normalized.mp4"
        if not source.exists():
            source = video_path

        # recompute (no Gemini call, pure local derivation) so a --force rerun picks up any
        # change to analysis.round_analysis.build_highlights, e.g. the overlap-dedup rule --
        # and keep analysis.json in sync so `report`'s own highlight list matches the reel.
        highlights = build_highlights(events, motion_doc, k=5)
        analysis["highlights"] = highlights
        analysis_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")

        # delete stale clips first -- dedup can shrink the set or rename files (rank/label
        # shift), a plain overwrite would leave orphaned clips from the old run behind.
        if hl_dir.exists():
            for stale in hl_dir.glob("*.mp4"):
                stale.unlink()

        records = write_highlight_clips(highlights, events, source, hl_dir)
        md_path.write_text(render_highlights_md(video_path.name, records), encoding="utf-8")

        if motion_path.exists():
            plot_highlight_overlay(motion_doc, records, out_dir / "motion_highlights.png")

        logger.info("%s: %d highlight clip(s) -> %s", slug, len(records), hl_dir)
    return 0


# ── report ───────────────────────────────────────────────────────────────────────────────────
def render_audit_md(
    video_name: str, read_doc: dict[str, Any], analysis: dict[str, Any],
    decision: dict[str, Any], out_dir: Path,
) -> str:
    """Pure -- every input is already-parsed JSON, no filesystem access, so a fixture exercises
    it directly. ``out_dir`` is only used to decide whether to LINK the highlights sidecar
    (``highlights/HIGHLIGHTS.md``, ``motion_highlights.png``), never read from."""
    events = sorted(list(read_doc.get("events") or []), key=lambda e: float(e.get("ts", 0.0)))
    bout = read_doc.get("bout") or {}
    usage = read_doc.get("usage") or {}
    duration = decision.get("duration_seconds")
    n_frames = decision.get("n_frames")
    method = decision.get("method", "?")
    regime = "moving" if decision.get("camera_moving") else "fixed"

    lines = [f"# Round audit — {video_name}", "", "## Header", "",
             f"- file: `{video_name}`",
             f"- duration: {hhmmss(float(duration)) if duration is not None else '?'}",
             f"- frames sampled: {n_frames if n_frames is not None else '?'} "
             f"(sampling method: {method})",
             f"- camera regime: {regime}",
             f"- identity discriminator: {bout.get('identity_discriminator', '-')}",
             f"- notes: {bout.get('notes', '-')}", ""]

    lines += ["## Timeline", "",
             "| ts | actor | label | type | success | confidence | note |",
             "|---|---|---|---|---|---|---|"]
    for e in events:
        lines.append(
            f"| {hhmmss(float(e.get('ts', 0.0)))} | {e.get('actor', '-')} | "
            f"{e.get('label', '-')} | {e.get('type', '-')} | {e.get('successful', '-')} | "
            f"{e.get('confidence', '-')} | {e.get('note', '')} |"
        )
    lines.append("")

    lines += ["## Sequences", ""]
    for seq in analysis.get("sequences") or []:
        start, end = seq.get("startTs"), seq.get("endTs")
        n = len(seq.get("eventIdx") or [])
        lines.append(
            f"- Sequence {seq.get('sequenceId')}: "
            f"{hhmmss(float(start)) if start is not None else '-'}–"
            f"{hhmmss(float(end)) if end is not None else '-'} "
            f"({n} event{'s' if n != 1 else ''})"
        )
    lines.append("")

    lines += ["## Highlights", ""]
    if (out_dir / "highlights" / "HIGHLIGHTS.md").exists():
        lines.append("Score breakdown + clips: [highlights/HIGHLIGHTS.md](highlights/HIGHLIGHTS.md)")
    if (out_dir / "motion_highlights.png").exists():
        lines.append("Motion curve with highlight windows: [motion_highlights.png](motion_highlights.png)")
    for i, hl in enumerate(analysis.get("highlights") or [], start=1):
        title = " → ".join([str(hl.get("label", "-")), *(hl.get("also") or [])])
        lines.append(
            f"{i}. {hhmmss(float(hl.get('start', 0.0)))}–{hhmmss(float(hl.get('end', 0.0)))} "
            f"— {title} (score {hl.get('score', '-')})"
        )
    lines.append("")

    lines += ["## Difficulty", "", f"- derived difficulty: {analysis.get('difficulty', '-')} / 10",
             "", "| component | value |", "|---|---|"]
    for k, v in (analysis.get("difficulty_inputs") or {}).items():
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines += ["## Auditoria visual (a preencher)", ""]
    for e in events:
        lines.append(
            f"- [ ] {hhmmss(float(e.get('ts', 0.0)))} {e.get('actor', '-')} — "
            f"{e.get('label', '-')} ({e.get('type', '-')}) — verdict: ____"
        )
    lines.append("")

    lines += ["## Model usage / cost", "", f"- source: {read_doc.get('source', '-')}",
             f"- prompt tokens: {usage.get('prompt', '-')}",
             f"- candidate tokens: {usage.get('candidates', '-')}",
             f"- thinking tokens: {usage.get('thoughts', '-')}",
             f"- total tokens: {usage.get('total', '-')}", ""]
    return "\n".join(lines) + "\n"


def write_index_readme() -> None:
    """Scans ``OUT_ROOT`` for every slug that already has an ``AUDIT.md`` -- NOT just the
    slugs this run touched -- so a ``--only`` run never truncates the index down to one round;
    it only ever adds to what is already reported."""
    slugs = sorted(p.parent.name for p in OUT_ROOT.glob("*/AUDIT.md"))
    lines = [
        "# Owner round audits", "",
        "PRIVATE — see this repo's CLAUDE.md \"Public vs Private Data\". Everything linked "
        "here lives under `data/video/owner/out/` (gitignored) and never leaves it.", "",
    ]
    for slug in slugs:
        lines.append(f"- [{slug}]({slug}/AUDIT.md) — sheet: [{slug}.pdf]({slug}/sheets/{slug}.pdf)")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_report(in_dir: Path, only: list[str] | None = None) -> int:
    videos = _filter_videos(_find_videos(in_dir), only)
    if not videos:
        logger.warning("no .mov/.mp4 under %s (after --only filter)", in_dir)
        return 0
    any_written = False
    for video_path in videos:
        slug = _slug(video_path)
        out_dir = OUT_ROOT / slug
        read_path = out_dir / "read.json"
        analysis_path = out_dir / "analysis.json"
        decision_path = out_dir / "decision.json"
        if not (read_path.exists() and analysis_path.exists()):
            logger.warning("%s: missing read.json/analysis.json, run `read` first, skip", slug)
            continue
        read_doc = json.loads(read_path.read_text(encoding="utf-8"))
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        decision = (
            json.loads(decision_path.read_text(encoding="utf-8")) if decision_path.exists()
            else {}
        )
        md = render_audit_md(video_path.name, read_doc, analysis, decision, out_dir)
        (out_dir / "AUDIT.md").write_text(md, encoding="utf-8")
        any_written = True
        logger.info("%s: AUDIT.md written", slug)
    if any_written:
        write_index_readme()
    return 0


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

    def _add_only(p: argparse.ArgumentParser) -> None:
        p.add_argument("--only", action="append", default=None,
                       help="restrict to this slug (repeatable, e.g. --only round1 --only "
                            "round2); default: every round under --in")

    p_frames = sub.add_parser("frames", help="segment every round + build a frame sheet")
    p_frames.add_argument("--in", dest="in_dir", type=Path, default=DEFAULT_IN_DIR)
    p_frames.add_argument("--target-count", type=int, default=None,
                          help="adaptive sampling frame budget (default: what a 5s fixed "
                               "interval would produce)")
    p_frames.add_argument("--force", action="store_true", help="rebuild even if a sheet exists")
    _add_only(p_frames)

    p_read = sub.add_parser("read", help="send each sheet to Gemini + derive sequences/"
                                         "difficulty/highlights")
    p_read.add_argument("--in", dest="in_dir", type=Path, default=DEFAULT_IN_DIR)
    p_read.add_argument("--you", required=True,
                        help="kit description discriminating 'you' from 'partner' (this flow "
                             "has no reference-photo context page, so this is the text "
                             "fallback) -- run once per round via --only, the kit differs per "
                             "video")
    p_read.add_argument("--model", default=DEFAULT_MODEL)
    p_read.add_argument("--thinking", choices=("low", "medium", "high"), default=DEFAULT_THINKING)
    p_read.add_argument("--dry-run", action="store_true",
                        help="print the page count that would be sent, call nobody")
    _add_only(p_read)

    p_hl = sub.add_parser("highlights", help="cut the top clips + score breakdown + motion "
                                             "overlay (demo of the highlight engine)")
    p_hl.add_argument("--in", dest="in_dir", type=Path, default=DEFAULT_IN_DIR)
    p_hl.add_argument("--force", action="store_true", help="rebuild even if HIGHLIGHTS.md exists")
    _add_only(p_hl)

    p_report = sub.add_parser("report", help="render one AUDIT.md per round + the out/ index")
    p_report.add_argument("--in", dest="in_dir", type=Path, default=DEFAULT_IN_DIR)
    _add_only(p_report)

    a = ap.parse_args()
    if a.cmd == "frames":
        return cmd_frames(a.in_dir, a.target_count, a.force, only=a.only)
    if a.cmd == "read":
        return cmd_read(a.in_dir, a.you, a.model, a.thinking, a.dry_run, only=a.only)
    if a.cmd == "highlights":
        return cmd_highlights(a.in_dir, a.force, only=a.only)
    if a.cmd == "report":
        return cmd_report(a.in_dir, only=a.only)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
