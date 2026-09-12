# RRB-derived partner strength as a replacement for the difficulty/intensity sliders — pre-registration

Written 2026-09-12, **before any arm was scored**. Everything below was fixed against read-only
MARGINALS: how many rounds the owner has logged, how many carry a binary assessment, what share
of entries map to a Lamas action, how many gated bouts the public corpus holds, how the corpus
clusters by year. None of those say which arm wins. Results are appended by the runner to
`docs/research/rrb_round_rating_study.md`, never to this file.

Runner: `scripts/research/rrb_round_rating.py`. Tests: `tests/test_rrb_round_rating.py`.
Outputs: `out/rrb_study/` (gitignored).

---

## 0. The question being asked, and the one it is not

An external pilot (n = 27 rounds, 25 labelled) proposes to **delete one whole step from the round
logger** — the `difficulty` and `intensity` steppers — and to derive the partner's strength from
the logged action sequence instead, via the RRB submission shares already published in
`data/rating/markov_action_weights.json`.

**The question the owner is asking:** *is the derived number more predictive than what production
uses today, and which of the pilot's adjustments actually help?*

**The question this study cannot answer, and will not pretend to:** whether the round-logger UX is
better without the step. That is a product decision. This study only supplies the rating-side
evidence for it.

**The confound that is named here before it can be discovered later.** The owner-side label
(`RoundSnapshot.outcome ∈ {succeeded, partial, failed, no_attempt}`) is *also* typed by the same
user in the same form, seconds after the entries. "RRB dominance predicts the recorded round
assessment" is therefore a statement about the internal consistency of one form, not about an
external outcome. It is the pilot's own criterion and it is reproduced faithfully, but every
verdict on dataset (a) is explicitly labelled **internal-consistency**, and the recommendation
must not rest on it alone. The external-outcome question is dataset (b).

---

## 1. What production actually does today (the A0 to beat)

The pilot benchmarked against `eloService.ts` — **V1, dormant since ADR-16 (2026-08-26)**. It is
not what runs. Production is Rating V2, Glicko-2, and the difficulty slider enters it in exactly
one place:

```ts
// GrapplingArcApp/src/services/rating/ratingV2Evidence.ts
const opponentRating =
  currentGlobal.rating +
  RATING_V2_CALIBRATION.ELO_PER_DIFFICULTY_POINT * (difficulty - RATING_V2_CALIBRATION.DIFFICULTY_BASELINE);
```

`ELO_PER_DIFFICULTY_POINT = 70`, `DIFFICULTY_BASELINE = 5`, `ATTEMPT_WEIGHT = 0.1`
(`ratingV2Calibration.ts`, verified 2026-09-12). Each own-actor entry is one Glicko-2 observation:
score `1` unless `successful === false`, weight `ATTEMPT_WEIGHT × relativeShares(...)[i] × n`
(mean-1 Markov split), opponent = the virtual partner above. The Analytics mirror is
`analysis/rating_v2/node_rating.py` (ADR-16: own-anchor, re-anchored on the final global).

So `difficulty` buys exactly one thing: **a ±280 Elo band around the athlete's own current
rating** (difficulty 1..9 → −280..+280). `intensity` buys **nothing at all** — grep confirms no
rating path reads it. That asymmetry is registered now because it changes what "remove the step"
means: removing `intensity` costs the rating engine nothing by construction, and only `difficulty`
is actually on trial.

## 2. Arms

Every arm is a rule for producing, from one round/bout, either (i) a **dominance score**
`P_own ∈ (0,1)` and (ii) a **virtual opponent offset** in Elo points. `P_partner = 1 − P_own`.

