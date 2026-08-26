# RRB as a progression — trajectories, offensive/defensive cycles, and the action-weights artifact

Runners: `analysis/rrb_progression.py` (pure functions over `analysis/lamas_chain` chains) and
`scripts/build_markov_action_weights.py` (writes `data/rating/markov_action_weights.json`).
Tests: `tests/test_rrb_progression.py`. Consumer side of the artifact:
`analysis/markov_weights.py` and the App's `src/services/markovActionWeights.ts`.

`docs/research/lamas_chain_divisions.md` §8 built RRB as a value **per state**: the probability
that a chain starting at that action ends in her submission rather than the opponent's. This
document does the one thing that value makes possible and that §8 does not do — it **walks a
single bout** with it. Three questions come off the same walk:

| § | what | question |
|---|---|---|
| §2 | trajectory / progression | did the athlete's RRB standing MOVE over the bout, and by how much |
| §3 | offensive / defensive cycles | how long does she hold positive-RRB ground, how often does she lose it |
| §4 | the weights artifact | how much of an ELO move is each action worth |

Corpus read **2026-08-26**, read-only: 913 `status='final'` bouts, 741 with a sequence. Every
figure below is a measurement on that corpus and moves when the corpus does.

**This is descriptive analysis, not a pre-registered PoC** (ADR-03) — with one exception that is
NOT descriptive and is called out as such: the weights artifact in §4 is a **production input**,
consumed by the Analytics athlete-ELO redistribution and by the App's sequence scorer. Its
transform, its gates and its fallbacks are pre-registered in §4.1 and pinned by tests, because a
number that moves a rating has a different standard than a number that fills a table.

---

## 1. The value function, pre-registered

### 1.1 The definition, verbatim

```
V(s)          = 2 · sub_share(s) − 1                                    ∈ [−1, +1]
sub_share(s)  = p_sub_own(s) / (p_sub_own(s) + p_sub_opp(s))            (lamas_chain.rrb, §8.1)

pos_R(i)      = +V(state_i)   when step i's actor IS the reference athlete R
                −V(state_i)   when step i's actor is the opponent
                None          when the actor is unknown, is neither corner, or V is None
```

`V` is zero exactly where the two absorption probabilities are equal and +1 where every finish
reachable from `s` is hers. The sign flip in `pos_R` is exact rather than an approximation: §8.2
lifts the state space by side and asserts the two rows of a state are exact mirrors, so `−V` IS
the opponent-side row's value.

### 1.2 `balance` was rejected, and why

`balance = p_sub_own − p_sub_opp` is §8.1's **primary** relation. It is not the value function
here, for the reason §8.1 gives for shipping the share at all: a difference cannot separate
"nothing happens here" from "something one-sided happens here".

Measured on the full corpus, every non-`SUB` balance sits inside **[−0.018, +0.075]** — §8.6's
flattening, now confirmed on a corpus seven times larger than the one that first showed it. A
trajectory drawn in balance space is a flat line with one spike at `SUB`, and it would say
nothing about the fight. The share is balance's own bounded monotone transform (`r / (1 + r)` of
the odds), so **the sign of `V` and the sign of `balance` are identical by construction** and
only the magnitudes become legible. No signal is created; nothing is re-derived. The share is
read straight off `rrb`'s published rows.

The **plain odds ratio** stays rejected for §8.1's reason — unbounded, and undefined wherever
`p_sub_opp` is zero.

### 1.3 The fallback chain, in order

`value_table` resolves each state through exactly three tiers and records which one fired:

| tier | fires when | value |
|---|---|---|
| `rrb_sub_share` | the corpus's absorption is estimable AND the row cleared its own bout-cluster gate | `2·sub_share − 1` |
| `reward_risk_centered` | the row above is refused, but `reward_risk` has a **gated** score for it | `clip(score − pooled_retention, −1, +1)` |
| `none` | neither | `None` — a **refusal**, not a zero |

