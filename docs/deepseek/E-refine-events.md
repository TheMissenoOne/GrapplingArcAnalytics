# E — Refine events: pbp → structured [{label, type, actor, successful}]

**Goal**: Convert play-by-play (pbp) commentary into **structured events** that survive the import pipeline (actor must resolve to one of two athletes verbatim; labels must be canonical or specific; type must be from a fixed vocabulary).

**Output format**: One JSON file per dump.  
**Input**: One bout's pbp window at a time (never load the full 290KB dump).

---

## The Consumer Rules (silent droppers)

These rules enforce **correctness on import**. Violate them and your events vanish.

1. **Actor matching** (`scripts/insert_ufc_matches.py:170`)  
   - `actor` must be the **full athlete name, verbatim** — from the bout key (a_name) or the opponent field.
   - First names, nicknames, "the red corner", "the top fighter" → resolve to full name.
   - If commentary says "João" and the athlete is "João Vieira Souza", use the full name.
   - **Wrong actor = event dropped silently.**  
   Example: bout key is `("João Vieira Souza", 2025)`, opponent is "Gordon Ryan".  
   Valid actors: "João Vieira Souza", "Gordon Ryan".  
   Invalid: "João", "JVS", "red corner", "the guard player".

2. **Label vocabulary** (`analysis/clean_label`)  
   - Labels are canonicalized against `analysis/data/technique_library.json` (137 techniques).
   - On-vocabulary: "Armbar" → "armbar" (canonical en string).
   - Off-vocabulary: "Weird Guard Variant" → survives but doesn't merge into the shared graph.
   - **Before you use a label, check the library** (recipe below).

3. **Type vocabulary** (fixed 8 values)  
   - `guard | submission | takedown | control | pass | escape | sweep | transition`
   - Never: concept, strike, reset, referee, crowd.
   - **Wrong type = event dropped.**

4. **Successful flag** (optional, omit unless meaningful)  
   - `True` = landed/finished.
   - `False` = attempted but defended.
   - Omit for a neutral position reached (e.g., entering guard = no successful flag).
   - A finishing submission = `{label: "Triangle", type: "submission", successful: True}`.

---

## Recipes (work one bout at a time without loading the file)

### Recipe 1: List all bouts in a dump

```bash
grep -n "), 20[0-9][0-9]): {" scripts/dumps/<event>_data.py
```

Output: line number and the bout key.  
Example:
```
2: {('Craig Jones', 2025): {
5: {('Abubakar Abubakarovich', 2025): {
```

Each line is a bout. Use the line number to extract it with Recipe 2.

### Recipe 2: Extract one bout's pbp (small window, no full load)

```bash
uv run python -c "
import scripts.dumps.<event>_data as m
k = list(m.RAW[<index>])[0]  # bout index (0-based; line number - 2)
v = m.RAW[<index>][k]
print(f'{k[0]} vs {v[\"opponent\"]} | method: {v[\"method\"]}')
[print(f'{s[\"ts\"]:>8} | {s[\"text\"]}') for s in v['pbp']]
"
```

Example: Polaris 31, bout 0 (line 2):
```bash
uv run python -c "
import scripts.dumps.polaris31_data as m
k = list(m.RAW[0])[0]
v = m.RAW[0][k]
print(f'{k[0]} vs {v[\"opponent\"]} | method: {v[\"method\"]}')
[print(f'{s[\"ts\"]:>8} | {s[\"text\"]}') for s in v['pbp']]
"
```

Output:
```
Josh Krier vs Anthony Serrapica | method: Submission
     1:23 | Josh Krier sits and pulls guard against Anthony Serrapica.
     2:45 | Guard pass attempt by Serrapica.
     3:12 | Josh Krier establishes control from top.
    ...
```

### Recipe 3: Check a label against the technique library

```bash
grep -i "<term>" analysis/data/technique_library.json
```

Example:
```bash
grep -i "armbar" analysis/data/technique_library.json
```

Output (one match per canonical entry):
```json
{"en": "armbar", "type": "submission", "variants": ["arm bar", "arm lock", "arm drag to armbar"], ...}
```

If found:
- Use the canonical `"en"` string verbatim (e.g., "armbar", not "Armbar").
- Use the `"type"` field (must be one of the 8 types above).

