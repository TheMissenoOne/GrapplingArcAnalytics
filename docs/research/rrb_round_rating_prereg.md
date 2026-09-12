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