| id | arm | dominance score | opponent offset | runs on |
|---|---|---|---|---|
| **A0** | **production V2** — difficulty→virtual opponent, Markov-weighted observations | n/a (score is the `successful` flag) | `70 × (difficulty − 5)` | owner |
| **A0g** | **production global track** — Glicko-2 on the bout result, real opponent | binary winner | real opponent pre-bout rating | corpus |
| **A0n** | **the null the pilot omitted: delete the sliders and substitute NOTHING** | `successful` flag | **0** (opponent = own current global) | owner |
| **A1** | RRB dominance as ex-post performance, pilot defaults γ=1, T=1, terminal=marginal | `sigmoid(Z)` | `400·log10(P_own/(1−P_own))` | both |
| **A2** | A1 with the pilot's adjustments **swept** (§4) | as A1 under the sweep cell | as A1 | both |
| **A3** | RRB score against a **pre-bout partner prior** (the opponent's real rating) | RRB `P_own` | real opponent pre-bout rating | corpus only — see §2a |
| **A4** | **hybrid, the candidate closest to production** — keep Glicko-2 and the `successful`-flag score, replace difficulty→opponent by an RRB-derived offset | `successful` flag | `400·log10(P_own/(1−P_own))` from the round's OWN actions | owner (+ corpus analogue) |
| **N1** | marginal rate / home-advantage null | constant = training-set base rate | — | both |
| **N2** | `difficulty` alone, as a raw score | difficulty | — | owner |
| **N3** | `intensity` alone, as a raw score | intensity | — | owner |
| **N4** | mapped-sequence LENGTH alone | number of mapped entries | — | both |

`N4` is not decoration. Hvattum's (2019) plus-minus review is a catalogue of performance metrics
that turned out to measure exposure; the pilot's own γ=0 result (ρ = 0.49 with length) is that
failure mode showing itself. An arm that cannot beat N4 is measuring how much the athlete typed.

### 2a. A3 is not runnable on the owner's data, and that is a finding

The App's `RoundSnapshot`/`RoundEntry` carry **no partner identity** — `actor` is the literal
string `'you' | 'partner'` and nothing else (`GrapplingArcApp/src/types/session.ts`, grep for
`partnerId`/`partnerName` returns nothing). The pilot's own escape from self-cancellation is
"ex-post performance **or** a pre-round partner prior"; the second half of that disjunction has
no data to stand on in the App as it exists. A3 is therefore registered **corpus-only**, and the
owner-side conclusion has to survive on ex-post performance alone.

### 2b. Self-cancellation, stated as an identity to be tested, not a worry

If the same round's `P_own` is used both as the observation score `s` and (through the offset) as
the thing that sets `E`, then by construction `E = P_own` and `s − E = 0`: the rating never moves,
regardless of what happened. This is the opponent-indifference result of Chen, Sun, Seif El-Nasr
& Nguyen (2017, arXiv:1702.06253) in its sharpest form — when the update is driven by a
performance rating, the opponent's rating drops out of the update. **Pre-registered as an exact
identity**, asserted in `tests/test_rrb_round_rating.py`, not as an empirical finding. It is why
A1 (score = RRB, offset = RRB) is registered as a *diagnostic* arm and A4 (score = flag, offset =
RRB) as the *candidate* arm.

## 3. Definitions, fixed now

Let the round/bout produce an ordered list of `(code, actor)` where `code = lamas_state(event)`
(`analysis/lamas_chain.py`) and `actor ∈ {own, other}`. Unmapped events (`code is None`) are
**dropped from the dominance score** — not given the mean, which is what `markov_weights.weight_of`
does for the *weight* channel. The two channels are different questions: "how much information
does this action carry" (weight — mean is the no-information value) versus "who was winning here"
(value — an unmapped action says nothing about who, and averaging it in is inventing a reading).

```
v(code)   = the block's published share, per §3a
z_i       = log( v(code_i) / (1 − v(code_i)) ) · (+1 if own else −1)
Z(γ, T)   = ( Σ_i z_i ) / ( n^γ · T )          n = number of MAPPED actions
P_own     = sigmoid(Z)
offset    = 400 · log10( P_own / (1 − P_own) )
C         = 1 − 2·|P_own − 0.5|                (competitiveness ∈ [0,1])
k_mult    = λ · shape(C),  shape ∈ {C, C², √C}
```

`γ = 1` is the pilot's mean (length-normalised); `γ = 0` is the pilot's "full compounding" (a plain
sum, which grows with n and is the length artefact); `γ = 0.5` is the √n middle. This is the
parameterisation that reproduces both of the pilot's stated behaviours from one exponent, and it
is fixed here so it cannot be redefined after seeing a table.

### 3a. Terminal handling — three settings, one of which is the leakage control

`SUB` carries share **0.8065** in the `global` block, and the artefact's own provenance says why
that is partly circular: `n_terminal = 104` of `n = 176` appearances of `SUB` **are** the chain's
absorbing last step. Reading it at face value lets "the bout ended in a submission" enter the
score that is then asked to predict who won.

| setting | `SUB` | `SUBA` | purpose |
|---|---|---|---|
| `landed` | 0.8065 | 0.5178 | the artefact as published — the optimistic reading |
| `marginal` | **0.5582** | **0.5582** | the pilot's proposal: the n-weighted mix of the submission family, `(1082·0.5178 + 176·0.8065)/1258 = 0.5582` — reproduced exactly from the artefact's own `provenance.actions.global` counts, not quoted from the pilot |
| `drop` | — | — | **the leakage control**: the whole submission family is removed from the score |

`drop` is registered **before** any cross-tab between "a submission code appeared" and the label is
computed. On the owner's data 83 of 184 mapped entries are `SUB`/`SUBA` (§5) and a landed
submission is plainly not independent of typing `succeeded`; on the corpus 192 of 407 decided
bouts ended by SUBMISSION. If an arm's advantage evaporates under `drop`, the arm was reading the
outcome.

### 3b. λ and the "K budget"

The pilot fixes λ ≈ 0.549 "to keep the K budget". Registered operationally: λ is whatever makes
`mean(k_mult) = 1` **over the training split only**, so the total information a corpus contributes
is invariant to the shape — the same mean-1 invariant the Markov contract already declares. The
pilot's 0.549 and the values 0.4 / 0.7 / 1.0 are swept as fixed points around it; the
budget-preserving λ is reported alongside.

## 4. The sweep (A2)

Full grid, 3 × 3 × 3 × 3 × 4 = **324 cells**, run only on the corpus (dataset (b)), where the
label is external:

* γ ∈ {0, 0.5, 1}
* T ∈ {0.75, 1, 1.5}
* terminal ∈ {landed, marginal, drop}
* K shape ∈ {linear, quadratic, sqrt}
* λ ∈ {0.4, 0.549, 0.7, 1.0}

**T and λ cannot change an AUC** (both are monotone rescalings of `Z`, and `k_mult` does not enter
a ranking) — that is stated *now*, so a flat column in the results table reads as an arithmetic
fact rather than as a null result. They are swept because they move log-loss/Brier and the
realised rating spread, which is where they must be judged.

Multiplicity: the sweep is **exploratory and carries no verdict**. Only the 8 lettered arms of §2
carry one, and their p/interval decisions go through Benjamini–Hochberg at α = 0.05
(`stats_rigor.benjamini_hochberg`).

## 5. Datasets, with the marginals that were measured first

### (a) Owner's own rounds — PRIVATE, `owner_id = 0c555ab2-…`, this owner only

Measured read-only, 2026-09-12, from `user_sessions.data`:

| quantity | value |
|---|---|
| sessions / rounds / entries | 39 / 140 / 640 |
| rounds carrying a binary assessment (`succeeded` vs `failed`) | **100** (67 / 33) |
| `partial` / `no_attempt` | 22 / 18 |
| date span | 2026-07-22 → 2026-08-25 |
| entries by actor | `you` 475, `partner` 165 |
| `successful` | `true` 555, `false` 72, absent 13 |
| **Lamas mapping coverage** | **184 / 640 = 28.8 %** |
| rounds with ≥ 1 mapped entry | 103 / 140 |
| rounds with mapped entries on **both** sides | **18 / 140** |
| mapped entries per round (mean / median / max) | 1.79 / **1** / 5 |
| mapped codes present | CDP 10, GPSA 3, GPS 39, SWPA 2, SWP 22, TKDA 5, TKD 20, SUBA 28, SUB 55 |
| mapped codes **absent** | PGD, BTKA, BTK — no back-take or guard-pull mapping fires at all |

This is the single most consequential marginal in the study and it is recorded before any verdict:
**for the median round, the "mean actor-signed log-odds" is computed over ONE action**, and 83 of
the 184 mapped actions are submission-family. The pilot's `n = 25` was not the constraint; the
constraint is that the App's round vocabulary (`control` 265, `guard` 124, `escape` 39 — all
unmapped) barely intersects the Lamas action space the weights are defined on.

