# CV Investigation Skill

Computer vision exploration for BJJ position detection using the ViCoS dataset.

## When to Use

When working on position detection, pose keypoint analysis, or baseline classifiers.

## Approach

### Phase 1 — Download & Explore
1. `cv/vicos_download.py` — download images + JSON annotations from ViCoS
2. `cv/vicos_explore.py` — parse JSON, visualize skeletons, class distribution

### Phase 2 — Feature Engineering
1. `cv/pose_features.py` — extract: joint angles, relative distances, bounding box ratios, center-of-mass offset, confidence masks
2. Normalize: `(feature - mean) / std` across dataset

### Phase 3 — Baseline Classifier
1. Merge position labels (top/bottom → position only)
2. Train RF + XGBoost
3. Evaluate: per-class F1, confusion matrix, top-2 accuracy

## Reference

- ViCoS keypoint format: COCO 17-keypoints `[x, y, confidence]`
- 10 positions → 18 classes (with orientation)
- waizbart: 90%+ with ViTPose + TF
- ValterH: 92% with 3 camera views

## Expected Outcome

`cv/FINDINGS.md` report on:
- Dataset quality (class balance, occlusion patterns)
- Baseline accuracy
- Gap to production (what's needed for 90%+)
