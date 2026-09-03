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
<date>) — not yet human-reviewed"` — the same provenance convention
`frame_answer_import.py` uses, so a reviewed/unreviewed answer is distinguishable the same way
regardless of which script produced it. The raw response text is also saved next to the answer
(`gemini_raw.json`).

No `GEMINI_API_KEY` in the environment (checked via `os.environ`, `.env` loaded first) forces
`--dry-run` automatically: prints the resolved prompt and the file list it would have sent, and
still writes a correctly-shaped, empty `events.json` (`source` says why) so a caller does not
need two code paths depending on whether a key was present.

**Known gap, not fixed here:** the prompt (`PROMPT_gemini_frame_reading.md`) describes a sheet
that includes an "Allowed labels" vocabulary section — the real trials/broadcast sheets
`frame_pdf.py` renders always carry one. `video_frames.py`'s sheet does not embed the node
library (out of scope for this test), so Gemini reads this sheet against its own judgement of
technique names rather than the closed vocabulary. Fine for testing the extraction+reading
loop; a production run over this footage would want the library pages added to
`build_sheet()` the same way `frame_pdf.py`'s `draw_library_pages` does.

## Cost (rough)

Gemini's image tokenization scales with resolution, not bytes — a page raster around 1024px on
its long side is ~256-1300 tokens depending on tiling. A 3-page, 9-frame sheet PDF is a handful
of embedded JPEGs plus the context page's text, well under 5k input tokens; `gemini-2.5-flash`
pricing is per-million-token and this is a rounding error per bout. The real cost driver at
scale is bout COUNT, not sheet size — 100 bouts is still under a few hundred thousand tokens.

## Next step (not this pass)

Fine-tuning (or few-shot prompt tuning) on frames the model already reads with high confidence
— named here, not attempted. `docs/gemini_concordance_audit.md` has the concordance-QA
procedure this would build on for measuring any improvement.