### (b) Public corpus — `matches`, `status='final'`, PUBLIC (privacy class A)

Gate reused verbatim from `analysis.poc.e9_markov.load_corpus`: `sequence IS NOT NULL`, ≥ 4
events, `attribution.bout_flags(...)['perspective_reliable']`.

| quantity | value |
|---|---|
| final bouts with a sequence | 911 |
| after the gate | **465** |
| of those, with `winner_id` | **407** (SUBMISSION 192, DECISION 212, POINTS 3) |
| Lamas coverage | 5290 / 7558 = **70.0 %** |
| mapped actions per bout (mean / median) | 11.38 / 7 |
| bouts by year (decided, gated) | enough for rolling origins ≤2023→2024, ≤2024→2025, ≤2025→2026 |

## 6. Metrics and protocol

Proper scoring rules throughout, per Leitner, Zeileis & Hornik (2009) and the benchmark frame of
Kovalchik (2016): a rating system is judged by the **calibrated probability it assigns to a future
result**, not by its spread or its face validity.

* **AUC** — `stats_rigor.auc`, percentile bootstrap, **4000 draws, seed 20260820** (the repo's
  pinned pair; the task asked for 2000, and the larger pinned value is used instead so the
  interval is comparable to every other AUC in `docs/research/`. Stated, not silently changed.)
* **Brier** and **log-loss** — computed on the same held-out units; paired bootstrap for Δ,
  resampling unit = **the round** (owner) / **the bout** (corpus), never the event.
* **Chronological order is never broken.** Owner: rounds processed in `(session createdAt, round
  index)` order, prequential — every probability is issued from state that saw only earlier
  rounds. Corpus: `next_moves.split_by_year` + rolling origins (≤2023→2024, ≤2024→2025,
  ≤2025→2026), reported per fold, **never pooled**.
* **Length artefact** — `stats_rigor.spearman(|Z|, n_mapped)` per arm, reported for every arm
  including the winners.
* **Coverage** — reported per arm as the share of units on which the arm could produce a score at
  all. An arm that scores 60 % of rounds and an arm that scores 100 % are not comparable on a
  pooled number; the common-subset number is reported alongside.
* **Determinism** — sorted iteration everywhere, fixed seeds, `matplotlib` Agg. Two runs must be
  byte-identical.

## 7. Hypotheses and verdicts

Each gets exactly one of **PASS / FAIL / NULL** (NULL = the interval contains the no-effect point,
i.e. the data cannot tell). Criteria are fixed here.

| id | hypothesis | PASS iff |
|---|---|---|
| **H1** | *(owner, internal-consistency)* RRB dominance (A1, pilot defaults) separates `succeeded` from `failed` better than `difficulty` (N2) | paired ΔAUC bootstrap interval **strictly above 0** |
| **H1b** | H1 survives the leakage control | H1 passes **and** still passes with terminal = `drop` |
| **H2** | *(owner, prequential)* the A4 hybrid predicts the next round's assessment better than A0 (production) on log-loss | paired Δ log-loss interval strictly below 0 |
| **H2n** | *(owner, the lazy arm)* A4 beats **A0n** — deleting the sliders and substituting nothing | paired Δ log-loss interval strictly below 0 |
| **H3** | *(corpus, sanity)* bout-level RRB dominance identifies the recorded winner | AUC interval strictly above 0.5 |
| **H3b** | H3 is not a submission tautology | H3 passes with terminal = `drop` |
| **H4** | *(corpus, the one that matters)* a rating updated by an RRB arm predicts the NEXT year's winners better than A0g | Δ log-loss interval strictly below 0 **in ≥ 2 of 3 folds**, and never strictly worse in the third |
| **H5** | *(both)* the length artefact is controlled at γ = 1 | `|ρ(|Z|, n_mapped)| < 0.20` at γ = 1 **and** materially larger at γ = 0 |

## 8. Death rules — what kills an arm outright

Written before the run so no arm can be rescued by a post-hoc adjustment.

1. **Coverage floor.** An arm that produces a score on **< 50 %** of the labelled units of a
   dataset is reported as *not estimable* on that dataset and carries no verdict. It does not get
   a "restricted to the rounds where it works" headline number.
2. **Both-sides floor (owner only).** A *signed* dominance score needs both corners to appear. The
   owner has 18/140 rounds with both sides mapped. The signed arms are therefore reported on the
   full set **and** on that 18-round subset, and if the two disagree in direction the arm is
   declared **not estimable on this data** — 18 is below any honest bar.
3. **Leakage death.** An arm whose advantage over the relevant null disappears under
   terminal = `drop` is dead, however good its `landed` number was. This is not a tie-break; it is
   a disqualification.
4. **Length death.** An arm with `|ρ(|Z|, n_mapped)| ≥ 0.4` is dead regardless of AUC — it is
   measuring exposure (Hvattum 2019).
5. **Null death.** An arm that does not beat N1 (marginal rate) on log-loss is dead. An arm that
   does not beat N4 (length alone) on AUC is dead.
6. **The lazy death (the one most likely to fire).** If **A0n** — removing the sliders and putting
   *nothing* in their place — is statistically indistinguishable from A0 **and** from A4, then the
   recommendation is to remove the step and build **no** replacement. RRB's burden is not to beat
   `difficulty`; it is to beat *the absence of difficulty*. An RRB implementation that only ties
   with substituting zero is complexity with no measured buyer.
