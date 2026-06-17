---
id: "009"
slug: baseline-classifier
phase: 4
lane: D
priority: P1
status: done
depends: ["[[008-pose-features]]"]
branch: feature/009-baseline-classifier
created: 2026-06-12
tags: [kanban, phase-4, P1, cv]
---

# 009 — Baseline Position Classifier

## Goal
`cv/baseline_classifier.py` trains RF + XGBoost on [[008-pose-features|card 008]] features → 18-class position prediction, target ~80% accuracy (CLAUDE.md §ViCoS phase-1 target).

## Context
Reference points: 90%+ (waizbart, CNN), 92% (ValterH, 3-view ViTPose). We accept lower w/ classic ML on keypoints — speed + interpretability. Class imbalance numbers come from [[007-vicos-explore|card 007]] notes.

## Execution Plan
1. `cv/baseline_classifier.py`:
   - `train_baseline(X, y, model: Literal["rf","xgb"], seed=42) -> tuple[Any, dict]` — stratified 80/20 split, class weights from distribution, returns model + metrics dict (accuracy, macro-F1, per-class F1, confusion matrix).
   - `cross_validate(X, y, model, folds=5) -> pd.DataFrame` — grouped by `image_id` (both athletes of one image stay in same fold — leakage guard).
   - `feature_importance(model, names, top_n=20) -> pd.DataFrame`.
   - Persist best model `data/processed/position_clf.joblib` (gitignored via `*.joblib`).
2. Notebook `notebooks/baseline_classifier.ipynb` — confusion matrix heatmap; analyze top/bottom confusions vs cross-position confusions.
3. `tests/test_baseline_classifier.py` — tiny synthetic separable dataset → accuracy 1.0; group-split leakage test (same image_id never in both folds); metrics dict keys.
4. Record real accuracy + confusion findings in card on completion.
5. Gates clean.

## Acceptance Criteria
- [ ] ≥75% accuracy real data (80% target; document gap + next steps if short)
- [ ] Group-aware CV — no image leakage
- [ ] Feature importance table (validates [[008-pose-features|card 008]] design)
- [ ] Gates clean

## Test Plan
Blob dataset (sklearn make_classification, 18 classes) → both models fit, metrics shape; grouped-fold assert via synthetic image_ids.
