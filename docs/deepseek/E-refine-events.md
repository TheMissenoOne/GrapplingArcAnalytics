# E — Refine events: pbp → structured [{label, type, actor, successful, ts}]

**Goal**: Convert play-by-play (pbp) commentary into **structured events** that survive the import pipeline (actor must resolve to one of two athletes verbatim; labels must be canonical or specific; type must be from a fixed vocabulary).

**Output**: One JSON file per dump at `scripts/dumps/<event>_events.json`.  
**Input**: One bout's pbp window at a time (never load the full dump file).

---

## What You Deliver

Per dump, emit one sidecar JSON file. Format:

```
scripts/dumps/<event>_events.json
```

Content is a JSON object mapping `"<a_name>|<year>"` → array of event objects.

You **do not** touch the DB. You **do not** edit the dump file. The splices script (`scripts/apply_events.py`) merges the sidecar into the dump after you finish each batch.

**Processing order:**
1. Process the **31 READY** dumps first (listed below, §Dump Status).
2. Then process the **2 LOW** dumps (UFC 320 — truncated, Judo Paris — sparse): refine what exists.
3. **Skip** the 3 DONE dumps (Ethan Crelinsten, Polaris 4, Polaris Pro 1) — they already carry events.

---

## Dump Status — 63 Total

| Status | Count | Description |
|--------|-------|-------------|
| READY | 31 | Full pbp, 0 events. Refine to structured events. |
| LOW | 2 | Sparse/truncated pbp. Refine what exists. |
| DONE | 3 | Already have events (Ethan Crelinsten, Polaris 4, Polaris Pro 1). Skip. |
| REFINED | 27 | Already refined by prior work. Skip. |

### 31 READY (refine full pbp → events)

`cji2day1`, `cji2day2`, `craigjones`, `eddie_bravo_invitational_14_the_absolutes`, `leandro_lo`, `musumeci`, `pgf_world_2026_week_1_opening_day`, `pgf_world_2026_week_2_things_are_heating_up`, `pgf_world_2026_week_3_this_marks_the_halfway_point`, `pgf_world_2026_week_4_the_playoff_race_is_on`, `pgf_world_2026_week_5_regular_season_finale`, `polaris28prelims`, `polaris29`, `polaris30`, `polaris31`, `polaris32`, `polaris33`, `polaris34`, `polaris35`, `polaris36`, `polaris_18_submission_grappling_full_bjj_event_replay`, `polaris_25_prelims_live_full_no_gi_bjj_grappling_undercard`, `polaris_26_live_prelims_nine_free_matches_live`, `polaris_bjj_squads_team_usa_vs_team_uk_ireland_grappling_full_event`, `ruotolos`, `supercut_the_entire_2024_adcc_worlds_65kg_bracket`, `team_bjj_stars_vs_team_polaris_full_squads_matchup_polaris_37`, `ufc_324_free_fight_marathon`, `ufc_327_free_fight_marathon`, `ufc_328_free_fight_marathon`, `wno_30_open_weight_grand_prix_undercard_free_live_prelim_matches`

### 2 LOW (refine what exists)

`ufc_320_free_fight_marathon`, `evento_completo_final_do_jud_equipes_mistas_olimp_adas_paris_2024`

### 3 DONE (skip)

`ethan_crelinsten`, `polaris4`, `polarispro1`

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
   - On-vocabulary: use the library's `"en"` string **verbatim**, preserving case (e.g. `"Armbar"`, `"Guard Pass"`, `"Triangle Armbar"`).
   - Off-vocabulary: "Weird Guard Variant" → survives but doesn't merge into the shared graph.
   - **Before you use a label, check the library** (Recipe 3 below).
   - **Copy the grep'd `en` exactly** — do not lowercase, do not titleize manually.

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

## Timestamp Rules (pbp → ts)

`pbp[].ts` is integer seconds from bout start (e.g. `0`, `12`, `142`). Every emitted event **must** carry a `ts` field.

1. **(a) Order events by ts** — the events array per bout must be sorted ascending by `ts`.
2. **(b) Dedup by ts** — repeated commentary on the same action at roughly the same ts = one event. E.g. "armbar's deep… still deep… defending hard" all at ts `310` → one `{label: "Armbar", type: "submission", actor: "...", ts: 310}`.
3. **(c) Bout boundary** — pbp lines near the end may describe the next bout's walkout or announcements. If `ts` exceeds the visible fighting window OR the text clearly refers to a different match, treat that `ts` as the boundary and skip lines beyond it.
4. **(d) Copy ts verbatim** — each event gets `"ts": <integer>` from the first pbp entry that describes that action.

---

## Recipes (work one bout at a time)

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

### Recipe 2: Extract one bout's pbp

```bash
uv run python -c "
import scripts.dumps.<event>_data as m
k = list(m.RAW[<index>])[0]  # bout index (0-based; line number - 2)
v = m.RAW[<index>][k]
print(f'{k[0]} vs {v[\"opponent\"]} | method: {v[\"method\"]}')
[print(f'{s[\"ts\"]:>8} | {s[\"text\"]}') for s in v['pbp']]
"
```

