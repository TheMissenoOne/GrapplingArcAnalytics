# PoC-E11 — the high-confidence action chain: does data quality beat data quantity?

**STATUS: PRE-REGISTRATION ONLY. No corpus evaluation has been run against this document.**
Written before `analysis/poc/e11_action_chain.py` produced a single held-out number; the
runner re-emits every line of this section verbatim above its results, so the criterion
travels with the numbers it judged.

Plan cell: `docs/research/03_POC_PLANS.md` (PoC-E11). Literature: Lamas 2024
(doi:10.1177/17479541231210979) for the twelve-action state space, already implemented in
`analysis/lamas_chain.py`; the semi-Markov critique (Sci Rep 2026) for why dwell must not be
folded; Decroos 2019 for the held-out-evaluation framing. **Inherits:** PoC-E9's
`Backoff`/`fit_backoff`/`score_order`/`paired_delta`/`wins`/`_boot_ci` (the fixed-target
estimator), PoC-E8's `temporal_split`, `stats_rigor` for every interval.

---

## What is being tested

The owner's question is *"train on part of the corpus, predict the rest — and does training
only on the trustworthy part beat training on everything?"*, asked in the twelve-action space
Lamas 2024 defines and this repo already implements.

1. **Is the action chain predictive at all?** Does an order-1 chain beat its own order-0
   train marginal on held-out bouts? Nothing else in this cell means anything if it does not.
2. **Quality vs quantity.** Does a high-confidence training subset predict held-out bouts
   better than the full corpus does — at equal evaluation, and at equal training size?
3. **Which context vocabulary.** At a fixed training set, does the twelve-action space beat
   the seven-family collapse, the eight event types, or the raw label?
4. **How much data is enough to measure?** Where does held-out prediction stabilise as the
   training window grows?
5. **What the house graph techniques say at action level** — PageRank, reward-risk,
   communities — against what they say at label level. Descriptive.

---

## The methodological crux, and why it decides the target alphabet

PoC-E9 established that **per-step log-likelihood is a function of vocabulary size**, so every
arm must be scored on ONE fixed target symbol set, reached by marginalising each arm's
next-state distribution through a train-estimated `P(target | state)`.

This cell has a second, sharper version of the same trap, and it is **measured, not feared**.
The twelve-action space splits every family into attempt and success off the `successful`
flag, and `successful` is annotated per BATCH, not per corpus. Measured read-only on
2026-08-25, over the gated training window:

| family | success share, FULL train | success share, HC-B train |
|---|---|---|
| SWPA→SWP | 15.3% | 100.0% |
| TKDA→TKD | 24.2% | 56.9% |
| GPSA→GPS | 13.0% | 33.3% |
| BTKA→BTK | 4.2% | 88.0% |
| SUBA→SUB | 16.9% | 55.2% |

A model trained on the high-confidence subset and scored on a twelve-symbol target would lose
by a landslide **because the two sets are annotated under different conventions**, not because
one is better data. That is PoC-E9's vocabulary trap wearing a different hat, and publishing it
as a quality finding would be a fabrication.

**Therefore the PRIMARY target is the seven-symbol, annotation-invariant family alphabet**

```
CDP · PGD · SWP* · TKD* · GPS* · BTK* · SUB*
```

where `X*` collapses `XA` and `X`. It is a function of the event's `type` and `label` only —
never of `successful` — so it means the same thing in every batch. The twelve-symbol target is
run as an explicitly-labelled SECONDARY and every verdict on it carries the batch caveat.

Three further decisions, stated because each is contestable:

* **Repeats are NOT folded.** PoC-E9's rule, for PoC-E9's reason, plus Lamas' own: the paper
  publishes guard pass → guard pass = 0.30, a cell that folding deletes.
* **Order is fixed at 1.** PoC-E4 closed second order at label level (Δ −0.203
  [−0.258, −0.133]) and PoC-E9 measured the maximum estimable order as 1 for both coarse
  spaces. Re-litigating it here would be re-running a closed cell. Order 1 is also the order
  Lamas 2024 publishes. The order-0 train marginal is kept as the mandatory floor (C1).
