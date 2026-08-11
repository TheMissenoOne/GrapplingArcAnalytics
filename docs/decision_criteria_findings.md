# Decision criteria — first measured run (2026-08-10)

`analysis/decision_criteria.py` answers "which opponent condition actually governs the
response the athlete chooses". This is the result of pointing it at the real corpus.

**Nothing survives the gates today. The blocker is not corpus size — it is that 91% of
exchanges record no opponent condition at all.**

## Per-athlete: not viable

Gordon Ryan, the most-documented athlete in the DB:

| | |
|---|---|
| contexts | 52 |
| usable observations (action → condition → response) | **45** |
| largest single context | 8 |
| contexts reaching the 8-observation minimum | 1 |
| triads testable | **0** |

The one context that reached the minimum carried the same condition on every exchange, so
there was nothing to contrast. Per-athlete decision criteria are not a thin signal here;
they are not computable.

## Pooled corpus: computable, but starved of conditions

All 684 final matches, both athletes' perspectives:

| | |
|---|---|
| raw decision patterns | 3760 |
| usable (a response was observed) | 2572 |
| **carrying any opponent condition** | **232 (9.0%)** |
| distinct contexts | 604 |
| contexts ≥ 8 observations | 74 |
| triads tested | 118 |
| **triads surviving** | **0** |

Failing gate: 87 had a credible interval including zero, 31 failed Benjamini-Hochberg FDR.

Strongest candidate, and the only substantive one:

```
context   -|takedown
condition cond:stands  ->  response  takedown
effect +0.70   ci_lo +0.29   stability 0.99   n 7/8   matches 4   q 0.26
```

A real-looking pattern with a large effect that is stable under match resampling — and it
still fails FDR at q=0.26, because 118 simultaneous tests is enough that one 7-of-8 result
is not surprising. That is the correction working, not the correction being too harsh:
uncorrected, this would have been reported as p≈0.01 and shipped as a "decision criterion".

Everything else in the top 12 is n=1/1 — a single exchange in a single match.

## Coarsening the condition space does not rescue it

A condition ontology was authored (`CONDITION_FAMILIES` in `analysis/opponent_conditions.py`)
and the composite-key ordering bug fixed, specifically to test whether concentrating evidence
would let criteria clear the gates. It does not:

| condition level | triads tested | survive |
|---|---|---|
| raw condition keys | 118 | **0** |
| order-normalized composites | 118 | **0** |
| rolled up to families | 112 | **0** |

Family coverage went from 22 of 232 observations to **204 of 232** in the process, so the
grouping is now real and the answer is not an artifact of a bad ontology. There simply is not
enough condition-carrying evidence at any level of abstraction.

Two defects were found and fixed on the way, both worth knowing about:

- **The authored families initially covered the wrong vocabulary.** The 18 curated postural
  conditions (`cond:sprawls`, `cond:hand-fight`) barely occur — only `cond:stands` appears, 22
  times. 210 of 232 condition-carrying observations are `cond:opponent-<event type>` fallbacks.
  The families now cover both.
- **Composite bundle keys are order-dependent.** `classify_opponent_condition` builds a
  composite in event order, so one pair of opponent events yields two keys:
  `opponent-attacks-and-opponent-passes` (5 observations) and
  `opponent-passes-and-opponent-attacks` (4) are the same condition recorded twice.
  `normalize_condition_key` merges them **in the analysis layer only** — fixing it in the
  classifier would change the published flowchart payload, which card 019's regression guard
  requires to stay byte-identical.

## Update 2026-08-10b — chain conditioning, volume floors, and why runs matter

Two changes since the first run, and the second one mattered more than the first.

**Volume floors as a pre-filter (the bigger win).** `MIN_TRIAD_N=3`, `MIN_TRIAD_MATCHES=2`,
`MIN_TRIAD_OPPONENTS=2`, applied *before* testing so thin triads never enter the FDR family.
This did not merely exclude junk — it rescued the one genuine candidate. `cond:stands → takedown`
(n=7/8, 4 matches) went from **q=0.26 to q=0.056** purely because 114 one-off triads stopped
inflating the correction against it. It now survives. Excluding one-offs is not just hygiene;
carrying them was actively suppressing the real signal.

**Chain conditioning** (`extract_chain_patterns`): every opponent event between two of the
athlete's own moves becomes the condition, including `guard`/`control`, never dropped.

