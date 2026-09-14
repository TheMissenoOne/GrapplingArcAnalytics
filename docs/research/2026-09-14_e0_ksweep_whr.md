# PoC-E0 extension — Elo K past 80, and Whole-History Rating (PoC-E3)

Date: 2026-09-14 · Runner: `scripts/research/e0_ksweep_whr.py` · Instrument:
`analysis/poc/e0_rating_eval.py` (PoC-E0) · Backlog items: `k-sweep-alem-80`, `poc-e3-whr`.

Two open questions closed against one harness and one criterion.

> **Nothing in this document changes production.** `analysis/elo_calibration.py`'s
> K stays where it is, `analysis/rating_v2/` stays where it is, and no replay is
> proposed. A K change retroactively rewrites every athlete's rating and `elo_series`
> (`research-methodology` §2e, the iron rule) and is the owner's call, not a report's.
> This is evidence, and it is deliberately reported with the criteria that FAIL as
> well as the ones that pass.

---

## 1. Pre-registration

Written before the reported run; the criteria are literal code in the runner
(`run_corpus`), not prose reconstructed afterwards, so they cannot quietly move to fit
the numbers. A coarse `--quick` shakedown (K ∈ {40, 80, 200, 400}, w ∈ {40, 160},
200 bootstrap draws, scouting corpus only) was run first to prove the instrument
executes; it used these same criteria.

### 1.1 What was already known (the reason these items exist)

The 2026-08-24 E0 run swept Elo K over {10, 20, 30, 40, 60, 80} on both corpora and
recorded, in `docs/research/poc/e0_notes.md`:

> On BOTH corpora log loss decreases monotonically across the whole grid — best-in-grid
> is K=80 (mult) on scouting (0.4997) and on db (0.6015), not the production K=40.
> Neither corpus shows a minimum inside [10,80]; a wider sweep is needed before "higher
> K wins" is a finding rather than an artifact of a truncated grid.

A minimum at the last grid point is not a minimum. That is the whole of item 1.

Item 2 is the last unrun next-action in the same notes: *"WHR (PoC-E3) enters through
this same harness."* The plan is `docs/research/03_POC_PLANS.md` §PoC-E3.

### 1.2 Protocol (unchanged from E0 — that is the point)

