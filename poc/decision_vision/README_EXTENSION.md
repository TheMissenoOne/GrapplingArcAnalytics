# Decision Vision temporal/external-prior extension

## Why this extension exists

Static RGB POC result on 6 matches:

```text
335 frames
train 4 matches
validation 2 matches

fine-tune:
  train loss -> ~0.011
  held-out macro-F1 -> 0
  => venue/identity memorization

frozen ResNet probe:
  leaf     0.078
  family   0.054
  category 0.110
  => chance-level
```

Conclusion: **do not spend the next iteration on a larger RGB still-image
classifier**.

The next experiment removes most venue/identity signal and adds time:

```text
ephemeral FFmpeg frames
       ↓
┌─────────────────────────────┐
│ existing generic pose      │ -> temporal pose dynamics
│ existing bjj3 position API │ -> temporal position prior
└─────────────────────────────┘
       ↓
identity-reduced numeric features
       ↓
linear probe
       ↓
leave-one-match-out evaluation
```

No raw RGB feature goes into the probe.

## Files to merge

Add to the current POC package:

```text
poc/decision_vision/
  external_datasets.json
  EXTERNAL_DATASETS.md
  visual_labels.py
  temporal_features.py
  train_temporal.py
  test_visual_labels.py
```

Keep the existing:

```text
common.py
frame_stream.py
extract_frames.py
train.py            # frozen as static-RGB baseline
predict.py
inspect_db.py
```

## Step 1 — fix visual target semantics

Do not change DB events.

For CV only:

```text
Double Leg Attempt -> Double Leg
Armbar Attempt     -> Armbar
```

when the base canonical node exists.

Reason: attempt/outcome is not reliably visible in one short clip and was
causing leaf==family collapse / noisy labels.

Integrate `visual_labels.collapse_visual_node_key()` into manifest generation
before deriving leaf/family/category.

Also emit a quality report:

```text
visual_target_rows
taxonomy_resolved_rows
leaf_eq_family_rows
attempt_collapsed_rows
```

Target before another semantic experiment:

```text
taxonomy_resolved > 80%
leaf==family should fall materially from the observed ~89.6%
```

If it does not, fix taxonomy mapping before interpreting leaf-vs-family results.

## Step 2 — build temporal derived features

Requires:

```bash
export ROBOFLOW_API_KEY=...
```

Then:

```bash
PYTHONPATH=poc uv run --extra cv --extra postgres \
  python -m decision_vision.temporal_features \
  --manifest data/cv_decision_poc/manifest.csv \
  --position-model-id bjj3/1
```

Outputs only:

```text
data/cv_decision_poc/temporal/
  samples.csv
  features.npz
```

No frames/video saved.

### Pose features

Existing Analytics path:

```text
frame
 -> cv.pose_estimate.PoseEstimator
 -> select_grappler_pair()
 -> cv.pose_features.pair_to_features()
 -> 68 dimensions/frame
```

Temporal summary:

```text
center
pre mean
post mean
post-pre delta
std
last-first
mean abs frame delta
pose pair quality
```

### Position features

Existing Analytics path:

```text
frame
 -> cv.roboflow_classifier.RoboflowClassifier("bjj3/1")
 -> cv.roboflow_labels
 -> role-aware probabilities
 -> collapse to broad position prior
```

Fixed broad classes:

```text
standing
5050 guard
back
closed guard
half guard
mount
open guard
side control
takedown
turtle
```

Same temporal summary as pose.

## Step 3 — leave-one-match-out probe

Run:

```bash
PYTHONPATH=poc uv run --extra cv \
  python -m decision_vision.train_temporal \
  --data data/cv_decision_poc/temporal
```

Experiments run independently:

```text
pose
position
fused
```

Heads:

```text
leaf
family
category
```

Model is deliberately weak:

```text
StandardScaler
 -> balanced multinomial LogisticRegression
```

No deep fine-tuning in this iteration.

Evaluation is `LeaveOneGroupOut(match_id)`, not one arbitrary 4/2 split.

Report:

```text
data/cv_decision_poc/temporal/report.json
```

For every fold, classes absent from training are excluded from metric
calculation and counted as `skipped_unseen_class_samples`.

## Decision gates

### Gate A — position prior transfers

If `position/category` meaningfully beats chance across held-out matches:

```text
keep bjj3 prior
```

It proves external BJJ supervision supplies transferable structure even though
generic RGB features did not.

### Gate B — temporal pose transfers

If `pose/category` or `pose/family` beats chance:

```text
keep temporal geometry branch
```

Then test BJJ-trained pose as the next replacement for generic COCO pose.

### Gate C — fusion beats both

If fused materially improves over both branches:

```text
continue toward temporal criterion model
```

### Gate D — all remain chance

Stop classification work on this feature set. Next POC should be one of:

```text
1. BJJ-specific keypoint backend
2. short clip encoder / optical flow
3. better event timing labels
4. manual criterion clip labels
```

Do not fine-tune a larger RGB model as the automatic next step.

## External raw-image imports

Do not merge external images directly into GrapplingArc criterion labels.

Correct use:

```text
external dataset
 -> task-specific detector / pose / representation
 -> derived feature on GrapplingArc match frame
 -> GrapplingArc criterion statistics
```

