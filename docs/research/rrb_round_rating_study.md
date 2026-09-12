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
| **H1** ◦ | *(owner, T1 — **sanity only**, §D1)* RRB dominance separates `succeeded`/`failed` better than `difficulty` | **PASS** | ΔAUC **+0.195** [+0.092, +0.306]; 0.923 vs 0.750 |
| **H1b** ◦ | *(sanity only)* H1 survives the leakage control (`terminal=drop`) | **PASS** | ΔAUC **+0.193** [+0.089, +0.306]; arm still 0.915 |
| **T2** ★ | *(owner, objective — **decision target**)* finish-side from the prefix, vs `difficulty` | **PASS** | best `actions_states` **0.989**, Δ **+0.116** [+0.022, +0.235] |
| **H2** ★ | *(owner — **decision target**)* an RRB arm beats production V2 on prequential log-loss | **PASS — but only for `hier4`** | plain RRB: Δ −0.0015 [−0.0639, +0.0560] (**tie**). `hier4` (§C6): Δ **−0.1909 [−0.2781, −0.1061]**, log-loss **0.4356 vs 0.6265** |
| **H2n** | *(owner)* an RRB arm beats deleting the sliders and substituting nothing | **PASS** | plain −0.0682 [−0.0817, −0.0546]; `hier4` −0.2576 [−0.3267, −0.1776] |
| **H2x** | *(added)* production A0 beats A0n | **PASS** | Δ −0.0667 [−0.1252, −0.0033] |
| **H3** | *(corpus)* dominance identifies the recorded winner | **PASS** | `states_occ` **0.849** [0.809, 0.887] |
| **H3b** | H3 is not a submission tautology | **PASS** | `states_occ_drop` 0.749 [0.699, 0.797] |
| **H4** | *(corpus, T3)* an RRB-updated rating forecasts next year's winners better than production | **NULL** | no arm, no fold, no interval excluding 0 — and no arm beats a coin flip |
| **H5** | the length artefact is controlled at γ = 1 | **FAIL as specified** | γ=1 **over-corrects** (ρ −0.23 … −0.47). γ ≈ 0.5 is the neutral point; state arms are neutral by construction |
| **§B1** | the FULL catalogued-node chain lifts coverage over the Lamas 12 states | **PASS** | entry coverage **50.0 % → 81.8 %** (89.0 % with inference) |
| **§B2** | the node value `v` is informative (not ≈0.5 everywhere) | **FAIL — death rule 8 fires** | `sd(logit v)` = **0.012**; mount 0.5007, half guard 0.4994, back control 0.5039 |
| **§B3** | `fullchain_nodes` beats the frequency and actor-balance controls | **PASS** | vs freq **+0.174** [+0.081, +0.280]; vs signed-own-share **+0.211** [+0.116, +0.319] |
| **§B4** | the production chain-compiler **inference** helps | **FAIL — it costs signal** | T1 AUC 0.936 → 0.860; Δ vs production falls from +0.184 (clean) to +0.107 (straddles 0) |
| **§C1** | the notebook's edge residual improves **calibration** at gate 2–5, and gate 10 loses it | **PASS** | ΔBrier(gate 3) **−0.0118** [−0.0209, −0.0038]; gate 10 = 0.1164 ≈ labels-only 0.1177 |
| **§C1b** | …and gate = 1 gives a *smaller* gain | **FAIL** | gate 1 is our **best** cell (Brier 0.0919, AUC 0.984) |
| **§C2** | the calibration gain is knowledge, not capacity | **PASS** | vs shuffled-label null ΔBrier **−0.0596** [−0.0874, −0.0305] |
| **§C3** | the same hierarchy works on the public corpus (per-athlete) | **FAIL** | personalization **hurts**: AUC 0.772 (global prior) → **0.708** (personal). Only 51 of 189 units have any prior history |
| **§C6a** | `hier4` beats `actions_states` on **discrimination** | **NULL** | ΔAUC −0.019 [−0.097, +0.026] |
| **§C6b** | `hier4` beats `actions_states` on **calibration** | **PASS** | ΔBrier **−0.1104** [−0.1420, −0.0730]; ECE 0.145 vs 0.414 |
| **§C6c** | the four-layer model's **context** layer earns its place | **FAIL** | the best cell is `w_ctx = 0`; `no_context` is numerically identical (Δ = 0.0000 exactly) |
| **§C6d** | which layer carries the calibration gain | **the PERSONAL layer** | vs `no_personal` ΔBrier −0.1464 [−0.1832, −0.1069]; vs `no_residual` only −0.0135 [−0.0239, −0.0046] |