**The centring is not cosmetic; without it the fallback is wrong.** `reward_risk.score` is
`P(next action is hers) − P(next action is the opponent's)`, and its zero means "the next action
is a coin flip", not "neither athlete is ahead". Measured pooled retention over every scored
appearance:

| corpus | pooled retention |
|---|---|
| full corpus | **+0.412** |
| `adcc` family | **+0.321** |
| `ibjjf` family | **+0.170** |

The same athlete simply tends to act again. Substituting the raw score would put **eleven of
twelve** states on the positive side of the full corpus and classify almost every step as
offensive. Subtracting the corpus's own pooled score moves zero to "this action retains the
initiative no better than a typical action of this corpus", which is the sign the share carries.
The clip matters: `score − pooled` leaves `[−1, +1]` in practice (measured `TKD` at **−1.170** in
the `ibjjf` family).

**A mixed table is flagged and its magnitudes are not comparable to a pure one.** Tier 1 spreads
over `[−0.050, +0.195]` outside `SUB`; tier 2 spreads about **five times wider**, because §8.6's
propagation flattening does not apply to a one-step reading. `value_table` returns
`mixed_source`, and the rule is stated rather than corrected: **signs stay comparable,
magnitudes do not.**

On this corpus **the fallback never fires**: all twelve rows gate on the full corpus and all
twelve on the `adcc` family. It exists so a thin slice degrades in a named way instead of
silently.

### 1.4 The value table, full corpus, 2026-08-26

336 usable bouts of 913 after the actor gate; **118 absorbing bouts**, coverage `adequate`.

| state | V | sub_share | balance | n | bouts | terminal | source |
|---|---|---|---|---|---|---|---|
| CDP | −0.005 | 0.4976 | −0.0016 | 186 | 111 | 5 | rrb_sub_share |
| PGD | **−0.050** | 0.4752 | −0.0176 | 152 | 116 | 3 | rrb_sub_share |
| SWPA | −0.024 | 0.4879 | −0.0085 | 181 | 69 | 13 | rrb_sub_share |
| SWP | +0.133 | 0.5666 | +0.0466 | 47 | 38 | 6 | rrb_sub_share |
| TKDA | +0.000 | 0.5001 | +0.0001 | 592 | 181 | 30 | rrb_sub_share |
| TKD | +0.052 | 0.5262 | +0.0161 | 122 | 74 | 19 | rrb_sub_share |
| GPSA | −0.009 | 0.4956 | −0.0031 | 306 | 150 | 10 | rrb_sub_share |
| GPS | +0.083 | 0.5413 | +0.0268 | 55 | 40 | 5 | rrb_sub_share |
| BTKA | +0.023 | 0.5116 | +0.0081 | 1281 | 212 | 55 | rrb_sub_share |
| **BTK** | **+0.195** | 0.5977 | +0.0749 | 50 | 32 | 8 | rrb_sub_share |
| SUBA | +0.036 | 0.5178 | +0.0121 | 1082 | 258 | 78 | rrb_sub_share |
| **SUB** | **+0.613** | 0.8065 | +0.4291 | 176 | 122 | 104 | rrb_sub_share |

Two things a reader should carry forward. **`PGD` is the only negative-sign state with any
weight of evidence**, which is now the fourth statistic in this document family to put guard-pull
on the negative side (§5.6, §7.6, §8.6). And **`SUB`'s value is partly circular**: 104 of its 176
appearances ARE the chain's terminal step, so it largely restates §1.5's truncation rule.
`n_terminal` ships in every row so that stays visible.

---

## 2. Progression

### 2.1 The definition, verbatim

```
Δ_i         = pos_R(i+1) − pos_R(i)      for every transition where BOTH ends are valued
net         = Σ Δ_i
gained      = Σ max(Δ_i, 0)              two disjoint arms on one denominator
lost        = Σ min(Δ_i, 0)              gained + lost == net
per_action  = net / (number of valued transitions)
```

