# The refiner prompt — transcript → enriched events sidecar

Copy everything between the `=== PROMPT ===` markers into ChatGPT (or DeepSeek/Claude), then paste
the transcript underneath it. Save the returned JSON as `scripts/dumps/<event>_events.json` and
splice it with `scripts/apply_events.py` (step 4 of `ingestion_pipeline.md`).

**This file is the spec, not a summary.** It preserves the strict event rules that replaced
`docs/deepseek/E-refine-events.md` and `docs/deepseek/F-transcript-to-dump.md`, and adds the
ruleset-aware scouting envelope.

Fill three inputs before pasting:

1. `<BOUT CONTEXT>` — exact bout keys plus externally verified context:

   ```text
   Name|Opponent|2026 | ruleset_id=cji-2025-women | uniform=no_gi | start=53:05
   ```

   The key comes from the preliminary dump. Run step 2 first, fix participant names in the dump,
   then read keys back verbatim:

   ```bash
   uv run python -c "
   import scripts.dumps.<slug>_data as d
   for block in d.RAW:
       for k, v in block.items():
           print(f'{k[0]}|{v[\"opponent\"]}|{k[1]}')
   "
   ```

   `apply_events.py` compares keys by plain string equality. Do not tidy or fuzzy-match them.
   `ruleset_id`, `uniform`, and `start` are supplied facts. Never ask the model to infer them.
2. `<TECHNIQUE LABELS>` — allowed vocabulary:

   ```bash
   uv run python -c "import json;print(', '.join(sorted(e['en'] for e in json.load(open('analysis/data/technique_library.json')))))"
   ```
3. The transcript, including its original timestamps.

---

