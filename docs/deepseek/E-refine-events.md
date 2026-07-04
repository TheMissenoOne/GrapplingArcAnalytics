# E — Refine events: pbp → structured [{label, type, actor, successful, timestamp}]

**Goal**: Convert play-by-play (pbp) commentary into **structured events** that survive the import pipeline.

**Output**: One JSON sidecar per dump, then splice into the dump module.

**Source of truth**: Raw transcript files live in `transcripts/queue/`. They contain the full YouTube auto-caption text with timestamps. The dump modules (`scripts/dumps/*_data.py`) **do not** store pbp in git — pbp was ephemeral, loaded at batch-generation time and now removed. To transcribe a dump, read the transcript file directly.

**Key difference from prior workflow**: Do NOT use the dump module's embedded pbp (it's gone). Read the transcript file, filter action lines, write events. The helper `scripts/refine_pbp.py` exists but produces noisy output (many false positives from analyst chatter). **Manual line-by-line review of filtered action lines produces higher quality.**

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

## Dump Status — 63 Total (July 2026)

**All 63 dumps now have events** (via automated keyword refiner + manual passes). No dump retains raw pbp in git — it was ephemeral working-tree data that was removed after splicing events.

| Status  | Count | Description |
|---------|-------|-------------|
| REFINED | 63    | Events exist. Quality varies (auto keyword vs manual). |

If you want to improve a specific dump's events:

1. **Regenerate pbp**: The transcript for the queue-based dumps must be re-fetched from YouTube. The match card is in `transcripts/queue/<event>.txt` but the full auto-caption text has been removed. To re-fetch, use a YouTube caption downloader (`yt-dlp --write-auto-subs --sub-lang en` or similar) on the original video URL.

2. **Read the transcript**: Once fetched, parse the transcript file using Recipe 2 to filter action lines.

3. **Cross-reference** against the existing events in the sidecar JSON at `scripts/dumps/<event>_events.json`. Fix bad labels, wrong actors, duplicates.

### Priority Dumps for Manual Review

These have the most noisy auto-generated events (keyword false positives from analyst chatter):

- `leandro_lo` (11 bouts, many false positives from speculation like "hunting for the armbar")
- `ruotolos` (11 bouts, same issue)
- `musumeci` (3 bouts, tiny pbp windows)
- All `pgf_world_2026_week_*` (analyst chatter, not play-by-play)
- `ufc_320_free_fight_marathon` — truncated pbp, got 1 event total. Needs re-fetch.

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

## Actor Ownership Rules (WHICH fighter owns each node)

Rule 1 (above) is about spelling the name right. This is about picking the **right fighter**.
`actor` is **not** "who is winning the exchange" or "who the commentator is talking about" — it is
the fighter whose **game that node belongs to**. Each fighter's dossier graph is built only from the
nodes they own, so if you attribute a guard to the passer, the guard player's game loses their guard
and the passer's game gains one that isn't theirs.

**actor = the fighter who, for that node type:**

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

### The one that trips people up: guard belongs to the guard player, not the passer

One physical moment is often **two events with different actors**. A is on top passing, B is on
bottom in half guard:

```json
{"label": "Half Guard", "type": "guard", "actor": "B", "ts": 140}
{"label": "Guard Pass",  "type": "pass",  "actor": "A", "successful": false, "ts": 145}
```

Half Guard is **B's** (their game shows half guard); the pass attempt is **A's**. Never assign the
guard to A just because A is the active/aggressing fighter.

### Identifying the guard player

The guard is owned by whoever **pulled guard**, is **underneath**, is **being passed against**, or is
named with the guard: *"in Gordon's half guard"*, *"Mica's De la Riva"* → the guard is Gordon's /
Mica's, even if the sentence is about the opponent trying to pass it. A completed pass **ends** the
guard node (the passer now owns a `control` node); a guard recovery/retention **re-opens** it (guard
player owns it again).

### Attacks from a position keep their attacker

A submission or sweep launched **from** guard is a separate node owned by whoever throws it — usually
the guard player. Triangle from closed guard → the guard player owns **both** the `guard` (Closed
Guard) and the `submission` (Triangle Choke). Back-take → the one taking the back owns the `control`.

### Neutral / symmetric positions

50/50, double guard pull, a neutral leg entanglement, standing grip-fighting: assign to the fighter
who **initiated** it or is the more active party; if truly symmetric, assign it to the fighter who
**breaks the symmetry** (attacks or transitions out of it) so the node lands in the game that used it.

