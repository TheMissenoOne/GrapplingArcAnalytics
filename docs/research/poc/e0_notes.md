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

## 2026-08-24 — db run, per-bout Glicko, K sweep

Ran the three remaining next actions above, plus the regression re-check. Both runs
labelled in the regenerated `e0.md` (`--source scouting` then `--source db`, merged via
`--source all`); `analysis/poc/e0_rating_eval.py` now also has `--source db`.

**Regression (`--source scouting`) — CONFIRMED.** Every row recorded in the headline table
above matches the new run to the last printed digit (elo-k40 0.5174, elo-k40-flat 0.5263,
win-rate 0.5774, glicko2-tau\* 0.5794–0.5795, constant-0.5 0.6931). The only additions are
the new `glicko2-perbout-tau0.5` row and the K-sweep table — the instrument didn't move.

1. **`--source db`, closed corpus: 732 of 864 `matches` rows eligible** (dropped: 56
   DRAW/DQ/unset `win_type`, 76 `DECISION` rows scraped without a recorded `winner_id`, 0
   missing year). Elo K=40×mult still wins (0.6110), but the margin over win-rate and yearly
   Glicko-2 shrinks hugely versus the scouting run — this closed corpus has no "predict the
   rostered athlete" prior to lean on, and the harder underlying problem (a wide field, most
   opponents met once) pulls every engine's log loss up toward ln 2. `athlete-elo` (the real
   graph-growth engine, wired via `AthleteEloEngine` — a predict-then-update adapter that
   re-replays each athlete's own history through the unmodified `athlete_elo.replay_matches`
   on every bout, not a reimplementation) comes in LAST among the learning engines: 0.6759,
   worse than the win-rate baseline (0.6417). It is tuned to grow a per-node dossier rating
   toward a rank-ELO target, not to minimize win-probability log loss, and its K schedule
   (belt floor, gap-to-target, 2.5× competitive multiplier) was never fit against this
   criterion — exactly the kind of gap ADR-03 exists to surface: an engine can be well-designed
   for its actual job and still lose badly on a criterion it was never aimed at.

2. **Per-bout-period Glicko-2 closes most of the gap, as predicted.** Scouting: yearly 0.5795
   → per-bout 0.5060 — the first Glicko variant to beat elo-k40 (0.5174) outright, and it also
   leads the cold-start slice (0.4897 vs elo-k40's 0.5019). DB: yearly 0.6238 → per-bout
   0.6117, essentially tying elo-k40 (0.6110) and clearing elo-k40-flat and win-rate. Confirms
   the diagnosis above: period granularity, not τ, was the yearly variant's problem — closing a
   rating period after every bout instead of freezing a whole calendar year recovers the
   "update immediately" advantage Elo had.

3. **K sweep (K ∈ {10,20,30,40,60,80}, flat and win-type/stage-multiplied, by log loss).** On
   BOTH corpora log loss decreases monotonically across the whole grid — best-in-grid is K=80
   (mult) on scouting (0.4997) and on db (0.6015), not the production K=40. Neither corpus
   shows a minimum inside [10,80]; a wider sweep (e.g. 100–160) is needed before "higher K
   wins" is a finding rather than an artifact of a truncated grid. **This does contradict
   `calibrate_k_factor`'s production default** (K=40, chosen by grid-searching rating spread
   against a target std-dev, not held-out prediction) **in direction, not yet in magnitude** —
   per ADR-03 discipline, no production K change is recommended from this run alone; extend the
   grid first. Win-type/stage multipliers earn their keep at every K on both corpora (mult
   always beats flat).

### Decisions recorded (update)

- **No production rating change.** Confirmed again on the closed corpus.
- **Per-bout-period Glicko-2 is the interesting engine now** — it ties or beats Elo on both
  corpora with the same `rating_v2` math, just a different replay cadence. Worth its own
  PoC-style writeup before touching `engine_version`/ADR-02: "one line in the harness" becoming
  "a new production replay cadence" is a bigger claim than this run alone supports.
- **`athlete_elo`'s production K schedule is validated for its actual job** (dossier growth
  toward a rank target), **not for win-probability prediction** — the db run is the first
  external check of that boundary, and it holds: worst learning-engine log loss on the corpus,
  exactly where an engine optimized for something else should land.
- K sweep needs a wider grid (>80) before it says anything actionable about production K;
  `calibrate_k_factor`'s target-σ mode is not retired by this run, only put on notice.
