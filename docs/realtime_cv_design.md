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
| Pose estimator | **YOLOv8-pose** (Ultralytics) — COCO-17, multi-person, ONNX-exportable. (Supersedes the earlier MoveNet pick; implemented in Phase 1.) |
| Annotation | **CV-assisted + fully manual.** Every detected event is editable, and the user can annotate from scratch on a video with no CV. |
| Athlete graphs | **Per-athlete edge-centric graphs**, embedded (node + whole-graph) into a **Qdrant** vector DB. |
| Prediction loop | Athlete/position priors from Qdrant **both re-rank the classifier's probabilities and surface ranked suggestions** in the annotation UI. |
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
│  pose_estimate (YOLOv8-pose) ──────────────────────│  ✅ Phase 1
│    → 17 kp × 2 athletes                             │
│  cv.inference.classify_pose_pair(kp0, kp1) ─────────│  ✅ Phase 1
│    → ViCoS position class + softmax probs           │
│  ┌─ prior re-rank ◄── Qdrant athlete/position priors │  NEW (loop)
│  segmenter.segment(frames) → events ────────────────│  ✅ Phase 3
│  vocab_map.map_all(events) → grappling nodes ───────│  ✅ Phase 2
└───────────┼───────────────────────────▲────────────┘
            ▼  position timeline         │ ranked suggestions
┌─ Annotation/Review studio (web/) ──────┴──────────┐
│  CV-assisted: edit/merge events, set actor, finishes│
│  Manual: scrub video, mark in/out, pick node (auto- │
│          complete ranked by Qdrant priors)          │
│  → emit SessionPayload {topics, rounds:[{entries}]} │
└───────────┼────────────────────────────────────────┘
            ▼
   import into GrapplingArcApp → processSession → graph   ✅ untouched
            │
            ▼  (also) per-athlete graph builder
┌─ Athlete graphs → Qdrant ─────────────────────────┐
│  build edge-centric graph per athlete (you+opponents)│
│  embed: per-(athlete,position) node + whole-graph   │
│  upsert → Qdrant ──► priors feed the loop above ────│
└────────────────────────────────────────────────────┘
```

### Actor assignment
CV distinguishes **top vs bottom**, not *which* athlete is "you". The review UI asks once per match: "you are top/bottom" (or the user toggles per round). `actor` derives from that + the per-frame top/bottom label.

### Segmentation
A raw per-frame class stream is noisy. Segmenter: smooth with a short majority window, then collapse runs of the same `(position, top/bottom)` into a single event with start/end timestamps and mean confidence. A position *change* emits a `transition`-type bridge entry.

---

## 5. Proposed layout (inside GrapplingArcAnalytics)

```
cv/
  pose_estimate.py     # ✅ YOLOv8-pose: frame(np) → [kp_a, kp_b]
  inference.py         # ✅ load model + classify_pose_pair / classify_frame
  vocab_map.py         # ✅ ViCoS class → grappling-arch.nodes.json node (via variations[])
  segmenter.py         # ✅ frame class stream → discrete event sequence
analytics-ish/
  athlete_graph.py     # NEW — build per-athlete edge-centric graph from observed sequences
  graph_embed.py       # NEW — node + whole-graph embeddings (feature vectors)
  vector_store.py      # NEW — Qdrant client: upsert graphs, query priors
  priors.py            # NEW — position/athlete priors → re-rank probs + ranked suggestions
realtime/
  app.py               # NEW — FastAPI: /classify, /segment, /annotate, /suggest, /priors
  export.py            # NEW — events → SessionPayload JSON (validates vs sessionProcessor shape)
web/
  (Vite + React)       # NEW — live overlay + post-hoc scrub + manual annotation + review
docs/
  realtime_cv_design.md  # this file
tests/
  test_inference.py ✅, test_vocab_map.py ✅, test_segmenter.py ✅,
  test_athlete_graph.py, test_graph_embed.py, test_priors.py, test_export.py
