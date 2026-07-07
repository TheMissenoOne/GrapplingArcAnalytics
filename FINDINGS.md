# QA Findings — GrapplingArc Site

Generated 2026-07-06 from full-site audit: 102 breakdown pages, 14 fighter dossiers,
4 event pages, The Ocean, ELO leaderboards. Updated 2026-07-07 with pipeline + data-leakage findings.

---

## 1. Data: Missing Stats (Root Cause)

**`export/site_data.py` — breakdown page stat computation**

~14 breakdowns show all-zero stats for one fighter because their name alias
doesn't resolve to the source `Athlete` record. When `stats["a"]` or
`stats["b"]` returns all zeros, the breakdown still renders (empty stat grid
cells).

**Files:** `export/site_data.py:render_breakdown_page` (line 623+); the stats
come from `build_breakdowns()` → `bout_stats()` which joins on `athlete_key`.

**Fix:** Either (a) detect zero-stat fighter at render time and emit a
"Stats unavailable — fighter not yet matched to athlete record" banner, or
(b) add the missing aliases to `analysis/names.py:ATHLETE_ALIASES` so their
bouts get stats on the next export.

---

## 2. Heading Hierarchy Gaps

**`export/site_data.py` — generated detail pages**

Breakdown page (`render_breakdown_page`) uses:
- `<h1>` for the bout title (correct)
- `<div class="sec-label">` for section headings (NOT `<h2>`)
- No `<h2>` or `<h3>` elements anywhere → the page has no heading outline.

Dossier page (`render_profile_page`) uses:
- `<h1>` for athlete name
- `<h2>` inside "The systems" section (line 881), "Defense" (line 923),
  "Counter moves" (line 948)
- But "Signature game", "Response patterns", "Finishing profile",
  "From abstract to concrete" use `<h2>` only via `.h-lg` styling on a `<div>`
  — not semantic heading elements.

Event page (`render_event_page`) uses:
- `<h1>` for event name
- No `<h2>` or `<h3>` — "Every bout" uses `<div class="sec-label">`.

**Impact:** Screen readers and SEO crawlers see no heading hierarchy.
`h1` → `div` with no intervening `h2`.

**Fix:** Replace `<div class="sec-label">Heading</div>` with
`<h2 class="sec-label">Heading</h2>` in all three render functions. Keep
styling; change only the element tag.

---

## 3. Athlete Dedup Gaps

**`analysis/names.py:ATHLETE_ALIASES`**

Verified ~200 distinct athlete keys in the export. Found candidates still
missing from `ATHLETE_ALIASES`:

| DB key | Likely canonical | Status |
|---|---|---|
| `pig` | ? (unknown) | Unresolved; 0-link alias |
| `guto` | ? (unknown) | Unresolved; 0-link alias |
| `gordao` | ? (unknown) | Unresolved; 0-link alias |
| `daniel manoel` | `dan manasoiu` | Typo variant, not aliased |
| `thiago rela` | ? (unknown) | Unresolved |
| `joshua cisneros` | `josh cisneros` | Double first-name variant |

Each unresolved alias means one fighter's stats don't accumulate into their
dossier — some athletes with 3+ wins still show 0-link dossiers.

**Fix:** For each resolved case, add to `ATHLETE_ALIASES`. For truly unknown
`pig`/`guto`/`gordão`/`thiago rela`, investigate the source transcript to
confirm real name.

---

## 4. ELO Display Formatting

**`export/site_data.py` — `sig_card` (line 645)**

`sig_card` shows:
- `elo_pct` → `"Top X%"` (percentile within discipline pool)
- `elo_delta_pct` → `"▲ X.X% this bout"` (raw percentage points, not
  relative)

The delta label says "%" but `elo_delta_pct` is already in percentage point
units. If `elo_delta_pct = 5.2`, the display shows `"▲ 5.2% this bout"` —
which is a percentage point change, not a percent change. This is misleading
when `elo_pct` is also displayed as a percentage.

**Fix:** Change delta label to `"▲ X.Xpp this bout"` (for "percentage points")
or compute a relative change from `elo_pct` before/after.

---

## 5. ELO Null-Safe Crash in `_train_this_style`

**`export/site_data.py:_train_this_style` (line 601)**

The function accesses `f["name"]` for both `a` and `b` fighter dicts. If a
fighter dict is missing the `name` key (e.g., ELO data incomplete), the
`slugify(f["name"])` call on line 608 raises `KeyError`.

**Impact:** A single missing ELO name field kills the entire breakdown page
generation for that bout.

**Fix:** Use `f.get("name", "unknown")` instead of `f["name"]` on line 608,
and wrap `slugify` in a try/except or use a default.

---

## 6. Filter UX: No `<label>` on `<select>`

