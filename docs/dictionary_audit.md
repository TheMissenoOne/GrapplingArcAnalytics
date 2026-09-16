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
#    (scripts/dictionary_seed.py plan/extract, then preverify BEFORE ask spends any
#    Gemini call — see "Preverify" below)
uv run python -m scripts.dictionary_seed plan --per-technique 3 --min-events 3
uv run python -m scripts.dictionary_seed extract
uv run python -m scripts.dictionary_seed preverify
uv run python -m scripts.dictionary_seed ask

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
| 0–2 | `scripts/dictionary_seed.py` — a corpus event with video, frame extracted, a BLIND Gemini read (`review_confidence` high/medium/low decides the rank; see "Decisão 2026-09-16" below) | `gemini: <candidate> (<confidence>) · corpus: <corpus label> [<agree_near>]` |
| 2 | an unreviewed model label already in the dataset, plus its ±1 neighbouring frames | `gemini read (+1 frame)` |
| 3 | the 4 Fable reference reads and the 64 Gemini reads under `data/frame_pdf/out/experiments/`, frames recovered from the Bernardi sheets with `pdfimages -j` | `<reader>` |

A seed row that was never actually read (a preverify skip, `visible: no`, or a Gemini error —
no `candidate_label`) is not a review item at all: `dictionary_audit.gather_proposals` drops
it before it can reach a sheet, since there is nothing to show a human.

### Preverify — screen before spending a Gemini call

`scripts/dictionary_seed.py preverify` runs after `extract`, before `ask`: five cheap, local,
no-network* checks over each planned candidate's already-extracted frames GATE `verdict:
skip` — `missing_frames` (centre frame absent/undecodable, OR fewer than 5 of the 9 strip
frames present — stage A/`ask_alignment` only sends whichever strips exist, so a handful
missing is fine, e.g. the heel hook `helena-crevar-vs-aurelie-le-vern-2024`@182s with 8/9
strips is usable), `blank_frame` (near-uniform or near-black centre), `static_window` (all 9
present strip frames near-identical — a paused feed or replay card sitting still across the
whole ±30s window), `ts_out_of_range` / `ts_in_intro` (corpus timestamp past the video's own
duration, or inside the first 20s tale-of-the-tape window), and `duplicate_frame` (an
identical centre frame already used by an earlier candidate). (*`ts_out_of_range` probes each
distinct `video_url` once via `yt-dlp --dump-json` when the plan row carries one.)

A sixth check, `no_people` (fewer than 1 person detected on the centre frame via an optional
YOLOv8n detector — installed with the `cv` extra; missing extra/weights just records
`detector: unavailable` and is skipped, never fails the batch), is **advisory only** — it
lands in a `warnings` list, never `reasons`, and never gates the verdict. Measured against two
real sheets (2026-09-16): a general-purpose detector merges two entangled grapplers into one
box, or misses a dark-arena frame, often enough on this domain that gating on it would have
thrown away real, usable frames (`ponytail:` note on `PERSON_MIN_COUNT` in the module).

Writes `preverify.jsonl` (verdict `ok`/`skip` + reasons + warnings + metrics per candidate),
`preverify_summary.json` (counts per reason AND per warning) and `preverify_sheet_<n>.png`
contact sheets (≤24 candidates/page — centre thumb + the 9 strip thumbs, captioned `skip:
<reasons>` / `warn: <warnings>`) for a human to spot-check. `ask` then reads
`preverify.jsonl` if present: a `skip` verdict is written straight to `seed.jsonl` with
`visible: false`, reason `preverify:<reasons>`, zero Gemini usage — never sent to the model,
never silently dropped. `ask` is also resume-safe on its own: a candidate already in
`seed.jsonl` (matched on node_key+bout+ts_ms) is skipped and new answers are appended.

A frame already carrying a verdict is never queued again. A technique with no proposal at all
gets a row in `queue.md` saying so rather than an empty sheet — that is the honest signal that
its frames have to be MADE (step 2), not found.

### Decisão 2026-09-16 — o candidato é o alvo, o corpus é a segunda opinião

**A leitura cega do Gemini (`candidate_label`) é o alvo de revisão; o corpus label que
`plan` procurou é só a segunda opinião ao lado.** Medido: no batch de 91 candidatos, 6
discordâncias foram checadas a olho — em 5 de 6 o modelo nomeou a posição realmente VISÍVEL
no frame, e o corpus label simplesmente não estava naquele frame (desalinhamento de
timestamp, mesmo depois da janela de alinhamento de ±30s). Confiar no corpus label como
verdade era o prior errado.

Consequências, todas em `scripts/dictionary_seed.py`:

- **Stage A (alinhamento) virou OPCIONAL, desligado por padrão.** `ask` sem flag faz UMA
  chamada por candidato: lê o frame central (offset 0, já filtrado pelo `preverify`) às
  cegas — não precisa mais mostrar o corpus label, porque a resposta do modelo já É o
  candidato. `ask --align` liga de volta o stage A de duas chamadas (o comportamento antigo).
- **`review_confidence` foi redefinido** em torno do candidato, não da concordância com o
  corpus (`compute_review_confidence`): `high` só quando a segunda opinião confirma (`agree`
  full/partial); `medium` no tier `near` OU quando o próprio modelo relatou confiança alta;
  `low` no resto, incluindo uma linha nunca lida de fato (preverify skip / `visible: no` /
  erro).
- **`seed.jsonl` ganhou `candidate_label`/`candidate_type`** (a leitura do modelo, null
  quando a linha nunca foi lida) e **`second_opinion`** (`{corpus_label, agree, agree_near}`).
  `report` re-grada um `seed.jsonl` existente com esses campos, sem nenhuma chamada nova.
- **A fila de revisão (`dictionary_audit.py queue`)** agora mostra o candidato primeiro, com
  a segunda opinião ao lado — uma linha sem `candidate_label` (nunca lida) não vira item de
  fila.

### Results — 2026-09-16 run

First full `plan`→`ask` pass over the realigned batch: 91 candidates, 27 skipped by preverify
before any Gemini call (64 ok; skip reasons `missing_frames` 22 — two ~2h event VODs whose
frame extraction never landed enough of the 9-frame strip — `ts_out_of_range` 22, `duplicate_frame`
5; `no_people` warned on 14, advisory only). Strict agreement (`agree`): full 2, partial 1, no
88 (2% full) — most of that 88 is not a random miss.

`score_agreement` gained a `near` tier the same day (between `partial` and `no`): same curated
family (`technique_library.json` `type`, narrowed by `style_profile_core._sub_family` for
submissions so a leg lock and a shoulder lock don't collide) or the state a corpus ACTION
declares it lands in (`data/taxonomy/inference_table.json` `action_exit_orientation` — the
same R0 table `analysis.outcome_inference` uses). Re-grading the same 91 rows in place (no new
Gemini calls, `agree_near`, strict `agree` kept) moves 23 of the 88 `no` rows to `near` — full
2, partial 1, near 23, no 65. Full numbers, the strict-vs-near table and a hand-eyeballed
"frame check" (5 of 6 checked disagreements are corpus-timestamp misalignment, not a wrong
model read — stage A's own `visible: yes` is lenient) are in
`data/finetune/audit/gemini_seed/REPORT.md`, regenerated by `scripts.dictionary_seed report`.

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
