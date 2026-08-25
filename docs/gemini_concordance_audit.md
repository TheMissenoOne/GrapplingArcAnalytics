# Gemini concordance audit — raw reading to clean DB data

The documented, repeatable procedure for turning a batch of raw Gemini frame-readings into
data that is safe to enrich or import. This is the measured QA step of the hybrid-read plan
in `docs/hybrid_reprocessing_plan.md` — it exists because a model's read, however good, has
never been checked against the frames by an independent pass, and this workspace's rule is
that losing data is acceptable, dirty data is not (`docs/frame_pdf_reading.md` §2.5).

Scope boundary: **how to read the frame sheet itself** (crop before you read, derive the
clock mapping, identity from the winner card) is `docs/frame_pdf_reading.md` §2 — this doc
does not repeat it, only cites it. The canonical prompt pasted into Gemini, and the running
measured-performance log, live in `docs/PROMPT_gemini_frame_reading.md` — keep that prompt
and `scripts/frame_answer.py`'s schema in sync, same push.

## 1. Reading

Paste `docs/PROMPT_gemini_frame_reading.md`'s prompt into Gemini (AI Studio), together with
ONE bout PDF from `data/frame_pdf/trials_2023_24/` (or any sheet `scripts/frame_pdf.py`
rendered). **One PDF per call** — batching bouts into one request was not tested and the
prompt is written against a single bout's context page. Save each response as a raw text
file into one folder; names don't matter, AI Studio's `.txt` copy-paste dumps work as-is.

## 2. Normalize

```bash
uv run python scripts/gemini_normalize.py <input_dir> [--out <dir>]
```

Default `--out`: `data/frame_pdf/trials_2023_24/answers/raw` — one structured
`<slug>.json` per bout, matching `scripts/frame_answer.py`'s schema. What it does, so a
human doesn't have to fix any of it by hand:

- **Tolerant parse.** Handles a bare `[{event}, ...]` array with no bout header, a plain
  `{"bout": ..., "events": ...}` object, and a `{"bouts": [...]}` wrapper holding several
  bouts, and strips literal control characters AI Studio's line-wrapping leaves inside JSON
  strings.
- **Bout matching.** Each reading is matched to its bout in the curated index
  (`data/frame_pdf/trials_2023_24_bouts.json`) by timestamp range, not by filename.
- **Dedup.** Exact-duplicate events (same reading pasted twice) are dropped.
- **Penalty events** (`type: "penalty"`) move to `bout.penalties` free text — penalties are
  scoreboard facts, not techniques, per the frame_answer schema.
- **Winner forced.** `bout.winner` is always overwritten to the curated index's published
  result. A Gemini answer that names the other winner is preserved as an audit flag on the
  file, never as data — see §3's identity-swap rule.
- **Label snapping.** Labels are folded (casefold + every unicode dash variant collapsed to
  `-`) and matched against the vocabulary; a fold-match snaps to the library's exact label.
  An off-library label that doesn't fold-match is flagged for the audit, not silently kept
  or dropped.
- Exit 1 on any file it cannot parse or match to a bout in the index.

## 3. Concordance audit — the QA heart

This is the step that makes a model reading trustworthy enough to touch the DB. Verdict
rules, copied verbatim from what batch 1 ran:

Render each bout's PDF to pages (`pdftoppm -r 150`). For every event in the normalized
reading, an independent auditor reads the frames of that event's **[ts-15, ts+15] window**
and forms their **own read before comparing it to Gemini's line** — reading Gemini's claim
first and then looking for confirmation is not an audit, it's priming.

- **CONCORDANT** — same technique (or a listed synonym), same actor, same `successful`,
  timestamp within ±10s. Keep as read.
- **DISCORDANT** — the auditor's independent read disagrees. Take a second look now knowing
  Gemini's claim. Keep, corrected, ONLY if the disagreement is clearly resolved by that
  second look (e.g. the pass was on the other athlete, the ts belongs on the scoreboard
  change two frames later). Otherwise drop the event — an unresolved disagreement does not
  get a coin flip.
- **UNVERIFIABLE** — the frames and the narration together don't show the claimed event
  (sampling gap, occluded scramble, no scoreboard confirmation). Drop it.

Only high-confidence events survive. Losing data is acceptable; dirty data is not.

