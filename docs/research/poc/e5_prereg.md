# PoC-E5 — pre-registration

Written 2026-08-25, **before any arm was scored**. Everything below was fixed against the
corpus's MARGINALS only (how many bouts, how many athletes, how many labels carry an
embedding) — read-only counts that say nothing about which method wins. `e5.md` reproduces
this file verbatim above its results, so the criterion travels with the numbers.

## Correction to `03_POC_PLANS.md`, registered before the run

The plan names candidate (a), "current ELO-weighted mpnet centroid", as **the baseline**.
Traced 2026-08-25, that is not what ships:

* The dossier's "Grapples most like NN%" comes from `export/site_data.py` →
  `athlete_systems.compare_profiles` → `match_systems(...)["aggregate_similarity"]`
  (`site_data.py:778`, rendered at `site_data.py:1433`).
* Its input is `from_career_graphview(name, _career_graphview(athlete, ..., limit=12))` —
  the career graph **truncated to the 12 busiest nodes**.
* `analysis/embeddings.nearest_graphs`, the pgvector path the plan calls the baseline, has
  **no production caller** anywhere in the workspace — grepped 2026-08-25 across `.py`,
  `.ts` and `.tsx`, the only hits are its own definition and
  `tests/test_private_data_boundary.py`. Nothing ships on it.

So the baseline arm is the system-matching method as configured in production, and the
ELO-weighted centroid is a candidate like any other — with one caveat that also has to be
declared up front: **the ELO weighting is not evaluable under this criterion.** The weight
is `graph_edges.elo`, a career-level ELO-replay artefact; half a career has no persisted
graph and therefore no edge ELOs. An occurrence-weighted centroid stands in its place and
is labelled as a stand-in, not as candidate (a).

Candidates (c) graphlet correlation distance and (e) graph2vec are **deferred**, stated
here rather than quietly dropped: (c) needs an orbit counter (orca is a C dependency) and
(e) needs a trained model plus `karateclub`, and neither can be justified before the two
cheap spectral/path descriptors have shown that anything at all clears the nulls on this
corpus. If a signature arm wins, they are the obvious next two.

**Added to the candidate set:** NetLSD (Tsitsulin et al. 2018). Same family as portrait
divergence — a permutation- and size-invariant whole-graph signature — and the plan's
candidate list predates the owner naming it. Registered here before any arm ran.

## Corpus, gate, cohort

* Source: `matches` where `status='final' AND sequence IS NOT NULL`, through
  `analysis/poc/e9_markov.load_corpus` — the same loader, the same gate, no restatement.
* Gate: `attribution.bout_flags(...)["perspective_reliable"]` AND ≥ 4 events. **MANDATORY**
  — 43.9% of corpus bouts file every event under one athlete.
* **Corpus drift is expected and reported.** PoC-E8 and PoC-E9 published 429 gated bouts;
  the corpus has grown since. The runner prints the current count against 429 and says so;
  these numbers are not directly comparable to those cells'.
* An athlete's bouts = gated bouts in which that athlete has **≥ 1 own event**. A bout they
  appear in but file nothing under is not tape of their game.
* Graph = `transitions/build_graph.network_from_sequences` over that athlete's OWN events,
  bout by bout. Production's builder, unmodified.
* **Data class: athlete corpus only.** Every row read is `matches` / `technique_nodes`,
  both public. No `owner_kind='user'` graph, no `user_sessions`, nothing app-fed enters
  any arm, any distance or any centroid.

**Floors, fixed from the bout-count marginal measured read-only before the run:** 38
athletes have ≥ 4 gated own-event bouts, 14 have ≥ 6.

* **Primary: floor ≥ 4** (38 athletes eligible). Chosen for power — a paired bootstrap over
  14 queries cannot separate two methods that differ by less than about a fifth of the
  scale, and reporting that as "no difference" would be the exact error ADR-03 exists to
  prevent.
* **Sensitivity: floor ≥ 6** (14 eligible) — `03_POC_PLANS`'s floor as written.
* A half must carry ≥ 3 nodes and ≥ 2 edges. An athlete losing either half is dropped from
  the cohort and **counted in the report**, never silently.