If not found:
- Check if a close variant exists in the output above.
- If nothing matches, pick the closest library technique for this type, OR keep a plain descriptive label (e.g., "Guard Pass Attempt" if "guard pass" isn't found).
- **Type still must be one of the 8.**

### Recipe 4: Verify the two athletes for actor resolution

```bash
uv run python -c "
import scripts.dumps.<event>_data as m
k = list(m.RAW[<index>])[0]
a_name, year = k
v = m.RAW[<index>][k]
print(f'Athletes: {a_name!r} vs {v[\"opponent\"]!r}')
"
```

Use these exact strings for `actor`. No nicknames, no abbreviations.

---

## Refinement Rules

### Granularity & Dedup

- One event per **real action** (technique application, position change, defensive success).
- Repeated commentary ("still hunting the armbar… armbar's deep… defending the armbar") = **one event**.
- Skip walkouts, announcements, rule explanations, crowd noise, analyst tangents.
- Aim for **~6–20 events per bout**; fewer if sparse pbp, more if detailed.
- Order events by `ts` (timestamp).

### Actor

- Full name, verbatim (from bout key or opponent field).
- Example: Commentary says "João goes for the armbar" → `actor: "João Vieira Souza"` (use full name from key).
- Never use referee, coach, or analyst names.

### Label & Type

1. Check the library (Recipe 3).
2. Use canonical `en` string if found.
3. If not found, use a specific technique from the library of the same type, OR a descriptive label + the correct type.
4. Never invent types; always pick from the 8.

### Successful

- **True** = landed / finished (e.g., "armbar locked in", "guard pass completed").
- **False** = attempted but defended (e.g., "armbar attempt defended").
- **Omit** = neutral position or setup (e.g., "enters guard" — no success needed).

A finishing submission always has `successful: True`.

### Fix the Bout Metadata

Update the bout's `winner`, `win_type`, and `submission` based on what pbp says.

- `winner`: full name of the athlete who won.
- `win_type`: points, decision, submission, knockout, etc. (must match the pbp narrative).
- `submission`: the finishing technique name (or None if no submission).

Example:
```json
{
  "winner": "Craig Jones",
  "win_type": "submission",
  "submission": "footlock"
}
```

---

## Worked Example

**pbp excerpt** (Polaris 31, bout 2):
```
    2:10 | Guard pull by Kade Ruotolo.
    2:45 | Pass attempt by Josh Krier.
    3:00 | Still hunting the pass. Guard staying strong.
    3:20 | Still hunting the pass. Deep in half guard.
    3:45 | Pass completed to side control.
    4:10 | Side control maintained.
    4:50 | Armbar hunt by Ruotolo.
    5:15 | Still deep in the armbar. Krier defending hard.
    5:30 | Armbar locked in. Krier taps.
```

**Events to emit**:
```json
[
  {
    "label": "guard pull",
    "type": "guard",
    "actor": "Kade Ruotolo",
    "ts": "2:10"
  },
  {
    "label": "guard pass",
    "type": "pass",
    "actor": "Josh Krier",
    "successful": true,
    "ts": "3:45"
  },
  {
    "label": "side control",
    "type": "control",
    "actor": "Josh Krier",
    "ts": "4:10"
  },
  {
    "label": "armbar",
    "type": "submission",
    "actor": "Kade Ruotolo",
    "successful": true,
    "ts": "5:30"
  }
]
```

**Bout metadata to set**:
```json
{
  "winner": "Kade Ruotolo",
  "win_type": "submission",
  "submission": "armbar"
}
```

**What was dropped**:
- "Pass attempt" (2:45) — because the pass succeeded at 3:45; the attempt is the same action.
- Intermediate "still hunting" / "maintaining" comments — noise, not new actions.

---

## Output Format

Write one file per event batch (typically one dump, multiple bouts).

**Filename**: `transcripts/deepseek/<event>_events.json`

**Content** (JSON object, bout-key → events array):
```json
{
  "Josh Krier|2025": [
    {"label": "guard pull", "type": "guard", "actor": "Josh Krier", "ts": "1:23"},
    ...
  ],
  "Abubakar Abubakarovich|2025": [
    {"label": "armbar", "type": "submission", "actor": "Abubakar Abubakarovich", "successful": true},
    ...
  ]
}
```

Keys are `"{a_name}|{year}"` (from the RAW bout key).

---

## After Refinement: Splice Events into Dump

Once you've written the sidecar JSON, the helper `scripts/apply_events.py` splices it in:

```bash
uv run python -m scripts.apply_events <module_name> transcripts/deepseek/<event>_events.json
```

Example:
```bash
uv run python -m scripts.apply_events polaris31_data transcripts/deepseek/polaris31_events.json
```

This:
1. Loads the dump module.
2. Patches the events into RAW (by bout key).
3. Drops the pbp.
4. Rewrites the dump (greppable format).

---

## Verification

Run the self-check:
```bash
uv run python -m scripts.apply_events --check
```

This ensures the dump round-trips and has no orphan pbp.

Then test the import:
```bash
uv run python -m scripts.reprocess_all --only <Label> --dry-run
```

(where `<Label>` is the event label from the DATASETS entry, e.g., "Polaris 31").

Check the output for:
- No "dropping event with unknown actor" warnings.
- All bouts have non-empty events.
- `clean_label` successfully maps labels to library entries.

If all pass, commit the refined dumps and run the full import:
```bash
uv run python -m scripts.reprocess_all --only <Label>
```

---

## Troubleshooting

- **"dropping event with unknown actor"** → actor doesn't match one of the two athletes (wrong name or partial name). Use full name from bout key or opponent field.
- **Labels don't merge into the graph** → label is off-vocabulary. Check the library (Recipe 3); if not found, pick a standard technique name + correct type.
- **Metadata mismatch** (e.g., winner name not in events) → fix `winner` / `win_type` / `submission` fields based on pbp.
