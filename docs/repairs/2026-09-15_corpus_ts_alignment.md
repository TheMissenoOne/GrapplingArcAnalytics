# 2026-09-15 — corpus `ts_origin`/`video_start_seconds` alignment defect

Found while reviewing `scripts/dictionary_seed.py`'s first Gemini seed batch: a frame pulled
for `back control` (`dante-leon-vs-pat-shahgholi-2025__5000.jpg`, planned as "5s into the
bout") was the WNO 30 tale-of-the-tape intro card, not the fight. **Read-only measurement
below — nothing in this doc writes to `matches`.** It is a record of what's wrong and how big,
for whoever picks up the actual repair.

## The defect

`matches.video_start_seconds` marks where a bout's SECTION starts inside the uploaded video —
not where the referee says "go". `dante-leon-vs-pat-shahgholi-2025` carries
`video_start_seconds=6746`, `ts_origin='video_absolute'`, and its own `sequence[].ts` values
run 5…397 — small numbers, clearly counted from the BOUT's own start (bout-relative), not from
6746. The `ts_origin` flag said `video_absolute` anyway, so `video_start + ts` was never even
tried; the code trusted the flag and read `ts` directly, landing every frame in the seconds
right after `video_start_seconds` — which is the tale-of-the-tape card, not the bout.

`ts_origin` is written once, by hand or by whichever script inserted the row, and nothing
re-verifies it against the `sequence` it's paired with. It can quietly disagree with the data
it's supposed to describe.

## The fix (code, already in `scripts/dictionary_seed.py`)

`classify_ts_origin(video_start_seconds, event_ts, video_duration)` decides a match's ts
semantics from the EVENT VALUES instead of trusting the stored flag:

- every event `ts` below `video_start_seconds` (itself > 60s, a real offset not noise) →
  `bout_relative`
- every event `ts` at/after `video_start_seconds` → `video_absolute`
- no usable `video_start_seconds` (NULL/0 — the download IS the whole file) and the max event
  `ts` fits inside the video's own duration (`yt-dlp --dump-json`, no download) →
  `absolute_from_zero`
- anything else (no events, mixed evidence, doesn't fit) → `unknown` — skipped, counted,
  never guessed

`scripts/dictionary_seed.py plan` now classifies every match this way and reads the DB's own
`ts_origin` nowhere; `absolute_ts` computes the real second off the evidence-based class.
Unit-tested (`tests/test_dictionary_seed.py`, `classify_ts_origin`/`absolute_ts`/
`ts_class_matches_flag`), no network in the tests.

## Measured, corpus-wide (287 `matches.status='final' and video_url is not null`)

### Population A — matches that ever carried an explicit `ts_origin` flag: 20 of 287

All 20 are flagged `video_absolute`; **zero** carry `bout_relative` — nobody ever wrote that
flag, so every candidate the FIRST `dictionary_seed` batch planned necessarily came from one of
these 20 (the old code required a non-null flag to produce a `ts` at all).

Evidence-based reclassification of those 20:

| source_batch | n | flag=video_absolute → evidence=video_absolute (agrees) | → bout_relative (**confirmed wrong flag**) | → unknown (unverifiable from events alone) |
|---|---|---|---|---|
| WNO30 | 2 | 0 | **2** | 0 |
| (none) | 6 | 2 | 0 | 4 |
| IBJJF2021-NoGi | 2 | 0 | 0 | 2 |
| CrevarSingles | 2 | 0 | 0 | 2 |
| CBJJEBJJPaulista2026-Frames | 2 | 0 | 0 | 2 |
| YaraSoares | 2 | 0 | 0 | 2 |
| UFCBJJ4 | 1 | 0 | 0 | 1 |
| AnaVieiraWNO | 1 | 0 | 0 | 1 |
| WNO31 | 1 | 0 | 0 | 1 |
| Polaris36-Frames | 1 | 0 | 0 | 1 |
| **Total** | **20** | **2** | **2** | **16** |

Only WNO30's 2 matches are a **confirmed** wrong flag (this is the tale-of-the-tape case,
reproduced exactly). The other 16 are not confirmed wrong — per-match evidence just can't
vouch for them either (mixed event values, or a `video_start_seconds` under the 60s noise
floor) — a human spot-check or a duration probe would settle each one individually; this
sweep only says "don't trust these blind," not "these are wrong."

### Population B — matches with NO `ts_origin` flag at all: 267 of 287 (93%)

These were **never used as a candidate before this fix** — the old code needed a truthy flag
to compute anything. Evidence-based reclassification (no duration probe run corpus-wide, to
keep this a read-only DB sweep — `dictionary_seed.py plan` probes lazily per-match when it
actually needs one):

| evidence class | count | % of 267 |
|---|---|---|
| `video_absolute` | 178 | 67% |
| `bout_relative` | 2 | 1% |
| `unknown` (still, even with evidence) | 87 | 33% |

**180 of 267 (67%) are now usable** with zero duration probing — the real headline of this
fix is not "2 wrong flags corrected," it's "267 matches that were silently invisible to any
ts-dependent tool are now 67% recoverable." A future batch should re-run `plan` against the
full corpus (not the capped/prioritized first batch below) to find out how large the newly
unlocked candidate pool actually is per curated technique.

## Applied to the actual first batch (96 candidates, `scripts/dictionary_seed.py`)

Re-running the SAME 96 candidates' ts through `classify_ts_origin` (per
`ts_class_matches_flag`, comparing the evidence-based class against the row's own match's DB
flag):

