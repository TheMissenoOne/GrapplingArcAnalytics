# Hybrid reprocessing plan — narration + frames via Gemini (v2 refining process)

**Status: plan, not implemented.** No code in this repo calls Gemini today (`grep -rl "gemini\|google-genai" --include="*.py" .` → nothing, `pyproject.toml` carries no such dependency). This
document is the design for the refining process that runs **when the DB is eventually migrated
and every match gets reprocessed** — it replaces transcript-only refining
(`docs/ingestion_pipeline.md` steps 2-4: `batch_queue.py` → pbp → LLM sidecar by hand →
`apply_events.py`) with a hybrid read: the narration transcript **and** captured frames sent
together to a multimodal model (Google Gemini), with narration anchoring the visual read rather
than standing alone.

Scope boundary: this doc is the *next* refining process, not a change to the two that exist
today. `docs/ingestion_pipeline.md` is still authoritative for the live transcript-only path;
`docs/frame_pdf_reading.md` is still authoritative for the manual frame-reading loop this plan
extends. Nothing here changes `docs/match_event_model.md`'s actor-ownership rules — a hybrid
read still has to produce `actor`/`type`/`successful` under that same model.

## 1. Inputs per bout

| Input | Source | Notes |
|---|---|---|
| Video URL | the bout's page (YouTube or FloGrappling) | Both proven through `scripts/frame_pdf.py`'s existing `yt-dlp --cookies-from-browser firefox` path (`COOKIES_FROM_BROWSER = "firefox"`, `_cookie_args()`) — no Flo-specific branch exists in the script, the generic downloader already handles it. Flo measured working 2026-08-25; the page's own URL is what gets passed in, never the CloudFront `.m3u8` the player streams from — that URL is session-scoped and not stable input for a manifest entry. |
| Transcript | `Entry.transcript` (path to a `.md`, see `parse_transcript`) when one exists | Timestamps are **video-absolute**, the same invariant the whole pipeline enforces (AA-010, `scripts/frame_pdf.py` module docstring): seconds from the start of the file at the URL, never bout-relative. A bout-relative transcript silently misattributes every caption to the wrong window. |
| Sampling step | `DEFAULT_STEP_SECONDS = 5` | Unchanged default — chosen against what a reader has to resolve (a pass, a sweep, a back take), not against exhaustiveness; a 10-minute bout stays ~120 frames. `Entry.step` overrides per bout when a denser pass is warranted (see §5's `--zoom` gap noted in `docs/frame_pdf_reading.md` §4.1). |

No new manifest shape is needed for this — `Entry` (`scripts/frame_pdf.py`) already carries
`url`, `start`/`end`, `step`, `kind`, and `transcript`. A hybrid-read manifest is the same JSON
`--manifest` already accepts, with `transcript` populated wherever a video has one.

## 2. The PDF as the exchange artifact

`frame_pdf.py` already attaches per-window narration to the rendered sheet: `transcript_window()`
joins every caption line whose timestamp falls in `[t, t+step)`, and the render loop writes
`captions.get(ts) or "(no narration in this window)"` onto each frame's caption (line ~915).
Silence is a real, explicit answer, not an absence — the placeholder string exists so a model
never confuses "nothing was said" with "this caption wasn't looked up."

That makes one PDF simultaneously three things:

1. **The durable storage artifact.** Bulk per-frame JPEGs are dropped after render (policy
   adopted 2026-08-25) — only the PDF is kept, archived under `data/frame_pdf/out/processed/`.
   *(Caveat: as of this writing `docs/frame_pdf_reading.md` §1 still describes `out/processed/`
   as unadopted — empty, unreferenced by any code, superseded by the flat
   `data/frame_pdf/out/<slug>/events.json` contract. That doc is owned by someone else mid-edit;
   reconcile the two before this plan starts moving frames, since one of them is stale.)*
2. **The human-review artifact.** `scripts/frame_registrar.py` is the review surface today and
   stays one under this plan — a human still checks a model's hybrid read against the same pages.
3. **A candidate model input.** Frames + captions laid out on a page, already legible to a
   human, is also plausible as what gets attached to a Gemini request instead of (or alongside)
   raw JPEGs — see §3's open question on this.

**Requirement carried forward by this plan:** every PDF produced for hybrid reading MUST be
rendered with narration attached whenever a transcript exists for that video — i.e. `Entry.transcript`
populated, not left empty. A sheet with no captions gives the model nothing to anchor identity or
outcome against and degrades silently to the frames-only case (§4).

**Orientation.** The current page is `PAGE_W, PAGE_H = 1654, 2339` — A4 at 200 dpi, **portrait**.
A `--orientation` flag is being added now (not yet in `scripts/frame_pdf.py` as of this writing —
`grep -n orientation scripts/frame_pdf.py` returns nothing) and landscape is the candidate default
for model input specifically: it holds each 16:9 broadcast frame at higher effective resolution
than portrait's narrower column, which matters given `docs/frame_pdf_reading.md`'s measured
finding that the two athletes occupy as little as ~3% of frame area at low resolution.

## 3. Model step (Gemini)

Input bundle per request:

- Frames (or PDF pages — open question, §6) for the bout's sampled window.
- Each window's transcript slice — already computed by `transcript_window()`, so no new
  extraction step; the same function that stamps a PDF caption produces the model's caption.
- `data/frame_pdf/node_library.json` — the closed technique vocabulary (376 nodes as of this
  writing, `{"key", "label", "type", "node_type", "corpus_events"}` per entry). Same role it
  plays for a human or model reader today: every returned `label` must resolve against it.
- The bout context page content `frame_pdf.py` already builds for a human reader (which fight,
  competitor kit hints, the answer schema).

Output: structured JSON in the **existing** `scripts/frame_answer.py` schema — `bout` (
`athlete_a`/`athlete_b` required; `event`/`year`/`winner`/`win_type`/`bout_start_seconds`/
`bout_end_seconds`/`final_score`/`advantages`/`penalties`/`identity_discriminator`/
`identity_verified_by`/`notes` optional) and `events` (`ts`/`label`/`actor`/`successful`/`type`
required, `points`/`confidence`/`note`/`new_label` optional). This is a hard requirement, not a
suggestion — reusing the schema is what makes every downstream stage (validator, registrar,
converter) work unmodified against a Gemini answer exactly as it works against today's manual
reads.

**No new pipeline stages.** The provenance chain already exists and does not need to know its
reading came from a person or a model:

```
Gemini reads bout → events.json (source: "frame_answer_import (returned reading, not yet
  human-reviewed)")
  → scripts/frame_registrar.py human review (stamp_source() → "frame_registrar (human review
    over model reading)")
    → scripts/frame_answer_to_dump.py  (REVIEWED_MARK gate: only "human review" in `source`
      converts — a raw Gemini output is skipped, reason recorded, exactly like today's model
      reads)
      → scripts/dump_import
```

`scripts/frame_answer_import.py` is already shaped for a model's output specifically (the docstring:
"an answer produced by reading a sheet was a dead end until someone retyped it... nothing is
auto-corrected"). Gemini becomes the thing producing the `answers.json` that script's `--write`
consumes — it does not replace `frame_answer_import.py`, `frame_registrar.py`, or
`frame_answer_to_dump.py`; it replaces the **manual or single-modality vision-model reading step**
that currently produces that file by hand.

## 4. Narration-anchoring rules

The two modalities are not equally trustworthy for every fact, and the schema's own fields
already carve the split:

| Question | Narration contributes | Frames contribute |
|---|---|---|
| Who is who | commentator naming a competitor by name | kit discriminator (§2.1 of `docs/frame_pdf_reading.md`: shorts colour, spats, hair) |
| Score / outcome | scoring calls ("two for the takedown"), finish confirmation ("tap!") | scoreboard graphic (authoritative but easy to miss between samples) |
| Position / technique | rarely precise enough alone ("scrambling here") | the primary evidence — a guard pass or a sweep is a visual fact |
| Round/clock context | commentary references to time remaining | the on-screen clock (`docs/frame_pdf_reading.md` §2.2's clock-mapping arithmetic) |

**Conflict rule:** frames win for **position identity** (what technique/position is visible —
narration lags or generalizes what it describes); narration wins for **actor identity** and
**outcome** (who did it, who won, whether it finished — narration names people, frames show
generic bodies in matching gis). This mirrors §2.1 of `docs/frame_pdf_reading.md` almost exactly:
that section already treats the winner-card frame as the identity anchor because "grapplers in
no-gi are frequently both in black" — narration widens that anchor to cover every window, not
just the final one.

**Silence degrades gracefully.** A window whose caption is the literal placeholder
`"(no narration in this window)"` is read frames-only for that window — this is the existing
behaviour of a manual read over a sheet with a silent stretch, not new logic; the hybrid model
just gets told explicitly (via the same placeholder string) when it has no narration to lean on,
rather than being left to guess whether an empty caption means "nothing said" or "extraction
failed."

## 5. Migration tie-in + scars

**Video/offset precedence already handles a reprocessed hybrid read without new code.**
`scripts/dump_import._resolve_video`'s precedence (documented in its own docstring, and
`docs/ingestion_pipeline.md` §7): `explicit_seconds`/`explicit_ts_origin` — frame-pdf's own
`video_start_seconds`/`ts_origin` (alembic `0047_match_video_clock`) — **always win** on a
reimport; an existing non-null `video_url`/`video_start_seconds` in the DB is **never
overwritten**. A hybrid-read reprocessing pass re-asserts its own timing every run, and a hand
fix applied via `scripts/apply_video_fixes.py` survives it.

**The dumps-diverged scar (docs referenced: `scripts/backfill_edge_bouts.py`,
`scripts/prune_orphan_athlete_graphs.py`).** The AA-011 repair — three phantom athletes deleted,
a duplicate WNO 24 bout merged, 866 → 865 matches — landed by hand-editing the **DB**, and was
never carried back into the source dumps. A full `reprocess_all` from the dumps as they exist
today would resurrect the deleted phantoms, because de-dup keys off
`frozenset(participants)+year` taken from the dump, not the DB. **A corpus-wide reprocess at
migration time must start from the repaired DB state, or re-apply the AA-011 repair as a step
before/after replay** — reprocessing straight from `scripts/dumps/*.py` is not safe as-is.

**One-sided-filing hazard, and why hybrid reading is expected to fix it at the source.**
`analysis/attribution.py` / `docs/match_event_model.md` measured **307 of 700 bouts (43.9%)**
file every event under one athlete — a property of which ingest batch produced the dump, not of
the corpus (`CJI 2 - Day 1` and `Polaris 18`: zero one-sided bouts; `ADCC 2022`: 15 of 30). That
happened because some transcript-only batches set `actor` to whoever the transcript happened to
be narrating at the time, with no independent check. A hybrid read has that independent check
built in — the frame shows which body did what, and narration is used only to *name* that body,
not to decide whose game the event belongs to. Reprocessing a currently one-sided bout under this
plan should correct its attribution rather than reproduce it, provided the anchoring rule in §4
is actually enforced (frames decide the position, narration only supplies the name).

## 6. Open decisions for the owner

- **Gemini API wiring.** No key, no SDK dependency, no client code exists in this repo yet
  (`pyproject.toml` has nothing Gemini-related). Needs: which Gemini model/tier, where the key
  lives (`.env`, not committed — same convention as `.env.example`'s Kaggle keys), and a rough
  cost estimate per bout at the ~50-150 frames/bout range a 5s step over a typical bout produces.
- **Batch size** — one bout per Gemini request (matches today's per-folder `out/<slug>/` unit),
  or batched across a manifest to amortize the context-page/vocabulary tokens.
- **PDF pages vs. raw frame JPEGs as the model input.** The PDF already exists and is the review
  artifact; sending it directly avoids a second export path, but a multi-frame grid page may read
  worse to a vision model than individually-cropped frames (`docs/frame_pdf_reading.md` §2.3/§4.3:
  cropping to the ~3% action region was the single highest-leverage manual habit measured). Needs
  a side-by-side test before committing to one.
- **Whether landscape becomes the default** page orientation once `--orientation` lands, or stays
  opt-in for hybrid-model input specifically while portrait stays the human-review default.
- **Review-sampling rate once model quality is measured.** Today every reviewed file requires
  100% human pass-through (the `frame_answer_to_dump.py` gate has no partial-trust mode). Once a
  batch of Gemini reads has measured accuracy against human review, decide whether some fraction
  can skip review — this plan does not propose loosening the gate now, only naming it as the
  next decision once there is data to decide it with.

## Provenance & maintenance

Written 2026-08-25, before any Gemini integration code exists — a design document, not a record
of a shipped feature. Every code reference above was checked directly against source at that
date:

- `scripts/frame_pdf.py`: `COOKIES_FROM_BROWSER`, `_cookie_args()`, `Entry` dataclass fields,
  `DEFAULT_STEP_SECONDS`, `transcript_window()`, the caption line (`captions.get(ts) or
  "(no narration in this window)"`), `PAGE_W, PAGE_H` (A4, portrait), and the absence of any
  `--orientation` or `--zoom` flag — all read directly, not inferred.
- `scripts/frame_answer.py`, `scripts/frame_answer_import.py`, `scripts/frame_answer_to_dump.py`,
  `scripts/frame_registrar.py`: schema fields, provenance stamps (`stamp_source`,
  `REVIEWED_MARK`), and the review gate read directly from source.
- `scripts/dump_import.py`: `_resolve_video`'s precedence read directly from its docstring.
- `scripts/backfill_edge_bouts.py`, `scripts/prune_orphan_athlete_graphs.py`: AA-011 repair
  numbers (three phantom athletes, duplicate WNO 24, 866 → 865) read directly from source
  comments.
- `analysis/attribution.py` / `docs/match_event_model.md`: the 307/700 (43.9%) one-sided-filing
  figure and per-batch breakdown read directly from source.
- `data/frame_pdf/node_library.json`: node count (376) and shape read directly.
- Gemini SDK/dependency absence: `grep -rl "gemini\|google-genai" --include="*.py" --include="*.toml" .`
  returned nothing.

Not verified here (left as the §6 open items): actual Gemini API behaviour on this kind of input,
real cost per bout, and whether PDF pages or raw frames read better to that specific model — none
of this is testable without wiring a key, which is explicitly out of scope for this doc.
