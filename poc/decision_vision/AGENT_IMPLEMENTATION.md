# Coding-agent implementation instructions

Implement as a **POC extension only**. Do not wire to production Decision Space,
DB writes, site export, app seed, or current `cv/` contracts.

## Read first

1. `CLAUDE.md`
2. `AGENTS.md`
3. `poc/decision_vision/README_FFMPEG.md` or current POC README
4. `cv/pose_estimate.py`
5. `cv/pose_features.py`
6. `cv/roboflow_classifier.py`
7. `cv/roboflow_labels.py`
8. `db/models.py`
9. `docs/taxonomy.json`

Observed baseline is a **failed experiment and must remain recorded**:

```text
6 matches / 335 frames
deep fine-tune -> held-out F1 0, train loss ~0.011
frozen RGB probe -> chance-level
```

Do not "fix" this by unfreezing a larger backbone.

## Non-negotiable experiment contract

- no video download
- no frame persistence
- FFmpeg frame bytes in RAM only
- DB read-only
- derived numeric `.npz` / CSV allowed
- external datasets never increment Decision Space occurrence counts
- split/evaluation by match, never by frame
- default next classifier is linear probe
- raw RGB is not a feature in this extension
- any external dataset with unverified license stays disabled
- no production edits outside `poc/decision_vision/` except a pyproject dependency
  only if strictly required; prefer existing dependencies

## Work order

### 1. Merge extension files

Add:

```text
poc/decision_vision/external_datasets.json
poc/decision_vision/EXTERNAL_DATASETS.md
poc/decision_vision/visual_labels.py
poc/decision_vision/temporal_features.py
poc/decision_vision/train_temporal.py
tests/poc/test_decision_vision_visual_labels.py
```

Move/adapt the provided `test_visual_labels.py` to the repo's chosen POC test
location if pytest discovery requires it.

### 2. Integrate vision-only label collapse into manifest generation

In `poc/decision_vision/extract_frames.py`:

- keep original DB `criterion_label`, `criterion_event_index`, source, response
- add:
  - `visual_leaf_label`
  - `visual_family_label`
  - `visual_category_label`
  - `visual_label_collapsed: bool`
- collapse `* Attempt` only for CV target
- resolve base technique against current `TechniqueNode` table
- do not mutate DB
- do not change Decision Space semantics

Change `temporal_features.py`/trainer to prefer visual_* columns when present and
fall back to existing leaf/family/category for backward compatibility.

### 3. Add manifest quality report

Create pure helper:

```python
def manifest_quality(frame: pd.DataFrame) -> dict[str, int | float]:
    ...
```

Report at least:

```text
rows
criterion_events
taxonomy_resolved_rate
leaf_eq_family_rate
attempt_collapsed_rate
matches
classes_leaf
classes_family
classes_category
```

Write:

```text
data/cv_decision_poc/manifest_quality.json
```

### 4. Temporal features

Reuse existing project code. Do not reimplement pose math or Roboflow parsing.

Generic pose:

```python
from cv.pose_estimate import PoseEstimator
from cv.pose_features import pair_to_features
```

Position prior:

```python
from cv.roboflow_classifier import RoboflowClassifier
```

Default model:

```text
bjj3/1
```

Frame acquisition:

```python
from decision_vision.frame_stream import FrameStream
```

Persist only numeric features.

### 5. Evaluation

Use leave-one-match-out. Run three ablations:

```text
pose
position
fused
```

For each:

```text
leaf
family
category
```

Default model:

```python
StandardScaler()
LogisticRegression(class_weight="balanced")
```

For a held-out fold, if a test class does not occur in training:

```text
exclude from scored subset
increment skipped_unseen_class_samples
```

Always report evaluation coverage.

Do not select the best hyperparameters on held-out matches in this POC.

### 6. BJJ-specific pose adapter — separate follow-up

Do NOT guess keypoint order.

First call the public `bjj-recognition-model/1` on one disposable POC frame and
save only its JSON schema (coordinates may be rounded/redacted if desired).
Determine:

```text
number of keypoints
class names/order
whether it maps to COCO-17
```

Only if mapping is explicit, add an adapter implementing the same:

```python
frame -> list[np.ndarray shape (17,3)]
```

contract used by `PoseEstimator`.

Then rerun the exact same temporal evaluation:

```text
generic_pose
vs
bjj_pose
```

No other protocol changes.

### 7. External datasets

Registry is authoritative for this POC.

Enable now:

```text
bjj3             MIT
grappling_set    CC BY 4.0
bjj_recognition  CC BY 4.0
bjj_ai           Public Domain
mma_set          CC BY 4.0
```

Low priority / possible duplicate:

```text
rbflw_sample     MIT
```

Disabled:

```text
bjj_techniques   UNVERIFIED
```

If raw images are downloaded:

```text
data/external/decision_vision/<dataset-key>/
```

must remain gitignored.

Create:

```text
data/external/decision_vision/<dataset-key>/SOURCE.json
```

with:

```json
{
  "dataset_key": "...",
  "source_url": "...",
  "version": "...",
  "downloaded_at": "...",
  "license": "...",
  "modifications": "..."
}
```

Never commit raw data.

## Acceptance criteria

Commands:

```bash
uv run ruff check poc/decision_vision tests/poc
uv run pytest poc/decision_vision/test_common.py tests/poc/test_decision_vision_visual_labels.py
```

Then real POC:

```bash
PYTHONPATH=poc uv run --extra cv --extra postgres \
  python -m decision_vision.extract_frames ...

PYTHONPATH=poc uv run --extra cv --extra postgres \
  python -m decision_vision.temporal_features \
  --manifest data/cv_decision_poc/manifest.csv

PYTHONPATH=poc uv run --extra cv \
  python -m decision_vision.train_temporal \
  --data data/cv_decision_poc/temporal
```

Run must leave no `.jpg`, `.png`, `.mp4`, `.webm`, `.mkv` under
`data/cv_decision_poc/`.

Report must contain:

```text
per-feature-set
per-head
per-held-out-match fold
macro-F1
accuracy
uniform chance accuracy
evaluated samples
skipped unseen-class samples
```

## Stop conditions

Stop and report, do not paper over:

- pose pair detection rate < 0.70 overall
- bjj3 API unavailable / class contract changed
- taxonomy resolved rate remains poor after attempt collapse
- fewer than 3 independent matches remain evaluable
- all three feature sets remain at chance under leave-one-match-out

If all are chance, recommend temporal clip/motion supervision. Do not recommend
deeper RGB fine-tuning as the next automatic step.

### parallel: dataset-registry-test
file: tests/poc/test_external_dataset_registry.py
do: Validate every enabled external dataset has URL, verified license, commercial_use=true, and a non-empty poc_role; assert bjj_techniques is disabled/unverified.
sig: test_external_dataset_registry() -> None
done when: uv run pytest tests/poc/test_external_dataset_registry.py

### parallel: manifest-quality
file: poc/decision_vision/manifest_quality.py
do: Implement pure manifest_quality(frame) metrics with no IO and no DB access.
sig: manifest_quality(frame: pd.DataFrame) -> dict[str, int | float]
done when: uv run ruff check poc/decision_vision/manifest_quality.py

### parallel: attribution-doc
file: poc/decision_vision/ATTRIBUTION_EXTERNAL_DATASETS.md
do: Generate concise attribution entries for the enabled CC BY 4.0/MIT datasets from external_datasets.json; include source URL, author/project, license, and intended POC use.
sig: documentation only
done when: test -s poc/decision_vision/ATTRIBUTION_EXTERNAL_DATASETS.md


# Additional required POC: top/bottom and persistent athlete state

Implement this independently of criterion-classification success.

## Add files

```text
poc/decision_vision/remote_frame_sequence.py
poc/decision_vision/role_tracking.py
poc/decision_vision/build_role_timeline.py
tests/poc/test_decision_vision_role_tracking.py
```