Prequential walk-forward: every engine predicts P(a wins) for a bout *before* seeing
its result, then observes it. First corpus year burned in (observed, not scored). Mean
log loss is the headline, with a seeded percentile bootstrap over bouts
(`stats_rigor.bootstrap_ci`, 2000 draws). Brier and accuracy reported alongside.
Two corpora, both public: the ADCC-2026-women scouting records (ego-centric — see the
E0 docstring's caveat) and the closed `matches` table.

Every arm is scored on the identical bout stream in the identical order, which is what
licenses the paired comparisons below.

### 1.3 The grids

- **K**: the published grid {10, 20, 30, 40, 60, 80} plus a geometric extension
  {100, 125, 160, 200, 250, 320, 400}, each in both the flat and the
  win-type×stage-multiplied variant.
- **w** (WHR drift, Elo per √year): {20, 40, 80, 160, 320}, each with a per-bout refit
  and a per-year refit, and each read both as a MAP point estimate and with a
  Glickman variance deflation.

### 1.4 Verdict rules — fixed in advance

**K (reference = `elo-k80`, the incumbent best-in-grid):**

| id | rule |
|---|---|
| **K-PRIMARY** | the best K in the extended grid beats `elo-k80` by **more than the bootstrap CI width** of `elo-k80`'s log loss |
| **K-SECONDARY** | paired bootstrap of the per-bout log-loss difference (best − reference), 95% CI **excludes 0** |
| **K-TERTIARY** | K selected on the TUNE window (earlier years, split at the median bout) and scored on the held-out TEST window beats the reference there, paired CI excluding 0 |

**WHR (reference = the best E0 incumbent: Glicko-2 per-bout, Glicko-2 yearly, or Elo at
its best K, whichever wins on that corpus):**

| id | rule |
|---|---|
| **WHR-PRIMARY** | best WHR arm beats the best E0 incumbent by **more than the incumbent's bootstrap CI width** |
| **WHR-SECONDARY** | paired bootstrap of the per-bout difference, 95% CI **excludes 0** |
| **WHR-TERTIARY** | both sides' hyper-parameter selected on TUNE, compared on the held-out TEST window, paired CI excluding 0 |

**Why three and not one.** The PRIMARY rules are the ones named in the backlog items,
and they are stated first so they cannot be quietly dropped — but they are known in
advance to be *underpowered and mis-specified*: comparing a gap against the CI width of
one arm treats two arms evaluated on the SAME bouts as if they were independent
samples. On these corpora the incumbent's CI width is ≈ 0.04–0.08 log-loss units while
plausible engine gaps are ≈ 0.01–0.02, so PRIMARY can only fire on an implausibly large
effect. SECONDARY is the correctly-specified version of the same question (paired). And
both still select the hyper-parameter on the data they report, which is optimistic;
TERTIARY is the selection-free reading and is therefore the one to believe when the
three disagree. All three are reported for every corpus, pass or fail.

**Pre-registered secondary readings** (descriptive, no pass/fail attached): where the K
curve turns and whether that turn is interior to the grid; whether variance deflation
helps WHR; whether WHR's refit cadence matters the way Glicko-2's period granularity
did; and the fitted w, which §4 of the PoC-E3 plan wants regardless of the verdict as
the first *measured* value for "how fast does grappling strength drift".

### 1.5 What would make this run void

A corpus that changed size under the run, a non-deterministic ranking, or an arm whose
prediction could see its own result. The instrument's leak tests
(`tests/test_poc_e0.py`) and the WHR asserts (`tests/test_research_whr.py`) are what
stand behind that.

---

## 2. Method — the WHR implementation

Whole-History Rating: **Rémi Coulom, "Whole-History Rating: A Bayesian Rating System for
Players of Time-Varying Strength", Computers and Games 2008 (CG 2008), LNCS 5131,
pp. 113–124** — cited from memory; the scientific-papers MCP was not reachable from this
run, so the reference is given in full rather than as a resolved identifier, and the
implementation follows the paper's §2–§4 as described below rather than a verified
verbatim reading.

The model, as implemented in `scripts/research/e0_ksweep_whr.py:WhrModel`:

- Each player carries a rating *trajectory* r(t) on the natural Bradley–Terry scale
  (r = Elo / (400/ln 10) — identical to Glicko-2's `SCALE`, which is why the deflation
  below needs no unit conversion). The corpus dates bouts to the **year**, so one year
  is one rating node and a player's bouts within a year share a node.
- Likelihood: Bradley–Terry, P(i beats j) = 1/(1 + e^(r_j − r_i)).
- Prior: a Wiener process on each trajectory, r(t₂) − r(t₁) ~ N(0, w²·|t₂ − t₁|). One
  knob, `w`, in Elo per √year.
- Fit: MAP by **Newton's method, one player at a time**, holding every other player
  fixed — the paper's own scheme. The Hessian of one player's rating vector is
  tridiagonal (log-likelihood curvature −p(1−p) per game on the diagonal, ±1/(w²Δt)
  from the prior). Sweeps cycle over players in sorted order until the largest step
  falls below 1e-4.
- Anchoring: an undefeated player's MAP rating diverges, so one virtual win **and** one
  virtual loss against a rating-0 opponent are attached to each player's first node.
  That pins the scale and keeps the Hessian strictly negative definite even for a
  one-game player — which matters enormously here: the `matches` corpus has ~780 bouts
  over ~777 athletes.
- Uncertainty: the Newton step already produces the Hessian, so the posterior variance
  at a node is the corresponding diagonal of (−H)⁻¹. Predicting at a later time adds
  w²·Δt of accumulated drift. The `whr-w*` arms feed that variance through Glickman's
  g-factor, g(φ²) = 1/√(1 + 3φ²/π²), before the logistic; the `-map` arms do not. Both
  are reported because on a corpus this sparse, an undeflated MAP Bradley–Terry rating
  is exactly the overconfidence Glicko exists to fix, and a WHR that loses only because
  it was denied its own posterior would be a strawman.

Two deliberate simplifications, both marked in the source: a dense `numpy` solve
instead of the tridiagonal Thomas recursions (n = years-this-player-competed ≤ 19 here),
and per-bout refits warm-started with a capped 2 Newton sweeps rather than refitting to
convergence 780 times.

**Adapting a batch method to a prequential harness.** WHR fits a whole history at once;
E0 predicts one bout at a time. `WhrEngine` refits only on bouts already observed and
then predicts the next one, so no prediction sees its own result or any later one. Two
cadences are run: `refit='bout'` (matching `Glicko2PerBout`) and `refit='year'`
(matching `Glicko2Yearly`) — E0's 2026-08-24 reading was that *cadence*, not the rating
model, decided the Glicko comparison, so WHR is measured at both rather than at whichever
flatters it.

One harness change was needed and made: `evaluate` in `analysis/poc/e0_rating_eval.py`
was split into `stream_scores` (one prequential pass, returning the raw per-bout rows)
and `report_from_rows` (aggregation), because paired comparisons need the rows, not an
already-averaged mean. `evaluate` now calls both and its behaviour is unchanged.

### Reproduce

```bash
uv run python -m scripts.research.e0_ksweep_whr --self-check     # WHR math, no corpus
uv run python -m scripts.research.e0_ksweep_whr --quick          # coarse grids, ~15s
set -a; source .env; set +a                                      # DATABASE_URL, read-only
uv run python -m scripts.research.e0_ksweep_whr --source all     # the run below
```

---

## 3. Results

Generated by the runner; the block between the markers is REPLACED on each run — do not
hand-edit it.

<!-- E0KWHR:RESULTS -->

## Corpus: `scouting`

690 distinct bouts, 2008–2026 · dropped `{'draws': 1, 'no_year': 0, 'mirrors_folded': 34}` · first corpus year burned in · 689 scored predictions per arm · 45 arms in one prequential pass (28s).

TUNE years 2009–2024 (503 bouts) · TEST years 2025–2026 (186 bouts).

### K sweep — Elo by predictive log loss

| K | variant | log loss | 95% CI | Brier | accuracy |
|---|---|---|---|---|---|
| 10 | mult | 0.5814 | [0.5623, 0.6006] | 0.1974 | 0.726 |
| 10 | flat | 0.5932 | [0.5759, 0.6103] | 0.2025 | 0.716 |
| 20 | mult | 0.5469 | [0.5207, 0.5733] | 0.1834 | 0.739 |
| 20 | flat | 0.5582 | [0.5339, 0.5821] | 0.1879 | 0.732 |
| 30 | mult | 0.5287 | [0.4985, 0.5586] | 0.1764 | 0.739 |
| 30 | flat | 0.5388 | [0.5112, 0.5664] | 0.1804 | 0.739 |
| 40 | mult | 0.5174 | [0.4845, 0.5505] | 0.1722 | 0.747 |
| 40 | flat | 0.5263 | [0.4959, 0.5563] | 0.1756 | 0.744 |
| 60 | mult | 0.5050 | [0.4679, 0.5423] | 0.1675 | 0.745 |
| 60 | flat | 0.5114 | [0.4773, 0.5460] | 0.1700 | 0.754 |
| 80 ← | mult | 0.4997 | [0.4596, 0.5406] | 0.1654 | 0.751 |
| 80 ← | flat | 0.5037 | [0.4666, 0.5410] | 0.1671 | 0.760 |
| 100 | mult | 0.4986 | [0.4562, 0.5424] | 0.1646 | 0.758 |
| 100 | flat | 0.5001 | [0.4608, 0.5406] | 0.1656 | 0.763 |
| 125 | mult | 0.5010 | [0.4555, 0.5479] | 0.1648 | 0.766 |
| 125 | flat | 0.4991 | [0.4571, 0.5417] | 0.1649 | 0.766 |
| 160 | mult | 0.5090 | [0.4594, 0.5609] | 0.1665 | 0.763 |
| 160 | flat | 0.5022 | [0.4565, 0.5492] | 0.1653 | 0.761 |
| 200 | mult | 0.5229 | [0.4682, 0.5796] | 0.1697 | 0.758 |
| 200 | flat | 0.5100 | [0.4599, 0.5612] | 0.1669 | 0.763 |
| 250 | mult | 0.5462 | [0.4845, 0.6083] | 0.1749 | 0.750 |
| 250 | flat | 0.5244 | [0.4692, 0.5808] | 0.1701 | 0.755 |
| 320 | mult | 0.5881 | [0.5175, 0.6604] | 0.1835 | 0.737 |
| 320 | flat | 0.5517 | [0.4898, 0.6149] | 0.1761 | 0.751 |
| 400 | mult | 0.6477 | [0.5654, 0.7324] | 0.1943 | 0.735 |
| 400 | flat | 0.5919 | [0.5202, 0.6631] | 0.1844 | 0.735 |

### K verdicts

- Reference `elo-k80`: 0.4997, CI width **0.0810**.
- Best in grid: `elo-k100` at 0.4986 (gain +0.0011).
- Curve minimum (mult variant): **K=100** — INTERIOR (the grid now contains the turn).
- **K-PRIMARY** (gain > CI width 0.0810): **FAIL** (+0.0011).
- **K-SECONDARY** (paired bootstrap of best−reference, 95% CI excludes 0): **FAIL** (-0.0011 [-0.0055, +0.0032]).
- **K-TERTIARY** (K chosen on TUNE = `elo-k100`, scored on TEST vs `elo-k80`): **FAIL** — 0.4899 vs 0.4865, paired +0.0034 [-0.0051, +0.0127].

### WHR and the E0 incumbents

| engine | log loss | 95% CI | Brier | accuracy |
|---|---|---|---|---|
| `whr-w160` | 0.4843 | [0.4499, 0.5196] | 0.1590 | 0.777 |
| `whr-w80` | 0.4878 | [0.4538, 0.5228] | 0.1609 | 0.776 |
| `whr-w80-map` | 0.4883 | [0.4468, 0.5309] | 0.1611 | 0.776 |
| `whr-w160-map` | 0.4900 | [0.4440, 0.5376] | 0.1601 | 0.777 |
| `whr-w320` | 0.4900 | [0.4548, 0.5252] | 0.1606 | 0.776 |
| `whr-w40-map` | 0.4920 | [0.4521, 0.5344] | 0.1629 | 0.773 |
| `whr-w40` | 0.4921 | [0.4581, 0.5288] | 0.1628 | 0.773 |
| `whr-w20` | 0.4945 | [0.4600, 0.5319] | 0.1639 | 0.764 |
| `whr-w20-map` | 0.4945 | [0.4549, 0.5375] | 0.1640 | 0.764 |
| `elo-k100` | 0.4986 | [0.4562, 0.5424] | 0.1646 | 0.758 |
| `elo-k80` | 0.4997 | [0.4596, 0.5406] | 0.1654 | 0.751 |
| `glicko2-perbout-tau0.5` | 0.5060 | [0.4706, 0.5435] | 0.1687 | 0.757 |
| `whr-w320-map` | 0.5166 | [0.4588, 0.5773] | 0.1638 | 0.776 |
| `whr-w160-year` | 0.5418 | [0.5061, 0.5778] | 0.1839 | 0.712 |
| `whr-w80-year` | 0.5445 | [0.5104, 0.5795] | 0.1857 | 0.690 |
| `whr-w320-year` | 0.5477 | [0.5085, 0.5867] | 0.1840 | 0.705 |
| `whr-w40-year` | 0.5479 | [0.5142, 0.5826] | 0.1873 | 0.689 |
| `whr-w20-year` | 0.5497 | [0.5159, 0.5842] | 0.1882 | 0.687 |
| `win-rate` | 0.5774 | [0.5582, 0.5965] | 0.1954 | 0.718 |
| `glicko2-tau0.5` | 0.5795 | [0.5428, 0.6178] | 0.2001 | 0.679 |
| `constant-0.5` | 0.6931 | [0.6931, 0.6931] | 0.2500 | 0.500 |

### WHR verdicts

- E0 incumbent best: `elo-k100` 0.4986, CI width **0.0862**.
- WHR best: `whr-w160` 0.4843 (gain +0.0144).
- **WHR-PRIMARY** (gain > incumbent CI width): **FAIL** (+0.0144 vs 0.0862).
- **WHR-SECONDARY** (paired bootstrap WHR−incumbent, 95% CI excludes 0): **FAIL** (-0.0144 [-0.0291, +0.0011]).
- **WHR-TERTIARY** (both chosen on TUNE — `whr-w160` vs `elo-k100` — scored on TEST): **FAIL** — 0.4645 vs 0.4899, paired -0.0254 [-0.0599, +0.0051].

### Post-hoc (NOT pre-registered — read as exploratory)

- Curve minimum vs the SHIPPED production K: `elo-k100` 0.4986 vs `elo-k40` 0.5174, paired -0.0188 [-0.0345, -0.0033] — CI excludes 0.
- WHR best vs the other per-bout Bayesian arm: `whr-w160` vs `glicko2-perbout-tau0.5`, paired -0.0217 [-0.0366, -0.0077].

### Slices for the finalists

| engine | slice | n | log loss | 95% CI | accuracy |
|---|---|---|---|---|---|
| `elo-k100` | experienced | 134 | 0.5645 | [0.4597, 0.6789] | 0.716 |
| `elo-k100` | cold_start | 555 | 0.4827 | [0.4375, 0.5286] | 0.768 |
| `elo-k80` | experienced | 134 | 0.5632 | [0.4656, 0.6665] | 0.687 |
| `elo-k80` | cold_start | 555 | 0.4844 | [0.4422, 0.5278] | 0.767 |
| `whr-w160` | experienced | 134 | 0.5361 | [0.4457, 0.6299] | 0.739 |
| `whr-w160` | cold_start | 555 | 0.4718 | [0.4341, 0.5087] | 0.786 |

## Corpus: `db`

780 distinct bouts, 2008–2026 · dropped `{'not_final': 0, 'excluded_win_type': 56, 'no_winner': 75, 'no_year': 0}` · first corpus year burned in · 778 scored predictions per arm · 45 arms in one prequential pass (107s).

TUNE years 2009–2024 (494 bouts) · TEST years 2025–2026 (284 bouts).

### K sweep — Elo by predictive log loss

| K | variant | log loss | 95% CI | Brier | accuracy |
|---|---|---|---|---|---|
| 10 | mult | 0.6445 | [0.6352, 0.6543] | 0.2277 | 0.605 |
| 10 | flat | 0.6494 | [0.6410, 0.6580] | 0.2295 | 0.611 |
| 20 | mult | 0.6296 | [0.6174, 0.6424] | 0.2220 | 0.604 |
| 20 | flat | 0.6334 | [0.6220, 0.6453] | 0.2232 | 0.614 |
| 30 | mult | 0.6216 | [0.6078, 0.6363] | 0.2190 | 0.607 |
| 30 | flat | 0.6245 | [0.6113, 0.6384] | 0.2198 | 0.616 |
| 40 | mult | 0.6164 | [0.6012, 0.6327] | 0.2170 | 0.608 |
| 40 | flat | 0.6186 | [0.6042, 0.6342] | 0.2176 | 0.616 |
| 60 | mult | 0.6102 | [0.5927, 0.6289] | 0.2148 | 0.612 |
| 60 | flat | 0.6114 | [0.5946, 0.6292] | 0.2149 | 0.616 |
| 80 ← | mult | 0.6071 | [0.5879, 0.6280] | 0.2138 | 0.613 |
| 80 ← | flat | 0.6075 | [0.5888, 0.6273] | 0.2137 | 0.616 |
| 100 | mult | 0.6059 | [0.5848, 0.6286] | 0.2135 | 0.613 |
| 100 | flat | 0.6058 | [0.5854, 0.6279] | 0.2132 | 0.616 |
| 125 | mult | 0.6062 | [0.5831, 0.6311] | 0.2139 | 0.612 |
| 125 | flat | 0.6055 | [0.5827, 0.6298] | 0.2134 | 0.615 |
| 160 | mult | 0.6094 | [0.5829, 0.6364] | 0.2152 | 0.616 |
| 160 | flat | 0.6080 | [0.5819, 0.6348] | 0.2146 | 0.612 |
| 200 | mult | 0.6158 | [0.5858, 0.6471] | 0.2175 | 0.618 |
| 200 | flat | 0.6137 | [0.5847, 0.6443] | 0.2168 | 0.614 |
| 250 | mult | 0.6274 | [0.5928, 0.6637] | 0.2211 | 0.616 |
| 250 | flat | 0.6244 | [0.5910, 0.6595] | 0.2203 | 0.611 |
| 320 | mult | 0.6488 | [0.6084, 0.6912] | 0.2268 | 0.613 |
| 320 | flat | 0.6448 | [0.6050, 0.6868] | 0.2259 | 0.609 |
| 400 | mult | 0.6798 | [0.6309, 0.7306] | 0.2340 | 0.607 |
| 400 | flat | 0.6744 | [0.6272, 0.7243] | 0.2329 | 0.607 |

### K verdicts

- Reference `elo-k80`: 0.6071, CI width **0.0401**.
- Best in grid: `elo-k125-flat` at 0.6055 (gain +0.0015).
- Curve minimum (mult variant): **K=100** — INTERIOR (the grid now contains the turn).
- **K-PRIMARY** (gain > CI width 0.0401): **FAIL** (+0.0015).
- **K-SECONDARY** (paired bootstrap of best−reference, 95% CI excludes 0): **FAIL** (-0.0015 [-0.0068, +0.0041]).
- **K-TERTIARY** (K chosen on TUNE = `elo-k160-flat`, scored on TEST vs `elo-k80`): **FAIL** — 0.6868 vs 0.6723, paired +0.0145 [-0.0019, +0.0315].

### WHR and the E0 incumbents

| engine | log loss | 95% CI | Brier | accuracy |
|---|---|---|---|---|
| `elo-k125-flat` | 0.6055 | [0.5827, 0.6298] | 0.2134 | 0.615 |
| `elo-k80` | 0.6071 | [0.5879, 0.6280] | 0.2138 | 0.613 |
| `whr-w80` | 0.6102 | [0.5880, 0.6326] | 0.2141 | 0.605 |
| `whr-w40` | 0.6107 | [0.5887, 0.6335] | 0.2144 | 0.607 |
| `whr-w20` | 0.6110 | [0.5889, 0.6335] | 0.2145 | 0.608 |
| `whr-w160` | 0.6116 | [0.5890, 0.6337] | 0.2144 | 0.609 |
| `whr-w80-map` | 0.6130 | [0.5854, 0.6409] | 0.2161 | 0.605 |
| `whr-w40-map` | 0.6135 | [0.5868, 0.6410] | 0.2163 | 0.607 |
| `whr-w20-map` | 0.6139 | [0.5875, 0.6412] | 0.2164 | 0.608 |
| `glicko2-perbout-tau0.5` | 0.6147 | [0.5907, 0.6393] | 0.2163 | 0.619 |
| `whr-w160-map` | 0.6163 | [0.5865, 0.6466] | 0.2168 | 0.609 |
| `whr-w320` | 0.6201 | [0.5985, 0.6427] | 0.2169 | 0.613 |
| `whr-w80-year` | 0.6213 | [0.6024, 0.6408] | 0.2187 | 0.599 |
| `whr-w160-year` | 0.6214 | [0.6023, 0.6407] | 0.2187 | 0.598 |
| `whr-w40-year` | 0.6219 | [0.6030, 0.6413] | 0.2189 | 0.598 |
| `whr-w20-year` | 0.6221 | [0.6033, 0.6417] | 0.2189 | 0.596 |
| `whr-w320-year` | 0.6261 | [0.6075, 0.6453] | 0.2200 | 0.597 |
| `glicko2-tau0.5` | 0.6277 | [0.6087, 0.6480] | 0.2216 | 0.590 |
| `whr-w320-map` | 0.6356 | [0.5994, 0.6726] | 0.2212 | 0.613 |
| `win-rate` | 0.6455 | [0.6319, 0.6600] | 0.2274 | 0.598 |
| `constant-0.5` | 0.6931 | [0.6931, 0.6931] | 0.2500 | 0.500 |

### WHR verdicts

- E0 incumbent best: `elo-k125-flat` 0.6055, CI width **0.0471**.
- WHR best: `whr-w80` 0.6102 (gain -0.0046).
- **WHR-PRIMARY** (gain > incumbent CI width): **FAIL** (-0.0046 vs 0.0471).
- **WHR-SECONDARY** (paired bootstrap WHR−incumbent, 95% CI excludes 0): **FAIL** (+0.0046 [-0.0024, +0.0113]).
- **WHR-TERTIARY** (both chosen on TUNE — `whr-w80-map` vs `elo-k160-flat` — scored on TEST): **FAIL** — 0.7007 vs 0.6868, paired +0.0139 [+0.0003, +0.0281].

### Post-hoc (NOT pre-registered — read as exploratory)

- Curve minimum vs the SHIPPED production K: `elo-k100` 0.6059 vs `elo-k40` 0.6164, paired -0.0105 [-0.0186, -0.0016] — CI excludes 0.
- WHR best vs the other per-bout Bayesian arm: `whr-w80` vs `glicko2-perbout-tau0.5`, paired -0.0045 [-0.0098, +0.0005].

### Slices for the finalists

| engine | slice | n | log loss | 95% CI | accuracy |
|---|---|---|---|---|---|
| `elo-k125-flat` | experienced | 25 | 0.5989 | [0.4267, 0.7868] | 0.680 |
| `elo-k125-flat` | cold_start | 753 | 0.6058 | [0.5841, 0.6294] | 0.613 |
| `elo-k80` | experienced | 25 | 0.5740 | [0.4230, 0.7291] | 0.720 |
| `elo-k80` | cold_start | 753 | 0.6082 | [0.5894, 0.6277] | 0.610 |
| `whr-w80` | experienced | 25 | 0.6718 | [0.4840, 0.8802] | 0.640 |
| `whr-w80` | cold_start | 753 | 0.6081 | [0.5867, 0.6300] | 0.604 |

<!-- E0KWHR:RESULTS -->

---

## 4. Reading and verdicts

Run: 2026-09-14, both corpora, 45 arms in one prequential pass each (48s scouting,
97s db), 2000 bootstrap draws. The whole block in §3 was produced twice by two separate
processes and came out **byte-identical**, so the `PYTHONHASHSEED` tie-break class of
non-determinism that reshuffled the analogue lists in 2026-07-07 is not present here.

Note on comparability: the `matches` corpus has grown from 732 eligible bouts (the
2026-08-24 E0 run) to **780**. Every incumbent was therefore re-run here rather than
quoted, so all comparisons inside this document are on one corpus snapshot; the small
drifts against `poc/e0.md` (`elo-k40` db 0.6110 → 0.6164) are the corpus, not the
instrument.

### 4.1 `k-sweep-alem-80` — the curve turns, and the turn is not worth anything

**The question is answered: the grid was truncated, and the minimum is just past its
old edge.** On BOTH corpora the mult-variant curve bottoms at **K = 100** (scouting
0.4986, db 0.6059) and then climbs — steeply. By K=400 log loss is *worse than the
production K=40* on both (scouting 0.6477, db 0.6798), and on scouting it is within
0.05 of the coin flip. So "log loss falls monotonically, higher K keeps winning" was
exactly the truncated-grid artifact the note suspected. That caveat is now closed.

| | scouting | db |
|---|---|---|
| curve minimum (mult) | **K=100**, 0.4986 | **K=100**, 0.6059 |
| best arm in grid (either variant) | `elo-k100` 0.4986 | `elo-k125-flat` 0.6055 |
| K=80 (previous best-in-grid) | 0.4997 | 0.6071 |
| K=400 | 0.6477 | 0.6798 |

**All three pre-registered criteria FAIL, on both corpora.**

| criterion | scouting | db |
|---|---|---|
| K-PRIMARY (gain > CI width) | FAIL (+0.0011 vs 0.0810) | FAIL (+0.0015 vs 0.0401) |
| K-SECONDARY (paired CI excludes 0) | FAIL (−0.0011 [−0.0055, +0.0032]) | FAIL (−0.0015 [−0.0068, +0.0041]) |
| K-TERTIARY (chosen on TUNE, scored on TEST) | FAIL (+0.0034 [−0.0051, +0.0127]) | FAIL (+0.0145 [−0.0019, +0.0315]) |

The reason is visible in the table and is the actual finding: **the curve has a broad
flat bottom, not a point.** K=80, K=100 and K=125 (mult variant) span 0.0024 of log
loss on scouting (0.4986–0.5010) and 0.0012 on db (0.6059–0.6071) — an order of
magnitude below those same arms' own sampling error (CI widths 0.04–0.09). Widen to
K=60…160 and the band is still only 0.0104 / 0.0043. They are the same engine as far as
this corpus can tell. K-TERTIARY is the sharpest statement of that: selecting the
best-on-TUNE K (100 on scouting, 160-flat on db) and scoring it on the held-out later
years makes things *worse* than just keeping K=80, on both corpora. Fine-tuning inside
the plateau does not generalise; it fits the tuning window's noise.

**What IS decision-relevant (post-hoc, not pre-registered).** The interesting contrast
is not K=80 vs K=100 — it is the plateau vs the *shipped* value. `elo-k100` beats
`elo-k40` by −0.0188 [−0.0345, −0.0033] on scouting and −0.0105 [−0.0186, −0.0016] on
db; **both paired intervals exclude zero, on both corpora, in the same direction.** So
the direction the 2026-08-24 run flagged survives the wider grid: by held-out
prediction, production K=40 is measurably *too slow*, and a value somewhere in the
60–160 plateau is better. This was computed after seeing the grid and is labelled
exploratory for that reason — it is evidence to re-test deliberately, not a licence.

**Recommendation to the owner: still no production K change from this run.** Three
reasons, in order of weight. (1) `calibrate_k_factor`'s K is not this engine's K —
production K feeds `athlete_elo`'s graph-growth schedule (belt floor × gap-to-target ×
2.5 competitive × temporal decay), and the E0 db run already measured that engine as the
*worst* learning arm on this criterion precisely because it is aimed at a different job;
a K fitted on bout-outcome log loss is not transferable to it without re-deriving the
whole schedule. (2) Any K change rewrites every athlete's rating and `elo_series` and
needs a gated full replay. (3) The honest effect size is ~0.01–0.02 log-loss units on a
corpus where the best arm clears a coin flip by 12% — real, but not urgent. If the owner
does want to act, the cheapest correct next step is **not** "set K=100": it is to run
this same harness against `athlete_elo`'s own K schedule (the `AthleteEloEngine` adapter
already exists in E0) and sweep *that* engine's base K, because that is the K production
actually ships.

