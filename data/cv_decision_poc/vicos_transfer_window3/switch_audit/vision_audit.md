# Decision Vision — Visual Role-Switch Audit

> Companion to `pass2_switch_audit.md`, which is **not** superseded or edited by this file.
> That one audits the temporal engine's own bookkeeping; this one audits the *images*.

## Method

**Blind-first, and the order is recorded in this file's own history.** Sections "Visual sequence",
"Blind verdict" and "Evidence" for both candidates were written and saved to disk **before** any
pipeline output for those timestamps was opened — no `state_samples*.csv`, no `segments.csv`, no
`pass2_switch_audit.md`, no overlay. The "Pipeline comparison" sections were appended afterwards.

**Contamination disclosure (full).** While inventorying the CSV *schema* before the audit began, I
saw the header and the first two data rows of `state_samples_raw.csv` — timestamps `5292.0` and
`5292.5`. Both are >20s away from either candidate and neither was consulted for a verdict. I also
read `vicos_state.py` and `role_tracking.py` as source code (not as predictions) to understand what
`role` means. Declared rather than omitted.

**Identity.** Neutral labels only; no face recognition, no attempt to name either competitor.
The two are separable by stable, non-biometric appearance:

| label | appearance |
|---|---|
| **Athlete A** | dark/black gi, orange trim, shaved head |
| **Athlete B** | white gi, black belt, dark curly hair |

These cues are unusually strong (whole-garment colour contrast), so A/B could be followed by eye
through every frame of both windows with no ambiguity.

**Screen-Y was NOT used as a proxy for role.** Judgement was made from weight-bearing relationship,
torso orientation, whose back is on the mat, guard/pin configuration, and continuity of motion
across neighbouring frames — never from who sits higher in the image. Several frames here are
explicitly cases where the two disagree.

**Evidence base.** 71 frames extracted for this audit into `frames/` (see "Provenance"), reviewed as
chronological sequences via contact sheets, then at full resolution for every decisive frame.

### Provenance — the requested frames did not exist

The audit request stated that ~18 PNGs were already prepared around 5313.5s and 5415s. **They were
not.** The 18 pre-existing `sw*.png` files in this directory cover switches at 5326, 5336, 5356,
5358, 5369 and 5377 — none within 12s of either candidate — and were written at 01:06 while the
current run's CSVs are timestamped 09:13, i.e. they belong to a **previous, pre-fix** switch set.
No frame anywhere in the repo lay near 5313 or 5415.

The evidence was therefore produced for this audit with `poc/decision_vision/audit_frames.py`
(a parameterised generalisation of the throwaway `scratch_audit_frames.py`, which had
`SWITCHES = [5377.5]` hardcoded and emitted only 3 frames per event):

| window | sampling | frames | actual span |
|---|---|---|---|
| 5308–5320 | 0.5s | 24 | 5308.00 → 5319.50 |
| 5410–5427 | 0.5s | 35 | 5410.00 → 5427.00 |
| 5427–5440 | 1.0s | 13 | 5427.00 → 5439.00 |

Filenames carry only a timestamp (`frame_<ts>s.png`) — no predicted role, position or state — so
the images could not leak a verdict during the blind pass.

---

## 5313.5s

### Visual sequence

| frame | A role | B role | interpretation |
|---|---|---|---|
| `frame_05308.00s.png` | top | bottom | A kneeling over B, stacking him; B's back on the mat, legs folded up |
| `frame_05309.00s.png` | top | bottom | unchanged; A driving forward pressure |
| `frame_05309.50s.png` | top | bottom | camera pushes to a tight close-up; same relationship |
| `frame_05310.00s.png` – `frame_05313.00s.png` | top | bottom | sustained stack/pressure, B flattened on his back, A's weight on him |
| `frame_05313.50s.png` | top | bottom | last close-up frame; relationship still A over B |
| `frame_05314.00s.png` | — | — | **motion-blurred; hard camera cut** |
| `frame_05314.50s.png` | top | bottom | wide angle now. A kneeling over supine B — **and a standing referee enters frame** |
| `frame_05316.00s.png` | top | bottom | unambiguous at full resolution: A upright over B, B supine |
| `frame_05317.50s.png` – `frame_05319.50s.png` | top | bottom | unchanged; B's legs elevate (guard retention), A stays on top |

