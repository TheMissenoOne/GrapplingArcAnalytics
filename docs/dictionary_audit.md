# Dictionary audit — completing the technique dictionary for the vision dataset

The vision dataset (`docs/vision_dataset.md`) can only teach a model the techniques it holds
EXAMPLES of. This is the loop that turns "210 curated techniques" into "N techniques a model
can actually be tuned on", and it is the step that comes BEFORE the next tuning run — the
pre-registered honest prior in `docs/vision_dataset.md` §8 already says a FAIL should be read
as a coverage problem, not a hyper-parameter problem.

Scope boundary: how a sheet is rendered is `scripts/frame_pdf.py`; how a sheet is READ is
`docs/frame_pdf_reading.md`; turning a model reading into ground truth is
`docs/gemini_concordance_audit.md`; the dataset itself is `docs/vision_dataset.md`. This doc
is only about the VOCABULARY: which techniques are covered, which are missing, and what the
owner has to look at to close the gap.

Runner: `scripts/dictionary_audit.py`. Measured output:
`docs/research/2026-09-15_dictionary_audit.md` (generated block) +
`data/finetune/audit/coverage.json`. Tests: `tests/test_dictionary_audit.py`.

Privacy class **A, public competition data**. Every input is published footage — the dataset
labels, `matches.sequence` on prod (read-only), the frame-reading experiments. No user-fed row
is read, and nothing here may ever be joined to one (root `CLAUDE.md`).

---

## 1. What "complete" means

Fixed here so the finish line cannot move later. The dictionary is complete when:

1. **Every in-scope curated entry has ≥ N human-verified frames** (`--min-human`, default 8).
   In-scope = not `concept` type and present in the reader's vocabulary
   (`data/frame_pdf/node_library.json`). "Verified" = `source: human` OR `review: accepted`,
   the same disjunction `vision_dataset_export.admissible` uses — a human-accepted model
   reading counts, a raw one does not.
2. **Every in-scope entry has a written visual definition** — 1–2 lines in
   `analysis/data/technique_definitions.json`, keyed by `node_key`, saying what has to be
   VISIBLE in a frame for the label to apply. Without it two auditors are not measuring the
   same thing, and the near-miss classes (`Snap Down` vs `Single Leg Takedown`) are exactly
   where that bites.
3. **Zero unresolved candidates** — every label seen in the corpus or in a read with no
   curated entry has a verdict: added, aliased onto an existing entry, or rejected.
4. **Zero `unreachable` entries** — a curated entry with no node in `node_library.json` can
   never receive a frame: the reader is only ever shown that file's labels, and
   `vision_dataset.build_labels` drops anything else as `off_library_label`. Reconciling the
   two vocabularies is a reviewed change, not a script's job.

N = 8 is a knob, not a law. It is the smallest count at which the near-miss groups in the
dataset card have more than "two examples each", which is the state §5 of
`docs/vision_dataset.md` names as unlearnable at any tuning setting.

## 2. The loop

```bash
# 1. measure — read-only, ~40 s (prod corpus read included)
uv run python -m scripts.dictionary_audit measure --min-human 8
#    --no-db skips the prod read (every other number still moves)

# 2. optional: seed candidate frames from corpus events that have video
#    (scripts/dictionary_seed.py — costs Gemini calls, owner's call)
uv run python -m scripts.dictionary_seed ...

# 3. queue — build one contact sheet per technique that needs frames
uv run python -m scripts.dictionary_audit queue --cap 12 [--limit 40]

# 4. the owner reviews data/finetune/audit/sheets/<node_key>.pdf, following
#    data/finetune/audit/queue.md top-down, and writes verdicts.jsonl

# 5. apply — dry run first, then write
uv run python -m scripts.dictionary_audit apply verdicts.jsonl
uv run python -m scripts.dictionary_audit apply verdicts.jsonl --write

# 6. re-measure, and export when the numbers justify a run
uv run python -m scripts.dictionary_audit measure
uv run python -m scripts.vision_dataset_export vertex-sft --gcs-prefix gs://<bucket>/ga/v1
```

Step 5 `--write` re-runs `vision_dataset --build` at the end, so the labels, the manifest and
`DATASET_CARD.md` all come out consistent with the new verdicts. `--no-rebuild` skips it (the
card then lags, deliberately, and the next build catches up).

### Where the queue's frames come from

Priority order on every sheet, which is also the order of how much a human's minute is worth:

