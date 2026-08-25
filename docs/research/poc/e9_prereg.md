# PoC-E9 — state space, Markov order, event kernels, history-dependent absorption

**STATUS: PRE-REGISTRATION ONLY. No corpus evaluation has been run against this document.**
Written before `analysis/poc/e9_markov.py` produced a single held-out number; the runner
re-emits every line of this section verbatim above its results, so the criterion travels
with the numbers it judged.

Plan cell: `docs/research/03_POC_PLANS.md` (PoC-E9). Literature: Lamas 2024
(doi:10.1177/17479541231210979 — the one peer-reviewed BJJ Markov paper), the semi-Markov
critique (Sci Rep 2026, s41598-026-52938-1 — memorylessness is systematically violated in
match event streams), Decroos 2019 / Singh 2019 for the value-model limb.

---

## What is being tested

Four hypotheses of the repository owner's, in one runner:

1. **294 distinct labels are too sparse for a usable chain; the 8 event types are not.**
2. **A third state space — the 8 types with `control` exploded into positions of absolute
   dominance — beats both.**
3. **There is a maximum estimable Markov order, and it is different per state space.**
   PoC-E4 already CLOSED this at label level (2nd order LOSES, Δ logL/step −0.203
   [−0.258, −0.133]); the open question is order-k on the SMALL spaces.
4. **An ADCC-family kernel measurably diverges from the global kernel.**
5. **Entry into the "time expired / points" terminal is history-dependent** — it depends on
   previous steps, not only on the current state.

---

## Corpus, gate, split

* Source: `matches` where `status='final' AND sequence IS NOT NULL` — 864 bouts, 9 592
  events, 294 distinct normalised labels (measured 2026-08-25, read-only).
* Gate: `attribution.bout_flags(...)["perspective_reliable"]` AND ≥ 4 events — **MANDATORY**;
  43.9% of bouts file every event under one athlete. Measured: **429 bouts / 7 078 events
  pass**. The runner asserts this count against PoC-E8's published 429; a mismatch means the
  corpus moved and every number below is about a different corpus.
* Mirroring: corpus bouts enter the AUC limb TWICE (once per athlete perspective, E8's
  contract) and are **de-duplicated by key** (`e4.dedupe_by_key`) in every chain-based arm —
  forgetting that halves context support silently.
* Split: **temporal only** (ADR-03). Most recent 25% of gated bouts by
  `(year, created_at, id)`; bouts sharing the boundary key go to TRAIN. Never random.
  Measured shape: 322 train / 107 eval bouts.
* LGPD: athlete corpus only. No `owner_kind='user'` row enters any arm.
* Nothing here touches a production export.

## Chain definitions

* **C-own** (primary for arms 1–4) — one chain per actor per bout: that fighter's own
  ordered states, the succession `transitions/build_graph.network_from_sequences` builds its
  edges from and the definition PoC-E4's order probe used.
* **C-bout** (sensitivity for arms 1–3, **the only** chain for arm 5) — one chain per bout,
  every event in stored order regardless of actor. Actor-free by construction, therefore
  immune to the measurably refuted guard/pass actor convention (same actor on both sides of
  63.4% of close pairs — `analysis/attribution.py` lines 1–40). A terminal is a property of
  the bout, not of one fighter, which is why arm 5 uses only this one.
* **No repeat-folding, in any arm.** E4/E8 fold `A → A` because their *graph* builders refuse
  a self-edge. A Markov chain does not, dwell IS information (the semi-Markov critique's whole
  point), and folding makes the number of scored steps depend on the state space — which
  would destroy the step-for-step pairing the primary criterion needs. **Consequence: this
  runner's label-space numbers are NOT directly comparable to `e4.md`'s.** A folded,
  hard-backoff k=1 vs k=2 parity row is reported as the bridge.

## State spaces

| id | definition | states (train, measured) |
|---|---|---|
| **S-label** | `technique_match.clean_label(label, type)` | 226 |
| **S-cat** | the event `type`, restricted to `attribution.EVENT_TYPES` | 8 |
| **S-v3** | S-cat with `control` exploded into five dominance states | 12 |