### Blind verdict

```
classification:        false_switch
confidence:            high
identity continuity:   stable
```

No top/bottom exchange occurs anywhere in this window. A is the top player in the first frame and
the top player in the last. The gi colours make this impossible to confuse.

### Evidence

The only thing that actually changes across the candidate instant is **the camera**, not the
grappling. `frame_05313.50s.png` is the last frame of a tight close-up; `frame_05314.00s.png` is
motion-blurred mid-cut; `frame_05314.50s.png` is a wide shot from a different angle. Across that
cut, the athletes' apparent scale, orientation and image position all change abruptly while their
actual relationship does not.

Two confounds are introduced by that cut, both visible from `frame_05314.50s.png` onward:

1. **A third person enters the frame.** The referee — dark clothing, standing, fully upright — is
   in shot for the rest of the window. In a wide shot he is *more cleanly posed* than the two
   entangled athletes, whose limbs overlap.
2. **Scale collapse.** The athletes go from filling the frame to occupying a small fraction of it,
   so keypoint precision drops exactly at the moment the pipeline commits.

### Pipeline comparison

Raw observations (`state_samples_raw.csv`), role column with `role_conf`:

| ts | position (conf) | role (conf) | pose_pair |
|---|---|---|---|
| 5312.5 | back (0.73) | athlete2 (0.70) | True |
| 5313.0 | takedown (0.53) | athlete2 (0.72) | True |
| **5313.5** | **5050_guard (0.55)** | **athlete1 (0.878)** | True |
| 5314.0 | unknown (0.00) | unknown (0.00) | **False** |
| 5314.5 → 5318.0 | takedown/standing (0.27–0.64) | **none** (0.57–0.92) | True |

```
committed switch:   athlete2 -> athlete1 @ 5313.5
corroboration:      29% pre / 0% post   (pass2_switch_audit.md)
visual verdict:     false_switch (high confidence)
agreement:          NO
```

**Failure mode: a single isolated frame above the floor.** `5313.5` is the *only* observation in
the neighbourhood at or above the 0.85 commit floor (0.878). It is immediately preceded by two
`athlete2` readings and followed by `unknown` then a nine-second run of `none`. Nothing ever agrees
with it again — the 0% post-corroboration already recorded in `pass2_switch_audit.md` is exactly
what the images show.

Two compounding details:

- The position label that produced this role is **`5050_guard` at 0.55 confidence**, while the
  imagery shows a stack/pressure pass. Since `role_head()` (`vicos_state.py:87`) derives the role
  purely from the position label's `1`/`2` suffix, a wrong position label *is* a wrong role. There
  is no independent role evidence to contradict it.
- The commit lands one sample before the hard camera cut, and the whole run of `none` that follows
  is the wide shot, where the athletes are small and a standing referee is in frame.

### Recommendation

Reject. This is the clearest possible case for elapsed-time persistence: a lone frame, contradicted
by both neighbours and by everything after it.

---

## 5415s

### Visual sequence

| frame | A role | B role | interpretation |
|---|---|---|---|
| `frame_05410.00s.png` | top | bottom | B on his back playing guard; A postured over him, controlled |
| `frame_05412.00s.png` | top | bottom | unchanged; A stacked forward, B framing from bottom |
| `frame_05413.50s.png` | top | bottom | B begins elevating A — sweep/inversion entry |
| `frame_05414.00s.png` | uncertain | bottom | A's base breaking, his hips rising |
| `frame_05415.00s.png` | uncertain | uncertain | A being lifted; neither player is supporting the other's weight |
| `frame_05416.00s.png` | uncertain | uncertain | A inverting over B |
| `frame_05416.50s.png` | **none (inverted)** | bottom | full-res decisive frame: A is upside down, head toward the mat, legs in the air, gi jacket pulled open; B is on his back underneath, doing the lifting. **Top/bottom is genuinely undefined here** |
| `frame_05417.00s.png` | — | — | motion-blurred; **hard camera cut** |
| `frame_05417.50s.png` | top | bottom | wide angle; A landing/settling over B |
| `frame_05419.00s.png` | top | bottom | full-res: B on his side on the mat, A upright over him. Referee at frame edge |
| `frame_05421.50s.png` | top | bottom | A consolidating on top |

