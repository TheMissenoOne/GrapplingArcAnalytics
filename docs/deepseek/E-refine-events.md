# Refiner prompt — turn preliminary `pbp` into `events`

`scripts/batch_queue.py` now emits a **preliminary** dump per transcript in
`scripts/dumps/<event>_data.py`. Each bout carries a `pbp` list (cleaned, timestamped
commentary) but an **empty `events`** list. Your job: read `pbp`, write `events`.

Run this prompt **once per bout** (or per dump, a bout at a time). Keep everything else
in the bout dict unchanged except as noted.

## Input (one bout)

```python
(a_name, year): {
  "opponent": b_name,             # side B
  "event": "...", "weight_class": "", "stage": "",
  "win_type": None|"SUBMISSION"|"DECISION"|"POINTS"|"DRAW",   # PRELIMINARY guess
  "submission": None|"Armbar"|...,                            # PRELIMINARY guess
  "winner": None|name,                                        # PRELIMINARY guess
  "method": "...",
  "pbp": [ {"ts": <sec from bout start>, "text": "<commentary>"}, ... ],
  "events": [],                   # <-- you fill this
}
```

The two athletes are `a_name` (the dict key) and `opponent` (side B).

## Output — fill `events`, correct the result, drop `pbp`

`events` = chronological list of the **actual grappling actions** you can identify in
`pbp`. Each event:

```python
{"label": "<Technique / Position, Title Case>",
 "type": "<one of: takedown | pass | sweep | control | submission | guard | transition | escape>",
 "actor": "<a_name or opponent — the athlete DOING it>",
 "successful": True|False}   # include ONLY for attempts/finishes; omit for neutral positions
```

Rules:
1. **actor must be exactly `a_name` or the `opponent` string** (copy them verbatim, incl.
   spelling). Commentary uses first names / nicknames — resolve them to the two athletes.
2. **type** — pick the closest: guard pulls/retention = `guard`; passing = `pass`;
   sweeps/reversals = `sweep`; mount/back/side/control = `control`; takedowns/throws =
   `takedown`; sub attempts & finishes = `submission`; position changes/entries =
   `transition`; escapes = `escape`.
3. **successful** — `True` if it landed / finished; `False` if attempted-but-defended.
   Omit the key for a neutral position reached (no success/fail sense). A finishing sub
   = `submission` + `successful: True`.
4. **Order chronologically** by `ts`. Skip walkouts, intros, rules, crowd, and pure
   commentary — only real actions.
5. **Correct the result from what you read:** set `winner` (verbatim name), `win_type`,
   and `submission` (or `None`) to match the pbp. Fix the preliminary guess if wrong.
   Rebuild `method` as `"<win_type>"` or `"<win_type> (<submission>)"`.
6. **Delete the `pbp` key** in the final dict — it is scratch input, not stored.
7. If `pbp` has no discernible grappling action, leave `events: []` and keep the result
   fields as-is (do not invent events).

## Example (shape, from a real refined dump)

```python
"events": [
  {"label": "Pull Guard", "type": "transition", "actor": "Gabby Pagana", "successful": True},
  {"label": "Straight Foot Lock Attempt", "type": "submission", "actor": "Gabby Pagana", "successful": False},
  {"label": "Inversion / Leg Entanglement", "type": "guard", "actor": "Paige Climber"},
  {"label": "Sweep / Top Position", "type": "sweep", "actor": "Gabby Pagana", "successful": True},
]
```

## After refining every dump

Move refined `scripts/dumps/<event>_data.py` into the `reprocess_all.py` `DUMPS` registry
(`("scripts.dumps.<mod>_data", "<event tag>", "<Label>")`), then:
`uv run python -m scripts.reprocess_all --only <Label> --dry-run` to sanity-check, then
without `--dry-run` to import + re-export the site.
