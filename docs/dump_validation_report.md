# Dump validation report — malformed athlete names

Read-only audit, 2026-07-07. Scope: every `scripts/dumps/*_data.py` (58 dumps), checked against
what actually reaches the DB — not just raw-string pattern matching.

## Method

`scripts.dump_import.build_matches(raw, clean=False)` is the real importer entry point
(`reprocess_all.py` calls it). Its output `CanonicalMatch.a_name` / `.b_name` is exactly what
`run_dump()`'s `resolve()` feeds into `analysis.names.clean_athlete_name()` → `Athlete.name`.
So this audit ran `build_matches()` on every dump's `RAW` and flagged the **resolved**
`a_name`/`b_name` — not raw dict keys/`opponent` fields in isolation, since a dirty `opponent`
string is often never used (b_name is derived from `winner` or event actors first, `opponent`
is only a last-resort fallback in `dump_import.build_matches`).

`clean_athlete_name()` already strips `[H:MM:SS]` transcript timestamps and spaced `'nickname'`
quotes — nothing else. So parens, colon-clauses, digit-suffixes, stage-label prefixes, and
country/team words all still land in the DB as-is.

Calibration check (per task): `polaris_bjj_squads_..._data.py` — confirmed known-bad, 18/18
bout keys malformed, worst dump by far.

## Summary

| | count |
|---|---|
| Dumps affected (real, DB-reaching) | 11 / 58 |
| Real malformed name entries | 69 |
| Cosmetic-only (bracket timestamp, already neutralized) | 29 — **no action needed** |
| Duplicate pair+year keys found | 8 total — **2 real data-loss risk**, 6 harmless (identical duplicate blocks) |

### Worst dumps (real, DB-reaching malformed count)

| Dump | malformed | notes |
|---|---:|---|
| `polaris_bjj_squads_team_usa_vs_team_uk_ireland_grappling_full_event_data.py` | 36 | every one of 18 bouts: `a_name` unbalanced-open-paren (`Match N (Name`), `b_name` unbalanced-close-paren (`Name)`). 100% of the dump. Worst by a wide margin. |
| `ufc_320_free_fight_marathon_data.py` | 8 | `(Encore/Replay)` / `(Additional segment)` annotations + `Name 2` digit-suffix disambiguators |
| `supercut_the_entire_2024_adcc_worlds_65kg_bracket_data.py` | 7 | `(Opening Round)` / `(Semifinal)` / `(Final)` / `(Bronze Medal Match)` round labels leaked into `opponent` |
| `ufc_324_free_fight_marathon_data.py` | 4 | full sentence/description leaked as name: `"Marlon 'Chito' Vera: The bantamweight championship title defense begins at 33"` |
| `ufc_327_free_fight_marathon_data.py` | 4 | `"Name: Starts at"` — truncated sentence fragment |
| `pgf_world_2026_week_5_regular_season_finale_data.py` | 3 | team/description garbage: `"Las Vegas Kings representative"`, `"unnamed opponent (likely subset of Phenoms vs. Kings)"` |
| `musumeci_data.py` | 2 | `(UFC BJJ 3)` / `(UFC FPI 9)` show labels |
| `pgf_world_2026_week_3_...` | 2 | `(Rematch)`, `(Mention/Stats)` |
| `pgf_world_2026_week_1_...` | 1 | `(Squires)` — team annotation (NOT the real athlete "Joshua Squires", who is clean elsewhere — false-positive checked and excluded) |
| `ruotolos_data.py` | 1 | `(Rematch)` |
| `ufc_328_free_fight_marathon_data.py` | 1 | `(Middleweight Championship)` |

## Full detail

