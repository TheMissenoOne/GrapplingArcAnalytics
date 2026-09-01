# Gemini frame-reading prompt (canonical)

The prompt below is pasted into Gemini (AI Studio) together with ONE bout PDF from
`data/frame_pdf/trials_2023_24/` (or any frame sheet rendered by `scripts/frame_pdf.py`).
One PDF per call. The response feeds `scripts/gemini_normalize.py` (step 3 of
`docs/gemini_concordance_audit.md`). It mirrors the `scripts/frame_answer.py` contract —
keep the two in sync: a schema change there is a prompt change here, same push.

---

You are reading a frame sheet of ONE grappling bout (ADCC ruleset, no-gi). The PDF you
received contains, in order: a context page (video URL, title, time range covered, the two
athletes when named), one or more "Allowed labels" vocabulary pages (376 techniques grouped
by kind), then grid pages of video frames. Above every frame its VIDEO-ABSOLUTE timestamp is
printed as "H:MM:SS (NNNNs)". Below every frame is the commentary narration transcribed in
that frame's 5-second window — "(no narration in this window)" means silence, not missing
data.

Your job: watch the bout through these frames, use the narration to orient yourself, and
return ONE JSON object — nothing else, no prose, no markdown fences.

## Output shape

    {
     "bout": {
       "athlete_a": "<full name>",            // required
       "athlete_b": "<full name>",            // required
       "event": "<event name if known>",
       "year": <int>,
       "winner": "<full name>",               // only if the outcome is shown or narrated
       "win_type": "SUBMISSION" | "POINTS" | "DECISION" | "DRAW",
       "bout_start_seconds": <int>,           // video-absolute second the match actually starts
       "bout_end_seconds": <int>,             // video-absolute second it ends (tap / buzzer / hand raise)
       "identity_discriminator": "<how you told the two bodies apart, e.g. 'A in black rashguard, B in white with blue trim'>",
       "final_score": {"<name>": <int>, "<name>": <int>},   // MAP, never a string like "6-0"
       "advantages": {"<name>": <int>},       // same map rule
       "penalties": "<free text, e.g. 'Silva ~t21700 (1)'>",   // bout level, NEVER as events
       "notes": "<anything a reviewer should know>"
     },
     "events": [
       {"ts": <int>, "label": "<vocabulary label>", "actor": "<full name>",
        "successful": true|false, "type": "takedown",
        "points": <int>, "confidence": "high"|"low", "note": "<short>"},
       ...
     ]
    }

Omit optional fields you cannot support; never write null or empty strings.

## Hard rules (a validator rejects violations)

1. "ts" is the VIDEO-ABSOLUTE second, taken ONLY from the printed stamps — never a
   bout-relative time, never interpolated outside the sampled range. Events sorted by ts.
2. "label" must be copied VERBATIM from the Allowed-labels pages of this same PDF. If you
   see a technique the vocabulary does not list, use the closest listed label and say the
   real name in "note" — or, only if nothing is close, use your own label and add
   "new_label": true.
3. "actor" is exactly "athlete_a" or "athlete_b"'s name. Ownership convention: the event
   belongs to the athlete whose game it is — a guard event belongs to the GUARD PLAYER
   (bottom), a pass to the PASSER, a takedown to the ATTACKER, a submission to the one
   APPLYING it, an escape to the one ESCAPING, a control to the one CONTROLLING.
4. "type" is one of exactly: control, submission, guard, takedown, pass, transition,
   sweep, escape.
5. "successful": true only when the frames or narration show it worked (takedown completed,
   pass consolidated, submission produced a tap). A visible ATTEMPT that failed or was
   defended is the same event with "successful": false. This field is required on every event.
6. "points": include ONLY what the on-screen scoreboard or the narration explicitly awards.
   If you cannot tell, OMIT the field entirely — never write 0 to mean "unknown"; 0 is
   reserved for "the scoreboard awarded nothing" and should also just be omitted.
7. "final_score"/"advantages": name→points maps, never strings — a string scoreline has no
   declared orientation and has attributed wins to the loser before.
8. Penalties are scoreboard facts, not techniques: record them in bout.penalties free text,
   never in "events".

## How to combine frames and narration