### 4.2 `poc-e3-whr` — REJECTED, and informative in the rejection

**All three pre-registered criteria FAIL, on both corpora.**

| criterion | scouting | db |
|---|---|---|
| WHR best | `whr-w160` 0.4843 | `whr-w80` 0.6102 |
| E0 incumbent best | `elo-k100` 0.4986 | `elo-k125-flat` 0.6055 |
| WHR-PRIMARY (gain > incumbent CI width) | FAIL (+0.0144 vs 0.0862) | FAIL (−0.0046 vs 0.0471) |
| WHR-SECONDARY (paired CI excludes 0) | FAIL (−0.0144 [−0.0291, **+0.0011**]) | FAIL (+0.0046 [−0.0024, +0.0113]) |
| WHR-TERTIARY (TUNE-selected, TEST-scored) | FAIL (−0.0254 [−0.0599, +0.0051]) | FAIL (**+0.0139 [+0.0003, +0.0281]**) |

Two different failures, and the difference between them is the whole reading.

On the **scouting** corpus WHR is the best arm in the entire table — `whr-w160` at
0.4843 beats every Elo, both Glicko-2 variants and the win-rate baseline — and it misses
significance by 0.0011 of CI (the paired interval's upper end is +0.0011, i.e. it fails
by about a thousandth of a log-loss unit). Read as "promising but unproven on a corpus
that cannot prove it": the scouting records are ego-centric and 69% roster-wins, which
is the bias E0's docstring already disqualifies for engine *selection*.

