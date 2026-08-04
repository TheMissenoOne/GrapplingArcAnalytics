# Decision Flow payload contract (schema v1)

Authoritative spec for the compiled decision-flow payload consumed by
`site/flowchart.js` (`window.GA_FLOWCHART`) and any App-side consumer.
Produced by `analysis/flowchart_compiler.py::compile_flowchart` +
`analysis/flowchart_layout.py::layout_flowchart` (Analysis owns both).

**Contract rule**: renderer never parses Analytics ID syntax (no
`split(":")` semantics). Every reference the UI needs is spelled out in the
payload — display names, timestamps, video ids, links. IDs are opaque keys.

## JSON shape

```jsonc
{
  "schemaVersion": 1,
  "key": "gordon-ryan-closed-guard",
  "title": "Closed Guard",
  "athlete": "gordon-ryan",
  "athleteLabel": "Gordon Ryan",
  "rootPositionKey": "closed-guard",
  "rootPositionLabel": "Closed Guard",

  "exchanges": 21,            // observed exchange count (ranked branches only)
  "matches": 6,               // distinct matches contributing evidence
  "sources": {"expert": 1, "hybrid": 1, "observed": 7},

  "compilerVersion": "1.2.0", // bump on payload-shape / semantics change
  "layoutVersion": 5,         // bump on layout-constants / algorithm change

  "evidence": {               // ONLY entries referenced by a node's evidenceIds
    "m:gordon-ryan-vs-x-2021:i:14": {
      "matchSlug": "gordon-ryan-vs-x-2021",
      "matchLabel": "Gordon Ryan vs X · ADCC 2021",  // optional
      "year": 2021,                                   // optional
      "timestampSeconds": 782,                        // optional
      "videoId": "abc123"                             // optional
    }
  },

  "nodes": [
    {
      "id": "n:athlete-action:hip-bump",
      "key": "hip-bump",                 // technique-node / condition key
      "kind": "athlete-action",          // see Node kinds
      "title": "Hip Bump Sweep",
      "subtitle": "Branch 1 · 9×",       // optional
      "source": "observed",              // observed | expert | hybrid
      "support": 9,                      // observed count; null for expert
      "matchCount": 4,                   // optional
      "successRate": 0.75,               // optional
      "confidence": 0.62,                // optional
      "evidenceIds": ["m:...:i:14"],     // keys into "evidence"
      "warning": true                    // optional (denied outcome)
    }
  ],

  "edges": [
    {"id": "e:...", "source": "n:...", "target": "n:...", "kind": "reaction"}
  ],

  "branches": [
    {"id": "branch:1", "actionKey": "hip-bump", "score": 0.72,
     "conditions": ["n:opponent-condition:hip-bump:cond:posts-hand"],
     "depth": 1}
  ],

  "layouts": {
    "desktop": {
      "layoutVersion": 5, "routingVersion": 1,
      "mode": "desktop", "width": 2010, "height": 846,
      "nodes": {"n:...": {"x": 900, "y": 20, "width": 280, "height": 88}},
      "edges": {"e:...": {"points": [{"x": 0, "y": 0}, {"x": 0, "y": 88}]}}
    },
    "compact": {
      "layoutVersion": 5, "routingVersion": 1,
      "...": "same shape, vertical layout"
    }
  }
}
```

## Athlete dossier envelope

Analytics may attach the compiled payload unchanged to the existing Pro dossier:

```jsonc
{
  "schemaVersion": 1,
  "decisionFlow": { "...": "the complete payload above" }
}
```

`AthleteDossierV1.decisionFlow` is optional. It is omitted—not emitted as `null`—when patterns
are unavailable or fail eligibility. The dossier's outer `schemaVersion` remains `1`; compiler,
layout, and routing versions remain owned by the nested payload. Quality stays at
`decisionFlow.decisionFlow.quality`. Consumers must treat every node, edge, evidence, and branch
ID as an opaque byte-identical key.

Public-site code and generated site output are outside this envelope contract and are not changed.

## Node kinds

| kind | meaning | kicker |
|---|---|---|
| `root-position` | starting position | Starting position |
| `position` | leads-to position (results-in) | Leads to |
| `athlete-action` | athlete's move | — |
| `opponent-condition` | opponent's reaction | Opponent |
| `response` | athlete's answer | Gordon responds |
| `outcome` | terminal result (submission / denied) | Result |
| `portal` | same response recurs as its own branch | Continues below |

## Edge kinds

`action` (root→action) · `reaction` (action→condition) · `response`
(condition→response/outcome) · `results-in` (response→position) ·
`returns-to` · `portal` (to the branch where the response recurs).

## Source semantics

- `observed` — support/matchCount/confidence from match data.
- `expert` — from `flowchart_definitions.json`; stats are `null` (renderer
  must not fake "Seen 0"); labeled "Expected".
- `hybrid` — observed node that also appears in the expert triples.

## Frozen rules (don't change without bumping compilerVersion)

1. Node ids are context-scoped (`n:kind:action[:cond[:resp]]`) — the SAME
   technique key can appear in multiple branches with different ids.
2. `evidenceIds` ≤ 8 per node; every id resolves in `evidence`.
3. Portal = a response key observed under ≥2 conditions AND present as its
   own action branch; edge kind `portal`, node kind `portal`.
4. Expert triples attach to existing branches when the action is known;
   fully-covered triples (cond + response both already observed) are dropped.
5. Deterministic ordering: branches by score desc then key; conditions and
   responses likewise (score, then key).
6. The browser never recomputes layout; coordinates are build-time only.

## Versioning

- `compilerVersion` changes when nodes/edges/evidence semantics change.
- `layoutVersion` changes when layout constants/algorithm change (it also
  appears inside each layout object).
- `routingVersion` changes when edge-routing semantics change (it also appears inside each layout
  object).
- Compiler, layout, and routing versions all participate in the site export
  cache key (Phase 6 cache invalidation: match-sequence hash + ontology
  revision + definitions hash + compilerVersion + layoutVersion + routingVersion).