A transition with an unvalued end is **excluded and counted** (`unvalued_transitions`), never
scored as zero. That is `analysis/transitions/build_graph.py`'s convention as `lamas_chain`
inherits it — an unknown is never charged — carried to a signed difference, where "charge it
zero" is not neutral: it would drag a valued neighbour's Δ toward the missing step.

`net` telescopes to `pos(last) − pos(first)` across any contiguous valued stretch, which is why
`gained`/`lost` ship beside it: the pair is what separates a fight that climbed steadily from
one that swung and landed in the same place.

**Per exchange.** An EXCHANGE is a maximal run of consecutive steps by the same actor — the
chain's own unit of "whose turn it is". `exchanges` reports one row each with its endpoints and
its Δ; `net_per_exchange` normalises by their count.

### 2.2 A real bout, step by step

**Polaris 36 — Sarah Galvão def. Libby Genge by submission** (`04f0e490-…`), reference athlete
Sarah Galvão, value table §1.4:

| i | state | actor | V | pos | phase | Δ |
|---|---|---|---|---|---|---|
| 0 | PGD | Genge | −0.050 | **+0.050** | off | −0.026 |
| 1 | BTKA | Galvão | +0.023 | +0.023 | off | 0.000 |
| 2 | BTKA | Galvão | +0.023 | +0.023 | off | +0.059 |
| 3 | GPS | Galvão | +0.083 | +0.083 | off | −0.047 |
| 4 | SUBA | Galvão | +0.036 | +0.036 | off | −0.088 |
| 5 | TKD | Genge | +0.052 | **−0.052** | **def** | +0.088 |
| 6 | SUBA | Galvão | +0.036 | +0.036 | off | 0.000 |
| 7 | SUBA | Galvão | +0.036 | +0.036 | off | +0.098 |
| 8 | SWP | Galvão | +0.133 | +0.133 | off | +0.480 |
| 9 | SUB | Galvão | +0.613 | +0.613 | off | — |

```
net        +0.5634        gained  +0.7248     lost   −0.1614
per_action +0.0626        over 9 valued transitions, 0 unvalued
exchanges  4              net_per_exchange +0.1409
start_pos  +0.0496        end_pos +0.6130     (telescopes: 0.613 − 0.050 = 0.563 ✓)
cycles     off(5) | def(1) | off(4)     off_share 0.90     recoveries 1/1
```

Read it. Row 0 is the one that makes the side convention concrete: **Genge pulls guard, and that
is a small GAIN for Galvão** (+0.050), because `PGD` is worth −0.050 to whoever performs it. Row
5 is the bout's only defensive ground — Genge lands a takedown — and it lasts exactly one action
before Galvão is back on top. And row 8→9 carries **85% of the whole bout's net** (+0.480 of
+0.563), which is the honest shape of this statistic on a submission win: the finish dominates,
because `SUB` is where the value lives and where the truncation rule puts the chain's end.

That last point is not a defect of the bout, it is the measure's own signature: on any
submission-won bout, `net` is mostly "she finished". `per_action`, `gained/lost` and the cycle
occupancy of §3 are what carry information about the rest of the fight.

### 2.3 Per athlete — and what it does NOT show

`athlete_progression` pools an athlete's bouts, refusing every chain the actor gate refuses (§6
below). Full corpus: **672 bout-sides used, 888 refused as `single_actor`, 266 as `one_sided`**,
and after the per-athlete bout-cluster gate (≥5 effective bouts), **17 of 441 athletes** publish
anything at all. That number is the headline: this layer is not a leaderboard, it is a lens on
the handful of athletes this corpus has covered densely enough.

