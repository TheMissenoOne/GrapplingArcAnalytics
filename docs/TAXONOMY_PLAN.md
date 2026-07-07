# GrapplingArc Taxonomy — Implementation Plan

## Thesis (unchanged from the original proposal)

The taxonomy is a first-class layer of the knowledge graph, not category/subcategory
fields stapled onto techniques. Every technique, position, system, and match event
references one or more taxonomy nodes, so analytics, search, similarity scoring, and
article generation operate on **concepts** rather than hardcoded technique names.

This is the right model. But the original plan skipped the part that makes or breaks
it: **the existing data is messy, and the taxonomy is worthless until the data is
canonicalized against it.** This revision keeps the graph thesis and adds the three
missing layers — canonicalization, coverage reconciliation, and a concrete migration.

---

## What the real data actually looks like

Export analyzed: **400 technique nodes** (`technique_nodes_rows.json`).

### Distribution by current `node_type`

| node_type | count | Maps to taxonomy? |
|-----------|-------|-------------------|
| submission | 113 | ✅ Submission |
| control | 56 | ✅ Control |
| takedown | 41 | ✅ Takedown |
| transition | 39 | ✅ Transition |
| guard | 36 | ✅ Guard |
| pass | 31 | ✅ Pass |
| escape | 28 | ✅ Escape |
| sweep | 23 | ✅ Sweep |
| **strike** | **20** | ❌ no home — MMA artifact |
| concept | 5 | ⚠️ becomes Concept nodes |
| **penalty** | **5** | ❌ no home — scoring event |
| defensive | 2 | ⚠️ fold into Escape/Control |
| **match** | **1** | ❌ no home — event artifact |

267 of 400 nodes are `source: user` (logged from real sessions/matches), 133 are
`source: library` (curated). The user-sourced nodes are where the mess lives.

### The three data problems the original plan ignored

**1. Node types with no taxonomy home (26 nodes).**
`strike`, `penalty`, and `match` are not grappling techniques — they are MMA actions
and match-scoring events that leaked into the technique library. Decision required
(see "Out-of-scope node types" below).

**2. Heavy duplication and aliasing.**
The same technique appears under many labels:

- `Americana` · `Americana (Keylock)` · `Keylock` → one technique
- `Rear Naked Choke` · `Rear Naked Choke Attempt` · `Mata-Leão` · `Neck Crank / Rear Naked Choke` → one technique
- `Triangle` · `Triangle Choke` · `Triangle Attempt` · `Triângulo` → one technique
- `Single Leg` · `Single-leg takedown` · `Single Leg Takedown` · `Single Leg Takedown Attempt` · `Deep Single Leg` · `Snatch Single Leg Takedown` → one technique family
- `Foot Lock` · `Footlock` · `Straight Foot Lock` · `Straight Ankle Lock` · `Straight Ankle Lock Attempt` → ankle lock family
- `Omoplata` · `Omoplata (Shoulder Lock)` · `Omoplata Attempt` · `Omoplata / Triangle Attempt` → one technique

The plan's `aliases` field is exactly the mechanism to solve this — but the plan never
states the rule: **duplicates collapse into one canonical technique node; every variant
spelling becomes an alias, not a separate node.**

**3. "Attempt" and compound event labels.**
Dozens of nodes carry an `Attempt` suffix (`Armbar Attempt`, `Guillotine Attempt`) or
are compound match events (`Katagatame / Gift Wrap Attempt`, `Sweep / Back Take`). These
came from match-breakdown logging, not the technique library. They are **outcomes of a
technique, not distinct techniques.** Policy below.

---

## Data model

Unchanged in spirit from the original, tightened in specifics.

### Taxonomy node

```json
{
  "id": "pressure-pass",
  "name": "Pressure Pass",
  "kind": "subcategory",       // category | subcategory | concept | principle
  "parent": "pass",            // null for top-level
  "aliases": ["smash pass", "stack pass"]
}
```

### Technique node (after migration)

```json
{
  "id": "body-lock-pass",
  "name": "Body Lock Pass",
  "canonical": true,
  "aliases": ["passagem body lock", "body lock pass to mount"],
  "taxonomy": ["pass", "pressure-pass"],
  "concepts": ["pressure", "connection", "inside-position"],
  "principles": ["pressure", "connection", "inside-position"],
  "source": "library"
}
```

Key change from the original: the technique references three **distinct** reference
sets — `taxonomy` (what it *is*), `concepts` (what qualities it *has*), and `principles`
(what it *embodies*). The original blurred concepts and principles together. Keeping them
separate is what lets analytics answer both "how much pressure passing" (taxonomy) and
"how much of your game is pressure-based" (concept) as different questions.

---

## The taxonomy (from your specification)

Generated as `taxonomy.json` — **9 categories, 87 subcategories, 20 concepts, 12
principles**. Your proposed structure is adopted almost verbatim. Notes on the few
adjustments:

- **Grip** is kept as a category as you specified, but see "Grip is not a technique
  category" below — it behaves differently from the other eight and should probably be
  modeled as a Concept/Control dimension rather than a sibling of Submission.
