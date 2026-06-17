---
id: "007"
slug: vicos-explore
phase: 4
lane: D
priority: P1
status: done
depends: ["[[006-vicos-download]]"]
branch: feature/007-vicos-explore
created: 2026-06-12
tags: [kanban, phase-4, P1, cv]
---

# 007 — ViCoS Exploration

## Goal
`cv/vicos_explore.py` parses annotations into a tidy DataFrame (one row per athlete-instance: 17×3 keypoints + position class) and reports class distribution.

## Context
10 positions × top/bottom → 18 classes (CLAUDE.md §ViCoS). Output frame is the direct input to [[008-pose-features|card 008]]. Reference: `waizbart/bjj_cnn_position_detector` parsing approach.

## Execution Plan
1. `cv/vicos_explore.py`:
   - `load_annotations(path) -> pd.DataFrame` — flatten JSON → columns: `image_id, athlete_idx, position, role, kp_{i}_{x|y|c}` for i in 0–16. Cache to `data/processed/vicos_keypoints.parquet`.
   - `class_distribution(df) -> pd.DataFrame` — counts + share per 18-class label.
   - `keypoint_quality(df) -> pd.DataFrame` — per-class mean confidence, % keypoints below 0.3 (occlusion proxy — grappling = heavy occlusion).
2. Notebook `notebooks/vicos_explore.ipynb` — distribution bar chart, sample pose skeleton overlays (if images present), confidence histograms.
3. `tests/test_vicos_explore.py` — mini fixture JSON (3 instances, 2 classes) → exact frame shape, class counts, parquet round-trip.
4. Record findings in card on completion: class imbalance ratio, low-confidence classes → informs [[008-pose-features|card 008]] feature choices + [[009-baseline-classifier|card 009]] class weights.
5. Gates clean.

## Acceptance Criteria
- [ ] Parquet cache w/ 1 row per athlete-instance, 51 keypoint cols + labels
- [ ] Class distribution table in notebook + card notes
- [ ] Fixture tests, no-network
- [ ] Gates clean

## Test Plan
Fixture JSON → assert row count, column names, dtype float for kp cols, class_distribution shares sum to 1.
