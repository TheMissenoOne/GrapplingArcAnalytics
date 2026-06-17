# Realtime CV — Match Technique-Sequence Capture

**Date:** 2026-06-17
**Scope:** New web app + Python backend that watch a match (live screen or recorded video), use computer vision to detect a technique/position sequence, and export it as a GrapplingArcApp `SessionPayload` that builds the career technique graph.
**Status:** 📐 DESIGN — no code yet. Review before building.

> **One-line summary:** The hard part (the graph engine) already exists in GrapplingArcApp. The CV half (keypoints → position) already exists in `cv/`. This project builds the missing middle — **frame → keypoints → position timeline → editable sequence → `SessionPayload` JSON** — and reuses both ends untouched.

---

## 1. Decisions (locked)

| Decision | Choice |
|----------|--------|
| Capture modes | **Both** — live screen overlay *and* post-hoc on recorded video. Live shows positions in real time; the graph is only built after a post-match review pass. |
| Inference location | **Python backend**, reusing `cv/` (`pose_features`, `baseline_classifier`). |
| Output to app | **Export `SessionPayload` JSON** — imported into GrapplingArcApp like any session. The app graph engine is untouched. |
| Code location | **Inside GrapplingArcAnalytics** — new `cv/` helpers + `realtime/` backend + `web/` frontend, one repo, one test suite. |
| Pose estimator | **MoveNet via onnxruntime** — fast, CPU, single-person×2, good enough at 5–10 fps. |
| First step | **This design doc.** |

---

## 2. How GrapplingArcApp builds the graph (reference)

The web app must produce the *input* to this — it does not reimplement it.

### Data model (the contract)
Source of truth: `GrapplingArcApp/src/services/sessionProcessor.ts`, `src/types/session.ts`.

```ts
ChainEntry = {
  label: string;                 // technique/position name
  type: NodeType;                // submission|control|transition|defensive|
                                 //   concept|sweep|takedown|escape|pass|guard
  actor: 'you' | 'partner';
  setup?: string;                // how you bridged into this move
  successful?: boolean;          // false = missed (transparent to the graph)
  points?: number;
}
Round   = { difficulty, intensity, entries: ChainEntry[], outcome? }
SessionPayload = { topics: ChainEntry[], rounds: Round[], timestamp?, notes? }
```

### Two graphs in the app — only the second matters here
1. **Session mini-graph** (`src/utils/buildSessionGraph.ts`) — disposable per-session visual recap. Radial layout. Ignore for export.
2. **Persistent career graph** (`sessionProcessor.ts` + `graphDomain.ts`) — the real model, **edge-centric**:
   - Only **your landed moves** become nodes.
   - **Partner moves** never become nodes — buffered onto the *edge* as `reaction[]` (the stimulus you answered).
   - **Missed moves** (`successful:false`) are transparent — no node, chain stays contiguous.
   - Edges = `your move → your next move`, carrying `{ reaction[], setup, elo }`.
   - Node identity = `normalizeLabel(label) + actor` (NFD-stripped lowercase) → deterministic IDs; same technique always the same node.
   - ELO: each round scores `delta = K·(S−E)` (signed), distributed across your nodes weighted by `1 + pointsEarned`; `userElo = mean computedElo over non-concept nodes`. `computeNodeDynamics` adds `fundamentalScore` + `trend` (emerging/core/fading).

### Vocabulary (what CV labels must resolve to)
`GrapplingArcApp/src/data/grappling-arch.nodes.json` — **137 nodes**:
```
guard:18 submission:34 control:13 takedown:21 transition:2 escape:6 sweep:8 concept:11 pass:24
```
Each node: `{ name, type, translations{pt,en}, variations[] }`. `variations[]` is the fuzzy-match key — a CV class name is mapped to a node by matching against `variations`.

---

## 3. What `cv/` already provides vs. the gaps

| Capability | Status | Where |
|------------|--------|-------|
| keypoints → position label | ✅ | `cv/pose_features.pair_features` → `cv/baseline_classifier.train_baseline` |
| frame → 17 COCO keypoints (pose estimation) | ❌ **GAP 1** | ViCoS ships keypoints; live frames don't |
| persisted model + `predict(kp)→label` | ❌ **GAP 2** | `train_baseline` returns model in-memory only |
| ViCoS 18 position classes → app 137-node vocab | ❌ **GAP 3** | CV emits positions only |

**Consequence of GAP 3:** CV reliably yields a **position timeline** (10 positions × top/bottom). Submissions, sweeps, takedowns are *not* position classes — they are inferred from position *transitions* or added by hand in the review step. This is why the graph is only committed after review: live overlay shows positions; the human adds the finish before export.

---

## 4. Architecture

