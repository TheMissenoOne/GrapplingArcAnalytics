# RRB-derived partner strength vs the difficulty/intensity sliders — results

Pre-registration: [`rrb_round_rating_prereg.md`](rrb_round_rating_prereg.md) — §1–§11 written before
any arm was scored, the **addendum** (§A1–A5: granularity arms, the objective target, the mapper
defect) written after §1–§11 were scored and before any addendum arm was.
Runner: `scripts/research/rrb_round_rating.py`. Tests: `tests/test_rrb_round_rating.py` (12 pass).
Artefacts: `out/rrb_study/` (gitignored) — `owner_rounds.json` (private fixture),
`owner_results.json`, `corpus_q1.json`, `corpus_q2.json`, `addendum.json`, `sweep.csv`, 3 PNGs.

Run 2026-09-12 against prod **read-only**. No database write, no replay persisted, no artefact
regenerated. Dataset (a) appears here as aggregates only.

> **Read §1 first.** The first pass of this study used a broken mapper and reached the opposite
> owner-side conclusion. The second external report caught it, the defect reproduced exactly, and
> everything below is the corrected run. Both readings are kept so the size of the error is visible.

---

## 0. Verdicts

| id | hypothesis | verdict | the number |
|---|---|---|---|
| **H1** | *(owner, T1)* RRB dominance separates `succeeded`/`failed` better than `difficulty` | **PASS** | ΔAUC **+0.195** [+0.092, +0.306]; RRB 0.923 vs difficulty 0.750 |
| **H1b** | H1 survives the leakage control (`terminal=drop`) | **PASS** | ΔAUC **+0.193** [+0.089, +0.306]; RRB still 0.915 [0.852, 0.965] |
| **T2** | *(owner, objective)* finish-side from the prefix, vs `difficulty` | **PASS for the combined arm, NULL for plain actions** | `actions_states` **0.989**, Δ **+0.116** [+0.022, +0.235]; plain `actions` 0.956, Δ +0.082 [−0.019, +0.197] |
| **H2** | *(owner)* the A4 hybrid beats production on prequential log-loss | **NULL** | Δ **−0.0015** [−0.0639, +0.0560] — a tie, at n = 100 |
| **H2n** | *(owner)* A4 beats deleting the sliders and substituting nothing | **PASS** | Δ **−0.0682** [−0.0817, −0.0546] |
| **H2x** | *(added)* production A0 beats A0n | **PASS** | Δ **−0.0667** [−0.1252, −0.0033] |
| **H3** | *(corpus)* dominance identifies the recorded winner | **PASS** | best `states_occ` **0.849** [0.809, 0.887]; pilot's cell 0.796 |
| **H3b** | H3 is not a submission tautology | **PASS** | `states_occ_drop` 0.749 [0.699, 0.797]; `actions_drop` 0.727 [0.674, 0.777] |
| **H4** | *(corpus, T3)* an RRB-updated rating forecasts next year's winners better than production | **NULL** | no arm, no fold, no interval excluding 0 — and no arm beats a coin flip |
| **H5** | the length artefact is controlled at γ = 1 | **FAIL as specified** | γ=1 **over-corrects** (ρ −0.23 … −0.47). The length-neutral arms are the state/edge ones (ρ −0.04 … −0.16) and γ = 0.5 for actions (ρ −0.04) |

Death rules: **none fired on the corrected run.** Rule 3 (leakage) fired on the *broken-mapper*
run and is what made the first pass reject the idea — see §1. Rule 2 (both-sides floor) no longer
binds: the canonical mapper lifts owner rounds with both corners mapped well above the 18 measured
with the broken one. Rule 6 (the lazy death) did **not** fire: substituting nothing is worse than
either input, decisively.

---

## 1. The defect, and how much it moved

The second external report said the production mapper covers **28.7 %** of the owner's events
because it reads **localized labels**, and that fixing aliases reaches **50.0 %**. Both reproduce
here exactly:

| mapper | coverage | codes that appear |
|---|---|---|
| `lamas_state(raw label)` — the first pass | **28.75 %** (184/640) | no `PGD`, no `BTKA`, no `BTK` |
| `lamas_state(technique_match.clean_label(label, type))` | **50.00 %** (320/640) | `BTK` **94**, `BTKA` 20, `PGD` 22 appear |