| athlete | bouts | Δ n | per_action (95% CI, bout-clustered) | off_share | mean def cycle | mean off cycle |
|---|---|---|---|---|---|---|
| Helena Crevar | 8 | 138 | +0.0068 [−0.0092, +0.0411] | 0.692 | 1.67 | 3.48 |
| Ana Carolina Vieira | 6 | 113 | +0.0030 [−0.0038, +0.0292] | 0.361 | 3.80 | 2.15 |
| Bruno Fernandes Rocha | 8 | 98 | **+0.0176 [+0.0021, +0.0398]** | 0.547 | 1.71 | 2.00 |
| Victor Hugo | 6 | 91 | +0.0064 [−0.0008, +0.0226] | 0.454 | 3.12 | 2.59 |
| Gordon Ryan | 6 | 74 | +0.0076 [−0.0039, +0.0435] | 0.637 | 1.81 | 3.40 |
| Sarah Galvão | 5 | 70 | **+0.0201 [+0.0027, +0.0717]** | 0.507 | 2.85 | 2.71 |
| Craig Jones | 9 | 62 | +0.0053 [−0.0091, +0.0263] | 0.648 | 1.47 | 2.30 |
| Giancarlo Bodoni | 6 | 54 | +0.0029 [−0.0613, +0.0482] | 0.650 | 1.62 | 2.79 |
| Dorian Olivarez | 5 | 54 | +0.0152 [−0.0026, +0.1204] | 0.644 | 1.91 | 2.71 |
| Nick Rodriguez | 5 | 47 | +0.0262 [−0.0099, +0.1371] | 0.404 | 3.88 | 2.10 |
| Leandro Lo | 7 | 44 | −0.0002 [−0.0050, +0.0056] | 0.333 | 3.40 | 1.55 |
| Elisabeth Clay | 5 | 38 | −0.0296 [−0.1304, +0.0090] | **0.860** | 1.50 | **6.17** |
| Tye Ruotolo | 6 | 30 | +0.0211 [−0.0038, +0.0638] | 0.611 | 1.27 | 1.57 |
| Eli Braz | 5 | 29 | +0.0352 [−0.0145, +0.1485] | **0.265** | 3.13 | 1.00 |
| Kade Ruotolo | 5 | 21 | +0.0272 [−0.0092, +0.1193] | 0.654 | 1.29 | 2.43 |
| Anabel Lopez | 6 | 15 | −0.0741 [−0.5035, +0.0967] | 0.429 | 2.00 | 1.50 |
| Nadia Frankland | 5 | 11 | +0.0469 [−0.1378, +0.5182] | 0.750 | 1.33 | 3.00 |

**Two of seventeen intervals exclude zero, which is what chance produces.** At 95% coverage the
expectation from nothing at all is 0.85 of seventeen, the two athletes concerned share bouts with
others in the table, and no multiplicity correction was pre-registered because no per-athlete
claim was. **Nothing in the `per_action` column is a finding.** It is reported so the column that
IS informative can be seen next to it.

**The occupancy columns are the informative ones**, and they are the same lesson §8.6 already
taught in a different shape: the propagated difference flattens, the residence does not.
`off_share` spans 0.265 (Eli Braz) to 0.860 (Elisabeth Clay) and mean defensive cycle length
spans 1.27 actions (Kade Ruotolo) to 3.88 (Nick Rodriguez) — a three-fold spread on a quantity
that needs no absorption estimate at all. Read as scouting: Clay lives on top and rarely goes
under; Rodriguez and Vieira spend long stretches underneath and come back; Eli Braz's chains are
mostly the opponent's ground with single-action offensive flashes. Those are descriptions of
*where the fight happens*, not claims about outcome.

---

## 3. Offensive / defensive cycles

### 3.1 The definition, verbatim

```
phase(i) = "off"       pos_R(i) > 0
           "def"       pos_R(i) < 0
           "neutral"   pos_R(i) == 0 exactly
           "unvalued"  pos_R(i) is None

cycle    = a maximal run of consecutive steps in one phase
recovery = a "def" run IMMEDIATELY followed by an "off" run
collapse = an "off" run immediately followed by a "def" run
```

Durations are counted in **ACTIONS**, which is the chain's only clock: no bout in the corpus
carries both a stage and a usable clock (`ruleset_scoring.adcc_clock_feasibility`), so a duration
in seconds would be a fact about the annotation batch.