Example — Polaris 31, bout 0 (line 2):
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
Rhys James vs Archie Hutchinson | method: Unknown
       0 | you Polaris 31. So now, let's get the action started...
      12 | So here we have Reese James based out of the northwest...
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
{"en": "Armbar", "type": "submission", "variants": ["arm bar", "arm lock", "arm drag to armbar"], ...}
```

**CRITICAL**: Copy the `"en"` string verbatim, preserving case.  
- Library has `"Armbar"` → use `"Armbar"`, not `"armbar"`.
- Library has `"Guard Pass"` → use `"Guard Pass"`, not `"guard pass"`.

If not found in library:
- Check if a close variant exists in the output above.
- If nothing matches, pick the closest library technique for this type, OR use a plain descriptive label + the correct type. Type still must be one of the 8.

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
- Repeated commentary ("still hunting the armbar… armbar's deep… defending the armbar") = **one event** at the earliest `ts`.
- Skip walkouts, announcements, rule explanations, crowd noise, analyst tangents.
- Aim for **~6–20 events per bout**; fewer if sparse pbp, more if detailed.
- **Order events by `ts` ascending** in the output array.

### Actor

- Full name, verbatim (from bout key or opponent field).
- Example: Commentary says "João goes for the armbar" → `actor: "João Vieira Souza"` (use full name from key).
- Never use referee, coach, or analyst names.

### Label & Type

1. Check the library (Recipe 3).
2. Use canonical `en` string **verbatim** (preserve case).
3. If not found, use a specific technique from the library of the same type, OR a descriptive label + the correct type.
4. Never invent types; always pick from the 8.

### Successful

- **True** = landed / finished (e.g., "armbar locked in", "guard pass completed").
- **False** = attempted but defended (e.g., "armbar attempt defended").
- **Omit** = neutral position or setup (e.g., "enters guard" — no success needed).
- A finishing submission always has `successful: True`.

### ts (timestamp)

- Copy the integer `ts` from the first pbp entry that describes the action.
- Every event **must** have `"ts": <integer>`.

### Fix the Bout Metadata

Update the bout's `winner`, `win_type`, and `submission` based on what pbp says.

- `winner`: full name of the athlete who won.
- `win_type`: points, decision, submission, knockout, etc. (must match the pbp narrative).
- `submission`: the finishing technique name (or `None` if no submission).

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

**Events to emit** (ordered by `ts`, label preserves library case):
```json
[
  {"label": "Guard Pull", "type": "guard", "actor": "Kade Ruotolo", "ts": 130},
  {"label": "Guard Pass", "type": "pass", "actor": "Josh Krier", "successful": true, "ts": 225},
  {"label": "Side Control", "type": "control", "actor": "Josh Krier", "ts": 250},
  {"label": "Armbar", "type": "submission", "actor": "Kade Ruotolo", "successful": true, "ts": 330}
]
```

(ts values are seconds from bout start; 2:10 = 130s, 3:45 = 225s, etc.)

**Bout metadata to set**:
```json
{
  "winner": "Kade Ruotolo",
  "win_type": "submission",
  "submission": "Armbar"
}
```

**What was dropped**:
- "Pass attempt" (2:45) — same action as the pass at 3:45, keep only the completion.
- Intermediate "still hunting" / "maintaining" comments — noise, not new actions.

---

## Output Format

Write one file per dump.

**Filename**: `scripts/dumps/<event>_events.json`

**Content** (JSON object, bout-key → events array):
```json
{
  "Josh Krier|2025": [
    {"label": "Guard Pull", "type": "guard", "actor": "Josh Krier", "ts": 83},
    {"label": "Armbar", "type": "submission", "actor": "Josh Krier", "ts": 310},
    ...
  ],
  "Abubakar Abubakarovich|2025": [
    {"label": "Armbar", "type": "submission", "actor": "Abubakar Abubakarovich", "successful": true, "ts": 270}
  ]
}
```

Keys are `"{a_name}|{year}"` — match the RAW bout key exactly.

---

## After Refinement: Splice Events into Dump

Once you've written the sidecar JSON, the helper `scripts/apply_events.py` splices it in:

```bash
uv run python -m scripts.apply_events <module_name> scripts/dumps/<event>_events.json
```

Example:
```bash
uv run python -m scripts.apply_events polaris31_data scripts/dumps/polaris31_events.json
```

This:
1. Loads the dump module.
2. Patches the events into RAW (by bout key).
3. Drops the pbp.
4. Rewrites the dump (greppable format).

---

## Verification

Run the per-dump check (checks first bout for events + no pbp):
```bash
uv run python -m scripts.apply_events --check
```

Then test the import:
```bash
uv run python -m scripts.reprocess_all --only <Label> --dry-run
```

(where `<Label>` is the DATASETS label, e.g. "Polaris31". See `scripts/reprocess_all.py` for the full label list.)

Check the output for:
- No "dropping event with unknown actor" warnings.
- All bouts have non-empty events.
- `clean_label` successfully maps labels to library entries.

If all pass, commit the refined dumps + sidecar JSON and run the full import:
```bash
uv run python -m scripts.reprocess_all --only <Label>
```

---

## Troubleshooting

- **"dropping event with unknown actor"** → actor doesn't match one of the two athletes (wrong name or partial name). Use full name from bout key or opponent field.
- **Labels don't merge into the graph** → label is off-vocabulary. Check the library (Recipe 3); if not found, pick a standard technique name + correct type.
- **Metadata mismatch** (e.g., winner name not in events) → fix `winner` / `win_type` / `submission` fields based on pbp.
- **pbp ts out of order** → the dumps already sort pbp by ts; just process them left-to-right.
- **Bout has 0 pbp lines** → skip that bout (no events to extract).