**`site/search.js` — `GADiscover._render()`**

The sort `<select>` element (line 237) has no associated `<label>`. Screen
readers see an unlabeled combobox.

**Fix:** Add `<label class="sr-only" for="sort-select">Sort by</label>` + `id`
on the `<select>`, or use `aria-label="Sort by"`.

---

## 7. Filter UX: Tag Buttons Missing `aria-pressed`

**`site/search.js` — chip-t buttons**

Facet chips (`.chip-t`) are `<button>` elements that toggle on/off via the
`.on` CSS class, but they don't update `aria-pressed` attribute.

**Fix:** In `Discover.prototype._chip` (line 151), set
`c.setAttribute('aria-pressed', String(active))` and update it in
`_syncChips`.

---

## 8. Filter Bar Layout Break at <480px

**`site/site.css` — `.facetsbar`**

At viewports narrower than 480px, `.facetsbar` wraps each facet group onto a
new line but the groups still show as a wall of buttons with no visual
grouping. The `<label>`/.lbl text is tiny (10px) and easily missed.

**Fix:** Add `@media(max-width:480px){ .facet-group{width:100%} }` so each
facet group takes a full row.

---

## 9. Responsive: Breakdown `.corner` Avatar Stack

**`site/site.css` — `.bout` grid at `max-width:900px`**

The VS bout hero switches to single-column, but `.corner .av` (84×84px
initials box) stays large. On 375px screens the avatars + name text reach the
viewport edge with only 14px padding.

**Fix:** Add a `max-width:375px` rule to scale `.corner .av` down to 56px.

---

## 10. Responsive: Dossier Radar Canvas Overflow

**`site/site.css` — `.radar-wrap`**

The radar canvas is hardcoded `width:320` in the JS (`render_profile_page`
payload, line 810 + `_PROFILE_JS` line 738). The wrap has
`max-width:100%;overflow:hidden` so it clips rather than scales on small
screens.

**Fix:** Use a relative width in JS: read `wrap.clientWidth` and cap at 320
instead of drawing at fixed 320 and clipping. Already partially done (line
738 gets `wrap.clientWidth`) but the canvas `width` attr is still hardcoded.

---

## 11. Missing Alt Text on Hero Images

**`export/site_data.py` — `render_profile_page`**

The hero background image (`hero-bg` div, line 900) uses
`background-image:url(...)`. No `role="img"` or `aria-label` is set.
Background images are invisible to screen readers.

**Fix:** Add `role="img" aria-label="Photo of {fighter name}"` to the
`.hero-bg` div.

---

## 12. Inconsistent `h1` vs Page Title

**`export/site_data.py:_head` (line 492)**

The `<title>` is `"{title} — GrapplingArc"` while the visible `<h1>` on
detail pages matches only `title`. On index pages (breakdowns.html, events.html,
grapple-like.html), the `<title>` and `<h1>` match. OK, verified consistent.

---

---

## 13. Jekyll Scaffolding Missing (CRITICAL)

**`_config.yml` expects `_layouts/`, `_includes/`, `_posts/`**

`_config.yml` (line 16–21) sets `layout: breakdown` as default for posts and
references `jekyll-feed`/`jekyll-seo-tag` plugins. But none of these
directories exist on disk:

```
$ ls _layouts _includes _posts
ls: cannot access: No such file or directory
```

A CI run with `actions/jekyll-build-pages@v1` would produce an empty or
broken `_site/`. The AGENTS.md references a Jekyll workflow that cannot
execute with current scaffolding.

**Files:** `/GrapplingArc/_config.yml`

---

## 14. CI/Deploy Workflow Misaligned (CRITICAL)

**`_config.yml` builds to `_site/` but content lives in `site/`**

No `.github/workflows/*.yml` exists on disk. The Jekyll config targets
`_site/` as the build output, but the actual generated content lives in
`site/` (pre-built by `export.site_data`). There is no `_site/` directory.

The deploy path is unclear: Jekyll expects to build → `_site/`, but the
exporter writes directly to `site/`. These two pipelines are not coordinated.

**Files:** `_config.yml`, `.github/workflows/` (missing)

---

## 15. events-data.js Data Leakage

**`site/events-data.js` — 4 sub-issues**

| Issue | Detail |
|---|---|
| **Unmatched parens** | `"Match 7 (Hunter Colvin vs Ellis Younger"` and `"Match 1 (Jon Blank"` — missing closing `)` |
| **Empty headlines** | `polaris-18`, `ufc-324`, `ufc-327`, `ufc-328` have `"headline": ""` |
| **YouTube descriptions** | `"Rafael Fiziev: This fight is featured at the beginning of the video"`, `"Khalil Rountree Jr.: Starts at"`, `"Du Plessis (Middleweight Championship)"` — raw YouTube metadata leaked into athlete name arrays |
| **Typo names** | `"Diego P"` (likely "Diego Pato"), `"Joo Hayes"` (likely "Joao Hayes") |

