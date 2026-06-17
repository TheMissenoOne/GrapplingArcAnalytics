"""Tests for pose feature engineering — invariance, angles, pairwise."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cv.pose_features import (
    L_ANKLE,
    L_ELBOW,
    L_HIP,
    L_KNEE,
    L_SHOULDER,
    L_WRIST,
    NOSE,
    R_ANKLE,
    R_ELBOW,
    R_HIP,
    R_KNEE,
    R_SHOULDER,
    R_WRIST,
    build_feature_matrix,
    normalize_pose,
    pair_features,
    single_pose_features,
)


def _make_upright_pose() -> np.ndarray:
    """Standard upright pose (17, 3)."""
    kp = np.zeros((17, 3))
    kp[:5, :2] = [100, 100]
    kp[L_SHOULDER, :2] = [80, 130]
    kp[R_SHOULDER, :2] = [120, 130]
    kp[L_ELBOW, :2] = [70, 170]
    kp[R_ELBOW, :2] = [130, 170]
    kp[L_WRIST, :2] = [60, 210]
    kp[R_WRIST, :2] = [140, 210]
    kp[L_HIP, :2] = [85, 230]
    kp[R_HIP, :2] = [115, 230]
    kp[L_KNEE, :2] = [85, 280]
    kp[R_KNEE, :2] = [115, 280]
    kp[L_ANKLE, :2] = [85, 330]
    kp[R_ANKLE, :2] = [115, 330]
    kp[:, 2] = 0.9
    return kp


def _make_top_mount_pose() -> np.ndarray:
    """Person on top in mount."""
    kp = np.zeros((17, 3))
    kp[NOSE, :2] = [100, 140]
    kp[L_SHOULDER, :2] = [80, 160]
    kp[R_SHOULDER, :2] = [120, 160]
    kp[L_ELBOW, :2] = [60, 180]
    kp[R_ELBOW, :2] = [140, 180]
    kp[L_WRIST, :2] = [50, 200]
    kp[R_WRIST, :2] = [150, 200]
    kp[L_HIP, :2] = [85, 240]
    kp[R_HIP, :2] = [115, 240]
    kp[L_KNEE, :2] = [85, 260]
    kp[R_KNEE, :2] = [115, 260]
    kp[L_ANKLE, :2] = [85, 280]
    kp[R_ANKLE, :2] = [115, 280]
    kp[:, 2] = 0.9
    return kp


def _make_bottom_mount_pose() -> np.ndarray:
    """Person on bottom in mount."""
    kp = np.zeros((17, 3))
    kp[NOSE, :2] = [100, 280]
    kp[L_SHOULDER, :2] = [80, 300]
    kp[R_SHOULDER, :2] = [120, 300]
    kp[L_ELBOW, :2] = [60, 320]
    kp[R_ELBOW, :2] = [140, 320]
    kp[L_WRIST, :2] = [50, 340]
    kp[R_WRIST, :2] = [150, 340]
    kp[L_HIP, :2] = [85, 180]
    kp[R_HIP, :2] = [115, 180]
    kp[L_KNEE, :2] = [85, 140]
    kp[R_KNEE, :2] = [115, 140]
    kp[L_ANKLE, :2] = [85, 100]
    kp[R_ANKLE, :2] = [115, 100]
    kp[:, 2] = 0.9
    return kp


def test_normalize_pose_shape() -> None:
    kp = _make_upright_pose()
    norm = normalize_pose(kp)
    assert norm.shape == (17, 3)
    assert norm[:, 2] == pytest.approx(kp[:, 2])


def test_normalize_pose_center() -> None:
    kp = _make_upright_pose()
    norm = normalize_pose(kp)
    hip_mid = (norm[L_HIP, :2] + norm[R_HIP, :2]) / 2
    assert hip_mid[0] == pytest.approx(0.0, abs=1e-6)
    assert hip_mid[1] == pytest.approx(0.0, abs=1e-6)


def test_translation_invariance() -> None:
    kp = _make_upright_pose()
    shifted = kp.copy()
    shifted[:, :2] += 50
    norm1 = normalize_pose(kp)
    norm2 = normalize_pose(shifted)
    np.testing.assert_array_almost_equal(norm1, norm2)


def test_scale_invariance() -> None:
    kp = _make_upright_pose()
    scaled = kp.copy()
    scaled[:, :2] *= 2.0
    norm1 = normalize_pose(kp)
    norm2 = normalize_pose(scaled)
    np.testing.assert_array_almost_equal(norm1, norm2)


def test_degenerate_pose() -> None:
    zero = np.zeros((17, 3))
    norm = normalize_pose(zero)
    np.testing.assert_array_equal(norm, np.zeros((17, 3)))


def test_wrong_shape() -> None:
    with pytest.raises(ValueError):
        normalize_pose(np.zeros((10, 3)))


def test_single_pose_feature_count() -> None:
    kp = _make_upright_pose()
    features = single_pose_features(kp)
    assert len(features) == 28


def test_single_pose_elbow_angle() -> None:
    kp = _make_upright_pose()
    kp[L_ELBOW, :2] = [80, 170]
    kp[L_WRIST, :2] = [40, 170]
    kp[R_ELBOW, :2] = [120, 170]
    kp[R_WRIST, :2] = [160, 170]
    features = single_pose_features(kp)
    assert features[0] == pytest.approx(np.pi / 2, abs=0.15)
    assert features[1] == pytest.approx(np.pi / 2, abs=0.15)


def test_pair_feature_vertical_ordering() -> None:
    top = _make_top_mount_pose()
    bottom = _make_bottom_mount_pose()
    pf = pair_features(top, bottom)
    assert pf[3] != 0
    pf_swapped = pair_features(bottom, top)
    assert pf_swapped[3] == pytest.approx(-pf[3], abs=1e-6)


def test_pair_feature_count() -> None:
    top = _make_top_mount_pose()
    bottom = _make_bottom_mount_pose()
    pf = pair_features(top, bottom)
    assert len(pf) == 12


def test_build_feature_matrix_synthetic() -> None:
    kp1 = _make_upright_pose()
    kp2 = _make_top_mount_pose()
    athlete0: dict[str, float | str] = {
        "image_id": "img_1",
        "athlete_idx": 0,
        "position": "mount",
        "role": "top",
        "class_label": "mount_top",
    }
    athlete1: dict[str, float | str] = {
        "image_id": "img_1",
        "athlete_idx": 1,
        "position": "mount",
        "role": "bottom",
        "class_label": "mount_bottom",
    }
    for i in range(17):
        athlete0[f"kp_{i}_x"] = kp1[i, 0]
        athlete0[f"kp_{i}_y"] = kp1[i, 1]
        athlete0[f"kp_{i}_c"] = kp1[i, 2]
        athlete1[f"kp_{i}_x"] = kp2[i, 0]
        athlete1[f"kp_{i}_y"] = kp2[i, 1]
        athlete1[f"kp_{i}_c"] = kp2[i, 2]
    df = pd.DataFrame([athlete0, athlete1])
    X, y, names = build_feature_matrix(df)  # noqa: N806
    assert X.shape[0] == 1
    assert X.shape[1] == len(names)
    assert X.shape[1] == 28 + 28 + 12
    assert len(y) == 1
    assert not np.any(np.isnan(X))
