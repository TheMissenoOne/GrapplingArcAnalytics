# Reading a frame sheet — operator guide and automation backlog

`scripts/frame_pdf.py` turns competition footage into frames a vision model reads back as a
timestamped match sequence. That script is documented by its own docstring; **this doc covers the
half that is still manual** — how to actually read a rendered sheet without inventing events, where
the answer goes, and what should be automated next.

Scope boundary: the transcript → dump → DB → site path is `docs/ingestion_pipeline.md`. The event
and actor-ownership model is `docs/match_event_model.md`. This doc starts at "a sheet has been
rendered" and ends at "an answer JSON is ready to import".

## 1. The loop today

| # | Step | Command | Owner |
|---|---|---|---|
| 1 | Refresh the label vocabulary | `uv run python scripts/frame_pdf.py --dump-library` | maintainer |
| 2 | Render sheets for a manifest | `uv run python scripts/frame_pdf.py --manifest data/frame_pdf/women_65.json` | maintainer |
| 3 | Render as a directory instead of a PDF | add `--format frames` | maintainer |
| 3a | Render landscape (bigger, more 16:9 cells) | add `--orientation landscape` | maintainer |
| 4 | **Read the sheet → answer JSON** | manual, see §2 | vision model |
| 5 | Validate the answer | `uv run python scripts/frame_answer.py` | maintainer |
| 6 | **Human review against the frames** | `uv run python scripts/frame_registrar.py` → localhost:8765 | maintainer |
| 7 | **Convert answer → dump shape** | `uv run python -m scripts.frame_answer_to_dump [--write]` | maintainer |
| 8 | Import the dump | `scripts/dump_import` | maintainer |
Step 7 (`scripts/frame_answer_to_dump.py`) reads every `events.json`, converts only the
files a human has reviewed, and refuses (rather than guesses) anything it cannot resolve —
see §4.7. It never opens a database connection; `--write` only produces
`scripts/dumps/frame_pdf_data.py`, a plain dump `.py` for step 8 to import later.

**Answer location (corrected again 2026-08-25 — `out/processed/` IS now adopted):**
`data/frame_pdf/out/processed/<slug>.pdf` plus a `<slug>.events.json` sidecar, written by
`scripts/frame_answer_import.py` (and rewritten by `frame_registrar.py` on save). Measured
2026-08-25: `processed/` holds all 24 bout PDFs, 21 `.events.json` sidecars (model readings,
preserved) and 1 hand-read `.json`. The file's `source` field carries provenance:
`frame_answer_import (…not yet human-reviewed)` for a raw model reading, `frame_registrar
(human review over model reading)` once a human has passed through the registrar. Only
reviewed files are admissible for import (§4.7's converter must gate on this).

**Storage policy (2026-08-25):** once a bout's PDF is rendered, its image folder (`strip/`,
`clip.mp4`, `frames.jsonl`) is dropped — the PDF is the durable artefact, and frames are
regenerable from the video URL. `out/` now holds only `processed/` (~2.5 GB freed). Consequence
for §1 step 6: reviewing one of the 21 preserved model readings against its frames requires
re-capturing them first — re-run `frame_pdf.py --format both` for that bout (or `--format
frames` alone) before opening `frame_registrar.py`.

