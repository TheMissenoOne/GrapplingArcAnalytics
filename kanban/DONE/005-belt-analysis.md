---
id: "005"
slug: belt-analysis
phase: 3.5
lane: B
priority: P2
status: done
depends: ["[[003-bjjheroes-pipeline]]", "[[004-technique-frequency]]"]
branch: feature/005-belt-analysis
created: 2026-06-12
tags: [kanban, phase-3-5, P2, analysis]
---

# 005 — Belt-Level Analysis

## Goal
`analysis/belt_analysis.py` joins BJJ Heroes belt/team data with ADCC results → stats per belt rank and team.

## Context
Blocked on [[003-bjjheroes-pipeline|card 003]] (only source of belt data). Answers: win-type mix by belt-era, team dominance over time, debut-to-medal lag.

## Execution Plan
1. `analysis/belt_analysis.py`:
   - `join_fighters(adcc_df, heroes_df) -> pd.DataFrame` — normalized-name join (shared helpers from [[004-technique-frequency|card 004]]), report hit-rate.
   - `team_dominance(joined) -> pd.DataFrame` — wins/medals per team per year.
   - `win_type_by_team(joined) -> pd.DataFrame` — sub vs points vs decision mix.
2. `tests/test_belt_analysis.py` — synthetic frames, join hit/miss, aggregation correctness.
3. Notebook section w/ real-data sanity (expect Atos/AOJ/Danaher-era clusters).
4. Gates clean.

## Acceptance Criteria
- [ ] Join hit-rate ≥ 60% on real data (log actual; tune aliases if below)
- [ ] Pure functions, no I/O
- [ ] Gates clean

## Test Plan
3 fighters, 2 teams, 1 name-miss. Assert per-team aggregates + miss excluded + hit-rate value.
