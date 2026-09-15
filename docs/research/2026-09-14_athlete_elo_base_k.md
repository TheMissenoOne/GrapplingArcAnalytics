# Sweeping the base K of the engine production actually ships (`athlete_elo`)

Date: 2026-09-14 · Runner: `scripts/research/athlete_elo_base_k.py` · Instrument:
`analysis/poc/e0_rating_eval.py` (PoC-E0) · Backlog item: `athlete-elo-base-k-sweep`.

The follow-up named in `docs/research/2026-09-14_e0_ksweep_whr.md` §4.4:

> If the owner wants one follow-up, it is this one: sweep `athlete_elo`'s OWN base K
> through this harness via the existing `AthleteEloEngine` adapter. That engine is what
> ships, it scored worst in the E0 db run, and it is the only K on which a sweep result
> would be directly actionable.

> **Nothing in this document changes production.** No K moves, no replay is run, no prod
> row is written. An ELO-math change retroactively rewrites every athlete's rating and
> `elo_series` and is gated on a deliberate full replay (`research-methodology` §2e);
> §5 spells out what that would cost. This is evidence, reported with the criteria that
> fail as well as the ones that pass.

---

## 1. Pre-registration

Written and committed to this file before any corpus arm ran. The criteria are literal
code in the runner's `run_corpus`, not prose reconstructed afterwards.

### 1.1 Which K — and why the previous sweep did not measure it

The 2026-09-14 sweep moved `analysis/elo_calibration.py`'s plain-Elo K (base 40 ×
win-type × stage) and found a flat bottom around K=100. That engine does not ship. What
ships for an athlete graph is `analysis/athlete_elo.py`:

```
K = _base_k(n) × gap_factor × competitive_mult × temporal_decay
    _base_k(n)       = 40 (n ≤ 10), 32 (n ≤ 30), then log-decay → floor 10
    gap_factor       = clamp(|rank_target − graph_elo| / 400, 0.1, 1.0)
    competitive_mult = 2.5 ranked/leaderboard, 1.0 casual        (gated in repository.py)
    temporal_decay   = 2 ** (−months_since / 36)
```

So "the base K" is not a scalar — it is a **ladder** plus three multipliers on it. The
question "K=40? the 2.5?" has one answer: **they are the same knob.** Every term is
multiplicative and nothing outside `k_factor` reads `_base_k`, so scaling the ladder by
*c* is arithmetically identical to scaling `competitive_mult` by *c*. This sweep therefore
moves the **level** of the whole schedule through the `competitive_mult` parameter
`replay_matches` already exposes, and **`analysis/athlete_elo.py` is not edited at all**.

The identity is asserted against the real replay — converge-clamp included — by
`tests/test_research_athlete_elo_base_k.py::test_scaling_the_base_k_ladder_equals_scaling_competitive_mult`,
with a companion test that two levels genuinely diverge so the equivalence cannot pass
trivially. If a future change makes the ladder enter non-multiplicatively, that test goes
red and these tables become *labelled* wrong rather than *silently* wrong.

**Reporting unit.** `L = K_BASE_EARLY × competitive_mult` — the nominal K of a
first-ten-matches bout, before `gap_factor` and `temporal_decay` shrink it. Shipped
competitor path **L = 100**; shipped casual path **L = 40**. (L = 100 is also, exactly,
where the plain-Elo curve bottomed on both corpora. Coincidence, noted because it will
otherwise look like a finding.)

### 1.2 Protocol — unchanged from E0, which is the point

Prequential walk-forward: every arm predicts P(a wins) for a bout *before* seeing its
result, then observes it. First corpus year burned in. Mean log loss is the headline with
a seeded percentile bootstrap over bouts (`stats_rigor.bootstrap_ci`, 2000 draws); Brier
and accuracy alongside. TUNE/TEST split on the **median bout**, same `split_years` as the
previous study. Every arm is scored on the identical bout stream in the identical order,
which is what licenses the paired comparisons.

Two corpora, both public: the ADCC-2026-women scouting records and the closed `matches`
table. §1.6 pre-registers what happens when a corpus cannot run this engine.

