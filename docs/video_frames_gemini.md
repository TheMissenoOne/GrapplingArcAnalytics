# Video → frames → Gemini (experimental)

Two scripts, chained: `scripts/video_frames.py` turns one local video file into a frame sheet
(reusing `scripts/frame_pdf.py`'s grid renderer, not reimplementing it), and
`scripts/gemini_read_frames.py` sends that sheet to Gemini and saves the answer in the same
`{"bout": ..., "events": ..., "source": ...}` shape `scripts/frame_answer_import.py` already
consumes. This is a test of the extraction + reading pipeline on footage with no upload, no
broadcast graphics and (unlike `frame_pdf.py`'s YouTube path) a **handheld, moving camera** —
not a production import path yet. Fine-tuning on the model's high-confidence reads is a later
step, not attempted here.

## Flow

```
uv run python -m scripts.video_frames --video <path.mp4> --out data/frame_pdf/out/<slug>/
uv run python -m scripts.gemini_read_frames --sheets data/frame_pdf/out/<slug>/sheets \
    --out data/frame_pdf/out/<slug>/events.json
```

`video_frames.py` always writes `motion.json` (per-analysis-frame diff/motion series) and
`decision.json` (what it picked and why); `--dry-run` stops there, plus `motion.png` (three
series + the chosen timestamps marked in red) — for eyeballing the call before spending the
extraction/render pass. A real run adds `frames/*.jpg` (native resolution, named by
video-absolute second, same convention as `frame_pdf.py`) and a landscape 2x2 sheet PDF under
`sheets/`.

## The decision: fixed camera vs moving camera

Sampled at ~9 fps (dense enough to catch a short window, cheap enough to run ORB on every
pair), two things are measured between each consecutive analysis frame:

- **`diff_raw`** — plain grayscale mean-abs-diff, downscaled to 256px wide. Motion of any
  kind, camera included.
- **`cam_motion`** — the camera's OWN motion: ORB features + an affine RANSAC fit
  (translation + rotation + uniform scale — `cv2.estimateAffinePartial2D`, not a full
  homography, which degrades when matched points cluster on one side of frame, which a gi and
  a mat both do) between the two frames, expressed as one px-equivalent scalar (rotation
  converted to arc-length at the frame's half-diagonal, so it shares units with translation).
- **`diff_residual`** — `diff_raw` after warping the previous frame by the fitted camera
  motion first. What moved that the camera didn't cause.

`camera_moving` = median `cam_motion` over the whole video exceeds 2.0px, OR more than 30% of
windows spike above that (a camera that pans once through an otherwise-still shot passes the
median check and fails the frequency one — either alone is enough to call it moving). A failed
ORB fit (too few matches — a fast pan blurs corners away exactly when the estimate would
matter most) counts as high motion in the frequency check rather than being ignored, since an
unreadable estimate is itself evidence of motion.

- **Camera moving** → fall back to exactly `frame_pdf.py`'s own criterion: a fixed interval
  (`DEFAULT_STEP_SECONDS` = 5s, from 0 to the video's length, no trailing partial frame — same
  arithmetic ffmpeg's `fps=1/step` filter uses). `diff_residual` carries no signal once the
  whole frame is displaced every sample, so there is nothing left to be smart about.
- **Camera fixed** → Otsu-threshold `diff_residual` into static/action. Keep the CENTRE
  timestamp of every static run at least 1s long (a clean read of the held position; shorter
  runs are threshold noise, not a position) and the PEAK timestamp of every action run (most
  likely to show a completed technique rather than a mid-transition blur).

## This video (`data/video/owner/VID20260725WA0005.mp4`, 43s, handheld)

Measured: median `cam_motion` = **5.20px** (2.6x the threshold), **87%** of windows above
threshold → `camera_moving = true` as expected for a handheld phone clip that pans/tilts
throughout. Fell back to the 5s-interval criterion → **9 frames**, one landscape 2x2 sheet PDF
(4 frames/page, 3 pages), `owner_20260725.pdf` (~730 KB).

## Gemini reading

`gemini_read_frames.py` sends every `.pdf`/`.png`/`.jpg` under `--sheets` as inline file parts
plus the prompt in `docs/PROMPT_gemini_frame_reading.md` (its own `---`-delimited body only —
loaded fresh each run, so the two files cannot drift), asks for `response_mime_type:
application/json`, and stamps the result with `source: "gemini_read_frames (<model>,
thinking=<level>, <date>) — not yet human-reviewed"` — the same provenance convention
`frame_answer_import.py` uses, so a reviewed/unreviewed answer is distinguishable the same way
regardless of which script produced it. `automatic_function_calling` is explicitly disabled
(we never pass tools) so the SDK's "Direct use of AFC..." log line never fires. The raw
response text is also saved next to the answer (`gemini_raw.json`), alongside
`usage_metadata` (prompt/candidates/thoughts token counts) for cost tracking.

Default model is `gemini-3.6-flash` (`--model` to override). `--thinking {low,medium,high}`
(default `high`) sets `GenerateContentConfig.thinking_config.thinking_level`; a model that
rejects the field (400) is retried once without it, with a log warning, rather than failing
the whole run.

No `GEMINI_API_KEY` in the environment (checked via `os.environ`, `.env` loaded first) forces
`--dry-run` automatically: prints the resolved prompt and the file list it would have sent, and
still writes a correctly-shaped, empty `events.json` (`source` says why) so a caller does not
need two code paths depending on whether a key was present.

**First real read** (2026-09-03, `owner_20260725` sheet, `--thinking low`): 3 events —
Takedown 15s (Athlete B), Guard Play 20s (Athlete A), Side Control 35s (Athlete B), all
`confidence: "low"`. A `--thinking high` rerun on the same sheet read the same window
differently (Guard Pull 15s / Closed Guard 20s, both Athlete B; Side Control 35s, Athlete A) —
expected on a 9-frame, 40s handheld clip with no vocabulary page (see "Known gap" below);
thinking level changes what the model infers, not which frames it sees. Cost: prompt 3421
tokens (1293 text + 2128 image), 1422 thoughts tokens, 318 output tokens, 5161 total — a
rounding error per bout at Gemini Flash pricing.

**Gap closed (2026-09-05):** `video_frames.py:process` now embeds the vocabulary by default —
`build_sheet()` draws `frame_pdf.draw_library_pages` on the sheet unless called with
`no_library=True`, the same pages the broadcast/trials path already carries. The single-sheet
read described above predates this change and read without the vocabulary; the baseline runs
below (`gemini_baseline.py`, trials_2023_24 corpus) already use library-embedded sheets and are
the current numbers to trust for label-discrimination accuracy. The round-video production
path (`scripts/video_jobs.py`, `docs/video_jobs.md`) also gets the library for free through the
same default.

## Cost (rough)

Gemini's image tokenization scales with resolution, not bytes — a page raster around 1024px on
its long side is ~256-1300 tokens depending on tiling. A 3-page, 9-frame sheet PDF is a handful
of embedded JPEGs plus the context page's text, well under 5k input tokens; `gemini-3.6-flash`
(default; override with `--model`) pricing is per-million-token and this is a rounding error
per bout — measured total 5161 tokens for this sheet at `--thinking high` (see above). The real
cost driver at scale is bout COUNT, not sheet size — 100 bouts is still under a few hundred
thousand tokens.

## Baseline zero-shot (2026-09-03)

`scripts/gemini_baseline.py` measures a fresh zero-shot `gemini-3.6-flash`, `thinking=high`
read against ground truth, so the fine-tuning gain (next step, below) has a number to beat.
Ground truth: `data/frame_pdf/trials_2023_24/answers/<slug>.events.json`, concordance-audited
(`docs/gemini_concordance_audit.md`) — every kept event was independently re-read off the
frames by a human auditor, which is the "human provenance" this baseline needs.

**Deliberate scope adaptation.** The ticket named `data/frame_pdf/out/<slug>/events.json` +
its sheet as the source of human-reviewed pairs; measured 2026-09-03, that pipeline currently
has ZERO bouts carrying both a rendered sheet and a human/reviewed `source` (all 21
`out/processed/*.events.json` sidecars are still `frame_answer_import (…not yet
human-reviewed)`). The trials_2023_24 corpus does have 41 bouts with both a PDF sheet (library
pages embedded, same as any `frame_pdf.py` sheet) and a concordance-audited answer — that is
where this baseline actually runs. `select_candidates` picks bouts from the curated index in
file order, capped at `--n` (hard cap 10, cost guard).

Each candidate's sheet is read FRESH (no reuse of the reading that produced the ground truth —
that reading was already audited/corrected). Match rule: same `type`, same canonicalised label
(`scripts.enrich_from_audit.node_key`, i.e. `clean_label → _normalize_name → canonicalize`,
the repo's one node-key chain), `|ts diff| ≤ 10s` — greedy nearest-ts, one-to-one.

### Run: 10 bouts, `gemini-3.6-flash`, thinking=high

| type | support | predicted | TP | precision | recall | F1 |
|---|---|---|---|---|---|---|
| control | 32 | 12 | 4 | 0.33 | 0.13 | 0.18 |
| transition | 13 | 22 | 5 | 0.23 | 0.38 | 0.29 |
| guard | 22 | 4 | 2 | 0.50 | 0.09 | 0.15 |
| pass | 6 | 8 | 3 | 0.38 | 0.50 | 0.43 |
| escape | 4 | 3 | 0 | 0.00 | 0.00 | — |
| sweep | 1 | 0 | 0 | — | 0.00 | — |
| submission | 15 | 10 | 7 | 0.70 | 0.47 | 0.56 |
| takedown | 13 | 20 | 10 | 0.50 | 0.77 | 0.61 |
| **overall** | **106** | **79** | **31** | **0.39** | **0.29** | **0.34** |

Other numbers: mean `|ts|` error among matches **1.13s** (tight — when the model gets an event
right, it gets the timestamp very right), actor accuracy among matches **90%**,
`confidence: "high"` rate **100%** (every one of the 79 model events across all 10 bouts —
Gemini's own confidence field carries zero signal here; it never says "low", so it cannot
correlate with correctness). Cost: 140,610 prompt + 39,326 thoughts + 7,650 output = **187,586
tokens for 10 bouts** (~18.8k tokens/bout at `thinking=high`) — a rounding error at Gemini
Flash pricing, confirming the earlier single-sheet measurement's cost note.

Full per-bout CSV: `data/processed/gemini_baseline.csv` (gitignored, regenerate with
`uv run python -m scripts.gemini_baseline --n 10`). Raw per-bout readings:
`data/frame_pdf/gemini_baseline/<slug>/gemini_baseline.json` (gitignored).

### Three most common failure modes

**1. Wrong technique at the right moment, not just a spelling miss.** The model reads SOME
action at roughly the right time but names the wrong node — not a fold-fixable spelling
variant, a different technique. `josh-saunders-vs-ricky-luzny`, human `ts=6125 takedown
"Single Leg Takedown"` → model `ts=6120 takedown "Snap Down"`; human `ts=6310 submission "Rear
Naked Choke"` → model `ts=6330 submission "Choke"` (a real-but-different library label, not a
synonym of RNC). This is the largest driver of low recall on `control`/`guard` — the model
sees an action but not which position it resolved into.

**2. Actor swap, partial or whole-bout.** `owen-jones-vs-cammy-donnelly`: human has every
early-guard event owned by Owen Jones (`Guard Pull`, `Open Guard`, …); the model's
`ts=2375 transition "Guard Pull" — Cammy Donnelly` and `ts=2700 "Guard Pull" — Cammy Donnelly`
put the SAME actions on the other athlete. Same defect class the concordance-audit doc already
named for the classic pipeline (`docs/gemini_concordance_audit.md` §Batch 2) — this baseline
shows it survives a fresh zero-shot read on a sheet that already embeds the vocabulary AND an
identity-discrimination instruction.

**3. Over-generation of same-type events that don't correspond to distinct human events, plus
timestamp drift wide enough to slip past a 10s window.** `declan-moody-vs-anton-minenko`: human
records 3 takedown-family events across the bout; the model reports 5 `takedown` events
(`"Snap Down"` three times, `"Take Down"` once, plus the one real
`"Takedown (Back Exposure)"`) — and even that one correct label lands at `ts=6835` against the
human's `ts=6855`, a 20s gap that misses the ±10s tolerance entirely. Zero of this bout's 4
human events matched. The model appears to narrate every frame showing sustained wrestling
as its own event rather than segmenting the actual technique changes.

### Recommendation

Zero-shot precision (0.39) and recall (0.29) are too low to trust unsupervised, and the
`confidence: "high"` field is useless as a filter (constant). But the two things that DO work
well when a match happens — mean ts error 1.1s, actor accuracy 90% — say the failure is
concentrated in WHICH label the model assigns, not WHEN or WHO. That points fine-tuning (or
few-shot examples) at label discrimination specifically: pairs like `Snap Down` vs `Single Leg
Takedown`, `Choke` vs `Rear Naked Choke`, and the guard/control sub-position vocabulary
(recall 0.09–0.13, the two weakest types) rather than at timestamp extraction or actor
identification, which the model is already doing acceptably. The dataset this needs is not
more raw readings — `docs/gemini_concordance_audit.md`'s corpus already has 400+ audited
events — it's audited (label, near-miss) pairs specifically on the confusions in failure mode
1, since that is measurably where the errors concentrate.

## Guidance A/B (2026-09-02)

Pre-registered question: does a Markov next-move hint improve the zero-shot read? Same 10
bouts, same split/seed, same matcher as the baseline above — **A** = whole-sheet one-call read
(`gemini_read_frames.read_frames`, the baseline table above), **B** = page-by-page read with a
per-page Markov guidance block (`gemini_read_frames.read_frames_guided`, `--guidance` on
`scripts/gemini_baseline.py`). Pre-registered verdict: guidance helps if F1 rises **≥ 0.05
without losing recall**.

### Run: 10 bouts, `gemini-3.6-flash`, thinking=high, `--guidance`

| type | support | A pred/TP | A P/R/F1 | B pred/TP | B P/R/F1 |
|---|---|---|---|---|---|
| control | 32 | 12 / 4 | .33 / .13 / .18 | 66 / 8 | .12 / .25 / .16 |
| transition | 13 | 22 / 5 | .23 / .38 / .29 | 6 / 2 | .33 / .15 / .21 |
| guard | 22 | 4 / 2 | .50 / .09 / .15 | 97 / 6 | .06 / .27 / .10 |
| pass | 6 | 8 / 3 | .38 / .50 / .43 | 51 / 1 | .02 / .17 / .04 |
| escape | 4 | 3 / 0 | .00 / .00 / — | 12 / 0 | .00 / .00 / — |
| sweep | 1 | 0 / 0 | — / .00 / — | 3 / 0 | .00 / .00 / — |
| submission | 15 | 10 / 7 | .70 / .47 / .56 | 26 / 8 | .31 / .53 / .39 |
| takedown | 13 | 20 / 10 | .50 / .77 / .61 | 30 / 9 | .30 / .69 / .42 |
| **overall** | **106** | **79 / 31** | **.39 / .29 / .34** | **291 / 34** | **.12 / .32 / .17** |

Other numbers, A vs B: mean `|ts|` error **1.13s → 0.74s** (tighter — guidance's per-page prompt
narrows the reading to that page's own timestamps), actor accuracy **90% → 97%**, `confidence:
"high"` rate **100% → 98%** (still no signal). Cost: A **187,586** tokens / 10 bouts (~18.8k/bout);
B **1,101,760** tokens / 10 bouts (~110k/bout, 615,214 prompt + 435,896 thoughts + 50,650
output) — **~5.9x** A, because guidance trades one call/bout for one call/frame-grid-page
(14–33 pages/bout here), each re-sending the context + vocabulary pages and a fresh thinking
pass. Full per-bout CSV: `data/processed/gemini_baseline_guided.csv` (gitignored, regenerate
with `uv run python -m scripts.gemini_baseline --n 10 --guidance --out-dir
data/frame_pdf/gemini_baseline_guided --csv data/processed/gemini_baseline_guided.csv`). Raw
per-bout readings: `data/frame_pdf/gemini_baseline_guided/<slug>/` (gitignored).

### Three examples where guidance changed the read

**1. Over-generation, driven by the guidance block itself.** `jozef-chen-vs-oliver-taza`: A
reads 8 events across the whole bout; B reads **60** on the same 11 human events, dominated by
an alternating `Guard Pull` / `Guard Pass` / `Open Guard` cycle every 5–10s
(`ts=1525..1795`, actor flipping almost every step) that has no counterpart in the human
sequence at all. The Markov prior over-weights the guard↔pass transition (a real, common
transition in the corpus) and — with no bout-level context across page calls — the model
narrates it as a NEW event on nearly every page rather than recognising one held position.

**2. A real recall gain.** `elijah-dorsey-vs-nicky-ryan`: B matches two human events A missed
entirely — `ts=4645 guard "Butterfly Guard" (Nicky Ryan)` and `ts=4865 guard "X-Guard" (Nicky
Ryan)` — both guard-family positions, the type the corpus-statistics hint is built from
(`analysis/next_moves.py` fits state transitions specifically on guard/control states). This is
the one place the mechanism does what it was meant to.

**3. The same actor-swap defect the whole-sheet baseline already had, undiminished.**
`owen-jones-vs-cammy-donnelly`: human has every early-guard event owned by Owen Jones
(`ts=2385 "Open Guard"`, `ts=2460 "Half Guard"`, …); B's `ts=2390 "Open Guard" — Cammy
Donnelly` and `ts=2405 "Open Guard" — Cammy Donnelly` put the same action on the other athlete
— identical failure class to the baseline's failure mode 2, page-by-page reading and a
guidance hint did nothing to fix it (bout actor accuracy dropped to 50%, the worst of the 10).

### Verdict: guidance REJECTED

F1 fell **0.34 → 0.17** (a 0.17 drop, nowhere near the +0.05 bar) and recall's small gain
(.29 → .32) came entirely from a 3.7x flood of predicted events (79 → 291, support unchanged
at 106) that crushed precision (.39 → .12). Per-bout precision degraded on every type except
`transition`. The page-by-page split removes the whole-bout context that keeps the model from
re-narrating a held position as a fresh event every page, and the guidance block's "likely next
move" framing appears to invite exactly that — proposing a transition the model then reports as
having happened. Combined with ~5.9x the token cost, this is a net loss on every axis except
timestamp tightness and actor accuracy (both already acceptable in A). Do not adopt
page-by-page + Markov guidance as implemented; the next lever for the label-discrimination
problem A already identified is still few-shot/fine-tuning examples on confusable label pairs,
not a next-move prior fed one grid page at a time.

## Next step (not this pass)

Fine-tuning (or few-shot prompt tuning) on frames the model already reads with high confidence
— named here, not attempted. `docs/gemini_concordance_audit.md` has the concordance-QA
procedure this would build on for measuring any improvement.
