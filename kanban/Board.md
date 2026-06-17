---
tags: [kanban, board]
---

# Board

Live view of the column directories. Requires the **Dataview** community plugin — without it, the blocks below render as code; the directories (`TODO/`, `DOING/`, `DONE/`) are always the source of truth.

## 🔵 TODO

```dataview
TABLE WITHOUT ID file.link AS Card, lane AS Lane, priority AS Prio, phase AS Phase, depends AS "Depends on"
FROM "TODO"
WHERE id
SORT lane ASC, id ASC
```

## 🟠 DOING

```dataview
TABLE WITHOUT ID file.link AS Card, priority AS Prio, branch AS Branch
FROM "DOING"
WHERE id
SORT id ASC
```

## 🟢 DONE

```dataview
TABLE WITHOUT ID file.link AS Card, priority AS Prio, phase AS Phase
FROM "DONE"
WHERE id
SORT id DESC
```

## Ready to Pick — Concurrency Waves

Lanes (A–E) own disjoint files → one agent per lane runs conflict-free. Full lane/file table: [[README]].

| Wave | Run in parallel |
|------|-----------------|
| 1 — **now** | [[001-adcc-elo-calibration\|001·A]] · [[003-bjjheroes-pipeline\|003·B]] · [[004-technique-frequency\|004·C]] · [[006-vicos-download\|006·D]] |
| 2 | [[002-adcc-elo-export\|002·A]] · [[005-belt-analysis\|005·B]] · [[007-vicos-explore\|007·D]] |
| 3 | [[008-pose-features\|008·D]] · [[010-user-benchmark\|010·E]] |
| 4 | [[009-baseline-classifier\|009·D]] · [[011-fighter-similarity\|011·E]] |

Pick rule unchanged: within your lane, lowest `id` whose `depends` are all in `DONE/`.

## Dependency Graph

Open graph view scoped to `tag:#kanban` (pre-configured) — column dirs are color-coded TODO/DOING/DONE.