---

## Timestamp Rules (pbp → ts)

`pbp[].ts` is integer seconds from bout start (e.g. `0`, `12`, `142`). Every emitted event **must** carry a `ts` field.

1. **(a) Order events by ts** — the events array per bout must be sorted ascending by `ts`.
2. **(b) Dedup by ts** — repeated commentary on the same action at roughly the same ts = one event. E.g. "armbar's deep… still deep… defending hard" all at ts `310` → one `{label: "Armbar", type: "submission", actor: "...", ts: 310}`.
3. **(c) Bout boundary** — pbp lines near the end may describe the next bout's walkout or announcements. If `ts` exceeds the visible fighting window OR the text clearly refers to a different match, treat that `ts` as the boundary and skip lines beyond it.
4. **(d) Copy ts verbatim** — each event gets `"ts": <integer>` from the first pbp entry that describes that action.

---

## Recipes (work one bout at a time)

### Recipe 1: Find the raw transcript file

Transcripts are in `transcripts/queue/`. Find the one matching your dump:

```bash
ls transcripts/queue/ | grep -i <event>
```

Example — find Polaris 31's transcript:
```bash
ls transcripts/queue/ | grep -i polaris
```

If no match, check `transcripts/<event>.txt` for non-queue dumps (ADCC, WNO, etc.).

### Recipe 2: List bouts + read action lines from transcript

Use this one-shot script to show bouts, their timestamps, and filtered action lines:

```python
from pathlib import Path
import re

TR = Path("scripts/dumps/<event>_data.py")

# Parse bout lineup (timestamp + athlete names)
import importlib
mod = importlib.import_module(f"scripts.dumps.<event>_data")
bouts = []
for i, bd in enumerate(mod.RAW):
    for (a, yr), v in bd.items():
        bouts.append((i, a, v["opponent"], yr, v.get("start", "")))

for idx, a, opp, yr, start in bouts:
    print(f"[{idx:>2}] {a:30s} vs {opp:30s} start={start}")
```

Then read the transcript file manually. The transcript is a huge text file with timestamps like `1:23:45 | text`. Filter action lines:

```bash
grep -i -E 'takedown|sweep|pass|submission|armbar|triangle|choke|heel|kimura|guillotine|mount|back|guard pull|foot lock|tap|finish|wins|swept|passed' transcripts/queue/<event>.txt | head -100
```

For deeper per-bout analysis, parse the transcript window between bout start and next bout start:

```python
TR = Path("transcripts/queue/<event>.txt")
text = TR.read_text()
# parse timeline
lines = []
for line in text.split('\n'):
    m = re.match(r'(\d{1,2}:\d{2}:\d{2})\s+(.*)', line.strip())
    if m:
        ts = sum(int(x)*60**i for i,x in enumerate(reversed(m.group(1).split(':'))))
        lines.append((ts, m.group(1), m.group(2)))
# filter lines within bout window (start_sec to end_sec)
action_words = ['takedown', 'sweep', 'pass', 'submission', ...]
action = [l for l in lines if start_sec <= l[0] < end_sec and any(w in l[2].lower() for w in action_words)]
for _, ts_str, txt in action[:10]:
    print(f"  {ts_str} | {txt[:120]}")
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

Once you've written the sidecar JSON, splice it:

```bash
uv run python -m scripts.apply_events <module_name> scripts/dumps/<event>_events.json
```

**IMPORTANT**: Pass the full module name with `_data` suffix. The apply_events script constructs the path as `{module}.py`, so pass `polaris31_data` (not `polaris31`).

Example:
```bash
uv run python -m scripts.apply_events polaris31_data scripts/dumps/polaris31_events.json
```

This:
1. Loads the dump module.
2. Patches the events into RAW (by bout key).
3. Drops the pbp.
4. Rewrites the dump (greppable format).

### Composite Keys

Some dumps have multiple bouts for the same athlete (compilation videos, e.g. `craigjones` where Craig Jones fights 15 opponents). The sidecar key format for these is:

```
"{a_name}|{opponent}|{year}"
```

NOT just `"{a_name}|{year}"` — the opponent disambiguates which bout. apply_events composes keys internally the same way.

To find the correct key for a bout:

```python
import scripts.dumps.<event>_data as m
for bd in m.RAW:
    for (a, yr), v in bd.items():
        key = f"{a}|{v['opponent']}|{yr}"
        print(key)
```

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