7. **Self-cancellation death.** A1-as-a-rating-update (score and offset from the same round) is
   dead by §2b's identity before it is run, and is kept only as the diagnostic that proves the
   identity holds in code.

## 9. What is deliberately NOT done

* **No per-node replay on the corpus.** The claim under test is about the *global* track's score
  and opponent; `node_rating.py` projects from that global and would add a second moving part.
  `ponytail:` ceiling — a node-level sweep is a separate cell if the global result justifies one.
* **No write of any kind.** No prod DB mutation, no replay persisted, no `computed_elo` touched,
  no artefact regenerated. Everything lands in `out/rrb_study/` (gitignored).
* **No owner row in the report.** Dataset (a) appears as aggregates only — counts, rates,
  intervals. No round text, no reflection, no label, no session id.
* **`intensity` is not rehabilitated.** It is scored as N3 for completeness and because the pilot
  reported ρ = 0.22 against competitiveness, but no path exists to make it a rating input and none
  is proposed.

## 10. Privacy

Dataset (a) is **PRIVATE, owner-fed**. It is pulled for exactly one `owner_id`, it serves that same
owner's own product decision, and it is the permitted direction under the root `CLAUDE.md` rule
(private data may serve the owner's own experience). It is **never** mixed with dataset (b): no
owner round enters a centroid, an archetype, an ELO, the athlete corpus, `data/finetune` or the
`site/` bundle, and the two datasets are loaded by two functions that never meet. Dataset (b) is
privacy class **A, public competition data**, filtered `status='final'` on `matches` — no
`owner_kind='user'` graph is read anywhere in this study.

## 11. Literature the design leans on

| Source | What it fixes here |
|---|---|
| Kovalchik (2016), *Searching for the GOAT of tennis win prediction*, JQAS | The benchmark frame: rating systems compared by log-loss + calibration on FUTURE matches, against a named reference. §6 and H4 are that protocol. |
| Chen, Sun, Seif El-Nasr & Nguyen (2017), *Opponent Indifference in Rating Systems: A Theoretical Case for Sonas*, arXiv:1702.06253 | The self-cancellation identity (§2b) is their opponent-indifference result; it is also the reason a performance-rating update must take its score from somewhere the opponent term does not already encode. |
| Gorgi, Koopman & Lit (2019), *Analysis and Forecasting of Tennis Matches by using a High Dimensional Dynamic Model*, JRSS-A | The A4 framing: keep a latent dynamic strength, feed within-match statistics in as observations with their own noise, rather than replacing the latent state with the statistic. |
| Leitner, Zeileis & Hornik (2009) | Ratings are probability statements; evaluate with proper scoring rules. Brier/log-loss are primary, AUC secondary. |
| Hvattum (2019), plus-minus ratings review | The exposure/length artefact class. N4 and death rule 4 exist because of it. |
| Lamas et al. (2024), *No-gi BJJ: a Markovian analysis* | The state space and the reward-risk quantity the RRB shares are derived from (`analysis/lamas_chain.py`). |
| Glickman (1999/2012), Glicko-2 | Weighted observations as the continuous extension of repeat-count; already the basis of `ATTEMPT_WEIGHT` on both sides. |

> The workspace `scientific-papers` MCP was **not available in this run's tool set** (only
> filesystem/bash/web tools were exposed). The five external references above were supplied by the
> orchestrator with full citations; they are used for design framing and are not re-derived here.
> No number in this study is taken from a paper.

---

*Pre-registration ends here. Everything below the line in
`docs/research/rrb_round_rating_study.md` is generated by the runner.*

---

# ADDENDUM — granularity arms, the objective target, and a mapper defect

Written 2026-09-12, **after** the §1–§11 arms were scored and **before** any arm in this addendum
was. Triggered by a second external report from the owner. Everything fixed below was fixed
against marginals (coverage counts, how many rounds end in a landed submission), never against a
label.

## A1. A defect in the harness, found by the second report and reproduced exactly

The second report says the production mapper covers **28.7 %** of the owner's events because it
reads **localized labels**, and that fixing aliases lifts coverage to **50.0 %**.

Both numbers reproduce here to the digit. Measured 2026-09-12:

| mapper | coverage | what appears |
|---|---|---|
| `lamas_state(raw label)` — §5's number | **28.75 %** (184/640) | no `PGD`, no `BTKA`, no `BTK` |
| `lamas_state(technique_match.clean_label(label, type))` | **50.00 %** (320/640) | `BTK` 94, `BTKA` 20, `PGD` 22 appear |

The owner logs in **pt-BR** — `control/Costas` (108), `control/Montada` (67), `guard/Meia Guarda`
(53), `transition/Puxada para Guarda` (22). `lamas_chain`'s label-level rules match English tokens
(`"back control"`, `"hooks in"`, `"body triangle"`, the guard-pull and clinch lists), so the
single largest action in the owner's log — back control, 108 entries — was silently invisible.

**This is my defect, not the corpus's:** `analysis/technique_match.clean_label` already exists and
already does exactly this (pt/variant → canonical English, type-hint guarded). The §1–§11 owner
numbers were computed without it. Every owner-side arm is **re-run** through it, the original
numbers are kept in the report as `raw` for contrast, and the canonical mapper is the one that
carries a verdict from here on.

The 320 that still do not map are `Montada` (mount), `Meia Guarda`, `Quatro Apoios` (turtle),
`Guarda Fechada` — **dwell/position states**, which `lamas_chain`'s rule 2 excludes by design and
which the second report also identifies as the remainder. That is a property of the Lamas action
space, not a second bug.

## A2. Target T2 — finish-side from the prefix (the objective test)

The owner's second report proposes, and this addendum adopts, an **objective** target to sit beside
the subjective round assessment:

> For every unit that ended in a **landed submission**, delete the terminal event and predict from
> the prefix alone **which side lands it**.

This target has no self-report in it. It is registered on both datasets:

* **owner rounds** — a round whose last entry is `type='submission'` with `successful` truthy.
  Label = 1 if that entry's actor is `you`. Marginal measured: 51 terminal submission attempts, 42
  landed (the second report's counts, reproduced).
* **public corpus** — bouts with `win_type = 'SUBMISSION'`; delete the last mapped `SUB` step;
  label = 1 if the winner is `athlete_a`. ~192 bouts before gating.

T1 (round assessment / recorded winner) and T3 (next-bout forecast) are unchanged from §7.

## A3. Granularity arms — the owner's explicit request

All arms below are scored on **T1, T2 and T3**, with coverage and length-ρ reported per arm. Let
`V(code)` be the value table of §3a and `z_i = ±logit(V(code_i))` the actor-signed log-odds of
step *i* (own +, partner −), over MAPPED steps only.

| granularity | definition, fixed here | why it is a different arm |
|---|---|---|
| **`actions`** | `Z = Σ z_i / n^γ` — §3, the pilot's statistic | repetition-weighted: doing a thing twice counts twice |
| **`states`** | `Z = z_last`, the **last non-terminal** mapped step | a position, not a count. Length-insensitive **by construction** — this is the owner's "state-based arms should be less length-sensitive", registered as a prediction to be tested, not assumed |
| **`states_occ`** | `Z = mean z over DISTINCT (code, actor) pairs` | occupancy rather than repetition; the middle case between `actions` and `states` |
| **`edges`** | `Z = Σ (z_{i+1} − z_i) / (n−1)^γ` over consecutive mapped steps | the **progression**, i.e. how far the RRB standing MOVED — `rrb_progression.trajectory`'s own question, and the VAEP/xT form (value of the state reached minus the state left). At γ=0 it telescopes to `z_n − z_1` and is therefore length-free in the strongest sense |
| **`actions_states`** | mean of the two Zs | the owner's requested combination |
| **`edges_states`** | mean of the two Zs | idem |

Averaging Zs is coherent because every component is on the same log-odds scale.

Coverage rules, fixed now: `actions`/`states`/`states_occ` need ≥ 1 mapped step; `edges` needs
≥ 2. A unit below that produces no score and is excluded from that arm's n (and reported in its
coverage).

**Edge values are NOT re-estimated.** The edge arm reads the same published state values and takes
their difference. Estimating a per-edge value from the corpus transition network would be a second
artefact with its own gates, its own coverage and its own PR — `ponytail:` ceiling, and the
telescoping form already answers "is progression better than accumulation", which is the question.

## A4. Two numbers from the second report to adjudicate, not merely restate

* **λ.** The second report says λ = 0.548 "keeps the mean K budget"; §5 of the results measured the
  budget-preserving λ at **1.042** (linear). These cannot both be right for the same definition.
  The definition used here is stated and testable: λ such that `mean(λ · shape(1 − 2|P − 0.5|)) = 1`
  over the scored units. Whichever number the data gives under **that** definition is reported, and
  the disagreement is reported as a disagreement rather than averaged away.
* **ρ(competitiveness, intensity).** Pilot 1 reported +0.22, the second report −0.01. Measured
  directly and reported; no verdict rides on it.

## A5. Verdict form

The addendum's table reports, per **(arm × target)**: n, coverage, AUC + CI, Brier, log-loss,
length-ρ, **and the paired Δ against the production V2 signal for that target with its CI** —
oriented difficulty for T1/T2 on the owner's rounds, `A0g_winner` for T3 on the corpus. An arm
"beats production" only when that paired interval excludes 0. AUC alone carries no verdict.

Death rules §8 apply unchanged, including rule 3 (leakage): every arm is reported at
`terminal = marginal` **and** `terminal = drop`.

---

# ADDENDUM §B — the FULL catalogued-node Markov chain, and high-confidence inference

Written 2026-09-12, **before any §B arm was scored**. Two owner decisions:

> *"vamos tentar usando o markov de todas as ações catalogadas, não das ações Lamas"*
> *"usando também a inferência de alta confiança já estabelecida"*

§A's finding was that the bottleneck is the **Lamas 12-state vocabulary**: 50 % of the owner's
entries map, and the 320 that do not are dwell/position states (`Montada`, `Meia Guarda`,
`Quatro Apoios`) that the Lamas space has no state for. §B removes that ceiling by replacing the
12-state space with the **whole catalogued node vocabulary**, and by letting the production chain
compiler **infer** the actions that a state→state pair implies.

## B1. The value function, fixed now

Vocabulary = every canonical `node_key` appearing in the public corpus sequences, derived by the
repo's own key map: `canonicalize(_normalize_name(clean_label(label, type)))` — the same
derivation `node_rating.node_key_of` uses, with `clean_label` in front so pt-BR and English land on
one key. Measured read-only 2026-09-12: **252 distinct keys over 10 121 corpus events** (129 with
≥5 occurrences, 97 with ≥10).

States are **lifted by side** relative to a reference athlete, exactly as `lamas_chain.rrb` lifts
its 12: `(node_key, is_own)`. Each gated bout contributes its ordered event stream **twice**, once
with each athlete as reference, which makes the mirror `v(node, own) = 1 − v(node, opp)` exact by
construction rather than an assumption (asserted in the runner).

Absorbing states: `FIN_own` / `FIN_opp` when the bout ended in a submission by that side,
`END_other` for every other ending (decision/points/unknown — never recoded as a draw, ADR-06).

```
v(node) = B_own(node) / ( B_own(node) + B_opp(node) )        B = (I − Q)⁻¹ R
```

i.e. **P(the reference side's finish comes before the opponent's, given the chain is at this node
and does reach a finish)** — the same conditional `lamas_chain.rrb`'s `sub_share` publishes, on a
21× larger state space. Dirichlet/Laplace smoothing **α ∈ {0.5, 1}** is added uniformly over every
destination including the absorbing ones, so a rarely-seen node shrinks toward 0.5 rather than
inheriting a singleton's outcome. **Both α values are reported; neither is chosen after seeing a
verdict** — if they disagree on a verdict, the verdict is NULL.

## B2. Arms