Adapt the supplied `test_role_tracking.py` into the repo test location.

## Semantics

Never conflate:

```text
screen vertical ordering
BJJ top/bottom role
persistent athlete identity
```

Existing:

```python
PoseEstimator.select_grappler_pair(order_by="hip_y")
```

orders by image geometry. It MUST NOT be used as the top/bottom semantic label.

Use role-aware bjj3 detections:

```text
mount1 -> mount_top
mount2 -> mount_bottom
```

through the existing `cv.roboflow_classifier` + `cv.roboflow_labels`.

For role-less/symmetric labels (`standing`, `5050 guard`), emit role unknown /
symmetric. Never invent top/bottom.

## Efficient frame acquisition

Do not call one FFmpeg process per second for a full-match timeline.

Use one FFmpeg process:

```text
remote URL
 -> fps filter
 -> fixed-size BGR24 rawvideo
 -> stdout
```

Default cadence:

```text
1 frame / second
```

No media persistence.

## Identity tracker

Top/bottom is a state, not identity.

Maintain:

```text
track_0
track_1
```

using adjacent-frame bbox continuity:

```text
cost =
0.65 * normalized_center_distance
+
0.35 * (1 - IoU)
```

For two detections compare both direct and swapped assignments.

A sweep should look like:

```text
before:
track_0 top
track_1 bottom

after:
track_0 bottom
track_1 top
```

not:

```text
track_0 remains "top" by changing person
```

## Actual DB athlete identity

Anonymous tracking is valid output.

Do not use face recognition.

Provide explicit optional seed:

```text
--seed-top-athlete-id <uuid>
```

At the first role-resolved frame:
- bind the current top track to supplied athlete;
- bind the other track to the other Match participant;
- preserve these identities across subsequent role swaps.

If no seed is provided, athlete_id fields stay blank while track IDs remain.

## Outputs

```text
role_samples.csv
top_bottom_segments.csv
athlete_state_segments.csv
role_timeline_report.json
```

`role_samples.csv` minimum columns:

```text
timestamp
position
role_resolved
symmetric

top_track_id
top_athlete_id
top_confidence

bottom_track_id
bottom_athlete_id
bottom_confidence

track_0_role
track_0_position
track_0_athlete_id

track_1_role
track_1_position
track_1_athlete_id
```

`athlete_state_segments.csv` represents the state machine:

```text
track/athlete
start
end
position
role
```

## Acceptance

```bash
uv run pytest tests/poc/test_decision_vision_role_tracking.py
uv run ruff check poc/decision_vision tests/poc
```

Real run:

```bash
PYTHONPATH=poc uv run --extra cv --extra postgres \
  python -m decision_vision.build_role_timeline \
  --match-id <MATCH_UUID> \
  --sample-every 1.0
```

Assert no media:

```bash
find data/cv_decision_poc \
  \( -name '*.jpg' -o -name '*.png' -o -name '*.mp4' -o -name '*.webm' \) \
  -print
```

Expected: no output.

Manual validation:
- choose >= 3 matches from different venues;
- audit >= 20 predicted top/bottom state changes;
- record correct/incorrect switch and position.

Stop if:
- role-resolved rate < 0.70 outside symmetric states;
- tracker repeatedly changes identity without a real role/body transition;
- bjj3 labels systematically disagree with obvious top/bottom states.

### parallel: role-tracker-tests
file: tests/poc/test_decision_vision_role_tracking.py
do: Test role parsing, matched top/bottom pair selection, bbox identity continuity across a role swap, and explicit athlete-ID seeding.
sig: test_parse_vicos_label() -> None; test_select_role_pair_prefers_matching_top_bottom() -> None; test_identity_survives_role_swap_when_boxes_move_smoothly() -> None; test_explicit_top_seed_binds_real_athlete_ids() -> None
done when: uv run pytest tests/poc/test_decision_vision_role_tracking.py

