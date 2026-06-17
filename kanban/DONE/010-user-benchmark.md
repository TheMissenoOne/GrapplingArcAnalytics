---
id: "010"
slug: user-benchmark
phase: 5
lane: E
priority: P2
status: done
depends: ["[[001-adcc-elo-calibration]]", "[[004-technique-frequency]]"]
branch: feature/010-user-benchmark
created: 2026-06-12
tags: [kanban, phase-5, P2, analysis]
---

# 010 — User vs Pro Benchmarking

## Goal
`analysis/benchmark.py` + `export/benchmark_results.py`: compare a user's training data (UserBundle) against ADCC pro baselines → importable app JSON.

## Context
User side parsed by `schemas/app_types.UserBundle.from_json()` (already implemented — verify against current app `mock_user_bundle.json` first). Pro side: sub frequency ([[004-technique-frequency|card 004]]) + ELO context ([[001-adcc-elo-calibration|card 001]]).

## Execution Plan
1. Verify `UserBundle` parser against fresh `mock_user_bundle.json` from GrapplingArcApp; update `schemas/app_types.py` if app types drifted.
2. `analysis/benchmark.py`, pure:
   - `user_submission_profile(bundle) -> pd.DataFrame` — per-technique attempt/success counts from session rounds (`actor == "you"`).
   - `pro_baseline(adcc_df) -> pd.DataFrame` — ADCC sub distribution via [[004-technique-frequency|card 004]] fns.
   - `compare(user_profile, pro_baseline) -> pd.DataFrame` — per-technique: user share vs pro share, ratio, percentile; name-match via shared alias helpers.
3. `export/benchmark_results.py` — `export_benchmark_results(bundle_path) -> dict` summary-dict pattern; writes `data/processed/benchmark_results.json` (new importable format per CLAUDE.md).
4. `tests/test_benchmark.py` — fixture bundle JSON (mini, 2 sessions) + synthetic adcc → compare output values hand-checked; unknown-technique handling (user logs technique absent from ADCC → flagged, not dropped).
5. Gates clean.

## Acceptance Criteria
- [ ] Round-trips real mock_user_bundle.json
- [ ] Unknown techniques surfaced w/ `no_pro_data` flag
- [ ] JSON output documented (schema comment in module)
- [ ] Gates clean

## Test Plan
Fixture bundle: 5 rounds, 3 techniques, 1 not in ADCC. Assert counts, shares, flag, JSON keys.