| rank | source | caption |
|---|---|---|
| 0 | `scripts/dictionary_seed.py` — a corpus event with video, frame extracted, a BLIND Gemini read agreeing with the corpus label | `corpus+gemini ✓` |
| 1 | the same, where the blind read DISAGREED | `corpus ✗ gemini: <its guess>` |
| 2 | an unreviewed model label already in the dataset, plus its ±1 neighbouring frames | `gemini read (+1 frame)` |
| 3 | the 4 Fable reference reads and the 64 Gemini reads under `data/frame_pdf/out/experiments/`, frames recovered from the Bernardi sheets with `pdfimages -j` | `<reader>` |

A frame already carrying a verdict is never queued again. A technique with no proposal at all
gets a row in `queue.md` saying so rather than an empty sheet — that is the honest signal that
its frames have to be MADE (step 2), not found.

## 3. Verdicts

One JSON object per line, one line per frame:

```json
{"node_key": "kimura", "bout": "<slug>", "ts_ms": 145000, "verdict": "accept", "note": "figure-four closed"}
```

| `verdict` | effect |
|---|---|
| `accept` | the frame shows this technique. The claim becomes admissible for training, keeping the **proposer's** `source` and gaining `review: accepted`. |
| `reject` | it does not. Recorded so the frame is never queued again, and any model line for it is marked `review: rejected`. |
| `relabel:<node_key>` | it shows something else. A line is minted for the new key with `source: human` — the human IS the origin of that claim — and the original proposal is rejected. |
| `alias:<node_key>` | this label is the same technique as an existing entry. Writes a PROPOSAL to `analysis/data/dictionary_proposals.json`. |

Two rules the command does not bend:

- **`source` is never rewritten.** Accepting a model's proposal does not make it
  human-authored; that promotion is the laundering `frame_registrar.py` was fixed for on
  2026-08-24, and `admissible()` already treats "accepted" and "human" the same way, so there
  is nothing to gain from it and an audit trail to lose.
- **No script edits `analysis/data/technique_library.json`.** `alias:` and an `accept` on a
  label with no curated entry both write proposals. Applying one is a reviewed, hand-made
  change, same gate as a schema change.

An accepted frame whose proposer named no actor (every seed frame: the blind read reports a
ROLE, not an identity) mints a label line with `actor: null` and `claim:
"technique_presence"`. Those lines are admissible for `frame-classification` and are SKIPPED
by the Vertex SFT target builder — a null actor in a tuning target teaches the model to emit
one, which is what `frame_answer.py` refuses on the way in.

### Verdicts are durable, and they have to be

`vision_dataset --build` rewrites every `labels/*.jsonl` from the answer files. A `review`
written only into a label file is therefore erased by the next build — which is why every
verdict is also appended to `data/finetune/audit/verdicts.jsonl`, and why
`vision_dataset.build()` replays that store at the end of each build
(`vision_dataset.apply_verdicts`). `scripts/dataset_review.py` writes to the same store.
Deleting that file loses human work that no re-run can reconstruct.

## 4. What this feeds

The pre-registered criterion is unchanged and lives in `docs/vision_dataset.md` §8:

> the tuned model passes if its micro-averaged F1 ≥ 0.44 (zero-shot baseline 0.34 + 0.10)
> over the `splits/v1.json` val bouts, scored by `scripts.gemini_baseline.match_bout` +
> `bout_metrics`, on the same 12 bouts, with no re-cut split.

This audit is the coverage lever that criterion depends on. It does not change the metric, the
matcher or the split, and a coverage improvement that does not move F1 on those 12 bouts is a
negative result to report, not to re-cut.

## Provenance & maintenance

Written 2026-09-15 with the first full run of `measure` + `queue` (numbers in
`docs/research/2026-09-15_dictionary_audit.md`, regenerate to re-check). Re-verification:

| Claim | How to re-check |
|---|---|
| bucket counts | `uv run python -m scripts.dictionary_audit measure` |
| an `unreachable` entry really is dropped | `grep -n "off_library_label" scripts/vision_dataset.py` |
| verdicts survive a rebuild | `uv run pytest tests/test_dictionary_audit.py -q` |
| the curated library is untouched by `apply` | same test file, `test_alias_writes_a_proposal_and_never_touches_the_curated_library` |

Not verified here: whether N = 8 is the right threshold (it is a stated knob, not a measured
one — the measurement that would settle it is a tuning run at two coverage levels), and
whether the `concept`-type exclusion should hold for `Grip Fighting` and `Level Change`, which
are arguably visible in a frame. Both are owner calls, recorded rather than assumed.