The owner logs in **pt-BR**: `control/Costas` (108 entries), `control/Montada` (67),
`guard/Meia Guarda` (53), `transition/Puxada para Guarda` (22). `lamas_chain`'s label rules match
English tokens (`"back control"`, `"hooks in"`, `"body triangle"`, the guard-pull and clinch
lists), so **back control — the single most logged action in the owner's entire history — was
invisible**. `analysis/technique_match.clean_label` already existed and already solved this.

What that one bug did to the conclusion, same data, same statistic, same seed:

| | broken mapper | canonical mapper |
|---|---|---|
| H1 (ΔAUC vs difficulty) | +0.040 [−0.117, +0.186] → **NULL** | **+0.195 [+0.092, +0.306] → PASS** |
| H1b (leakage control) | −0.111 [−0.303, +0.075], arm at **chance** (0.645) | **+0.193 [+0.089, +0.306], arm at 0.915** |
| H2 (vs production, log-loss) | +0.0237 → RRB worse | **−0.0015 → tie** |
| RRB AUC on T1 | 0.802 | **0.923** |

The first pass's headline — "the advantage is a submission tautology" — was an artefact: at 28.75 %
coverage, removing the submission family left 64 rounds with almost nothing in them. At 50 %, the
back-take and guard-pull codes carry the signal on their own and the control barely costs anything.

The 320 entries that *still* do not map — `Montada`, `Meia Guarda`, `Quatro Apoios`,
`Guarda Fechada` — are **dwell/position states**, which the Lamas action space excludes by design
(its rule 2: those are the pause between actions). The second report reaches the same reading.

## 2. The answer, plainly

**Yes — with the caveat that "better" means "better at describing the round", and "as good as" at
feeding the rating.**

Three numbers carry it.

1. **ΔAUC +0.195 [+0.092, +0.306].** RRB dominance separates the owner's `succeeded` from `failed`
   rounds at AUC 0.923 against the difficulty slider's 0.750, and the paired interval clears 0 with
   room. It **survives** the leakage control at 0.915 — the signal is not "I got the submission".
2. **Δ log-loss −0.0015 [−0.0639, +0.0560].** Drop RRB into production's actual Glicko-2 slot
   (replace `70 × (difficulty − 5)` with `400·log10(P/(1−P))`) and the forecast is a **statistical
   tie** — with RRB's discrimination far ahead (AUC 0.921 vs 0.739). This is the number the product
   decision rests on: **removing the step costs the rating nothing measurable.**
3. **H4 NULL in all three folds, and no arm beats a coin flip.** On the public corpus, *no* update
   rule — RRB's, production's, or any granularity — predicts next year's winners better than 0.5.
   RRB describes a bout well and forecasts a rating badly, and so does what runs today (§5c).

### What to do

| | recommendation | strength of evidence |
|---|---|---|
| **`intensity` stepper** | **Delete it.** No rating path reads it — it reaches nothing. | certain (code, not statistics) |
| **`difficulty` stepper** | **Can be deleted**, with RRB in its place, at measured parity on log-loss and a large gain on discrimination. It must **not** be deleted with *nothing* in its place — that is worse than both, decisively (Δ +0.067 / +0.068). | good, but a **tie is not a win**: n = 100 rounds cannot separate the two. If the owner wants a *win* before shipping, the cheap path is more rounds, not more math. |
| **Which RRB variant** | `actions` or `states_occ` at **γ = 1, terminal = `marginal`**, through `clean_label`. Both reach AUC ≈ 0.92 on T1 and tie production on log-loss. On the *objective* target the combined `actions_states` arm is the only one that beats difficulty with a clean interval (0.989, Δ +0.116 [+0.022, +0.235]). | moderate |
| **Which variant NOT to use** | **`edges` (pure progression) is dead.** AUC 0.42–0.69 on the owner's rounds, *worse* than difficulty in every cell, and worst coverage (needs ≥2 mapped steps). Combining it with states rescues it only back to par. | clear |
| **γ** | **Not γ = 1 for the length artefact.** γ=1 over-corrects (ρ = −0.23 to −0.47). γ = 0.5 is the length-neutral point for `actions`; the **state-based arms are length-neutral by construction** (ρ −0.04 … −0.16) and that is the owner's own hypothesis, confirmed. | clear, measured on both datasets |
| **RRB as a rating *update* on the corpus** | **No.** H4 NULL everywhere; the self-cancellation identity (§4) kills the self-anchored form outright. | clear |
| **The real bottleneck** | **Mapping coverage, still.** 50 % is double 28.75 % and it flipped the whole conclusion. The next 20 points are in the dwell/position vocabulary (`Montada`, `Meia Guarda`, `Quatro Apoios` — 157 entries) that the Lamas space has no state for. That is a *taxonomy* decision, not a statistics one, and it would move the result more than every cell of the 324-cell sweep combined. | — |

