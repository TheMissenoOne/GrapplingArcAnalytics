# PoC-E13 — GraphSAGE: inductive link prediction on athlete ActionFlow graphs

**STATUS: PRE-REGISTRATION ONLY. No held-out number has been produced against this
document.** Written before `analysis/poc/e13_graphsage.py` scored a single candidate pair;
the runner re-emits this section verbatim above its results, so the criterion travels with
the numbers it judged (the E8/E9 convention).

Plan cell: `docs/research/03_POC_PLANS.md` (PoC-E13). Literature: Hamilton, Ying &
Leskovec 2017 (arXiv:1706.02216 — GraphSAGE, and specifically its PPI *multi-graph*
protocol: train on some graphs, generalise to completely unseen ones); Zhang & Chen 2018
(arXiv:1802.09691 — why a node-embedding GNN with a dot decoder is the *weak* form of GNN
link prediction, and what it can and cannot represent); Ma et al. 2024/2025
(arXiv:2411.03845 — a well-tuned trivial model matches sophisticated ones, so the baselines
decide the verdict); Kumar et al. 2025 (doi:10.1007/s11227-025-07882-8 — the current
link-prediction survey's recommendation to run calibrated heuristic baselines).

---

## The claim being tested

Everything vector-shaped in this repository today is **transductive**: `technique_nodes.
embedding` is one fixed 768-d mpnet vector per canonical label, and a graph's embedding is
a weighted mean of them (`analysis/embeddings.py`). Two consequences: the vector of a
position is the same for every athlete who plays it, and a node the library has never seen
has no vector at all.

GraphSAGE's claim is that an *aggregation function* — not a per-node lookup table — can be
learned on some graphs and applied to graphs never seen in training. Stated for this
corpus:

> **H1 (inductive).** A GraphSAGE encoder trained on the ActionFlow graphs of one set of
> athletes predicts held-out transitions in the graphs of *different* athletes it never
> saw, better than the corpus-wide transition prior, than target popularity, than
> label-text cosine similarity (the existing pgvector system's notion of relatedness),
> than the classical structural heuristics, and than the same decoder with the
> neighbourhood aggregation switched off.

The last two comparisons carry the weight. Beating the corpus prior is the claim that an
athlete's *own* observed game changes which transitions they will show. Beating the
no-aggregation ablation is the claim that **message passing** — not the input features
already available today — is what produced the difference. Anything that beats neither is
an expensive re-description of `analysis/embeddings.py`.

## Corpus, gate, LGPD

* Source: `matches` where `status='final' AND sequence IS NOT NULL`. Read-only, one
  `SELECT`. **Athlete corpus only** — no `owner_kind='user'` row, no `user_sessions`, no
  App-fed data enters any arm, and nothing here writes a vector, a row or an export.
* Gate: `attribution.bout_flags(...)["perspective_reliable"]` AND ≥ 4 events, i.e. the
  identical filter PoC-E8 and PoC-E9 use. 43.9% of bouts file every event under one
  athlete; ungated, an athlete graph measures the ingest batch.
* Corpus shape, measured read-only 2026-08-25 **before this document was written** (shape
  only — no score, no AUC, no arm was run): 909 bouts with a sequence, **466 pass the
  gate**, 526 athletes appear in at least one gated bout, 270 distinct canonical labels,
  240 of them (88.9%) carry a `technique_nodes.embedding`.
  *PoC-E9 published 429 gated bouts on 2026-08-25; this cell measures 466. The corpus moved
  between the two runs. The runner prints its own gate count and does not assert E9's.*

## The graphs

One graph per athlete, built by `transitions/build_graph.network_from_sequences` from
**that athlete's own events only** (`actor_id == athlete`), which is exactly the
within-actor ActionFlow definition — their own ordered flow, one bout at a time, no
cross-fighter edge. Nodes are `technique_match.clean_label` canonical labels; edges are
directed, weighted by count.

Consequence, stated because it costs a feature: `reward`/`risk` are **not** used as node
features. `risk` is defined against the *opponent's* next event and is structurally 0 in a
single-actor sequence, and `reward`/`ok_rate` are event-derived quantities that cannot be
partitioned by edge, so under Arm A's edge holdout they would leak held-out information
into the observed graph. The feature set below is the leak-free subset.

## Node features (the marriage with the existing pgvector system)

`x_v = [ e_v (768) ‖ log1p(w_in) , log1p(w_out) , log1p(w_in + w_out) ]` → **771-d**.

* `e_v` = **the production vector**: `technique_nodes.embedding` for
  `names._normalize_name(label)`, read from the DB. A label with no embedded library row
  gets the mean of the present vectors (a neutral "unknown position"), identically for
  GraphSAGE and for the text baseline. Deliberately no on-the-fly encoding: the PoC then
  measures what production could deploy today, on the vectors production actually holds.
* The three structural terms are computed **from the observed subgraph only**, never from
  the full graph.

## Task, split, candidate set

**Athlete-level split (the inductive axis, and it is temporal too).** Eligible athletes are
those with ≥ `MIN_EDGES` = 10 distinct directed edges. They are ordered by their **debut
key** — the `(year, created_at, id)` of their first gated bout — and the latest-debuting
25% become EVAL. Train and eval athlete sets are disjoint by construction; a validation
slice (the latest-debuting 20% of TRAIN) is used for early stopping and nothing else.

**Bout-level quarantine.** Any gated bout in which an EVAL athlete participates is removed
from the training corpus entirely — from the training graphs *and* from the corpus-prior
baseline. Two athletes in one bout are not independent (one's guard is the other's pass),
so athlete-disjointness alone would leave a shared-bout channel open. A test asserts the
quarantine holds.

**Within-graph holdout — two arms, both pre-registered:**

* **Arm A (PRIMARY) — edge holdout.** Each athlete's distinct directed edges are sorted
  canonically and a seeded RNG marks `HOLDOUT_FRACTION` = 0.30 of them held out. The
  *observed graph* is rebuilt **from the observed edges alone** (node set = their
  endpoints, weights = observed counts), so no held-out edge touches message passing or
  any feature. This is Hamilton's multi-graph protocol: unseen graphs, partially observed.
* **Arm B (SECONDARY) — chronological bout holdout.** For athletes with ≥ 2 gated bouts,
  the most recent 30% of their bouts (by bout key, boundary to observed) are held out; the
  observed graph is built from their earlier bouts only, and the positives are transitions
  appearing in the later bouts that the earlier graph does not contain. This is the arm
  that satisfies the house temporal rule literally and answers the product question ("given
  the bouts we have ingested for this new athlete, what will they show next"); it is
  secondary only because it is thinner.

**Candidate set (identical for every method — the comparison is paired).** All ordered
pairs `(u, v)`, `u ≠ v`, with **both endpoints in the observed node set** and `(u, v)` not
an observed edge. Positive iff `(u, v)` is a held-out edge. No negative sampling: every
non-edge is scored, so the row set is deterministic and no method gets a luckier draw.
Held-out edges with an endpoint outside the observed node set are **excluded and counted**
— a structural method cannot score them at all, and letting the text method win on rows
its rivals cannot see would measure the exclusion rather than the model.

## Methods scored on those rows

| id | score for pair (u,v) | fitted on |
|---|---|---|
| `prior` | count of `u → v` in the TRAIN corpus (quarantined) | train bouts |
| `popularity` | TRAIN-corpus occurrence count of `v` | train bouts |
| `text` | cosine of the two production mpnet vectors | nothing (transductive baseline) |
| `adamic_adar` | Adamic–Adar over the observed graph, undirected | that athlete's observed graph |
| `pref_attach` | `out_deg(u) · in_deg(v)` on the observed graph | that athlete's observed graph |
| `mlp` | same decoder, **0 aggregation layers** (features only) | train athletes' graphs |
| `sage` | 2-layer GraphSAGE mean aggregator, directed (self ‖ mean of out-neighbours ‖ mean of in-neighbours), L2-normalised per layer, dot decoder over separate source/target projections | train athletes' graphs |

Hyperparameters are **fixed a priori, not swept**: hidden 64, output 32, 2 layers, Adam
lr 0.01, weight decay 1e-4, ≤ 300 epochs, early stopping on validation-athlete AUC with
patience 30, class-weighted BCE over all candidate pairs, seed 20260825, single-threaded
CPU. `mlp` is the same code with `layers=0` and the same budget.

## Criterion (verbatim; nothing below is negotiable after the first number)

**Primary metric.** AUC over the pooled EVAL candidate rows — the probability that a true
held-out transition is ranked above a never-observed pair — with an **athlete-clustered**
percentile bootstrap interval (cluster = athlete; 4000 draws, seed 20260825). Rows inside
one athlete's graph are not independent, so a row-level interval is anti-conservative and
is not the one that decides anything.

**Comparisons.** For each baseline, the **paired** ΔAUC (`sage` − baseline) on exactly the
same rows, athlete-clustered, 2000 draws. A method beats another **iff the paired interval
excludes 0**. Overlapping intervals are a null, and a null is a result.

**Verdict, Arm A:**

* **ACCEPT** — `sage` beats **all six** comparators.
* **PARTIAL** — `sage` beats **both** `prior` **and** `mlp`, but not all six.
* **REJECT** — `sage` fails to beat `prior`, or fails to beat `mlp`, or both. In that case
  the null is published as the outcome and no GraphSAGE artefact reaches production.

Arm B is reported with the same table and the same rule, labelled SECONDARY. Arm A decides.

**Power gate (checked before any verdict is read).** An arm is UNDERPOWERED — and gets no
verdict at all — unless it has ≥ 8 eval athletes carrying ≥ 1 positive, ≥ 40 positives in
total, and `stats_rigor.coverage` over the per-athlete positive counts returns
`estimable = True`. UNDERPOWERED is a publishable outcome and is not a failure of the
model; it is a statement about the corpus.

**Descriptives reported beside the criterion, deciding nothing:** Hits@20 and MRR per
method, the count and share of held-out edges dropped for having an unseen endpoint, the
embedding-coverage share, per-graph sizes, and the epoch early stopping selected.

## Secondary probe (report-only, no accept criterion)

Do GraphSAGE node embeddings organise techniques differently from the text embeddings?
Eval-graph node embeddings are averaged per canonical label, k-means at k = 8 (the number
of `attribution.EVENT_TYPES`) is fitted on both spaces with a fixed seed, and two numbers
are reported: adjusted mutual information against the event type, and mean adjusted Rand
index across athlete-bootstrap resamples (stability). **Neither number can accept or
reject anything** — ADR-03's rule is that structure that looks better is not evidence, and
this probe exists to say what changed, not to argue that it improved.

## What this PoC deliberately does NOT do

* No `torch-geometric`. Measured: `torch` 2.12 is already installed (4.0 GB with its CUDA
  wheels) as a **transitive dependency of the core `sentence-transformers`**, so using it
  costs 0 bytes; PyG's sampling machinery buys nothing on graphs of ≤ 60 nodes, where a
  dense normalised adjacency matmul *is* the mean aggregator in three lines.
* No labelling trick / enclosing-subgraph model (SEAL). Zhang & Chen 2018 is cited
  precisely because it predicts that the node-embedding-plus-dot-decoder form tested here
  is the weaker one. If Arm A rejects, "we tested the weak form" is the honest reading and
  the recorded next action, not a retro-fitted excuse.
* No hyperparameter sweep, no architecture search, no alternative aggregators. One
  pre-registered configuration; a sweep after seeing the eval numbers would invalidate the
  interval this document is built on.
* No production change of any kind, under any verdict. `technique_nodes.embedding`,
  `graphs.embedding` and every export stay exactly as they are.
