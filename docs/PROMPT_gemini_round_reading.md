# Gemini round-reading prompt (video-pro)

The prompt below is loaded verbatim (its own `---`-delimited body only — `load_prompt()` in
`scripts/gemini_read_frames.py` splits on `\n---\n`, so a change here is a change to what gets
sent, nothing to sync by hand) and sent, together with ONE frame sheet built by
`scripts/video_jobs.py` (via `scripts/video_frames.py:build_sheet`, `context=` param), to
`gemini_read_frames.read_frames(sheets, prompt, model, thinking)`.

This is the **video-pro** variant of `docs/PROMPT_gemini_frame_reading.md` — same sheet
mechanics (context page, vocabulary pages, timestamped frame grid), different vocabulary and a
different privacy class. The frame-reading prompt reads a PUBLIC competition bout and names two
real athletes; this one reads a PRIVATE user-recorded training round and never names anyone —
see `analysis/round_analysis.py`'s own docstring and this repo's `CLAUDE.md` "Public vs Private
Data" section. Nothing this prompt's answer produces may reach `data/finetune`, a CV/vision
dataset, the public corpus, an archetype centroid, an athlete's ELO, or the `site/` export.

---

You are reading a frame sheet of ONE grappling training round (no broadcast, no scoreboard,
no commentary — this is a phone recording of two people training). The PDF you received
contains, in order: a context page (round kind, any notes the person recording left, and — if
attached — a reference photo of ONE of the two people), one or more "Allowed labels"
vocabulary pages (the same closed technique vocabulary used everywhere in this project), then
grid pages of video frames. Above every frame its VIDEO-ABSOLUTE timestamp is printed as
"H:MM:SS (NNNNs)".

There is no narration, no scoreboard and no announced winner. Read the frames alone.

Your job: watch the round through these frames and return ONE JSON object — nothing else, no
prose, no markdown fences.

## Actor vocabulary — read this before anything else

There are exactly two people in every frame, and you must never use a name:

- **"you"** — the person shown in the reference photo on the context page. If NO photo was
  attached, "you" is whichever body the context page's notes describe (e.g. kit colour); if
  neither is enough to decide, say so in `bout.identity_discriminator` and use "you"/"partner"
  as your best call — never invent a name, never leave `actor` blank.
- **"partner"** — the other person, always, no matter their belt, gender or who is winning.

## Output shape

    {
     "bout": {
       "round_kind": "round" | "full_session",
       "identity_discriminator": "<how you told the two bodies apart, e.g. 'you = photo, dark blue rashguard; partner = white gi'>",
       "notes": "<anything a reviewer should know>"
     },
     "events": [
       {"ts": <int>, "label": "<vocabulary label>", "actor": "you" | "partner",
        "successful": true|false, "type": "takedown",
        "confidence": "high"|"low", "note": "<short>"},
       ...
     ],
     "resets": [<int>, <int>, ...]
    }

Omit optional fields you cannot support; never write null or empty strings.

## Hard rules (a validator rejects violations)

1. "ts" is the VIDEO-ABSOLUTE second, taken ONLY from the printed stamps — never interpolated
   outside the sampled range. Events sorted by ts.
2. "label" must be copied VERBATIM from the Allowed-labels pages of this same PDF. If you see a
   technique the vocabulary does not list, use the closest listed label and say the real name
   in "note" — or, only if nothing is close, use your own label and add "new_label": true.
3. "actor" is exactly "you" or "partner" — never a name, never null. Ownership convention:
   the event belongs to the athlete whose game it is — a guard event belongs to the GUARD
   PLAYER (bottom), a pass to the PASSER, a takedown to the ATTACKER, a submission to the one
   APPLYING it, an escape to the one ESCAPING, a control to the one CONTROLLING.
4. "type" is one of exactly: control, submission, guard, takedown, pass, transition, sweep,
   escape.
5. "successful": true only when the frames show it worked (takedown completed, pass
   consolidated, submission produced a tap or forced a reset). A visible ATTEMPT that failed or
   was defended is the same event with "successful": false. Required on every event.
6. There is no scoreboard and no "winner" field — never invent points, a score, or a result the
   frames themselves don't show as a submission/tap.
7. "resets" is a separate top-level array of VIDEO-ABSOLUTE seconds, one per moment the pair
   fully separates and restarts from a neutral position (both back on their feet apart, or an
   explicit "reset"/"go again" between reps) — this is what splits the round into sequences
   downstream, so log every one you see, even mid-round.

## Discipline

- Log DISCRETE occurrences, not per-frame states: a mount held across ten frames is ONE
  "control" event at the second it is established. A lost-and-regained position is two.
- 5-second (or coarser) sampling misses things between frames. Only report what these frames
  actually support. An honest 4-event answer beats an invented 15-event one. Use
  "confidence": "low" where you are unsure; prefer omission over fabrication.
- Setup time, water breaks, and anyone walking in/out of frame produce no events.
- Return the JSON object and nothing else.

---

## Status

Not yet run against real user footage (this doc ships with `scripts/video_jobs.py`'s first
version, Fase 3 of the video-pro plan). No measured accuracy numbers here yet — see
`docs/video_frames_gemini.md` for the baseline this variant's parent prompt already has on
public competition footage, and `docs/frame_pdf_reading.md` §"Three most common failure
modes" for the defect classes (label confusion, actor swap) to watch for once real rounds are
read; the reference-photo anchor this variant adds is specifically aimed at the actor-swap
class, which the public-corpus prompt cannot rely on (no reference photo exists for a
competition bout).
