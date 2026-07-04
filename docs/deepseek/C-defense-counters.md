# Deepseek QA — ELO-adjusted Defense Rate + Counter Moves

Two new analysis engines, wired into the fighter dossier (`export/site_data.py`).

## Inputs (attach)
- `analysis/defense_rate.py`
- `analysis/counter_moves.py`
- `analysis/path_to_victory.py` (`edge_ptv`, `path_to_victory`)
- `export/site_data.py` (the `build_fighters` compute + `render_profile_page` render)

## Defense Rate checks
1. **Only opponent attempts count.** `elo_adjusted_defense_rate` must skip events where
   `actor_id == athlete_id` — a defense stat built from the athlete's OWN attacks is wrong.
2. **`successful is False` = defended.** Undefined `successful` must default to *landed*
   (not defended), matching the app convention. Confirm `is False` (not falsy) so a
   missing key isn't miscounted as a defense.
3. **ELO weighting.** Each attempt is weighted by `opponent_input_elo`; the rate is
   `Σ(elo·defended) / Σ(elo·attempts)`. Confirm the weight cancels correctly (a category
   defended equally across ELOs yields that raw rate).
4. **None on no data.** A category the opponent never attempted → `rate: None`, not 0.0
   (0.0 would read as "never defends" — a lie).

## Counter Moves checks
5. **Ranked by landing value.** For each node, responses are sorted by `edge_ptv(n→b)`
   descending — the counter that leads to the highest-PtV position ranks first.
6. **`min_count` filters noise.** One-off transitions (weight < min_count) are dropped so
   a single fluke response isn't surfaced as a "counter".
7. **`leads_to` is the response's own best next move**, not a re-listing of the counter.
8. **No leakage into the render.** The dossier shows counters ranked by PtV value only —
   confirm no raw PtV number is printed (contract: structure/labels, never raw PtV).

## Output
`<check#>: PASS|FAIL — reason`. Flag any own-attempt leaking into defense, any falsy-vs-
`is False` bug, any counter list not sorted by edge PtV, and any raw PtV shown on the page.
