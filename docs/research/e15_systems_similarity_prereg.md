# PoC-E15 — "grapples most like" at the SYSTEM level: pre-registration

Written 2026-09-11, **before any arm was scored**. Everything below was fixed against the
corpus's MARGINALS only (bout counts, cohort sizes, embedding coverage, systems per half) —
read-only counts that say nothing about which method wins. Results are appended to this same
file, below the pre-registration, by
`uv run python -m scripts.research.e15_systems_similarity`.

Owner decision being tested (2026-09-09): *"grapples most like" should compare SYSTEMS
(constellations/communities) rather than the whole athlete graph; metric and presentation
still to define.*

---

## 1. What actually ships today (traced, not assumed)

**Production already compares systems.** The path is:

```
export/site_data.py:1005   build_system_profile(athlete.name, from_career_graphview(...limit=12))
export/site_data.py:1044   compare_profiles(sp, all_profiles, k=5)
  → analysis/athlete_systems.py:match_systems      greedy best-match over the two athletes' systems
    → analysis/athlete_systems.py:system_similarity   the per-pair score
      → detect_athlete_systems → network_metrics.detect_communities (greedy modularity)
export/site_data.py:1819   rendered as   <chip>Name <span class="sim">63%</span></chip>
```

So the owner's decision is **already half-implemented**, and re-stating it as "compare
systems instead of the whole graph" would be a no-op. The thing that is NOT implemented is
the half that matters, and naming it is this cell's whole contribution:

`system_similarity` scores a pair of systems as

```
0.50 · cosine(8-dim TYPE-SHARE vector) + 0.20 · (same hub TYPE) + 0.15 · (size closeness) + 0.15 · (ELO closeness)
```

**Not one of those four terms looks at WHICH techniques are in the system.** Two athletes
whose systems are each "60% guard / 40% sweep" score 1.0 on the primary term whether one is
a berimbolo player and the other a spider-guard player. 0.35 of the weight (hub *type* +
size) is explicitly coarse-type or size. That is a mechanism, not a suspicion, for PoC-E5's
finding that the shipped percentage is statistically indistinguishable from a three-number
size descriptor (`docs/research/poc/e5.md`: production MRR 0.250 vs size-null 0.269, paired
Δ +0.019 [−0.125, +0.163]).

**E15's real question is therefore not "systems vs whole graph" — it is "does putting
technique IDENTITY into the system-level score buy anything over the size descriptor, and
does the system decomposition buy anything over plain shared-technique overlap?"**

## 2. Inheritance from PoC-E5 — what is reused verbatim, what is new

Reused **unmodified** (importing, not restating — the numbers must stay comparable):

| Thing | Source |
|---|---|
| corpus + gate | `analysis/poc/e9_markov.load_corpus` (`status='final' AND sequence IS NOT NULL`, ≥4 events, `attribution.bout_flags(...)['perspective_reliable']`) |
| athlete bouts, half-graphs, cohort gate | `analysis/poc/e5_grapple_like.{athlete_bouts,split_halves,build_cohort}` (≥3 nodes, ≥2 edges per half) |
| graph builder | `analysis/transitions/build_graph.network_from_sequences`, own events only |
| detector | `analysis/athlete_systems.detect_athlete_systems` (via `build_system_profile`) — production's |
| retrieval scoring | `analysis/poc/signatures.retrieval` (pessimistic ties, NaN last) |
| paired bootstrap | `analysis/poc/e5_grapple_like._paired_ci` → `stats_rigor.bootstrap_ci`, **seed 20260820, 4 000 draws**, resampling unit = ATHLETE |
| win rule | `e5_grapple_like.wins` — interval strictly above 0 AND non-degenerate (`hi > lo`), PoC-E9's amendment |
| nulls | `signatures.size_signature`, `signatures.degree_signature` |
| embeddings | `e5_grapple_like.load_embeddings` — `technique_nodes.embedding`, all-mpnet-base-v2, 768-dim |
| set overlap | `analysis/constellations/compare.jaccard` |

**New code in this cell, and only this**: a scorer-injected greedy matcher (production's
pairing loop with the per-pair score as a parameter), two per-pair scorers (member Jaccard,
per-system mpnet centroid cosine), a size-weighted aggregate, and the runner. No production
module is edited. Nothing is written to the database.

### The detector is held FIXED, deliberately

