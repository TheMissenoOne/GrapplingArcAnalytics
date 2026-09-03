"""Two synthetic videos, one per decision branch.

A camera-fixed clip and a panned one differ in exactly one way -- whether the background
texture holds still between frames -- so a correct implementation must read that difference
back out of the pixels, not out of a flag the test hands it. Both videos are generated here
(no fixture asset to rot).
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from scripts.video_frames import (
    MIN_STATIC_WINDOW_SECONDS,
    MotionRecord,
    compute_motion_series,
    decide_camera_moving,
    fallback_frame_timestamps,
    otsu_threshold,
    select_static_scene_frames,
)

FPS = 9.0
W, H = 256, 144
BG = np.random.RandomState(42).randint(0, 255, (H, W, 3), dtype=np.uint8)


def _write(path, frames: list[np.ndarray]) -> None:
    # MJPG/.avi, not mp4v/.mp4: MJPG encodes every frame independently, so a static hold
    # stays exactly static in the compressed stream. mp4v's inter-frame prediction inserts
    # GOP-boundary quantization noise that reads as spurious motion on an otherwise-still
    # frame -- measured directly, not theoretical (a ~3.2 residual spike on a hold with zero
    # actual change).
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    for f in frames:
        vw.write(f)
    vw.release()


def _static_scene_frames(n: int) -> list[np.ndarray]:
    """Fixed background; a block sits still except during two short move windows.

    Timeline (idx / FPS=9): hold @ posA until t=2s (idx<18), moves to posB over t=2-2.5s
    (idx 18-22), holds until t=4s (idx 23-35), moves to posC over t=4-4.5s (idx 36-40), holds
    to the end.
    """
    pos_a, pos_b, pos_c = (20, 20), (150, 60), (60, 100)
    block = 40   # ~7.7% of the 256x144 frame -- big enough that its mean-abs-diff contribution
    # clears MJPG quantization noise with margin; a few-percent action region is realistic
    # (docs/frame_pdf_reading.md measures ~3% on real broadcast footage)
    out = []
    for i in range(n):
        t = i / FPS
        if t < 2.0:
            pos = pos_a
        elif t < 2.5:
            frac = (t - 2.0) / 0.5
            pos = (int(pos_a[0] + frac * (pos_b[0] - pos_a[0])),
                  int(pos_a[1] + frac * (pos_b[1] - pos_a[1])))
        elif t < 4.0:
            pos = pos_b
        elif t < 4.5:
            frac = (t - 4.0) / 0.5
            pos = (int(pos_b[0] + frac * (pos_c[0] - pos_b[0])),
                  int(pos_b[1] + frac * (pos_c[1] - pos_b[1])))
        else:
            pos = pos_c
        frame = BG.copy()
        x, y = pos
        frame[y:y + block, x:x + block] = (30, 200, 30)
        out.append(frame)
    return out


def _panned_frames(n: int) -> list[np.ndarray]:
    """Same background, translated a few px further right every frame -- a steady handheld
    pan. ``np.roll`` keeps it real texture (no interpolation blur) so ORB matches cleanly."""
    return [np.roll(BG, shift=i * 3, axis=1) for i in range(n)]


@pytest.fixture(scope="module")
def static_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("v") / "static.avi"
    _write(path, _static_scene_frames(54))
    return path


@pytest.fixture(scope="module")
def panned_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("v") / "panned.avi"
    _write(path, _panned_frames(54))
    return path


def test_static_camera_with_moving_block_is_not_camera_moving(static_video) -> None:
    records = compute_motion_series(static_video, analysis_fps=FPS)
    decision = decide_camera_moving(records)
    assert decision["camera_moving"] is False, decision


def test_static_scene_windows_cover_both_holds_and_both_moves(static_video) -> None:
    records = compute_motion_series(static_video, analysis_fps=FPS)
    chosen, otsu_t = select_static_scene_frames(records)

    def has_one_in(lo: float, hi: float) -> bool:
        return any(lo <= t <= hi for t in chosen)

    # static holds -> a frame near each hold's centre
    assert has_one_in(0.5, 1.5), chosen     # hold @ posA
    assert has_one_in(2.8, 3.7), chosen     # hold @ posB
    assert has_one_in(4.8, 5.9), chosen     # hold @ posC
    # action windows -> a frame inside each move
    assert has_one_in(2.0, 2.5), chosen     # posA -> posB
    assert has_one_in(4.0, 4.5), chosen     # posB -> posC


def test_panned_camera_is_camera_moving(panned_video) -> None:
    records = compute_motion_series(panned_video, analysis_fps=FPS)
    decision = decide_camera_moving(records)
    assert decision["camera_moving"] is True, decision
    assert decision["median_cam_motion"] > decision["thresholds"]["cam_motion_px"]


def test_otsu_threshold_splits_bimodal_series() -> None:
    low = [1.0] * 10
    high = [20.0] * 10
    t = otsu_threshold(low + high)
    # Perfectly bimodal input can land the cut right at the low group's own value (any t in
    # [1.0, 20.0) separates the two groups with equal between-class variance) -- the property
    # that matters is the split itself, not where exactly inside the gap t sits.
    assert 1.0 <= t < 20.0
    assert all(x <= t for x in low)
    assert all(x > t for x in high)


def test_otsu_threshold_flat_series_returns_that_value() -> None:
    assert otsu_threshold([5.0, 5.0, 5.0]) == 5.0


def test_fallback_frame_timestamps_covers_duration_at_step() -> None:
    # Matches frame_pdf.py's own extract(): ffmpeg's `fps=1/step` filter emits t=0, step,
    # 2*step, ... with no trailing partial-interval frame -- same criterion, same result.
    ts = fallback_frame_timestamps(duration=12.0, step=5.0)
    assert ts == [0.0, 5.0, 10.0]


def test_fallback_frame_timestamps_short_video_still_yields_one_frame() -> None:
    assert fallback_frame_timestamps(duration=1.0, step=5.0) == [0.0]


def test_select_static_scene_frames_drops_short_static_run() -> None:
    # Two 0.2s static runs either side of one action spike -- both runs are shorter than
    # MIN_STATIC_WINDOW_SECONDS and must not each mint their own frame.
    assert MIN_STATIC_WINDOW_SECONDS >= 1.0
    records = [
        MotionRecord(t=0.0, diff_raw=1.0, diff_residual=1.0, cam_motion=0.1, inliers=50),
        MotionRecord(t=0.2, diff_raw=1.0, diff_residual=1.0, cam_motion=0.1, inliers=50),
        MotionRecord(t=0.4, diff_raw=20.0, diff_residual=20.0, cam_motion=0.1, inliers=50),
        MotionRecord(t=0.6, diff_raw=1.0, diff_residual=1.0, cam_motion=0.1, inliers=50),
        MotionRecord(t=0.8, diff_raw=1.0, diff_residual=1.0, cam_motion=0.1, inliers=50),
    ]
    chosen, _ = select_static_scene_frames(records)
    # frame 0 always kept, the action spike at t=0.4 kept, neither short static run gets one
    assert 0.4 in chosen
    assert len(chosen) <= 2
