# PoC-E0 — reading of the first run

Date: 2026-08-23. Companion to the generated `e0.md` (tables; regenerate with
`uv run python -m analysis.poc.e0_rating_eval` — do not hand-edit that file).
Corpus: the scouting records (689 scored of 707 distinct bouts, 2008–2026,
first corpus year burned in). Harness: `analysis/poc/e0_rating_eval.py`,
11 tests in `tests/test_poc_e0.py`.

## Headline numbers (overall slice)

| engine | log loss | accuracy |
|---|---|---|
| Elo K=40 × win-type × stage | **0.5174** | 0.747 |
| Elo K=40 flat | 0.5263 | 0.744 |
| win-rate baseline | 0.5774 | 0.718 |
| Glicko-2 (yearly periods, any τ) | 0.5795 | 0.679 |
| constant 0.5 | 0.6931 | 0.500 |

## What the run actually says

1. **The τ sweep is a null result, as predicted.** τ ∈ {0.2, 0.5, 0.8, 1.2}
   agree to four decimals. With at most a handful of rated bouts per athlete per
   year, the volatility machinery never engages — exactly FightMatrix's finding
   on sparse MMA schedules (see `04_BIBLIOGRAPHY.md` §C). τ=0.5 is fine; tuning
   it further is wasted effort **on this corpus**.

2. **Period granularity dominates everything Glicko.** The configured engine
   (calendar-year periods, ADR-04) trails even the win-rate baseline overall.
   The mechanism is structural, not a bug: within year Y, Glicko-2's predictions
   use states frozen at Y−1 close, while Elo updates after every bout — and on a
   corpus where most opponents appear once and information is scarce, incorporating
   a result *immediately* is worth more than modelling uncertainty about it. The
   next Glicko variant to test is **per-event (or per-bout) periods**, which is a
   one-line change in the harness — NOT a config change to production, which
   would be a new `engine_version` under ADR-02.

3. **The win-type/stage multipliers earn their keep, mildly.** 0.5174 vs 0.5263
   flat — small but consistent with the felixgnwn design intent. A K sweep by
   predictive loss (replacing `calibrate_k_factor`'s target-σ criterion) is the
   obvious next cell in the grid.

4. **Absolute numbers carry the corpus's bias.** This corpus is ego-centric
   (16 rostered athletes + their opponents' recovered rows) and 69% roster-wins,
   so "predict the roster athlete wins" is a strong prior every learning engine
   partially absorbs — the cold-start slice scoring *better* than the experienced
   slice is that prior showing through, not a general property of the engines.
   Between-engine comparisons stand (same stream for all); absolute quality
   judgements wait for a `--source db` run against the closed `matches` corpus.

## Decisions recorded

- **Harness: ACCEPTED** (pre-registered criterion — Elo beats both baselines —
  met; prequential leak test and metric sanity tests pin the instrument).
- **No production rating change from this run.** The corpus bias caveat is
  disqualifying for engine *selection*; the run's job was to prove the
  instrument and it did. It also already earned its keep: it falsified "τ needs
  tuning" and localised Glicko-2's weakness to period granularity, neither of
  which was knowable from spread-based arguments.

## Next actions (in order)

1. `--source db` loader (needs a `DATABASE_URL` environment): the `matches`
   table is a closed corpus and the graph-growth engine (`athlete_elo`) can
   join the comparison there.
2. Per-bout-period Glicko-2 variant in the sweep (harness-side experiment).
3. K sweep for Elo by log loss; retire `calibrate_k_factor`'s target-σ mode if
   the sweep contradicts it (expected).
4. WHR (PoC-E3) enters through this same harness.