`docs/rating_v2/05_COMPARACAO_DETECTORES.md` (waves 6/6b/6c, ADR-08 closed 2026-08-19)
measured `constellations.detect` (Louvain + connectivity guarantee) against
`athlete_systems`' greedy modularity on the same input: **statistical tie on stability
(16/27, Δ +0.0120, CI [−0.0068, +0.0301], sign p = 0.44), tie on coverage (100% vs 100%),
aggregate Jaccard 0.773 between the two partitions.** Swapping detectors here would add a
variable that has already been measured as inert and would confound "different scoring" with
"different partition". **Every arm in this cell runs on the production detector.** The single
variable under test is how two systems are SCORED.

## 3. Corpus, gate, cohort — marginals measured read-only 2026-09-11

* **465 gated bouts of 911** final+sequence (310 under 4 events, 136 dropped as one-sided).
  PoC-E5 published 466 of 909; PoC-E8/E9 published 429. **Corpus drift is expected and
  reported** — these numbers are not directly comparable to those cells'.
* **39 athletes** at the ≥4-bout floor (E5 had 38).
* Cohorts after the ≥3-node/≥2-edge half gate:

| floor | split | cohort | chance MRR |
|---|---|---|---|
| ≥4 | **chronological (PRIMARY)** | **36** (3 halves lost) | **0.116** |
| ≥4 | odd/even (sensitivity) | 35 (4 lost) | 0.118 |
| ≥6 | chronological (sensitivity) | 14 (0 lost) | 0.232 |
| ≥6 | odd/even (sensitivity) | 14 (0 lost) | 0.232 |

* **Embeddings: 217 of 253 cohort labels (86%)** carry a `technique_nodes.embedding`. The
  ≥50%-coverage gate from E5 is retained; below it the mpnet arms are SKIPPED, not imputed.
* **Systems per half** (production detector, untruncated): min 1, **median 3**, max 7,
  mean 3.01; **0 halves have no system**. Registered because it bounds what this cell can
  say: a size-weighted aggregate over a median of three matched pairs is three numbers, and
  a method built on a decomposition that fine cannot be rescued by a wider bootstrap.

### Why the chronological split is PRIMARY here (and why that is not split-shopping)

PoC-E5 pre-registered **odd/even** as primary and chronological as a *leakage probe*, and
its own reading is explicit that the chronological result was "a hypothesis for the next
cell, not a verdict for this one". E15 **is** that next cell, so it promotes the probe to
primary, with the reason stated before the run: odd/even halves share events, opponents and
annotation batches, and E5 measured that confound at production 0.250 (odd/even) → 0.163
(chronological). A descriptor that only recognises the annotation batch is worthless for a
dossier sentence. **Odd/even is still reported as a sensitivity pass**, so this cell cannot
be accused of reporting only the split that flatters the candidate.

### Data class — athlete corpus only

Every read is `matches` and `technique_nodes`, both PUBLIC. This cell **never touches the
`graphs` table**, so `owner_kind` cannot leak by construction: there is no query that could
return a user-owned row. No `user_sessions`, no `profiles`, no app-fed data enters any arm,
any distance, or any centroid.