### Blind verdict

```
classification:        false_switch   (a real scramble, but NO stable role exchange)
confidence:            medium-high
identity continuity:   stable
```

Something genuinely happens here — unlike 5313.5, this is not a camera artefact. B attempts a
sweep and briefly inverts A. But the attempt **fails**: A is never replaced as top player, and B
is never established on top. Before: A top. After: A top. Net exchange: none.

The honest description of `frame_05415.00s.png` – `frame_05416.50s.png` is not "B is top" but
"**nobody is top**" — a transitional inversion, which is exactly the symmetric/undefined class the
audit brief warns against forcing into a binary.

### Evidence

Weight-bearing is the criterion, and it never transfers. At `frame_05416.50s.png` A's head is
toward the mat and his legs are vertical while B lies on his back — B is *lifting*, not *mounting*.
By `frame_05418.00s.png` A's weight is back on B, and it stays there for the remaining 21 seconds
of the tail (below).

Note a second confound beginning here: **A's gi jacket comes open during the inversion**
(`frame_05416.50s.png` shows bare torso). It stays open through `frame_05427.00s.png` –
`frame_05431.00s.png`, where the referee stops the action for him to refit it. A silhouette that
changes from "black gi" to "bare torso" mid-sequence is a substantial appearance change for any
tracker.

### Pipeline comparison

| ts | position (conf) | role (conf) | pose_pair |
|---|---|---|---|
| 5412.0 | back (0.76) | athlete2 (0.60) | True |
| 5412.5 → 5414.0 | unknown | unknown | **False** (4 consecutive) |
| 5414.5 | mount (0.80) | athlete2 (0.68) | True |
| **5415.0** | **back (0.955)** | **athlete1 (0.900)** | True |
| 5415.5 | unknown | unknown | False |
| 5416.0 | mount (0.79) | athlete2 (0.60) | True |
| 5417.0 | back (0.30) | athlete2 (0.57) | True |
| 5418.0 | standing (0.54) | athlete1 (0.48) | True |
| 5419.5 | mount (0.45) | athlete2 (0.914) | True |

```
pipeline switch timestamp:        5415.0
estimated visual switch time:     none — no reversal ever completes
difference:                       n/a (this is false detection, NOT latency)
corroboration:                    33% pre / 50% post  ("weak", pass2)
visual verdict:                   false_switch (medium-high confidence)
agreement:                        NO
```

The latency-vs-false-detection question the brief asks has a clean answer here: **false detection.**
There is no later moment in 5410–5440 at which the roles actually exchange, so the commit is not an
early or late detection of a real event — there is no real event to be late for.

**Failure mode: the hip-Y ordering flips when a body inverts.** `select_grappler_pair`
(`cv/pose_estimate.py:154-157`) takes the two largest bounding boxes and then orders them:

```python
top_two = sorted(poses, key=_bbox_area, reverse=True)[:2]
kp0, kp1 = sorted(top_two, key=_hip_y)   # smaller hip-y (higher) first
```

`athlete1` vs `athlete2` therefore *is* a per-frame screen-Y sort — recomputed from scratch on every
frame, with no memory. At `frame_05416.50s.png` A is upside down, so **A's hips rise above B's in
image space and the sort order swaps** — without any grappling role having changed. The one
high-confidence frame that triggered the commit (5415.0, role conf 0.900, position `back` at 0.955)
sits exactly inside that inversion.