| **§D1** | the manual round outcome is a rating input | **binding owner decision, not a hypothesis** | T1 demoted to sanity (◦); the decision targets are T2 and the prequential (★) |
| **§D2a** | per-action success can be inferred from the sequence often enough to replace the flag | **FAIL — death rule 14 fires** | resolves **18.9 %** of entries (121/640). On the ACTION denominator it is **54.0 %** (121/224) — both reported, see §D |
| **§D2b** | where it resolves, the inference agrees with the manual flag | **PARTIAL** | concordance **70.2 %** (85/121); 36 disagreements, 26 of them "the sequence says it did not land" |
| **§D2c** | mode (c) `inferred_then_flag` can be adopted without regression | **PASS (by identity)** | Δ log-loss **0.0000** — and it is an *identity*, not a null: the forecast is provably invariant to the scores (§D) |
| **§D2d** | mode (b) `inferred_only` matches today's accuracy | **NULL**, at a large evidence cost | Δ +0.0021 [−0.0024, +0.0062]; observations **431 → 89 (−79 %)**, RD 78.8 → 127.6 |

★ = decision target (§D1)  ·  ◦ = plausibility/sanity only, carries no product decision.

Death rules that fired: **rule 8 (flat value)** on §B's `v`; **rule 14 (resolution floor)** on §D's
inference under the entry denominator; **rule 3 (leakage)** on the
*broken-mapper* first pass only (§1). Rules 9 (frequency), 11 (capacity), 10 (α disagreement) did
**not** fire. Rule 6 (the lazy death) did **not** fire: substituting nothing is worse than
everything.

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

**Yes.** With three qualifications, each of which is a number.

1. **ΔAUC +0.195 [+0.092, +0.306].** RRB dominance separates the owner's `succeeded` from `failed`
   rounds at 0.923 against the difficulty slider's 0.750, and it **survives** the leakage control
   at 0.915 — the signal is not "I got the submission".
2. **Δ log-loss −0.1909 [−0.2781, −0.1061].** The four-layer `hier4` arm (§C6), dropped into
   production's actual Glicko-2 slot as the virtual-opponent offset, scores **0.4356** against
   production's **0.6265**. This is the only arm in the study that beats production on the
   prequential protocol with a clean interval — plain RRB merely ties (−0.0015).
3. **H4 NULL in all three folds, and no arm beats a coin flip.** On the public corpus *no* update
   rule — RRB's, `hier4`'s, or production's — predicts next year's winners better than 0.5. The
   whole family describes a round well and forecasts a rating not at all.

And one result that says *why* it works, which is not what any of the three external reports
claimed: **the node value is flat.** `sd(logit v)` over the 243-node vocabulary is **0.012** — mount
0.5007, half guard 0.4994, closed guard 0.4989, back control 0.5039. The chain mixes far faster
than it absorbs, exactly as `markov_action_weights.json`'s own caveat says
(*"a cadeia mistura mais rápido do que absorve"*). The arms still win, and win against both the
frequency control (+0.174) and the actor-balance control (+0.211), so the tiny differences are
correctly **ordered** — but nobody should describe this as "positions carry value". They carry a
consistent sign at a hair's amplitude.

### THE RECOMMENDATION — one representation for the Glicko-2 virtual-opponent slot

**Implement `hier4` without its context layer**, i.e.
**global Lamas-attempt prior → personal canonical-label Beta-Binomial posterior (walk-forward,
α = 0.5) → gated edge residual (gate = 2, λ = 1.5)**, mapped through `technique_match.clean_label`,
scored with `last_flag_only`, and fed into production's existing slot as
`opponentRating = currentGlobal + 400·log10(P/(1−P))` — replacing
`ELO_PER_DIFFICULTY_POINT × (difficulty − 5)`, never as the observation score.

