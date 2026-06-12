# Technique Library — Audit Findings & Refinement Plan

**Date:** 2026-06-12  
**Scope:** `export/tech_library.py` — phase 2 of GrapplingArcAnalytics  
**Output:** `data/processed/technique_library.json` (89 entries), `data/processed/technique_effectiveness.json` (30 scores)  
**Status:** ✅ APPLIED 2026-06-12 — all findings fixed, outputs regenerated, covered by `tests/test_tech_library.py`

> **Additional bug found during application (not in original audit):** library sort key gave
> unscored entries `-1`, which sorts *before* every scored entry (max score 0.734 → key
> -0.734 > -1). Output was effectively unscored-first, contradicting the "effectiveness
> descending" docstring. Fixed: unscored key is now `1` (sorts last). Regression test:
> `test_build_library_dedup_and_sort`.

---

## 1. Current State

### Pipeline
- **Sources:** grappling_techniques dataset (76 rows) + ADCC historical (1,028 matches, 32 sub types) + existing app nodes (137)
- **Cross-reference:** Name normalization + `NAME_ALIASES` dict resolves `rnc→Rear Naked Choke`, heel hook variants, PT-BR names
- **Effectiveness formula:** `sub_count×0.35 + stage_depth×0.25 + weight_class_span×0.15 + finals_rate×0.15 + sex_span×0.10`, with floor for `<3` ADCC occurrences
- **Output shape:** `NodeLibraryItem[]` — `{_id, name, type, translations, variations, source, already_in_app, effectiveness?}`

### Numbers
| Metric | Value |
|--------|-------|
| Total techniques | 89 |
| From dataset | 71 |
| ADCC-only | 18 |
| Already in app | 38 |
| New | 51 |
| With effectiveness scores | 30 |
| ruff/mypy | clean |

---

## 2. Audit Findings

### 2.1 Dead Code — `existing_names_lower` (P1)
**File:** `export/tech_library.py:284-295`  
**Problem:** Set `existing_names_lower` is computed from `existing_nodes` but never read.  
**Impact:** Wasted CPU on every export run.  
**Fix:** Delete the block.

```python
# DELETE — never referenced
existing_names_lower = {
    _normalize_name(n.get("name", "") + " " + n.get("translations", {}).get("en", ""))
    for n in existing_nodes
}
existing_names_lower.update(...)
```

### 2.2 Dead Code — `tech_names` (P1)
**File:** `export/tech_library.py:348-357`  
**Problem:** Set `tech_names` is computed from `tech_df` rows + library entries, then never referenced in the subsequent loop.  
**Impact:** Wasted CPU. Note that the loop (line 359+) correctly uses `seen_normalized` for dedup instead.  
**Fix:** Delete the block.

```python
# DELETE — never referenced
tech_names = {_normalize_name(str(r.get("technique_name", r.get("Name", ""))))
              for _, r in tech_df.iterrows()}
tech_names.update(...)
```

### 2.3 Missing Portuguese Translations (P1)
**File:** `export/tech_library.py:85-139`  
**Problem:** Several ADCC-only techniques lack a key in `DEFAULT_PT_TRANSLATIONS`, causing `pt_name = name_en` fallback. Additionally, some keys exist only with a suffix (`"guillotine choke"`) while the normalized name lacks the suffix (`"guillotine"`).

| Technique | Norm Key | `DEFAULT_PT_TRANSLATIONS` Has | Result |
|-----------|----------|-------------------------------|--------|
| Guillotine | `guillotine` | `guillotine choke → Guilhotina` | Miss — falls back to `"Guillotine"` |
| Triangle | `triangle` | `triangle choke → Triângulo` | Miss — falls back to `"Triangle"` |
| Ezekiel | `ezekiel` | `ezekiel choke → Ezequiel` | Miss — falls back to `"Ezekiel"` |
| Headlock | `headlock` | missing | Falls back to `"Headlock"` |
| Cross Face | `cross face` | missing | Falls back to `"Cross Face"` |
| Shoulder Lock | `shoulder lock` | missing | Falls back to `"Shoulder Lock"` |
| Wristlock | `wristlock` | `wrist lock → Chave de Pulso` | Space mismatch — falls back to `"Wristlock"` |
| Leg Lock | `leg lock` | missing | Falls back to `"Leg Lock"` |
| Twister | `twister` | missing | Falls back to `"Twister"` |
| Z Lock | `z lock` | missing | Falls back to `"Z Lock"` |

**Impact:** 11 ADCC-only entries show EN title as PT name. Portuguese users see raw English.  
**Fix:** Add 8 new keys + 3 alias keys to `DEFAULT_PT_TRANSLATIONS`:

```python
"guillotine": "Guilhotina",
"triangle": "Triângulo",
"ezekiel": "Ezequiel",
"headlock": "Gravata",
"cross face": "Pressão Facial",
"shoulder lock": "Chave de Ombro",
"wristlock": "Chave de Pulso",
"leg lock": "Chave de Perna",
"twister": "Twister",
"z lock": "Z Lock",
```