This is the precise proxy the audit brief forbids a human from using, implemented as the pipeline's
identity rule.

Two further observations from this window:

- **The position head is wrong here, not just the role.** The smoothed timeline reads `standing`
  from 5414.5 through 5424.5 and again 5428.0–5447.5, while the imagery shows both athletes on the
  mat for all of 5418–5439. Role is derived from position, so role cannot be more trustworthy than
  the label it is read off.
- `pose_pair=False` for four consecutive samples (5412.5–5414.0) — the detector lost the pair right
  as the sweep began, so the commit rests on the first frame it recovered.

---

## 5410–5440 context

The wider tail settles the question that 5 frames could not.

- `frame_05418.00s.png` – `frame_05426.50s.png` — A consolidates on top; B is flattened and
  defensive. The referee closes in and leans over the pair.
- `frame_05427.00s.png` – `frame_05431.00s.png` — action **stopped by the referee**; A's gi is off
  the shoulders (bare torso clearly visible) and is refitted. Both athletes remain roughly in place,
  a third person (the referee) is centrally in frame, and there is no grappling to classify.
- `frame_05432.00s.png` – `frame_05439.00s.png` — restart; A back in the gi (`AOJ` visible on the
  back), on top of B (`M. GABRIEL` visible on the back), controlling from side/top through the end
  of the window.

So over the full 30-second tail, **A is the top player at the start and at the end**, with one
failed inversion (~5414–5417) and one refereed stoppage (~5427–5431). No sustained reversal ever
occurs. If the pipeline committed a role switch anywhere in 5410–5440, the imagery does not support
a *lasting* exchange — at most it supports a ~2–3s window of genuinely undefined role.

---

## Identity-continuity audit (Phase 3)

Overlays produced by `poc/decision_vision/audit_overlay.py`, which re-runs the same
`PoseEstimator` + `select_grappler_pair` the pipeline uses and draws which detections were chosen.

```
visual identity continuity:   stable   (both events)
pipeline identity continuity: NOT APPLICABLE — no identity is tracked
```

**Visually stable.** A black gi and a white gi never become confusable; A and B are followable by
eye in all 71 frames, including across both camera cuts. So a supposed role switch here cannot be
excused as "the tracker started following the other person" — there is no *visual* ambiguity.

**But the pipeline holds no identity at all.** Three findings, each verified in source:

1. **No tracker is wired into this path.** `role_tracking.py:PairIdentityTracker` (IoU + distance
   association) exists but is only used by `build_role_timeline.py`. The run that produced this
   directory came from `live_state.py`, which never instantiates it. `PoseEstimator` is stateless
   across calls.
2. **Ordering is by image-space hip Y**, recomputed per frame (`cv/pose_estimate.py:157`). Nothing
   carries over between frames, so "athlete1" at 5414.5 and "athlete1" at 5415.0 are not
   guaranteed to be the same human being.
3. **Pair selection is by bounding-box area alone**, with no person-vs-official filtering — YOLO
   emits a single `person` class. Across the 71 audited frames the detection count was
   `{1: 23, 2: 32, 3: 12, 4: 2, 5: 2}`: **16 frames (23%) contained 3+ people.** A referee who is
   closer to camera or more fully extended than a folded-up athlete can win a top-2-by-area
   contest. In the audited frames he happened not to — that is geometry, not a safeguard.

Consequence for the smoothing layer: both readings straddling the 5417 cut are below the 0.85
floor, so the sticky-carry rule holds the last committed role straight through the cut **without
any check that the body on the other side is the same one.** A hard camera cut is indistinguishable
from a momentary detector dropout, so the smoother papers over an identity change exactly as it
papers over noise.

### Caveat on the overlay evidence

The audit frames were sampled at `output_size=640`; the original run streamed at `320`. Re-running
the detector on the 640px frames does not always reproduce the recorded detections at the margin
(e.g. `frame_05314.00s.png` yields 2 people now, while the run recorded `pose_pair=False`). The
overlay therefore sources `role`/`position`/confidences **from the recorded CSV**, not from
re-inference, and the detection counts above should be read as representative of the scene, not as
a byte-exact replay of the run.

