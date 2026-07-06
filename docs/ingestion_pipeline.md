# Match ingestion pipeline — transcript → DB → embeddings → site

End-to-end, how one grappling/MMA event goes from a raw YouTube page to live interactive
breakdowns. Ownership is marked per step: **YOU** (manual), **DeepSeek** (refiner LLM),
**maintainer** (the deterministic scripts — me or you running them).

```
 YOU            batch_queue        DeepSeek           apply_events      reprocess_all      embeddings        site_data
transcript ─▶ preliminary dump ─▶ events sidecar ─▶ spliced dump ─▶ matches in DB ─▶ pgvector ─▶ site/  ─▶ validate
 (.txt)        (pbp + [])          (_events.json)     (events)        (+ELO replay)    (768-d)     (pages)   (deviance)
```

---

## 1. Grab the transcript  ·  **YOU**  ·  → `transcripts/queue/<Event>.txt`

From the YouTube video page, save one `.txt` with three parts:

- **Ref block** (top) — the match card = **source of truth for the bout list + start times**.
  One line per bout: `Name vs Name: (M:SS)` or `Name vs Name: (M:SS - M:SS)`. Copy it from the
  video description / pinned comment. Wrap it as `Ref:"…"`.
- **Link** — `Link: https://www.youtube.com/watch?v=<id>` (the video; used for per-event seek).
- **Transcript body** — the full auto-caption dump (the `timestamp / duration / text` blocks).
  This is the source of truth for the **event sequence**.

Drop the file in `transcripts/queue/`. The filename stem is the event handle (e.g. `Polaris31.txt`);
map a nicer display name in `batch_queue.STUB_EVENTS` if the stem is ugly.

## 2. Preliminary dump  ·  **maintainer**  ·  `scripts/batch_queue.py`

```bash
uv run python scripts/batch_queue.py
```

Parses every queued transcript → `scripts/dumps/<event>_data.py`: one bout per `("Name", year)`
key, each carrying preliminary `winner`/`method` guesses and a **`pbp`** window (cleaned,
timestamped commentary) with an **empty `events`** list. Output is pretty-printed (greppable) and
still a valid importable module. It never invents events — that's step 3.

## 3. Refine pbp → events  ·  **DeepSeek**  ·  → `scripts/dumps/<event>_events.json`

DeepSeek walks each dump with grep + a python bout-reader, checks labels against the technique
library, and emits a sidecar `{ "<a_name>|<year>": [ {label,type,actor,successful?,ts} ] }`.
Spec: **`docs/deepseek/E-refine-events.md`**. The rule that governs *which fighter* owns each node
(guard → the guard player, not the passer) is **`docs/match_event_model.md`** (§ Actor Ownership).

## 4. Splice events into the dump  ·  **maintainer**  ·  `scripts/apply_events.py`

```bash
uv run python -m scripts.apply_events <module> transcripts/deepseek/<event>_events.json
uv run python -m scripts.apply_events --check     # round-trip self-test
```

Sets each matched bout's `events`, drops its `pbp`, normalizes `ts` "M:SS"→seconds, rewrites the
dump. Only matched bouts lose their pbp, so a partial sidecar leaves the rest refinable.

## 5. Register + import to the DB  ·  **maintainer**  ·  `scripts/reprocess_all.py`

Add each refined dump to the `DUMPS` list: `("scripts.dumps.<mod>_data", "<Event tag>", "<Label>")`
(the *Event tag* is the card-page grouping, e.g. `"Polaris 31"`). Then:

```bash
uv run python -m scripts.reprocess_all --only <Label> --dry-run   # parse + report, no writes
uv run python -m scripts.reprocess_all --only <Label>             # import this event
uv run python -m scripts.reprocess_all                            # re-import the whole corpus
```

`run_dump` de-dupes bouts (`frozenset(participants)+year`), idempotently replaces them, and
**double-pass replays both athletes' ELO**. Labels are canonicalized to the technique library and
actors resolved to sides on the way in (`_clean_events` drops any event whose actor ≠ either
athlete — see `docs/match_event_model.md`).

## 6. Embeddings → pgvector  ·  **maintainer**  ·  `analysis/embeddings.py`

Run after any import (new nodes/graphs need vectors). This **propagates the 768-d embeddings into
the DB** (`technique_nodes`, `graph_edges`, `graphs`, `archetypes` pgvector columns):

```bash
uv run python -m analysis.embeddings all      # nodes, then edges + graphs + archetype centroids
# (or: `backfill` = nodes only · `graphs` = edges+graphs+archetypes only)
```

Powers the semantic grappling-map / "related positions" / stylistic nearest-graphs.

## 7. Export the site  ·  **maintainer**  ·  `export/site_data.py`

Regenerates the **entire** `GrapplingArc/site/` bundle from the DB (all `*-data.js` globals +
`breakdown-*`/`grapple-*`/`event-*`/`the-ocean.html`). Run it **after** embeddings so the
map/ocean pages reflect fresh vectors:

```bash
cd ../GrapplingArcAnalytics && uv run python -m export.site_data   # ~10-12 min (N+1 over remote DB)
```

Then commit + push `GrapplingArc/site/` (main → GitHub Pages deploys). Per-phase timing prints to
the log; it is slow, not hung (see [[queue-refiner-and-export-perf]]).

## 8. Validate  ·  **maintainer**

- **Recheck list — did any bout come out unlike the athlete's usual game?**
  ```bash
  uv run python -m analysis.match_deviance          # per (athlete, bout) deviance, most-deviant first
  ```
  High deviance + a stark `shift` (e.g. `guard 32%→0%`) on a low-event bout = likely mis-refined
  (wrong actor ownership / wrong athlete / noisy labels). Recheck those transcripts.
- **Import warnings** — `reprocess_all --dry-run` output: any "dropping event with unknown actor"
  means an actor didn't resolve to a fighter (fix the name in the sidecar).
- **Site invariants** — the `site-checker` agent: no dup/self slugs, dead links, missing globals.

---

### Quick full refresh (after a batch of imports)

```bash
uv run python -m scripts.reprocess_all         # import all registered dumps + ELO replay
uv run python -m analysis.embeddings all       # re-embed → pgvector (propagate to DB)
uv run python -m export.site_data              # regenerate site/
uv run python -m analysis.match_deviance       # QA: what to recheck
```

Sub-specs: refiner = `docs/deepseek/E-refine-events.md` · event/actor model =
`docs/match_event_model.md` · public-site contract = `../GrapplingArc/CLAUDE.md`.