* **Scored positions start at index 1**, not PoC-E9's 3. The median gated action chain is six
  steps long; a floor of 3 discards a third of the corpus for an order sweep this cell does not
  run. Every arm uses the same floor, so every Δ stays paired step-for-step.

---

## Corpus, gates, split

Source: `matches` where `status='final' AND sequence IS NOT NULL`, athlete corpus only. Read
only; nothing in this cell touches a production export, and no `owner_kind='user'` row exists
in `matches` to reach.

**Design-time sizing, measured read-only 2026-08-25** (counts only — no outcome was computed):
909 final matches with a sequence · 599 with ≥ 4 events · **466 pass**
`attribution.bout_flags(...)["perspective_reliable"]` · 4 763 mapped action steps · 70.7% of
gated events carry a Lamas action.

> **The corpus moved under PoC-E8/E9.** Those cells published 429 gated bouts; today the same
> gate returns 466 (the 2026-08-25 concordance-audit and archived-reading imports). The runner
> reports both numbers and does not silently inherit the old one.

### "High confidence", defined measurably — two named limbs, both pre-registered

There is no `source` column on `matches`; provenance is not stored per bout. So high
confidence is defined by properties of the row that can be computed, and each definition is
reported with its subset size and the `event` tags it selects, so a reader can see what it
actually picked.

* **HC-A — attribution quality (PRIMARY).** `perspective_reliable` **and** the mapped action
  chain names ≥ 2 distinct `actor_id`s **and** chain length ≥ 4. This is exactly the double
  refusal `analysis/lamas_chain._actor_reliability` already applies before the reward-risk
  layer: `one_sided` is the corpus's own verdict, `single_actor` closes the hole it leaves on
  short bouts. Design-time size: **232 of 350 train bouts, 3 412 action steps.**
* **HC-B — annotation completeness (SECONDARY, power-gated).** HC-A **and** `successful`
  present on ≥ 90% of the bout's events. This is the machine-checkable proxy for "came through
  a frame-read or concordance-audited pipeline", since those pipelines annotate every event and
  the transcript pipelines do not. Design-time size: **27 of 350 train bouts, 208 action
  steps** — and the selected tags are almost entirely ADCC Trials 2023/2024, which means
  **HC-B is confounded with event family and with an era**. Any HC-B result is a statement
  about those batches, not about "audited data" in general, and the report says so.

### Split

Temporal only (ADR-03). Gated bouts sorted by `(year, created_at, id)`; the most recent 25% is
the eval set, bouts sharing the boundary key go to TRAIN. Design-time shape: **350 train / 116
eval bouts, 746 scored eval steps across 97 contributing bouts, boundary at 2025.**

