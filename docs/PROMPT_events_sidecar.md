# The refiner prompt — transcript → events sidecar

Copy everything between the `=== PROMPT ===` markers into ChatGPT (or DeepSeek/Claude), then paste
the transcript underneath it. What comes back is saved verbatim as
`scripts/dumps/<event>_events.json` and spliced in with `scripts/apply_events.py` (step 4 of
`ingestion_pipeline.md`).

**This file is the spec, not a summary of one.** It replaces the event rules previously split
across `docs/deepseek/E-refine-events.md` and `docs/deepseek/F-transcript-to-dump.md`, which
disagreed with each other and with the code — see "Why this replaced two specs" at the bottom.

Two things to fill in before pasting:

1. `<BOUT KEYS>` — the exact keys the sidecar must use, one per bout you want refined. Get them
   from the dump `batch_queue` already wrote:
   ```bash
   uv run python -c "
   import scripts.dumps.<slug>_data as d
   for k, v in d.RAW[0].items():
       print(f'{k[0]}|{v[\"opponent\"]}|{k[1]}')
   "
   ```
   (`RAW` is a list holding one dict — that is the shape `batch_queue` writes.)
2. `<TECHNIQUE LABELS>` — the allowed label vocabulary, so the model does not invent labels that
   fall out of the shared graph:
   ```bash
   uv run python -c "import json;print(', '.join(sorted(e['en'] for e in json.load(open('analysis/data/technique_library.json')))))"
   ```
   If that is too long for one message, paste the labels for the types the bout actually involves.

---

```
=== PROMPT ===
You are refining a grappling match transcript into structured events. Return ONLY a JSON
object. No prose, no markdown fences, no commentary.

## Output shape

{
  "<bout key>": [
    {"label": "Closed Guard", "type": "guard", "actor": "Full Name", "ts": 3185},
    {"label": "Triangle Choke", "type": "submission", "actor": "Full Name", "ts": 3402,
     "successful": false},
    {"label": "Triangle Choke", "type": "submission", "actor": "Full Name", "ts": 3455,
     "successful": true}
  ]
}

Use EXACTLY these bout keys, verbatim, including capitalisation and spacing:

<BOUT KEYS>

A bout with nothing reliable to report gets an empty list. Never omit a key, never invent one.

## ts — the single most common mistake

`ts` is an INTEGER number of SECONDS, counted from the START OF THE VIDEO, not from the start
of the bout. A bout that begins at 53:05 and sees a guard pull thirty seconds in gets
ts = 3215, not 30.

The site subtracts the bout's start offset when it renders, so a bout-relative value is
subtracted twice and every video link lands in the wrong place.

Convert every timestamp you read in the transcript directly: "53:05" -> 3185, "1:12:44" -> 4364.

## Which lines may become events

Ask of each line: is the commentator describing something visibly happening RIGHT NOW, between
THESE two athletes, in THIS bout? Only then may it become an event.

Include on: gets, takes, passes, sweeps, pulls, recovers, escapes, locks, finishes, taps,
enters, lands, secures, completes.

Exclude entirely: predictions ("he may look for a double leg"), coaching ("he should stand"),
hypotheticals ("if he posts, the triangle is there"), general instruction ("closed guard is
strong for leg entries"), tendencies ("he's known for back attacks"), history ("he took his
back in their last match"), biography, strategy talk, judging speculation, crowd and fatigue
commentary, and hedged guesses ("it looks like he might be thinking about a kimura").

Exclude on: could, might, may, should, would, probably, likely, expect, needs to, wants to,
looking to, thinking about, usually, normally, known for, likes to.

One sentence can hold both. "He's looking to take the back, and now he secures both hooks"
yields ONE event: the completed back take.

Emit only when a clear statement, several adjacent lines together, or an official announcement
confirms it. Never from an isolated technique name, an unresolved pronoun, a replay of
something you did not already catch live, or narration bleeding in from another bout.

## Attempts

A technique that was executed and failed keeps ITS OWN type and carries "successful": false.
It is never re-typed as "transition".

  {"label": "Guard Pass", "type": "pass", "successful": false, ...}
  {"label": "Double Leg Takedown", "type": "takedown", "successful": false, ...}

A failed guillotine is still part of that athlete's submission game. Re-typing it hides it,
and a label whose type disagrees with the library entry is rejected on import and vanishes
from the shared graph.

Do not write "Attempt" into the label — the flag carries that.

"Looking for" is not an attempt. It becomes one once materially established: one hook in is a
Back Take attempt; both hooks in is Back Control.

Use "transition" only when the action's result is genuinely no other category: arm drag, duck
under, snap down, inversion, leg-entanglement entry, takedown defence.

## actor — whose game is it

The actor is the athlete whose GAME the node belongs to, not whoever is winning the exchange:

  guard      -> the guard player (bottom)
  sweep      -> the sweeper (the one who was on the bottom)
  pass       -> the passer
  control    -> the one holding the control
  escape     -> the one escaping
  takedown   -> the one taking down
  submission -> the one attacking

Use the athlete's full name exactly as it appears in the bout key. Never a nickname, never
"the top fighter", never a pronoun. If you cannot tell which athlete it was, drop the event —
an unresolvable actor is silently discarded on import, so a guess is worse than a gap.

## type

Exactly one of: guard, control, pass, sweep, takedown, submission, escape, transition

## label

Use one of these strings verbatim where one fits:

<TECHNIQUE LABELS>

Prefer the short reusable label over the commentator's phrasing, and do not over-specify past
what was actually said: "he passes the guard" is "Guard Pass", not a named variation. Normalise
synonyms (RNC -> Rear Naked Choke, back mount -> Back Control, single-leg -> Single Leg
Takedown). Never emit a placeholder like {"label": "Match"}.

## One event per occurrence

A position held across several lines is ONE event, timestamped where it was first clearly
established. Re-emit only if it was clearly lost and then regained. A replay refines an event
you already caught live and keeps the LIVE timestamp; if you cannot place that live moment
confidently, emit nothing.

## Before you answer, check

- valid JSON, top level an object, every provided bout key present
- every "actor" is one of that bout's two athletes, spelled as in the key
- every "type" is from the eight allowed
- every "ts" is an integer, in seconds, from the start of the VIDEO
- events sorted by ts within each bout
- no two events identical in (label, ts, type, actor, successful)
- nothing hedged, predicted, remembered or coached made it in
=== PROMPT ===
```

