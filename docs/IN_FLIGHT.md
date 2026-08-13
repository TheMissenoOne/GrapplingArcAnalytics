# Work in flight — GrapplingArcAnalytics

Unfinished work, so a reorganisation cannot quietly drop it. Each entry says where it stopped, what
would prove it done, and what it blocks.

**This file is a register, not a plan.** Plans live in `docs/superpowers/plans/`.

Last updated: 2026-08-12.

---

## 1. Persistent identity in the CV pipeline — WIRED, NOT YET RERUN

**Plan:** `docs/superpowers/plans/2026-08-12-decision-vision-identity.md`
**Evidence:** `data/cv_decision_poc/vicos_transfer_window3/switch_audit/vision_audit.md`

### Why it matters
`athlete1`/`athlete2` are a per-frame screen-space sort (`cv/pose_estimate.py:154-157`,
`sorted(top_two, key=_hip_y)`), recomputed with no memory. They change meaning when a body inverts.
This corrupts `pair_to_features` itself — it is not symmetric, and `vicos_state.py:261` records that
**ViCoS `athlete_idx` is persistent identity, not geometry (~0.47 hip-y correlation)** — so `state`
and `position` are exposed too, not only `role`.

### Where it stopped
- `PoseIdentityTracker` **written** in `poc/decision_vision/role_tracking.py`, beside the untouched
  `PairIdentityTracker`. Hungarian assignment (`scipy.optimize.linear_sum_assignment`) over ALL
  candidates, cost `0.6·bbox + 0.4·keypoint-displacement`, rejection above
  `POSE_IDENTITY_COST_MAX = 0.5`, counters for `reinitializations`, `assignment_swaps`,
  `third_person_rejections`.
- 4 synthetic tests **written** in `tests/poc/` (crossing, hip_y-flip-must-not-swap, third person
  rejected, dropout reinit).
- Tests **run** (2026-08-12). Three were broken — two failed, and one passed while asserting
  nothing (`False == False`), because the synthetic helper puts keypoint 0 at
  `(cx - scale, cy - scale)` and the assertions read it as the centre. **The tracker was correct;
  the tests were not.** Rewritten to assert IDENTITY (is this the same array we handed in?) and
  checked with a negative control. 41 tests in `tests/poc`, ruff clean.
- `live_state.py` **wired**: `select_grappler_pair` replaced by `PoseIdentityTracker`; an
  unresolved frame is emitted as unusable rather than guessed; `identity_resolved` rides on every
  row; the four metrics land in `report.json["identity"]` and in the progress metrics. The module
  docstring said "order the pair by hip_y" — corrected, it had just become false.
- **Rerun 1 (broken tracker)** exposed a one-way door: `missing > max_missing` removed a track from
  `active` and only active tracks are assigned, so one brief occlusion killed identity permanently.
  `identity_resolved_rate` 0.10. Fixed (`72bf427`) with counted re-seeding; the run is kept at
  `data/cv_decision_poc/vicos_transfer_window3_identity/` as evidence of the defect, NOT a baseline.
- **Rerun 2 (fixed)** → `data/cv_decision_poc/vicos_transfer_window3_identity2/`.
  Coverage healthy: `identity_resolved_rate` **0.645** vs the old `pose_pair_rate` 0.675.
  `tracker_reinitializations` 11, `assignment_swaps` 55.
  ⚠️ **The gate did NOT pass.** Committed switches stayed at 4, and NONE share a timestamp with the
  audited set:

  | switch | audited verdict | after |
  |---|---|---|
  | 5313.5 | **false** (corr 29%/0%) | gone ✓ |
  | 5415.0 | **false** (failed sweep) | gone ✓ |
  | 5336.0 | **true** (a2 takes over) | **gone** ✗ |
  | 5436.0 | **true** (corr 75%/100%) | 5436.5 — same event, one sample later (latency, not loss) |

  New and unaudited: **5326.0**, **5368.5**, **5461.5**. The first two are recidivists — `pass2`
  had called 5326 "weak, borderline" and 5369.5 **spurious**, and the 0.85 confidence floor had
  removed them. Their return suggests that floor was masking identity instability rather than
  fixing it.

  Trading four switches for four others is evidence of CHANGE, not of improvement. Only visual
  inspection of the new ones — and of why 5336.0 vanished — decides.