If raw datasets are downloaded for local training, keep under:

```text
data/external/decision_vision/
```

and gitignore them. Preserve source/version/license metadata in a sidecar.

## Next external-model experiment

After the temporal generic-pose probe:

```text
BJJ-Recognition-Model
    ↓
inspect keypoint schema
    ↓
adapter to (17,3) only if schema mapping is valid
    ↓
same temporal_features.py
    ↓
compare:
generic_pose vs bjj_pose
```

Do not change the evaluation protocol between the two.


# Additional POC — top/bottom + athlete state timeline

This is now a separate, lower-complexity target from Decision Criterion
classification.

Question:

```text
Can CV reliably tell:
1. the current grappling position,
2. which persistent visual athlete is top,
3. which is bottom,
4. when those roles switch?
```

Architecture:

```text
remote video
   ↓
one FFmpeg process
   ↓ 1 frame / second by default
bjj3 role-aware detector
   ↓
mount_top / mount_bottom
half_guard_top / half_guard_bottom
...
   ↓
bbox continuity tracker
   ↓
track_0 / track_1
   ↓
top/bottom timeline
+
per-athlete state timeline
```

Important distinction:

```text
top/bottom = BJJ positional role
track_0/track_1 = persistent visual identity
athlete_id = real DB identity, only when explicitly seeded
```

Do not use `PoseEstimator.select_grappler_pair(order_by="hip_y")` to define BJJ
top/bottom. That method means "higher/lower in image coordinates", not
grappling role.

## Run

```bash
export ROBOFLOW_API_KEY=...

PYTHONPATH=poc uv run --extra cv --extra postgres \
  python -m decision_vision.build_role_timeline \
  --match-id <MATCH_UUID> \
  --sample-every 1.0
```

Outputs:

```text
data/cv_decision_poc/role_timeline/
  role_samples.csv
  top_bottom_segments.csv
  athlete_state_segments.csv
  role_timeline_report.json
```

No images/video are persisted.

### Anonymous tracking

Without a seed the output still tracks:

```text
track_0
track_1
```

across frames.

Example:

```text
00:30  mount
        track_0 = top
        track_1 = bottom

00:46  half guard
        track_0 = top
        track_1 = bottom

01:02  half guard
        track_0 = bottom
        track_1 = top
        ROLE SWITCH
```

That role switch is itself a useful state event.

### Binding tracks to real match athletes

Pure bbox tracking cannot know whether `track_0` is Athlete A or Athlete B on
its own.

If you know who is top at the first role-resolved sample:

```bash
--seed-top-athlete-id <ATHLETE_UUID>
```

The POC binds that visual track to the supplied DB athlete and the other track
to the other match participant. It then follows the identities by bbox
continuity even when top/bottom swaps.

Do not infer the initial identity from face recognition in this POC.

### Symmetric states

For labels such as:

```text
standing
5050 guard
```

bjj3 may not define top/bottom. Output:

```text
role_resolved = false
symmetric = true
```

Do not invent a top/bottom assignment.

## Success metrics

The report includes:

```text
role_resolved_rate
symmetric_rate
role_switch_events
tracker_reinitializations
identity_seed_applied
```

First gate:

```text
role_resolved_rate >= 0.70
tracker_reinitializations low
```

Then visually/manual-audit ~20 role switches from several held-out matches.

If role/position tracking is stable while exact Decision Criterion recognition
remains weak, this timeline is still valuable as a state layer for Decision
Space:

```text
Athlete A:
bottom half guard
 -> top half guard
 -> top side control

Athlete B:
top half guard
 -> bottom half guard
 -> bottom side control
```

That gives the system a reliable state backbone even before it can name the
specific action that caused each transition.


# Local dashboard — progress and results

This extension adds:

```text
poc/decision_vision/dashboard.py
poc/decision_vision/progress.py
```

Launch from the Analytics repo root:

```bash
PYTHONPATH=poc uv run --extra dev \
  uvicorn decision_vision.dashboard:app \
  --host 127.0.0.1 \
  --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

The page polls every ~1.5 seconds and shows:

```text
runs / status / phase / percent
role-resolved rate
role-switch count
top/bottom timeline
persistent visual tracks
per-athlete state segments
pose / position / fused probe results
legacy RGB baseline result summary
```

The dashboard is read-only and never serves source media. It reads only the
derived CSV/JSON artifacts under `data/cv_decision_poc/`.

Override the data root:

```bash
export DECISION_VISION_DATA_ROOT=/path/to/data/cv_decision_poc
```

## Progress contract

Long-running workers publish:

```text
<output>/progress.json
```

through `ProgressReporter`.

Example:

```json
{
  "run_id": "role-<match>",
  "pipeline": "role_timeline",
  "status": "running",
  "phase": "sampling",
  "current": 80,
  "total": 350,
  "percent": 22.86,
  "message": "Sampled 80 frames through 99.0s",
  "metrics": {
    "role_resolved_samples": 64,
    "role_switch_events": 3
  }
}
```

Writes are atomic so the dashboard never reads half-written JSON. UI and worker
processes are independent; restarting the dashboard does not affect an active
analysis run.