- **Off-Balancing** and **Kuzushi** both appear in your concept list. Kuzushi *is* the
  Japanese term for off-balancing — these are aliases of one concept, not two. Merged,
  with `kuzushi` as an alias of `off-balancing`.
- **Guard Recovery** appears under both Escape and Transition in your structure. That is
  actually correct in a multi-classification model — the same subcategory node is
  referenced by techniques in both families. No change needed; it's one node
  (`guard-recovery`) referenced twice.

### Category → subcategory (as implemented)

```
Control      → Peripheral, Body, Head, Arm, Leg, Clinch, Front Headlock,
               Top, Bottom, Back, Pin
Guard        → Closed, Open, Half, Butterfly, Hook, Sleeve-Based, Collar-Based,
               Lapel, Inverted, Leg Entanglement, Turtle
Pass         → Pressure, Mobility, Standing, Dynamic, Half Guard, Headquarters,
               Leg Weave, Folding
Sweep        → Elevation, Rotation, Off-Balance, Hook, Reversal, Wrestle-Up
Escape       → Positional, Guard Recovery, Submission, Bridge, Hip, Turtle,
               Standing, Scramble
Submission   → Choke, Strangle, Arm Lock, Shoulder Lock, Wrist Lock, Leg Lock,
               Compression Lock, Spine Lock, Neck Crank
Takedown     → Hand Throw (Te-Waza), Hip Throw (Koshi-Waza),
               Foot Sweep/Trip/Reap (Ashi-Waza), Sacrifice Throw (Sutemi-Waza),
               Leg Attack, Body Lock, Clinch Throw, Snap Down, Arm Drag,
               Duck Under, Go Behind, Counter
Transition   → Position Advancement, Position Recovery, Guard Pull, Guard Recovery,
               Back Take, Mount Transition, Leg Entry, Stand Up, Inversion, Scramble
Grip         → Gi Grip, No-Gi Tie, Sleeve, Collar, Pant, Wrist, Two-on-One,
               Underhook, Overhook, Collar Tie, Body Lock, Seatbelt
```

### Choke vs Strangle

Your Submission list separates **Choke** (airway/tracheal) from **Strangle** (blood/
carotid). This is technically correct and worth keeping — it directly powers the
`strangles_over_locks` heuristic. Mapping rule for migration:

- Blood chokes → `strangle`: RNC, triangle, arm triangle, bow and arrow, D'Arce,
  anaconda, guillotine (blood variant), north-south, loop, Ezekiel, cross choke
- Air chokes → `choke`: guillotine (high/air variant), can crank-style
- When ambiguous, default to `strangle` (the vast majority of BJJ "chokes" are blood
  chokes) and flag for review.

---

## Out-of-scope node types — decision required

The 26 nodes in `strike`, `penalty`, `match` do not belong in a grappling taxonomy.
Three options:

| Option | What happens | Recommendation |
|--------|-------------|----------------|
| A | Delete them from the technique library | ❌ loses MMA match-log data |
| B | Move to a **separate `event` node class** outside the technique taxonomy | ✅ recommended |
| C | Extend taxonomy with a `Strike` category + `Match Event` class | ⚠️ only if MMA is a roadmap goal |

**Recommended: Option B.** Strikes, penalties, and match markers are match-log events.
Move them to an `event` node type with their own small taxonomy (`strike/punch`,
`strike/kick`, `event/penalty`, `event/stall`). They stay in the graph for match
breakdowns but never pollute grappling analytics. If MMA becomes a first-class product
later, promote them to Option C with no data loss.

`concept` (5 nodes: Arm Drag, Berimbolo, Lapel Guard, Leg Drag Pass, Worm Guard) and
`defensive` (2 nodes) are mislabeled techniques, not concepts. Re-map:
- `Arm Drag` → Takedown/Transition technique referencing the `arm-drag` subcategory
- `Berimbolo` → Sweep/Transition technique, concepts `inversion`, `back-exposure`
- `Worm Guard`, `Lapel Guard Techniques` → Guard/`lapel-guard`
- `Leg Drag Pass` → Pass/`mobility-pass`
- `Single-leg Defense`, `Sprawl` → Escape or Takedown-defense

---

## Canonicalization layer (the missing step)

Before any technique can reference taxonomy, duplicates must collapse. This is a
one-time migration pass plus an ongoing rule.

### Canonicalization pipeline

```
1. Normalize label
   - lowercase, trim, collapse whitespace
   - strip "Attempt" suffix → set outcome flag instead
   - fold PT→EN via a translation map (Mata-Leão→Rear Naked Choke,
     Triângulo→Triangle, Guarda Fechada→Closed Guard, Montada→Mount,
     Raspagem de Gancho→Hook Sweep, Fuga de Quadril→Hip Escape, etc.)

2. Cluster by canonical form
   - embedding similarity (the export already has embeddings) + string distance
   - nodes above 0.92 cosine similarity are candidate duplicates

3. Elect a canonical node per cluster
   - prefer source:library over source:user
   - prefer the shortest clean English label
   - all other labels become aliases[]

4. Assign taxonomy
   - map canonical node to category + subcategory nodes
   - attach concepts and principles
```

