---
id: "001"
slug: adcc-elo-calibration
phase: 3
lane: A
priority: P0
status: done
depends: []
branch: feature/001-adcc-elo-calibration
created: 2026-06-12
tags: [kanban, phase-3, P0, analysis]
---

# 001 — ADCC ELO Calibration

## Goal
`analysis/elo_calibration.py` computes per-fighter ELO from `adcc_historical` parquet using the K-factor scheme in CLAUDE.md; K calibrated against app ELO distribution.

## Context
Blocks [[002-adcc-elo-export|card 002]] (app export). Math borrowed from `felixgnwn/adcc_elo_engine/elo_engine.py` (cite in docstring). Spec already in CLAUDE.md §ELO Engine:
- K = 40 × win_type_mult × stage_mult
- win_type_mult: SUB=1.15, DECISION=0.85, POINTS=1.0
- stage_mult: SPF=1.4, F=1.3, SF=1.2, 3RD=1.15, R2/R1/E1/8F=1.0
- expected = 1/(1 + 10^((elo_b − elo_a)/400)); update elo += K × (score − expected); initial 1000

## Execution Plan
1. `analysis/elo_calibration.py` — replace stubs:
   - `compute_adcc_elo(df: pd.DataFrame, base_k: float = 40.0) -> pd.DataFrame` — pure fn (AGENTS.md rule 5). Input: normalized adcc_historical frame. Sort by `year`, then `match_id` for stable intra-event order. Iterate matches, update winner/loser ELO. Return DataFrame: `fighter, elo, matches, wins, losses, last_year`.
   - Multiplier dicts as module constants; unknown stage/win_type → 1.0 multiplier, log warning.
   - `calibrate_k_factor(df, target_std: float, k_grid: list[float]) -> float` — grid-search base K minimizing |std(elo) − target_std|. Target std comes from app ELO export (caller supplies; default grid 10–80 step 5). **Fallback:** if no app target available yet, skip calibration, keep base_k=40 (engine default) and note it in [[002-adcc-elo-export|card 002]] — calibration is re-runnable later without API change.
2. Wire `analysis/__init__.py` exports.
3. `tests/test_elo.py` — toy 3-fighter sequence with hand-computed expected values; DQ/INJURY treated as POINTS multiplier (1.0); symmetric zero-sum check (sum of deltas per match = 0); calibration returns grid member minimizing objective.
4. Gates: `uv run pytest && uv run ruff check . && uv run mypy .` (mypy strict — full annotations).

## Acceptance Criteria
- [ ] `compute_adcc_elo` reproduces hand-computed ELO on toy data (±1e-6)
- [ ] Runs on real parquet (1,028 matches) without error; top-10 printed in notebook or script smoke check
- [ ] Pure functions — no I/O inside `analysis/`
- [ ] Gates clean

## Test Plan
Synthetic frame: fighters A/B/C, 4 matches, mixed win types/stages. Assert exact ELO values, zero-sum invariant, initial-1000 default, unknown-stage fallback.