**`unvalued` is its own phase and is never merged away.** Bridging over it would let one missing
actor splice two offensive runs into a single long one, which is the one way this table could
invent a dominance streak that did not happen. For the same reason, adjacency is required:
`def → neutral → off` is **not** a recovery, because the stretch between is ground on which
nothing was measured.

Denominators drop the last run of a chain, which has no successor — `build_graph`'s rule again.

### 3.2 ⚠️ The recovery RATE is 1.00 by construction here

With only `off` and `def` present, two-phase runs **alternate by definition**: every defensive
run that has a successor is followed by an offensive one. Measured, that is the case for every
one of the 17 gated athletes above — `recovery_degenerate: true` on all of them — because on the
full corpus no state's value is exactly zero and the actor gate has already removed the unknown
actors.

So the rate ships **with its degeneracy flag beside it**, not as a finding. The quantities that
carry information are the ones the flag points at: **how long she stays under**
(`mean_def_cycle_len`) and **how often she goes there** (`def_cycles`). The rate becomes
informative only where neutral or unvalued ground exists — a thin corpus, or a mixed value table
— which is exactly when it should be consulted.

This is named here rather than fixed because it is not a bug: a binary partition of a signed
quantity has alternating runs, and any "recovery rate" over it is a tautology. Redefining
recovery to restore variance (a threshold band around zero, say) would be choosing a constant to
manufacture a result, which is the move this document family exists to refuse.

---

## 4. `data/rating/markov_action_weights.json`

Generator: `scripts/build_markov_action_weights.py`. Consumer: `analysis/markov_weights.py`
(Analytics athlete-ELO) and `src/services/markovActionWeights.ts` (App sequence scorer). This
section is **pre-registered**, because the artifact feeds ratings.

### 4.1 The transform, verbatim

```
w(s) = max( (V(s) + 1) / 2 , WEIGHT_FLOOR )  =  max( sub_share(s), WEIGHT_FLOOR )
w(s) = NEUTRAL_SHARE = 0.5                       when V(s) is None

WEIGHT_FLOOR   = 0.01     strictly positive, so sum(w) > 0 is STRUCTURAL
WEIGHT_PLACES  = 4        every weight rounded, so the file is byte-identical across machines
normalized     = false    the consumer renormalises over the actions actually present
```

A weight IS the share of the finishes reachable from `s` that are hers. It is the value function
of §1.1 mapped back through its own inverse, so **weights and progression can never disagree in
sign**.

