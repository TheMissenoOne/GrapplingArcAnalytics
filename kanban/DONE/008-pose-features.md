---
id: "008"
slug: pose-features
phase: 4
lane: D
priority: P1
status: done
depends: ["[[007-vicos-explore]]"]
branch: feature/008-pose-features
created: 2026-06-12
tags: [kanban, phase-4, P1, cv]
---

# 008 — Pose Feature Engineering

## Goal
`cv/pose_features.py` turns raw 17-keypoint pairs (2 athletes/image) into a scale/translation-invariant feature matrix for position classification.

## Context
Two athletes per image → pairwise relational features matter most (relative position defines guard/mount/back). Invariance: normalize by torso length, center on hip midpoint.

## Execution Plan
1. `cv/pose_features.py`, pure functions:
   - `normalize_pose(kp: np.ndarray) -> np.ndarray` — center hip-mid, scale by torso (shoulder-mid↔hip-mid distance); handle zero-confidence keypoints (mask, impute hip-mid).
   - `single_pose_features(kp) -> np.ndarray` — joint angles (elbow/knee/hip/shoulder, 8 angles), limb-length ratios, torso orientation.
   - `pair_features(kp_a, kp_b) -> np.ndarray` — inter-athlete: head-to-head vector, hip-to-hip distance, vertical ordering (top/bottom signal), overlap of bounding hulls, relative torso angle.
   - `build_feature_matrix(df) -> tuple[np.ndarray, np.ndarray, list[str]]` — X, y, feature names from [[007-vicos-explore|card 007]] parquet.
2. `tests/test_pose_features.py` — hand-built poses: translated/scaled copy → identical features (invariance); athlete-above pose → vertical-order feature sign; zero-conf masking.
3. Feature count target: 60–120. Document each group in module docstring.
4. Gates clean.

## Acceptance Criteria
- [ ] Translation + scale invariance asserted in tests
- [ ] NaN-free matrix on real parquet (masking verified)
- [ ] Named features (list aligns w/ matrix columns)
- [ ] Gates clean

## Test Plan
Synthetic 17-kp arrays (upright, inverted, offset/scaled clones). Exact-equality invariance asserts, angle spot-values (90° elbow), mask path on conf=0.