### Adjudicating the two external reports

| claim | source | this study |
|---|---|---|
| production mapper covers 28.7 % | report 2 | **confirmed, 28.75 %** |
| alias fix reaches 50.0 % | report 2 | **confirmed, 50.00 %** exactly |
| difficulty→Elo AUC on T2 = 0.878 | report 2 | **confirmed, 0.878** |
| ΔAUC RRB − difficulty on T2 = +0.052 [−0.022, +0.149], not significant | report 2 | **confirmed in kind**: +0.082 [−0.019, +0.197] for plain actions — interval straddles 0. (The *combined* arm does clear it.) |
| difficulty→Elo AUC on T1 = 0.583 | pilot 1 | **refuted.** Oriented correctly, difficulty is **0.750**. Pilot 1 scored a monotone-decreasing predictor with an AUC that assumes increasing. |
| ΔAUC RRB − difficulty = +0.139 [+0.053, +0.234] | report 2 | **direction confirmed, magnitude larger here**: +0.195 [+0.092, +0.306] |
| λ = 0.548/0.549 "keeps the K budget" | both | **refuted under the stated definition.** With `C = 1 − 2|P − 0.5|`, mean C = **0.899** on the owner's rounds, so budget-preserving λ = **1.112** (linear), 1.232 (quadratic), 1.055 (sqrt); on the corpus 1.042/1.084/1.021. λ ≈ 0.55 would need mean C ≈ 1.8, which is impossible for a quantity bounded by 1. **0.548 cuts the K budget roughly in half.** Whatever it preserves, it is not `mean(k_mult) = 1`. |
| ρ(competitiveness, intensity) | +0.22 (pilot 1) vs −0.01 (report 2) | **+0.18** with the canonical mapper. Closer to pilot 1; neither report's number is reproducible without its mapper. No verdict rides on it. |
| RRB does not predict whether a given terminal attempt lands (AUC 0.338) | report 2 | **not tested here** — a different target from T1/T2/T3. Reported as untested, not as agreed. |

## 3. Data, as measured

| | owner rounds (PRIVATE) | public corpus |
|---|---|---|
| unit | round | bout |
| n | 140 rounds / 39 sessions / 640 entries | 465 gated bouts (of 911 with a sequence) |
| **T1** labelled | **100** (`succeeded` 67 / `failed` 33) | **407** decided |
| **T2** labelled (finish-side from prefix) | **42** landed terminal submissions (51 attempts) | **149** SUBMISSION bouts with a mapped terminal |
| span | 2026-07-22 → 2026-08-25 | 2008 → 2026 |
| **coverage, canonical mapper** | **50.0 %** | 70.0 % |
| mapped actions per unit (mean / median) | 2.3 / 2 | 11.4 / 7 |
| codes now present | all 12 | all 12 |

## 4. Self-cancellation

Asserted as an identity, not measured as an effect: when the same round's `P` is both the score and
the source of the opponent offset, `s − E = 0` for every `P`. Measured maximum residual over
P ∈ {0.05 … 0.95}: **9.2e-09** — nonzero only because `glicko2.SCALE = 173.7178` is the published
rounding of 400/ln(10) = 173.717792761…

This is Chen, Sun, Seif El-Nasr & Nguyen's (2017) opponent-indifference result in closed form. It
is why the A1 self-anchored update is dead before it runs, and why the candidate arm A4 keeps the
`successful` flag as its score and uses RRB only for the opponent.