It is the only arm in this study that beats production on the decision target with a clean interval
(**prequential log-loss 0.4356 vs 0.6265, Δ −0.1909 [−0.2781, −0.1061]**), and it is the best
calibrated (**Brier 0.088, ECE 0.145** against `actions_states`' 0.196 / 0.414) — which is the axis
that matters, because this slot converts `P` into Elo points directly and a Glicko-2 engine consumes
a probability, not a ranking. Its "personal fit" is a running Beta-Binomial count per `node_key`:
on-device, no training pipeline, no artefact, one number per technique.

**On the tie-break rule.** `hier4` and `actions_states` genuinely **tie on ordering**
(ΔAUC −0.019 [−0.097, +0.026], NULL on T2) — so if the slot needed a *ranking*, the owner's rule
("prefer the simplest with best coverage") would select `actions_states`: no fitting, no personal
state, published constants only, 98 % coverage. It does not tie on **calibration**
(ΔBrier −0.1104 [−0.1420, −0.0730]), and calibration is what this slot consumes, so the tie-break
does not fire. **If the owner wants zero on-device fitting anyway, `actions_states` is the fallback
— but it must be recalibrated (Platt/isotonic on the owner's own rounds) before its `P` is allowed
near `400·log10`, because ECE 0.414 fed straight into an Elo offset is a systematic error, not
noise.**

**Three conditions attached to shipping it, each from a measured number:**

1. **Clamp the offset.** `hier4`'s leave-one-entry-out sensitivity is 52 Elo mean / **758 Elo max**
   (§C3) — one logged action can move the inferred partner by three quarters of a rating point of
   standard deviation. Clamp to roughly ±400 (the `difficulty` slider's own ±280 is the precedent).
2. **Drop the context layer and the chain-compiler inference.** The best grid cell is `w_ctx = 0`
   and `no_context` is numerically identical (Δ = 0.0000); the inference layer *costs* 0.076 AUC
   (§B4). Both are complexity with a measured price and no measured buyer.
3. **Do not generalise it past this user.** The same construction **failed** on the public corpus
   (AUC 0.772 → 0.708 with per-athlete layers, §C2). It works in the regime it was built for — one
   person with a long, growing log — and there is no evidence it works anywhere else. A second
   user's first twenty rounds will score on the prior alone, which is `fullchain_nodes`, which is
   fine.

### What to do

| | recommendation | strength |
|---|---|---|
| **`intensity` stepper** | **Remove it.** No rating path reads it. | certain (code, not statistics) |
| **`difficulty` stepper** | **Replace it** with an RRB-derived virtual opponent inside Glicko-2. `hier4` beats it on log-loss by 0.19 nats; plain RRB ties it. Do **not** remove it with nothing in its place — that is worse than both, decisively. | good on the owner's 140 rounds; untested on any other user |
| **Never as both `s` and `E`** | The same round's dominance may set the virtual **opponent** or supply the **score**, never both: `s − E = 0` identically (max residual 9.2e-09). RRB is the opponent; the `successful` flag stays the score. | proved, not measured |
| **Do NOT add a custom competitiveness K yet** | There is no forecasting evidence for it. H4 is NULL everywhere and the corpus cannot currently validate any update rule. | clear |
| **Canonicalisation is part of model validity** | Not a preprocessing detail. 28.75 % → 50 % coverage **flipped four verdicts** (§1). Any arm shipped without `clean_label` is a different model. | measured |
| **γ ≈ 0.5 for repeated-action arms** | γ=1 over-corrects the length artefact (ρ −0.23 … −0.47); γ=0.5 lands at ρ −0.04. State arms are length-neutral by construction. | measured on both datasets |
| **λ from the training data (~1.1), never 0.548** | Budget-preserving λ measured at 1.007–1.471 depending on the arm; 0.548 would need mean C ≈ 1.8, impossible for a quantity bounded by 1. It halves the K budget. | arithmetic |
| **Which arm** | For **ranking**: `actions_states` (AUC 0.989 on T2) — no fitting, no parameters, published constants only. For **a rating input**: `hier4` — same ordering (ΔAUC −0.019, NULL) but far better calibrated (Brier 0.088 vs 0.196, ECE 0.145 vs 0.414), and calibration is what Glicko-2 consumes. | see §C6 |
| **Drop from `hier4`** | The **context layer** (best cell `w_ctx = 0`, Δ exactly 0.0000) and the **chain-compiler inference** (costs 0.076 AUC). Keep the personal label layer (−0.146 Brier) and the gated edge residual (−0.014). | measured |
| **Do NOT personalize on the corpus** | Per-athlete layers *hurt* there: AUC 0.772 → 0.708. Only 51 of 189 units have any prior history. The method needs a long personal log, which is exactly what a corpus athlete does not have. | measured |

### Not yet supported by anything in this study

* **Forward prediction of any kind.** H4 is NULL in every fold for every arm; production's own
  global track scores 0.6892 / 0.7170 / 0.7035 against a coin flip's 0.6931 and is at chance
  (AUC 0.504 / 0.495) in two folds of three. **No update rule can currently be validated on
  next-bout prediction in this repo** — not RRB's, not `hier4`'s, not the one that ships.
* **Generalisation beyond this owner.** Every owner-side number is n = 100 (T1) or n = 42 (T2)
  rounds from one person over five weeks, with the personal layers fitted on that same person.
  The corpus test of the same construction **failed**.
* **Anything competitive.** None of this may touch a centroid, a ranking, the corpus or the site.

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

## G. Glossary — six things called "edges" or "states", and why none of them is another

Three external artefacts and this study all use the words *action*, *state* and *edge*. They do
**not** mean the same objects, and two of the "edge" results that look contradictory are measuring
different things. Both stand.

| term | **in this study** | **in the external notebook** |
|---|---|---|
| `actions` | the **repeated Lamas codes** of a round, actor-signed mean log-odds of the published `sub_share`. Repetition counts. | — |
| `states` | the **last mapped Lamas code**. One value per round. | — |
| `states_occ` | the **distinct `(Lamas code, actor)` occupancy** of a round — each pair counted once. | — |
| `edges` (§A) | **ΔV of the published Lamas values** — pure progression, `z_{i+1} − z_i`. **Nothing is re-estimated**; it is a telescoping difference of constants. | — |
| `fullchain_nodes` (§B) | actor-signed log-odds of the **corpus absorption value** over the whole 243-key catalogued vocabulary. | — |
| **action labels** | — | **canonical technique labels** (`Mata-Leão`, `Smash da Meia Guarda`, …) with **learned personal posteriors**, empirical-Bayes shrunk toward a global prior, walk-forward. |
| **edge residual** | — | **observed transition value − label-predicted value**, EB-shrunk, with a support gate. A *residual on top of a fitted label layer*, not a difference of constants. |

**The two "edge" results test different objects and both stand.** §A/§B's `edges` is a pure
progression of published numbers and is the **weakest** owner-side arm on T1 (0.837–0.856 against
`fullchain_nodes`' 0.936) — a difference of near-constant constants carries little. The notebook's
`edge residual` is a *learned correction* on a *fitted* backbone and does exactly what a residual
should: it moves calibration (ΔBrier −0.0118 [−0.0209, −0.0038]) and not ordering (ΔAUC 0.0000).
Reading either as evidence about the other is a category error.

One more distinction that mattered: `lamas_state` reads a **localized** label and returns `None`
(§1), while `technique_match.clean_label` resolves pt-BR → canonical English. Everything in §A
before the addendum used the first; everything after uses the second.

---

## B. The FULL catalogued-node Markov chain (prereg §B)

Owner decision: *"vamos tentar usando o markov de todas as ações catalogadas, não das ações
Lamas"*, plus *"usando também a inferência de alta confiança já estabelecida"*.

### B1. Coverage — the point of the exercise, and it worked

| mapper / space | owner entries mapped | corpus |
|---|---|---|
| Lamas 12 states, raw labels (§A first pass) | 28.75 % | 70.0 % |
| Lamas 12 states, `clean_label` (§A corrected) | 50.00 % | 70.0 % |
| **full catalogued vocabulary (243 node keys)** | **81.82 %** | — |
| full vocabulary **+ chain-compiler inference** | **89.02 %** | — |

Of the 320 entries the Lamas space could not map, the ones that now carry a value include `mount`
(67), `half guard` (53), `closed guard` (23), `side control` (14). Still unmapped: `quatro apoios`
(37) — the technique library has no `Quatro Apoios → Turtle` entry, a concrete one-line gap.

Corpus chain: 465 gated bouts, 243 node keys (120 with ≥5 occurrences), mirror identity
`v(node, own) = 1 − v(node, opp)` exact to **0.0**, α-sensitivity max |v(0.5) − v(1.0)| = **0.030**
(verdicts identical at both α, so death rule 10 does not fire).

### B2. The value is flat — death rule 8 fires

`sd(logit v)` = **0.0120** (observed) / 0.0244 (with inference), against the pre-registered floor of
0.1. Every value sits inside [0.489, 0.535]:

| node | n | v | | node | n | v |
|---|---|---|---|---|---|---|
| back control | 1508 | 0.5039 | | mount | 333 | 0.5007 |
| half guard | 396 | 0.4994 | | closed guard | 107 | 0.4989 |
| triangle choke | 364 | 0.5061 | | guard pass | 131 | 0.4988 |
| heel hook | 122 | 0.5147 | | armbar | 162 | 0.5072 |

**Positions are not distinguishable.** This is the Lamas amplitude caveat confirmed on a state
space 20× larger, and it is the honest answer to the owner's question "is `v` informative or ≈0.5
everywhere": **≈0.5 everywhere, with a consistent sign**.

### B3. …and yet the arm wins, against both controls

Owner T1, n = 100, production reference = oriented `difficulty` (0.750):

| arm | n | AUC | vs production | vs actor-balance | vs frequency |
|---|---|---|---|---|---|
| **`fullchain_nodes` γ=0.5 α=0.5** | 97 | **0.936** [0.880, 0.978] | **+0.184** [+0.080, +0.296] | **+0.211** [+0.116, +0.319] | **+0.174** [+0.081, +0.280] |
| `fullchain_nodes` γ=1 | 97 | 0.923 | +0.171 [+0.060, +0.289] | +0.198 [+0.108, +0.301] | +0.161 [+0.073, +0.258] |
| `actions_fullchain` (combo) | 92 | 0.929 | +0.204 [+0.105, +0.316] | +0.182 [+0.081, +0.297] | +0.154 [+0.049, +0.273] |
| `fullchain_last` | 97 | 0.882 | +0.131 [+0.023, +0.240] | +0.158 [+0.047, +0.280] | +0.120 [+0.013, +0.235] |
| `fullchain_edges` γ=0.5 | 89 | 0.856 | +0.127 [−0.009, +0.268] | +0.068 [−0.049, +0.199] | +0.041 [−0.070, +0.156] |
| `CONTROL_fullchain_freq` | 97 | 0.762 | +0.010 [−0.150, +0.162] | — | — |
| `CONTROL_signed_own_share` | 97 | 0.724 | −0.027 [−0.185, +0.127] | — | — |

Both controls sit at production's level and neither beats it; `fullchain_nodes` beats **both** with
clean intervals. So the flat-but-ordered value is doing real work — it is not the vocabulary
(frequency control) and it is not "what share of the round did I log as mine" (actor-balance
control). Death rule 9 does not fire.

On **T2** the ordering flips: `fullchain_edges` is the best full-chain arm (0.978 [0.931, 1.000],
Δ vs production +0.108 [+0.017, +0.223]) while being the weakest on T1 — the "good residual, bad
backbone" signature the notebook independently reported.

### B4. Inference adds coverage and subtracts signal

1 986 inferred actions on the corpus, 142 on the owner's rounds (strict tier: `actor_readable =
bout_flags(...)['perspective_reliable']`, true on all 465 gated bouts and honest on the owner's
two-corner log).

| | coverage | best T1 AUC | Δ vs production |
|---|---|---|---|
| observed only | 81.8 % | **0.936** | **+0.184** [+0.080, +0.296] |
| + inference | 89.0 % | 0.860 | +0.107 [−0.006, +0.217] — **straddles 0** |

On T2 the damage is larger: inferred `fullchain_edges` collapses to 0.427–0.448 (it *loses* to
production with a clean interval) where observed scores 0.978. The generic states the compiler
inserts (`control transition`, `start neutral`, `start top`, `finish`, `reversal`) dilute the
sequence. **Verdict: do not use the inference layer for scoring.**

> **A production defect found on the way, and not fixed here.** `chain_compiler.compile_chain` keys
> its states from `_normalize_name(label)` with **no `clean_label`**, so run on a pt-BR user log it
> emits `costas` / `montada` / `meia guarda` — a node key space **disjoint** from the English one
> the corpus produces (measured: 108 `costas` for the same entries the direct mapper keys as
> `back control`). This study works around it by canonicalising labels before compiling. The defect
> is in `analysis/chain_compiler.py`, it affects the edge-as-path / map layer and not just this
> study, and it is handed back rather than patched by a research runner.

### B5. T3 — forward prediction, unchanged

| fold | `A0g` (production) | `B_fullchain` | coin flip | Δ |
|---|---|---|---|---|
| ≤2023 → 2024 | 0.6892 | 0.6930 | 0.6931 | +0.0037 [−0.0502, +0.0563] |
| ≤2024 → 2025 | 0.7170 | 0.6930 | 0.6931 | −0.0240 [−0.0821, +0.0319] |
| ≤2025 → 2026 | 0.7035 | 0.6932 | 0.6931 | −0.0103 [−0.0831, +0.0614] |

`B_fullchain` lands on the coin flip to four decimals, which is what a flat `v` predicts: score
≈ 0.5 ⇒ the rating barely moves. **H4 NULL.**

---

## C. Personalized hierarchical layers (prereg §C)

> **The external notebook's artefacts were not available to us** — only its reported numbers, and
> the owner notes it ran on more limited data. Everything in §C is a **re-implementation from a
> described model**, and every claim is treated as a hypothesis to validate, not a result to
> reproduce.

### C1. Reconciliation — their number | ours (same construction) | ours on the corpus

Target T2, walk-forward, per-session bootstrap, n = 42 rounds / 39 sessions.

| claim | theirs | **ours, same construction** | **ours, public corpus** |
|---|---|---|---|
| action-label backbone, AUC | 0.963 | **0.963** (after the 10-round warm-up) | 0.708 |
| + edge residual, AUC | 0.963 | **0.947** all / **0.963** after warm-up | 0.708 |
| **ΔAUC from the residual** | ≈ 0 | **+0.0000 [+0.0000, +0.0000]** — exact | +0.0000 |
| + edge residual, Brier | 0.095 | **0.1059** all / **0.0761** after warm-up | 0.2426 |
| walk-forward gate 3, Brier / log-loss | 0.111 / 0.346 | **0.1059 / 0.3413** | 0.2426 / 0.6777 |
| ΔBrier (gate 3) | −0.0318 [−0.0450, −0.0195] | **−0.0118 [−0.0209, −0.0038]** — same sign, CI excludes 0, **≈3× smaller** | −0.0000 [+0.0000, +0.0000] |
| gate 5 / gate 10, Brier | 0.123 / 0.138 | **0.1075 / 0.1164** (labels-only = 0.1177) | — |
| "gate 10 loses it" | claimed | **CONFIRMED** — gate 10 is within 0.001 of labels-only | — |
| "gate 1 gives a smaller gain" | claimed | **REFUTED** — gate 1 is our **best** cell (Brier 0.0919, AUC 0.984) | — |
| capacity null | not reported | shuffled labels: Brier 0.1655, **ΔBrier(real − shuffled) = −0.0596 [−0.0874, −0.0305]** | — |

**Verdict on their headline claim** — *"action-label = dominance backbone, edge residual =
calibration layer, do not use edge-RRB as the main score"*: **PASS on the owner's rounds**, with
gate ∈ {2, 3, 5} and gate 10 losing it exactly as stated; **FAIL on the sub-claim about gate 1**;
**FAIL on the public corpus**.

### C2. Where their numbers do NOT hold, plainly

**On the public corpus, per-athlete personalization makes things worse.** 189 submission bouts,
149 athletes, and only **51 of 189** units have any prior history at all:

| arm | n | AUC | Brier | ECE |
|---|---|---|---|---|
| **`global_prior_only`** (no personal layer) | 189 | **0.772** [0.697, 0.840] | 0.2478 | 0.264 |
| `personal_labels_only` | 189 | 0.708 [0.625, 0.790] | 0.2426 | 0.223 |
| `hier_gate2..10` | 189 | 0.707–0.709 | 0.2415–0.2436 | 0.214–0.226 |

Personalization costs **0.064 AUC** and the edge residual moves Brier by at most 0.0011
(interval [−0.0028, +0.0000]). The method needs a **long personal history**; the owner has 140
rounds in five weeks, a corpus athlete has roughly one prior submission bout. *That is a property
of the method which 140 rounds of one person cannot reveal, and it is the main thing the larger
data adds.*

### C6. The agreed four-layer arm — does the complexity buy anything?

`hier4` = global Lamas prior → personal canonical-label value (EB, walk-forward) → state/occupancy
context → gated EB transition residual. Grid gate ∈ {2,3,5} × α ∈ {0.5,1,2} × λ ∈ {1,1.5} × w_ctx ∈
{0,0.5,1}. Best cell by Brier: **gate 2, α 0.5, λ 1.5, w_ctx 0**.

| arm (owner T2, n = 42) | AUC | Brier | log-loss | ECE |
|---|---|---|---|---|
| **`hier4`** | 0.971 [0.914, 1.000] | **0.0883** | **0.2859** | **0.1446** |
| `actions_states` (§A, no fitting at all) | **0.989** [0.958, 1.000] | 0.1955 | 0.5833 | 0.4138 |
| ablation `no_personal` | **0.996** | 0.2347 | 0.6626 | 0.4603 |
| ablation `no_context` | 0.971 | 0.0883 | 0.2859 | 0.1446 |
| ablation `no_residual` | 0.971 | 0.1018 | 0.3536 | 0.2039 |
| `shuffled_null` | 0.890 | 0.1474 | 0.4762 | 0.2409 |

Paired, per-session bootstrap, against `actions_states`:
**ΔAUC −0.019 [−0.097, +0.026] (NULL)**, **ΔBrier −0.1104 [−0.1420, −0.0730] (PASS)**.

Read under the pre-registered rule: **this is a calibration result, not a discrimination one.**
Four things follow, and they are the answer to "does the extra complexity buy anything":

1. **For ranking, it buys nothing.** `actions_states` — an average of two published constants, no
   fitting — is *numerically better* (0.989 vs 0.971) and the difference is NULL. So is
   `no_personal`, which is `hier4` with the entire fitted layer removed, at 0.996.
2. **For a probability, it buys a great deal.** Brier 0.088 vs 0.196, ECE 0.145 vs 0.414. A
   Glicko-2 engine consumes a probability, not a ranking, so this is the axis that matters for the
   product.
3. **The gain is the PERSONAL layer, not the edge residual.** ΔBrier vs `no_personal`
   **−0.1464** [−0.1832, −0.1069]; vs `no_residual` only **−0.0135** [−0.0239, −0.0046]. The
   residual is real and small; the personal posterior is the engine.
4. **The context layer is dead weight.** The best cell is `w_ctx = 0` and `no_context` is
   *numerically identical* (Δ = 0.0000 exactly). Drop layer 3.

And it survives the capacity null: ΔBrier vs `shuffled_null` −0.0591 [−0.0881, −0.0301].

### C6b. The head-to-head that decides the product question

`hier4`'s `P` as the virtual-opponent offset inside production's own prequential Glicko-2 protocol
(§5c), everything else identical, n = 100:

| arm | log-loss ↓ | Brier ↓ | AUC ↑ |
|---|---|---|---|
| **`A4_rrb` = `hier4` offset** | **0.4356** | **0.1406** | **0.913** |
| `A0_difficulty` (production) | 0.6265 | 0.2171 | 0.739 |
| `N1_marginal` | 0.6342 | 0.2211 | 0.500 |
| `A0n_zero` | 0.6931 | 0.2500 | 0.500 |

**Δ vs production −0.1909 [−0.2781, −0.1061]** — the first and only clean win over production in
this study.

> **Two cautions that travel with that number.** (i) It is **ex-post conditional**: the offset for
> round *t* is read from round *t*'s own actions, as the pilot's own framing requires. (ii) The
> personal layers are fitted (walk-forward, never on the round being scored) on **finish-side**
> outcomes while the prequential is scored on the **`succeeded`/`failed`** label, and those two are
> strongly correlated. There is no self-leakage, but the two targets are not independent, and the
> honest reading is "a model of my own rounds predicts my own round labels well", not "RRB
> forecasts".
>
> **And it was very nearly wrong.** The first run of this table reported `hier4` as *significantly
> worse* than production (Δ +0.0667) because a local per-arm dict shadowed the `offsets` parameter,
> so every round after the first silently read 0.0 and the arm collapsed onto `A0n_zero`. Caught by
> checking the arm's numbers against the offsets actually passed in; pinned by
> `test_prequential_offsets_reach_every_round_not_just_the_first`.

### C3. Coherence of the inferred partner Elo (descriptive — no verdict)

| arm | n | within-session sd (Elo) | lag-1 autocorr | LOO mean / max (Elo) | Kendall τ vs outcome | budget λ | ECE vs finish-side |
|---|---|---|---|---|---|---|---|
| `difficulty` (production) | 140 | 71.7 | −0.280 | — | −0.450 | 1.407 | 0.267 |
| `intensity` | 140 | 73.4 | −0.163 | — | −0.324 | 1.301 | 0.373 |
| `states_only` | 119 | 25.0 | −0.279 | 4.7 / 43.5 | −0.536 | 1.109 | 0.379 |
| `labels_only` | 131 | **2.3** | −0.251 | **0.7 / 10.6** | −0.622 | 1.007 | **0.423** |
| `hier_residual` | 132 | 113.8 | −0.292 | 52.2 / 757.8 | **−0.681** | 1.471 | **0.184** |

(τ is negative for every arm because the Elo *offset* falls as dominance rises; |τ| is the
agreement.) Read as §C3 pre-registered:

* **Smoothness and usefulness point in opposite directions here.** `labels_only` is twenty times
  smoother than production (sd 2.3 vs 71.7 Elo) and one logged entry moves its inferred partner Elo
  by 0.7 points on average — and it has the **worst** calibration of all five (ECE 0.423), because
  its flat `v` keeps `P` pinned near 0.5. Smoothness alone is not a recommendation, which is exactly
  why §C3 carries no verdict (death rule 13).
* **`hier_residual` is the best-ordered (|τ| 0.681) and the best-calibrated (ECE 0.184)** and the
  least stable (sd 113.8, one entry worth up to 758 Elo). If it ships, the offset should be clamped.
* **Lag-1 autocorrelation is negative for every arm** (−0.16 to −0.29). Consecutive rounds in one
  session *alternate* rather than persist. Under a "same partner, same day" model it should be
  positive; it is not, for any arm including production's `difficulty`. Either rounds within a
  session are not against one partner, or the athlete alternates hard and easy rounds. **The App
  cannot tell us which, because it records no partner identity** (§2a) — and that is a cheap
  product change with a measurable research payoff.
* **Budget λ is 1.007–1.471 across the five arms**, never near the externally quoted 0.548.

---

## D. Inferred per-action success (prereg §D)

Owner rules: the manual round outcome is **not** a rating input (§D1 — reflected in the verdict
table's ★/◦ marks), and per-action success is **read off the sequence** where the transition makes
it observable, **UNKNOWN otherwise**, never auto-success or auto-failure (§D2).

The rule is implemented against production's own tables —
`data/taxonomy/inference_table.json`'s `action_exit_orientation` and `attribution.classify`'s
curated role — and both of the owner's worked examples are pinned as tests:
`Half Guard → Sweep → Mount ⇒ 1.0` and `Back Control → RNC → Back Control ⇒ 0.0`, the second in
pt-BR as well.

### D1. How often the sequence actually resolves it

| | owner | corpus |
|---|---|---|
| entries | 640 | 9 817 |
| **resolved by inference** | **121 (18.9 %)** | **2 044 (20.8 %)** |
| unresolved | 519 | 7 773 |
| entries that carry a manual flag | — | 3 049 (31.1 %) |

By the compiler's own classification the owner's 640 entries are **412 states, 224 actions, 4
transparent** — and a state is never scored by anything in this study. On the **action**
denominator the rule resolves **121 / 224 = 54.0 %**.

Per type, it resolves where the taxonomy gives it a direction and nowhere else:

| type | inferred | unresolved | |
|---|---|---|---|
| `sweep` | 17 | 7 | 71 % — exit orientation `top` |
| `pass` | 29 | 13 | 69 % |
| `takedown` | 17 | 8 | 68 % |
| `submission` | 56 | 27 | 67 % |
| `escape` | 2 | 37 | 5 % — exit orientation `neutral` |
| `transition` | 0 | 22 | `neutral` |
| `control` / `guard` / `technique` / `concept` | 0 | 425 | states, never scored |

> **The pre-registration's 25 % floor is ambiguous and both readings are given.** It says
> "< 25 % of entries". On the entry denominator (18.9 %) **death rule 14 fires** — the rule is
> insufficient to replace the flag. On the action denominator (54.0 %) it clears comfortably. The
> ambiguity was not resolved before scoring, so the stricter reading is the headline and the looser
> one is stated beside it rather than substituted for it.

### D2. Where it resolves, it agrees with the athlete 70 % of the time

Concordance between the inferred score and the manual flag, on the 121 resolved entries:
**85 agree (70.2 %), 36 disagree** — and the disagreements are lopsided: **26** are "the sequence
says it did not land, the flag says it did" against 10 the other way. That asymmetry is what you
would expect from a default-to-landed toggle (`successful` undefined = landed, on both sides of the
contract), and it is the strongest argument in this study for the owner's rule: the sequence is
catching attempts the form recorded as successes.

### D3. The three modes in the production V2 slot — and why this protocol cannot decide between them

| mode | observations scored | final rating | final RD | A4 log-loss | Δ vs `flag` |
|---|---|---|---|---|---|
| **(a) `flag`** (today) | 431 | 2511.6 | 78.8 | 0.6249 | — |
| **(b) `inferred_only`** | **89** (−79 %) | 1803.4 | **127.6** | 0.6296 | +0.0021 [−0.0024, +0.0062] |
| **(c) `inferred_then_flag`** | 431 | 2466.2 | 78.8 | 0.6249 | **0.0000 [0.0000, 0.0000]** |
| **(d) `last_flag_only`** — *the owner's product mode* | **161** | 1887.6 | 104.4 | 0.6272 | **+0.0009 [−0.0014, +0.0030]** |

**Mode (c)'s exact zero is an identity, not a null result, and this must not be misread.** In the
virtual-opponent design the athlete's own rating **cancels out of the forecast**: `expected_score`
returns 0.340565187099 for a rating of 1200, 1500 or 2400 against the same +140 offset, because
only the *difference* enters. So the round forecast depends on the **offset** and the **RD**, and
not at all on the rating level. Mode (c) scores exactly the same 431 observations as (a) — so the
RD is identical (78.79 both) and the forecasts are identical by construction, even though **24 of
the 431 own-entry scores differ** and the final rating moves by 45 points (2511.6 → 2466.2).

The honest consequence: **§D cannot be adjudicated by the prequential protocol.** The only channel
by which a scoring mode reaches that forecast is the observation *count*, through RD — which is
exactly why mode (b), at 89 observations and RD 127.6, is the only one that moves at all, and moves
by +0.0021 (NULL). Deciding between the modes needs a metric that reads the rating **level** — and
this study has none, because the owner's own rating has no external criterion to be checked against.

### D3b. The owner's product mode: `internal = inferred|NULL, last = manual flag`

The refinement (2026-09-12): the manual flag survives **only on the last node of a sequence**,
because the final action has no following transition and the sequence therefore cannot resolve it
(the D7 anchor rule). Every internal action is inferred or NULL.

Measured on the owner's fixture: **128 last-own-nodes**, of which the sequence could have inferred
only **45** — so the flag on the final node contributes 83 observations nothing else can supply,
which is precisely the gap the rule exists to cover.

| | observations | RD | Δ log-loss vs today |
|---|---|---|---|
| today (`flag` everywhere) | 431 | 78.8 | — |
| **`last_flag_only`** | **161 (37 %)** | 104.4 | **+0.0009 [−0.0014, +0.0030] — NULL** |
| `inferred_only` (no flag at all) | 89 (21 %) | 127.6 | +0.0021 [−0.0024, +0.0062] — NULL |

**The owner's mode is statistically indistinguishable from today's behaviour** while removing the
toggle from every internal step, and it recovers 72 more observations than dropping the flag
entirely. Given §D3's identity — the forecast only ever sees the observation *count*, through RD —
the honest statement is: *this metric cannot separate them, and on the one channel it can see
(evidence volume) `last_flag_only` is the best of the flag-free options by a wide margin.*

`ponytail:` "last node of a sequence" is implemented here as the last **own-actor entry of the
round**; the owner fixture does not carry `sequenceId`. Ceiling — a round with two chains has two
last nodes and this sees one. Upgrade path: keep `sequenceId` in the fixture and partition by it
(`services/sequencePartition` already does this on the App side).

### D4. What this supports

* **Adopt mode (d), `last_flag_only`** — the owner's own rule. NULL against today on the only
  metric available, and the best evidence volume of any flag-free design (161 vs 89).
* **Mode (c) `inferred_then_flag` is the zero-risk intermediate** if the toggle is kept: it
  regresses nothing (identically, provably) and corrects 24 of 431 observation scores toward what
  the sequence shows.
* **Do not adopt mode (b), `inferred_only`.** At 18.9 % entry resolution it discards 79 % of the
  evidence, and the RD cost (78.8 → 127.6) is a real loss of confidence that this study's metrics
  happen not to penalise.
* **The cheapest way to unlock mode (b)** is not a better inference rule: it is exit orientations
  for `escape` and `transition` (61 owner entries, currently `neutral` and therefore unresolvable
  by construction) and a `Quatro Apoios → Turtle` library row. That is table work, not modelling.

---

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
