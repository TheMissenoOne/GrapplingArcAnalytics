# ADCC Trials Finals 2023-24 — frame sheets with commentary

56 PDFs covering the whole of
[*Watch Every ADCC Trials Finals From 2023-24*](https://www.youtube.com/watch?v=pwGbW5GZgfc),
8h29m26s of broadcast, one PDF per bout or window. 86 MB in total.

Six are named bouts, taken from the partial index in the video's own transcript header:

| bout | window |
|---|---|
| Elijah Dorsey vs. Nikki Ryan | 1:23:32 – 1:26:13 |
| Dorian Oliver vs. Dominic Mahia | 1:37:45 – 1:40:55 |
| Anna Carolina Vieira vs. Franciele Nascimento | 3:08:08 – 3:29:34 |
| Jasmine Rocha vs. Enriquez | 5:54:44 – 6:00:05 |
| William Tackett vs. Jacob Rodriguez | 6:58:49 – 7:03:43 |
| Andrew Tackett vs. Oliver Taza | 7:11:14 – 7:15:29 |

The index covers six ranges out of eight and a half hours, so the remaining fifty files are
fixed windows filling every gap around those ranges. Nothing in the video is left out, and the
file name carries the window's start in seconds (`pwGbW5GZgfc-5012.pdf`).

## The transcript beside each frame

Each frame carries the commentary spoken during its window, aligned on **video-absolute
seconds** — the same clock the frame stamps and the file names use. A window where nobody spoke
prints `(no narration in this window)` rather than borrowing a neighbouring line.

> **The caption is a guide, not ground truth.** It is an auto-caption of live commentary: the
> commentator misnames athletes, talks about one bout while another is on screen, anticipates
> and speculates, and the text lags the picture. **When the caption and the frame disagree, the
> frame wins.** Use the caption to orient where to look and to break a tie on a label — never
> to record an event you did not see in the frame itself.

Page one of every PDF repeats this, along with the two rules that cost the most when broken:
the scoreboard's name order is not a mat position, and an uncertain event should be reported
with a **coarser** label, never omitted and never guessed.

## Regenerating

These are build output, kept here only because they are the deliverable. Everything needed to
rebuild them is tracked:

```bash
uv run python scripts/frame_pdf.py \
  --manifest data/frame_pdf/trials_2023_24_manifest.json \
  --format pdf --pdf-step 5 --min-free-gb 2
```

`--format pdf` matters: `frames` and `both` keep each window's `clip.mp4`, which across
fifty-six windows fills a disk. The manifest itself comes from
`scripts/build_trials_manifest.py`, which reads `data/frame_pdf/trials_2023_24_transcript.md`.