### 1.3 The grid

Geometric, ratio √2, from ⅛× to 8× the shipped level, containing it exactly:

**L ∈ {12.5, 17.7, 25, 35.4, 50, 70.7, 100, 141.4, 200, 282.8, 400, 565.7, 800}**

(i.e. `competitive_mult` from 0.3125 to 20). The span is deliberately wide because the one
durable lesson of the previous sweep is that a grid whose optimum sits at an edge has not
measured an optimum. Carried on the same stream as context, **not** as sweep arms and
excluded from best-in-grid selection: `athlete-elo-L40` (the shipped *casual* path, which
is not a member of the √2 ladder), `rank-target-only` (§2), `win-rate`, `constant-0.5`,
`glicko2-perbout-tau0.5`, and `elo-k40` / `elo-k100` so this study and the previous one
are read on ONE corpus snapshot rather than compared across two runs of a corpus that
keeps growing.

> Amendment, before any corpus arm ran: the first draft of this section listed L=40 as if
> the ladder contained it. It does not (the ladder passes 35.4 → 50), and the runner
> crashed on the missing arm rather than quietly dropping the comparison. L=40 was added
> as a context arm, outside the grid and outside the verdict. Recorded here rather than
> silently corrected.

**Grounding.** Szczecinski and co-authors' stochastic analysis of Elo (Zanco, Szczecinski,
Kuhn & Seara, *Stochastic analysis of the Elo rating algorithm in round-robin
tournaments*, arXiv:2212.12015, Digital Signal Processing 2023,
doi:10.1016/j.dsp.2023.104313) is the reason to expect a **U-shaped** loss curve in K at
all: K is a step size, small K retards tracking of a time-varying skill and large K
inflates estimation variance, so predictive loss falls then rises. That paper's setting
(round-robin, stationary skills) is not this corpus, so the transfer is *directional*, not
quantitative — it predicts a turn exists, not where. The same reading predicts the broad
flat bottom the previous sweep measured. The `scientific-papers` MCP was not reachable
from this session, so this citation was resolved by web search and is given with its arXiv
id and DOI rather than as an MCP-verified identifier.

### 1.4 Verdict rules — fixed in advance

Reference = the SHIPPED level, `athlete-elo-L100`.

| id | rule |
|---|---|
| **BK-PRIMARY** | the best L in the grid beats the shipped level by **more than the bootstrap CI width** of the shipped level's log loss |
| **BK-SECONDARY** | paired bootstrap of the per-bout log-loss difference (best − shipped), 95% CI **excludes 0** |
| **BK-TERTIARY** | L selected on the TUNE window and scored on the held-out TEST window beats the shipped level there, paired CI excluding 0 |

