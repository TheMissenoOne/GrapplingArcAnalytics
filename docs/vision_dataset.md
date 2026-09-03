# Vision dataset (`data/finetune/`) and Gemini fine-tuning

Two things, in this order, because the order is the whole point:

1. **The dataset** — `data/finetune/`, built by `scripts/vision_dataset.py`. Immutable frames,
   one label line per labelled frame with its own provenance, a split cut once and frozen. It
   outlives whichever model we tune this quarter, and it is the ground truth for our OWN
   vision model later.
2. **The tuning run** — `scripts/vision_dataset_export.py vertex-sft` reshapes one split for
   Vertex AI, `scripts/gemini_finetune.py` uploads it, starts the job and scores the result.

An exporter is a function OF the dataset. Adding one must never change the storage format.

Scope boundary: how a sheet is rendered is `scripts/frame_pdf.py`'s docstring; how a sheet is
READ is `docs/frame_pdf_reading.md`; the QA that turns a model reading into ground truth is
`docs/gemini_concordance_audit.md`. This doc starts at "audited answers exist".

---

## 1. Pipeline

```
competition footage ──frame_pdf.py──▶ sheet PDF ──Gemini/AI Studio──▶ raw reading
                                          │                              │
                                          │                    gemini_normalize.py
                                          │                              ▼
                                          │                     concordance audit
                                          │                (gemini_audit_assemble.py)
                                          │                              ▼
                                          │                  answers/<slug>.events.json
                                          ▼                              │
                            pdfimages -j + pdftotext ◀──────────────────┘
                                          │
                                          ▼
   data/finetune/  frames/<bout>/<ts_ms>.jpg + labels/<bout>.jsonl + splits/ + manifests/
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              ▼                           ▼                           ▼
      export_vertex_sft          export_frame_classification      export_coco
      (gs:// JSONL, tuning)      (our own model, CSV/JSONL)       (stub — no boxes)
```

### Where the pixels come from — no re-download, no re-render

`frame_pdf.py` writes each frame into the sheet PDF through reportlab, which **passes a JPEG
through untouched**. The storage policy (`docs/frame_pdf_reading.md` §1) then deletes the
`frames/` folders and keeps the PDF. So the original 1280×720 JPEGs are still on disk, inside
the PDFs, and `pdfimages -j` recovers them byte-for-byte — verified: the extracted files still
carry ffmpeg's `Lavc61.19.101` JPEG comment.

Timestamps come from the sheet's own **text layer** (`H:MM:SS (NNNNs)` printed above each
cell), read in document order and paired with the images in document order. Nothing is
re-derived by `start + i×step` arithmetic that could drift from what the auditor actually read.
The builder refuses to guess if the two counts disagree.

Requires poppler (`pdfimages`, `pdftotext`) on PATH. Nothing else — no network, no yt-dlp.

---

## 2. Layout and schema

```
data/finetune/
  frames/<bout_slug>/<ts_ms>.jpg   immutable; a rebuild never rewrites an existing byte
  frames/<bout_slug>/frames.json   [{file, ts, ts_ms, sha256, bytes, page, index}]
  labels/<bout_slug>.jsonl         one line per labelled frame
  sheets/<bout_slug>.pdf           SYMLINK to the rendered sheet (a derived view, not a copy)
  splits/<version>.json            frozen once cut
  manifests/<version>.json         counts, class distribution, hashes, taxonomy  ← committed
  DATASET_CARD.md                  origin, privacy, measured limits              ← committed
  exports/<consumer>/              regenerable; never an input to anything
```

Label line:

```json
{"bout": "elijah-dorsey-vs-nicky-ryan",
 "frame": "frames/elijah-dorsey-vs-nicky-ryan/004490000.jpg",
 "ts": 4490, "ts_ms": 4490000, "event_ts": 4490,
 "node_key": "collar tie", "label": "Collar Tie", "type": "control",
 "state": "collar tie", "action": null,
 "actor": "Elijah Dorsey", "actor_key": "elijah dorsey", "successful": true,
 "taxonomy_version": "node_library@8b2dcd1cfa75",
 "source": "human", "reviewer": "concordance audit (frames + narration + published result)",
 "reviewed_at": "2026-08-25", "review": "accepted", "confidence": "high",
 "label_id": "6ecf1f8b4b11"}
```