---

## After you get the JSON back

```bash
# save it, then splice it into the dump
uv run python -m scripts.apply_events <slug>_data scripts/dumps/<event>_events.json
uv run python -m scripts.apply_events --check          # round-trip self-test, writes nothing
```

Then continue at step 5 of `ingestion_pipeline.md`.

Read the dry-run log for `dropping event with unknown actor <name>` — that is an actor string
that matched neither athlete, and the event was silently discarded. Fix the sidecar and re-splice;
do not just re-import.

## Why this replaced two specs

Both former specs were wrong in ways that broke real output, and they contradicted each other, so
neither could be trusted alone. Resolved against the code and against the 6,367 events already in
`scripts/dumps/*_events.json` (2026-08-13):

| Question | Answer, and how it was settled |
|---|---|
| Are timestamps bout-relative or video-absolute? | **Video-absolute.** `export/site_data.py:852` does `max(0, ts - start)` under the comment "Convert broadcast-absolute ts → match-relative". `E-refine-events.md`'s "seconds from bout start" would double-subtract. The live sidecars agree: `leandro_lo_events.json` runs ts 3185–21686. |
| Does a failed attempt get `transition`, or its own type? | **Its own type.** `F-transcript-to-dump.md`'s header claims it re-types to `transition`, but its own body (§6) says the opposite, and the data is unambiguous: `successful: false` appears on submission (310), control (283), takedown (138), guard (119), pass (84), escape (20) and sweep (14). `transition` is only 38 events total. |
| Two-part or three-part sidecar keys? | **Three-part** (`name\|opponent\|year`). `apply_events.py` accepts a two-part key as a fallback, but all 388 keys in the repo are three-part. |

`E-refine-events.md` and `F-transcript-to-dump.md` are kept for their worked examples and their
history, both now carrying a header pointing here. Do not refine from them.
