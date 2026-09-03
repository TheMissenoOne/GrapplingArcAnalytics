#!/usr/bin/env python
"""Turn ONE video file into a frame sheet, choosing HOW to sample it from the footage itself.

Two regimes exist because a fixed-camera broadcast and a handheld clip need different
sampling: a fixed camera lets pixel motion mean "something happened" (a bimodal split between
static holds and action peaks); a moving camera pollutes every pixel every frame, so that
split is meaningless and the only honest fallback is time -- the same fixed interval
``scripts/frame_pdf.py`` already uses for broadcast footage. This module measures which
regime a video is in, per-video, rather than assuming one.

    uv run python -m scripts.video_frames --video data/video/owner/clip.mp4 --out data/frame_pdf/out/clip/

Two passes:

**Segmentation** (always runs, dry-run or not) -- walk the video at a low analysis frame rate
(``--analysis-fps``, default 9; ~8-10fps is the "does this look like footage" rate, not the
rate anything gets rendered at) computing, between each consecutive analysis frame: the raw
grayscale diff (motion of ANY kind, camera included) and the camera's own global motion
(translation + rotation, via ORB features + an affine RANSAC fit -- an affine, not a full
homography, because a phone's pan/tilt/roll has no perspective term worth fitting for and a
homography degrades badly when the matched points cluster on one side of the frame, which a
gi and a mat both do). Diff computed AFTER undoing that motion (``diff_residual``) isolates
what moved that the camera didn't cause -- entirely camera noise on a locked-off shot,
mostly athlete motion once the two are separated.

**Decision**: ``camera_moving`` when the estimated camera motion is high, by median or by how
often it spikes (either alone is enough -- a camera that pans once through an otherwise-still
shot would pass a median check and fail a frequency one). Moving -> fall back to
``frame_pdf.py``'s fixed-interval criterion (``DEFAULT_STEP_SECONDS``), because residual diff
carries no signal once the whole frame is displaced every sample. Not moving -> Otsu-threshold
``diff_residual`` into static/action, keep the CENTRE of every static run (a clean read of the
position) and the PEAK of every action run (the moment most likely to show a completed
technique, not a blur mid-transition).

Output, always: ``motion.json`` (raw series) and ``decision.json`` (what was chosen and why).
``--dry-run`` stops there plus ``motion.png``, a 3-series plot with the chosen timestamps
marked -- for eyeballing the call before spending the extraction+render pass on it. A real run
adds ``frames/*.jpg`` (native resolution, named by video-absolute second) and a landscape
2x2 sheet PDF in ``sheets/`` -- built with ``scripts/frame_pdf.py``'s own page/grid renderer
(imported, not re-implemented), so a sheet from this path looks and reads exactly like one
from the YouTube pipeline.

Privacy class: whatever the input video is. This script performs no upload and no DB write;
it is pure local frame extraction. The sample invocation in this repo's docs uses
``data/video/owner/`` (gitignored, private) -- callers own that classification, not this file.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.frame_pdf import (  # noqa: E402
    DEFAULT_GRID_LANDSCAPE,
    DEFAULT_STEP_SECONDS,
    FONT_B,
    PAD,
    _register_fonts,
    _text_block,
    draw_grid_pages,
    hhmmss,
    page_size,
    set_page_size,
)

logger = logging.getLogger("video_frames")

# ── Segmentation constants (each a decision, not a magic number) ────────────────────────────
# ~8-10fps: dense enough that a 5-frame-wide action window (spec's own worked cases) still gets
# several samples, cheap enough that ORB+RANSAC on every pair doesn't dominate wall time. Not
# the video's native rate (this repo's sample clip is ~31.6fps) -- diffing every native frame
# buys nothing but 3-4x the compute for near-identical neighbours.
ANALYSIS_FPS = 9.0
# Matches diff_raw/diff_residual/ORB detection all off one small frame -- cheap, and a global
# camera motion estimate needs no more resolution than this to be usable.
DOWNSCALE_WIDTH = 256
# cam_motion is translation (px, on the downscaled frame) plus rotation converted to an
# arc-length in the same px unit (radius = half the frame diagonal) -- one scalar, one unit,
# no separately-tuned translation/rotation weight to justify.
# Threshold: a tripod-fixed shot's frame-to-frame ORB fit has been observed (this repo's own
# ADCC broadcast sheets, all locked-off cameras) to sit under 1px of apparent drift from
# matching noise alone. 2.0px clears that noise floor with margin while still catching a slow
# creep, which a handheld shot never stays under for long.
CAM_MOTION_PX_THRESHOLD = 2.0
# A camera that is moving CONTINUOUSLY (handheld) rather than momentarily (one push-in) should
# trip the median check above already; this is the belt-and-suspenders case -- frequent spikes
# even when the median hides them (e.g. a camera that whip-pans back to still repeatedly).
CAM_MOTION_FRAC_THRESHOLD = 0.3
# A "static window" shorter than this is a stutter in the Otsu split, not a position worth its
# own frame -- below one analysis-frame gap it is noise in the threshold, not a held position.
MIN_STATIC_WINDOW_SECONDS = 1.0
# ORB needs a floor on matched points before an affine fit means anything; below it, "no
# reliable estimate" is the honest answer, not a fit to 3 noisy correspondences.
MIN_GOOD_MATCHES = 8
# Lowe's ratio test threshold for ORB/BF k=2 matches -- the standard value from the original
# paper, no reason to retune it for this use.
LOWE_RATIO = 0.75
RANSAC_REPROJ_PX = 3.0


@dataclass
class MotionRecord:
    t: float
    diff_raw: float
    diff_residual: float | None
    cam_motion: float | None
    inliers: int

    def to_json(self) -> dict[str, Any]:
        return {"t": self.t, "diff_raw": self.diff_raw, "diff_residual": self.diff_residual,
                "cam_motion": self.cam_motion, "inliers": self.inliers}


# ── Pass 1: per-frame motion ─────────────────────────────────────────────────────────────────
def _downscale_gray(frame: np.ndarray, width: int = DOWNSCALE_WIDTH) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = width / w
    small = cv2.resize(frame, (width, max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def estimate_camera_motion(gray0: np.ndarray, gray1: np.ndarray,
                           orb: cv2.ORB, matcher: cv2.BFMatcher,
                           ) -> tuple[np.ndarray | None, int]:
    """Affine fit (2x3) mapping ``gray0`` onto ``gray1``, plus RANSAC inlier count.

    ``None`` when there are not enough matched features to trust a fit -- a fast pan blurs
    ORB's corners away exactly when the estimate would matter most, and a fit to noise is
    worse than admitting the frame is unreadable this way.
    """
    kp0, des0 = orb.detectAndCompute(gray0, None)
    kp1, des1 = orb.detectAndCompute(gray1, None)
    if des0 is None or des1 is None or len(des0) < MIN_GOOD_MATCHES or len(des1) < MIN_GOOD_MATCHES:
        return None, 0
    pairs = matcher.knnMatch(des0, des1, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < LOWE_RATIO * p[1].distance]
    if len(good) < MIN_GOOD_MATCHES:
        return None, 0
    pts0 = np.float32([kp0[m.queryIdx].pt for m in good])
    pts1 = np.float32([kp1[m.trainIdx].pt for m in good])
    M, mask = cv2.estimateAffinePartial2D(pts0, pts1, method=cv2.RANSAC,
                                          ransacReprojThreshold=RANSAC_REPROJ_PX)
    if M is None:
        return None, 0
    return M, int(mask.sum()) if mask is not None else 0


def compute_motion_series(video_path: Path, analysis_fps: float = ANALYSIS_FPS,
                          downscale_width: int = DOWNSCALE_WIDTH) -> list[MotionRecord]:
    """One record per analysis-frame boundary. The first sampled frame gets no record (there
    is no prior frame to diff against); its timestamp is still worth keeping for extraction,
    which callers do separately from ``[0.0] + [r.t for r in records]``."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS) or analysis_fps
    step_frames = max(1, round(native_fps / analysis_fps))

    orb = cv2.ORB_create(500)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    records: list[MotionRecord] = []
    prev_gray: np.ndarray | None = None
    frame_idx = 0
    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_idx / native_fps
        gray = _downscale_gray(frame, downscale_width)
        if prev_gray is not None:
            diff_raw = float(np.abs(gray.astype(np.float32) - prev_gray.astype(np.float32)).mean())
            M, inliers = estimate_camera_motion(prev_gray, gray, orb, matcher)
            if M is not None:
                h, w = gray.shape
                warped = cv2.warpAffine(prev_gray, M, (w, h), flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_REPLICATE)
                diff_residual = float(np.abs(gray.astype(np.float32) -
                                             warped.astype(np.float32)).mean())
                tx, ty = float(M[0, 2]), float(M[1, 2])
                rot_rad = float(np.arctan2(M[1, 0], M[0, 0]))
                diag = float(np.hypot(w, h))
                cam_motion = float(np.hypot(tx, ty)) + abs(rot_rad) * (diag / 2)
            else:
                diff_residual, cam_motion = None, None
            records.append(MotionRecord(t=t, diff_raw=diff_raw, diff_residual=diff_residual,
                                        cam_motion=cam_motion, inliers=inliers))
        prev_gray = gray
        frame_idx += step_frames
    cap.release()
    return records