```

`analysis/similarity.py` (card 011, cosine-sim fighter matching) is the natural home/precedent
for `graph_embed`/`priors` — reuse its vectorization rather than inventing a new one. The app
vocab JSON (`grappling-arch.nodes.json`) is loaded via `cv.vocab_map.load_app_nodes` (no copy needed).

---

## 6. Build phases

1. ✅ **Inference unlock**: `cv/inference.py` + `cv/pose_estimate.py` (YOLOv8-pose). *Done.*
2. ✅ **Vocab mapper**: `cv/vocab_map.py` — ViCoS class → node via `variations[]`. *Done.*
3. ✅ **Segmenter**: `cv/segmenter.py` — smoothing + run-collapse. *Done.*
4. **Backend**: `realtime/app.py` FastAPI, `/classify` + `/segment`, reusing 1–3.
5. **Exporter**: `realtime/export.py` — events → `SessionPayload`; assert it satisfies `validateSession`'s shape.
6. **Web app — capture + review**: `web/` live (`getDisplayMedia` + canvas overlay) and post-hoc (video scrub), both ending in the review table that calls the exporter.
7. **Manual annotation mode** (§8): video scrubber + in/out marking + node picker, sharing the same event model + exporter as the CV path. No CV required.
8. **Athlete-graph builder** (§9): `analytics-ish/athlete_graph.py` — build a per-athlete edge-centric graph from exported `SessionPayload`s / match sequences. Pure, testable.
9. **Embeddings + Qdrant** (§9): `graph_embed.py` + `vector_store.py` — node + whole-graph vectors, upsert to Qdrant. Reuse `analysis/similarity.py` vectorization.
10. **Prediction loop** (§9): `priors.py` + `/suggest`,`/priors` routes — query Qdrant for athlete/position priors, re-rank classifier probs, feed ranked suggestions to the annotation UI.

Ship order is dependency order; each phase is independently testable. Phases 8–9 are pure
Python and can run **in parallel** with the web work (4–7) — good parallel-packet candidates.

---

## 8. Manual annotation mode

The CV path produces a *draft* event timeline; manual annotation is a first-class peer, not
just an edit layer. Both write the **same event model** and flow through the **same exporter**,
so a session can be all-CV, all-manual, or any mix.

- **Source**: a recorded video (`<video>` element, frame-accurate scrub). No CV needed.
- **Mark**: set an event's in/out by scrubbing; pick the node from a searchable list backed by
  `grappling-arch.nodes.json` (137 nodes) via `cv.vocab_map.load_app_nodes`. Set `actor`
  (you/partner), `type`, optional `setup`, and `successful`.
- **Edit CV output**: every detected event is mutable — re-label, merge/split, shift in/out,
  delete, toggle `successful`, or reassign `actor`. This is the post-match review pass.
- **Assisted**: the node picker is an autocomplete **ranked by Qdrant priors** (§9) — given the
  prior event + athlete, the likely next nodes float to the top. Suggestion, never auto-commit.
- **Shared event model**: one internal `AnnotationEvent {label, type, actor, start, end,
  successful, source: "cv" | "manual", confidence?}`. The exporter (`realtime/export.py`)
  collapses these into the `SessionPayload` chain regardless of source.

`/annotate` (backend) persists/loads a draft annotation set per video so work survives reloads.

## 9. Athlete graphs + Qdrant prediction loop

Goal: a corpus of **per-athlete technique graphs** that is queryable as vectors, so observed
tendencies *direct* both CV detection and manual annotation.

**Build (`analytics-ish/athlete_graph.py`)** — for each athlete (you and every named opponent),
build an **edge-centric graph** with the same semantics as the app's career graph
(`sessionProcessor.ts`): nodes = positions/techniques the athlete uses, edges = transitions,
weighted by frequency/recency. Source = exported `SessionPayload`s + annotated match sequences.

**Embed (`graph_embed.py`)** — two vector families, reusing `analysis/similarity.py` vectorization:
- **Per-(athlete, position) node vectors** — encode "what this athlete does *from* this position"
  (out-edge distribution over next nodes). Drives next-move priors.
- **Whole-graph vector per athlete** — a style fingerprint. Drives "who fights like X" opponent
  similarity.

**Store (`vector_store.py`)** — a **Qdrant** collection per family. Payload carries
`athlete`, `position`, `weight_class`, etc. for filtered queries (e.g. *node vectors filtered to
position=mount_top, ordered by similarity to opponent Y*). Local Docker/embedded; client wrapped
so tests can run against an in-memory/mock store.

**Priors + loop (`priors.py`)** — given current detected position + athlete context, query Qdrant
for the next-move distribution and produce a prior vector over nodes. Two consumers (per the
decision — both):
1. **Re-rank CV**: blend the prior into the classifier softmax (`p' ∝ p^α · prior^(1-α)`) before
   argmax, biasing detection toward transitions this athlete actually makes. `α` tunable; prior
   is *advisory* and bounded so a strong CV signal still wins.
2. **Suggest**: the annotation node picker (§8) ranks candidates by the same prior.

Backend routes: `/priors` (prior vector for a context) and `/suggest` (ranked node list).
The loop is **closed**: each exported/annotated session updates the athlete graph → re-embeds →
upserts to Qdrant → improves the next session's priors.

---

## 7. Open risks / to resolve later

- **Grappler pair selection/order** — YOLOv8-pose returns N people; `select_grappler_pair` picks the 2 largest and orders by hip-y. The order must match training's `athlete_idx` — see the Phase 1 note; validate on real data.
- **Classifier was trained on ViCoS still images**, not screen-captured broadcast frames (compression, overlays, camera motion). Expect a domain gap; the review step is the safety net. Possible later: fine-tune on a few labeled match clips.
- **Submission/finish detection** — not from positions; manual in review for now. Candidate later: a small temporal head over the position+keypoint stream.
- **Frame rate vs latency** — 5–10 fps target; tune the smoothing window to match.
- **Prior loop cold-start & feedback bias** — early on Qdrant is empty (no priors; CV runs unbiased). As it fills, a too-strong `α` can make CV/suggestions self-reinforce the corpus (confirmation loop). Keep the prior advisory + bounded, log when a prior flips an argmax, and evaluate CV accuracy with priors **off** as the baseline.
- **Athlete identity** — graphs are keyed by athlete name; needs a resolver (manual tag, or later face/ID) to attribute a detected grappler to the right athlete graph. Until then, "you vs opponent" only.
- **Qdrant as infra** — adds a service dependency; wrap the client so tests/CI use an in-memory/mock store and the backend degrades gracefully (no priors) when Qdrant is down.