```text
=== PROMPT ===
You are refining a grappling match transcript into a structured scouting sidecar. Return ONLY
one valid JSON object. No prose, markdown fences, or commentary.

## Input bout context

Use these exact keys and supplied attributes. Do not infer or change ruleset_id, uniform, start,
participants, year, or key spelling:

<BOUT CONTEXT>

## Output shape

{
  "<bout key>": {
    "events": [
      {"label": "Closed Guard", "type": "guard", "actor": "Full Name", "ts": 3185},
      {"label": "Triangle Choke", "type": "submission", "actor": "Full Name", "ts": 3402,
       "successful": false,
       "rule_evidence": {"position_final": "submission escaped"}}
    ],
    "scouting_observations": [
      {"actor": "Full Name", "kind": "initiative", "value": "waits for the first attack",
       "phase": "regular", "ts": 3210}
    ],
    "timing": {"end_ts": 3785, "overtime_start_ts": 3605},
    "adjudication": {
      "status": "verified",
      "kind": "point_total",
      "result": {
        "positive": {"Full Name": 2, "Opponent Name": 0},
        "negative": {"Full Name": 0, "Opponent Name": 1},
        "advantages": {"Full Name": 0, "Opponent Name": 0},
        "penalties": {"Full Name": 0, "Opponent Name": 1}
      }
    }
  }
}

Every supplied bout key must appear exactly once. Never invent a key. Empty evidence is:

{
  "events": [],
  "scouting_observations": [],
  "timing": {},
  "adjudication": {"status": "unknown", "kind": "none"}
}

Allowed top-level bout fields are exactly `events`, `scouting_observations`, `timing`, and
`adjudication`.

## Timestamps: video-absolute only

Every `ts`, `end_ts`, and `overtime_start_ts` is an INTEGER number of seconds counted from the
START OF THE VIDEO, never from the bout start. A bout beginning at 53:05 with a guard pull thirty
seconds later uses `ts = 3215`, not 30. Convert transcript timestamps directly:
`53:05 -> 3185`, `1:12:44 -> 4364`.

Do not subtract the supplied bout start. The deterministic consumer derives bout-relative timing
only when a parseable start exists; otherwise it preserves the absolute value and marks relative
timing unavailable.

## Transcript noise and identity

The transcript is a YouTube auto-caption dump: timestamp, duration line, commentary. Duration
lines, interface text, music, applause, biography, ads, and navigation are noise. Auto-captions
mangle names; map a clearly identifiable mention to one of the two bout-key names. Never invent a
third athlete. An unresolved actor means omit the item.

## Events: observed actions only

Ask: is the commentator describing something visibly happening RIGHT NOW between THESE athletes
in THIS bout? Include clear completed or materially attempted actions: gets, takes, passes, sweeps,
pulls, recovers, escapes, locks, finishes, taps, enters, lands, secures, completes.

Anticipated progressions are NOT events. Exclude:

- “can/could/might/may/should/would”, “looking to”, “hunting”, “setting up”, “working toward”;
- coaching, conditional futures, teaching, tendencies, reputation, history, judging speculation;
- isolated technique names, unresolved pronouns, crowd/fatigue commentary, or replay-only guesses.

A named possible next position is not a reached position. “Looking to take the back, and now she
secures both hooks” yields only the completed back-control event. Ambiguous landing means omit it.
A missing event reduces coverage; an invented event corrupts the athlete graph.

## Attempts and successful

An executed failed technique keeps its own type and has `successful: false`; never re-type it as
`transition` and never put “Attempt” in the label. “Looking for” is not an attempt. Use
`successful: true` only when completion is explicit. Omit `successful` when outcome is unknown.

Use `transition` only when no other category describes the result: arm drag, duck under, snap
down, inversion, leg-entanglement entry, takedown defence.

## actor: owner of the game node

The actor is the athlete whose GAME the node belongs to:

- guard → guard player (bottom)
- sweep → sweeper
- pass → passer
- control → controller
- escape → escaping athlete
- takedown → athlete taking down
- submission → attacker

Use the exact full name from the bout key. Never nickname, pronoun, or “top fighter”.

## type and label

`type` is exactly one of: guard, control, pass, sweep, takedown, submission, escape, transition.

Use an exact label from this vocabulary where one fits:

<TECHNIQUE LABELS>

Prefer reusable labels. Do not over-specify beyond the evidence. Normalize clear synonyms
(RNC → Rear Naked Choke, back mount → Back Control, single-leg → Single Leg Takedown). Never emit
a placeholder such as `Match`.

**One event = one state OR one action. Never both in a single label.** Do not emit a label
shaped like "A to B" or "X / Y" — those pack a state and an action (or two actions) into one
node, and the ontology has no way to split them back apart later (`docs/taxonomy/
04_ONTOLOGIA_CANONICA.md`). "Guard Pass to Mount" is two events: `pass`/`Guard Pass`, then the
`control`/`Mount` state it lands in. "Escape to Turtle" is `escape`/`Escape`, then (only if the
resulting position is actually visible, not implied) `control`/`Turtle Position`. If the
commentary only supports the action and not a clear landing position, emit the action alone —
do not guess the landing state.

Whether an athlete is on **top or bottom** of a position is metadata about THAT event, never a
second name for it. Write "Half Guard" (the state), not "Top Half Guard" — the actor field
already says whose game the node belongs to, and the ownership table above (guard → bottom,
control → top-ish, …) already carries the orientation for the common cases. Do not invent
parenthetical/prefixed perspective variants ("… (Top)", "… (Bottom)", "Top …") of a state that
already has a plain entry in the vocabulary.

## One event per occurrence

A position held across several lines is one event at the first clear establishment. Re-emit only
after it was clearly lost and regained. A replay may refine an event already caught live but keeps
the LIVE timestamp. If the live moment cannot be placed confidently, omit it.

**Log the state an exchange STARTS from, not only the action that ends it.** Measured
under-registration: Back Control alone is 44% of every position event in the corpus, because a
transcript narrates "she takes the back and locks in the choke" and only the action (the choke)
gets logged — the position that made the choke possible never does. When the commentary
establishes a position before describing what happens from it (a control, a guard, a pin), emit
that position as its own event FIRST, at the moment it is established, even if the athlete then
immediately attacks from it. Do not fold "arrived at a position" into "did something from it".

## scouting_observations

Allowed `kind`: `posture`, `initiative`, `feint_reaction`, `setup`. Add only direct, current-bout
evidence. `value` must be short, factual, and non-evaluative. `phase` is `regular`, `overtime`, or
an explicitly supplied ruleset phase. Never infer overtime from tension, commentary, or elapsed
time. Do not turn reputation or advice into an observation.

## rule_evidence

Optional. Add only concrete visible or officially announced qualifiers relevant to later ruleset
projection, such as `stabilized`, `position_final`, `initiative`, or
`submission_danger_exit`. Describe evidence; never add points or eligibility. If unclear, omit the
whole field. The downstream system, not this model, maps evidence onto a verified ruleset snapshot.

## timing

Add `end_ts` or `overtime_start_ts` only when clearly announced, displayed, or supplied. Both are
video-absolute seconds. Omit unknown keys. Never derive them from a ruleset duration.

## adjudication: official/native result only

Record adjudication only from a clearly visible official scoreboard/card or explicit official
announcement. Never infer points, advantages, penalties, negatives, cards, a winner, or a score
from technique events.

- `status`: `verified`, `partial`, or `unknown`.
- `kind`: `point_total`, `round_cards`, or `none`.
- `point_total.result`: keep `positive`, `negative`, `advantages`, and `penalties` as distinct
  athlete-keyed maps. Do not net or total them.
- `round_cards.result`: keep native `rounds`/cards. Do not convert 10–9 cards to points.
- No reliable official evidence: `{ "status": "unknown", "kind": "none" }`, without `result`.

Do not use the supplied ruleset_id to manufacture an official score. Potential ruleset eligibility
belongs only in `rule_evidence`; official adjudication belongs only here.

## Final check

- valid JSON; every supplied key present; no invented fields or keys
- every actor is one of that key's two athletes, exactly spelled
- event types and observation kinds use the allowed sets
- all timestamps are integer, sorted within each array, video-absolute
- no duplicate `(label, ts, type, actor, successful)` event
- no hedged, predicted, historical, remembered, or coached statement became evidence
- no inferred ruleset, uniform, official points, cards, advantages, penalties, or timing
=== PROMPT ===
```

---

## After receiving JSON

```bash
uv run python -m scripts.apply_events <slug>_data scripts/dumps/<event>_events.json
uv run python -m scripts.apply_events --check
```

The splicer accepts the enriched contract above and the legacy `key -> [events]` shape. Enriched
sidecars patch only the four allowed fields. Unknown fields fail closed. Only matched bouts lose
their `pbp`; partial sidecars leave other bouts refinable.

Read unmatched-key and unknown-actor warnings. Fix the sidecar or preliminary dump, then re-splice.
Do not repair identity after import.

## Why this remains strict

- Timestamps are video-absolute because site export subtracts the bout start.
- Failed attempts keep their native type because import validates type against the library.
- Three-part keys (`name|opponent|year`) are canonical; two-part keys remain a legacy fallback.
- Actor means node ownership, not who appears to be winning.
- Ruleset projections are deterministic and separate from official native adjudication.