## 5. Tables

### 5a. Owner, T1 — round assessment (internal-consistency; canonical mapper)

n = 100 labelled rounds, base rate 0.67. Production reference = **oriented** `difficulty`
(AUC 0.750 [0.648, 0.844]); oriented `intensity` = 0.712. "BEATS" = paired ΔAUC interval > 0.

| arm | n | cov | AUC | 95 % CI | ρ(&#124;Z&#124;,len) | Δ vs production | |
|---|---|---|---|---|---|---|---|
| `actions_marginal_γ1` | 93 | 0.93 | **0.923** | [0.862, 0.970] | −0.12 | +0.195 [+0.092, +0.306] | **BEATS** |
| `actions_marginal_γ0` | 93 | 0.93 | 0.922 | [0.862, 0.970] | +0.58 | +0.194 [+0.102, +0.294] | **BEATS** |
| `states_occ_marginal` | 93 | 0.93 | 0.921 | [0.861, 0.969] | −0.16 | +0.193 [+0.090, +0.304] | **BEATS** |
| `actions_states_marginal_γ0` | 93 | 0.93 | 0.918 | [0.856, 0.966] | +0.49 | +0.191 [+0.099, +0.292] | **BEATS** |
| **`actions_drop_γ1`** (leakage control) | 89 | 0.89 | **0.915** | [0.852, 0.965] | −0.09 | +0.193 [+0.089, +0.306] | **BEATS** |
| `states_occ_drop` | 89 | 0.89 | 0.913 | [0.850, 0.964] | −0.12 | +0.191 [+0.087, +0.306] | **BEATS** |
| `actions_drop_γ0` | 89 | 0.89 | 0.906 | [0.841, 0.959] | +0.40 | +0.184 [+0.077, +0.296] | **BEATS** |
| `actions_states_drop_γ1` | 89 | 0.89 | 0.896 | [0.826, 0.953] | +0.07 | +0.175 [+0.075, +0.277] | **BEATS** |
| `states_drop` | 89 | 0.89 | 0.853 | [0.758, 0.935] | +0.36 | +0.131 [+0.033, +0.232] | **BEATS** |
| `states_marginal` | 93 | 0.93 | 0.773 | [0.641, 0.893] | +0.03 | +0.045 [−0.090, +0.173] | — |
| **`PROD_difficulty_oriented`** | 100 | 1.00 | 0.750 | [0.648, 0.844] | — | — | reference |
| `edges_states_drop_γ1` | 58 | 0.58 | 0.687 | [0.506, 0.850] | −0.14 | −0.029 [−0.215, +0.157] | — |
| `edges_states_marginal_γ1` | 75 | 0.75 | 0.628 | [0.443, 0.808] | −0.12 | −0.114 [−0.298, +0.055] | — |
| `edges_drop_γ1` | 58 | 0.58 | 0.515 | [0.317, 0.706] | −0.37 | −0.201 [−0.433, +0.015] | — |
| **`edges_marginal_γ1`** | 75 | 0.75 | **0.450** | [0.293, 0.618] | −0.48 | −0.292 [−0.486, −0.099] | **LOSES** |

### 5b. Owner, T2 — finish-side from the prefix (objective)

n = 42, base rate 0.833 (the owner lands most of the submissions in his own log). Production
reference = oriented `difficulty`, **0.878** [0.761, 0.967] — exactly report 2's number.

| arm | n | cov | AUC | 95 % CI | ρ | Δ vs production | |
|---|---|---|---|---|---|---|---|
| `edges_states_drop_γ1` | 27 | 0.64 | 1.000 | [1.000, 1.000] | −0.52 | +0.033 [+0.000, +0.120] | — (n = 27) |
| **`actions_states_marginal_γ1`** | 41 | 0.98 | **0.989** | [0.958, 1.000] | −0.03 | **+0.116 [+0.022, +0.235]** | **BEATS** |
| `states_marginal` | 41 | 0.98 | 0.987 | [0.950, 1.000] | +0.25 | +0.113 [+0.019, +0.232] | **BEATS** |
| `actions_states_drop_γ0` | 38 | 0.90 | 0.982 | [0.938, 1.000] | +0.30 | +0.099 [+0.017, +0.207] | **BEATS** |
| `states_drop` | 38 | 0.90 | 0.979 | [0.930, 1.000] | +0.27 | +0.096 [+0.014, +0.207] | **BEATS** |
| `actions_drop_γ1` | 38 | 0.90 | 0.969 | [0.906, 1.000] | −0.21 | +0.086 [+0.000, +0.200] | borderline |
| `actions_marginal_γ1` | 41 | 0.98 | 0.956 | [0.882, 1.000] | −0.27 | +0.082 [−0.019, +0.197] | — |
| `edges_drop_γ1` | 27 | 0.64 | 0.962 | [0.870, 1.000] | −0.42 | −0.005 [−0.083, +0.064] | — |
| **`PROD_difficulty_oriented`** | 42 | 1.00 | 0.878 | [0.761, 0.967] | — | — | reference |
| `edges_marginal_γ1` | 31 | 0.74 | 0.819 | [0.573, 0.988] | −0.57 | −0.088 [−0.362, +0.119] | — |

At n = 42 every interval is wide. The reading that survives that is directional and it agrees with
report 2: on the objective target the RRB family is **at least as good as** difficulty, and the
arms that add the state term are the ones that clear the interval.

### 5c. Owner, prequential — the actual production comparison

Glicko-2 state at round *t* has seen only rounds < *t*; the offset for round *t* comes from round
*t*'s own input. n = 100; 21 rounds produce no RRB score and fall back to offset 0.

| arm | log-loss ↓ | Brier ↓ | AUC ↑ |
|---|---|---|---|
| **`A4_rrb` (states)** | **0.6219** | 0.2146 | 0.806 |
| **`A4_rrb` (actions)** | **0.6249** | 0.2161 | **0.921** |
| `A4_rrb` (states_occ) | 0.6260 | 0.2166 | 0.919 |
| **`A0_difficulty` (production)** | 0.6265 | 0.2171 | 0.739 |
| `N1_marginal` (constant 0.67) | 0.6342 | 0.2211 | 0.500 |
| `A4_rrb` (edges_states) | 0.6560 | 0.2315 | 0.725 |
| `A0n_zero` (delete, substitute nothing) | 0.6931 | 0.2500 | 0.500 |

* **H2** (A4 actions vs A0): **−0.0015 [−0.0639, +0.0560]** → **NULL**. A tie.
* **H2n** (A4 vs A0n): **−0.0682 [−0.0817, −0.0546]** → **PASS**.
* **H2x** (A0 vs A0n): **−0.0667 [−0.1252, −0.0033]** → **PASS**.

The three best RRB granularities and production all sit inside 0.005 nats of each other, and all
four sit ~0.008 nats above the constant null. **The difficulty slider and the RRB derivation are
worth about the same small amount, and both are worth more than nothing.**

### 5d. Corpus, T1 — recorded winner

465 gated / 407 decided. Base rate (athlete_a wins) 0.600 — a storage convention, see §5f.

| arm | n | cov | AUC | 95 % CI | ρ |
|---|---|---|---|---|---|
| **`states_occ_marginal`** | 404 | 0.99 | **0.849** | [0.809, 0.887] | −0.44 |
| `actions_marginal_γ1` | 404 | 0.99 | 0.796 | [0.749, 0.839] | −0.23 |
| `actions_states_marginal_γ1` | 404 | 0.99 | 0.771 | [0.721, 0.816] | −0.30 |
| **`states_occ_drop`** (leakage control) | 377 | 0.93 | **0.749** | [0.699, 0.797] | −0.38 |
| `actions_states_drop_γ1` | 377 | 0.93 | 0.738 | [0.686, 0.786] | −0.36 |
| `actions_drop_γ1` | 377 | 0.93 | 0.727 | [0.674, 0.777] | −0.43 |
| `states_marginal` | 404 | 0.99 | 0.723 | [0.672, 0.770] | −0.21 |
| `edges_states_marginal_γ1` | 395 | 0.97 | 0.719 | [0.665, 0.767] | −0.36 |
| `edges_marginal_γ1` | 395 | 0.97 | 0.658 | [0.602, 0.710] | −0.47 |
| `N4_length` | 407 | 1.00 | 0.449 | [0.391, 0.507] | +1.00 |

**H3 PASS, H3b PASS** — every interval strictly above 0.5, including under the leakage control.
16× the pilot's n, and it holds.

### 5e. Corpus, T2 — finish-side from the prefix

n = 149, base 0.705. `actions_states_marginal_γ1` **0.880** [0.810, 0.940]; `states_marginal`
0.859 [0.785, 0.923]; `edges_states_marginal_γ0` 0.836 with **ρ = −0.08**, the most length-neutral
cell in the whole study. The corpus reproduces the owner's T2 ordering: the combined arm wins,
pure `edges` is the weakest.

### 5f. Corpus, T3 — forward prediction (the one that matters, and the one that fails)

Rolling origin, per fold, never pooled. All arms predict identically (`expected_score` on the two
real athlete states) and differ only in the update rule.

| fold | best arm | LL | production `A0g` LL | coin-flip LL | Δ best vs A0g |
|---|---|---|---|---|---|
| ≤2023 → 2024 (train 123 / test 109) | `A4_blend` 0.6766 | 0.6892 | 0.6931 | −0.0127 [−0.0391, +0.0116] |
| ≤2024 → 2025 (train 232 / test 98) | `A3_states` 0.6893 | 0.7170 | 0.6931 | −0.0277 [−0.0848, +0.0268] |
| ≤2025 → 2026 (train 330 / test 77) | `A4_blend` 0.6841 | 0.7035 | 0.6931 | −0.0194 [−0.0559, +0.0147] |

**H4 NULL** — not one interval, in any fold, for any of the eight arms, excludes 0.

**And the finding nobody asked for:** *no arm, including production, beats a constant 0.5.*
Coin-flip log-loss is 0.6931; `A0g_winner` scores 0.6892 / 0.7170 / 0.7035 and its AUC is 0.583 /
0.504 / 0.495 — chance in two folds of three. `N1_marginal` looks better still (0.6707 / 0.6931 /
0.5279) but is not a competitor: `athlete_a` wins **60.6 % / 50.0 % / 77.9 %** of those test years
because the winner is often entered first. Every rating arm is exchangeable under swapping a and b
(`E_a + E_b = 1`) and therefore *cannot* exploit that convention; N1 trivially does.

The cause is visible in the fold sizes — 77–109 test bouts, heavy athlete churn, every unseen
athlete entering at the 1500/350 seed and predicting ≈0.5 by construction. This is a statement
about **corpus size**, not about Glicko-2. It also means H4's NULL had little power to begin with,
and that **no** update rule can currently be validated on next-bout prediction in this repo.

### 5g. The length artefact, across arms (H5)

| family | best |ρ| | where |
|---|---|---|
| `actions`, γ=0 | 0.40 – 0.61 | worst — the raw sum, exactly Hvattum's (2019) exposure failure |
| `actions`, γ=1 | 0.09 – 0.43 | **over-corrected**, sign flipped negative |
| `actions`, γ=0.5 (corpus sweep) | **0.04** | the length-neutral point for the action family |
| `states` / `states_occ` | 0.03 – 0.16 (owner), 0.21 – 0.44 (corpus) | length-free by construction on the owner's data |
| `edges_states` | **0.04 – 0.16** | the most length-neutral family overall (corpus T2: −0.08) |

**H5 FAILS as pre-registered** (|ρ| ≥ 0.20 at γ=1 on the corpus) and fails in the *opposite*
direction from the one the pilot warned about: dividing by *n* over-penalises long sequences. The
owner's own hypothesis — "state-based arms should be less length-sensitive" — is **confirmed**.

### 5h. The adjustments sweep (A2), 324 cells

`out/rrb_study/sweep.csv`. AUC is invariant to **T** and to **(shape, λ)** as pre-registered, so
the grid collapses to 9 informative AUC rows (given in the first pass of §5 and unchanged —
they are corpus-side and the mapper defect was owner-side only). Three findings stand:

1. **γ = 0.5 is the length-neutral exponent for `actions`**, not γ=1 (ρ +0.24 → −0.04 → −0.33 on
   `landed`).
2. **T is a pure calibration knob** — zero effect on AUC, up to 0.09 on log-loss.
3. **λ = 0.548/0.549 does not preserve the K budget** — see §2's adjudication table. Use
   `budget_lambda()`, which computes it from the data.

## 6. Deviations from the pre-registration

1. **Bootstrap draws 4000, not 2000** — declared in prereg §6 before the run; 4000/seed 20260820 is
   this repo's pinned pair, so intervals are comparable to every other AUC in `docs/research/`.
2. **Stepper orientation decided after seeing AUC < 0.5.** Raw and oriented arms are both reported;
   H1/T2 use −difficulty. The justification is semantic and prior ("how hard was this round" ⇒
   harder means less likely to have succeeded), and the deviation **strengthens the baseline**,
   i.e. works against the conclusion the study reaches.
3. **The mapper defect (§1)** was found after the §1–§11 arms were scored. The addendum was written
   before the corrected arms were run; both readings are published.
4. **A3/A4-with-partner-prior not runnable on the owner's data** — the App carries no partner
   identity at all (prereg §2a, confirmed in `GrapplingArcApp/src/types/session.ts`).

## 7. What could not be run, and why

* **The `scientific-papers` MCP was not available** in this run's tool set. The external references
  were supplied with full citations by the orchestrator and used for design framing only; no number
  here comes from a paper.
* **"Does a given terminal attempt land?"** — report 2's third target (they measure RRB 0.338 vs
  difficulty 0.733). Not implemented; reported as untested rather than as agreed.