- FRAMES decide positions and techniques (what configuration the bodies are in).
- NARRATION decides identity and outcome (who the commentator names, scoring calls,
  "he taps!", decision announcements). When they conflict on WHO, trust the narration name
  + your identity_discriminator; when they conflict on WHAT position, trust the frames.
- The caption under a frame covers that frame's 5s window; the spoken content may lag the
  action by a few seconds — prefer the frame's evidence for WHEN.
- Silent windows are frames-only: read them, but drop to "confidence": "low" if identity
  is uncertain there.

## Discipline

- Log DISCRETE occurrences, not per-frame states: a mount held across ten frames is ONE
  "control" event at the second it is established. A lost-and-regained position is two.
- 5-second sampling misses things. Only report what these frames + narration actually
  support. An honest 6-event answer beats an invented 20-event one. Use "confidence":
  "low" where you are unsure; prefer omission over fabrication.
- Walkouts, replays, podium shots and crowd frames produce no events.
- Return the JSON object and nothing else.

---

## Measured performance (first batch, 2026-08-25)

41 bouts / 401 events read by Gemini over the ADCC Trials 2023-24 set. Concordance audit
(6 independent vision passes, protocol in `docs/gemini_concordance_audit.md`): **397 kept
(93% concordant as delivered, 99% usable after anchored corrections), 22 corrected, 4
dropped**. Recurring defect classes to watch: whole-bout identity swaps when both kits are
dark and there is no commentary (2 of 41 bouts — caught by the published-winner check);
actor flipped on guard/pass exchanges; ts a few frames late relative to the scoreboard
change; ASCII hyphens where the vocabulary carries U+2011 (auto-snapped by the normalizer).

### Bruno Rocha batch (2026-09-01) — the low-water mark, and why

4 bouts / 43 events read over `data/frame_pdf/bruno_rocha/` (four single-bout FloGrappling
uploads). Audit: **15 kept (35%), 9 of them corrected, 28 dropped**. Per bout: FPJJ Circuito
Paulista 6/7, CBJJE vs Bryan Silva 3/9, CBJJE vs Keven Julio 5/14, CBJJ Brasileiro No-Gi 1/13.

The spread is the finding, and it tracks ONE variable — whether the broadcast shows points:

- The **FPJJ** bout has a full gi scoreboard (points, vantagens, running clock) and a
  `VENCEDOR` card. Every claim could be checked against a score change, so 6 of 7 survived and
  five of those only needed their `ts` snapped onto the scoreboard, 6–13 s later than read.
- The **CBJJ Brasileiro** bout has a scoreboard but is filmed from across the whole hall. The
  three 4-point awards are certain and their owner is certain; the POSITION behind two of them
  is not, and `+4` is mount *or* back control. Both were dropped rather than guessed — the
  fact is preserved in the answer's `notes`, where it cannot become a node.
- The two **CBJJE** bouts have a lower third with names and a clock and **no points column at
  all**, so nothing bounds a positional claim. That is where the drops concentrate.

Two defect classes from batch 1 recurred, one of them worse:

- **Whole-bout identity swap**, again justified by the graphic: Gemini bound "Keven Julio" to
  the athlete with the blue ankle bands "where the scoreboard's blue bar aligns with his ankle
  bands" — the exact inference the prompt forbids, and here demonstrably wrong (the same
  broadcast puts the same athlete on the blue side in one bout and the red side in the other).
  New wrinkle: the swap was **not uniform**. Gemini's actor names came out inverted on the
  mid-bout controls and *correct* on the finish, apparently because the finish was assigned
  from the published winner. So a blanket inversion is NOT a safe repair — every actor has to
  be re-derived from the frames.
- **`ts` early rather than late**: in the scored bout every timestamp sat 6–13 s BEFORE its
  score change, the mirror of batch 1's late reads. Snap to the scoreboard either way.

What made the identity call decidable was cheap and reusable: two of the four bouts are the
same athlete at the same event on the same day, so the body that appears in BOTH videos must
be the athlete both bouts have in common — kit, tape and tattoos matched frame to frame, with
no reliance on any graphic. Worth reaching for whenever a batch contains two bouts of one
athlete.
