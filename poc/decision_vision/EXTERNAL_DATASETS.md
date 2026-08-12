# External BJJ datasets — Decision Vision registry

This registry is for **visual pretraining / external priors**, not for Decision
Space occurrence counts.

External images may teach:

```text
where are the grapplers?
what position/configuration is visible?
what pose geometry is visible?
```

They must never add synthetic observations to:

```text
N(A -> C -> B)
P(B | A,C)
match_count
opponent_count
```

Only GrapplingArc match/session evidence can do that.

## Recommended order

### 1. bjj3 — PRIMARY NOW

- 24,056 images
- object detection
- MIT
- 18 role-aware BJJ position labels
- use: external position prior
- current Analytics already contains `cv.roboflow_classifier.py` and
  `cv.roboflow_labels.py` specifically for the bjj3 label family

Do **not** start by downloading 24k images and fine-tuning the failed RGB
criterion classifier. Use the already-trained position model as a structured
feature first.

### 2. BJJ-Recognition-Model — PRIMARY NEXT

- 3,835 images
- keypoint detection
- CC BY 4.0
- class: athlete
- use: compare BJJ-trained pose against generic YOLOv8 COCO pose

Gate: inspect one real API/export response before wiring it into
`cv.pose_features`. Do not assume keypoint order == COCO-17.

### 3. Grappling Set — SECONDARY

- 6,028 images
- object detection
- CC BY 4.0
- position labels overlapping bjj3

Potential offline/local position pretraining. First check duplicate lineage with
bjj3 / Rbflw-sample because the naming convention is nearly identical.

### 4. BJJ AI — OPTIONAL

- 2,107 images
- athlete detection
- Public Domain

Use only if generic person/pose detection fails to isolate the grappler pair.
The label semantics are not Decision Criterion semantics.

### 5. MMA set — OPTIONAL / PARTIAL MAP

- 2,555 images
- object detection
- CC BY 4.0
- 25 mixed striking + grappling classes

Only map relevant grappling labels (`turtle`, `5050_guard`, `armlock`, `back1`,
`closed_guard`, `half_guard`, `open_guard`, `side_control`, `takedown`,
`standing`). Keep striking labels out of the GrapplingArc taxonomy.

### 6. Rbflw-sample — LOW PRIORITY

- 12,027 images
- MIT
- same 18-label family as bjj3
- lower published trained-model metrics

Likely useful only for provenance/lineage analysis or ablation.

### 7. ViCoS BJJ Positions — RESEARCH ONLY (state benchmark)

- 120,279 frames, 2 athletes per frame, COCO-17 poses (manually verified)
- 6 sparring sequences, 3 smartphone cameras
- CC BY-NC-SA 4.0 — **non-commercial**: research/benchmark only, never
  commercial model training
- 18 role-aware classes encode which athlete is top (e.g. `mount2`)
- use: state-tracking benchmark (position + top/bottom + identity continuity)
- split rule: **leave-one-sequence-out only** — random frame splits leak
  appearance/mat/lighting across adjacent frames (the RGB-POC failure mode)
- citation: Hudovernik & Skocaj, ACM MMSports'22
- data: `data/external/decision_vision/vicos_bjj/annotations.json` (gitignored)

### 8. BJJ Techniques — QUARANTINE

License metadata could not be verified. Do not use in commercial training until
license + class list are recorded.

## Provenance flags

`bjj3` and `grappling_set` are marked `provenance_review_required: true`: both
publish the same ViCoS-derived 18-class role ontology with **no documented
image provenance**. A Roboflow uploader cannot clear upstream non-commercial
images simply by licensing their own upload MIT/CC BY 4.0. Until image lineage
is verified, treat both as **approved for POC priors, unresolved for commercial
model training**.

## Commercial-license handling

- MIT: commercial use permitted; preserve license/copyright obligations.
- CC BY 4.0: commercial use permitted; attribution required, link license,
  indicate modifications where applicable.
- Public Domain: commercially usable; keep provenance anyway.
- UNVERIFIED: disabled.

A dataset license is not a warranty about every underlying photograph's
personality, broadcast, or third-party rights. Preserve dataset/version/source
metadata for any commercial release.