**`repository.rated_athlete_graph_ids` is deliberately NOT applied, and here is why rather
than a silent omission:** that filter exists to stop a baseline mixing the Glicko-2 and V1
rating scales inside one `node_key`. This cell reads **no rating at all** — half-career
graphs have no persisted `graph_edges`, `computed_elo` is 0 for every node in every arm
(E5's declared substitution), so `system_similarity`'s 0.15 ELO term is a constant for every
pair alike and the new arms drop the ELO term entirely. With no rating read, there is no
scale to mix.

## 4. The arms — fixed now, in this order

Seven arms. **Only two carry a hypothesis**; the rest are nulls, references and one
ablation, and none of them gets a verdict. Keeping the tested family at two is the
multiplicity control.

| id | arm | role | definition |
|---|---|---|---|
| `a0` | production method (athlete_systems, 12-node) | reference — what ships | `1 − match_systems(...)['aggregate_similarity']` on `_to_athlete_graph(h, 12)` |
| `n1` | **null: size only** — **H0** | null | L2 over `(log n, log m, mean degree)` |
| `n2` | null: degree histogram | null | L2 over the normalised, log-binned degree sequence |
| `c1` | node Jaccard, whole graph (no systems) | **ablation** — control for H1 | `1 − jaccard(nodes(A), nodes(B))`; the degenerate one-system case |
| **`c2`** | **system overlap (greedy match, member Jaccard, size-weighted)** | **H1** | below |
| `r1` | mpnet centroid, occurrence-weighted (flat, whole half-graph) | reference — control for H2 | E5's strongest arm, `cosine_distance` of `e5_grapple_like._centroid(..., weighted=True)` |
| **`c3`** | **system mpnet centroid (greedy match, size-weighted)** | **H2** | below |

**`c2` and `c3`, defined exactly.** Both run on the **untruncated** half-graph (stated
reason: truncating to the 12 busiest nodes discards precisely the long-tail technique
identity these arms exist to exploit; E5 measured truncation as costing production nothing,
Δ −0.008 [−0.081, +0.051], so the comparison to `a0` is not distorted by it).

1. Detect A's and B's systems with the production detector. Both lists arrive pre-sorted
   `(-size, hub)` by `detect_athlete_systems`.
2. Greedy pairing, exactly production's loop: walk A's systems in that fixed order; each
   takes the best-scoring **unmatched** system in B; strict `>` so a tie falls to B's first
   candidate in B's own fixed order. **No dict, set or frozenset iteration decides anything**
   (failure-archaeology #10 — the 2026-07-07 `PYTHONHASHSEED` reshuffle of these exact
   analogue lists).
3. Per-pair score:
   * `c2`: `jaccard(set(a.members), set(b.members))`.
   * `c3`: cosine similarity of the two systems' **occurrence-weighted mpnet centroids**
     (each system's member vectors weighted by that member's `occ` in the half-graph;
     members without an embedding are dropped, a system with no embedded member scores 0).
4. Aggregate: `Σ_matched size(a_sys) · score / Σ_all size(a_sys)` — the "size-normalised
   weighted overlap" the decision asks for. An A-system with no partner counts as 0.
   **The denominator is constant within a query row, so it cannot change the retrieval
   ranking** — it exists so the number a dossier might print is honest, not to win MRR.
5. Distance = `1 − aggregate`. Asymmetric A→B, like production. Within a row the query is
   fixed, so the asymmetry cannot bias the ranking being scored.

**Deliberately NOT in the arm set, stated rather than quietly dropped:**
* *Hub-identity term.* Subsumed — the hub is a member, so member Jaccard already carries it.
  It earns its place in the PRESENTATION (§7), not in the metric.
* *Truncated (12-node) variants of `c2`/`c3`.* Not measured. Add only if a system arm is
  accepted and someone proposes wiring it behind production's existing truncation.
* *`constellations.detect` as a second detector.* Measured inert (§2).
* *Hungarian/optimal assignment instead of greedy.* Would change two things at once. If a
  greedy system arm clears, optimal assignment is the obvious next ablation.

## 5. Metric, thresholds, verdicts — decided before the numbers

**Primary metric: MRR** (mean reciprocal rank of the athlete's own half B among all halves
B), per-athlete reciprocal ranks retained so every comparison is a **paired bootstrap over
athletes**. **top-1 / top-3 / top-5 recall reported beside it, never the criterion** — with
36 queries a hit/miss rate has a far wider paired interval than a reciprocal rank. Ties rank
pessimistically. The chance floor `H_n / n` is printed in every table.

A **win** = the paired ΔMRR interval lies strictly above 0 **and** is non-degenerate
(`hi > lo`).

| | Verdict rule, fixed before the run |
|---|---|
| **H1** — system-level similarity beats the size descriptor | **ACCEPT** iff `c2` wins against **`n1` (H0, size)** AND against `n2` AND against `a0`. Anything less is REJECT. |
| **H1b** — does the DECOMPOSITION earn its keep? (sub-question, reported separately, no accept/reject) | Δ(`c2` − `c1`). Strictly above 0 → the system decomposition adds signal over plain shared-technique overlap. Interval covers 0 → **shared-technique overlap is the whole effect, and "systems" is a presentation decision, not a metric one.** |
| **H2** — per-system mpnet centroid beats per-graph centroid | **ACCEPT** iff `c3` wins against **`r1`** AND against `n1` AND against `n2`. |
| **H0** | `n1`, the three-number size descriptor, is the thing every candidate must beat. E5 measured it out-scoring the shipped method. |

* **Conjunctive criteria are the multiplicity control.** Requiring an arm to clear three
  intervals simultaneously makes a false ACCEPT far harder than any α-correction on a single
  comparison would, so no correction is applied — stated, not assumed.
* **A carried-over reference gets no verdict.** `r1` is E5's leader re-run on (nearly) the
  same data; its number here is the SAME measurement ± corpus drift, not an independent
  confirmation. It is reported so H2 has something to be tested against, and for no other
  reason. E5's own chronological table already shows `r1` NOT clearing the size null
  (+0.135 [−0.021, +0.285]) — that is the bar, and it is a high one.
* **An inconclusive interval is reported as inconclusive**, never as evidence of
  equivalence (ADR-03).
* **If no arm is accepted, the shipped method stands** and this cell still delivers what it
  was for: a measured answer to whether the owner's system-level decision is a metric change
  or a presentation change, plus the presentation design in §7 — which does not depend on
  any arm winning.

## 6. Stated limits, before the numbers

* **36 queries.** Intervals will be wide. E5's paired intervals on this cohort ran roughly
  ±0.13; an effect smaller than that cannot be resolved here and will be reported as
  unresolved, not as a tie.
* **Median 3 systems per half.** The size-weighted aggregate averages three numbers for a
  typical athlete. A decomposition-based method is being asked to work at a resolution where
  the decomposition is nearly trivial. If H1 and H2 both fail, "the corpus is too thin for
  system-level matching" is a live and honest reading, distinct from "system matching does
  not work".
* **Undirected, by inheritance.** Community detection projects the directed ActionFlow graph
  onto an undirected one (`detect_communities` → `to_undirected`). Every system arm is
  therefore scored on strictly less than the graph carries — gap #10's complaint, inherited
  from E5 and not fixed here.
* **`c2` is unweighted inside a system** (set Jaccard over members) while the aggregate
  across systems is size-weighted. An occurrence-weighted within-system overlap (Ruzicka) is
  a defensible variant and is NOT run, to keep the tested family at two.
* **Production's 12-node truncation is not reproduced in the new arms** (§4). `a0` keeps it
  so the reference is the shipped method; the comparison is "production as shipped" vs
  "these arms as they would be built", which is the decision-relevant contrast.
* **Nothing here touches production.** No DB write, no replay, no site export, no edit to
  `export/site_data.py` or `analysis/athlete_systems.py`.

## 7. Presentation proposal — doc only, wired by nobody in this cell

The current dossier renders `Grapples most like` + chips of `Name 63%`
(`export/site_data.py:1819-1823`). After E5, **that percentage alone is not defensible**: it
is defined, and its defined value was not distinguishable from how much tape an athlete has.

The proposed sentence keeps the ranking and replaces the bare number with the thing a reader
can check:

> **Grapples most like Mica Galvão** — shares two systems: *half guard → back take* and
> *closed guard → armbar*.

Rules the design must obey, independent of which arm wins:

1. **Never a raw percent alone.** A number may appear only next to the named shared systems,
   and only if this cell (or a successor) has a self-recognition MRR to stand behind it —
   PoC-E5's standing acceptance condition: *"a swap carries the method's self-recognition
   number onto the dossier beside the percentage."*
2. **Name the shared systems by their hubs, on BOTH sides.** The existing `AthleteSystem`
   already carries `name` (= hub label), `hub`, `members` and `size`; `match_systems`
   already returns the matched `a_system`/`b_system`/`a_hub`/`b_hub` per pair. **The payload
   needed to render this sentence already exists** — `profile_dict["analogues"]` currently
   throws it away by keeping only `best_match` and `aggregate_similarity`. That is the whole
   delta on the export side: stop discarding what is already computed.
3. **Cap at the top 2–3 shared systems** and order them by the aggregate's own weight
   (`size(a_sys) · score`, descending, ties by hub label — a total order, per
   failure-archaeology #10) so the sentence is stable across exports.
4. **Show the overlap, not the score.** Where the two systems' member sets intersect, the
   shared technique names are the honest evidence and should be available on hover/expand.
   A reader who disagrees can check it; a percent cannot be checked.
5. **If no shared system clears a floor, say so** — "no shared system" is a legitimate and
   informative dossier line. Do not fall back to printing a percent to fill the space.
6. **Degrade honestly at low tape.** An athlete with one detected system (the marginal above
   says the floor is 1) gets at most one named system; the UI must not imply a richer
   comparison than the data supports.

Cost, named so the decision is informed: the sentence is longer than a chip, so the
`.ana .chips` row becomes a short list, not a horizontal chip strip. That is a layout change
in `export/site_data.py`'s systems section (and the corresponding CSS in the `site/` bundle),
and it is a design task, not a research one.

---

## 8. Amendment — what changed AFTER the primary table was seen (post-hoc, disclosed)

Everything in §§1–7 was fixed before any arm ran. Two things were added afterwards. Both are
disclosed here rather than folded silently into the results, and **neither carries a
verdict** — the accept/reject rules in §5 are untouched and were applied exactly as written.

**(a) Four paired comparisons that §5 left blank.** The comparison list shipped without
`Δ(c1 − n2)` and `Δ(r1 − n2)`, so the degree-null column was empty for the ablation and the
reference. Those cells were filled. This adds no hypothesis and changes no rule; it makes
the table legible.

**(b) A confound probe, `p1`.** The primary table showed the *ablation* `c1` — plain
whole-graph node Jaccard, the control that was supposed to lose — as the strongest arm and
the first in this series to clear the size null. Before believing that, one mechanism had to
be ruled out. Read-only marginal, measured before the probe was written: **82 of 170 cohort
labels are used by exactly one athlete** (68/170 are used by ≥3). A label only one athlete
uses can appear in the intersection of that athlete's OWN two halves and in no cross-athlete
intersection at all — so it inflates the true pair's Jaccard and nobody else's. That is a
self-recognition cheat by construction, and it would mean `c1` recognises *who annotated the
tape*, not the game.

`p1` re-runs `c1` over the shared vocabulary only (labels used by ≥3 cohort athletes,
40% of the vocabulary). The threshold and **both branches of the reading were written into
the runner before the probe was executed** — `scripts/research/e15_systems_similarity.py`,
`SHARED_VOCAB_MIN_ATHLETES` and `_reading`'s two-branch text — so the probe could not be read
after the fact in whichever direction suited it.

**The probe's own honest limit:** a shared-vocabulary restriction removes idiosyncratic
*labels*; it does not remove the possibility that an athlete's whole career was ingested in
one dump by one annotator who made systematically different choices among the *shared*
labels. That residual is not measured here and is the first thing the next cell must design
against.

### Determinism, checked rather than asserted

failure-archaeology #10 is this exact output: the "grapples most like" analogue lists
reshuffled on ~43/77 dossiers per export because `PYTHONHASHSEED` broke ties in hub
selection and community ordering. `--digest` hashes every arm's distance matrix on the
primary cohort plus the probe's vocabulary filter. Measured 2026-09-11 under
`PYTHONHASHSEED` 0, 7 and 123: **`8c6d6c41774605f524f57dc6c31d72061fac1c5b7de0ddca175fdc90cbd11469`,
identical in all three.** A full re-run against the live DB also reproduces this document
byte-for-byte. `--self-check` asserts the greedy matcher and the size-weighted aggregate on
hand-built systems with no DB.

### What the next cell must pre-register

`c1` is now a hypothesis, not a control, and it is the strongest candidate this series has
produced. It cannot be accepted here — promoting a control to a winner after seeing the
table is the exact error pre-registration exists to prevent. The next cell should register,
before running:

1. `c1` (whole-graph node Jaccard) as the **candidate**, `n1` (size) and `n2` (degree) as
   nulls, `a0` (production) and `r1` (flat mpnet centroid) as references, chronological
   split primary — i.e. this cell's table with the roles corrected.
2. An **annotation-provenance control** stronger than `p1`: hold out athletes whose bouts
   span multiple ingestion batches, or stratify the cohort by `matches.created_at` batch, so
   "same annotator" and "same athlete" are not the same variable.
3. A **shared-vocabulary sweep** (≥2 / ≥3 / ≥4 athletes) rather than one threshold, with the
   monotonicity of the advantage as the reading — a signal that decays smoothly as rare
   labels are removed is style; one that falls off a cliff at ≥2 is vocabulary.
4. Whether the arm ships as the RANKING or only as the EXPLANATION (§7) — those are separate
   decisions and only the first needs a metric to win.

### One thing this cell settles regardless of any amendment

On the chronological split — the one without the batch confound — **the shipped method
scores MRR 0.113 against a chance floor of 0.116 over 36 candidates**, and loses to the
three-number size descriptor by a paired Δ of −0.086 [−0.168, −0.012]. PoC-E5 reported it as
"defined, and its defined value is not distinguishable from volume". On this cohort and this
split it is weaker than that: it is not distinguishable from **chance**. That result needs no
amendment, no new arm and no acceptance — it is the shipped method measured against its own
pre-registered criterion, and it is the strongest argument for §7's rule that the dossier
must never print that percentage alone.

---

<!-- E15:RESULTS -->

## Results

Generated by `uv run python -m scripts.research.e15_systems_similarity` — **do not hand-edit**; a re-run replaces this block. Script: `scripts/research/e15_systems_similarity.py`.

**Corpus gate:** 465 gated bouts of 911 final+sequence (310 under 4 events, 136 dropped as one-sided) — **DRIFT** against PoC-E5's published 466; the corpus moved since that cell ran, so its numbers are not byte-comparable to these.

**Embeddings:** 217 of 253 cohort labels carry a `technique_nodes.embedding` (86%); the mpnet arms run

### PRIMARY — chronological split, ≥4-bout floor

Cohort: **36 athletes** (of 39 at the ≥4-bout floor; 3 lost a half to the ≥3-node / ≥2-edge gate). Split: `chronological`. Median half-graph: 10 nodes / 12 edges. Chance MRR: 0.116. Shared vocabulary (≥3 athletes): 68/170 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| a0 production method (athlete_systems, 12-node) | reference | 0.113 [0.082, 0.148] | 0% | 6% | 19% | -0.086 [-0.168, -0.012] | — | — |
| n1 null: size only (H0) | null | 0.199 [0.131, 0.280] | 6% | 19% | 33% | — | — | — |
| n2 null: degree histogram | null | 0.176 [0.100, 0.271] | 8% | 14% | 19% | — | — | — |
| c1 node Jaccard, whole graph (no systems) | ablation | 0.366 [0.246, 0.489] | 22% | 44% | 47% | +0.168 [+0.046, +0.291] | +0.190 [+0.031, +0.340] | +0.254 [+0.141, +0.370] |
| c2 system overlap (member Jaccard, size-weighted) | **H1** | 0.286 [0.181, 0.401] | 17% | 31% | 36% | +0.087 [-0.030, +0.209] | +0.110 [-0.042, +0.255] | +0.173 [+0.072, +0.288] |
| p1 node Jaccard, SHARED vocabulary only (confound probe) | probe (post-hoc) | 0.339 [0.225, 0.458] | 19% | 39% | 53% | +0.141 [+0.028, +0.256] | +0.163 [+0.009, +0.309] | +0.227 [+0.120, +0.340] |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.304 [0.199, 0.415] | 14% | 39% | 44% | +0.105 [-0.006, +0.222] | +0.128 [-0.015, +0.262] | +0.191 [+0.095, +0.295] |
| c3 system mpnet centroid (size-weighted) | **H2** | 0.211 [0.126, 0.312] | 8% | 25% | 33% | +0.012 [-0.069, +0.104] | +0.035 [-0.087, +0.152] | +0.098 [+0.028, +0.184] |
| _chance (random ranking)_ | — | 0.116 | 3% | 8% | 14% | — | — | — |

### Sensitivity — odd/even split, ≥4-bout floor (PoC-E5's primary split)

Cohort: **35 athletes** (of 39 at the ≥4-bout floor; 4 lost a half to the ≥3-node / ≥2-edge gate). Split: `odd_even`. Median half-graph: 11 nodes / 11 edges. Chance MRR: 0.118. Shared vocabulary (≥3 athletes): 64/168 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| a0 production method (athlete_systems, 12-node) | reference | 0.248 [0.148, 0.368] | 14% | 23% | 31% | +0.016 [-0.110, +0.151] | — | — |
| n1 null: size only (H0) | null | 0.232 [0.151, 0.327] | 9% | 23% | 40% | — | — | — |
| n2 null: degree histogram | null | 0.195 [0.109, 0.299] | 11% | 17% | 23% | — | — | — |
| c1 node Jaccard, whole graph (no systems) | ablation | 0.501 [0.364, 0.641] | 40% | 51% | 57% | +0.269 [+0.118, +0.419] | +0.306 [+0.116, +0.485] | +0.253 [+0.119, +0.389] |
| c2 system overlap (member Jaccard, size-weighted) | **H1** | 0.369 [0.246, 0.493] | 23% | 43% | 49% | +0.136 [+0.003, +0.274] | +0.173 [+0.007, +0.332] | +0.120 [-0.020, +0.260] |
| p1 node Jaccard, SHARED vocabulary only (confound probe) | probe (post-hoc) | 0.442 [0.308, 0.584] | 34% | 43% | 49% | +0.210 [+0.067, +0.360] | +0.247 [+0.064, +0.422] | +0.194 [+0.052, +0.341] |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.371 [0.250, 0.503] | 26% | 40% | 49% | +0.139 [-0.010, +0.291] | +0.176 [+0.025, +0.325] | +0.123 [-0.045, +0.287] |
| c3 system mpnet centroid (size-weighted) | **H2** | 0.259 [0.153, 0.377] | 17% | 20% | 31% | +0.027 [-0.077, +0.128] | +0.064 [-0.059, +0.190] | +0.010 [-0.137, +0.158] |
| _chance (random ranking)_ | — | 0.118 | 3% | 9% | 14% | — | — | — |

### Sensitivity — chronological split, ≥6-bout floor

Cohort: **14 athletes** (of 14 at the ≥6-bout floor; 0 lost a half to the ≥3-node / ≥2-edge gate). Split: `chronological`. Median half-graph: 16 nodes / 19 edges. Chance MRR: 0.232. Shared vocabulary (≥3 athletes): 40/134 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| a0 production method (athlete_systems, 12-node) | reference | 0.270 [0.170, 0.411] | 7% | 29% | 57% | -0.203 [-0.392, -0.033] | — | — |
| n1 null: size only (H0) | null | 0.472 [0.295, 0.660] | 29% | 57% | 71% | — | — | — |
| n2 null: degree histogram | null | 0.294 [0.160, 0.466] | 14% | 21% | 57% | — | — | — |
| c1 node Jaccard, whole graph (no systems) | ablation | 0.717 [0.533, 0.893] | 57% | 79% | 86% | +0.245 [+0.031, +0.455] | +0.423 [+0.121, +0.692] | +0.448 [+0.262, +0.635] |
| c2 system overlap (member Jaccard, size-weighted) | **H1** | 0.675 [0.473, 0.875] | 57% | 64% | 79% | +0.203 [-0.086, +0.472] | +0.381 [+0.067, +0.670] | +0.405 [+0.192, +0.616] |
| p1 node Jaccard, SHARED vocabulary only (confound probe) | probe (post-hoc) | 0.704 [0.512, 0.882] | 57% | 79% | 86% | +0.231 [-0.001, +0.453] | +0.409 [+0.094, +0.690] | +0.434 [+0.240, +0.630] |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.481 [0.276, 0.699] | 36% | 43% | 57% | +0.009 [-0.177, +0.203] | +0.187 [-0.058, +0.428] | +0.211 [+0.043, +0.401] |
| c3 system mpnet centroid (size-weighted) | **H2** | 0.387 [0.214, 0.579] | 21% | 43% | 57% | -0.085 [-0.298, +0.127] | +0.093 [-0.156, +0.342] | +0.117 [-0.112, +0.356] |
| _chance (random ranking)_ | — | 0.232 | 7% | 21% | 36% | — | — | — |

### Sensitivity — odd/even split, ≥6-bout floor

Cohort: **14 athletes** (of 14 at the ≥6-bout floor; 0 lost a half to the ≥3-node / ≥2-edge gate). Split: `odd_even`. Median half-graph: 14 nodes / 18 edges. Chance MRR: 0.232. Shared vocabulary (≥3 athletes): 40/134 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| a0 production method (athlete_systems, 12-node) | reference | 0.387 [0.219, 0.583] | 21% | 43% | 64% | -0.026 [-0.266, +0.189] | — | — |
| n1 null: size only (H0) | null | 0.413 [0.254, 0.602] | 21% | 50% | 71% | — | — | — |
| n2 null: degree histogram | null | 0.345 [0.179, 0.538] | 21% | 36% | 57% | — | — | — |
| c1 node Jaccard, whole graph (no systems) | ablation | 0.829 [0.645, 1.000] | 79% | 86% | 86% | +0.415 [+0.168, +0.646] | +0.484 [+0.155, +0.762] | +0.441 [+0.261, +0.624] |
| c2 system overlap (member Jaccard, size-weighted) | **H1** | 0.557 [0.364, 0.763] | 43% | 57% | 86% | +0.144 [-0.071, +0.370] | +0.213 [-0.065, +0.484] | +0.170 [-0.054, +0.404] |
| p1 node Jaccard, SHARED vocabulary only (confound probe) | probe (post-hoc) | 0.592 [0.405, 0.786] | 43% | 64% | 86% | +0.179 [-0.059, +0.420] | +0.247 [-0.057, +0.533] | +0.205 [-0.054, +0.456] |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.659 [0.450, 0.851] | 57% | 71% | 79% | +0.246 [-0.006, +0.482] | +0.314 [+0.049, +0.563] | +0.272 [-0.020, +0.555] |
| c3 system mpnet centroid (size-weighted) | **H2** | 0.455 [0.260, 0.689] | 36% | 36% | 57% | +0.042 [-0.074, +0.179] | +0.110 [-0.113, +0.331] | +0.068 [-0.146, +0.303] |
| _chance (random ranking)_ | — | 0.232 | 7% | 21% | 36% | — | — | — |

## Reading

On the split this cell pre-registered as primary — chronological halves that share no event, no opponent set and no annotation batch — the shipped method scores MRR 0.113 [0.082, 0.148] against a chance floor of 0.116 over 36 candidates, and against the three-number size descriptor's 0.199 (paired Δ -0.086 [-0.168, -0.012]).
**H1, the owner's decision as a metric.** Scoring matched systems by WHICH techniques they contain (member Jaccard, size-weighted) reaches MRR 0.286 [0.181, 0.401]: Δ vs H0/size +0.087 [-0.030, +0.209], Δ vs production +0.173 [+0.072, +0.288]. That is the pre-registered criterion, and it is the only thing that decides H1.
**H1b, the question the ablation exists for.** Plain shared-technique overlap with NO systems at all (whole-graph node Jaccard) scores 0.366; the system decomposition moves it by -0.080 [-0.173, +0.003]. The decomposition does not measurably change the ranking. The honest reading is that shared techniques carry the signal and the system grouping is worth having for what it lets a dossier SAY, not for where it ranks an athlete — which is exactly the split between §7 and the metric.
**H2.** Decomposing the mpnet centroid per system reaches 0.211 against the flat occurrence-weighted centroid's 0.304, paired Δ -0.093 [-0.196, +0.012]. Note what the reference is: r1 is PoC-E5's strongest arm re-run on nearly the same data, so its own number here is that same measurement ± corpus drift and carries no verdict — it exists so H2 has something to be tested against.
Against the null that has beaten everything so far, r1 scores Δ vs H0/size +0.105 [-0.006, +0.222] and c3 scores +0.012 [-0.069, +0.104]. Clearing the size null on 36 queries is the bar PoC-E5 set and it has not moved.
**The finding this cell did not go looking for.** `c1`, registered as an ABLATION to control H1 and carrying no verdict, is the strongest arm in every pass — MRR 0.366 here — and it is the first arm in this series to clear the size null: Δ vs H0/size +0.168 [+0.046, +0.291], Δ vs degree +0.190 [+0.031, +0.340], Δ vs production +0.254 [+0.141, +0.370]. It is also the simplest thing in the table: `|A∩B| / |A∪B|` over technique labels, no communities, no embeddings, no weighting. **This cell cannot accept it** — it was pre-registered as a control, and promoting a control to a winner after seeing the table is the exact error pre-registration exists to prevent. It is the pre-registered hypothesis for the NEXT cell, and §Amendment says what that cell has to rule out first.
**And the confound that has to be ruled out before anyone ships `c1`.** 102 of 170 cohort labels are used by fewer than 3 athletes; a label only one athlete uses can land in the intersection of that athlete's OWN two halves and nowhere else, which is a self-recognition cheat by construction, not style. Restricting `c1` to the shared vocabulary drops it to 0.339 [0.225, 0.458] (Δ vs c1 -0.027 [-0.062, -0.001]), and against the size null it scores +0.141 [+0.028, +0.256]. The advantage SURVIVES the restriction, so the signal is shared-vocabulary style rather than annotation provenance — the strongest single result in this cell. This probe is POST-HOC (§Amendment) and carries no verdict either way.

### Verdicts

1. **H1 — system-level overlap beats the size descriptor** — REJECT — MRR 0.286 [0.181, 0.401]; Δ vs H0/size +0.087 [-0.030, +0.209], Δ vs degree +0.110 [-0.042, +0.255], Δ vs production +0.173 [+0.072, +0.288]
2. **H1b — does the DECOMPOSITION earn its keep? (sub-question, no accept/reject)** — NO — shared-technique overlap covers the effect; on this corpus 'systems' is a PRESENTATION decision, not a metric one: Δ(c2 − c1) -0.080 [-0.173, +0.003]
3. **H2 — per-system mpnet centroid beats per-graph centroid** — REJECT — MRR 0.211 [0.126, 0.312]; Δ vs flat centroid -0.093 [-0.196, +0.012], Δ vs H0/size +0.012 [-0.069, +0.104], Δ vs degree +0.035 [-0.087, +0.152]
4. **ship** — DO NOT WIRE — no system-level arm cleared its criterion; the shipped ranking stands and the change that IS supported is presentational (§7), which needs no new metric

Nothing in this cell touches production: no DB write, no replay, no site export, no edit to `export/site_data.py` or `analysis/athlete_systems.py`. The presentation proposal in §7 above stands on its own and does not depend on any arm winning.

<!-- /E15:RESULTS -->