- **`node_key`** is the canonical label — `canonicalize(_normalize_name(label))`, the same
  chain every graph/map consumer in this repo uses. An off-library label is DROPPED, not
  minted: an unlisted spelling silently splits one technique into two nodes.
- **`taxonomy_version`** pins the vocabulary by content hash of
  `data/frame_pdf/node_library.json` (376 nodes). Regenerate the library ⇒ new hash ⇒ old
  labels are still readable and still say which vocabulary they were written against.
- **`state` vs `action` is derived, not invented.** The library already carries a `node_type`;
  `control`/`guard` are positions a frame is IN, the other six types are things that HAPPEN.
  The raw `type` is kept so nothing is lost.
- **`label_id`** = sha256 of `(bout, ts_ms, node_key, actor_key, source)`, truncated — the
  handle `scripts/dataset_review.py` accepts/rejects by.

### There is no dense per-frame state, on purpose

The reading prompt says "log DISCRETE occurrences, not per-frame states", so a per-frame state
track does not exist in the source data. An unlabelled frame means **nothing was logged here**,
not **nothing is happening**. Training a frame-level state classifier on that absence would
learn the sampling policy, not the sport. The manifest counts unlabelled frames (6 636 of
7 117 today) so the gap is a number, not a surprise.

### Provenance: `source` is never rewritten

| `source` | what it is | admissible for training |
|---|---|---|
| `human` | concordance audit — an auditor formed an **independent** read of every event off the frames before seeing the model's line | yes |
| `gemini` | a raw model reading | only after `review: "accepted"` |
| `gemini_ft:<job>` | a tuned model's reading | only after `review: "accepted"` |

Promoting a model reading to `source: "human"` on review is exactly the laundering
`frame_registrar.py` was fixed for on 2026-08-24. So review records `review` +
`reviewer` + `reviewed_at` **alongside** the untouched `source`, and
`vision_dataset_export.admissible` is the disjunction. `scripts/dataset_review.py` is the only
writer of `review` and refuses a `source == "human"` line — there is nothing to review there.

---

## 3. The split — grouped by athlete, frozen once cut

`splits/<version>.json`, `group_key = athlete_connected_component`.

One example is one bout, which already satisfies the project's grouped-by-bout rule (measured
**93% vs 21% leakage**, `bjj-visual-dataset-calibration-gate`). The component grouping goes one
level further **because it has to**: the hardest half of this task is deciding which body is
which, and an athlete appearing in both train and val leaks exactly that. Kit, tape and tattoos
are memorisable across bouts — that property is what made the Bruno Rocha identity call
decidable by hand, so it is equally available to a model.

Measured 2026-09-02: the 62 bouts fall into **36 components**, the largest holding **22 bouts
(35% of the corpus)** and the rest holding 1–2 each. That is why the split is a deterministic
largest-group-first fill of whichever side has the bigger deficit, and **not**
`sklearn.GroupShuffleSplit` — a random group permutation can put that single 22-bout component
in val and hand back a 35% test set. Re-check with
`python -c "import json;s=json.load(open('data/finetune/splits/v1.json'));print(sorted((len(v) for v in s['groups'].values()),reverse=True))"`.

**A published split is frozen.** Rebuilding extends it: a new bout joins the side its group
already sits on, and a new bout whose group straddles both sides is EXCLUDED with that reason
rather than silently leaking. Reshuffling means `--version v2`, never an edit to `v1.json`.

Residual leakage, stated rather than hidden: the 41 `trials_2023_24` bouts all come from ONE
8-hour recording, so a val bout there shares broadcast style, mat and overlay with train bouts.
Only identity leakage is controlled.

---

## 4. Measured state (2026-09-02)

```
uv run python -m scripts.vision_dataset --build
```

| | |
|---|---|
| bouts | 62 (50 train / 12 val / 8 excluded) |
| frames | 7 117 (481 labelled, 6 636 unlabelled), 460 MB |
| labels | 933 — `human` 458, `gemini` 475 (unreviewed) |
| classes, admissible train | 87 distinct (type, node_key) — **53 with fewer than 3 examples** |
| dropped events | 1 (`off_library_label`) |
| sheets to upload | 62 PDFs, 578 MB, 7–208 frames each, max 19.3 MiB |
| tokens per example | 5 213 (14 frames) … 23 133 (208 frames), mean **14 117** — measured with `count_tokens`, not estimated |
| train tokens / epoch | ≈ 723 k (705 850 input + ~17 k target) |

