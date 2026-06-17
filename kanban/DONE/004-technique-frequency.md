---
id: "004"
slug: technique-frequency
phase: 3.5
lane: C
priority: P1
status: done
depends: []
branch: feature/004-technique-frequency
created: 2026-06-12
tags: [kanban, phase-3-5, P1, analysis]
---

# 004 — Technique Frequency Analysis

## Goal
`analysis/technique_freq.py` computes position heatmaps + submission frequency matrices from `grappling_techniques` and `adcc_historical`.

## Context
Feeds app insights + [[010-user-benchmark|card 010]] benchmarking. Pure functions on DataFrames (AGENTS.md rule 5). **This card owns the name-helper extraction** (`analysis/names.py`) — [[002-adcc-elo-export|card 002]], [[005-belt-analysis|card 005]] and [[010-user-benchmark|card 010]] consume it; assigning it here keeps concurrent lanes off `export/tech_library.py`.

## Execution Plan
1. Extract `_normalize_name`, `NAME_ALIASES`, `_resolve_aliases` from `export/tech_library.py` → `analysis/names.py` (or `schemas/names.py`); re-import in tech_library. No behavior change — existing tests must stay green.
2. `analysis/technique_freq.py`:
   - `position_distribution(tech_df) -> pd.DataFrame` — counts per `bjj_position` × `technique_type`.
   - `submission_frequency(adcc_df, by: Literal["year","weight_class","sex","stage"]) -> pd.DataFrame` — normalized sub counts pivot.
   - `submission_trend(adcc_df, top_n=10) -> pd.DataFrame` — per-year share of top subs (leg-lock era visible 2015+; sanity check).
3. Notebook `notebooks/technique_freq.ipynb` — heatmap (seaborn) per matrix; saved as exploratory artifact.
4. `tests/test_technique_freq.py` — synthetic frames, assert pivot shapes, normalization sums to 1, alias merge (inside/outside heel hook → heel hook bucket).
5. Gates clean.

## Acceptance Criteria
- [ ] Three pure functions, typed, no I/O
- [ ] tech_library tests still green after helper extraction
- [ ] Heel-hook trend visible in real-data notebook (2015+ rise)
- [ ] Gates clean

## Test Plan
Synthetic adcc frame w/ heel hook variants + RNC across 3 years. Assert merged counts, share sums, pivot index/columns.