```
┌─ Web app (new, web/) ─────────────────────────────┐
│  Live:  getDisplayMedia → <canvas> overlay         │
│  Post:  <video> file → scrub + frame extract       │
│           │ frames @ 5–10 fps via WebSocket         │
└───────────┼────────────────────────────────────────┘
            ▼
┌─ Python backend (new, realtime/, FastAPI) ────────┐
│  pose_estimate (MoveNet/onnxruntime) ──────────────│  GAP 1
│    → 17 kp × 2 athletes                             │
│  cv.pose_features.pair_features(kp_top, kp_bot) ────│  ✅ reuse
│    → model.predict()  (loaded artifact) ────────────│  GAP 2
│    → ViCoS position class + confidence              │
│  segmenter: collapse consecutive frames → events ───│  new
│  vocab_map: position class → grappling node ────────│  GAP 3
└───────────┼────────────────────────────────────────┘
            ▼  position timeline [{label,type,actor,t,conf}]
┌─ Review UI (web/) ────────────────────────────────┐
│  edit/merge events, set actor = you, add finishes   │
│  → emit SessionPayload {topics, rounds:[{entries}]} │
└───────────┼────────────────────────────────────────┘
            ▼
   import into GrapplingArcApp → processSession → graph   ✅ untouched
```

### Actor assignment
CV distinguishes **top vs bottom**, not *which* athlete is "you". The review UI asks once per match: "you are top/bottom" (or the user toggles per round). `actor` derives from that + the per-frame top/bottom label.

### Segmentation
A raw per-frame class stream is noisy. Segmenter: smooth with a short majority window, then collapse runs of the same `(position, top/bottom)` into a single event with start/end timestamps and mean confidence. A position *change* emits a `transition`-type bridge entry.

---

## 5. Proposed layout (inside GrapplingArcAnalytics)

```
cv/
  pose_estimate.py     # NEW — MoveNet onnxruntime: frame(np) → [kp_a, kp_b]
  inference.py         # NEW — save/load model artifact; classify_pose_pair(kp_a,kp_b)->(label,conf)
  vocab_map.py         # NEW — ViCoS class → grappling-arch.nodes.json node (via variations[])
  segmenter.py         # NEW — frame class stream → discrete event sequence
realtime/
  app.py               # NEW — FastAPI: /classify (frame→pos), /segment (batch→events)
  export.py            # NEW — events → SessionPayload JSON (validates vs sessionProcessor shape)
web/
  (Vite + React)       # NEW — live overlay + post-hoc scrub + review table
docs/
  realtime_cv_design.md  # this file
tests/
  test_inference.py, test_vocab_map.py, test_segmenter.py, test_export.py
```

The app vocab JSON (`grappling-arch.nodes.json`) is copied/symlinked into `data/` so `vocab_map` resolves without a hard path into the RN repo.

---

## 6. Build phases

1. **Inference unlock** (pure Python, no UI): `cv/inference.py` (persist + `classify_pose_pair`) and `cv/pose_estimate.py` (MoveNet adapter, COCO-17 out). Tests with a fixture frame. *Unblocks everything.*
2. **Vocab mapper**: `cv/vocab_map.py` — ViCoS class → node `{label,type,actor}` via `variations[]`. Test against `grappling-arch.nodes.json`.
3. **Segmenter**: `cv/segmenter.py` — smoothing + run-collapse + transition emission. Pure, fully unit-tested on synthetic streams.
4. **Backend**: `realtime/app.py` FastAPI, `/classify` + `/segment`, reusing 1–3.
5. **Exporter**: `realtime/export.py` — events → `SessionPayload`; assert it satisfies `validateSession`'s shape.
6. **Web app**: `web/` — live (`getDisplayMedia` + canvas overlay) and post-hoc (video scrub) modes, both ending in the review table that calls the exporter.

Ship order is dependency order; each phase is independently testable.

---

## 7. Open risks / to resolve later

- **MoveNet two-person handling** — MoveNet MultiPose returns up to 6 poses; need to pick the 2 grapplers and assign top/bottom by hip-y. Fallback: crop to the mat region.
- **Classifier was trained on ViCoS still images**, not screen-captured broadcast frames (compression, overlays, camera motion). Expect a domain gap; the post-hoc review step is the safety net. Possible later: fine-tune on a few labeled match clips.
- **MoveNet 17-kp vs ViCoS 17-kp ordering** — confirm COCO index parity before feeding `pair_features`; remap if MoveNet differs.
- **Submission/finish detection** — out of scope for v1 (manual in review). Candidate v2: a small temporal head over the position+keypoint stream.
- **Frame rate vs latency** — 5–10 fps target; tune the smoothing window to match.
