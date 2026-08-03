# Transcript TXT → structured Python dump

Authoritative spec for turning a noisy timestamped match transcript into the dict literal
`scripts/convert_dump.py` consumes. Supplied by the maintainer 2026-08-03.

## Reconciliation with E-refine-events.md

Two points where this spec and `E-refine-events.md` disagree — resolved against the code:

1. **Timestamps are broadcast-absolute, not bout-relative.** This spec is correct.
   `export/site_data.py:784` converts stored ts → match-relative by subtracting the bout
   start (`"Convert broadcast-absolute ts → match-relative"`), so what lands in the DB must
   be the position in the full video. `E-refine-events.md`'s "integer seconds from bout
   start" is stale — do not follow it.
   `dump_import.py:49` accepts either `ts` (int) or `timestamp` ("M:SS") and parses both.

2. **Attempt typing.** This spec types an incomplete pass/takedown as `transition`
   (§15) and keeps `successful` for submissions only (§14); `E-refine-events.md` uses the
   technique's own type plus `successful: False` for any category.
   Consequence to know either way: `clean_label` rejects a label whose event type
   disagrees with the library entry, so "Guard Pass Attempt" typed `transition` does not
   match the `pass`-typed library entry and drops out of the shared graph. Whichever
   convention is chosen has to be reflected in `analysis/data/technique_library.json`.

---

## Objective

Convert a noisy timestamped match transcript `.txt` into a Python dict literal with one
entry per match in the transcript's `Ref:` section: normalised metadata, a chronological
event list, only events from the current match, **broadcast-absolute** timestamps, and
consistent names + controlled labels.

Output must load with `ast.literal_eval`. Emit only the dict literal — no fences, no
prose, no comments.

## 1. Output shape

```python
{
    ("Fighter A", 2025): {
        "winner": "Fighter A",
        "method": "Submission (Triangle Choke)",
        "start": "0:00",
        "opponent": "Fighter B",
        "event": "WNO 22",
        "weight_class": "",
        "stage": "",
        "win_type": "SUBMISSION",
        "submission": "Triangle Choke",
        "events": [
            {"label": "Closed Guard", "timestamp": "4:44", "type": "guard",
             "actor": "Fighter A"},
            {"label": "Triangle Choke", "timestamp": "6:28", "type": "submission",
             "actor": "Fighter A", "successful": False},
            {"label": "Triangle Choke", "timestamp": "7:21", "type": "submission",
             "actor": "Fighter A", "successful": True},
        ],
    },
}
```

Preserve `Ref:` order. Key = `(fighter_a, year)`; on a duplicate key append the start
(`("D. Reis [1:32:39]", 2025)`).

## 2. The `Ref:` block

Each line gives a bout and its start (`00:00 - 25:20 A vs B`, or start only). Missing end
= next match's start; last match ends at the final transcript timestamp. Normalise to
`M:SS` / `H:MM:SS`; compare in seconds.

**All timestamps are absolute positions in the full video, never relative to the bout.**
`export/site_data.py` subtracts the bout `start` when it renders, so storing bout-relative
values double-subtracts and breaks every video link.

## 3. Clean the transcript

Drop interface noise (`Neste vídeo`, `Pesquisar transcrição`, `Capítulo 1:`, `7 segundos`,
`[Music]`, `[Applause]`). Accept only real markers (`5:17`, `1:38:52`); text after a marker
belongs to it until the next. De-duplicate repeated blocks on
`(timestamp_seconds, normalised_text)` — a duplicated source block must not produce
duplicated events.

## 4. Split segments into matches

Assign on `match_start <= segment < match_end`. No event may leak across a boundary.
A result announced just after the boundary may still be used — `RESULT_GRACE_SECONDS = 90`
— but **only** to establish winner / method / submission, never to harvest extra events.

## 5. Live-action filter

Before emitting, ask: *is the commentator describing something visibly happening now,
between these two athletes, in this match?* Only `LIVE_ACTION` and `LIVE_RESULT` may
create events. `REPLAY_CONFIRMATION` may only refine an event already found live.