- **Step 7 DONE.** All five re-audited from frames in `switch_audit/frames_identity/`; verdicts and
  evidence appended to `vision_audit.md`. Scoreboard: **before 2 true / 2 false → after 1 true (0.5s
  late), 2 false, 1 post-bout artefact, and 1 TRUE SWITCH LOST (5336.0)**.
  **The gate does not pass.** The tracker removed both audited false switches — what it was built to
  do — but 5336.0 went with them, and 5326.0 / 5368.5 / 5461.5 took their place.

### Next, in this order
1. **Bout-boundary guard.** 5461.5 sits where the clock runs 00:02 → 00:00 and the athletes
   separate. Nothing should commit a role switch after the bout ends. Cheapest, removes one outright.
2. **Investigate 5336.0.** Losing a true switch is the only outcome strictly worse than before, and
   the cause is unknown — today's re-seeding, a reinitialization landing there, or the probe reading
   a correctly-ordered pair differently. Start with `identity_resolved` and the reinit count around
   5330–5340 in `..._identity2/state_samples_raw.csv`. **Do not touch a threshold before this is
   understood.**
3. 5326.0 and 5368.5 are recidivists the 0.85 floor had removed. Their return is evidence that floor
   was masking identity instability rather than fixing it.

### Done when
Tests green; `live_state.py` uses the tracker; the controlled rerun of the SAME window (5292–5592,
600 frames, same probes, same smoothing) shows the false switches at 5313.5 and 5415 gone
**without** losing the two visually true ones.

### Blocks
WNO and Worlds. Do not go cross-venue before this — different camera work and more people in frame
amplify exactly this failure.

---

## 2. `segments.csv` hides a role switch — LOCATED, NOT FIXED

Precise mechanism, pinned to lines:

| line | behaviour |
|---|---|
| `live_state.py:187` | spans keyed on `(position, role, state)` — the role boundary IS created |
| `live_state.py:201` | the min-duration squash regroups by `(position, state)` — **role-blind** — and merges across it |
| `live_state.py:245,250` | the squash writes back only `position`/`state`, which is why the ROWS keep the right role |

That is why `state_samples.csv` carries `athlete1` from 5415.0 while `segments.csv` shows `athlete2`
for `standing 5414.5→5424.5`.

**Fix:** rebuild spans from the FINAL smoothed rows, keyed on all three dimensions.
**Not implemented** — deliberately deferred while another change owned `live_state.py`.

⚠️ An earlier write-up called this "carries the role from the segment's start". That is the symptom.
Fixing that would touch the wrong code.

---

## 3. `live_state.py` invocation — reported, NOT reproduced

Reported as: `uv run python poc/decision_vision/live_state.py` dies with `ModuleNotFoundError: No
module named 'cv'`.

**Could not reproduce on 2026-08-12.** Both `-m` from the repo root and the direct script path
import `cv` fine under `uv run`, which puts the project root on `sys.path`. Note the trap that made
a first check worthless: `--help` exits in argparse *before* `main()` imports `cv`, so it proves
nothing. Still worth settling on ONE documented invocation rather than leaving two, but treat the
defect as unconfirmed until someone reproduces it.

---

## 4. Version control — DONE (2026-08-12, `de70ef2`)

`poc/` and `tests/poc/` are tracked (~656KB of source), along with the findings. The pixels and the
weights are gitignored: `data/cv_decision_poc/` is ~117MB, of which ~86MB is model artifacts and
~27MB is audit frames `audit_frames.py` regenerates from a match id and a window.

Promotion criteria out of POC status: see `INDEX.md`.

---

## 5. Pending, lower urgency

- **Persistence-in-time** (≥N agreeing observations AND ≥X seconds) — worth adding, but **after**
  identity. Applied first it sustains the wrong identity instead of filtering a spike.
- **Re-audit** the four committed switches against `vision_audit.md` once the rerun exists.
- `scratch_audit_frames.py` at the repo root is superseded by `poc/decision_vision/audit_frames.py`.