### parallel: role-timeline-schema
file: tests/poc/test_decision_vision_role_timeline_schema.py
do: Validate role_samples and athlete_state segment column contracts from small synthetic DataFrames; no network, DB, FFmpeg, or Roboflow.
sig: test_role_sample_schema() -> None; test_athlete_segment_schema() -> None
done when: uv run pytest tests/poc/test_decision_vision_role_timeline_schema.py


# Required POC dashboard

Add a local read-only dashboard for progress and result inspection.

## Files

```text
poc/decision_vision/progress.py
poc/decision_vision/dashboard.py
tests/poc/test_decision_vision_progress.py
tests/poc/test_decision_vision_dashboard.py
```

Move/adapt the supplied POC tests to the repo's test location.

## Worker contract

Use `ProgressReporter` in:

```text
build_role_timeline.py
temporal_features.py
train_temporal.py
```

Also wire the legacy static-RGB `train.py` only if that baseline is still
actively rerun.

Workers communicate with the UI only by atomically replacing:

```text
<run-output>/progress.json
```

Required fields:

```text
run_id
pipeline
status
phase
current
total
percent
message
started_at
updated_at
finished_at
metrics
error
```

On failure call `reporter.fail(exc)` where practical, then re-raise. Never hide
the original failure.

## Dashboard architecture

FastAPI, local only:

```text
GET /
GET /api/runs
GET /api/run/{relative_run_path}
```

Default documented command:

```bash
PYTHONPATH=poc uv run --extra dev \
  uvicorn decision_vision.dashboard:app \
  --host 127.0.0.1 \
  --port 8765
```

Do not default to `0.0.0.0`.

Dashboard may read only:

```text
progress.json
role_timeline_report.json
report.json
training_report.json
role_samples.csv
top_bottom_segments.csv
athlete_state_segments.csv
samples.csv
```

Never expose through HTML/API:

```text
source_url
video/frame bytes
ROBOFLOW_API_KEY
DATABASE_URL
browser cookies
```

## UI requirements

Single page, no React/build step, no external frontend dependencies.

Show:

```text
1. run list
   pipeline / status / progress

2. current run
   phase / message / progress bar

3. state metrics
   role_resolved_rate
   role_switch_events
   tracker_reinitializations

4. top/bottom timeline
   horizontal time
   top lane
   bottom lane
   color by persistent track identity, not role

5. athlete states
   track/athlete
   role
   position
   start/end

6. probe results
   pose / position / fused
   leaf / family / category
   macro-F1
   accuracy
   evaluated samples
   skipped unseen-class samples
```

Poll every 1–2 seconds.

Completed runs without `progress.json` must still appear from historical result
files. Missing or malformed optional artifacts render an empty state instead of
HTTP 500.

## Acceptance

```bash
uv run pytest \
  tests/poc/test_decision_vision_progress.py \
  tests/poc/test_decision_vision_dashboard.py
```

```bash
uv run ruff check poc/decision_vision tests/poc
```

Manual:

```text
- start dashboard
- start a role timeline run
- progress changes without page reload
- top/bottom segments appear after run completes
- athlete tracks remain visually distinct across a role switch
- temporal probe table renders all feature/head combinations
- browser never requests a source video URL
```

### parallel: dashboard-tests
file: tests/poc/test_decision_vision_dashboard.py
do: Test /api/runs and /api/run using temp result dirs for running, historical completed, malformed progress, and missing optional artifacts.
sig: test_runs_running() -> None; test_runs_completed_without_progress() -> None; test_malformed_progress_is_ignored() -> None; test_missing_optional_artifacts() -> None
done when: uv run pytest tests/poc/test_decision_vision_dashboard.py

### parallel: progress-tests
file: tests/poc/test_decision_vision_progress.py
do: Test atomic progress lifecycle for update, complete, and fail states.
sig: test_progress_update() -> None; test_progress_complete() -> None; test_progress_fail() -> None
done when: uv run pytest tests/poc/test_decision_vision_progress.py
