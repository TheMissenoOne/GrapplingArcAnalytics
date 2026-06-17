---
id: "006"
slug: vicos-download
phase: 4
lane: D
priority: P1
status: doing
depends: []
branch: feature/006-vicos-download
created: 2026-06-12
tags: [kanban, phase-4, P1, cv]
---

# 006 — ViCoS Dataset Downloader

## Goal
`cv/vicos_download.py` downloads + verifies the ViCoS jiu-jitsu dataset (120,279 images, ~14 GB, JSON COCO-17 keypoint annotations) into `cv/vicos_data/` (gitignored).

## Context
Phase 4 entry point. Source: https://vicos.si/resources/jiujitsu/ — check license/terms before scripted download; if no direct URL, document manual step + verify-only mode. Annotations alone (~MBs) suffice for [[007-vicos-explore|card 007]] + [[008-pose-features|card 008]] — images only needed for visual spot-checks. Download annotations first, images behind optional flag. **Disk:** full image set ~14 GB — check free space before `download_images`, abort w/ clear message if < 20 GB.

## Execution Plan
1. Inspect ViCoS page → record actual download URLs/archive layout in card + CLAUDE.md §ViCoS.
2. `cv/vicos_download.py`:
   - `download_annotations(dest: Path = VICOS_DIR) -> Path` — fetch + extract annotation JSON; skip if present; checksum if provided upstream.
   - `download_images(dest, subset: float | None = None) -> Path` — optional, resumable (requests + Range), `subset` for sampled fraction.
   - `verify(dest) -> dict` — counts: images found, annotation entries, missing files.
   - CLI: `uv run python -m cv.vicos_download --annotations-only`.
3. `tests/test_vicos_download.py` — `verify()` on a tiny fixture tree; URL/path constants sanity. No network in tests.
4. Gates clean.

## Acceptance Criteria
- [ ] Annotations downloaded + parsed count matches published 120,279 (or documented discrepancy)
- [ ] Re-run = no-op (idempotent)
- [ ] License/terms noted in module docstring
- [ ] Gates clean

## Test Plan
Fixture dir w/ 2 fake images + mini annotation JSON → `verify()` returns exact counts + missing list.
