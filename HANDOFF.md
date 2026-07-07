# Handoff — QA Fix Implementation

## What's Done

Full-site QA audit completed. Findings in `FINDINGS.md` (19 issues, P0–P3).
Implementation plan in `docs/qa_implementation_plan.md`.

## What to Do

Implement fixes per `docs/qa_implementation_plan.md`. Tickets F1–F14, Lane F.

**Start with P0s (tickets F1, F2, F8, F9):**

### F1 — Missing stats (P0)
- `analysis/names.py` — identify ~14 zero-stat fighters from breakdown dataset, add `ATHLETE_ALIASES` entries
- `export/site_data.py:render_breakdown_page` — add fallback banner when `all(v == 0 for v in stats["a"].values())`

### F2 — ELO crash (P0)
- `export/site_data.py:608` — change `f["name"]` → `f.get("name", "unknown")`

### F8 — Jekyll scaffolding (P0)
- Create `_layouts/`, `_includes/`, `_posts/` with minimal Jekyll scaffolding, or remove Jekyll dependency and deploy `site/` directly
- Update or remove `_config.yml` to match actual pipeline

### F9 — CI/deploy workflow (P0)
- Create `.github/workflows/deploy.yml` that publishes `site/` to GitHub Pages (no Jekyll build needed — exporter pre-builds everything)
- Delete `_config.yml` if Jekyll is unused

**Then P1s (F3, F4, F10, F12), then P2s (F5, F6, F11), then P3s (F7, F13)**

## Files Map

| File | Tickets |
|---|---|
| `export/site_data.py` | F1, F2, F3, F5, F7 |
| `analysis/names.py` | F1, F4 |
| `site/search.js` | F6 |
| `site/site.css` | F6, F7 |
| `site/logo.svg` | F11 |
| `GrapplingArc/index.html` | F13 |
| `GrapplingArc/_config.yml` | F8, F9 |
| `GrapplingArc/.github/workflows/` | F9 |
| `GrapplingArc/_layouts/`, `_includes/`, `_posts/` | F8 |

## Test Before PR

```bash
uv run pytest tests/
uv run ruff check export/site_data.py analysis/names.py
uv run python -m export.site_data --out /tmp/site-test
# Verify /tmp/site-test has correct breakdowns, no crashes
```

## Key Context

- `site/` is pre-built by `export.site_data` — no Jekyll build needed
- events-data.js is auto-generated; fix the generator, not the output
- fighter OG images: add fallback to logo.svg if fighter JPG missing at export time
- logo.svg: replace with optimized version, target <2KB

## Files to Read First

- `FINDINGS.md` — full issue descriptions
- `docs/qa_implementation_plan.md` — ticket breakdown + dependency map
- `export/site_data.py` — main generator, most tickets touch this
- `analysis/names.py` — athlete alias map