Exclude entirely: predictions ("he may look for a double leg"), coaching ("he should
stand"), hypotheticals ("if he posts, the triangle is there"), general instruction
("closed guard is strong for leg entries"), tendencies ("he is known for back attacks"),
history ("he took his back in their last match"), biography, strategy and judging
speculation, crowd/fatigue commentary, and uncertain guesses ("it looks like he may be
thinking about a kimura").

Signals for inclusion: *gets, takes, passes, sweeps, pulls, recovers, escapes, locks,
finishes, taps, enters, lands, completes, secures*.
Signals for exclusion: *could, might, may, should, would, probably, likely, expect,
needs to, wants to, looking to, thinking about, usually, normally, known for, likes to*.

A sentence may hold both — "he is looking to take the back, and now he secures both
hooks" yields only the completed back take.

## 6. Attempts — use the technique's real type

An executed-but-failed attempt keeps the **technique's own type** and carries
`successful: False`. It is never re-typed as `transition`.

```python
{"label": "Guard Pass",  "type": "pass",       "successful": False, ...}
{"label": "Double Leg Takedown", "type": "takedown", "successful": False, ...}
{"label": "Armbar", "type": "submission", "successful": True, ...}
```

Rationale: a failed guillotine is still a submission — it belongs in the athlete's
submission game, colours as one, and counts toward finish statistics. `clean_label`
rejects a label whose event type disagrees with its library entry, so a pass attempt
typed `transition` silently falls out of the shared graph.

Do not put "Attempt" in the label; the flag carries that. (`clean_label` strips it anyway,
so both forms converge, but the flag is the contract.)

`transition` is for actions whose *result* is genuinely not another category: arm drag,
duck under, snap down, inversion, leg-entanglement entry, takedown defence.

Exclude hypothetical attempt language ("he may attempt the heel hook").
"Looking for" alone is not an attempt — it becomes one when materially established
(one hook in → `Back Take` attempt; both hooks → `Back Control`).

## 7. Evidence threshold

Emit only on a clear direct statement, several adjacent segments that jointly confirm,
an official announcement, or a replay clarifying an already-detected live event. Never on
tactical possibility, expectation, reputation, an isolated technique name, an unresolved
pronoun, or narration from another match. Classify internally high/medium/low; emit
high and medium only. No confidence field in the output.

## 8. Names

Build an alias table per bout and use one canonical name in the key, `winner`,
`opponent` and every `actor`. Priority: verified name from the reference > full name
stated in the transcript > the abbreviated reference name. Never mix `D. Reis` /
`Diogo Reis` in one match. Leave the actor off when identity cannot be resolved.

## 9. Metadata

`opponent` is always fighter B from the reference — never derived from the winner.
`winner` only on an official announcement or explicit confirmation, else `None`; never
inferred from final position or apparent control. `method` ∈ `Decision`,
`Submission (X)`, `Referee Decision`, `Overtime`, `Draw`. `win_type` ∈ `DECISION`,
`SUBMISSION`, `REFEREE_DECISION`, `OVERTIME`, `DRAW`. `submission` is the official
finish, not the last attack mentioned. `weight_class` / `stage` stay `""` unless stated.

## 10. Types and labels

```python
ALLOWED_TYPES = {"guard", "control", "pass", "sweep", "takedown",
                 "submission", "escape", "transition"}
```

Check `analysis/data/technique_library.json` first and use its `en` string verbatim.
Prefer concise reusable labels (`Closed Guard`, `Arm Drag`, `Knee Cut Pass`, `Back Take`)
over commentary. Normalise synonyms (`RNC` → `Rear Naked Choke`, `Back mount` → `Back
Control`, `Single-leg` → `Single Leg Takedown`). Do not over-specify beyond what the
transcript supports: "he passes the guard" is `Guard Pass`, not a named pass variation.
Never emit placeholder events (`{"label": "Match", "type": "match"}`); an empty
`"events": []` is correct when nothing is reliable.

Ownership: a node belongs to the athlete whose game it is — `guard` to the guard player
(bottom), `pass` to the passer. See `docs/match_event_model.md`.

## 11. Timestamps per event

Use the moment the action first becomes clearly established. A position held across
several segments produces **one** event, at its start; re-emit only if it was clearly
lost and regained. A replay refines the live event and keeps the **live** timestamp — if
that timestamp cannot be located confidently, emit nothing.

## 12. Validation before output

- loads with `ast.literal_eval`, top level is a `dict`
- every match has `start`, `event`, `events` (a list)
- `opponent == fighter_b` and `!= fighter_a`
- `winner`, when present, ∈ {fighter_a, fighter_b}
- every `actor` ∈ {fighter_a, fighter_b}; every `type` ∈ `ALLOWED_TYPES`
- `match_start <= event_seconds < match_end` (grace period only for the result)
- events sorted by timestamp
- no duplicate `(label, timestamp, type, actor, successful)`
- `SUBMISSION` ⇒ `submission` set and `method == f"Submission ({submission})"`;
  `DECISION` ⇒ `submission is None` and `method == "Decision"`
- unknown stays `None`, never a guess

## 13. The rule

Record only what is confirmed to be happening in the current match. Filter out
speculation, prediction, advice, hypotheticals, tendencies, biography, history, judging
opinion, replay duplication, and general analysis.