| arm | score of one round/bout |
|---|---|
| `fullchain_nodes` | actor-signed mean log-odds of `v` over mapped entries, `Σ ±logit(v_i) / n^γ`, γ ∈ {0.5, 1} |
| `fullchain_edges` | actor-signed mean of `v(target) − v(source)` over consecutive mapped pairs (needs ≥2) |
| `fullchain_last` | `±logit(v)` of the LAST non-terminal mapped node — "where were you when it ended" |
| `actions_fullchain` | mean of the Lamas `actions` Z and the `fullchain_nodes` Z |

Terminal handling as §3a: `marginal` (the submission family's own corpus marginal replaces the
terminal node's value), `drop` (submission-family nodes removed), `landed` (values as estimated).

## B3. The `+inferred` variant — every family gets one

Before scoring, each unit's event stream is run through the **production** chain compiler
(`analysis/chain_compiler.compile_two_sided` → `analysis/taxonomy_kind`, Fase 2: actor + two stance
axes + redundancy; exit orientation may only SUPPRESS an inference, never create one). The
compiler splices inferred actions (`ChainAction.inferred=True`, `provenance='inferred'`) into the
observed buffer without reordering, so `Guarda → Montada` yields an inferred `guard pass` and
`Meia Guarda` bottom → top yields a `sweep`.

**Strict tier, stated explicitly.** The compiler's confidence gate is `actor_readable`, which it
documents as `attribution.bout_flags(...)` — and its own default is the *cheap* one-sided test. The
**strict** reading is taken: `actor_readable = bout_flags(...)['perspective_reliable']`, the same
flag that already gates every bout into this study (so it is `True` on all 465 gated bouts, and
one-sided bouts never reach the inference rule at all). On the owner's rounds both corners are
logged (`you` 475 / `partner` 165), so `actor_readable=True` is honest there and is passed
explicitly rather than inferred from bucket sizes.

Two contracts inherited from the compiler and **not** re-implemented here:

* **an inferred action never becomes a state** — inferred entries enter the node sequence as
  actions only;
* **the redundancy rule prevents double counting** — when an observed action already explains the
  state delta, the rule suppresses the inference. That is the compiler's Fase 2 behaviour and is
  used as-is.

**The SAME inference is applied when building the corpus chain**, so `v()` and the scored sequences
live in one universe. A `+inferred` arm scored against a non-inferred `v()` would be comparing two
different state spaces and is not run.

## B4. Controls — all three are load-bearing

1. **Frequency null (`fullchain_freq`).** `v` replaced by the node's corpus **frequency percentile**
   (same vocabulary, same mapping, same coverage, no value). If `fullchain_nodes` cannot beat this,
   the signal is the vocabulary, not the value, and §B is a coverage story with a rating costume.
2. **No leakage into T3.** Inside every rolling-origin fold, `v` is re-estimated on the **training
   years only** (`year ≤ cutoff`). The pooled `v` is never used for a forward number.
3. **The owner's rounds NEVER enter the corpus chain.** Private → public is forbidden (root
   `CLAUDE.md`). The chain is estimated on `matches` alone; the owner's rounds are only ever
   *scored* against it. This is enforced by construction — the chain builder takes corpus bouts and
   has no path to `user_sessions` — and asserted in the runner.

## B5. What must be reported regardless of verdict

* **coverage before/after**, per arm, on the owner's 640 entries: the §A canonical mapper reached
  50.0 %; how many of the **320 still-unmapped** entries now carry a value.
* **number of inferred actions** added, on the owner's rounds and on the corpus.
* **is `v` informative or flat?** The spread of `v` across the vocabulary, and the specific values
  of `Montada`, `Meia Guarda`, `Quatro Apoios`, `Costas`. A `v` that is ≈0.5 everywhere is a null
  result and must be reported as one — §5's own caveat about the Lamas amplitude ("a cadeia mistura
  mais rápido do que absorve") is the prior here, not a surprise.
* **length artefact ρ per arm**, same as §A.
* **ΔAUC with CI against production** for T1/T2, and Δ log-loss vs `A0g` for T3. An arm beats
  production only when the paired interval excludes 0. **Whether inference helps** is its own paired
  Δ: `+inferred` arm vs the same arm without it, with CI.

## B6. Death rules, added to §8

8. **Flat-value death.** If `sd(logit(v))` over the vocabulary weighted by the owner's mapped
   entries is below 0.1, the arm is reported as *not informative* regardless of AUC — an AUC built
   on a value that does not vary is being carried by something else.
9. **Frequency death.** An arm that does not beat `fullchain_freq` with a clean interval is a
   vocabulary result, not a value result, and is reported as such.
10. **α-disagreement death.** If α=0.5 and α=1 give different verdicts for an arm, that arm's
    verdict is NULL.

---

# ADDENDUM §C — personalized hierarchical layers, and the coherence of the inferred Elo

Written 2026-09-12, **before any §C arm was scored**. Triggered by a third external artefact: an
owner-run notebook over the same 140 rounds. **The notebook itself was not available to us** —
only the numbers below. Everything in §C is therefore a **re-implementation from a described
model**, and any disagreement is as likely to be a difference in the re-implementation as a defect
in either side. That is stated up front and repeated in the report.

## C0. The numbers to reproduce or refute

Target: finish-side from the prefix (our T2). Personal layers learned walk-forward from round 10;
per-session bootstrap.

| model | AUC | Brier | log-loss |
|---|---|---|---|
| global Lamas-state | 0.933 | 0.214 | 0.620 |
| **personalized** Lamas-state | 0.943 | 0.106 | 0.367 |
| action-label (personal per-label values) | 0.963 | 0.127 | 0.428 |
| action-label + hierarchical directed-edge residual | **0.963** | **0.095** | **0.303** |
| walk-forward action-label | 0.962 | 0.142 | 0.464 |
| walk-forward + edge residual, gate = 3 | 0.962 | 0.111 | 0.346 |
| gate = 5 / 10 | — | 0.123 / 0.138 | 0.384 / 0.442 |