| dump | field | malformed value | issue | suggested clean name |
|---|---|---|---|---|
| polaris_bjj_squads | a_name (×18) | `Match 1 (Jon Blank` … `Match 17 (Roberto Jimenez`, `Heavyweight Super Fight (Sylvia Nastasa` | unbalanced_paren, stage_label_prefix, digits | `Jon Blank`, `Adam Benayoun`, `Roberto Jimenez`, `Nick Ronan`, `Nathan Orchard`, `Gio Martinez`, `Hunter Colvin`, `Sylvia Nastasa` (repeats across Match 1–17) |
| polaris_bjj_squads | b_name (×18) | `Dan Strauss)`, `Kieran Davern)`, `Jed Hue)`, `Ross Nicholls)`, `Darragh O'Connail)`, `Bradley Hill)`, `Ellis Younger)`, `Ben Dyson)`, `Kyle Bame)` | unbalanced_paren | same names, strip trailing `)` |
| musumeci_data.py | b_name | `Carrasco (UFC BJJ 3)` | digits, parenthetical | `Carrasco` |
| musumeci_data.py | b_name | `Machado (UFC FPI 9)` | digits, parenthetical | `Machado` |
| pgf_world_2026_week_1 | b_name | `Jonathan Wilson (Squires)` | parenthetical, team word | `Jonathan Wilson` |
| pgf_world_2026_week_3 | b_name | `Frank (Rematch)` | parenthetical | `Frank` |
| pgf_world_2026_week_3 | b_name | `Armin Bruni (Mention/Stats)` | parenthetical | `Armin Bruni` |
| pgf_world_2026_week_5 | b_name | `Eric (Twisters vs. Kings)` | parenthetical, team word | `Eric` |
| pgf_world_2026_week_5 | b_name | `Las Vegas Kings representative` | team word, not a name | no clean suggestion — placeholder, not a real athlete; drop the bout or find the real name from the transcript |
| pgf_world_2026_week_5 | b_name | `unnamed opponent (likely subset of Phenoms vs. Kings)` | unnamed, parenthetical, team words | same — drop or re-source |
| ruotolos_data.py | b_name | `P.J. Barch (Rematch)` | parenthetical | `P.J. Barch` |
| supercut_2024_adcc_65kg | b_name (×7) | `Bia Mesquita (Opening Round)`, `Amanda Levy (Opening Round)`, `Morgan Black (Opening Round)`, `Anna Karolina Vieira (Semifinal)`, `Helena Crevar (Semifinal)`, `Brianna Ste-Marie (Bronze Medal Match)`, `Anna Karolina Vieira (Final)` | parenthetical | strip round label |
| ufc_320 | b_name | `Magomed Ankalaev 2` | digits | `Magomed Ankalaev` |
| ufc_320 | b_name | `Johnny Walker 2` | digits | `Johnny Walker` |
| ufc_320 | b_name (×5) | `Magomed Ankalaev 2 (Encore/Replay)`, `Merab Dvalishvili (Encore/Replay)`, `Deiveson Figueiredo (Encore/Replay)`, `Johnny Walker 2 (Encore/Replay)`, `Jamahal Hill (Encore/Replay)`, `Magomed Ankalaev 2 (Additional segment)` | parenthetical (+digits) | strip label/digit |
| ufc_324 | b_name | `Rafael Fiziev: This fight is featured at the beginning of the video` | colon_clause | `Rafael Fiziev` |
| ufc_324 | b_name | `Paddy Pimblett: This matchup begins at 17` | colon_clause, digits | `Paddy Pimblett` |
| ufc_324 | b_name | `Marlon 'Chito' Vera: The bantamweight championship title defense begins at 33` | colon_clause, digits | `Marlon 'Chito' Vera` |
| ufc_324 | b_name | `Ricky Simon: This main event starts at 1` | colon_clause, digits | `Ricky Simon` |
| ufc_327 | b_name (×4) | `Khalil Rountree Jr.: Starts at`, `Dominick Reyes: Starts at`, `Roman Kopylov: Starts at`, `Aleksandar Rakic: Starts at` | colon_clause | strip `: Starts at` |
| ufc_328 | b_name | `Du Plessis (Middleweight Championship)` | parenthetical | `Du Plessis` |

## Duplicate participant-pair+year keys

Real risk — silently drops a bout's data on collapse (`build_matches` keeps whichever entry it
sees first for a `(frozenset(participants), year)` key; the rest are discarded):