**The eval set is ONE fixed set of rows for every arm** — all gated bouts in the eval window,
high-confidence or not. That is the deployment question ("predict whatever comes next, warts
and all"), and it is what makes every Δ paired. The sensitivity that restricts eval to
high-confidence bouts is pre-registered as REFUSED when fewer than 10 such bouts land in the
eval window; design-time it is 57 for HC-A (runnable) and **1 for HC-B (refused)**.

---

## Criteria — pre-registered, verbatim

Every interval is a **bout-clustered paired percentile bootstrap** (2 000 draws, seed 20260820,
`stats_rigor`'s seed). "A beats B" means the paired Δ (A − B) excludes 0 in A's favour **and
the interval is non-degenerate** — PoC-E9's `wins`, including its zero-width guard, which
exists because a constant smoothing offset can otherwise be published as evidence.

**C1 · Is the chain predictive at all?** ACCEPT iff the order-1 chain in the seven-family
context, trained on FULL, beats the order-0 train marginal (M0) on the fixed eval set.
*If C1 fails, every arm below is descriptive and no verdict about training sets is issued.*

**C2 · Quality vs quantity, unmatched — the practical question.** Three pre-declared outcomes
per high-confidence set H ∈ {HC-A, HC-B}, from Δ(H − FULL):
* **H WINS** — interval excludes 0 above. Training on the trustworthy subset alone is better.
* **FULL WINS** — interval excludes 0 below. Quantity beats quality at these sizes.
* **INDISTINGUISHABLE** — interval covers 0. The reported quantity is then the interval's
  half-width in nats/step, so the reader sees the resolution rather than a bare null.

**C3 · Quality vs quantity, SIZE-MATCHED — the science question.** C2 confounds quality with
sample size; PoC-E9's ADCC arm already showed a specialised kernel losing purely on variance
(56 training bouts against ~370). So for each H, FULL is subsampled **at bout level, without
replacement, to exactly |H| bouts, R = 20 seeded draws**, and each draw's per-step log-
probabilities are averaged into one paired vector. ACCEPT "quality carries information beyond
sample size" iff Δ(H − size-matched FULL) excludes 0 in H's favour. REJECT if it excludes 0
below. INDISTINGUISHABLE otherwise.

**C4 · Context vocabulary.** At FULL training and order 1, four context spaces are scored on
the same fixed family target: **A-12** (Lamas' twelve actions), **A-7** (the family collapse),
**S-type** (the underlying event's `type`), **S-label** (`clean_label` of the underlying
event). Report the undominated set. The owner's specific hypothesis is tested as a named row:
**ACCEPT "the attempt/success split earns its place" iff Δ(A-12 − A-7) excludes 0 in A-12's
favour.** Pre-declared reading if it does: the flag is informative *where it is present*, which
is a statement about annotation coverage, not a demonstration that landing a technique matters.

**C5 · Enough to measure.** Two sweeps over training-set size, both scored on the same fixed
eval rows in the A-7 context: (a) **recency-nested** — the most recent n train bouts,
deterministic; (b) **random subsample** — n bouts drawn without replacement, R = 20 seeds.
Grid n ∈ {10, 20, 27, 40, 60, 90, 130, 175, 232, 290, all}, which contains |HC-B| = 27 and
|HC-A| = 232 exactly so the equivalent-sample-size can be read straight off the curve.
**Stabilisation point n\*** = the smallest n on the grid such that Δ(n − FULL) covers 0 **and**
|point estimate| < 0.010 nats/step, **and the same holds for every larger n on the grid**. If
no n below 100% satisfies it, the reported answer is "not stabilised within this corpus".

**C6 · The graph layer — descriptive, no accept/reject, three honesty gates.**
* Communities are published only alongside observed modularity Q, bootstrap-over-bouts
  mean/p10 Jaccard (`constellations/stability`'s instrument, resampling BOUTS), and graph
  density. **If density > 0.9 the verdict line states that community structure on a
  near-complete twelve-node graph is not interpretable regardless of Q.** Design-time density
  is 0.977, so this is expected to fire.
* PageRank ties break by `(-score, state name)` — the deterministic tie-break scar. PageRank
  is reported on the twelve-node graph **and** on the seven-node family graph, because the
  twelve-node ranking is partly a function of the `successful` annotation policy and the
  seven-node one is not.
* The action-level ranking is compared to the shipped label-level ActionFlow graph
  (`transitions/build_graph.network_from_sequences` + `network_metrics`) by mapping each label
  to its Lamas family and aggregating PageRank mass, then Spearman with its interval. Agreement
  is corroboration; disagreement is the finding.

**Power gates.** The HC-B limb is reported **UNDERPOWERED, no verdict** if its training set has
< 20 bouts or < 150 action steps or `stats_rigor.coverage` refuses. The HC-restricted-eval
sensitivity is REFUSED with its count below 10 eval bouts.

**Null results are the expected outcome and are published as-is.** Nothing in this cell can
change a production export; the only change that could follow an accept — training the shipped
kernel on a subset — is a separate decision that would need its own cell.