**Identity is verified from walkouts, kit, and narration** — never from the technique
labels themselves. A winner contradiction against the published result (the thing §2 just
forced) means the whole bout's actor assignments are suspect, not just one event: in batch
1, 2 of 41 bouts were full identity swaps (Gemini had the two athletes' bodies crossed for
the entire bout), both caught and fixed the same way — anchor on the finisher / hand-raised
athlete being the published winner, then re-derive which body is which from there.

Auditors write one `<slug>.audited.json` per bout:

```json
{"kept_events": [...], "log": [...], "identity_check": "<how identity was verified>",
 "notes": "<anything the assembler or a later reader should know>"}
```

`kept_events` carries the (possibly corrected) surviving events in the same event shape
`scripts/frame_answer.py` validates. `log` is a per-event decision trail — worth keeping
even for CONCORDANT events, since it's what makes a dropped event auditable later.

## 4. Assemble

```bash
uv run python scripts/gemini_audit_assemble.py --audited <dir> [--answers <dir>]
```

Default `--answers`: `data/frame_pdf/trials_2023_24/answers`. Reads every
`answers/raw/<slug>.json` + its matching `<audited_dir>/<slug>.audited.json`, and writes:

- `answers/<slug>.events.json` — kept events only, published winner, identity from the
  audit's `identity_check`, `source: "gemini reading, concordance-audited (kept N/M)
  <date>"` (N kept out of M originally read — the ratio is the file's own QA receipt).
- `answers/audit_log/<slug>.json` — the per-event decision log + identity check + notes,
  kept separately so the decision trail survives without bloating the answer file.

Validates every assembled file with `scripts.frame_answer.validate` (schema + label check)
plus its own curated-bout-range check (kept events must sit inside the curated window,
±5s/+30s padding); **exits 1** on any problem — nothing partially-broken reaches disk as a
"final" answer.

**Provenance note (2026-08-25, owner decision):** for this documented flow, the concordance
audit above **substitutes** the human-review gate — an `answers/<slug>.events.json`
produced by this pipeline is treated as reviewed even though `scripts/frame_registrar.py`
never touched it. The registrar's human-review gate still governs the classic
`data/frame_pdf/out/<slug>/events.json` pipeline documented in `docs/frame_pdf_reading.md`;
this substitution applies only to files produced by this procedure.

## 5. DB cross-reference (dedupe)

Before generating a dump, check which audited bouts are already in prod — a read-only probe,
no writes. Match each audited bout's athlete pair against `matches` by normalized name
(`analysis.names.athlete_key`) plus a fuzzy surname pass for spelling variants, and spot-
check ambiguous pairs by direct name probe. Batch 1: 12 of 41 bouts were already in prod, 4
of them hidden behind a spelling variant between the audited name and the DB's existing
athlete row (Mejia/Mahia, Nicky/Nikki Ryan, Dan/Daniel Manasoiu, Leve/Levy) — plus two
genuine duplicate athlete rows already in prod, unrelated to this batch (Amanda Leve +
Levy, Nicky + Nikki Ryan — owner backlog, not fixed here).

Output of this step feeds §6/§7 directly:

- **`enrich_targets`** — `{slug: existing match id (or its 8-hex prefix)}` for the 12 bouts
  already in prod.
- **`db_name_map`** — `{audited spelling: existing DB spelling}` for every variant found.
  Using the DB's existing spelling in a new dump, rather than the audited name, is what
  prevents a phantom duplicate athlete row — the same de-dup-by-name failure mode the AA-011
  repair fixed once already (`docs/hybrid_reprocessing_plan.md` §5).

## 6. New bouts → dump

For the bouts NOT already in prod:

```bash
uv run python -m scripts.frame_answer_to_dump --from-answers <answers_dir> --allow-audited --exclude <enrich_targets slugs>
```