S-v3's control partition is a **frozen label map**, seeded from the curated tables in
`analysis/attribution.py` (`_CONTROL_BACK` / `_CONTROL_TOP` / `_CONTROL_GRIP`, each with its
own measured provenance) and named with `docs/taxonomy.json` v2 control-child ids
(`back-control`, `top-control`, `pin`, `front-headlock-control`, `peripheral-control`).
Only 38 of 376 `technique_nodes` carry a `taxonomy_id`, so the map is explicit rather than
joined. It is **a function of labels only** and is fixed in code before any held-out number
exists; the five buckets and their measured event counts over the gated corpus:

| state | seed | gated events |
|---|---|---|
| `control/back-control` | back control, body triangle, body lock, seatbelt, truck, rear body lock, standing back control, arm-drag/crab-ride to back take, straight jacket | 1 641 |
| `control/mount` | mount, three-quarter mount, mounted crucifix | 335 |
| `control/pin` | side control, north–south (both spellings), knee on belly, scarf hold, crucifix, near fall, top control (and its half-guard variants), half nelson, leg lace, ride out, ground and pound, head-arm control top | 241 |
| `control/front-headlock` | front headlock | 95 |
| `control/peripheral` | everything else typed `control` — standing grips, leg entanglement controls, turtle, escape-to-turtle | 82 |

State names are **prefixed** (`cat/pass`, `v3/control/pin`) so that `clean_label` is the
identity on them; a bare `"pass"` canonicalises to `"Guard Pass"` and would silently
de-synchronise the graph builder from `Kernel.node_of`. Asserted by test.

## Estimator

Hierarchical interpolated backoff (additive-Dirichlet / Jelinek–Mercer form):

    p_j(z | ctx_j) = λ_j · f̂(z | ctx_j) + (1 − λ_j) · p_{j−1}(z | ctx_{j−1})
    λ_j = n(ctx_j) / (n(ctx_j) + α · V)

