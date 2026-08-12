# Decision Vision — persistent identity at the pipeline input

**Status:** planned, not started · **Supersedes** the "go to cross-venue next" assumption
**Evidence:** `data/cv_decision_poc/vicos_transfer_window3/switch_audit/vision_audit.md`

## The finding

The visual audit's headline is not "two of the four committed switches were false". It is that
**the input stage provides no persistent identity**, and the audit's false switches are a symptom.

What the pipeline effectively does today:

```text
frame t → detect people → take 2 largest boxes → order by hip_y → athlete1 / athlete2
        → pair_to_features → ViCoS probe
```

`cv/pose_estimate.py:154-157`:

```python
top_two = sorted(poses, key=_bbox_area, reverse=True)[:2]
kp0, kp1 = sorted(top_two, key=_hip_y)   # smaller hip-y (higher) first
```

So `athlete1` / `athlete2` are a **per-frame screen-space sort**, recomputed from scratch with no
memory. They change meaning the moment the bodies change geometry. We have been interpreting `role`
as though those indices were persistent identity; they never were.

### This contaminates more than `role`

`pair_to_features(A, B)` is not equivalent to `pair_to_features(B, A)`, and the ViCoS training set
assumes a persistent ordering of the two athletes. So an ordering flip corrupts the **feature
vector**, not merely the label read off it — `state` and `position` are exposed too, not just
`role`.

### And `role` is not an independent head

`vicos_state.py:87` — `role_head()` reads the suffix of the position label:

```text
role-aware state
   ↓
half_guard1
   ├─ position = half_guard
   └─ role     = athlete1
```

So the mental model of three independent heads (position / role / state) is wrong. It is one
role-aware state label, decomposed. Which means:

```text
athlete1/athlete2 wrong  →  role wrong, automatically
```

even when the positional geometry is excellent. That is exactly how the audit found **plausible
position and absurd role at the same timestamp**.

## Target architecture

```text
YOLO pose detections
        ↓
candidate filtering
        ↓
PairIdentityTracker
        ↓
track_0 ──── stable order ──── track_1
        ↓
pair_to_features(track_0.pose, track_1.pose)
        ↓
ViCoS probes
```

`hip_y` may remain as geometric information. **It must never again define identity.**

The contract:

```text
track_0 at frame 100 == the same human as track_0 at frame 101,
regardless of: who is on top · orientation · sweep · inversion · screen position.
```

## Work, in order

1. **Wire `PairIdentityTracker` into `live_state.py`.** It already exists
   (`poc/decision_vision/role_tracking.py`, IoU + distance association) but is only used by
   `build_role_timeline.py`. The run that produced `switch_audit` came from `live_state.py`, which
   never instantiates it. It must consume the detected poses/bboxes and emit the persistent order
   that `pair_to_features` consumes.
2. **Stop blindly taking the two largest people.** 16/71 audited frames (23%) contained 3+ people;
   the referee is a real case, not a hypothetical, and can be larger in frame than a folded-up
   athlete. Once the two athletes are initialised, selection must favour continuity with the
   existing tracks — association cost over bbox-centre displacement + IoU + keypoint displacement,
   best assignment — instead of `largest_boxes[:2]`.
3. **Make non-correspondence explicit.** When the tracker cannot say who is who with confidence,
   emit `identity_resolved = false`. Dropping frames is strictly better than silently letting
   `track_0` become a different person between frames.
4. **Instrument it**, or the rerun proves nothing: `identity_resolved_rate`,
   `tracker_reinitializations`, `assignment_swaps`, `third_person_rejections`.
5. **Fix `segments.csv` semantics** (below).
6. **Rerun the SAME Spyder window** (below).
7. **Re-audit the four switches** against `vision_audit.md`.
8. Only if that passes: WNO, then Worlds.
9. **Persistence-in-time as hardening** — last, not first (below).

## The rerun is a controlled experiment

Change one variable. Same video, same window `5292–5592`, same 600 frames, same probes, same
smoothing. Only the athlete index changes: `hip_y` ordering → persistent visual track.

| metric | before | after |
|---|---:|---|
| pose pair coverage | baseline | ? |
| identity resolved | — | ? |
| raw position flips/min | 39.1 | ? |
| raw role flips/min | 4.2 | ? |
| smoothed role flips/min | 0.8 | ? |
| committed switches | 4 | ? |
| visually true switches | 2/4 | ? |
| identity reinitializations | — | ? |

The headline question is simpler than the table:

> **Do the false switches at 5313.5 and 5415 disappear WITHOUT eliminating the two visually true
> ones?**

This is a regression test over already-audited video — not a target to tune the algorithm toward.
Do not hardcode it as an expectation.

### 5415 is the valuable fixture

The athlete radically changes image geometry and comes back:

```text
top → inverted, head down → sweep attempt → still top
```

`hip_y` identity *should* fail there; persistent tracking *should* survive it. Regression fixture,
conceptually:

```text
window 5410–5440
expected: visual identity A stays A · visual identity B stays B · no net BJJ role switch
```

No video in git — an audit/reproduction command plus metadata is enough. Frames regenerate with
`poc/decision_vision/audit_frames.py`.

## Persistence-in-time is a SECOND defence, not the first

It would reject both audited false switches, and it is worth adding. But not before identity, or it
launders the wrong answer:

```text
wrong identity sustained for 5 seconds
        ↓ persistence-in-time
"excellent — we have abundant evidence of the wrong identity"
```

**Persistence solves spikes. The tracker solves semantics.** Different problems.

## Tooling defects to fix now (they will obstruct the next validation)

1. **`segments.csv` can hide a role switch.** It carries the role from a segment's start and
   segments on position/state, so the 5415.0 switch is invisible inside the `standing`
   `5414.5→5424.5` segment while `state_samples.csv` carries `athlete1` from 5415.0. Either split
   into `position_segments.csv` / `role_segments.csv` / `state_segments.csv`, or emit a composite
   `(position, role, state)` segment with a boundary whenever ANY semantically relevant dimension
   changes.
2. **`live_state.py` has two partially-working invocations.** `uv run python
   poc/decision_vision/live_state.py` dies with `ModuleNotFoundError: No module named 'cv'`
   (script-mode `sys.path[0]` is the file's own directory). Pick one: make
   `PYTHONPATH=poc uv run python -m decision_vision.live_state` the single supported path, or fix
   the imports so direct execution works. Do not leave both half-working.

## Gate status

**The role-tracking gate is no longer considered passed.** The smoothing layer passed its own gate
honestly — but we now know it was being fed unstable identities. The correct gate is:

```text
pose → persistent identity → role-aware state → smoothing
```

The good news is the defect is well localised and `PairIdentityTracker` already exists.
