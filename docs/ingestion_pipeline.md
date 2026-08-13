# Match ingestion pipeline — transcript → DB → embeddings → site

End-to-end, how one grappling/MMA event goes from a raw YouTube page to live interactive
breakdowns. **This is the single authority for the pipeline.** The event/label rules the refiner
follows live in `PROMPT_events_sidecar.md`, which is also the prompt you paste into ChatGPT.

Ownership is marked per step: **YOU** (manual), **LLM** (the refiner, run by hand in a chat window
— nothing calls an API), **maintainer** (the deterministic scripts).

```
 YOU            batch_queue        LLM (chat)         apply_events      reprocess_all      embeddings        site_data
transcript ─▶ preliminary dump ─▶ events sidecar ─▶ spliced dump ─▶ matches in DB ─▶ pgvector ─▶ site/  ─▶ validate
 (.txt)        (pbp + [])          (_events.json)     (events)        (+ELO replay)    (768-d)     (pages)   (deviance)
```

---

## 1. Grab the transcript · **YOU** · → `transcripts/queue/<Event>.txt`

From the YouTube video page, save one `.txt` with three parts:

- **Ref block** (top) — the match card, the source of truth for the **bout list + start times**.
  One line per bout. Write either `Name vs Name (M:SS)` / `Name vs Name (M:SS - M:SS)` or
  `M:SS Name vs Name` — `batch_queue.parse_match_card()` tries a chain of regexes and takes the
  first that matches each line.
- **Link** — `Link: https://www.youtube.com/watch?v=<id>`.
- **Transcript body** — the full auto-caption dump. Source of truth for the **event sequence**.

The filename stem is the event handle (e.g. `Polaris31.txt`) and is used everywhere downstream —
keep it short and stable. Map a nicer display name in `batch_queue.STUB_EVENTS` if the stem is ugly.

> There is no `fmt=colon/finals/nowcolon` flag. The root `TRANSCRIPT_PROCESSING.md` documents one;
> it exists in no current script.

## 2. Preliminary dump · **maintainer** · `scripts/batch_queue.py`

```bash
uv run python scripts/batch_queue.py
```

No flags — it scans every `transcripts/queue/*.txt` on each run. For each it writes
`scripts/dumps/<slug>_data.py`: a module-level `RAW` list holding one dict, keyed
`("Name", year)` per bout, each with preliminary `winner`/`method` guesses, a **`pbp`** window of
cleaned timestamped commentary, and an **empty `events`** list. Dumps already refined (containing
`"label":`) are skipped, so re-running is safe. It never invents events — that is step 3.

## 3. Refine pbp → events · **LLM, by hand** · → `scripts/dumps/<event>_events.json`

Paste the prompt from **`PROMPT_events_sidecar.md`** into ChatGPT, followed by the transcript.
Save the returned JSON at `scripts/dumps/<event>_events.json`.

Shape: `{"<a_name>|<opponent>|<year>": [{label, type, actor, ts, successful?}, ...]}`.
`apply_events.py` also accepts a two-part `name|year` key as a fallback, but every one of the 388
keys in this repo is three-part — prefer it.

Three rules cause every recurring failure, and all three are in the prompt:

- **`ts` is seconds from the start of the VIDEO**, never from the start of the bout.
  `export/site_data.py:852` subtracts the bout start when rendering, so a bout-relative value is
  subtracted twice and every video link lands in the wrong place.
- **A failed attempt keeps its own type** plus `successful: false`; it is not re-typed
  `transition`. `clean_label` rejects a label whose type disagrees with its library entry, and the
  event drops out of the shared graph.
- **`actor` is the athlete whose game the node belongs to** — `guard` to the guard player,
  `pass` to the passer. Full model: `match_event_model.md`.

`scripts/refine_pbp.py` exists as a keyword-extraction first pass but is noisy; it is not a
substitute for reading the transcript.

## 4. Splice events into the dump · **maintainer** · `scripts/apply_events.py`

```bash
uv run python -m scripts.apply_events <slug>_data scripts/dumps/<event>_events.json
uv run python -m scripts.apply_events --check     # round-trip self-test, writes nothing
```

Pass the module name **with** the `_data` suffix and **without** `.py`. Sets each matched bout's
`events`, drops its `pbp`, normalizes any `"M:SS"` string `ts` to integer seconds, and rewrites the
dump in the same greppable form. Only matched bouts lose their `pbp`, so a partial sidecar leaves
the rest refinable in a later pass.

> `apply_events.py`'s own docstring says the sidecar lives in `transcripts/deepseek/`. It does not —
> every sidecar in the repo is in `scripts/dumps/`. The docstring is stale.

## 5. Register the dump · **maintainer** · `scripts/reprocess_all.py`

Add one tuple to the **`DATASETS`** list (61 entries as of 2026-08-13):

```python
("scripts.dumps.<slug>_data", "<Event tag or None>", "<Label>"),
```

`<Event tag>` groups bouts into one card page in `export.site_data` (all four ADCC-2022 weight
dumps share `"ADCC 2022"`); use `None` for career compilations with no card page. `<Label>` is what
you pass to `--only` and must be unique.