**Rejected: the shifted balance `(balance + 1) / 2`.** Also non-negative and bounded, and it
would make every action weigh 0.5 ± 0.04 (§1.2's measured range) — a uniform redistribution with
another name.

**Rejected: the plain odds ratio.** Unbounded and undefined where `p_sub_opp` is zero (§8.1).

**The floor never binds on today's corpus** — the minimum observed share is 0.451 — and it is not
a smoothing prior. It exists so that a consumer normalising over a subset of actions can never
divide by zero as an *invariant* rather than as an observation.

**Weights are not pre-normalised** because a sum-to-one vector over twelve states is wrong for
every subset a consumer actually scores.

### 4.2 Which blocks ship

A family block ships **only when its own RRB clears its gates**: the corpus must have absorbing
bouts and the absorbing-bout coverage must be estimable (§8.4). A refused family is **omitted**
with its reason, so the consumer falls back to `global` instead of reading twelve neutral weights
as a measurement.

| block | bouts | usable | absorbing | verdict |
|---|---|---|---|---|
| `global` | 913 | 336 | 118 | **ships** — coverage `adequate`, 12/12 rows tier 1 |
| `adcc` | 185 | 53 | 8 | **ships** — coverage `adequate`, 12/12 rows tier 1 |
| `ibjjf` | 44 | 11 | **0** | **OMITTED** — `no_absorbing_bouts`; consumer falls back to `global` |

The `ibjjf` row is not a thin block, it is an empty one — the same failure §8.4 records for the
ADCC 2024 corpus, for the same reason: after the actor gate removes 33 of 44 bouts, none of the
eleven survivors was won by submission.

The generator **refuses to write at all** if the `global` block is not estimable. Twelve neutral
weights presented as a measurement is the failure that guard exists for.

### 4.3 The weights, 2026-08-26

| action | `global` | `adcc` | reads as |
|---|---|---|---|
| CDP | 0.4976 | 0.5038 | grip dispute standing |
| PGD | **0.4752** | **0.4583** | guard pull — lowest in both blocks |
| SWPA | 0.4879 | 0.4997 | sweep attempt |
| SWP | 0.5666 | 0.5045 | sweep landed |
| TKDA | 0.5001 | 0.5123 | takedown attempt |
| TKD | 0.5262 | 0.5392 | takedown landed |
| GPSA | 0.4956 | 0.4840 | pass attempt |
| GPS | 0.5413 | 0.6270 | pass landed |
| BTKA | 0.5116 | 0.5528 | back-take attempt |
| BTK | **0.5977** | **0.6799** | back taken |
| SUBA | 0.5178 | 0.5334 | submission attempt |
| SUB | **0.8065** | **0.7347** | submission landed |

The **order is the finding**, and it is the same in both blocks: `PGD` at the bottom, `BTK` the
highest non-`SUB` action, attempts below their own landings in every family. `global`'s spread is
1.70× (0.475 → 0.807) and `adcc`'s is 1.48×.

**The spread is small on purpose.** §8.6 measured why: the chain mixes faster than it absorbs, so
the action you are in tells you little about who eventually taps. This artifact publishes the
measurement, not an amplification of it. A consumer that needs sharper differentiation is
applying a transform of its own and must justify it in its own PR.

### 4.4 Determinism and provenance

No bootstrap is run (`n_boot=0`), so no RNG is touched; every weight is rounded to
`WEIGHT_PLACES`, which is what makes the file byte-identical regardless of the BLAS behind
`numpy.linalg.inv`. The artifact carries a `corpus_digest` over `(bout id, win_type, winner,
mapped chain)` — deliberately **not** the raw sequence, so an annotation edit that changes no
Lamas action cannot move the digest and send a reader looking for a number that did not move.

```
uv run python -m scripts.build_markov_action_weights --check
```

rebuilds from prod and diffs everything except `generated`. That is the gate that says whether
the committed artifact still matches the corpus and the code; it is the only step here that needs
the database.

### 4.5 ⚠️ `adcc` is eight finishes, all on one side

`absorbed_self: 8, absorbed_other: 0`. Every absorbing bout in the ADCC block ends in a
submission by the athlete whose action closes the chain, so `p_sub_opp` in that block comes
**entirely from side-flips inside the chain mirroring those same eight finishes**, never from an
independently observed opposite-side finish. The block clears every gate the report has, and this
is the thing no gate catches. Read `adcc` as eight fights' worth of evidence, and prefer `global`
wherever the family distinction is not load-bearing.

---

## 5. What this does not do

- **It does not predict.** Nothing here is a held-out prediction and nothing selects a production
  constant except the weights table, whose transform was fixed before the numbers were read.
- **It does not rank athletes.** §2.3 refuses that explicitly: two of seventeen intervals exclude
  zero, which is chance, and the gate leaves 17 of 441 athletes with anything at all.
- **It does not measure time.** Cycle durations are in actions. The corpus has no usable clock.
- **It does not survive bad attribution.** Every number here is read through `actor_id`, more so
  than `reward_risk`: the side of each step decides the sign of the position. A bout filing every
  event under one athlete would show a monotone climb by construction, which is why the
  aggregation consumes only chains that clear `lamas_chain`'s `one_sided` + `single_actor`
  refusal. 1154 of 1826 bout-sides are refused on this corpus.
- **It does not cross annotation batches.** The attempt/success partition follows the ingest batch
  (`successful` absent reads as ATTEMPT), so blocks are comparable WITHIN a family and not across
  (`ruleset_scoring.comparability`).
- **It does not touch private data.** Privacy class **A**: every input is a `matches` row from
  published competition footage. Nothing here reads a user graph or a session.