On the **closed `matches` corpus** WHR does not win. It ties Elo within noise overall,
and the selection-free TERTIARY reading is the only interval in this entire run whose CI
excludes zero **against** the hypothesis: +0.0139 [+0.0003, +0.0281], WHR significantly
*worse* out of sample. That is the corpus that matters for engine selection, and it says
no.

**Why, structurally.** WHR's advantage is retroactively propagating information backwards
through a dense opponent graph — beating someone who is later revealed to be strong
should raise your *past* rating. The `matches` corpus is **780 bouts over 777 athletes**:
barely more than one bout per athlete, an opponent graph that is mostly disconnected
pairs. There is almost no path along which to propagate anything, so WHR degenerates
toward "Bradley-Terry with a virtual win and a virtual loss" and inherits the same
information Elo has, minus Elo's cheapness. This is not a defect of the implementation;
it is the corpus telling you which family of method it can support. The same sparsity is
why the τ sweep was a null result in the first E0 run.

**Consequence for PoC-E3's accept criterion:** the plan's step 3 said WHR becomes a
shadow run if accepted. It is not accepted. No `engine_version`, no ADR-02 pinned run, no
re-derivation of the site's `RD ≤ 200` gate. Nothing moves.

### 4.3 The three secondary readings (no pass/fail attached)