## 6. Import to the DB · **HUMAN / ORCHESTRATOR ONLY**

```bash
uv run python -m scripts.reprocess_all --only <Label> --dry-run --no-export   # parse + report, NO writes
uv run python -m scripts.reprocess_all --only <Label> --no-export             # WRITES to prod
```

> Writing to the shared prod Supabase DB is an orchestrator/human action. A subagent runs the
> dry-run, reports, and hands off.

**Always pass `--no-export`.** A bare run auto-calls `export.match_breakdown.run()`, which
regenerates `GrapplingArc/assets/matches/*.json` — the legacy Jekyll-era tree, removed from the
live site in 2026-06. It costs 10+ minutes and serves nothing. The real export is step 8.

Read the dry-run log for `dropping event with unknown actor <name>`: an `actor` that matched
neither athlete, silently discarded. Fix the sidecar and re-splice — do not just re-import.

`run_dump` resolves both athletes by normalized `athlete_key` (creating rows if unseen), de-dupes
on `frozenset(participants) + year` in both orientations (idempotent, safe to re-run),
canonicalizes every label against the 208-entry technique library, skips striking-heavy MMA bouts
with too little grappling, and sets `Match.video_url` from `url_mapping.json` (step 7).

**ELO replay is not a separate command.** Every non-dry-run import replays in full — the graph and
ELO are path-dependent, so each touched athlete is rebuilt from all their `status == "final"`
matches. `reprocess_all` collects the athletes across all dumps and replays each once at the end.

## 7. Video URLs · `url_mapping.json` · generator is broken

Linkage happens at **import** time, not export: `dump_import.video_index()` keys mapped bouts by
`(frozenset(athlete_key(a), athlete_key(b)), year)` and sets `Match.video_url` + `&t=<start>s`. The
exporter only reads the column.

`scripts/yt/build_url_mapping.py` is **broken for new events** — it hardcodes a root path and looks
for `<stem>.py` files that have not lived there since dumps moved. `url_mapping.json` holds 28
entries, all legacy.

Practical fix: hand-edit `url_mapping.json`, adding a top-level key shaped like an existing entry
(`event_title`, `video_url`, `file`, `matches[]` with `athlete`/`year`/`opponent`/`seconds`), then
re-run step 6 — `video_index()` re-reads the file every run.

**Never hand-edit `GrapplingArc/assets/matches/*.json`.** It is generated output; the edit is a
no-op overwritten on the next export.

## 8. Embeddings → pgvector · **maintainer**

```bash
uv run python -m analysis.embeddings all      # nodes, then edges + graphs + archetype centroids
```

Run after any import — new nodes and graphs have no vectors, and the map/ocean pages need them.

## 9. Export the site · **maintainer**

```bash
uv run python -m export.site_data             # ~7 min warm, ~10-12 min cold
```

Regenerates the entire `GrapplingArc/site/` bundle from the DB. Run **after** embeddings. It is
slow, not hung. Then commit and push `GrapplingArc/site/` on `main`; GitHub Pages deploys.

## 10. Validate · **maintainer**

```bash
uv run python -m analysis.match_deviance      # per (athlete, bout) deviance, most deviant first
```

High deviance plus a stark shift (`guard 32% → 0%`) on a low-event bout usually means a
mis-refined transcript: wrong actor ownership, wrong athlete, or noisy labels. Recheck those.

Site invariants (dup/self slugs, dead links, missing globals) are the `site-checker` agent's job.

---

## Quick full refresh

```bash
uv run python -m scripts.reprocess_all --no-export
uv run python -m analysis.embeddings all
uv run python -m export.site_data
uv run python -m analysis.match_deviance
```

## Pre-flight

- Stem does not collide with an existing `DATASETS` label or queue entry —
  `uv run python -m scripts.transcript_status`.
- Ref-block times match `\d{1,2}:\d{2}(:\d{2})?`, or the bout window silently fails to parse.
- Sidecar `actor` strings are exact full names from the bout key.
- After a batch of imports, check for name-variant duplicates:
  `uv run python -m scripts.dedupe_athletes --dry-run` (destructive for real — read the report).

## Where the docs stand

| Doc | Status |
|---|---|
| **`ingestion_pipeline.md`** (this file) | The pipeline. Authoritative. |
| **`PROMPT_events_sidecar.md`** | The refiner rules AND the copy-paste prompt. Authoritative. |
| `match_event_model.md` | The event/actor model. Authoritative, referenced above. |
| `deepseek/E-refine-events.md` | Superseded — its bout-relative timestamp rule is wrong. |
| `deepseek/F-transcript-to-dump.md` | Superseded — documents the legacy whole-dump output, and its header contradicts its own §6. |
| root `TRANSCRIPT_PROCESSING.md` | Historical — describes the legacy `convert_dump` path. |

The legacy path still exists for old events: a hand-authored `transcripts/<stem>.py` dict literal
converted by `scripts/convert_dump.py <stem>.py <slug>` into the same `scripts/dumps/<slug>_data.py`
shape, with `events` already filled and no splice step. New events do not use it.
