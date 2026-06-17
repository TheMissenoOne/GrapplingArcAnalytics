"""Pose feature engineering from COCO keypoints — pure functions on numpy arrays.

Feature groups (target 60-120):
  1. Normalized keypoint coordinates (34) — centered on hip-mid, scaled by torso
  2. Joint angles (8) — elbow, knee, hip, shoulder (L/R)
  3. Limb-length ratios (8) — forearm/upper-arm, shin/thigh, arm/torso, leg/torso (L/R)
  4. Torso features (4) — orientation, aspect ratio, lean
  5. Pairwise features (12) — inter-athlete distances, ordering, overlap
  Total: ~68
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# COCO keypoint indices
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

# Names of the 68-dim pair feature vector produced by ``pair_to_features``:
# athlete-0 single features (28) + athlete-1 single features (28) + pairwise (12).
FEATURE_NAMES: list[str] = (
    [f"ath0_{i}" for i in range(28)]
    + [f"ath1_{i}" for i in range(28)]
    + [f"pair_{i}" for i in range(12)]
)


def _hip_midpoint(kp: np.ndarray) -> np.ndarray:
    """Compute hip midpoint from left/right hip keypoints."""
    return np.asarray((kp[L_HIP, :2] + kp[R_HIP, :2]) / 2.0)


def _shoulder_midpoint(kp: np.ndarray) -> np.ndarray:
    """Compute shoulder midpoint from left/right shoulder keypoints."""
    return np.asarray((kp[L_SHOULDER, :2] + kp[R_SHOULDER, :2]) / 2.0)


def normalize_pose(kp: np.ndarray) -> np.ndarray:
    """Center on hip midpoint and scale by torso length.

    Torso length = distance from shoulder-midpoint to hip-midpoint.
    If torso length is 0 or keypoints have zero confidence, returns zero array.
    """
    if kp.shape != (17, 3):
        raise ValueError(f"Expected (17, 3) keypoint array, got {kp.shape}")

    result = np.zeros_like(kp)
    hip_mid = _hip_midpoint(kp)
    shoulder_mid = _shoulder_midpoint(kp)
    torso_length = np.linalg.norm(shoulder_mid - hip_mid)

    if torso_length < 1e-6:
        return result

    result[:, :2] = (kp[:, :2] - hip_mid) / torso_length
    result[:, 2] = kp[:, 2]
    return result


def single_pose_features(kp: np.ndarray) -> np.ndarray:
    """Extract per-person features from a single (17, 3) keypoint array.

    Returns array of 28 features:
      - 8 joint angles (elbow L/R, knee L/R, hip L/R, shoulder L/R)
      - 8 limb-length ratios (forearm/upper-arm L/R, shin/thigh L/R, arm/torso L/R, leg/torso L/R)
      - 4 torso features (orientation sin, orientation cos, aspect ratio, lean)
      - 8 pairwise ratios (L/R symmetry features)
    """
    if kp.shape != (17, 3):
        raise ValueError(f"Expected (17, 3), got {kp.shape}")

    def angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        ba = a - b
        bc = c - b
        dot = np.dot(ba, bc)
        norm = np.linalg.norm(ba) * np.linalg.norm(bc)
        if norm < 1e-6:
            return 0.0
        return float(np.arccos(np.clip(dot / norm, -1.0, 1.0)))

    def length(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.linalg.norm(a - b))

    features: list[float] = []

    # Joint angles (8)
    features.append(angle(kp[L_SHOULDER, :2], kp[L_ELBOW, :2], kp[L_WRIST, :2]))
    features.append(angle(kp[R_SHOULDER, :2], kp[R_ELBOW, :2], kp[R_WRIST, :2]))
    features.append(angle(kp[L_HIP, :2], kp[L_KNEE, :2], kp[L_ANKLE, :2]))
    features.append(angle(kp[R_HIP, :2], kp[R_KNEE, :2], kp[R_ANKLE, :2]))
    features.append(angle(kp[L_SHOULDER, :2], kp[L_HIP, :2], kp[L_KNEE, :2]))
    features.append(angle(kp[R_SHOULDER, :2], kp[R_HIP, :2], kp[R_KNEE, :2]))
    features.append(angle(kp[L_ELBOW, :2], kp[L_SHOULDER, :2], kp[L_HIP, :2]))
    features.append(angle(kp[R_ELBOW, :2], kp[R_SHOULDER, :2], kp[R_HIP, :2]))

    # Limb lengths
    l_upper_arm = length(kp[L_SHOULDER, :2], kp[L_ELBOW, :2])
    r_upper_arm = length(kp[R_SHOULDER, :2], kp[R_ELBOW, :2])
    l_forearm = length(kp[L_ELBOW, :2], kp[L_WRIST, :2])
    r_forearm = length(kp[R_ELBOW, :2], kp[R_WRIST, :2])
    l_thigh = length(kp[L_HIP, :2], kp[L_KNEE, :2])
    r_thigh = length(kp[R_HIP, :2], kp[R_KNEE, :2])
    l_shin = length(kp[L_KNEE, :2], kp[L_ANKLE, :2])
    r_shin = length(kp[R_KNEE, :2], kp[R_ANKLE, :2])
    torso_len = length(_shoulder_midpoint(kp), _hip_midpoint(kp))

    # Limb-length ratios (8)
    eps = 1e-6
    features.append(l_forearm / (l_upper_arm + eps))
    features.append(r_forearm / (r_upper_arm + eps))
    features.append(l_shin / (l_thigh + eps))
    features.append(r_shin / (r_thigh + eps))
    features.append((l_upper_arm + l_forearm) / (torso_len + eps))
    features.append((r_upper_arm + r_forearm) / (torso_len + eps))
    features.append((l_thigh + l_shin) / (torso_len + eps))
    features.append((r_thigh + r_shin) / (torso_len + eps))

    # Torso features (4)
    shoulder_hip_vec = _hip_midpoint(kp) - _shoulder_midpoint(kp)
    torso_angle = np.arctan2(shoulder_hip_vec[1], shoulder_hip_vec[0])
    features.append(np.sin(torso_angle))
    features.append(np.cos(torso_angle))
    shoulder_width = length(kp[L_SHOULDER, :2], kp[R_SHOULDER, :2])
    hip_width = length(kp[L_HIP, :2], kp[R_HIP, :2])
    features.append(shoulder_width / (hip_width + eps))
    features.append(shoulder_hip_vec[1] / (torso_len + eps))

    # L/R symmetry ratios (8)
    features.append(l_upper_arm / (r_upper_arm + eps))
    features.append(l_forearm / (r_forearm + eps))
    features.append(l_thigh / (r_thigh + eps))
    features.append(l_shin / (r_shin + eps))
    features.append(features[0] / (features[1] + eps))  # L/R elbow angle
    features.append(features[2] / (features[3] + eps))  # L/R knee angle
    features.append(features[4] / (features[5] + eps))  # L/R hip angle
    features.append(features[6] / (features[7] + eps))  # L/R shoulder angle

    return np.array(features, dtype=np.float64)


def pair_features(kp_a: np.ndarray, kp_b: np.ndarray) -> np.ndarray:
    """Extract inter-athlete pairwise features.

    Returns array of 12 features:
      - head-to-head vector (2)
      - hip-to-hip distance (1)
      - vertical ordering (1)
      - bounding hull overlap ratio (1)
      - relative torso angle (1)
      - min/max/mean inter-keypoint distances (3)
      - vertical offset at shoulders, hips, head (3)
    """
    if kp_a.shape != (17, 3) or kp_b.shape != (17, 3):
        raise ValueError("Both keypoint arrays must be (17, 3)")

    features: list[float] = []
    eps = 1e-6

    head_a = kp_a[NOSE, :2]
    head_b = kp_b[NOSE, :2]
    hip_a = _hip_midpoint(kp_a)
    hip_b = _hip_midpoint(kp_b)
    shoulder_a = _shoulder_midpoint(kp_a)
    shoulder_b = _shoulder_midpoint(kp_b)

    # Head-to-head vector
    features.append(head_b[0] - head_a[0])
    features.append(head_b[1] - head_a[1])

    # Hip-to-hip distance
    features.append(float(np.linalg.norm(hip_b - hip_a)))

    # Vertical ordering: positive if athlete B is above (smaller y in image coords)
    features.append(float(hip_a[1] - hip_b[1]))

    # Bounding hull overlap (axis-aligned)
    def bounding_area(kp: np.ndarray) -> tuple[float, float, float, float]:
        x = kp[:, 0][kp[:, 2] > 0.3]
        y = kp[:, 1][kp[:, 2] > 0.3]
        if len(x) == 0:
            return (0, 0, 0, 0)
        return (float(x.min()), float(y.min()), float(x.max()), float(y.max()))

    x1a, y1a, x2a, y2a = bounding_area(kp_a)
    x1b, y1b, x2b, y2b = bounding_area(kp_b)

    area_a = max((x2a - x1a) * (y2a - y1a), 0)
    area_b = max((x2b - x1b) * (y2b - y1b), 0)
    if area_a > 0 and area_b > 0:
        inter_x1 = max(x1a, x1b)
        inter_y1 = max(y1a, y1b)
        inter_x2 = min(x2a, x2b)
        inter_y2 = min(y2a, y2b)
        inter = max((inter_x2 - inter_x1) * (inter_y2 - inter_y1), 0)
        union = area_a + area_b - inter + eps
        features.append(inter / union)
    else:
        features.append(0.0)

    # Relative torso angle
    vec_a = hip_a - shoulder_a
    vec_b = hip_b - shoulder_b
    dot = np.dot(vec_a, vec_b)
    norms = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
    rel_angle = np.arccos(np.clip(dot / (norms + eps), -1.0, 1.0))
    features.append(float(rel_angle))

    # Inter-keypoint distances summary
    conf_a = kp_a[:, 2]
    conf_b = kp_b[:, 2]
    valid = (conf_a > 0.3) & (conf_b > 0.3)
    if valid.sum() > 0:
        dists = np.linalg.norm(kp_a[valid, :2] - kp_b[valid, :2], axis=1)
        features.append(float(dists.min()))
        features.append(float(dists.max()))
        features.append(float(dists.mean()))
    else:
        features.extend([0.0, 0.0, 0.0])

    # Vertical offsets at key joints
    features.append(float(shoulder_a[1] - shoulder_b[1]))
    features.append(float(hip_a[1] - hip_b[1]))
    features.append(float(head_a[1] - head_b[1]))

    return np.array(features, dtype=np.float64)


def pair_to_features(kp0: np.ndarray, kp1: np.ndarray) -> np.ndarray:
    """Assemble the full 68-dim feature vector for a pair of athletes.

    Single source of truth shared by training (``build_feature_matrix``) and
    runtime inference (``cv.inference``), so the served vector is byte-identical
    to the trained one.

    Note the deliberate asymmetry: single-pose features run on the **normalized**
    pose (hip-centered, torso-scaled), while pairwise features run on the **raw**
    pixel-space keypoints (they encode inter-athlete distances/ordering).

    Parameters
    ----------
    kp0, kp1 : np.ndarray
        ``(17, 3)`` COCO keypoint arrays ``[x, y, confidence]`` in pixel coords
        for athlete 0 and athlete 1 respectively.

    Returns
    -------
    np.ndarray
        ``(68,)`` float64 vector: ``[ath0 (28), ath1 (28), pair (12)]``.
    """
    f0 = single_pose_features(normalize_pose(kp0))
    f1 = single_pose_features(normalize_pose(kp1))
    pf = pair_features(kp0, kp1)
    return np.concatenate([f0, f1, pf])


def build_feature_matrix(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build X, y, feature_names from ViCoS annotation DataFrame.

    Groups by image_id, pairs athlete 0 and 1, extracts features.
    Returns (X, y, feature_names).
    """
    from collections import defaultdict

    groups: dict[str, dict[int, np.ndarray]] = defaultdict(dict)
    labels: dict[str, str] = {}

    for _, row in df.iterrows():
        img_id = str(row["image_id"])
        kp = np.zeros((17, 3), dtype=np.float64)
        for i in range(17):
            kp[i, 0] = row.get(f"kp_{i}_x", 0.0)
            kp[i, 1] = row.get(f"kp_{i}_y", 0.0)
            kp[i, 2] = row.get(f"kp_{i}_c", 0.0)
        athlete_idx = int(row.get("athlete_idx", 0))
        groups[img_id][athlete_idx] = kp
        labels[img_id] = str(row.get("class_label", "unknown"))

    x_list: list[np.ndarray] = []
    y_list: list[str] = []

    for img_id, athletes in groups.items():
        if 0 in athletes and 1 in athletes:
            x_list.append(pair_to_features(athletes[0], athletes[1]))
            y_list.append(labels.get(img_id, "unknown"))

    if not x_list:
        logger.warning("No valid athlete pairs found in DataFrame")
        return np.empty((0, 0)), np.empty(0), []

    X = np.stack(x_list)  # noqa: N806
    y = np.array(y_list)

    return X, y, list(FEATURE_NAMES)