# ── Decision ──────────────────────────────────────────────────────────────────────────────────
def decide_camera_moving(records: list[MotionRecord]) -> dict[str, Any]:
    valid = [r.cam_motion for r in records if r.cam_motion is not None]
    median_cam = statistics.median(valid) if valid else 0.0
    # A frame whose ORB fit failed counts as "high" -- an unreadable estimate on a moving
    # camera happens for the same reason the motion is high (blur), so treating it as unknown
    # would systematically hide the busiest frames from the frequency check.
    high = [1 for r in records
           if r.cam_motion is None or r.cam_motion > CAM_MOTION_PX_THRESHOLD]
    frac_high = len(high) / len(records) if records else 0.0
    camera_moving = median_cam > CAM_MOTION_PX_THRESHOLD or frac_high > CAM_MOTION_FRAC_THRESHOLD
    return {
        "camera_moving": camera_moving,
        "median_cam_motion": median_cam,
        "frac_high_cam_motion": frac_high,
        "thresholds": {
            "cam_motion_px": CAM_MOTION_PX_THRESHOLD,
            "cam_motion_frac": CAM_MOTION_FRAC_THRESHOLD,
        },
    }


def otsu_threshold(values: list[float]) -> float:
    """Otsu's threshold on an arbitrary float series -- cv2's Otsu only runs on an 8-bit
    image, so the series is linearly rescaled into one column of uint8 pixels and the winning
    threshold is mapped back into the series' own units."""
    v = np.asarray(values, dtype=np.float64)
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        return lo
    scaled = ((v - lo) / (hi - lo) * 255).astype(np.uint8).reshape(-1, 1)
    t, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return lo + (float(t) / 255.0) * (hi - lo)


