# Deepseek QA — client-side discovery + landing repositioning

Public site (`GrapplingArc/site/`, static, dependency-free). New client-side faceted
discovery over the existing data globals + a repositioned landing page.

## Inputs (attach)
- `site/search.js` (the GADiscover module)
- `site/breakdowns.html`, `site/grapple-like.html` (rewired listings)
- `site/index.html` (repositioned landing)
- `site/site.css` (discovery + responsive styles)

## Discovery checks (search.js + listings)
1. **No backend / no deps.** Confirm everything runs client-side over `GA_BREAKDOWNS` /
   `GA_FIGHTERS` (techniques from `graph.nodes[].label`). No fetch/XHR/CDN.
2. **Facet logic.** OR within a facet, AND across facets. A facet with <2 distinct values
   is hidden. Verify the breakdowns technique facet is populated from graph nodes.
3. **URL state.** Search/facets/sort write to the query string and restore on reload +
   back-button (`?technique=…`, `?q=…`, `?sort=…`). Deep links like
   `breakdowns.html?technique=Butterfly%20Guard` filter correctly.
4. **Canvas virtualization (the perf fix).** A graph is mounted only while its card is
   near the viewport (IntersectionObserver) and `destroy()`d when it leaves — confirm the
   page does NOT mount a live force-graph for every card up front. Scroll 300+ cards →
   only a handful of live canvases at once.
5. **Pagination.** Initial render is one page (18 / 24); "Load more" appends the next.
6. **The 7 questions** now answerable: all Gordon Ryan bouts (search), Butterfly Guard
   breakdowns (technique facet / `?technique=`), ADCC 2024 (event facet), guard players
   (grapple-like archetype facet), etc.

## Landing checks (index.html)
7. **Repositioning.** Hero eyebrow "Become your own archetype"; journey section "From
   their game to yours" (Study → See the system → Build your own archetype); no `01/02/03`
   markers; ≤2 em-dashes in body copy.
8. **Counts honest:** 297 matches / 1,300+ fighters / 32 dossiers (match the real data).
9. **Waitlist.** Zero-backend mailto capture; submitting a valid email opens a prefilled
   mail draft and reveals the confirmation; `data-to` is the single config point.
10. **Nav + footer.** Mobile header = brand + hamburger only (lang + app inside the
    dropdown); footer has Privacy + Data & Deletion links; no horizontal scroll at 320/375.

## Output
`<check#>: PASS|FAIL — reason`. Flag any facet that never populates, any card that mounts
a graph while off-screen, any deep link that doesn't filter, and any dishonest count.