recursing down to the order-0 train marginal and finally to uniform `1/V`. `α` swept over
{0.1, 0.5, 1.0}; **headline α = 0.5** (E4's). An eval state never seen in train collapses
into a reserved `<unk>` symbol that the vocabulary already counts.

Interpolation, not E4's hard `MIN_CONTEXT=5` backoff, is the primary estimator here because
"maximum estimable order" must not be an artefact of one arbitrary pruning threshold. E4's
hard-backoff rule is still run, on the label space, as the parity check.

## Scored steps

Every chain index `t ≥ 3`, so k=1, k=2 and k=3 are scored on **identical** steps and no order
gets a head start on positions a lower order cannot see. Because repeats are not folded, the
step set is also identical **across state spaces** — the three arms are paired step-for-step.
Measured: 888 eval steps on C-own, 1 063 on C-bout.

---

## PRIMARY CRITERION (arms 1–3)

> Held-out mean log-likelihood of the **next event's category** — a fixed 9-symbol target
> alphabet (the 8 `EVENT_TYPES` plus `other`), identical for every arm. Each arm's next-state
> distribution is marginalised through the train-estimated `P(category | state)`. Interval:
> **bout-clustered** paired percentile bootstrap (`stats_rigor.bootstrap_ci`, seed 20260820,
> groups = bout key).
>
> * Arm **A beats arm B** iff the paired Δ interval excludes 0 in A's favour. Overlapping
>   intervals = no win.
> * Order **k+1 is estimable and worth it** for a state space iff its paired Δ against order k
>   excludes 0 in k+1's favour at the headline α. The **maximum estimable order** is the
>   largest such k, reported beside per-context support coverage.
> * An arm that does not beat **its own order-0 marginal** by the same test is reported as
>   carrying no usable memory, whatever its absolute likelihood.

Why a common target and not each space's own likelihood: per-step log-likelihood is a
function of vocabulary size. An 8-state chain scores ≈ −2.08 at uniform where a 226-state
chain scores ≈ −5.42; comparing those directly would "prove" the coarse space wins before
any data is read. Projecting every arm onto one fixed 9-symbol outcome is the only way the
comparison means anything. **This is the load-bearing methodological decision of the PoC.**

The projection is estimated, not assumed: `P(c | s)` comes from train counts with the same
additive smoothing over the 9 categories, for every arm including S-cat (where it is
near-degenerate). One code path, no special-casing, and it absorbs the 3.28% of events whose
`clean_label` appears under more than one `type` (measured: 34 such labels, 315 events).

## SECONDARY CRITERION (arms 1–3)

> Paired ΔAUC on held-out finish prediction — the PoC-E8/E4 harness verbatim
> (`evaluate_kernels`; label = a landed submission by the same actor within the next k=5
> events; PtV γ=0.8), bout-clustered. **Shaping is OFF for all three arms**, so the
> positional prior — which reads the state *name* — cannot reward one vocabulary over
> another. E4 measured shaping to be a wash at γ=0.8 (paired Δ +0.007 [−0.017, +0.031]), so
> this costs nothing and removes a confound.

## ARM 4 — the ADCC kernel

Family from `Match.event`: `adcc` iff the tag upper-cases to a string starting `ADCC`, else
`other`. Measured: 152 bouts total, **56 gated** (720 events, 70 athletes) across `ADCC 2022`
(54), `ADCC 2024` (42), `ADCC Trials 2023 East Coast` (19), `ADCC` (18), `ADCC Trials 2024
West Coast` (8), `ADCC Trials 2022 South America` (7), `ADCC World Championship` (3),
`ADCC WC Trials` (1). `analysis/scouting_rulesets.FAMILIES` is not joined to `matches`, so
this prefix rule is the mapping; it is stated here rather than inferred.

* **4a — held-out limb.** On ADCC eval bouts only: an ADCC-trained chain vs the
  global-trained chain, primary criterion, at the winning state space and order.
  **Power gate, pre-registered: ≥ 10 eval bouts AND ≥ 100 scored eval steps, and
  `stats_rigor.coverage` over contributing bouts must return `estimable`.** Below either,
  the arm reports **UNDERPOWERED** and NO verdict. Measured shape going in: gated ADCC bouts
  by year are 2019=2, 2022=31, 2023=1, 2024=22 — this gate is expected to bind, and saying so
  now is the point of pre-registering it.
* **4b — divergence limb** (descriptive, whole gated corpus, no split). Per state, the
  Jensen–Shannon divergence between the ADCC and non-ADCC outgoing distributions, with a
  **bout-level permutation p** (family label shuffled across bouts, 2 000 draws, seed
  20260820) and `stats_rigor.benjamini_hochberg` q across the state family. Every row gated
  on `stats_rigor.coverage` over contributing bouts; below the gate the row prints counts and
  **no interval** — never a wider one.

The verdict rides on 4a. 4b answers *where* the kernel differs and is reported beside the
criterion, never inside it.

## ARM 5 — history-dependent absorption

Terminal alphabet from `Match.win_type`:

| terminal | win_type | gated bouts |
|---|---|---|
| `END/submission` | `SUBMISSION` | 181 |
| `END/points` | `DECISION` ∪ `POINTS` | 213 |
| `END/draw` | `DRAW` | 34 |

`POINTS` is undercounted (12 corpus-wide); `DECISION` on this corpus overwhelmingly means a
points-limit ending, which is why the two are one terminal and why the merge is declared
here rather than discovered later. `win_type` NULL → the bout is **dropped** from this arm.
Never defaulted.

Chain = C-bout. States = S-cat and S-v3, both reported.

* **T1 — history depth.** Held-out log-likelihood of the observed terminal under
  `M0 = P(terminal)` (train marginal), `M1 = P(terminal | last state)`,
  `M2 = P(terminal | last 2 states)`, `M3 = P(terminal | last 3)`, all with the same
  interpolated backoff and α. **One observation per eval bout**, so the bootstrap over bouts
  IS the cluster bootstrap. **Absorption is history-dependent iff M1 beats M0 with a Δ
  interval excluding 0; it is DEEP-history-dependent iff M2 or M3 beats M1 the same way.**
* **T2 — step-index hazard.** Discrete-time competing-risks hazard
  `h_τ(bin) = P(absorb into τ at the next step | still running)`, binned by step index
  {1–4, 5–8, 9–12, 13–20, 21+}. `stats_rigor.wilson` per cell; `heterogeneity` over the
  bin × {points, submission, still-running} table (permutation p when min expected < 5);
  `compare_proportions` first bin vs last. **Increasing iff the last bin's hazard exceeds the
  first bin's with an Agresti-Caffo difference interval excluding 0.**
* **T3 — elapsed-time hazard, AA-010 safe.** T2 with the x-axis `ts − min(ts)` **within the
  bout**. A within-bout difference is invariant to `ts_origin`: an unknown origin is an
  additive per-bout offset, so the difference is valid under `video_absolute` and
  `bout_relative` alike, and **no missing `ts` is ever defaulted**. Restricted to bouts
  carrying `ts` on EVERY event — measured **381 of 429 gated**; the excluded 48 are counted
  in the report. Bins {0–2, 2–4, 4–6, 6–8, 8–10, >10 min}.
  **Stated limit, before the numbers:** the last event's timestamp is a *lower bound* on the
  bout's duration, so T3 measures the hazard of "no further recorded event", not of the final
  bell. Measured medians going in: span 612 s for `DECISION`, 423 s for `SUBMISSION`.

## Sensitivity analyses — reported, never a criterion

1. Chain = **C-bout** for arms 1–3 (the actor-free reading).
2. **Grappling-only**: `discipline.match_discipline(event) == 'grappling'` — measured 342 of
   429 gated bouts, with 70 mma-tagged and 17 NCAA-tagged in the primary corpus because E4
   and E8 kept them. Consistency with the closed cells wins the primary; the exclusion is
   reported.
3. α ∈ {0.1, 1.0} beside the headline 0.5.
4. The folded, hard-backoff (`MIN_CONTEXT=5`) k=1 vs k=2 parity row on S-label, against
   `e4.md`'s published Δ −0.203 [−0.258, −0.133].

## Data hazards that bound every number in this cell

* `successful` is present on **28.9%** of events (2 774 of 9 592); absent reads as `False`,
  so the finish label's positives are undercounted. Affects the SECONDARY criterion only.
* **43.9%** of corpus bouts are one-sided; the gate removes them but cannot repair actor
  noise inside the ones it keeps.
* Event objects carry five keys only: `label`, `type`, `actor_id`, `successful`, `ts`.
* `ts` is present on 94.7% of events but `ts_origin` is NULL for 592 of 864 matches — which
  is exactly why T3 uses within-bout differences and nothing else.
* `back control` is 1 952 events, 61.8% of all `control` and 20.3% of the entire corpus. The
  S-v3 explosion therefore produces one very large state and four small ones; that shape is a
  property of the corpus, and a v3 win must be read against it.
* The held-out window is right-censored at each chain's end, identically for every arm.

## Amendments (recorded before the corpus run, found by the synthetic fixtures)

Both were discovered by `tests/test_poc_e9.py` on synthetic corpora with a known answer,
**before `analysis/poc/e9_markov.py` was pointed at the database**. They tighten the
criterion; neither relaxes it.

1. **A degenerate interval is never a win.** `wins()` additionally requires `hi > lo`. A
   bootstrap over bouts that returns the same value in every draw saw no bout-to-bout
   variation, which is what happens when two models differ by a *constant offset* rather than
   by what they predict — the signature of one model being smoothed less than the other
   (nested interpolation shrinks once per level, so a higher order recovers prior mass an
   unobserved symbol was holding). Measured on a synthetic corpus with five uninformative
   contexts: Δ +0.0051 at width 0, which the original rule would have published as evidence.
2. **A single context can never be called history-dependent.** T1's verdict additionally
   requires the order-k terminal model to have more than one distinct train context (and,
   for DEEP history, more contexts at order k than at order 1). An order-k model whose
   context is identical for every bout is an order-0 model wearing a different smoothing.

Two further amendments, made after the *design* diagnostics of the first corpus pass and
**before any ADCC likelihood or any revised verdict was read**:

3. **Arm 4a uses an ADCC-internal temporal split.** The corpus-wide split puts **zero** ADCC
   bouts in the held-out window — the corpus' most recent quarter is 2025–2026 and every
   gated ADCC bout is 2019–2024 — so the power gate refused the arm on an empty set: a true
   statement about nothing. The ADCC subcorpus is therefore split on its own timeline (same
   rule: train ≤ T, evaluate T+1, boundary to train, no randomness), and the global kernel is
   trained on **every corpus bout ≤ that same boundary**, so both kernels see the same past
   and the contest is about the kernel rather than about how much data each one was given.
   The power gate itself is unchanged and still binds.
4. **The hazard trend is also reported against the last INTERIOR bin.** The final bin of both
   axes is open-ended, so every bout that reaches it must absorb inside it and its hazard is
   inflated by construction. The pre-registered first-vs-last contrast is reported exactly as
   written; a first-vs-last-interior contrast is reported beside it, and a trend claim resting
   only on the open-ended cell is not one this cell makes.

## Verdict format

One ACCEPT/REJECT per arm, decided by the criterion above and nothing else. A null result is
a publishable outcome and is recorded as one. No production export, engine or site artefact
changes on the strength of this cell; anything it accepts becomes a separate, gated decision.