**1. Update cadence dominates the rating model — again, and now for a second engine
family.** WHR refit per bout vs refit per calendar year:

| | per-bout | per-year | gap |
|---|---|---|---|
| scouting (best w) | 0.4843 | 0.5418 | 0.058 |
| db (best w) | 0.6102 | 0.6213 | 0.011 |

That is the same effect, same direction and same magnitude class as Glicko-2's
yearly → per-bout move in the 2026-08-24 run (scouting 0.5795 → 0.5060). Cadence is worth
**more than the choice between Elo, Glicko-2 and WHR**: with exactly one exception
(`whr-w320-map` on db, the least-regularised arm in the whole run), every per-bout arm
beats every per-year arm of its own family on both corpora. The E0 note's diagnosis
generalises to a second engine family, which makes it the single most transferable
result in this document.

**2. Variance deflation earns its keep, and its value scales with the drift it is
insuring against.** Deflated minus MAP log loss, by w:

| w (Elo/√year) | 20 | 40 | 80 | 160 | 320 |
|---|---|---|---|---|---|
| scouting | 0.0000 | **+0.0001** | −0.0005 | −0.0057 | −0.0266 |
| db | −0.0029 | −0.0028 | −0.0028 | −0.0047 | −0.0155 |

(negative = deflation helps; w=40 on scouting is the one cell where it very slightly
hurts.) On scouting the two arms are indistinguishable below
w=80 and then the gap opens by two orders of magnitude; on db deflation buys a flat
~0.003 everywhere and 0.016 at w=320. Interpretation: deflation is insurance against
overconfident extreme ratings, and a small w already prevents those through the prior —
so the insurance is worth what the volatility costs. Reported because a WHR evaluated
without its own posterior would have been a strawman, and the strawman is measurably
worse: `whr-w320-map` (0.6356 on db) falls behind every deflated WHR arm and behind
Glicko-2 per-bout.

