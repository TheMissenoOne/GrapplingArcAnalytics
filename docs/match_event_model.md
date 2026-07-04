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