The 56 ADCC-trials PDFs live canonically in `data/frame_pdf/trials_2023_24/` (duplicates that
used to sit under `out/` were removed); they already carry per-window narration captions
sourced from the event transcript (`--transcript`, §2 in the script's own docstring).

**`--orientation landscape`** (added 2026-08-25): rotates the sheet to A4 landscape and, unless
`--grid` is given explicitly, switches the default grid from 2x3 (portrait, 6 frames/page) to
2x2 (landscape, 4 frames/page) — measured to render each cell's 16:9 video frame at roughly
2.2x the area of the portrait default, and closer to the actual 16:9 aspect (box aspect 1.58
vs portrait's 1.08). A naive 3x2 relabelling of the portrait grid was checked and rejected —
it measures out smaller than portrait, not bigger, because column count (not row count) drives
the width-constrained cell size. Use it for a bout you expect to need cropping/scrubbing detail;
portrait stays the default (matches existing sheets, and fits more frames per page).

**Sourcing from FloGrappling** (measured 2026-08-25): `frame_pdf.py` works unchanged against a
FloGrappling page URL — `yt-dlp`'s generic extractor resolves the CloudFront m3u8 the page
embeds, and `--cookies-from-browser firefox` (the script's default) authenticates as the logged-in
browser session. Because FloGrappling serves split video/audio streams, the existing
`bv*[height<=…]` selectors (§4.2 below) are required — a bare `b[height<=720]` format string
fails on this source. **Store the page URL** in a manifest
(`flograppling.com/video/<id>-slug` or `flograppling.com/events/…?playing=<id>`), never the
resolved CloudFront playlist URL — that carries expiring auth tokens and will 403 on any later
re-run.

## 2. How to read a sheet

Do these in order. The order is the point — steps 1 and 2 make steps 4 and 5 possible, and doing
them last means re-reading everything.

### 2.1 Read the END first

The last frames carry the result graphic and the hand-raise. They tell you **who won** and, far more
importantly, **which competitor is which**. Grapplers in no-gi are frequently both in black; the
victory card is often the only frame in the whole sheet that binds a name to a body.

Work backwards from it: find a kit discriminator that holds across the whole bout (shorts colour,
long spats versus bare legs, hair) and only then read the timeline forwards.

### 2.2 Derive the clock mapping

The match clock is visible and counts **down**. Two frames give you the bout's position in the video:

```
bout_start_seconds = frame_ts - (match_duration - clock_reading)
```

Worked example (2026-08-20, `8xvq3lM6kQY`): the clock reads 10:00 at t=0 and t=5, and 9:56 at t=10.
Four seconds had elapsed by t=10, so the bout started at video-absolute **t=6**. The clock freezing
at 7:58 puts the finish at 6 + 122 = **t≈128**.

This is also the only reliable way to spot a bout that starts partway into a video, which is the
mislocalisation defect the frame_pdf docstring warns about (AA-010).

### 2.3 Crop before you read — do not read full frames

**This is the single highest-leverage habit.** These are wide-angle fixed-camera broadcasts. On the
bout measured 2026-08-20 the two athletes occupied roughly a 110×70 px region of a 640×360 frame —
about **3% of the frame area**. The rest is empty mat, banners and crowd. A vision model resizes the
whole image into a fixed budget, so reading full frames spends ~97% of that budget on nothing.

Cropping to the action and upscaling turned "cannot tell who is on top" into a confident read:

```bash
# generous action window, 6x — good default for a first pass
magick tNNNNN.jpg -crop 270x150+195+55 +repage -filter Lanczos -resize 600% zoom.png

# tight, 9-10x — for settling top/bottom in a ground scramble
magick tNNNNN.jpg -crop 110x70+215+100 +repage -filter Lanczos -resize 900% tight.png

# scoreboard strip: stack several frames' score regions into one image
magick t00045.jpg t00070.jpg t00100.jpg t00125.jpg \
  -crop 150x50+0+5 +repage -filter Lanczos -resize 500% -append score.png
```

Upscaling adds no information — the win comes from the **crop**, which is what redirects the
model's attention budget. Lanczos just keeps the existing pixels legible after the crop.

### 2.4 Read the scoreboard as its own pass

Points are the one thing the scoreboard is authoritative for, and it sits at a fixed screen
position, so a stacked strip (above) settles the whole bout in one image. Read it at several
checkpoints rather than trusting one frame.

A scoreboard that never changes is itself a strong finding: it **bounds** every other reading. On
the 2026-08-20 bout the board was 0-0-0 for both athletes at t=45/70/100/125, which independently
rules out any credited takedown (2), sweep (2) or completed guard pass (3) — so a "she passed the
guard here" reading can be rejected without ever seeing the pass.

Never write `points: 0` to mean "could not tell". An absent field is the only thing that carries
"nobody could score this".

### 2.5 Label coarsely, and prefer omission to invention

At one frame every 5s, techniques start and finish between samples and leave only a result. Record
the result generically (`Guard Pass`, `Takedown`, `Submission`, `Top Control`) rather than naming an
entry nobody saw. Every label must come **verbatim** from the sheet's `labels.md` — an unlisted
spelling does not fail, it mints a second node and splits one technique in two silently.

Check labels mechanically before returning an answer:

```bash
cd data/frame_pdf/out/<slug>
for l in "Guard" "Top Control" "Submission"; do
  grep -qx "\- $l" labels.md && echo "OK   $l" || echo "MISS $l"
done
```

### 2.6 State the identity discriminator in the answer

Write down *how* the two athletes were told apart and *what verified it*. An identity mapping that
is asserted but not justified is unfalsifiable, and a correct technique on the wrong athlete is two
errors. The answer schema (§4.6) should require this field.

### 2.7 The result line is not evidence

The BJJ Heroes line printed on the sheet exists so you can tell the competitors apart and know the
bout ended. An armbar named there and an armbar visible in the frames are two different claims;
only the second belongs in the answer.

## 3. Measured baseline — first full read (2026-08-20)

Bout: `anabel-lopez-vs-aurelie-le-vern--european-no-gi-2024-8xvq3lM6kQY`, 32 frames at 5s.

| Observation | Number | Consequence |
|---|---|---|
| Action region as share of frame area | ~3% | full-frame reading is unusable; §2.3 is mandatory |
| Athlete height in frame | ~60 px | top/bottom unreadable without a crop |
| Source resolution available | **1280×720** (fmt 136) | now sampled at 1280×720 for `full_match` — §4.2 |
| Frames with no bout action | 5 of 32 (16%) | 2 pre-start, 3 channel outro cards |
| Events produced | 7 | |
| Items left unresolved | 4 | 2 of them purely from the 5s sampling gap |
| Scoreboard changes in the bout | 0 | bounded every position reading |

The two sampling-gap losses were the transition that put a competitor on the ground (0:15→0:20) and
the finish itself (2:05→2:10). Both are exactly what a denser second pass over a named interval
would recover — see §4.1.

## 4. Automation backlog, ranked by measured impact

### 4.1 `--zoom SLUG:START-END:STEP` — a second, denser pass (highest value)

The frame_pdf docstring already names this as the right fix when events fall between frames ("a
second pass at `--step 2` over the interval in question"), but there is no ergonomic path to it —
today it means hand-editing a manifest. A flag that re-renders one window of an already-rendered
bout would have resolved both unresolved transitions above for roughly 24 extra frames.

This is the only backlog item that improves **correctness** rather than ergonomics. Everything else
makes reading faster; this makes answers more complete.

### 4.2 Stop discarding resolution — DONE 2026-08-20

Two knobs conspire, and both must move together:

| Location | Current | Effect |
|---|---|---|
| `fetch()` format selector | `bv*[height<=480]/b[height<=480]/…` | picks 360p when 720p exists |
| `FRAME_WIDTH` | `640` | rescales back down at extraction |

The 480p ceiling has a stated rationale — "tell mount from side control, not read a patch on a
sleeve", plus disk pressure from full-event reels. The disk half still holds. The legibility half
did not survive contact with this bout: at 640×360 the reader could not reliably tell top from
bottom, which is precisely the thing the ceiling was chosen to preserve.

Suggested shape, rather than a blanket raise: a per-entry manifest field (`"quality": 720`) that
defaults to 480 for `kind: full_event` and 720 for `kind: full_match`. A single bout is minutes of
footage, not hours, so the disk argument barely applies to it.

### 4.3 Auto-crop to the action region

Do §2.3 in the renderer instead of by hand. `opencv-python` is already a dependency (`pyproject.toml`),
and no ML is needed: the mat is a large uniform blue/yellow field, so the athletes are the
low-saturation / high-contrast blob on it. Compute a per-video action bounding box once as the union
of motion across sampled frames, pad it, and crop every frame to it.

Payoff compounds with §4.2: a crop taken from a 720p source is genuinely detailed, where a crop from
360p is merely legible.

### 4.4 A scoreboard strip and, later, clock OCR

Two tiers:

- **Cheap:** crop the fixed scoreboard rectangle from every frame at native resolution and render a
  strip of them on the context page. Settles points and clock without squinting at 32 frames.
- **Real prize:** OCR the clock and score. Clock readings alone yield `bout_start_seconds`,
  `bout_end_seconds` and every score change **with its timestamp** — deterministically, with no
  model judgement. That is the most error-prone manual arithmetic in §2.2 and §2.4.

Note `tesseract` is **not** currently on this machine's PATH (checked 2026-08-20). For a broadcast
scoreboard, digit template-matching with OpenCV is likely more robust than general OCR anyway, and
avoids adding a system dependency.

### 4.5 Drop dead frames

Trailing channel outro cards ("THANK YOU FOR WATCHING") were 3 of 32 frames, and they are
indistinguishable from content until read. Detect by colour histogram — a frame with none of the mat
blue is not mat footage — and drop trailing frames that fail it. Pre-start frames are worth
**keeping**: they are often the cleanest view of both athletes standing apart, which is what §2.1
needs.

### 4.6 `scripts/frame_answer.py` — DONE 2026-08-20

One module, not the two proposed: the schema and the checks are the same knowledge, and splitting
them is how a validator ends up enforcing a shape the schema no longer describes. It rejects:

- any `label` not present verbatim in the sheet's `labels.md`
- any `ts` outside `[bout_start_seconds, bout_end_seconds]` or beyond the video duration
- any `actor` that is not one of the two named competitors (null allowed, and meaningful)
- `points: 0` anywhere (the "could not tell" anti-pattern)
- a missing identity-discriminator field (§2.6)
- non-monotonic `ts`
- a high generic-label ratio — a signal the bout wants a denser pass (§4.1) rather than a defect
  — **not implemented**; it is a ranking signal, not a defect, and belongs with §4.1

Two departures from the proposal above, both deliberate. `ts` is checked against the frames that
actually exist (`frames.jsonl`), not against `bout_start_seconds`/`bout_end_seconds`: those are
themselves claims in the answer being validated, so checking one field against another in the same
file proves nothing. And a null `actor` is REJECTED rather than allowed — the reader is told to put
the uncertainty in `note` and still name someone, because a null actor silently drops the event from
every per-athlete artefact downstream while looking like a recorded observation.

### 4.6b `scripts/frame_registrar.py` — DONE 2026-08-20 (replaced the planned `frame_review.py`)

The half a validator cannot do: put the event next to the frame it was read off and let a human say
whether it is there. The registrar edits `events.json` in place; provenance survives the save via
`stamp_source()` — a save over a model reading stamps `frame_registrar (human review over model
reading)`, never plain `(human)` (fixed 2026-08-24; before that every save laundered model readings
into human authorship). `frame_review.py` and `review.json` were never written.

### 4.7 `scripts/frame_answer_to_dump.py` — DONE 2026-08-24

Converts reviewed answer JSON into the `(athlete_a_name, year)` dump shape `scripts/dump_import`
consumes. Never opens a database connection — reads `events.json`, writes a plain `.py` dump
literal (`scripts/dumps/frame_pdf_data.py`) on `--write`; importing it into the DB is step 8,
run separately by a maintainer.

Four things it refuses rather than guesses or silently drops:

- **Review gate.** Only files whose `source` contains "human review" convert (the
  `frame_registrar` stamp — §1). A raw model reading (`frame_answer_import (…not yet
  human-reviewed)`) is skipped, reason recorded. Measured 2026-08-24: all 21 files under
  `out/` are still unreviewed, so a real run converts zero files — the gate working, not a bug.
- **Actor/winner resolution.** Every event's `actor` and the bout's `winner` are resolved via
  `analysis.names.athlete_key` against ONLY that file's own `bout.athlete_a`/`athlete_b`. A
  name matching neither is refused — excluded, counted, listed — never guessed onto a side.
- **Slash labels.** `analysis.names._normalize_name` folds "Reset / Stalemate" and
  "Reset/Stalemate" to different keys depending on spacing (a known defect in the shared
  node-key contract, not fixed here). Any label containing `/` is refused (`slash_label`)
  rather than let a mangled key into the corpus.
- **Key whitelist.** Output events keep exactly `label`/`type`/`actor`/`ts`/`successful`/
  `points` (the same set `scripts/insert_ufc_matches.py`-style import consumes);
  `confidence`/`note`/`new_label` are dropped, with a per-file dropped-key count in the report.

`ts_origin='video_absolute'` and `video_start_seconds` (from the answer's
`bout_start_seconds`) are carried onto every converted block and, since `scripts/dump_import`
did not have anywhere to put them, `CanonicalMatch`/`build_matches`/`run_dump` were extended
with two new optional fields to carry them onto `matches.ts_origin`/`video_start_seconds`
(alembic `0047_match_video_clock`) — every other dump source leaves them `None`, unchanged.

### 4.8 `--status` for the sheet backlog

`frame_pdf.py` already skips fights whose video backs a reviewed sequence, but that check is a DB
query only. A `--status` mode reading `out/processed/` would show the three states that matter:
rendered-but-unanswered, answered-but-unimported, done. As of 2026-08-25 `out/processed/` holds
24 PDFs, 21 unreviewed `.events.json` sidecars and 1 hand-reviewed one — the frames directories
that used to sit next to them are gone (storage policy, §1).

### 4.9 Prompt additions for the generated README

Fold the lessons above into the context page the model actually reads:

- name the scoreboard's screen position and that it is fixed
- explain the countdown clock and the `bout_start_seconds` arithmetic (§2.2)
- require an explicit identity discriminator with its verification (§2.6)
- require `bout_end_seconds`, not just `bout_start_seconds` — the end is what bounds a finish
- instruct the reader to crop rather than read full frames (§2.3)
- optionally, a manifest `kit` field (`{"Aurelie Le Vern": "all black, long spats", …}`) printed on
  the context page. One line of human input per bout removes the hardest ambiguity in the whole
  task, at zero compute cost — the laziest fix on this list.

## Provenance & maintenance

Written 2026-08-20 from a first end-to-end manual read of
`data/frame_pdf/out/anabel-lopez-vs-aurelie-le-vern--european-no-gi-2024-8xvq3lM6kQY/` (32 frames),
which produced `out/processed/<same-slug>.json`.

Facts and how each was checked:

1. **720p available while we sample 360p** — `yt-dlp -F` on the video lists format 136 at 1280×720;
   `fetch()`'s selector is `bv*[height<=480]/b[height<=480]/…` and `FRAME_WIDTH = 640`. Both read
   directly from `scripts/frame_pdf.py`.
2. **~3% action area / ~60px athletes** — measured from the crop windows that produced a readable
   image (110×70 out of 640×360).
3. **Scoreboard never changed** — read from stacked crops of t=45/70/100/125.
4. **5 of 32 frames carried no bout action** — t=0 and t=5 pre-start (clock 10:00, not running),
   t=145/150/155 channel outro cards.
5. **`out/processed/` was unreferenced** — `grep -rn "out/processed\|frame_pdf/out" --include="*.py"`
   over the repo returned nothing outside `data/`.
6. **`tesseract` not on PATH**; `opencv-python>=4.10` and `pillow>=10.4` are declared in
   `pyproject.toml`.

Not verified: whether the 720p bump changes yt-dlp's failure rate on members-only or geo-blocked
uploads (the 403 behaviour documented in `fetch()` was not re-tested at a different quality); whether
the auto-crop heuristic in §4.3 holds on non-IBJJF footage with a different mat palette or a moving
camera. Both should be measured on a handful of bouts before either is made a default.

Re-verification commands:

```bash
cd GrapplingArcAnalytics

# what resolutions a given upload actually offers
yt-dlp -F --cookies-from-browser firefox "https://www.youtube.com/watch?v=<id>" | grep mp4

# the two resolution knobs
grep -n "FRAME_WIDTH\|height<=480" scripts/frame_pdf.py

# sheet backlog: rendered vs answered (out/ now holds only processed/, no frames dirs -- §1)
cd data/frame_pdf/out/processed && echo "pdf=$(ls *.pdf | wc -l) answered=$(ls *.events.json 2>/dev/null | wc -l)"

# label check for one answer -- frames dirs are dropped after render (§1), so this needs the
# folder re-captured first: uv run python scripts/frame_pdf.py --manifest <m> --format frames --force
python3 -c "
import json,sys,re,pathlib
slug=sys.argv[1]
d=json.load(open(f'data/frame_pdf/out/processed/{slug}.json'))
lab=set(re.findall(r'^- (.+)$', pathlib.Path(f'data/frame_pdf/out/{slug}/labels.md').read_text(), re.M))
bad=[e['label'] for b in d['bouts'] for e in b['events'] if e['label'] not in lab]
print('unknown labels:', bad or 'none')
" <slug>
```