ΔBrier (gate 3) −0.0318, 95 % CI [−0.0450, −0.0195]; **ΔAUC ≈ 0**.

Their conclusion, which §C tests rather than assumes: *action-label is the dominance backbone, the
edge residual is a **calibration** layer, and edge-RRB must not be the main score.* Note that this
agrees with §B independently — §B measured `fullchain_edges` as the weakest owner-side arm on T1
(0.837–0.856 vs `fullchain_nodes` 0.936) while it was the strongest on T2, which is exactly what
"good residual, bad backbone" looks like.

## C1. The model, fixed here

Three layers, each shrunk toward the one above it (Beta-Binomial, the standard hierarchical form):

```
prior       v_prior(k)    = the §B corpus absorption value of node_key k (global, public corpus)
labels      p_label(k)    = ( s_k + α · v_prior(k) ) / ( n_k + α )
edges       p_edge(e)     = ( s_e + α_e · p_hat(e) ) / ( n_e + α_e ),  p_hat(e) = σ( (logit p_label(src) + logit p_label(dst)) / 2 )
residual    R_e           = logit p_edge(e) − logit p_hat(e)              [only when n_e ≥ gate]

Z = Σ_i ±logit(p_label(k_i)) / n   +   λ · mean_e( ±R_e )
P = σ(Z)
```

`s_k` accumulates the **actor-oriented outcome**: a round that the reference side finished
contributes 1 for every own-side entry and 0 for every partner-side entry; a round the partner
finished contributes the mirror. `n_k` is the count. The sign in `Z` is the entry's own side, as in
every other arm in this study.

**Strict walk-forward.** Every personal count for round *t* is accumulated over rounds **strictly
earlier in time** (`(session createdAt, round index)`, the same total order §A uses). A round can
never see itself, and the layers are re-fitted at every step. The first rounds therefore score on
the prior alone; the **warm-up is reported at 10 rounds with a finish**, matching the notebook, and
the pre-warm-up rounds are reported separately rather than dropped silently.

**Grid:** gate ∈ {1, 2, 3, 5, 10}, α = α_e ∈ {0.5, 1, 2}, λ ∈ {1, 1.5, 2}. The notebook's cell is
gate = 3, α_e = 1, λ = 1.5.

**Bootstrap unit = the SESSION**, not the round (the notebook's choice, and the right one: rounds
within a session share a partner, a day and a mood). 4000 draws, seed 20260820.

## C2. The null that decides whether the gain is real

**`hier_shuffled`** — the identical hierarchy, identical parameter count, identical walk-forward,
fitted on **permuted node keys** (a fixed random relabelling of the owner's vocabulary, seed
20260820). Capacity preserved, information destroyed. If the personal layers' Brier gain survives
against the real arm but is matched by the shuffled one, the gain is **capacity**, not knowledge —
the model is memorising round-to-round autocorrelation, not learning what a technique is worth.
This null is pre-registered because a personalized, walk-forward, hierarchically shrunk model with
three free parameters fitted on 140 rounds is exactly the shape that gets a Brier gain for free.

**Verdict rule:** the hierarchy PASSES only when `ΔBrier(hier vs labels-only)` excludes 0 **and**
`ΔBrier(hier vs hier_shuffled)` excludes 0. Either one alone is not enough.

## C3. The coherence experiment (owner's proposal, pre-registered)

Every arm produces one `P` per round. Two transforms:

```
relative Elo offset    ΔR  = 400 · log10( P / (1 − P) )
coherent update        ΔR' = K · λ · C · (S − 0.5),   C = 1 − 2|P − 0.5|,  S = the round's outcome
```

Arms compared: `difficulty` (production), `intensity`, `states_only` (Lamas `states_occ`),
`labels_only` (§B `fullchain_nodes`), `hier_residual`. Five measurements, all **descriptive** — §C3
carries **no PASS/FAIL verdict**, because "smoother" is not "more correct" and this study will not
pretend otherwise:

* **(a) stability within a session** — sd of ΔR across the rounds of one session (averaged over
  sessions with ≥3 rounds) and lag-1 autocorrelation of ΔR inside a session. Same partner, same
  day ⇒ a partner-strength estimate ought to be smoother, *if* it is estimating the partner. A
  perfectly smooth arm that is also uninformative is worse, so (a) is read only alongside (c)/(e).
* **(b) leave-one-entry-out sensitivity** — mean and max |ΔR(full) − ΔR(drop one entry)| over every
  entry of every round. How much one logged action moves the inferred partner Elo.
* **(c) agreement with the recorded assessment** — Kendall τ between ΔR and the outcome ordering
  (`failed` < `partial` < `succeeded`), all 122 rounds with an outcome.
* **(d) K budget** — `mean(λ · C)` under the **budget-preserving** λ measured in §A/§B (≈1.04–1.11),
  not the external 0.548, with the discrepancy restated.
* **(e) calibration against finish-side** — reliability curve (5 equal-count bins) and **ECE** on
  T2. This is the one that matters: the notebook's whole claim is a calibration claim, and a
  calibration claim must be judged by a calibration measure, not by AUC.

## C4. Death rules, extended

11. **Capacity death.** A personalized arm whose ΔBrier against `hier_shuffled` does not exclude 0
    is reported as capacity, not knowledge, whatever its absolute Brier.
12. **Warm-up death.** A gain that exists only before the warm-up (i.e. on rounds scored by the
    prior alone) is an artefact of the prior, not of personalization, and is reported as such.
13. **Smoothness is not a verdict.** No arm may be recommended on §C3(a) alone.

## C5. Privacy, restated for §C

The personal layers are fitted on the owner's own rounds and serve the owner's own rounds. They
are **never** written anywhere, never aggregated across users (there is one user), and never touch
the corpus chain — `v_prior` flows public → private only, which is the permitted direction. No §C
artefact leaves `out/`.

## §C6 — the agreed four-layer arm, and the question it must answer

Added 2026-09-12, **before the arm was scored**. Owner + external agent converged on one
construction; §C6 fixes it and, more importantly, fixes the **question**:

> Does the extra complexity buy anything beyond `actions_states` — the §A arm that already scores
> 0.989 on T2 with no personalization, no shrinkage and no fitted parameter?

**`hier4`, the arm:**

```
layer 1  prior      v0(k) = the GLOBAL LAMAS value of k's ATTEMPT code
                    (lamas_state({type, label: k, successful: False}) -> action_values),
                    falling back to the §B corpus absorption value, then 0.5
layer 2  labels     p(k)  = ( s_k + α·v0(k) ) / ( n_k + α )        empirical-Bayes, walk-forward
layer 3  context    Z_ctx = the Lamas `states_occ` Z of the same round
layer 4  residual   R_e   = logit p_edge(e) − logit p̂(e), EB-shrunk, only when n_e ≥ gate

Z = (1 − w)·Z_labels + w·Z_ctx + λ · mean_e( ±R_e )
```

The attempt code is used for the prior deliberately: the published Markov weights were derived
under the attempt reading, and looking a state up under any other reading returns a number that was
never measured for it (`markovActionWeights.ts`'s own documented divergence).

**Grid:** gate ∈ {2, 3, 5}, α ∈ {0.5, 1, 2}, λ ∈ {1, 1.5}, w ∈ {0, 0.5, 1}. `w = 0` IS the
"no context layer" ablation and `personal=False` (α → ∞, the value never leaves the prior) IS the
"personal layer removed" ablation; both are cells of the same grid, not separate code.

**Three head-to-heads, each paired, per-session bootstrap, 4000 draws:**

1. `hier4` vs **`actions_states`** (§A, T1 and T2) — ΔAUC and ΔBrier. *This is the decisive one.*
   If a four-layer, walk-forward, empirically-shrunk, parameter-fitted model cannot beat a
   two-term average of published constants, the complexity has no buyer.
2. `hier4` vs **production V2** in the prequential Glicko-2 log-loss protocol (§5c): `hier4`'s `P`
   becomes the virtual-opponent offset, everything else identical.
3. `hier4` vs **`hier_shuffled`** (capacity null, §C2) and vs its own two ablations.

**Pre-registered reading rule:** ΔAUC ≈ 0 with ΔBrier < 0 is a **calibration** result, not a
discrimination one, and must be reported as such — a better-calibrated probability is worth
something to a rating engine (it is what Glicko-2 consumes) and nothing to a ranking.

---

# ADDENDUM §D — two binding owner rules

Written 2026-09-12, **before any §D arm was scored**. Two owner decisions that change what the
study is allowed to treat as evidence.

## D1. The manual round outcome is NOT a rating input

> *"the manual round outcome (succeeded/partial/failed/no_attempt) is NOT a rating input — the
> sequence is the primary record."*

Consequence for this study, applied retroactively to how results are **read**, not to how they were
computed:

* **T1** (`succeeded` vs `failed`) is demoted to a **plausibility / sanity target**. It carries no
  product decision. It was already labelled internal-consistency in §0 of the prereg — this makes
  the demotion explicit and puts it in the verdict table.
* **The decision targets are T2** (finish-side from the prefix — objective, derived from the
  sequence itself) **and the prequential V2 comparison** (does the arm make production's Glicko-2
  forecast better).
* No arm may be recommended on T1 alone. H1/H1b keep their numbers and lose their authority.

## D2. Per-action success is INFERRED from the sequence, never assumed

> *"Per-action success/failure is INFERRED from the sequence when the resulting transition makes it
> observable (Half Guard → Sweep → Top ⇒ sweep succeeded; Back Control → RNC attempt → Back Control
> ⇒ attempt did not finish); when the sequence cannot resolve it, the action is UNKNOWN (NULL ⇒ no
> observation) — never auto-success/failure."*

This is ADR-06 one level down, and it is exactly what `analysis/rating_v2/node_rating.py` already
does with a NULL flag: **a missing outcome is lost coverage, never a manufactured result.**

### The rule, fixed here (reusing production's own tables, inventing nothing)

For each observed action sitting on a compiled `ChainEdge(source_state → target_state)`:

| condition | `success_source` | score |
|---|---|---|
| the target state's topological role **matches the action's declared exit orientation** (`data/taxonomy/inference_table.json` → `action_exit_orientation`, read through `attribution.classify`'s curated role for the target state) | `inferred` | **1.0** |
| the target state **equals the source state** (the chain came back to where it started) | `inferred` | **0.0** |
| the edge is terminal and the action is a submission that ends the chain | `inferred` | **1.0** |
| anything else — no target, an action-to-action gap, an orientation the table calls `neutral`, an unresolvable role | **`unresolved`** | **no observation** |

`neutral` exit orientations (`escape`, `submission` mid-chain, `transition`, `control`) resolve
**only** by the return-to-source rule; they can never be read as a success, because "neutral" means
the table makes no claim about where the action lands. Exit orientation may **suppress** a reading,
never manufacture one — the same direction of travel Fase 2's inference rule already obeys.

### What is reported, regardless of verdict

Counts of `flag` / `inferred` / `unresolved` per dataset: how many entries resolve by inference, how
many would need the manual flag, how many stay NULL.

### The three scoring modes, evaluated in the production V2 slot

| mode | score of one entry |
|---|---|
| **(a) `flag`** | today's behaviour: `successful === false → 0`, else 1 |
| **(b) `inferred_only`** | the inferred score; **`unresolved` produces NO observation**. The manual flag is ignored entirely. |
| **(c) `inferred_then_flag`** | the inferred score where resolvable, else the manual flag, else NULL |

**The product target is (b)/(c)** — the manual `successful` toggle becomes optional. Reported as
prequential log-loss / Brier / AUC with paired CIs against (a), plus the observation counts each
mode produces (a mode that scores fewer observations is not automatically worse, but the difference
must be visible).

### Death rule 14

An inference rule that resolves **< 25 %** of entries is reported as *insufficient to replace the
flag* regardless of how the arms that use it score — at that coverage, mode (b) is mostly measuring
which entries happened to be resolvable.