Excluded, with the reason recorded in `splits/v1.json`:

- 4 `bruno_rocha` bouts — no sheet PDF was ever rendered for them (only `clip.mp4` + `strip/`).
  They are also the measured low-water mark (35% of events kept vs 93% on trials), so their
  absence costs little.
- 4 `trials_2023_24` answers whose bout PDF is not in the set (45 answers, 41 PDFs).

### Source batches — a correction worth recording

The handoff said "human truth today = `trials_2023_24/answers`; `data/frame_pdf/out` has zero
reviewed". That is true of `data/frame_pdf/out/processed/*.events.json` (21 files, all still
`frame_answer_import (… not yet human-reviewed)`) — and those are NOT ingested. But
`data/frame_pdf/out/processed/**audit**/` holds 21 bouts carrying the same
`gemini reading, concordance-audited (kept N/M) 2026-08-25` stamp as the trials set, with the
same per-event `audit_log/` verdict files. Those ARE ingested, as batch `women_65`, and they
are ~40% of the corpus.

If you disagree with that call, `--batches trials_2023_24` cuts a trials-only version — but cut
it as `--version v2`, do not re-cut `v1`.

---

## 5. What to label next — the class distribution decides it

Zero-shot baseline (`scripts/gemini_baseline.py`, 2026-09-02, `gemini-3.6-flash`,
`thinking=high`, N=10 audited trials bouts): **P 0.39 / R 0.29 / F1 0.34**, mean `ts` error
**1.1 s**, actor agreement **90%**, `confidence: "high"` on 100% of events (so the model's own
confidence is not a usable filter).

The failure is **label discrimination**, not timing and not identity: guard/control
sub-position recall 0.09–0.13, confusions inside one event type (Snap Down vs Single Leg
Takedown, Choke vs Rear Naked Choke).

So the manifest reports `train_near_miss_clusters` — labels of the SAME event type sharing a
content word, with their counts. Today's worst:

| type | group | counts |
|---|---|---|
| control | `front headlock` | `front headlock` ×2, `front headlock control` ×2 |
| guard | `butterfly` | `butterfly guard` ×1, `butterfly half guard` ×1 |
| submission | `lock` | `straight ankle lock` ×2, `aoki lock` ×1, `foot lock` ×1 |
| takedown | `single` | `single leg takedown` ×10, `low single leg` ×1 |

Two labels at ×2 each cannot be separated by any amount of tuning. **Vertex SFT has no
per-example weight**, so the only lever is coverage: the next labelling batch should be chosen
for the RARE members of these groups, not for more `guard pull` (×66). Labelling more of what
the model already gets right moves nothing.

---

## 6. Tuning: Vertex only, never an API key — proven, not assumed

```
$ uv run python -m scripts.gemini_finetune --check-api-key
Developer API client: vertexai=None

[1] gcs_uri (what a multimodal dataset needs) through an API key:
    ValueError: gcs_uri parameter is only supported in Gemini Enterprise Agent Platform mode,
    not in Gemini Developer API mode.

[2] the only dataset shape an API key accepts (types.TuningExample):
    fields = ['output', 'text_input']
```

`google-genai` 2.22.0. `types.TuningExample` has **two string fields**. There is no `Part`, no
`fileData`, no inline image — the Developer API tuning dataset has nowhere to put a frame
sheet. Multimodal SFT is Vertex-only.

### Why the text-only Developer-API format is not used at all

A `{"text_input": …, "output": …}` dataset would need a textual representation of a bout. One
exists — the sheets carry the commentary narration under every frame — and it is still the
wrong thing to train:

- The reading contract says **frames decide positions, narration decides identity and outcome**
  (`docs/PROMPT_gemini_frame_reading.md`). A narration-only model would have to invent every
  position it emits.
- That is precisely the fabrication the concordance audit exists to drop. Training a model to
  do it deliberately would be building a hallucination generator and calling it a baseline.
- And it does not serve the goal: the inference target is a sheet, not a transcript.