**Root cause:** Event `headline_bout` extraction and `headliners` selection
don't sanitize or validate against athlete records. YouTube description text
parsed as if it were athlete names. Parentheses from match labels leak
unclosed. Short/truncated names pass through unvalidated.

**Files:** `export/site_data.py:build_events` (or the upstream event profiler)

---

## 16. Logo SVG Bloat

**`site/logo.svg` — 68KB, 249 path elements, 121 empty**

```
249  d="..." paths
121  d="" paths (empty — no visual effect, pure bloat)
68KB file size
```

AI-generated SVG with massive embedded path data for what should be a simple
"GA" wordmark + arc icon. The empty paths double as padding but add no visual
value. On a slow connection this blocks the hero section from rendering.

**Fix:** Rebuild logo as optimized SVG (manual or via
svgo/svgoptimizer). Target: <2KB, <10 paths.

**Files:** `site/logo.svg`

---

## 17. Fighter OG Images 404

**`site/assets/fighters/` — only 5 images exist**

```
arman-tsarukyan.jpg
georges-stpierre.jpg
gordon-ryan.jpg
khamzat-chimaev.jpg
LICENSES.md
README.md
```

All ~100 breakdown pages and ~14 dossier pages reference
`assets/fighters/{slug}.jpg` in their OG `meta[property=og:image]` tags.
Social share previews (Twitter/X, Discord, WhatsApp) will fetch these URLs
and get 404s for 95%+ of pages.

**Fix:** Either (a) generate fighter images via the export pipeline (e.g.,
thumbnail from video keyframe) or (b) add a fallback OG image path in
`_head()` that redirects to a default when the fighter JPG doesn't exist
on disk at export time.

**Files:** `export/site_data.py:_head` (OG `meta` tag generator),
`site/assets/fighters/`

---

## 18. Canonical URL Uses Relative Path

**`/GrapplingArc/index.html` root redirect**

Line 8:
```html
<link rel="canonical" href="site/index.html"/>
```

This is a relative path `site/index.html` instead of an absolute URL like
`https://themissenoone.github.io/GrapplingArc/site/index.html`. Search
engines may interpret the relative canonical differently depending on the
served page URL.

All other generated pages use absolute canonical URLs via `_head()` with
`SITE_BASE + "/" + path`. Only this root redirect page is hand-written and
missing the pattern.

**Fix:** Change to `href="https://themissenoone.github.io/GrapplingArc/site/index.html"`

**Files:** `/GrapplingArc/index.html`

---

## 19. `_site/` Doesn't Exist

**Deploy workflow target directory is absent**

`_config.yml` implies Jekyll builds to `_site/` (default), but no `_site/`
directory exists anywhere under `/GrapplingArc/`. The exporter writes to
`site/`. If CI runs `jekyll build`, it creates `_site/` with whatever Jekyll
can gather from the sparse scaffolding — likely an empty or broken site.

**Fix:** Either (a) align the exporter to write to `_site/` and configure
the deploy action to publish `_site/`, or (b) configure the deploy action to
publish `site/` directly (no Jekyll build step needed since the exporter
pre-builds everything).

**Files:** `_config.yml`, `.github/workflows/` (missing)

---

## Priority Order

| Priority | Issue | Batch |
|---|---|---|
| **P0** | Missing stats (root cause) — breaks data accuracy | F1 |
| **P0** | ELO null crash `_train_this_style` — kills generation | F2 |
| **P0** | Jekyll scaffolding missing — CI would produce empty site | F8 |
| **P0** | CI/deploy workflow misaligned — no pipeline coordination | F9 |
| **P1** | Heading hierarchy — SEO + a11y | F3 |
| **P1** | Athlete dedup gaps — missing dossiers | F4 |
| **P1** | events-data.js data leakage — 4 sub-issues | F10 |
| **P1** | Fighter OG images 404 — social previews broken | F12 |
| **P2** | ELO delta label (% vs pp) — misleading display | F5 |
| **P2** | Missing `<label>` on sort select — a11y | F6 |
| **P2** | Missing `aria-pressed` on facet chips — a11y | F6 |
| **P2** | Logo SVG bloat — 68KB, 121 empty paths | F11 |
| **P3** | Filter bar layout <480px | F7 |
| **P3** | Avatar stack size at 375px | F7 |
| **P3** | Radar canvas clip at small screens | F7 |
| **P3** | Missing alt-text on hero backgrounds | F7 |
| **P3** | Canonical URL uses relative path | F13 |
| **P3** | `_site/` doesn't exist | F9 |
