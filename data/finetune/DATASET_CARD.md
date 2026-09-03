# Dataset card — GrapplingArc vision dataset (v1)

Generated 2026-09-03T01:20:56+00:00 by `scripts/vision_dataset.py`. **Generated file — do not hand-edit**;
change the builder or the source answers and rebuild.

## What it is

Competition grappling footage sampled every 5s, one JPEG per frame, with human-audited
technique events attached to the frame they were read off. Built to be the ground truth for
(a) supervised tuning of a hosted vision model and (b) our own frame classifier later. The
frames are the originals `scripts/frame_pdf.py` extracted — recovered from the sheet PDFs
with `pdfimages -j`, byte-for-byte, no re-encode.

## Provenance and privacy

**Public corpus only.** Every bout here is competition footage already published by its
event (FloGrappling / YouTube broadcasts of ADCC Trials, IBJJF, CBJJ, Polaris). Nothing a
user fed through the App is in this dataset, and nothing from it may be joined to user-fed
rows — root `CLAUDE.md`, "Public vs Private Data". Owner-recorded gym video may only enter
with the filmed athletes' consent recorded alongside it, and user video never enters without
an explicit opt-in. Neither is in v1.

Labels are DERIVED from broadcast footage; the footage itself is not redistributed by this
repo (`data/` is gitignored — only manifests, splits and this card are committable).

Label origin, per line, never rewritten:

| `source` | what it is | admissible for training |
|---|---|---|
| `human` | concordance audit (`docs/gemini_concordance_audit.md`) — an auditor formed an independent read of every event off the frames | yes |
| `gemini` | a raw model reading, no human verdict | only once `review: accepted` |
| `gemini_ft:<job>` | a tuned model's reading | only once `review: accepted` |

`scripts/dataset_review.py` is the only writer of `review`, and refuses `source == "human"`.

## Contents

| | |
|---|---|
| bouts | 62 (50 train / 12 val / 8 excluded) |
| frames | 7117 (481 carry ≥1 label, 6636 unlabelled) |
| labels | 933 |
| frame bytes | 460 MB |
| taxonomy | `node_library@8b2dcd1cfa75` (376-node technique library) |

Labels by origin: `gemini` 475, `human` 458

Train-split admissible labels per event type: `control` 81, `escape` 10, `guard` 74, `pass` 16, `submission` 59, `sweep` 10, `takedown` 40, `transition` 46

## Class distribution — read this before planning the next labelling batch

The zero-shot baseline (`scripts/gemini_baseline.py`, 2026-09-02, gemini-3.6-flash,
thinking=high, N=10 audited trials bouts) measured **P 0.39 / R 0.29 / F1 0.34**, mean ts
error **1.1 s**, actor agreement **90%**, and `confidence: "high"` on 100% of events (so the
model's own confidence carries no signal). The failure is **label discrimination**, not
timing and not identity: guard/control sub-position recall was 0.09–0.13, and the confusions
were near-misses inside one event type (Snap Down vs Single Leg Takedown, Choke vs Rear
Naked Choke).

That makes class COUNT per near-miss group the number that decides whether tuning can help.

- **87 distinct (event type, node_key) classes in the admissible train split;
  53 of them have fewer than 3 examples.** A class seen twice cannot be separated from
  its neighbour. (The build's "train classes" line counts distinct `node_key` over ALL label
  origins, so it reads slightly lower — different question, both true.)
- Near-miss groups (same event type, sharing a content word) with their per-label counts are
  in `manifests/v1.json` → `train_near_miss_clusters`. Worst offenders today:
  - `butterfly` — `butterfly half guard` ×1, `butterfly guard` ×1
  - `entry` — `leg entry` ×2, `leg entanglement entry` ×1
  - `leg` — `leg entry` ×2, `leg entanglement entry` ×1
  - `straight` — `straight ankle lock` ×2, `straight armbar` ×1
  - `front` — `front headlock` ×2, `front headlock control` ×2
  - `headlock` — `front headlock` ×2, `front headlock control` ×2
  - `lock` — `straight ankle lock` ×2, `aoki lock` ×1, `foot lock` ×1
  - `triangle` — `body triangle` ×3, `body triangle bottom` ×1

Consequence for the next batch: labelling more bouts of the same common positions moves
nothing. What moves F1 is bouts that carry the RARE members of these groups, and audit
attention spent on separating the pairs above rather than on re-confirming `guard pull`.
Vertex SFT has no per-example weight, so coverage is the only lever available here.

## Split

`splits/v1.json`, group key = **athlete connected component**. One example is one
bout, which already satisfies the project's grouped-by-bout rule (measured 93% vs 21%
leakage); the component grouping additionally keeps an athlete from appearing on both sides,
because kit/tape/tattoos are memorisable and identity is the hardest half of the task.

**A published split is frozen.** Rebuilding extends it with new bouts on the side their group
already sits on; a new bout whose group straddles both sides is excluded with that reason.
Reshuffling means a new version file, never an edit.

## Known limits (measured, not hedged)

- **No dense per-frame state.** The reading prompt logs discrete occurrences, not per-frame
  states, so unlabelled frames mean "nothing was logged here", NOT "nothing is happening".
  Do not train a frame-level state classifier on absence.
- **Residual leakage.** The `trials_2023_24` bouts all come from ONE 8h recording, so a val
  bout from that batch shares broadcast style, mat and overlay with train bouts. Only
  identity leakage is controlled.
- **Label quality varies by broadcast.** Concordance audit measured 93% kept on the trials
  batch and 35% on the Bruno Rocha batch; the difference tracks whether the broadcast shows
  points. See `docs/PROMPT_gemini_frame_reading.md`, "Measured performance".
- **Known defect classes** in model-origin labels: whole-bout identity swaps, actor flipped
  on guard/pass exchanges, `ts` a few frames off the scoreboard change.
- **Two source batches, one audit protocol.** `trials_2023_24` and `women_65`
  (`data/frame_pdf/out/processed/audit/`) both carry the `concordance-audited` stamp and
  both have per-event `audit_log/` verdict files. `data/frame_pdf/out/processed/*.events.json`
  — the unreviewed `frame_answer_import` sidecars one directory up — are NOT ingested.
  Rebuild with `--batches trials_2023_24` to cut a trials-only version.

## Files

```
frames/<bout>/<ts_ms>.jpg    immutable frame, sha256 in frames.json
frames/<bout>/frames.json    [{file, ts, ts_ms, sha256, bytes, page, index}]
labels/<bout>.jsonl          one line per labelled frame (schema in the builder docstring)
sheets/<bout>.pdf            symlink to the rendered sheet (derived view)
splits/<version>.json        frozen once cut
manifests/<version>.json     counts, hashes, taxonomy, class distribution — committable
```