Recorded here so nobody re-derives it. `export_coco` is a stub for the same style of reason —
this corpus has no bounding boxes anywhere in the pipeline.

### Example shape (one bout = one example)

```json
{"contents": [
  {"role": "user", "parts": [
    {"fileData": {"mimeType": "application/pdf",
                  "fileUri": "gs://<bucket>/<prefix>/sheets/<slug>.pdf"}},
    {"text": "<docs/PROMPT_gemini_frame_reading.md, prompt body verbatim>"}]},
  {"role": "model", "parts": [{"text": "{\"bout\": {...}, \"events\": [...]}"}]}]}
```

One example = one **bout**, not one page, because
`scripts/gemini_read_frames.py` sends the whole sheet in one call. Tuning on single pages would
train a model for a request shape it never receives.

Vertex document-tuning limits (docs.cloud.google.com, "Document tuning"): ≤300 PDF pages and ≤4
PDF files per example, ≤20 MB per file, ≤131 072 input+output tokens per example. Our sheets are
7–39 pages, max 19.3 MiB, max 23 133 tokens — inside every limit, with the largest sheet 3%
under the file-size cap. A sheet over 20 MB is skipped with that reason in `exports/vertex_sft/
stats.json`; the fix is a lower JPEG quality on re-render, or splitting it (4 PDFs/example are
allowed).

### The target is rebuilt from the labels, not copied from the answer file

Only admissible labels reach it, in the canonical vocabulary, and three bout-header fields are
dropped because they are **audit artefacts**: `notes`, `identity_verified_by`, and the audited
`identity_discriminator` — the auditor rewrote it citing internal page filenames and verdicts
(`"verified frames: …-05.jpg", "audit.flags=[]"`). A model cannot produce that and must not
learn to imitate it. `identity_discriminator` is instead taken from the model's own **pre-audit**
reading (the register is right, and the audit verified the identity itself); 34 of 62 bouts have
one, and the other 28 targets carry no discriminator. That trade-off is a `ponytail:` comment in
the exporter with its upgrade path — have the audit record its discriminator in the model's own
register.

---

## 7. Runbook

```bash
# 0. build / refresh the dataset (idempotent, offline, ~1 min)
uv run python -m scripts.vision_dataset --build

# 1. reshape one split for Vertex
uv run python -m scripts.vision_dataset_export vertex-sft --gcs-prefix gs://<bucket>/ga/v1

# 2. see the whole plan without spending anything
uv run python -m scripts.gemini_finetune --dry-run --gcs-prefix gs://<bucket>/ga/v1

# 3. what Vertex will actually tune (listed, never guessed)
uv run python -m scripts.gemini_finetune --list-models

# 4. upload (google-cloud-storage if installed, else it prints the gcloud commands)
uv run python -m scripts.gemini_finetune --upload --gcs-prefix gs://<bucket>/ga/v1

# 5. start the job  ← the owner's call, costs money
uv run python -m scripts.gemini_finetune --tune --gcs-prefix gs://<bucket>/ga/v1 \
    --base-model <from step 3> --epochs 3

# 6. poll
uv run python -m scripts.gemini_finetune --status projects/…/locations/…/tuningJobs/…

# 7. score it on the SAME val bouts, with the SAME matcher as the baseline
uv run python -m scripts.gemini_finetune --eval --model projects/…/models/… --thinking high

# 8. feed the tuned model's readings back as labels for review (active learning)
uv run python -m scripts.dataset_review list --disputed-only
uv run python -m scripts.dataset_review accept <label_id> --note "confirmed off frame 04490"
```

### Owner checklist — what has to exist before step 4

- [ ] A GCP project, and `GOOGLE_CLOUD_PROJECT` exported (`.env` is loaded automatically).
- [ ] **Billing enabled** on it. Tuning is not on the free tier.
- [ ] Vertex AI API enabled: `gcloud services enable aiplatform.googleapis.com`.
- [ ] A GCS bucket in the SAME region as `GOOGLE_CLOUD_LOCATION` (default `us-central1`).
      Cross-region reads of a tuning dataset fail late and confusingly.
- [ ] `gcloud auth application-default login` — the SDK uses ADC in Vertex mode, NOT
      `GEMINI_API_KEY`.