**3. The fitted drift w — the PoC-E3 §4 deliverable — is w ≈ 80–160 Elo per √year, and
it is weakly identified.** Best on scouting w=160, best on db w=80. But look at the
surface before quoting it: on db, w ∈ {20, 40, 80, 160} spans 0.6110 → 0.6107 → 0.6102 →
0.6116, a total range of 0.0014, far inside the noise. Only w=320 is clearly rejected
(0.6201). So the corpus supports **"an order of magnitude around 100 Elo/√year, and
certainly under 320"**, not a fitted number.

That still beats what is in production. `athlete_elo`'s temporal decay guesses a 36-month
K half-life with no measurement behind it; this is at least an out-of-sample-scored
bound, on the right corpus, in a unit that converts (w=100 Elo/√year means a year of
absence adds ~100 Elo of sd to a rating's uncertainty — which is large relative to the
seed RD of 250 and says a 3-year-idle athlete's rating carries essentially no
information). **Do not turn this into a constant yet.** Use it as the prior range for a
deliberate calibration if `athlete_elo`'s half-life is ever re-fit.

### 4.4 What this means for production

**Nothing changes.** No K change, no engine change, no replay, no `engine_version`, no
site gate re-derivation. Concretely:

- `analysis/elo_calibration.py` — K stays 40. The evidence that it is too slow is real
  and now reproducible, but it is evidence against the *wrong engine's* K (§4.1) and
  below the bar for a full replay.