| | patterns | conditioned | coverage | candidates | below floor | tested | **survive** |
|---|---|---|---|---|---|---|---|
| window (old) | 3760 | 232 | 9.0% | 118 | 114 | 4 | **1** |
| chain (new) | 7728 | 1152 | 14.9% | 779 | 763 | 16 | **1** |
| chain + families | 7728 | 1152 | 14.9% | 749 | 730 | 19 | **1** |

Conditioned observations rose **5×** (232 → 1152). Surviving criteria did not move. 98% of the
new candidates fall below the volume floor — denser conditioning converted "no condition
recorded" into "a condition recorded once", exactly the risk the plan flagged.

## Why the chain cannot do better: the sequences are not alternating

The cycle metrics (`analysis/exchange_cycles.py`) explain the ceiling:

| | |
|---|---|
| exchanges / runs | 2076 / 5206 |
| mean run length | **3.53** |
| **max run length** | **70** |
| runs of length 1 (true alternation) | **44.3%** |
| runs of 2+ | 55.7% |
| revisits | 5644 (**2.72 per exchange**) |
| most repeated positions | back control, triangle choke, half guard, mount, arm drag |

Chain conditioning can only see an opponent move if one was *recorded* between two of the
athlete's. It usually was not: **55.7% of runs are one side acting 2+ times uninterrupted**, and
a single run reaches **70 consecutive events by one athlete**. A run of 70 is not grappling, it
is a transcript attributing a long stretch to one actor — so part of the remaining gap is a data
defect, not a modelling one, and it is worth a look at the worst offenders before any further
statistical work.

(own and opponent figures are identical by construction here — both perspectives of every match
are pooled, so each match contributes both sides. Per athlete they diverge, and that divergence
is the initiative/defensive-burden signal.)

## What is actually blocking

The extractor only records a condition when opponent events fall between the athlete's
action and their response. In this corpus that happens 9% of the time. The remaining 91% of
exchanges are `action → response` with nothing observed in between, so there is no candidate
criterion to test. More matches will not fix this on its own; the *transcripts* would need to
describe what the opponent did mid-exchange.

Two paths, neither of which is "loosen the gates":

1. **Improve condition capture at ingest** — the refiner prompt (`docs/deepseek/`) could be
   asked to emit opponent reactions between an athlete's action and their next move. This is
   the root fix, and it changes the data, not the statistics.
2. **Coarsen the condition space** — 18 curated postural conditions plus a technique-derived
   tail is a wide space for 232 observations. Grouping conditions into families would
   concentrate evidence, at the cost of specificity. This needs a condition ontology that does
   not exist (see below).

## Do not wire this into the site yet

The plan gated the export wiring on this measurement, and the measurement says: a Decision
Criteria panel would render nothing on every page. The extraction, statistics and level
selection are built and tested; publishing them is deferred until condition capture improves.

## Notes for whoever picks this up

- **The taxonomy ladder does not apply to most conditions.** The curated condition keys are
  postural (`cond:sprawls`, `cond:hand-fight`, `cond:elbow-free`) — they describe a posture or
  reaction, not a technique, so the technique taxonomy has nothing to say about them.
  `select_level` takes the hierarchy as a parameter for this reason; `taxonomy_levels` returns
  `[]` for postural conditions rather than inventing a ladder. A condition ontology is the
  missing piece.
- **Bootstrap stability was initially wrong** and is worth understanding before trusting it.
  Counting "effect > 0" scored a 1-of-1 triad as perfectly stable: when its only match is
  resampled away, the count drops to zero, the Beta(1,1) prior floors the estimate at 0.5, and
  that still exceeds a small baseline. Stability now requires the cell to be non-empty in the
  resample, which drops 1-of-1 triads to ~0.65 while leaving the genuine 7-of-8 at 0.99.
- **The gates are load-bearing.** `tests/test_decision_criteria.py::test_null_calibration_finds_almost_nothing`
  fails if the pipeline starts manufacturing signal from independent data;
  `test_planted_criterion_is_recovered` fails if it goes blind. Keep both.

## Reproduce

```bash
uv run python -m analysis.decision_criteria --athlete "Gordon Ryan" --out /tmp/c.json
```

The pooled figures come from running `analyze()` over `extract_patterns` for both athletes of
every final match; there is no committed CLI for the pooled pass yet.
