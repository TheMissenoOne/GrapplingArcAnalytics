"""Tests for pose estimation — runtime injection, parsing, grappler selection.

The Ultralytics runtime is replaced with a canned callable, so no real model or
network is touched.
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.pose_estimate import PoseEstimator
from cv.pose_features import L_HIP, NOSE, R_HIP


def _pose(hip_y: float, scale: float = 1.0, conf: float = 0.9) -> np.ndarray:
    """A pose whose hips sit at ``hip_y`` and whose bbox scales with ``scale``."""
    kp = np.zeros((17, 3))
    kp[NOSE, :2] = [100, hip_y - 100 * scale]
    kp[L_HIP, :2] = [100 - 20 * scale, hip_y]
    kp[R_HIP, :2] = [100 + 20 * scale, hip_y]
    # a couple extra confident points to give the bbox height
    kp[5, :2] = [100 - 20 * scale, hip_y - 50 * scale]
    kp[15, :2] = [100, hip_y + 80 * scale]
    kp[:, 2] = conf
    return kp


def test_estimate_passes_through_valid_poses() -> None:
    poses = [_pose(200), _pose(260)]
    est = PoseEstimator(runtime=lambda _frame: poses)
    out = est.estimate(np.zeros((480, 640, 3)))
    assert len(out) == 2
    assert all(p.shape == (17, 3) for p in out)


def test_estimate_filters_bad_shape() -> None:
    bad = np.zeros((16, 3))  # wrong keypoint count
    est = PoseEstimator(runtime=lambda _frame: [_pose(200), bad])
    out = est.estimate(np.zeros((10, 10, 3)))
    assert len(out) == 1


def test_select_grappler_pair_none_when_fewer_than_two() -> None:
    est = PoseEstimator(runtime=lambda _frame: [])
    assert est.select_grappler_pair([_pose(200)]) is None


def test_select_grappler_pair_picks_largest_two_and_orders() -> None:
    est = PoseEstimator(runtime=lambda _frame: [])
    big_high = _pose(hip_y=150, scale=2.0)  # higher in frame (smaller hip-y)
    big_low = _pose(hip_y=400, scale=2.0)  # lower in frame
    tiny = _pose(hip_y=250, scale=0.2)  # smallest bbox -> dropped

    pair = est.select_grappler_pair([big_low, tiny, big_high])
    assert pair is not None
    kp0, kp1 = pair
    # hip_y ordering: athlete 0 is the higher (smaller hip-y) of the two big poses.
    assert (kp0[L_HIP, 1] + kp0[R_HIP, 1]) / 2 < (kp1[L_HIP, 1] + kp1[R_HIP, 1]) / 2
    assert np.isclose((kp0[L_HIP, 1] + kp0[R_HIP, 1]) / 2, 150)


def test_select_grappler_pair_order_none_keeps_input_order() -> None:
    est = PoseEstimator(runtime=lambda _frame: [])
    a = _pose(hip_y=400, scale=2.0)
    b = _pose(hip_y=150, scale=2.0)
    pair = est.select_grappler_pair([a, b], order_by="none")
    assert pair is not None
    # largest-two sort is stable for equal areas -> input order preserved
    assert np.isclose((pair[0][L_HIP, 1] + pair[0][R_HIP, 1]) / 2, 400)


def test_select_grappler_pair_invalid_order_raises() -> None:
    est = PoseEstimator(runtime=lambda _frame: [])
    with pytest.raises(ValueError):
        est.select_grappler_pair([_pose(200), _pose(260)], order_by="bogus")
