# Work in flight — GrapplingArcAnalytics

Unfinished work, so a reorganisation cannot quietly drop it. Each entry says where it stopped, what
would prove it done, and what it blocks.

**This file is a register, not a plan.** Plans live in `docs/superpowers/plans/`.

Last updated: 2026-08-12.

---

## 1. Persistent identity — WIRED AND RERUN; gate half-passed

**Plan:** `docs/superpowers/plans/2026-08-12-decision-vision-identity.md`
**Audit + re-audit:** `data/cv_decision_poc/vicos_transfer_window3/switch_audit/vision_audit.md`

Five runs, kept side by side because each one is evidence of something:

| run | what it shows |
|---|---|
| `..._identity`  | the one-way door: `identity_resolved_rate` **0.10**, switches 0 — a broken tracker that LOOKED like a triumph |
| `..._identity2` | after the fix: coverage 0.645, but 4 switches sharing no timestamp with the audited set |
| `..._identity3` | per-frame track positions added; located the referee capture |
| **`..._identity4`** | **the good state.** Both true switches present (5336.0 recovered exactly, 5436.5 at +0.5s latency), all five known-bad ones gone |
| `..._identity5` | the identity-break reset — REGRESSION, switches 4 → 14, segments 33 → 140. Reverted |

**Gate status: half.** The question as posed — do the audited false switches disappear without
losing the true ones — is **YES**. But precision did not improve: `identity4` is 2 true / 2 false,
the same ratio the original `hip_y` run had. The false positives moved rather than reduced. Both
new ones (5352.0, 5378.5) were audited from frames and are false.

### Fixed along the way, each by measurement
- Aged-out tracks could never return (one-way door). Re-seeding is counted, not silent.
- Referee capture after a camera cut. **Three approaches were refuted by measurement first** —
  contact ratio (distributions overlap), temporal straightness (fires too late), cut-by-cost (the
  cut cost LESS than the frame before it). What worked: detect the cut in the IMAGE (grey-histogram
  distance; cuts 0.0166–0.1597 vs 0.0044 loudest normal) **and** seed from the CLOSEST pair, which
  needs no threshold. Neither works without the other.

### Open
- 5352.0 and 5378.5 are false and **uninvestigated**. Both sit immediately after a re-seed.
- The `identity_broken` flag rides on every row but nothing consumes it. The correct use is a
  boundary consumers refuse to compare ACROSS — re-establishing a role after a break must still
  require agreeing observations. **Do not** clear the committed role: that was tried and measured
  worse.
- Thresholds come from four cuts in ONE bout. Re-measure before another venue.

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

## 5. Frame annotation flow — foundation live, UI missing

**alembic 0029 is APPLIED to production** (verified: columns, both check constraints, RLS on with
zero policies, and anon denied — proven by inserting a row as the service role and confirming anon
still reads `[]`, because an empty table makes `[]` ambiguous).

`poc/decision_vision/prelabel_frames.py` walks the **1936 events** that carry both a
`matches.video_url` and an `ts`, runs the full pipeline with 4s of lead-in, and writes a proposal
into `frame_annotations`. Prediction and correction are separate columns so the record of where the
model was wrong survives the fix; the upsert's `where status = 'pending'` means a re-run can never
erase a human decision.

**Sample of 5 matches (21 frames), measured:**

| | |
|---|---|
| identity resolved | **86%** (better than the audited window's 65% — the lead-in works) |
| unresolved | 14%, all tracker refusals, none from a missing frame |
| `role` = `none` | **67%** of resolved frames |
| agreement on comparable labels | **0 / 3** (`Mount`→takedown, `Takedown`→back, `Mount`→5050_guard) |

Only 3 of 21 labels are comparable at all: the rest are TECHNIQUES (`Pass`, `Rear Naked Choke`,
`Sweep`), and a technique is not a ViCoS position. **The review UI must say this**, or every
reviewer will reject everything for the wrong reason — the human label is context, not ground truth.

**What this means for the product:** with `role` empty two thirds of the time and position wrong on
the few verifiable cases, this will not save annotation labour. Its value is **collecting the
corrections** — the set that says where the model errs, which is exactly what was missing every time
a defect was chased today. So in the UI, **correcting must be cheaper than approving**, which is the
inverse of the usual design.

Missing: the review UI in `admin/`. Also worth doing first — a query over labels that map to ViCoS
classes would give dozens of verifiable cases and a real position-accuracy number for free.

## 6. Pending, lower urgency

- **Persistence-in-time** (≥N agreeing observations AND ≥X seconds) — worth adding, but **after**
  identity. Applied first it sustains the wrong identity instead of filtering a spike.
- **Re-audit** the four committed switches against `vision_audit.md` once the rerun exists.
- `scratch_audit_frames.py` at the repo root is superseded by `poc/decision_vision/audit_frames.py`.
