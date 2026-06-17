---
id: "002"
slug: adcc-elo-export
phase: 3
lane: A
priority: P0
status: done
depends: ["[[001-adcc-elo-calibration]]", "[[004-technique-frequency]]"]
branch: feature/002-adcc-elo-export
created: 2026-06-12
tags: [kanban, phase-3, P0, export]
---

# 002 — ADCC ELO Table Export

## Goal
`export/adcc_elo_table.py` produces JSON matching app `@grapplingarch:elo_stats` AsyncStorage format from [[001-adcc-elo-calibration|card 001]] output.

## Context
App integration contract in CLAUDE.md §App Integration. Mirror the pattern of `export/tech_library.py` (load pipeline → transform → write JSON to `data/processed/` → summary report). Cross-reference fighter names against `adcc_fighters` dataset for career stats enrichment.

## Execution Plan
1. Inspect app ELO shape: read GrapplingArcApp AsyncStorage type for `elo_stats` (TS source). Mirror fields in `schemas/app_types.py` dataclass if missing.
2. `export/adcc_elo_table.py`:
   - `export_adcc_elo_table() -> dict[str, Any]` entry point, same summary-dict pattern as `export_tech_library`.
   - Load `adcc_historical` via pipeline, call `compute_adcc_elo`, join `adcc_fighters` on normalized name via `analysis/names.py` (extracted by [[004-technique-frequency|card 004]] — hence the dependency; avoids two concurrent cards refactoring `export/tech_library.py`).
   - Output `data/processed/adcc_elo_table.json`: ranked list `{fighter, elo, matches, wins, losses, titles?, sub_ratio?, weight_class?}`.
   - Name-join misses logged, not fatal; report join hit-rate in summary.
3. `tests/test_elo_export.py` — synthetic elo frame + fighters frame → assert JSON shape, ranking order, join behavior (hit + miss).
4. Regenerate output, eyeball top-20 vs known ADCC greats (Gordon Ryan, Galvão sanity check).
5. Gates clean.

## Acceptance Criteria
- [ ] JSON validates against app shape (field-for-field)
- [ ] Join hit-rate reported; misses logged with names
- [ ] Tests no-network
- [ ] Gates clean

## Test Plan
Two synthetic frames, 3 fighters, 1 name-miss. Assert sorted desc by elo, miss entry lacks enrichment fields, summary counts correct.