| dump | pair | year | keys in dump | risk |
|---|---|---|---|---|
| `craigjones_data.py` | Craig Jones vs Kyle Bame | 2025 | two `('Craig Jones', 2025)` blocks, both resolving to this pair | **event counts differ: 13 vs 3** — one version is far more complete; the 3-event version wins arbitrarily by dict iteration order and the 13-event one is silently dropped |
| `pgf_world_2026_week_5_regular_season_finale_data.py` | Derek Rayfield vs Jake Strauss | 2026 | `('Derek Rayfield', 2026)` @ `start: '3:43:46'` and `('Jake Strauss', 2026)` @ `start: '4:22:14'` | **event counts differ: 2 vs 5** — two separate transcript timestamp windows of the same bout, keyed by whichever fighter's name led that segment; the 2-event version wins, the fuller 5-event one is dropped |

Harmless (found, but content is byte-identical — dedup drops an exact copy, no data lost):

- `ufc_matches_data.py` — `RAW` contains its first 3 blocks (5 bouts each) **triplicated
  verbatim** (Charles Oliveira/Max Holloway 2015, Dustin Poirier/Max Holloway 2025, Mateusz
  Gamrot/Charles Oliveira 2023, Paul Craig/Caio Borralho 2024, Reinier de Ridder/Bo Nickal 2025),
  plus Dricus du Plessis/Khamzat Chimaev 2025 duplicated once more later in the file. Structural
  copy-paste artifact in the source dump, not a name-malformation issue — worth a cleanup pass on
  `ufc_matches_data.py` (drop the 2 redundant blocks) but zero data-loss risk.

## False positives checked and excluded

- **`Joshua Squires`** (pgf week 2/3/4) — real athlete surname, not a team leak. Only flagged
  when it appears as `Jonathan Wilson (Squires)` (an actual parenthetical annotation elsewhere).
- Bracket-timestamp names (`Giancarlo Bodoni [2:01:54]`, `Lucas 'Hulk' Barbosa [2:10:33]`,
  `M. Galvão [2:11:18]`, etc. — 29 entries across `adcc2022_*`, `adcc2024_abs`,
  `ibjjf2023_worlds`, `wno20`) — cosmetic only. `analysis.names.clean_athlete_name()` already
  strips `[H:MM:SS]` before the name reaches the DB. **No action needed.**
- Dirty `opponent` field text that never reaches `CanonicalMatch` (e.g. `craigjones_data.py`'s
  `opponent: 'Richie Martinez (Quintet 3)'`, `leandro_lo_data.py`'s `opponent: 'Felipe (Cesar)'`)
  — `_derive_opponent()` resolves `b_name` from the clean `events[].actor` field first, so the
  dirty `opponent` string is dead text in the source, not a DB risk. (It *would* become a risk if
  that bout's events list were ever empty — worth a lint pass, not urgent.)

## Recommendation

**General import-time sanitizer, not per-dump hand edits.**

The malformed cases cluster into a small number of *mechanical* shapes, all seen across multiple
independent dumps (different transcripts, different months) — this is a systemic pattern in how
dumps get produced, not one-off typos:

1. parenthetical annotation (round/team/segment label) → strip `\s*\([^)]*\)\s*$` (and the
   unbalanced-open/close-paren variant for the Polaris case)
2. leaked stage/match-number prefix (`Match N (`, `Heavyweight Super Fight (`) → strip
   `^(match\s*\d+|...)\b` prefix
3. trailing digit-suffix disambiguator (`Name 2`) → strip `\s+\d+\s*$`
4. sentence/description colon-clause (`Name: This fight...`) → keep only the part before `:`

A `clean_athlete_name()`-style guard (in `analysis/names.py`, run inside `build_matches()` right
alongside the existing `[H:MM:SS]`/nickname stripping) would catch all 69 real entries and any
future dump that repeats the same transcript-refiner sloppiness, instead of hand-fixing 11 files
now and re-fixing the same shapes again next batch. Two things a sanitizer can't safely
auto-fix and still need human judgment:
- `Las Vegas Kings representative` / `unnamed opponent (likely subset of Phenoms vs. Kings)` —
  not malformed *names*, they're missing names; no regex recovers the real athlete.
- The 2 real duplicate-pair collapses (Craig Jones/Kyle Bame, Rayfield/Strauss) — a sanitizer
  can't merge two different event-lists into one bout; that needs a human to pick (or splice)
  the fuller transcript segment before the next `reprocess_all` run.

Separately: `ufc_matches_data.py`'s triplicated blocks are worth a quick manual dedup pass (delete
the two redundant copies) since they bloat the file for no reason, though they cause no data loss.