* **Per-node (`node_rating.py`) replay on the corpus** — out of scope (prereg §9). `ponytail:`
  ceiling; H4 says no global result justifies opening it.
* **A prospective forecast of the owner's rounds.** Every owner-side arm is ex-post: `difficulty`
  is typed with the round, RRB's actions are known only after it. Nothing supports a true pre-round
  forecast and none is claimed.
* **A per-edge value estimated from the corpus transition network.** The `edges` arm uses the
  telescoping difference of published state values instead (prereg §A3). `ponytail:` ceiling — a
  second artefact with its own gates is a separate PR, and `edges` lost badly enough that opening
  it is not indicated.
* **Any prod write.** No replay persisted, no migration, no artefact regenerated.

## 8. Literature

| Source | Where it bites |
|---|---|
| Kovalchik (2016), *Searching for the GOAT of tennis win prediction*, JQAS | §5f's protocol: rating systems compared by log-loss/Brier on *future* matches against a named reference. |
| Chen, Sun, Seif El-Nasr & Nguyen (2017), *Opponent Indifference in Rating Systems*, arXiv:1702.06253 | §4 — the self-cancellation identity is their opponent-indifference result; the formal reason A4 must keep an independent score. |
| Gorgi, Koopman & Lit (2019), JRSS-A | The A4 framing: keep the latent dynamic strength, feed within-bout statistics in as observations rather than replacing the state with the statistic. §5f is what happens when you try the latter. |
| Leitner, Zeileis & Hornik (2009) | Proper scoring rules first. §5c's AUC/log-loss split (A4 wins AUC by 0.18 and ties on log-loss) is why this matters here, not just in principle. |
| Hvattum (2019), plus-minus review | The exposure/length artefact class. §5g finding 1 is that artefact; finding 2 is it mis-corrected. |
| Lamas et al. (2024), *No-gi BJJ: a Markovian analysis* | The 12-state space and the reward-risk quantity the shares derive from (`analysis/lamas_chain.py`), including rule 2 — which is exactly why the remaining 50 % of the owner's entries do not map. |
| Glickman (1999/2012), Glicko-2 | Weighted observations as the continuous extension of repeat-count — already the basis of `ATTEMPT_WEIGHT` on both sides. |

## 9. Reproduce

```bash
set -a; source .env; set +a
uv run python -m scripts.research.rrb_round_rating --all
uv run pytest tests/test_rrb_round_rating.py -q
uv run ruff check scripts/research/rrb_round_rating.py tests/test_rrb_round_rating.py
```

Deterministic: sorted iteration, seed 20260820, matplotlib Agg.
`out/rrb_study/owner_rounds.json` is a **private fixture for one owner**, gitignored; never commit
it, never copy it into `data/`, never read it from another module.