def select_static_scene_frames(records: list[MotionRecord]) -> tuple[list[float], float]:
    """Centre of every static run, peak of every action run. Returns (timestamps, otsu_t).

    Records with no residual (a failed ORB fit) are treated as action -- the same reasoning as
    the frequency check above.
    """
    if not records:
        return [0.0], 0.0
    resid = [r.diff_residual if r.diff_residual is not None else float("inf") for r in records]
    finite = [x for x in resid if x != float("inf")]
    otsu_t = otsu_threshold(finite) if finite else 0.0
    is_action = [x > otsu_t for x in resid]

    out: list[float] = [0.0]   # the very first frame is always worth a look (docs §4.5)
    i = 0
    n = len(records)
    while i < n:
        j = i
        while j < n and is_action[j] == is_action[i]:
            j += 1
        run = records[i:j]
        if is_action[i]:
            peak = max(range(i, j), key=lambda k: resid[k])
            out.append(records[peak].t)
        else:
            duration = run[-1].t - run[0].t if len(run) > 1 else 0.0
            if duration >= MIN_STATIC_WINDOW_SECONDS:
                out.append((run[0].t + run[-1].t) / 2)
        i = j
    return sorted(set(round(t, 3) for t in out)), otsu_t


def fallback_frame_timestamps(duration: float, step: float = DEFAULT_STEP_SECONDS) -> list[float]:
    """The frame_pdf.py criterion: a fixed interval from 0 to the video's own length."""
    n = max(1, int(duration // step) + 1)
    return [round(min(i * step, duration), 3) for i in range(n)]


# ── Extraction + render ──────────────────────────────────────────────────────────────────────
def extract_frames(video_path: Path, timestamps: list[float], out_dir: Path) -> list[tuple[float, Path]]:
    """Native-resolution JPEGs, one per timestamp, named by video-absolute second (matches
    frame_pdf.py's ``t%05d.jpg`` convention)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("t*.jpg"):
        stale.unlink()
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or ANALYSIS_FPS
    frames: list[tuple[float, Path]] = []
    for t in timestamps:
        cap.set(cv2.CAP_PROP_POS_FRAMES, round(t * fps))
        ok, frame = cap.read()
        if not ok:
            logger.warning("could not read frame at t=%.2fs", t)
            continue
        path = out_dir / f"t{int(t):05d}.jpg"
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        frames.append((t, path))
    cap.release()
    return frames


def build_sheet(frames: list[tuple[float, Path]], video_path: Path, decision: dict[str, Any],
                out_path: Path) -> None:
    """Landscape 2x2 sheet via frame_pdf.py's own grid renderer -- context page written here
    (this caller has no Entry/DB/manifest to draw frame_pdf's own context page from), frame
    grid drawn by the imported, unmodified function so every sheet in the repo looks alike."""
    from reportlab.pdfgen.canvas import Canvas

    set_page_size("landscape")
    _register_fonts()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = Canvas(str(out_path), pagesize=page_size("landscape"))
    c.setTitle(video_path.name)
    y = page_size("landscape")[1] - PAD
    c.setFont(FONT_B, 15)
    c.drawString(PAD, y, video_path.name)
    y -= 24
    c.setLineWidth(1)
    c.line(PAD, y, page_size("landscape")[0] - PAD, y)
    y -= 20
    rows = [
        ("Frames", f"{len(frames)}, {hhmmss(frames[0][0]) if frames else '-'} to "
                   f"{hhmmss(frames[-1][0]) if frames else '-'}"),
        ("Sampling method", decision["method"]),
        ("Camera moving", str(decision["camera_moving"])),
        ("Median cam motion (px-equiv)", f"{decision['median_cam_motion']:.3f}"),
        ("Fraction high-motion windows", f"{decision['frac_high_cam_motion']:.2f}"),
    ]
    for k, v in rows:
        c.setFont(FONT_B, 9)
        c.drawString(PAD, y, k)
        y = _text_block(c, v, PAD + 200, y, page_size("landscape")[0] - PAD - 200 - PAD, 9, 12) - 4
    c.showPage()
    draw_grid_pages(c, frames, DEFAULT_GRID_LANDSCAPE)
    c.save()


def plot_motion(records: list[MotionRecord], chosen: list[float], otsu_t: float | None,
                out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ts = [r.t for r in records]
    fig, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    axes[0].plot(ts, [r.diff_raw for r in records], color="tab:blue")
    axes[0].set_ylabel("diff_raw")
    resid = [r.diff_residual if r.diff_residual is not None else np.nan for r in records]
    axes[1].plot(ts, resid, color="tab:orange")
    if otsu_t is not None:
        axes[1].axhline(otsu_t, color="black", linestyle="--", linewidth=0.8, label="otsu")
        axes[1].legend(loc="upper right", fontsize=7)
    axes[1].set_ylabel("diff_residual")
    cam = [r.cam_motion if r.cam_motion is not None else np.nan for r in records]
    axes[2].plot(ts, cam, color="tab:green")
    axes[2].axhline(CAM_MOTION_PX_THRESHOLD, color="black", linestyle="--", linewidth=0.8)
    axes[2].set_ylabel("cam_motion (px-equiv)")
    axes[2].set_xlabel("t (s)")
    for ax in axes:
        for t in chosen:
            ax.axvline(t, color="red", alpha=0.25, linewidth=0.8)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────
def process(video_path: Path, out_dir: Path, *, analysis_fps: float = ANALYSIS_FPS,
           step: float = DEFAULT_STEP_SECONDS, dry_run: bool = False) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS) or analysis_fps
    n_native = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = (n_native / native_fps) if native_fps else 0.0
    cap.release()

    records = compute_motion_series(video_path, analysis_fps)
    decision = decide_camera_moving(records)

    otsu_t = None
    if decision["camera_moving"]:
        decision["method"] = "fallback_frame_pdf_step"
        timestamps = fallback_frame_timestamps(duration, step)
    else:
        decision["method"] = "static_scene_windows"
        timestamps, otsu_t = select_static_scene_frames(records)
        decision["otsu_threshold"] = otsu_t

    decision["n_frames"] = len(timestamps)
    decision["n_frames_analyzed"] = len(records)
    decision["video"] = str(video_path)
    decision["duration_seconds"] = duration
    decision["frame_timestamps"] = timestamps

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "motion.json").write_text(
        json.dumps({"analysis_fps": analysis_fps, "downscale_width": DOWNSCALE_WIDTH,
                    "records": [r.to_json() for r in records]}, indent=2), encoding="utf-8")
    (out_dir / "decision.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    plot_motion(records, timestamps, otsu_t, out_dir / "motion.png")

    if not dry_run:
        frames = extract_frames(video_path, timestamps, out_dir / "frames")
        if frames:
            build_sheet(frames, video_path, decision, out_dir / "sheets" / f"{out_dir.name}.pdf")
        decision["n_frames_extracted"] = len(frames)

    return decision


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--analysis-fps", type=float, default=ANALYSIS_FPS)
    ap.add_argument("--step", type=float, default=DEFAULT_STEP_SECONDS,
                    help="fallback interval (seconds) when the camera is moving")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.video.exists():
        logger.error("no such video: %s", a.video)
        return 1

    decision = process(a.video, a.out, analysis_fps=a.analysis_fps, step=a.step,
                       dry_run=a.dry_run)
    logger.info("camera_moving=%s method=%s n_frames=%d (median_cam=%.3f frac_high=%.2f)",
               decision["camera_moving"], decision["method"], decision["n_frames"],
               decision["median_cam_motion"], decision["frac_high_cam_motion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
