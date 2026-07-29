---
id: "018"
slug: site-exporter-branding
phase: 6
lane: H
priority: P1
status: doing
depends: []
branch: feature/radial-brand-exporter
created: 2026-07-29
tags: [kanban, phase-6, P1, export]
---

# 018 — Site Exporter Branding

## Goal
Generated site chrome uses the official radial asset contract without duplicating the GA monogram beside the GrapplingArc wordmark.

## Context
Class (a): exporter-only markup. Site assets and generated HTML regeneration land in the paired public-site PR; no data shape, DB, or storage contract changes.

The local database is incomplete, so a full export would prune hundreds of valid generated pages. A branding-only migration must update existing generated details without opening a DB session, pruning, or rewriting data bundles.

## Execution Plan
1. Add pure exporter tests for navigation, footer, head assets, athlete OG preservation, and dossier app strip.
2. Replace generated chrome markup with decorative `brand-symbol.svg` plus one accessible `GrapplingArc` label.
3. Use `brand-og.png` by default and `brand-mark.svg` for favicon; preserve explicit athlete OG URLs.
4. Remove the redundant dossier GA orb.
5. Add an explicit `--branding-only --out <site>` migration for existing generated detail HTML only.
6. Run focused pytest and Ruff. Do not run the full site exporter.

## Acceptance Criteria
- [ ] Generated lockups contain no textual GA monogram.
- [ ] Default SEO assets use the agreed brand assets.
- [ ] Athlete-specific OG image remains unchanged.
- [ ] Branding-only migration is idempotent, preserves unrelated bytes, and bypasses the database.
- [ ] Focused tests and Ruff pass.

## Test Plan
Pure string assertions against `_nav`, `_FOOTER`, `_head`, and generated dossier output; no database or network.