`--from-answers`/`--allow-audited`/`--exclude` are the interface this doc's flow uses to
route around the classic `data/frame_pdf/out/` + registrar gate (§4's provenance note):
`--from-answers` points the converter at this pipeline's `answers/` directory instead of
`out/`, `--allow-audited` accepts the `"gemini reading, concordance-audited"` source stamp
as reviewed (alongside the registrar's own stamp), and `--exclude` skips the slugs §5 found
already in prod so they go through §7 instead. **As of this writing these three flags are
landing in `scripts/frame_answer_to_dump.py` in parallel with this doc** — confirm with
`uv run python -m scripts.frame_answer_to_dump --help` before running; the rest of the
script's contract (identity/winner resolution via `athlete_key` against only that bout's own
two competitors, slash-labels refused, six-key whitelist, `--write`/`--dry-run`) is
unchanged and already documented in the script's own docstring and
`docs/frame_pdf_reading.md` §4.7.

Output: `scripts/dumps/<batch>_data.py`, a plain dump literal. Importing it is the normal
`scripts/dump_import` path — owner-gated prod write, unchanged by this doc.

## 7. Existing bouts → additive enrichment

For the bouts §5 found already in prod:

```bash
uv run python -m scripts.enrich_from_audit --audited <dir> --crossref <path>          # dry-run, default
uv run python -m scripts.enrich_from_audit --audited <dir> --crossref <path> --write   # owner-gated
```

`--crossref` is the `enrich_targets` + `db_name_map` (+ optional `bout_start`) JSON §5
produced. Only inserts events not already present — an audited event is a duplicate (skipped,
not inserted) when an existing sequence event on the same match has the same actor and the
same canonicalized technique key (`clean_label` → `_normalize_name` → `canonicalize`, the
same chain every graph/map consumer uses) and either the existing event has no `ts` or both
timestamps sit within 30s of each other. Never modifies or removes an existing event, and
never overwrites a non-null `winner`/`win_type`/`video_*` column (the `_resolve_video` /
AA-011 precedence scar in `scripts/dump_import.py`). **Idempotent by construction**: a
second `--write` run against the same crossref must report 0 inserts — that is the check to
run after any `--write`, not a separate test.

## 8. Register

Append the batch's numbers to `docs/PROMPT_gemini_frame_reading.md`'s "Measured performance"
section, and log the batch + numbers via the `dashboard` agent (root CLAUDE.md's standing
rule — every consolidated change gets a dashboard log line).

## Batch 1 (2026-08-25)

41 bouts / 401 events read from `data/frame_pdf/trials_2023_24/` (the curated 52-bout index,
41 already rendered as PDFs at the time of this batch).

| Stage | Result |
|---|---|
| Events read (Gemini) | 401 |
| Kept after audit | 397 (99%) |
| Corrected (kept, changed) | 22 — actor swaps including 2 whole-bout identity swaps (§3), `ts` snapped to the true scoreboard-change frame, 1 `successful` flip, 1 dropped `points` field that didn't match the scoreboard |
| Dropped (unverifiable/unresolved discordant) | 4 |
| Assemble validation problems | 0 across all 41 files |
| Already in prod (§5) | 12 of 41 bouts, 4 behind a spelling variant (Mejia/Mahia, Nicky/Nikki Ryan, Dan/Daniel Manasoiu, Leve/Levy) |
| New vs prod | 29 of 41 bouts, routed to §6 |

Recurring defect classes worth watching on future batches (from `docs/PROMPT_gemini_frame_reading.md`'s
own measured-performance note): whole-bout identity swaps when both kits are dark and there
is no naming commentary; actor flipped specifically on guard/pass exchanges; `ts` a few
frames late relative to the true scoreboard change; ASCII hyphens where the vocabulary
carries U+2011 (auto-snapped by `gemini_normalize.py`, not an audit finding).

## Provenance & maintenance

Written 2026-08-25 alongside batch 1, the first run of this full procedure. Numbers in the
Batch 1 table are the batch's own measured output (41 `.events.json` files under
`data/frame_pdf/trials_2023_24/answers/`, 41 `.audited.json`-derived logs under
`answers/audit_log/`, both counted directly). Script interfaces (`gemini_normalize.py`,
`gemini_audit_assemble.py`, `enrich_from_audit.py`) read directly from each script's own
docstring/argparse at the same date. `scripts/frame_answer_to_dump.py`'s `--from-answers`/
`--allow-audited`/`--exclude` flags were **not yet present** in the source at time of
writing (confirmed via `grep -n add_argument scripts/frame_answer_to_dump.py`) — another
agent was landing them in parallel; verify with `--help` before relying on the exact flag
names in §6.

Not verified here: batching multiple bouts per Gemini call (§1 explicitly untested); whether
the concordance-audit substitution (§4) should extend to the classic `out/` pipeline as well,
or stay specific to this documented flow — that is an owner decision, not assumed here.