### "Attempt" policy

An `Armbar Attempt` is not a technique — it is an `Armbar` with `outcome: attempt`.
During migration:
- Strip the `Attempt` suffix, resolve to the canonical technique
- The match-log entry that referenced it keeps `outcome: attempt` on the **edge/event**,
  not the node
- This directly feeds the ELO `sequenceScorer` — an attempt is `S = 0.5`, exactly as
  the scorer already expects

### Embeddings help here

The export includes a per-node `embedding` vector. Use it: cluster the 400 nodes in
embedding space and the duplicate families (all the single-leg variants, all the
triangle variants) fall out automatically. This turns a manual dedup slog into a
review-and-confirm task.

---

## Grip is not a technique category

Your taxonomy lists **Grip** as a 9th top-level category alongside Submission, Pass, etc.
But a grip is not a technique the way an armbar is — it is a **control dimension** that
techniques use. `Two-on-One`, `Underhook`, `Collar Tie`, `Seatbelt` are inputs to
techniques, not techniques themselves.

Two ways to model this correctly:
- **As Concepts** (recommended): grips become Concept nodes (`concept/underhook`,
  `concept/two-on-one`) that techniques reference. "Body Lock Pass uses the body-lock
  grip and inside position."
- **As a Control sub-tree**: grips live under Control as a `Grip Control` subcategory.

Either is fine, but Grip-as-a-sibling-of-Submission creates a category that no technique
cleanly *belongs* to — every grip node would be a leaf with no techniques under it,
because techniques *reference* grips, they aren't *classified as* grips. Recommend
modeling grips as Concepts.

---

## Multiple classification (kept from original)

A technique references as many taxonomy, concept, and principle nodes as it needs.

```
Body Lock Pass
  taxonomy:   [pass, pressure-pass]
  concepts:   [pressure, connection, inside-position, body-control]
  principles: [pressure, connection, inside-position]

Kimura
  taxonomy:   [submission, shoulder-lock, arm-control]
  concepts:   [upper-body, inside-position, connection]
  principles: [upper-body-isolation, connection]
```

This is what lets the same technique surface under "pressure passing," "body control,"
and "connection-based systems" simultaneously.

---

## What the taxonomy unlocks (kept, now grounded)

**Analytics by concept, not label.** Instead of `Kimura: 17, Americana: 6, Omoplata: 4`,
the graph answers `Shoulder Locks: 27 attempts, 56% success`. This only works *after*
canonicalization collapses the 6 Americana spellings into one node.

**Search.** Searching "pressure passing" returns techniques, systems, athletes, projects,
and match breakdowns through shared taxonomy references.

**Grapple Like similarity.** Systems compare by taxonomy overlap, not exact technique
match — so a body-lock passer and a smash passer read as similar (both `pressure-pass`)
even with zero identical technique nodes.

**Procedural article generation.** "Gordon relied primarily on pressure-based passing,
using body control to restrict decision space" instead of listing raw technique names.

**Extensibility.** Adding Judo/Sambo/Catch = adding taxonomy nodes + mapping techniques.
No analytics, UI, or recommendation code changes.

---

## Migration plan

| Phase | Task | Output |
|-------|------|--------|
| 1 | Load `taxonomy.json` as taxonomy nodes in the graph | 128 taxonomy nodes live |
| 2 | Build PT→EN translation map + `Attempt` strip rules | canonicalization config |
| 3 | Cluster 400 nodes by embedding + string distance | duplicate candidate report |
| 4 | Human review: confirm canonical node per cluster | canonical technique list |
| 5 | Collapse duplicates → aliases; strip Attempt suffixes | ~150–200 canonical techniques (from 400) |
| 6 | Move strike/penalty/match to `event` node class | clean technique library |
| 7 | Auto-assign taxonomy via node_type + label rules | techniques reference categories |
| 8 | Human review: concepts + principles per technique | full semantic layer |
| 9 | Rewire analytics/search/GrappleLike to query taxonomy | concept-level features live |

Phases 3–5 are the real work. Everything downstream depends on the technique count
dropping from 400 noisy nodes to a clean canonical set.

---

## Acceptance criteria

- [ ] `taxonomy.json` loaded — 9 categories, 87 subcategories, concepts, principles
- [ ] Zero duplicate technique nodes (all variants are aliases of a canonical node)
- [ ] No `Attempt`-suffixed technique nodes (attempts are edge outcomes)
- [ ] Portuguese labels resolved to canonical English + PT alias retained
- [ ] `strike` / `penalty` / `match` nodes removed from technique taxonomy
- [ ] Every canonical technique references ≥1 taxonomy node
- [ ] Analytics can aggregate by subcategory (e.g. "Shoulder Locks" totals)
- [ ] Adding a new subcategory requires no code change
