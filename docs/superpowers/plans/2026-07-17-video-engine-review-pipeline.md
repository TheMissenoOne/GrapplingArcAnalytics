# Video Engine Review Pipeline Plan

> Draft plan for the Analytics module. Batch 1 is the only build target for now. Later phases are
> deliberately scoped as roadmap items, not implementation commitments.

## Overview

Build a video-engine pipeline in batches:

1. Extract targeted frame sequences from YouTube videos around registered node timestamps.
2. Present those frames in a local web review UI so a maintainer can mark each node assignment as
   correct, incorrect, or unreviewable.
3. Route incorrect items into a refinement queue.
4. Route successful items into an annotation queue for future computer-vision training.
5. Reuse the same engine later for uploaded session videos from the app, voiceover-to-transcript
   alignment, and highlight generation.

The key constraint is to keep the first release narrow. Batch 1 is review and routing only. It does
not include full annotation tooling, refinement editing, app upload processing, transcription
alignment, or highlight generation.

## Full-Scale Plan

### Phase A: Timestamped review extraction

- Pull short clips or frame sequences around registered node timestamps.
- Use FFmpeg-based extraction and keep the media local.
- Do not download or retain entire source videos when partial extraction is enough.
- Link extracted media to the exact node event that produced it.

### Phase B: Human review and routing

- Show a compact desktop review UI.
- Let a maintainer mark each node as:
  - correct
  - incorrect
  - unreviewable
- Persist the verdict locally.
- Send incorrect items to a refinement queue.
- Send correct items to an annotation queue.

### Phase C: Training-data preparation

- Turn approved node events into annotation-ready media.
- Keep the annotation surface separate from the review surface.
- Export data in a shape that can be used later for CV training.

### Phase D: App session processing

- Accept uploaded session videos from the app.
- Reuse the same extraction and review logic for user-generated media.
- Normalize timestamps against the recorded session timeline.

### Phase E: Voiceover mode

- Allow the app user to record commentary while scrubbing the timeline.
- Transcribe the voiceover.
- Use the transcript to help assign or verify nodes against the recorded session.

### Phase F: Highlight engine

- Use node data, pixel displacement, and momentum analysis.
- Generate quick-cut highlight clips from the strongest moments.
- Treat this as a later research and product phase after trusted labels exist.

## Batch 1 Scope

Batch 1 delivers the smallest useful loop:

`registered node timestamp -> targeted clip/frame extraction -> local review -> verdict -> queue routing`

### In scope

- Extract a short window around each registered node timestamp.
- Produce a 5-second review loop plus still frames.
- Show the evidence in a local web UI.
- Let the reviewer classify each item as correct, incorrect, or unreviewable.
- Store verdicts locally.
- Route items into the right downstream queue state.

### Out of scope

- Full annotation editor.
- Refinement editor for correcting bad labels in place.
- CV training pipeline.
- App-uploaded session processing.
- Voiceover capture and transcription.
- Highlight generation.

## Batch 1 Detailed Plan

### 1. Inputs

Batch 1 starts from existing node records, not from a new labeling flow.

- Source media: YouTube video URL or equivalent remote video reference.
- Anchor point: registered node timestamp.
- Node context: label, actor, type, and any other event metadata already stored with the node.
- Review unit: one node event at a time.

The review unit is the event itself, not the full match and not a generic video segment. The clip
exists only to validate whether that node assignment is correct.

### 2. Extraction contract

For each node event, produce a small review bundle:

- one short playable clip centered on the timestamp
- a frame strip or still sequence around the same point
- enough metadata to orient the reviewer without opening the full match

Practical target:

- review loop length: 5 seconds
- frame context: at least one frame before, one frame at the timestamp, and one frame after

Rules:

- keep extraction local
- do not require a full-video download if a partial extract is enough
- keep the original timestamp attached to the bundle
- if the timestamp cannot be trusted, mark the item unreviewable instead of guessing

### 3. Review verdicts

Batch 1 uses three verdicts:

- `correct`
- `incorrect`
- `unreviewable`

Meaning:

- `correct`: the registered node assignment matches the evidence
- `incorrect`: the label, actor, type, or timestamp assignment is wrong
- `unreviewable`: the evidence is too poor to decide confidently

For incorrect or unreviewable items, capture a short reason so the queue is actionable later.

### 4. Queue routing

The review verdict drives the next state:

| Verdict | Next queue |
|---|---|
| `correct` | annotation queue |
| `incorrect` | refinement queue |
| `unreviewable` | blocked or skipped queue |

Routing rules:

- correct items become candidates for annotation and future CV training
- incorrect items remain visible to a later correction workflow
- unreviewable items stay separate so they do not contaminate downstream training

### 5. Local persistence

Persist review state locally so the work can resume after restart.

Minimum data to store:

- event id or stable candidate id
- source video reference
- timestamp
- extracted media paths
- current verdict
- reason, if any
- queue state
- review timestamps

Persistence rules:

- idempotent re-review should overwrite the verdict cleanly
- media extraction should be reproducible from the source reference
- queue state should be restorable without reprocessing everything

### 6. Review UI

The first UI should be dense and task-focused.

Layout:

- main video or frame strip area
- metadata rail with node details
- verdict controls
- reason controls for incorrect and unreviewable items

Interaction model:

- keyboard-first
- one item at a time
- fast advance after verdict
- visible distinction between pending, reviewed, and routed items

UI must show enough context to answer:

- what node am I looking at
- where in the source video am I
- why was this item queued
- what verdict already exists, if any

### 7. Review workflow

1. Load the next pending node event.
2. Extract or fetch the 5-second review bundle.
3. Present the clip and frame context.
4. Reviewer selects correct, incorrect, or unreviewable.
5. Store verdict and reason.
6. Route the item to the matching queue.
7. Advance to the next item.

### 8. Operational constraints

- local-only for Batch 1
- offline-first persistence
- no refinement editor in this batch
- no training pipeline in this batch
- no app-upload support in this batch
- no highlight generation in this batch

### 9. Acceptance criteria

Batch 1 is ready when:

- a node timestamp can produce a short local review bundle
- the review UI can classify a node as correct, incorrect, or unreviewable
- verdicts persist across restart
- correct items route to annotation
- incorrect items route to refinement
- unreviewable items remain separated from both
- the system does not require a full source download to perform the review

## Proposed Batch 1 Behavior

### Media extraction

- Use FFmpeg or an equivalent partial-extraction flow.
- Extract only the relevant window around the node timestamp.
- Prefer local cache or temporary fragments over whole-video retention.
- Keep the output small and reviewable.

### Review UI

- Dense, desktop-first, local web UI.
- Show the clip or frame strip alongside the registered node metadata.
- Offer one-click verdicts:
  - correct
  - incorrect
  - unreviewable
- Allow a reason to be attached to incorrect or unreviewable items.

### Queue routing

- Correct -> annotation queue.
- Incorrect -> refinement queue.
- Unreviewable -> blocked or skipped queue, depending on the reason.

### Persistence

- Store review state locally.
- Keep review state separate from production app data.
- Make the queue state restart-safe and idempotent.

## Full Roadmap by Batch

1. Batch 1: timestamped extraction, review UI, verdict storage, queue routing.
2. Batch 2: refinement workflow for fixing incorrect nodes and feeding corrected labels back into the pipeline.
3. Batch 3: annotation workflow for CV-ready samples and export format hardening.
4. Batch 4: uploaded session video ingestion from the app.
5. Batch 5: voiceover recording, transcription, and timeline-to-node assignment.
6. Batch 6: highlight engine using node semantics plus motion analysis.

## Constraints

- Keep the first implementation local and offline-first.
- Avoid downloading full source videos if a partial clip is sufficient.
- Treat the review queue as a human-in-the-loop validation layer, not a model-training pipeline.
- Keep later phases separate so the first batch stays shippable.

## References

- User-provided example repo: https://github.com/ValterH/automatic-positions-detection-and-scoring-in-jiu-jitsu
- User-provided reference page: https://www.kevinbpatel.com/work/jiu-jitsu