- **27 of 96 (28%)** were on a match whose evidence-based class disagreed with (or couldn't
  confirm) the stored `ts_origin='video_absolute'` flag — of these, the WNO30 matches
  contribute the confirmed-wrong cases; the rest are the same "unverifiable" 16-match
  population as above, reached via whichever of their events happened to be sampled into the
  batch.
- **5 of 96** dropped entirely (`klass='unknown'` for their match): `Armbar`
  (`kendall-reusing-vs-anabel-lopez-2026`), `Takedown`/`Top Control`
  (`bruno-fernandes-rocha-vs-bryan-silva-2026`), `Side Control`/`Top Control`
  (`keven-julio-vs-bruno-fernandes-rocha-2026`).
- **91 of 96** carried forward into the realigned batch (`plan.jsonl`), each with its `ts`
  recomputed from the evidence-based class rather than the raw flag.

## Extraction + the alignment window

`extract` now pulls a 9-frame `±30s` window (640×360, offsets
`-30,-20,-10,-5,0,+5,+10,+20,+30`) around each candidate's second, plus the full-res centre +
`±2s` neighbours — `docs/vision_dataset.md`'s single-frame assumption doesn't hold even after
the reclassification fix (a per-match class is still one number for potentially many events;
one mis-logged `ts` inside an otherwise-good match slips through). Stage A (not blind) then
picks which of the 9 actually shows the label before the real blind read (stage B) runs.

Of the 91 realigned candidates: 69 extracted cleanly (11 of 13 matches downloaded);
**2 matches (22 candidates) failed to download** — `philippe-costa-vs-achilles-rocha-2025`
and `dante-leon-vs-pat-shahgholi-2025` (yt-dlp/ffmpeg `exit 222`, "Invalid argument", on the
widened `±35s` `--download-sections` span) — unrelated to the ts fix, recorded as a known
extraction failure, not guessed around. `ask` marks those 22 candidates
`align_reason=extraction_failed` rather than spending a Gemini call on an empty prompt.

## Status at end of session — blocked on the daily Gemini quota, not on the fix

Stage A ran for all 91 realigned candidates (69 real reads + 22 auto-marked
`extraction_failed`); several genuinely picked a non-zero offset (`leg lock -10s`,
`weave pass -30s`, `mount +10s`, `heel hook +10s`/`-5s`, `inverted de la riva guard +10s`,
`cross ashi -5s`, `de la riva guard -5s`) — direct, observed confirmation that the corpus
second and the labelled moment are not always the same frame, independent of the
`ts_origin`-flag question above. Stage B (the blind read) completed 31 of the ~69 askable
candidates before hitting `generativelanguage.googleapis.com`'s **250 requests/day** quota for
`gemini-3.1-pro` (`RESOURCE_EXHAUSTED`, retry-after ≈6h51m from 2026-09-15 13:38 local) — this
session's own earlier batches (96 + 91 stage-A + partial stage-B) used up the day's allowance.

That run also surfaced a real robustness bug, now fixed: `run_ask` had no per-call error
handling, so the 429 crashed the whole batch and `seed.jsonl` was never written — losing the
31 already-graded answers along with everything else, since `write_jsonl` only runs once, at
the end. `_safe_call` (scripts/dictionary_seed.py) now wraps every Gemini call in both stages;
an error degrades that ONE candidate (`review_confidence: low`, reason = the error text) and
the batch keeps going. Tested (`test_run_ask_survives_a_quota_error_and_keeps_grading_the_rest`).

**Next session:** `uv run python -m scripts.dictionary_seed ask` once the quota window has
passed — `plan.jsonl` (91 rows, realigned) and all extracted frames are already on disk, and
the run will no longer lose partial progress to a mid-batch error. `report` regenerates
`REPORT.md` with the misalignment/offset-histogram/before-after sections already wired
(`data/finetune/audit/gemini_seed/seed_before_alignment.jsonl` holds the ORIGINAL
single-frame, uncorrected-ts batch's own graded answers for the before/after comparison).