## The criterion

**Self-recognition.** Split an athlete's bouts into two halves, build a graph from each,
and ask every method to retrieve that athlete's own half B out of every athlete's half B.
No labels, no human judgement, nothing to game: a descriptor that cannot recognise the same
grappler twice cannot honestly tell a reader who somebody grapples like.

* **Split: odd/even by chronological key** (primary), so the two halves are balanced in
  size and in era.
* **Primary metric: MRR** (mean reciprocal rank of the true half B), with per-athlete
  reciprocal ranks retained so every comparison is a **paired bootstrap over athletes**
  (`stats_rigor.bootstrap_ci`, seed 20260820, 4 000 draws). Athlete is the resampling unit
  because the two halves of one athlete are not two independent observations.
* MRR rather than top-1 because top-1 is a coin per query; with 38 queries the paired
  interval on a hit/miss rate is far wider than on a reciprocal rank. **Top-1 and top-5
  recall are reported beside it** and are never the criterion.
* **Ties rank pessimistically.** A method returning the same distance for every candidate
  scores at the floor, not by luck.
* **Chance floor**, printed in every table: `H_n / n`, the MRR of a uniformly random
  ranking over n candidates.

### Nulls — mandatory arms, not decoration

1. **size only** — (log n, log m, mean degree). Nothing but how much tape exists.
2. **degree histogram** — the normalised degree sequence, log-spaced bins. Cheap structure,
   no spectrum, no paths.

NetLSD advertises size-invariance and portrait divergence is known to react to size. Both
claims are checked here rather than believed.

### Verdicts, decided before the numbers

* **A challenger is ACCEPTED as a replacement** iff its paired ΔMRR against the production
  arm excludes 0 in its favour **AND** it does the same against **both** nulls. Beating
  production while tying a degree histogram is not a style descriptor; it is a size
  descriptor that happens to beat another size descriptor.
* **A win requires a non-degenerate interval** (`hi > lo`) — PoC-E9's amendment, adopted
  verbatim.
* **If no challenger is accepted, the shipped method stands** — and still gains what
  PoC-E5 was for: a definition and a self-recognition number to print beside the
  percentage. A REJECT does not close the gap-#14 item; it settles which method closes it.
* **If NO arm clears both nulls**, the honest reading is stated as such: at half-career
  resolution this corpus cannot yet distinguish style from volume. That is a publishable
  null, not a failed run.

### Leakage probe — reported, never a criterion

Odd/even halves share events, opponents and annotation batches. A method could "recognise"
the batch rather than the game. The same cohort is therefore re-run under a
**chronological** split (early half vs late half), which removes that confound and costs
real signal because a grappler's game moves. **The gap between the two schemes is an upper
bound on the confound** and is reported as such. It decides nothing on its own.

## Stated limits, before the numbers

* **Both signature arms use an undirected projection.** NetLSD needs a symmetric Laplacian,
  the network portrait needs undirected shortest paths. The ActionFlow graph is directed,
  so both arms are scored on strictly less than the graph carries — gap #10's complaint,
  inherited. A directed variant is out of scope for this cell.
* **The production arm is reproduced, not invoked.** Production's node set comes from the
  persisted `graph_edges`; a half-career has no persisted graph, so the substrate here is
  `matches.sequence`. `computed_elo` is 0, which zeroes `system_similarity`'s 0.15 ELO term
  for every pair alike — a constant, with no effect on ranking.
* **No clustering is produced by this cell**, so `constellations/stability.py` is not
  invoked. That machinery grades a partition; there is no partition here. The stability
  instrument for a retrieval arm is the paired bootstrap over athletes, and it is applied
  to every comparison. Adding a clustering arm purely to have something to stabilise would
  reintroduce exactly the "it looks nice" criterion ADR-03 forbids.
* **38 queries is a small evaluation.** Intervals will be wide. An inconclusive paired Δ
  will be reported as inconclusive — never as evidence of equivalence.
