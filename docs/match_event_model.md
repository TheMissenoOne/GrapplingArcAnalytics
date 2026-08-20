# Match event model — the graph convention every entry path must follow

A bout's `sequence` is a list of **events**; each event is one grappling action. Events become the
transition graph (`export/match_breakdown.py:_transition_graph`) — node = normalized technique label,
edge = each consecutive pair, `fighter` side taken from the event's actor. This model is the same
whether events come from the **DeepSeek refiner** (`docs/deepseek/E-refine-events.md`), `convert_dump.py`,
the `insert_*.py` scripts, or the admin paste box. Get it wrong on any path and the graph is wrong.

## Event shape

```python
{"label": "<Technique / Position>",   # canonicalized to analysis/data/technique_library.json (clean_label)
 "type": "<one of the 8 below>",
 "actor": "<full athlete name>",       # must resolve to one of the two athletes (athlete_key) or the event is DROPPED
 "successful": True | False,           # optional: True landed/finished, False attempted-but-defended, omit if neutral
 "ts": <int seconds>}                  # optional but preferred: position in the bout, drives video seek
```

`type` ∈ `guard | pass | control | takedown | sweep | submission | escape | transition`.
(`strike`/`reset`/`referee` are dropped by `_clean_events`; `concept` library entries are not event nodes.)

## Actor ownership — WHICH fighter owns a node

`actor` is the fighter whose **game the node belongs to**, not who is winning the exchange or who the
commentator is naming. Each fighter's dossier graph is built only from the nodes they own, so a
mis-assigned owner pollutes both fighters' games.

| type         | owner = the fighter who…                                                        |
|--------------|---------------------------------------------------------------------------------|
| `guard`      | is **playing / retaining the guard** (bottom — whoever's guard it is)            |
| `pass`       | is **passing / clearing** the guard (top, attacking the guard)                  |
| `control`    | **holds** the dominant position (mount, back, side, knee-on-belly, crucifix, N-S)|
| `takedown`   | **completes** the takedown / throw                                              |
| `sweep`      | executes the **sweep / reversal** (bottom → top)                                |
| `submission` | **applies** the submission                                                      |
| `escape`     | **escapes** the bad position (out of mount, back, a submission)                 |
| `transition` | **initiates** the movement (guard pull, berimbolo, inversion, entry to a spot)  |

**Guard belongs to the guard player, not the passer.** One moment is often two events with different
actors — A passing into B's half guard:

```python
{"label": "Half Guard", "type": "guard", "actor": "B"}       # B plays the guard → B's game
{"label": "Guard Pass",  "type": "pass",  "actor": "A", "successful": False}   # A attacks it → A's game
```

The guard is owned by whoever pulled guard, is underneath, or is being passed against ("in Gordon's
half guard" → Gordon's). A completed pass ends the guard node (passer now owns a `control` node); a
recovery re-opens it. An attack **from** a position keeps its attacker (triangle from closed guard →
the guard player owns both the `guard` and the `submission`). Neutral 50/50 / double-guard-pull →
the fighter who initiates or breaks the symmetry.

> Full refiner-facing version with grep recipes: `docs/deepseek/E-refine-events.md`.

## Does the corpus actually follow this? Measured, 2026-08-20

The table above is the convention every entry path is told to follow. Whether the ingested rows
obey it is a separate question, and it was measured against prod rather than assumed — 9 600
sequence events across 700 bouts of `matches.status='final'`.

**Two types were checked against an independent column, not against themselves.**

| claim | test | result |
|---|---|---|
| `submission` actor = the fighter APPLYING it | in bouts with `win_type='SUBMISSION'`, is the last `successful=True` submission event's actor the `winner_id`? | **225 / 237 (94.9%)** — and matching the event label against `matches.submission` gives 233 / 246 (94.7%) |
| `control` actor = the fighter HOLDING the position | in bouts finished from the back (`matches.submission` names an RNC / bow-and-arrow / back attack), is the last `Back Control` event the winner's? | **31 / 37 (83.8%)** |

Baseline for both: an arbitrary event belongs to the winner 62.5% of the time (5 379 / 8 605).

**`guard` and `pass` do not hold as a per-event rule.**

| test | result | what the convention predicts |
|---|---|---|
| a `guard` event and a `pass` event within 10 s and two slots of each other carry the same actor | **52 / 82 (63.4%)** | near 0% — the guard is the bottom player's, the pass is the top player's, and one fighter cannot be both at once |
| within a bout, the athlete owning the most `guard` events also owns the most `pass` events | **108 / 166 (65.1%)** | well below the 73.7% expected under a null where actor is drawn in proportion to who is dominant in that bout |

**The mechanism.** 307 of 700 bouts (43.9%) file **every** event under one athlete. Some are
short and legitimate; 159 of them carry five events or more. One real row hands a single
fighter `Scarf Hold`, `Half Guard`, `Back Control`, `Smash Pass` and `Closed Guard` inside two
minutes, which is physically impossible. Those batches set `actor` to whoever the transcript
was narrating.

**It is a property of the BATCH, not of the corpus.** Among bouts of ≥8 events:

```
CJI 2 - Day 1     0 / 7      Polaris 18      0 / 7       Polaris 31   1 / 14
Polaris 33        2 / 11     Polaris 36      4 / 15      CJI 2 Day 2  5 / 17
ADCC 2024         6 / 15     WNO 22          4 / 7       ADCC 2022   15 / 30
```

### What follows from it

`analysis/attribution.py` derives role in two steps rather than one: the table above becomes a
per-`(type, label)` lookup, and then a **bout-level gate** decides whether this bout's actor
assignments can carry a role at all. A bout that is entirely one-sided (≥6 events, one actor)
or that gives one actor a top-side state and a bottom-side state within 10 s has no readable
role; its events are preserved raw and dropped from every directional statistic.

Corpus-wide that gate withholds roles on 186 of 700 bouts (4 152 of 9 600 events, 43.2%). On
the 38 ADCC-2026 scouting bouts it withholds 4 bouts and 45 of 488 events (9.2%).

**Fixing the ingest is the real repair**; the gate is what keeps a published number honest until
then. The `guard`/`pass` rows are the ones to re-read first, and `docs/PROMPT_events_sidecar.md`
is where the instruction to the refiner lives.