**VERDICT = PRIMARY and TERTIARY**, exactly as the backlog item words it ("beats the
shipped one by more than the CI width AND the TUNE-picked K also wins on TEST"). SECONDARY
is reported because it is the correctly-specified *paired* version of PRIMARY — comparing
a gap against the CI width of one arm treats two arms scored on the SAME bouts as
independent samples, which is underpowered by roughly the factor the previous study
measured (CI widths ≈ 0.04–0.09 against plausible gaps ≈ 0.01–0.02). When the three
disagree, TERTIARY is the selection-free reading and the one to believe.

### 1.5 Pre-registered predictions (falsifiable, stated before the run)

Formed by reading the engine, not by peeking at results.

- **P1 — the curve will fall monotonically toward a floor rather than turn, and the floor
  will be `rank-target-only`.** `replay_matches` converges the graph mean onto the athlete's
  `rank_target`; `gap_factor` and the converge-clamp both brake as it arrives. A larger L
  therefore does not learn faster, it *arrives at the leaked prior sooner*. If P1 holds, the
  sweep is measuring convergence speed to a present-day leaderboard snapshot, and no L in
  it is a rating-quality finding.
- **P2 — every sweep arm will lose to `rank-target-only`.** The adapter predicts from the
  rank target until an athlete's first bout and from the graph mean afterwards, and the
  graph mean starts at the belt floor (800) while the rated population averages ≈1024. So
  observing one bout *replaces* an informative prior with a worse one. If P2 holds, the
  binding defect is the seeding, not K.
- **P3 — BK-PRIMARY fails.** The previous study's plateau plus the braking above make a
  gap larger than a 0.04–0.09 CI width implausible.

P1 and P2 are descriptive; only the BK-* rules carry a verdict. They are recorded so the
reading below can be checked against what was expected rather than narrated to fit.

### 1.6 What would void a corpus, or the whole run

- **Void corpus (pre-registered, detected in code).** `athlete_elo` grows a rating only
  from an athlete's own event `sequence`. A corpus carrying none leaves every arm inert and
  identical, and thirteen identical rows are not thirteen measurements. `degenerate_arms`
  compares the arms' full prediction vectors and the runner prints a VOID section with the
  measured reason instead of a table. **This is expected to fire on the scouting corpus**,
  which carries no sequences — stated here, before the run, so the outcome is a
  pre-registered null and not a quietly skipped half of "both corpora".
- **Void run.** A corpus that changed size under the run, a non-deterministic ranking, or
  an arm whose prediction could see its own result. The harness leak tests
  (`tests/test_poc_e0.py`) and the four asserts in
  `tests/test_research_athlete_elo_base_k.py` stand behind that.

---

## 2. Method — and the leak this engine carries by design

`AthleteEloEngine` calls the real `athlete_elo.replay_matches`; the only thing this study
changes is the `competitive_mult` it passes. But there is a property of the production
engine that has to be on the table before any number below is read.

**The rank target is information from after the bout.** `replay_and_persist_athlete`
(`db/repository.py:627–639`) feeds an athlete's ADCC leaderboard `rank_elo` in as the
convergence target and each opponent's `rank_elo` as their input rating, falling back to
the black-belt floor (800) for the unranked. That leaderboard is a present-day snapshot.
Replaying a 2011 bout with it is not a leak *in production*, where the job is to describe
an athlete's current game — it is a leak **in a prequential forecast**, where the engine is
being asked to predict a result whose consequences are already baked into the number it
starts from. The adapter reproduces this faithfully rather than fixing it, because the
subject of the study is the shipped engine, not an improved one.

So the sweep carries a control arm, **`rank-target-only`**: predict
`expected(target_a, target_b)`, never update. It is the leaked static prior with the
learning removed. Any level that cannot beat it has added nothing to what the leaderboard
already said. Measured coverage on the closed corpus is printed in the results header
(106 of 777 athletes rated; 130 of 780 bouts with both sides rated), so the prior's reach
is a number in the report rather than an assumption.

Determinism: engines iterate in list order, ties in every ranking break on name, the
bootstrap is seeded. The results block between the markers is REPLACED on each run so
`git diff` shows real change rather than churn — do not hand-edit it.

### Reproduce

```bash
uv run python -m scripts.research.athlete_elo_base_k --self-check   # asserts, no corpus
uv run pytest tests/test_research_athlete_elo_base_k.py
set -a; source .env; set +a                                         # DATABASE_URL, read-only
uv run python -m scripts.research.athlete_elo_base_k --source all   # the run below
```

---

## 3. Results

Generated by the runner; the block between the markers is REPLACED on each run — do not
hand-edit it.

<!-- ATHLETEK:RESULTS -->

## Corpus: `scouting`

690 distinct bouts, 2008–2026 · dropped `{'draws': 1, 'no_year': 0, 'mirrors_folded': 34}` · first corpus year burned in · 689 scored predictions per arm · 20 arms in one prequential pass (0s).

Bouts carrying an event `sequence`: **0** of 690 · athletes with a `rank_elo` target: **0** of 332 · bouts with BOTH sides rated: **0**.

### VOID — the swept knob is inert on this corpus

Every level in the grid produced a **byte-identical prediction vector**, so no verdict is issued here. The reason is structural and is printed above: 0 of 690 bouts carry an event `sequence`, and `athlete_elo` grows a rating only from the athlete's own sequence events — with none, `AthleteEloEngine.observe` returns immediately, no graph is ever built, and K never multiplies anything. Every arm falls back to the rank target, which for these keys resolves to the black-belt floor for BOTH sides, so every prediction is exactly 0.5.

This is not a null result about K. It is the corpus being unable to run the engine at all, and it is reported rather than silently skipped so nobody re-runs the sweep here expecting an answer.

## Corpus: `db`

780 distinct bouts, 2008–2026 · dropped `{'not_final': 0, 'excluded_win_type': 56, 'no_winner': 75, 'no_year': 0}` · first corpus year burned in · 778 scored predictions per arm · 20 arms in one prequential pass (4s).

Bouts carrying an event `sequence`: **645** of 780 · athletes with a `rank_elo` target: **106** of 777 · bouts with BOTH sides rated: **130**.

### Base-K sweep — `athlete_elo` at each schedule level, by predictive log loss

| L (= 40 × competitive_mult) | competitive_mult | log loss | 95% CI | Brier | accuracy |
|---|---|---|---|---|---|
| 12.5 | 0.3125 | 0.7057 | [0.6856, 0.7272] | 0.2551 | 0.558 |
| 17.7 | 0.4425 | 0.7038 | [0.6838, 0.7250] | 0.2543 | 0.558 |
| 25 | 0.625 | 0.7012 | [0.6813, 0.7223] | 0.2532 | 0.558 |
| 35.4 | 0.885 | 0.6976 | [0.6779, 0.7185] | 0.2517 | 0.558 |
| 50 | 1.25 | 0.6930 | [0.6735, 0.7136] | 0.2497 | 0.558 |
| 70.7 | 1.768 | 0.6869 | [0.6677, 0.7071] | 0.2470 | 0.558 |
| 100 ← SHIPPED (competitor) | 2.5 | 0.6792 | [0.6605, 0.6991] | 0.2437 | 0.558 |
| 141.4 | 3.535 | 0.6699 | [0.6515, 0.6895] | 0.2397 | 0.561 |
| 200 | 5 | 0.6592 | [0.6407, 0.6791] | 0.2350 | 0.562 |
| 282.8 | 7.07 | 0.6477 | [0.6289, 0.6680] | 0.2299 | 0.573 |
| 400 | 10 | 0.6360 | [0.6167, 0.6571] | 0.2249 | 0.588 |
| 565.7 | 14.14 | 0.6259 | [0.6051, 0.6476] | 0.2206 | 0.598 |
| 800 | 20 | 0.6190 | [0.5970, 0.6418] | 0.2177 | 0.603 |

TUNE years 2009–2024 (494 bouts) · TEST years 2025–2026 (284 bouts).

### Verdicts

- Reference `athlete-elo-L100` (SHIPPED): 0.6792, CI width **0.0386**.
- Best in grid: `athlete-elo-L800` at 0.6190 (gain +0.0602).
- Curve minimum: **L=800** — at a grid EDGE (grid is truncated).
- **BK-PRIMARY** (gain > CI width 0.0386): **PASS** (+0.0602).
- **BK-SECONDARY** (paired bootstrap best−shipped, 95% CI excludes 0): **PASS** (-0.0602 [-0.0771, -0.0428]).
- **BK-TERTIARY** (L chosen on TUNE = `athlete-elo-L800`, scored on TEST vs shipped): **FAIL** — 0.6689 vs 0.6799, paired -0.0110 [-0.0232, +0.0016].

- **VERDICT (PRIMARY and TERTIARY, as pre-registered): FAIL**

### The sweep against its controls and the other engines

| engine | log loss | 95% CI | Brier | accuracy |
|---|---|---|---|---|
| `elo-k100` | 0.6059 | [0.5848, 0.6286] | 0.2135 | 0.613 |
| `rank-target-only` | 0.6079 | [0.5848, 0.6325] | 0.2135 | 0.617 |
| `glicko2-perbout-tau0.5` | 0.6147 | [0.5907, 0.6393] | 0.2163 | 0.619 |
| `elo-k40` | 0.6164 | [0.6012, 0.6327] | 0.2170 | 0.608 |
| `athlete-elo-L800` | 0.6190 | [0.5970, 0.6418] | 0.2177 | 0.603 |
| `win-rate` | 0.6455 | [0.6319, 0.6600] | 0.2274 | 0.598 |
| `athlete-elo-L100` | 0.6792 | [0.6605, 0.6991] | 0.2437 | 0.558 |
| `constant-0.5` | 0.6931 | [0.6931, 0.6931] | 0.2500 | 0.500 |
| `athlete-elo-L40` | 0.6961 | [0.6764, 0.7170] | 0.2510 | 0.558 |

### Secondary readings (pre-registered as descriptive — no pass/fail attached)

- **Best sweep arm vs the leaked static prior**: `athlete-elo-L800` vs `rank-target-only`, paired +0.0112 [-0.0041, +0.0264] — indistinguishable from the prior.
- **Casual path vs competitor path** (the only two levels production actually ships): `athlete-elo-L40` vs `athlete-elo-L100`, paired +0.0169 [+0.0131, +0.0208].
- **Production engine at its shipped level vs plain Elo at the level the previous sweep liked**: `athlete-elo-L100` vs `elo-k100`, paired +0.0734 [+0.0467, +0.1003].

### Slices for the finalists

| engine | slice | n | log loss | 95% CI | accuracy |
|---|---|---|---|---|---|
| `athlete-elo-L100` | experienced | 25 | 0.6461 | [0.5935, 0.6961] | 0.580 |
| `athlete-elo-L100` | cold_start | 753 | 0.6803 | [0.6602, 0.7006] | 0.558 |
| `athlete-elo-L800` | experienced | 25 | 0.6137 | [0.4961, 0.7499] | 0.660 |
| `athlete-elo-L800` | cold_start | 753 | 0.6192 | [0.5969, 0.6432] | 0.602 |
| `rank-target-only` | experienced | 25 | 0.6572 | [0.5160, 0.8133] | 0.660 |
| `rank-target-only` | cold_start | 753 | 0.6062 | [0.5835, 0.6297] | 0.616 |

<!-- ATHLETEK:RESULTS -->

---

## 4. Reading and verdicts

Run: 2026-09-14, both corpora, 20 arms in one prequential pass each (0s scouting, 4s db),
2000 bootstrap draws. The `matches` corpus is the same snapshot the previous study used
(780 eligible bouts), and every reference arm was re-run on this stream rather than quoted
— `elo-k40` 0.6164, `elo-k100` 0.6059, `glicko2-perbout-tau0.5` 0.6147 and `win-rate`
0.6455 all reproduce the 2026-09-14 numbers to the last printed digit, which is the
instrument not having moved under the harness change in §6.

### 4.1 The scouting corpus is VOID, as pre-registered

0 of 690 bouts carry an event `sequence` and 0 of 332 athlete keys resolve to a `rank_elo`,
so `AthleteEloEngine.observe` returns immediately for every arm, no graph is ever built,
and all thirteen levels emit the identical constant 0.5. The runner detects this by
comparing full prediction vectors (`degenerate_arms`) and prints a VOID section instead of
thirteen identical rows. §1.6 called it in advance. **No verdict is issued on that corpus,
and none should be sought there** — it is a records corpus, and this engine eats sequences.

That leaves one corpus, which is the honest scope of everything below.

### 4.2 The pre-registered verdict: FAIL — but not for the expected reason

| criterion | result |
|---|---|
| BK-PRIMARY (gain > shipped CI width 0.0386) | **PASS** (+0.0602) |
| BK-SECONDARY (paired CI excludes 0) | **PASS** (−0.0602 [−0.0771, −0.0428]) |
| BK-TERTIARY (TUNE-selected L, scored on TEST) | **FAIL** (−0.0110 [−0.0232, +0.0016]) |
| **VERDICT = PRIMARY and TERTIARY** | **FAIL** |

This is the opposite shape to the plain-Elo sweep, where all three failed on a flat bottom.
Here the effect is **large** — 0.06 log-loss units, an order of magnitude past anything the
Elo K sweep produced, and easily clearing the CI width that made PRIMARY nearly unfireable
there. Pre-registered prediction **P3 (PRIMARY fails) is FALSIFIED**, and it is recorded as
such rather than reworded.

And the run still says do not change K, for a reason that has nothing to do with statistical
power. Read the sweep column, not the verdict line:

| L | 12.5 | 25 | 50 | **100 (shipped)** | 200 | 400 | 800 |
|---|---|---|---|---|---|---|---|
| log loss | 0.7057 | 0.7012 | 0.6930 | **0.6792** | 0.6592 | 0.6360 | 0.6190 |

Monotone decreasing across the entire pre-registered grid, ending at the edge. That is
precisely the truncated-grid failure the previous study existed to close, reproduced here
in a different engine — the runner says so itself ("at a grid EDGE (grid is truncated)").
A monotone curve is not an optimum, and **BK-PRIMARY passing on a truncated grid is not
evidence for any particular K.**

### 4.3 Post-hoc: where the curve turns, and what it turns toward

Not pre-registered. Reproduce:

```bash
uv run python -m scripts.research.athlete_elo_base_k --source db \
    --levels 100,800,1131,1600,2263,3200,6400,12800      # implies --no-write
```

| L | 100 | 800 | 1131 | **1600** | 2263 | 3200 | 6400 | 12800 |
|---|---|---|---|---|---|---|---|---|
| log loss | 0.6792 | 0.6190 | 0.6152 | **0.6135** | 0.6173 | 0.6236 | 0.6473 | 0.6963 |

The turn is at **L ≈ 1600 — sixteen times the shipped level** (`competitive_mult` ≈ 40),
and it is interior, so the U-shape Szczecinski's step-size analysis predicts (§1.3) does
exist for this engine too. It is just very far from where production sits.

Now the number that decides the whole study:

| arm | log loss |
|---|---|
| `rank-target-only` — the leaked static prior, no learning at all | **0.6079** |
| `athlete-elo-L1600` — the post-hoc optimum | 0.6135 |
| `athlete-elo-L800` — best in the pre-registered grid | 0.6190 |
| `athlete-elo-L100` — **SHIPPED (competitor)** | 0.6792 |
| `constant-0.5` | 0.6931 |
| `athlete-elo-L40` — **SHIPPED (casual)** | 0.6961 |

- best sweep arm vs the prior (paired, positive = the sweep arm is worse): `L1600`
  **+0.0057 [−0.0114, +0.0233]** → indistinguishable; `L800` **+0.0112 [−0.0041, +0.0264]**
  → indistinguishable.
- **No level anywhere in the grid, pre-registered or post-hoc, beats the leaked prior.**
  The very best one ties it.

Pre-registered predictions **P1 and P2 both CONFIRMED** (P1's "monotone, no turn" held
across the whole pre-registered grid; the turn exists only 16× out, and the floor is the
prior exactly as predicted).

### 4.4 Root cause: the rank target is an absorbing barrier, so K only buys arrival time

Two measurements, both post-hoc, both cheap, and together they close the question.

**(a) A bigger K does not learn more — it closes the seeding gap sooner.** Mean distance
between a rated athlete's graph mean and their own rank target, measured at prediction time
over the 277 scored predictions where the athlete is rated:

| L | mean \|graph mean − rank target\| | fraction sitting BELOW their target |
|---|---|---|
| 40 (casual) | 286.0 | 100.0% |
| 100 (shipped) | 259.1 | 100.0% |
| 800 | 106.5 | 62.5% |
| 1600 | 74.7 | 46.2% |

The rated population averages `rank_elo` ≈ 1024 and every graph seeds at the black-belt
floor of 800. At the shipped level a ranked athlete's published graph mean sits **259 Elo
below their own target, 100% of the time** — the engine never finishes the climb inside the
bout counts this corpus provides. The sweep's entire 0.06 gain is that gap closing.

**(b) It cannot do better than the target, ever, at any K.** `replay_matches`'s converge
clamp sets `scale = (rank_target − graph_elo) / (prospective_mean − graph_elo)` whenever a
step would cross the target, so the graph mean lands *on* it and never past it. Seed an
athlete exactly at their target and `scale` is exactly 0: the rating freezes permanently.
Verified directly:

```
seeded at target, competitive_mult=2.5   -> snapshots {1200.0}
seeded at target, competitive_mult=20    -> snapshots {1200.0}
seeded at target, competitive_mult=400   -> snapshots {1200.0}
seeded at belt floor 800, huge K         -> max snapshot 1200.0, overshoot: False
```

And an adapter seeding each ranked athlete's first node at their own rank target instead of
the floor (K left at the shipped level) scores **0.6079 — byte-identical to
`rank-target-only`, paired difference +0.0000 [+0.0000, +0.0000] over 778 bouts.**

So the engine's reachable rating set is the segment [belt floor, rank_target] with the
target as an absorbing barrier. **`rank-target-only` is not an empirical rival; it is the
analytic ceiling of this K sweep.** Sweeping base K can move the engine along that segment
faster and nothing else. The answer to "what base K should ship" is therefore not a number
in the table — it is that **this criterion has nothing to award.**

That also dissolves the apparent PRIMARY/TERTIARY conflict in §4.2: the 0.06 in-sample gain
is real and mechanical (arrive at the prior sooner), while the held-out window is the years
where athletes have already accumulated bouts and closed part of the gap on their own, so
the advantage shrinks to −0.0110 and stops being significant. Both readings are the same
fact seen at two points along one convergence.

### 4.5 Three findings that are decision-relevant even though the verdict is FAIL

**1. The shipped CASUAL path is anti-predictive on this corpus.** `athlete-elo-L40` scores
**0.6961, worse than a coin flip** (0.6931) — the only learning arm in either study to do
so. The mechanism is (a): it pins a ranked athlete 286 Elo below their true level and then
predicts confidently from there. Scope honestly: `competitive_mult = 1.0` is gated to
non-leaderboard athletes (`repository.py:628`) and this corpus is the athlete corpus, so
this is not a measurement of app users. It IS a measurement that the gate is load-bearing —
removing it would not merely slow the climb, it would make the number actively misleading
for anyone with a real rank.

**2. The production engine is far behind plain Elo on prediction, and the gap is not K.**
`athlete-elo-L100` vs `elo-k100`, paired **+0.0734 [+0.0467, +0.1003]** — the largest gap
between two engines in either study, CI excluding zero. Even the post-hoc optimum L=1600
(0.6135) does not reach `elo-k100` (0.6059). This confirms the 2026-08-24 note's reading
("an engine can be well-designed for its actual job and still lose badly on a criterion it
was never aimed at") and now localises it: it is the rank-target anchor, not the K schedule.

**3. ADR-16 has already shrunk what this K controls, and that is measured.** For an athlete
covered by the pinned rating_v2 run, `replay_and_persist_athlete` **overwrites** V1's
`computed_elo` with the Glicko-2 projection, and `athletes.elo` / `elo_series` /
`graph_edges.elo` follow the projection, not this K. Measured read-only on prod today
(pinned run `cef15301-…`): **535 athlete graphs carry edges; 449 are V2-projected and 86 are
not** (MMA/wrestling, correctly held out by ADR-05 — the same 86 the 2026-08-26 pass found).
So `athlete_elo`'s base K decides a published rating for **86 graphs**, not the corpus. That
is the real blast radius, and it is small enough to be worth knowing before anyone budgets a
replay for it.

### 4.6 What this means for production

**Nothing changes.** Concretely:

- `analysis/athlete_elo.py` — `K_BASE_EARLY` / `K_BASE_MID` / `K_BASE_FLOOR` and
  `COMPETITIVE_K_MULT = 2.5` all stand. Untouched by this study: the runner never edits the
  module, it sweeps through the `competitive_mult` parameter that already exists.
- The competitive gate in `db/repository.py:628` — **keep it**, now with a measured reason
  (§4.5.1) rather than only the original design argument.
- `analysis/elo_calibration.py` — out of scope here; the previous study's conclusion stands.
- The `athlete-elo-base-k-sweep` backlog item — **CLOSED**. It asked for the one K on which
  a sweep would be "directly actionable"; the sweep ran, and the answer is that the K is not
  the binding constraint on this engine's forecasting quality at any value.

**The open question this replaces it with** is worth more than the one it closed: the
rank-target anchor is both the engine's ceiling and its floor. Whether a ranked athlete
should seed at their rank target (which §4.4b shows makes the engine *exactly* the static
prior — a degenerate rating, not a better one), whether the absorbing clamp should be a soft
prior instead of a hard barrier, or whether V1 should simply be retired for the last 86
graphs and everyone moved onto the V2 track, are all questions about the ANCHOR, not about K.
None of them is answerable by a sweep, and all of them are design decisions for the owner.

---

## 5. What changing this K would actually require

Recorded because the backlog item asked, not because this study recommends it. An
ELO-math change here is never casual (`research-methodology` §2e) and every step below
writes to production, so all of it is the owner's to run — a subagent never mutates prod.

1. **Change the pure function + fixtures first.** `analysis/athlete_elo.py` constants, then
   `tests/test_athlete_elo.py` and `tests/test_elo_convergence.py` green. The convergence
   tests assert the climb toward the rank target, so a K change moves them by design — that
   is the signal, not a failure to suppress.
2. **Full per-athlete replay.** Runbook: `docs/rating_v2/08_ESTADO_DO_CUTOVER.md` §"Runbook
   do replay completo". `uv run python -m analysis.rating_v2.replay` (read-only, confirms the
   corpus has not moved under the pin) → `uv run python -m scripts.backfill_edge_bouts
   --dry-run` then without it (~1300 athletes, SAVEPOINT per athlete). **Never
   `scripts.reprocess_all`** — it re-imports the dumps and resurrects the AA-011 phantom
   athletes (auto-memory `dumps-diverged-from-db`).
3. **Re-derive everything downstream of `computed_elo`, in dependency order.** Archetype
   pipeline → `scripts.assign_user_archetypes` → `export.ontology`. Baselines over athlete
   graphs must keep filtering `repository.rated_athlete_graph_ids`, or a V1-scale graph and a
   V2-scale graph end up averaged inside one `node_key`.
4. **Regenerate and publish the whole site bundle.** `uv run python -m export.site_data
   --full` (~10–12 min; that is the known N+1, not a hang), then commit + push `GrapplingArc`
   `main` — GitHub Pages publishes on push. The bundle is generated output and is never
   hand-edited.
5. **Validate before committing the site.** A known athlete's `athletes.elo` equals their
   rating in `athlete_rating_states_v2` under the pinned run; no pin-mismatch WARNING in
   step 2's log; a projected graph's `graph_edges.elo` in the ~1400–2300 band, not ~800; the
   public board and dossiers still render relative + % + "Grappling Rating (Glicko-2)", never
   a raw rating.
6. **Reversal** is `git revert` plus a re-run of step 2 — the replay is deterministic from
   `matches`, and there is no schema migration to undo.

Cost/benefit, stated plainly: that is a multi-hour prod operation whose measured effect
would land on **86 athlete graphs** (§4.5.3), in service of moving an engine along a segment
whose far end is a leaderboard snapshot it already has (§4.4). This study's recommendation
is not to spend it.

---

## 6. Artifacts

| What | Where |
|---|---|
| Runner (sweep + controls + verdict rules + VOID detector) | `scripts/research/athlete_elo_base_k.py` |
| Tests (synthetic, no DB) — the ladder≡multiplier identity, grid shape, live-knob and VOID guards, static control | `tests/test_research_athlete_elo_base_k.py` (8 cases) |
| Harness change that made the sweep possible | `analysis/poc/e0_rating_eval.py` — `AthleteEloEngine` gains `competitive_mult` + `name`; defaults reproduce the previous behaviour, `tests/test_poc_e0.py` green |
| Shared comparison machinery reused, not re-written | `scripts/research/e0_ksweep_whr.py` — `paired_delta`, `split_years`, `window`, `_best`, `write_block` (now takes its `marker`) |
| The study this answers | `docs/research/2026-09-14_e0_ksweep_whr.md` §4.4 |
| Replay runbook referenced by §5 | `docs/rating_v2/08_ESTADO_DO_CUTOVER.md` |