## Audit-tooling defects found (documented, not silently fixed)

Reported here rather than patched, per the audit's own ground rules.

1. **`segments.csv` can hide a committed role switch.** It reports the role at each segment's
   *start* and segments on position/state. The 5415.0 switch happens inside the `standing` segment
   `5414.5 → 5424.5`, which is stamped `athlete2` — while `state_samples.csv` carries `athlete1`
   from 5415.0 onward. Reading segments alone, this switch is invisible.
2. **`live_state.py` cannot be run as a script.** `uv run python poc/decision_vision/live_state.py`
   dies with `ModuleNotFoundError: No module named 'cv'` — script-mode `sys.path[0]` is the file's
   own directory and nothing prepends the repo root before `from cv.pose_estimate import ...`. It
   only works via `-m`/`-c` from the repo root or with `PYTHONPATH` pre-set. Unrelated to this
   audit; left untouched.

## Summary

| candidate | visual verdict | confidence | identity stable? | pipeline agreement |
|---|---|---|---|---|
| 5313.5 | `false_switch` — no exchange at all; camera cut + referee enters frame | high | visually yes; pipeline tracks none | **no** |
| 5415 | `false_switch` — real scramble, failed sweep, no stable exchange | medium-high | visually yes; pipeline tracks none | **no** |

Both committed switches rest on **exactly one frame** above the 0.85 confidence floor, with every
neighbouring explicit observation disagreeing.

## Temporal-engine conclusion

**Recommendation: C — investigate tracker identity continuity first.**

Elapsed-time persistence (option B) *would* reject both of these commits, and cheaply: each rests on
a single isolated frame, so any rule demanding two agreeing high-confidence observations spanning a
minimum wall-clock interval kills both. On the evidence of these two audits alone, B is a correct
and worthwhile change, and nothing here argues against adding it.

It is nevertheless the wrong thing to do *first*, because it treats the symptom of 5415 rather than
its cause. The 5415 flip was not noise that more patience would filter out — it was the pair
ordering genuinely inverting because `athlete1`/`athlete2` is a per-frame sort on image-space hip Y
and one athlete turned upside down. A time-based rule suppresses that flip only because it was
brief; a longer inversion, a scramble on a differently-angled camera, or a referee winning a
top-2-by-area contest would produce a *sustained* wrong ordering that persistence would then
faithfully commit. Persistence makes a stable identity more valuable, it does not supply one.

Cross-venue testing is precisely the condition that stresses this: different camera heights,
framing, cut rhythms and officials in frame. Going there with no identity tracker (`PairIdentityTracker`
is written but unwired), ordering by screen-Y, and 23% of frames containing a third person would
produce results whose failures could not be attributed — one could not tell a bad probe from a
swapped pair.

Concretely, before cross-venue evaluation: wire the existing `PairIdentityTracker` into the
`live_state.py` path, exclude non-athletes from pair selection, and emit the chosen track ids into
`state_samples_raw.csv` so a switch can be audited against *who* rather than against *which sort
position*. Then add elapsed-time persistence, and re-run this same visual audit to confirm both
5313.5 and 5415 stop committing.

Adding elapsed-time persistence **now**, on its own, would make the metrics look better while
leaving the failure that produced 5415 fully intact. Worse: persistence would then be sustaining
the wrong identity rather than filtering a spike — "abundant evidence of the wrong answer".

**Follow-up plan:** `docs/superpowers/plans/2026-08-12-decision-vision-identity.md` turns this into
ordered work, and records the wider consequence this audit surfaced — that the per-frame ordering
corrupts `pair_to_features` itself (it is not symmetric, and ViCoS was trained on a persistent
ordering), so `state` and `position` are exposed too, not only `role`. It also notes that the
role-tracking gate is **no longer considered passed**: smoothing passed its own gate honestly, but
was being fed unstable identities.
