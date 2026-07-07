# QA Fix — Implementation Proposal

Source: `FINDINGS.md` (12 findings, P0–P3). Fixes touch `export/site_data.py`,
`analysis/names.py`, `site/search.js`, `site/site.css`, static `.html` pages.

**New lane required** — none of A–E cover `export/site_data.py` or `site/`.
Propose **Lane F** (`export/site_data.py`, `site/*.html`, `site/search.js`,
`site/site.css`).

Cards ordered by priority (P0 first), sequentially within the lane.

---

## Ticket F1 — Fix Missing Stats (P0)

**Files:** `analysis/names.py` (aliases) or `export/site_data.py` (fallback UI)

Two options:
- **A** (recommended): Add missing `ATHLETE_ALIASES` entries so zero-stat
  fighters resolve on re-export. Requires identifying all ~14 anonymous
  fighters from the breakdown dataset.
- **B**: Emit "Stats unavailable — fighter not matched" banner in
  `render_breakdown_page` when `all(v == 0 for v in stats["a"].values())`.

Do both: A fixes the root, B is a safety net.

**Estimate:** 1–2h (A: data investigation of 14 breakdowns; B: ~10 lines).

---

## Ticket F2 — ELO Null Crash in `_train_this_style` (P0)

**File:** `export/site_data.py:601-620`

`sig_card(f["name"])` raises `KeyError` when ELO dict lacks `name` key.
Fix: `f.get("name", "unknown")` on line 608.

**Estimate:** 5 min.

---

## Ticket F3 — Heading Hierarchy (P1)

**File:** `export/site_data.py` — `render_breakdown_page`, `render_profile_page`,
`render_event_page`

Replace `<div class="sec-label">` → `<h2 class="sec-label">` in all three
functions. Also audit dossier page — some `<h2>` are styled `<div>`s.

**Affects:** `site_data.py:525`, `render_breakdown_page` template
(~8 occurrences), `render_profile_page` template (~6 occurrences),
`render_event_page` template (~1 occurrence).

**Estimate:** 30 min.

---

## Ticket F4 — Athlete Dedup Gaps (P1)

**File:** `analysis/names.py:ATHLETE_ALIASES`

Add 5–6 confirmed aliases from FINDINGS.md table. Requires:
1. Confirm each candidate via transcript source or bout data.
2. Add entry + comment.
3. Re-export and verify stats populate.

**Estimate:** 1h (mostly investigation).

---

## Ticket F5 — ELO Delta Label (P2)

**File:** `export/site_data.py:sig_card` (line 645)

Change delta label text from `"▲ X.X% this bout"` to `"▲ X.Xpp this bout"`
(or compute relative change from pre/post `elo_pct`).

**Estimate:** 5 min.

---

## Ticket F6 — Filter/Discovery UX (P2)

**Files:** `site/search.js`, `site/site.css`

1. Add `<label>` with `aria-label="Sort by"` to sort `<select>`.
2. Add `aria-pressed` attribute to facet chip toggles.
3. `@media(max-width:480px){ .facet-group{width:100%} }` for filter layout.

**Estimate:** 20 min.

---

## Ticket F7 — Responsive Polish (P3)

**Files:** `site/site.css`, `export/site_data.py`

1. `.corner .av` size reduction at 375px (84→56px).
2. Radar canvas: ensure JS uses `wrap.clientWidth` (already reads it,
   but canvas `width` attr is hardcoded 320).
3. Hero BG `role="img"` + `aria-label`.

**Estimate:** 30 min.

---

## Dependency Map

```
F1 (P0 - data)
  ↓
F2 (P0 - crash)
  ↓
F3 (P1 - heading)  F4 (P1 - aliases, runs parallel with F3)
  ↓                    ↓
F5 (P2 - ELO label)  F6 (P2 - filter UX)
  ↓
F7 (P3 - polish)
```

F1/F2 independent of each other (both P0, could be one card).
F3–F7 have no hard deps — re-export needed to verify F1, F3, F4.

---

## Proposed Concurrency (Lane F, max 1 agent)

| Wave | Card | Files |
|------|------|-------|
| 1 | F1 + F2 (P0 pair) | `analysis/names.py`, `export/site_data.py` |
| 2 | F3 (heading) + F4 (aliases) | `export/site_data.py` + `analysis/names.py` |
| 3 | F5 (ELO label) + F6 (filter UX) | `export/site_data.py` + `site/*` |
| 4 | F7 (responsive polish) | `site/site.css`, `export/site_data.py` |

---

## Kanban Cards Needed

Create `kanban/TODO/012-qa-fix-missing-stats.md` through
`kanban/TODO/018-qa-responsive-polish.md` using `_template.md`. All in
**lane F**, tags `[kanban, phase-6, P0–P3, export]`.

New lane entry for `kanban/README.md`:

| Lane | Cards | Files owned |
|------|-------|-------------|
| F | 012–018 | `export/site_data.py`, `site/*.html`, `site/search.js`, `site/site.css`, `analysis/names.py` |