- `analysis/rating_v2/` — untouched. WHR was rejected on the corpus that counts.
- `analysis/athlete_elo.py` — the 36-month half-life stands; §4.3 gives it a prior range
  for the day someone re-fits it.
- `calibrate_k_factor`'s target-σ mode — still not retired, but it now has a second
  independent reason to distrust it: its criterion (match a target spread) is not merely
  different from held-out prediction, it would be *insensitive* to the entire 60–160
  plateau this run says is flat.

**If the owner wants one follow-up, it is this one:** sweep `athlete_elo`'s OWN base K
through this harness via the existing `AthleteEloEngine` adapter. That engine is what
ships, it scored worst in the E0 db run, and it is the only K on which a sweep result
would be directly actionable. Everything else here is measurement of engines production
does not run.

---

## 5. Artifacts

| What | Where |
|---|---|
| Runner (K sweep + WHR + verdict rules) | `scripts/research/e0_ksweep_whr.py` |
| WHR unit tests (synthetic, no DB) | `tests/test_research_whr.py` (11 tests) |
| Harness split that made pairing possible | `analysis/poc/e0_rating_eval.py` — `stream_scores` / `report_from_rows`; `evaluate` unchanged in behaviour, `tests/test_poc_e0.py` green (21) |
| Previous E0 runs this extends | `docs/research/poc/e0.md`, `docs/research/poc/e0_notes.md` |
| The plan being answered | `docs/research/03_POC_PLANS.md` §PoC-E3 |