### 2.4 Missing Variation Alt Entries (P1)
**File:** `export/tech_library.py:418-436` — `_generate_variations` → `alts` dict  
**Problem:** The alt map keys are suffixed (`"guillotine choke"`) but ADCC-only entries use the short form (`"guillotine"`). The `n` variable (`src.lower().strip()`) doesn't match the alt key, so no alternate names are generated.

**Affected entries:**

| Entry | ADCC Wins | Variations Count | Root Cause |
|-------|-----------|-----------------|------------|
| Guillotine | 34 | 1 | Key `"guillotine choke"` ≠ `"guillotine"` |
| Triangle | 24 | 2 | Key `"triangle choke"` ≠ `"triangle"` |
| Ezekiel | 1 | 1 | Key `"ezekiel choke"` ≠ `"ezekiel"` |
| Headlock | 2 | 1 | No alt key at all |
| Cross Face | 1 | 1 | No alt key at all |
| Leg Lock | 2 | 1 | No alt key at all (footlock ≠ leg lock) |

**Additionally,** the following ADCC-only entries have zero alt map entries despite having the same norm-as-key issue (their alt entries simply don't exist):

| Entry | Key Missing From Alt Map |
|-------|-------------------------|
| Katagatame | `"katagatame"` |
| Footlock | `"footlock"` (despite line 435 having `"footlock"` — wait, let's verify) |

**Verification of footlock alt key:**
```python
# Line 435:
"footlock": ["chave de pe", "foot lock"],
```
The key exists. The issue: the technique named "Footlock" has `n = "footlock"` which DOES match this key. So footlock should have 3 variations. Let me verify...

Actually, I need to re-examine. The `_generate_variations` function is called for each entry in `build_technique_library`. The footlock entry is created with `name_en = sub_name.title()` where `sub_name` is the normalized ADCC name `"footlock"`. So `name_en = "Footlock"`, and in `_generate_variations`, `n = "footlock"` matches the key at line 435. So footlock should get 3 variations. Let me verify from the actual output.

Let me check the current output for footlock:

```json
{
  "translations": { "en": "Footlock", "pt": "Chave de Pé" },
  "variations": ["footlock", "chave de pe", "foot lock"]
}
```

Actually, this depends on whether footlock was already in the dataset. If footlock was in the grappling_techniques dataset, its `_generate_variations` call would use `name_en` from the dataset row, not from ADCC. In the dataset, footlock's name would be `"Footlock"` which normalizes to `"footlock"` — same result. So footlock should be fine.

The real issue is:
- **Guillotine** (34 wins, 1 variation) — key mismatch
- **Triangle** (24 wins, 2 variations) — key mismatch 
- **Katagatame** (7 wins, 1 variation) — no alt entry
- **Headlock** (2 wins, 1 variation) — no alt entry
- **Leg Lock** (2 wins, 1 variation) — no alt entry
- **Cross Face** (1 win, 1 variation) — no alt entry
- **Ezekiel** (1 win, 1 variation) — key mismatch
- Various other low-count ones

**Fix:** Add to the `alts` dict:

```python
"guillotine": ["guilhotina", "guillotine choke"],
"triangle": ["sankaku jime", "triangulo", "triangle choke"],
"ezekiel": ["ezekiel choke", "ezequiel"],
"katagatame": ["kata gatame", "shoulder choke"],
"headlock": ["gravata", "head lock"],
"leg lock": ["chave de perna", "leglock"],
"cross face": ["pressao facial", "crossface"],
"north south choke": ["north-south", "norte sul", "kuzure kami shiho gatame"],
# Additional ADCC-only entries
"dogbar": ["dog bar"],
"estima lock": ["estima lock"],
"shoulder lock": ["chave de ombro", "shoulder crank"],
"twister": ["spinal lock", "body twister"],
"wristlock": ["chave de pulso", "wrist lock"],
"z lock": ["z-lock"],
```

### 2.5 Redundant ADCC Type Branching (P2)
**File:** `export/tech_library.py:366-375`  
**Problem:** All branches of the type mapping return `"submission"`. The if/elif/else structure is meaningless.  
**Fix:** Replace with:

```python
app_type = "submission"
```

### 2.6 Normalization Key Gap in `_generate_variations` (P3)
**File:** `export/tech_library.py:418-436`  
**Problem:** The `alts` dict keys must match the lowered technique name. Currently, some keys use suffixed forms while ADCC-only entries use short forms. No validation exists to catch keys that will never match any entry.  
**Fix:** After implementing the alt entry additions above, consider adding a debug assertion that checks every library entry's lowered name against the `alts` keys (optional).

---

## 3. Verified Correct Behavior

The following aspects were audited and confirmed working:

| Check | Status |
|-------|--------|
| Effectiveness score calculation | ✅ Composite formula correct |
| Min threshold (3 ADCC wins) | ✅ Floor applied correctly |
| Alias resolution (`RNC` → `Rear Naked Choke`) | ✅ Working for 11 aliases |
| `_name_in_nodes` cross-reference | ✅ Correctly identifies 38 entries as existing |
| Origin suffix stripping | ✅ Removes `(Wrestling)`, `(BJJ)`, `(Judo)` |
| Heel hook variant merging | ✅ 52 total across 3 variants merged |
| ruff compliance | ✅ Clean (0 errors) |
| mypy strict | ✅ Clean (0 errors) |

---

## 4. Refinement Implementation Plan

### Step 1 — Dead Code Removal
**Files:** `export/tech_library.py`
- Delete lines 284-295 (`existing_names_lower`)
- Delete lines 348-357 (`tech_names`)

### Step 2 — Missing PT Translations
**Files:** `export/tech_library.py`
- Add 10 keys to `DEFAULT_PT_TRANSLATIONS` dict (section 2.3 table)

### Step 3 — Missing Variation Alt Entries
**Files:** `export/tech_library.py`
- Add 14 entries to `alts` dict in `_generate_variations` (section 2.4 table)

### Step 4 — Redundant Branching
**Files:** `export/tech_library.py`
- Replace lines 366-375 with `app_type = "submission"`

### Step 5 — Verify
```bash
uv run ruff check . && uv run mypy .
uv run python -c "from dotenv import load_dotenv; load_dotenv(); from export.tech_library import export_tech_library; s = export_tech_library(); print(s)"
# Manual spot-check:
#   - Guillotine → PT "Guilhotina", variations >= 2
#   - Triangle → PT "Triângulo", variations >= 2
#   - Headlock → PT "Gravata", variations >= 2
#   - All 11 PT fixes verified in JSON output
```

### Step 6 — Regenerate Output
```bash
uv run python -c "from dotenv import load_dotenv; load_dotenv(); from export.tech_library import export_tech_library; export_tech_library()"
```

### Step 7 — Commit
```
git add export/tech_library.py && git commit -m "refine: technique library audit fixes

- Remove dead code (existing_names_lower, tech_names)
- Add 10 missing PT translations for ADCC-only entries
- Add 14 variation alt entries (Guillotine, Triangle, Katagatame, etc.)
- Simplify redundant ADCC type branching
- ruff+ mypy clean"
```

---

## 5. Remaining Work (Future Phases)

| Phase | Description | Depends On |
|-------|-------------|------------|
| 3 | `export/adcc_elo_table.py` — ADCC ELO ratings, K-factor calibration | This refinement |
| 4 | `cv/vicos_exploration.py` — ViCoS pose keypoints parsing, class distribution | Phase 3 |
| 5 | Vector DB — competitor technique maps, similarity search, archetype clustering | Phase 4 |

---

## Appendix A: Current Top-20 Effectiveness Table

```
| Rank | Technique              | Score | Count | Stage Depth | Finals Rate | Weight Classes |
|------|------------------------|-------|-------|-------------|-------------|----------------|
|  1   | Rear Naked Choke       | 0.734 |   99  | 2.81        | 8%          | 7              |
|  2   | Armbar                 | 0.627 |   68  | 2.65        | 3%          | 8              |
|  3   | Guillotine             | 0.509 |   34  | 2.88        | 9%          | 7              |
|  4   | Heel Hook              | 0.504 |   52  | 2.87        | 10%         | 6              |
|  5   | Toe Hold               | 0.462 |   10  | 3.30        | 20%         | 7              |
|  6   | Kimura                 | 0.409 |   13  | 2.77        | 8%          | 6              |
|  7   | Americana              | 0.382 |    3  | 3.67        | 33%         | 2              |
|  8   | Darce Choke            | 0.380 |    6  | 3.17        | 17%         | 4              |
|  9   | Triangle               | 0.375 |   24  | 2.67        | 8%          | 5              |
| 10   | Footlock               | 0.368 |   18  | 2.83        | 0%          | 6              |
| 11   | Kneebar                | 0.355 |   17  | 2.65        | 0%          | 6              |
| 12   | North South Choke      | 0.350 |    3  | 3.67        | 33%         | 3              |
| 13   | Katagatame             | 0.271 |    7  | 2.43        | 0%          | 4              |
| 14   | Omoplata               | 0.250 |    3  | 2.67        | 0%          | 3              |
| 15   | Headlock               | 0.020 |    2  | 2.00        | 0%          | 1              |
| 16   | Leg Lock               | 0.020 |    2  | 4.50        | 50%         | 2              |
| 17   | Calf Slicer            | 0.020 |    2  | 3.00        | 0%          | 2              |
| 18   | Anaconda               | 0.010 |    1  | 3.00        | 0%          | 1              |
| 19   | Cross Face             | 0.010 |    1  | 3.00        | 0%          | 1              |
| 20   | Dogbar                 | 0.010 |    1  | 1.00        | 0%          | 1              |
```
