# Deepseek QA — verify the name-normalization fix restored dropped events

`_clean_events` / `_derive_opponent` (`scripts/insert_ufc_matches.py`) now use
`athlete_key()` for **every** name comparison (key-derived names AND event actors
AND winner), so `[H:MM:SS]` timestamps and accents/aliases collapse identically on
both sides. Previously a timestamp baked into a name (`"Cyborg [1:35:45]"`) survived
`_normalize_name` and mismatched, dropping the event as "unknown actor".

Your job: confirm the fix is complete and didn't over-merge distinct people.

## Inputs (attach)
- `scripts/insert_ufc_matches.py` (`_clean_events`, `_derive_opponent`)
- `analysis/names.py` (`athlete_key`, `_normalize_name`, `clean_athlete_name`, `ATHLETE_ALIASES`)
- The 6 timestamped dumps: `scripts/dumps/{adcc2022_abs,adcc2024_abs,adcc2022_99kg,adcc2022_88kg,ibjjf2023_worlds,wno20}_data.py`

## Checks
1. **Zero timestamp drops.** For each dump, every event whose actor name carries a
   `[H:MM:SS]` must now resolve to side a or b (no "unknown actor" drop). Confirm the
   named athletes (Gordon Ryan, Nick Rodriguez, Giancarlo Bodoni, Cyborg/Abreu,
   Vagner Rocha, Felipe Pena, Ffion Davies, Diogo Reis, Mica Galvão) keep their events.
2. **Both sides cleaned.** Verify NO remaining `_normalize_name(...)` call inside
   those two functions compares a name without `athlete_key` — a one-sided clean
   would silently drop the same-timestamp fights (the earlier regression).
3. **No false merges.** `athlete_key` applies `ATHLETE_ALIASES` + de-accent. Confirm
   no two *distinct* athletes in these dumps now collapse to the same key (which
   would wrongly fuse their graphs). Spot-check accented names (Galvão, Meregali).
4. **Residual drops are legitimate.** A handful of events still drop (~5) from
   ABBREVIATED names ("R. Lovato Jr." vs winner "Rafael Lovato Jr.", "N. Jesus" vs
   "Nathiely de Jesus") where the opponent isn't recovered. Confirm these are the
   abbreviation/opponent-derivation issue (a separate, pre-existing bug), NOT the
   timestamp bug — i.e. none involve a `[H:MM:SS]`.

## Output
`<dump>: <check#> PASS|FAIL — reason`. Flag any timestamped actor still dropping, any
one-sided `_normalize_name` left in the two functions, and any two distinct athletes
that now share an `athlete_key`.
