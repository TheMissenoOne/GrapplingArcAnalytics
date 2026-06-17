---
id: "011"
slug: fighter-similarity
phase: 5
lane: E
priority: P3
status: done
depends: ["[[001-adcc-elo-calibration]]", "[[010-user-benchmark]]"]
branch: feature/011-fighter-similarity
created: 2026-06-12
tags: [kanban, phase-5, P3, analysis]
---

# 011 — Fighter Similarity Matching

## Goal
`analysis/similarity.py` — cosine-similarity matching: given a user profile, return most stylistically similar ADCC fighters.

## Context
"You roll like X" feature for the app. Vector space: sub-type distribution (from matches won), win_type mix, sub_ratio/win_ratio (adcc_fighters), favorite_target one-hot, ELO bucket ([[001-adcc-elo-calibration|card 001]]). User vector from [[010-user-benchmark|card 010]] profile, projected into same space (fields w/o user equivalent → masked, not zero).

## Execution Plan
1. `analysis/similarity.py`, pure:
   - `fighter_vectors(adcc_df, fighters_df, elo_df) -> pd.DataFrame` — one row per fighter, L2-normalized feature columns; min 3 ADCC matches to qualify.
   - `top_similar(query_vec, vectors, k=5, mask: list[str] | None = None) -> pd.DataFrame` — masked cosine sim (compare only shared dims).
   - `user_vector(user_profile) -> tuple[np.ndarray, list[str]]` — vector + available-dims mask.
2. `tests/test_similarity.py` — synthetic vectors: identical → sim 1.0, orthogonal → 0.0; mask excludes dims correctly; min-match filter.
3. Notebook sanity: leg-locker query → expect Ryan/Tonon/Jones cluster; wrestler-passer query → distinct cluster.
4. Future hook (phase 5 vector DB, refinement doc §5): keep vectors exportable as parquet — no DB dependency now.
5. Gates clean.

## Acceptance Criteria
- [ ] Deterministic top-k w/ tie-break on name
- [ ] Mask path tested (user missing ELO dim still works)
- [ ] Cluster sanity documented in card on completion
- [ ] Gates clean

## Test Plan
Hand-built 4-fighter vector frame + query. Exact cosine values asserted, k > n handled, masked vs unmasked differ as expected.