- [ ] Optional: `uv pip install google-cloud-storage` so `--upload` runs in-process instead of
      printing `gcloud storage cp` lines.
- [ ] Confirm the base model from `--list-models`. Do not trust a hardcoded name — measured
      2026-09-02, `gemini-2.5-flash` already returns *"no longer available to new users"* to a
      new API key.

### Cost

Measured, not estimated: **705 850 input tokens + ~17 k target tokens across the 50 train
examples**, so ≈723 k tokens per epoch, ≈2.2 M for 3 epochs. Vertex bills SFT per training
token (dataset tokens × epochs); at Flash-class inference rates ($0.75/M input as of the
2026 introductory pricing) that order of magnitude is **single-digit dollars for the whole
run** — the tuning rate is higher than the inference rate, so treat that as a floor and read
the live figure on the Vertex pricing page before authorising. One `--eval` pass over the 12
val bouts is 189 676 input tokens, i.e. cents.

The real cost driver is bout COUNT, not sheet size, and 62 bouts is small. Storage: 578 MB of
sheets in the bucket.

---

## 8. Pre-registered success criterion

Fixed **before** the first tuning run, so the result cannot be re-interpreted afterwards.

> The tuned model passes if its **micro-averaged F1 ≥ 0.44** (baseline 0.34 + 0.10) over the
> `splits/v1.json` **val** bouts, scored by `scripts.gemini_baseline.match_bout` +
> `bout_metrics` — the identical matcher the baseline ran (`type` equal, canonical label
> equal, `|Δts| ≤ 10 s`, greedy nearest-ts one-to-one), on the same 12 bouts, with no re-cut
> split.

Encoded in `scripts/gemini_finetune.py` as `BASELINE_F1 = 0.34` / `MIN_F1_GAIN = 0.10`; the
`--eval` report prints `verdict: PASS|FAIL` next to the numbers.

Secondary numbers, reported but NOT part of the criterion (they were already near ceiling
zero-shot, so improving them proves little): mean `ts` error (baseline 1.1 s), actor agreement
(baseline 90%), `successful` agreement.

**Honest prior:** the diagnosis says the failure is label discrimination among near-miss
classes, and 53 of 87 train classes have fewer than 3 examples. 50 examples of a 87-class
long-tailed problem is a thin brief. If `--eval` says FAIL, the first hypothesis is dataset
coverage (§5), not the tuning hyper-parameters — do not spend a second run on epochs and
adapter size before the near-miss groups have examples.

---

## 9. What is committed

`data/finetune/*` is gitignored except three small files, which are what make any measurement
citable a year later:

```
data/finetune/manifests/<version>.json   counts, class distribution, sheet hashes, taxonomy
data/finetune/splits/<version>.json      the frozen split
data/finetune/DATASET_CARD.md            origin, privacy, measured limits
```

Frames (460 MB), labels, sheet symlinks and `exports/` stay local — all regenerable from the
sheet PDFs and the audited answers, which are themselves in the repo.

---

## Provenance & maintenance (2026-09-02)

Every number above was measured on this machine, not carried over:

| Claim | How to re-check |
|---|---|
| JPEGs survive the PDF round-trip | `pdfimages -j <sheet>.pdf /tmp/f && file /tmp/f-000.jpg` → still `comment: "Lavc…"` |
| corpus counts | `uv run python -m scripts.vision_dataset --build` |
| split purity | `uv run pytest tests/test_vision_dataset.py -q` (19 tests) |
| tokens per example | `client.models.count_tokens(model="gemini-3.6-flash", contents=[pdf_part, prompt_part])` |
| API key cannot do multimodal tuning | `uv run python -m scripts.gemini_finetune --check-api-key` |
| `TuningExample` is text-only | `python -c "from google.genai import types; print(sorted(types.TuningExample.model_fields))"` |
| Vertex document-tuning limits | docs.cloud.google.com → Tuning → Supported modalities → Document tuning |

Not verified here: the actual Vertex SFT price per training token (the pricing page is
JS-rendered and was not readable from this machine — the §7 figure is an inference-rate floor,
not a quote), and whether `--tune`/`--upload`/`--status` work end to end, because none of them
has been run: no GCP project is configured and starting a paid job is the owner's call, not a
subagent's.
