# Literature review — what this ecosystem does, against what the field knows

Date: 2026-08-23. Scope: the analytics implemented across **GrapplingArcAnalytics**,
**GrapplingArcApp**, **BracketAnalysis**, the **GrapplingArc** public site and
**GrapplingArcWeb**, cross-referenced against the academic literature on network
science in sport, rating systems, action valuation, community detection, graph
similarity, small-sample inference, tournament design and combat-sports computer
vision. All citations: `04_BIBLIOGRAPHY.md`. Ranked gaps: `02_GAPS_AND_OPPORTUNITIES.md`.
Implementation plans: `03_POC_PLANS.md`.

The one-paragraph verdict: **this ecosystem independently reinvented most of the right
architecture** — event streams → transition graphs → centrality/communities/value
iteration is exactly the passing-network / possession-value stack the football
literature converged on, and the inferential discipline (Wilson intervals, coverage
gates, BH q-values, pre-registered acceptance criteria, published null results) is
*above* the standard of most published sports-analytics work. What is missing is
(1) **predictive validation** — almost no metric has ever been scored against held-out
outcomes, which is the field's standard test; (2) **algorithm upgrades the literature
has already settled** (Leiden over Louvain, shrinkage over raw small-N σ-thresholds,
fitted over conventional Elo parameters); and (3) **external anchoring** — the App's
self-referential rating and the site's undefined metrics have no ground truth, while
published BJJ papers (Lamas 2024; Andreato 2015) contain numbers ours can be checked
against today.

---

## 1. Transition graphs and network metrics

**What we do.** `analysis/transitions/build_graph.py` builds within-actor directed
transition graphs from event sequences; `analysis/network_metrics.py` computes weighted
PageRank (Zhang et al. 2022), eigenvector and betweenness centrality, Markov
reward/risk per Lamas et al. (2024), and the arrow/dash edge-rendering rules the App
mirrors char-for-char (`services/directedEdges.ts`).

**What the literature says.** This is the passing-network programme (Peña & Touchette
2012; Buldú et al. 2018; Duch et al. 2010) transplanted to one athlete's technique
space — a sound and, for grappling, novel transplant. The only published BJJ analogue
is Lamas et al. (2024), which we already cite as the reward/risk base, and a 2025
judo attack-network analysis with the same node/edge semantics. Two findings:

- **Validated in design, not in output.** The metrics are the standard ones, correctly
  implemented (dist = 1/w for betweenness, sink handling in PageRank). But no
  centrality has ever been tested for *predictive* value the way Radicchi (2011)
  tested PageRank against ATP rankings — and our own `metric_evaluation.py` measured
  `mean_pagerank` at partial-r −0.359 against rank-ELO and ≈0 against win rate.
  The literature's bar is "does the metric predict outcomes the ranking misses";
  we have the harness and have not pointed it at the network metrics.
- **The n=5 ranking problem.** `reward_risk_ranking` publishes point estimates at
  `min_occ=5`, ranking an n=5 technique (+0.250) above an n=64 one (+0.133) —
  the exact pathology Efron & Morris (1975) shrinkage exists to fix, and the repo
  *already has* the Bayesian CI version (`reward_risk_with_ci`) sitting unused by the
  published report. Fix is a wiring change plus an empirical-Bayes prior
  (Whelan & Klein 2018). See PoC-E1.

**Data-quality caveat that dominates everything here.** `analysis/attribution.py`
measured that **43.9% of bouts file every event under one athlete** and the guard/pass
opposite-actor convention is REFUTED at event level (63.4% same-actor). Bracket export
gates on `perspective_reliable`; `network_metrics.build_transition_network` and
`insights.py` **do not**. Any within-actor metric computed over unfiltered bouts is
partly measuring the ingest batch, not the athlete. This is a bigger threat to
validity than any algorithm choice — the literature's term is measurement error in
the network construction step, and passing-network papers (Buldú 2018) flag exactly
this as the field's weakest link.

## 2. Community detection ("constellations" / "systems")

**What we do.** Louvain (networkx, resolution 1.0, seed 42) on the undirected
projection, with the ADR-07 connectivity gate splitting disconnected communities and
counting `rejected_rate` — in both repos, held to parity by a golden fixture. A second,
divergent detector (greedy modularity) survives in `network_metrics.detect_communities`
→ `athlete_systems.py`. The App additionally keeps the legacy ELO-path DFS live for
its Tendencies axis.

**What the literature says.**

- **Traag et al. (2019)** — which the code already cites — is not just "Louvain
  sometimes fails, count it": Leiden's refinement phase *prevents* badly-connected
  communities and converges to subset-optimal partitions, faster. Our gate repairs
  post-hoc and leaves the split parts un-reoptimised; the code's own comment ("if that
  rate turns out material, that is the measured case for switching") describes
  measuring the disease while the cure is published. Leiden is a drop-in
  (`leidenalg`, or pure-Python port for the App). PoC-E2.
- **Fortunato & Barthélemy (2007), the resolution limit**, is unaddressed and bites
  exactly at our scale: modularity cannot resolve communities smaller than ~√(2L)
  edges. On a 15-node user graph or a 3–8-athlete division graph, genuinely separate
  small systems get merged at resolution 1.0 — and the public site then reports
  "community detection separates his game into 4 self-contained systems" where two of
  the four are 2 nodes and 1 edge. A resolution sweep with a stability criterion
  (we already have bootstrap-Jaccard machinery in `constellations/stability.py`) or a
  degree-corrected SBM with model selection (Lee & Wilkinson 2019) are the two
  literature answers.
- **Good, de Montjoye & Clauset (2010)** — the modularity landscape is glassy; one
  deterministic run (the App's sorted-order Louvain) is one sample from exponentially
  many near-optima. Consensus clustering over seeds, which our bootstrap machinery is
  one step away from, is the standard mitigation.
- **Directed flow.** Modularity on the undirected projection throws away edge
  direction — but a "system" in grappling is a region play *flows through*.
  Infomap (Rosvall & Bergstrom 2008) optimises exactly that objective on directed
  graphs and is the literature's tool of choice for transition networks. PoC-X2.

**Verdict.** Architecture right (shared detector, golden parity, measured swap
justification, connectivity accounting — all good practice), algorithm one generation
behind its own citation, and small-graph behaviour unexamined against the resolution
limit. Consolidating the three coexisting detectors onto the shared one is overdue
engineering hygiene with statistical stakes.

## 3. Rating systems

**What we do.** Four engines: (1) App V1 "ELO" — a heavily patched Elo with
multiplicative virtual opponents, session floors, focus bumps and self-reported
outcome offsets; (2) App V2 / Analytics **Glicko-2** — textbook-correct, gate-verified
against Glickman's worked example, cross-repo golden fixtures, session-based periods
on the App side, yearly on the corpus side; (3) `athlete_elo.py` graph-growth engine
with belt bases and a 36-month temporal half-life; (4) Kaggle-corpus Elo ports
(ADCC, UFC).

**What the literature says.**

- **Glicko-2 was the right choice** for the corpus: on comparable sparse-schedule
  data it matches or beats Elo and TrueSkill (Dehpanah 2021), and practitioner MMA
  evidence (FightMatrix 2019) says exactly what our config should be tested for —
  on sparse schedules volatility contributes little, so τ=0.5 is a guess that a
  log-loss sweep should confirm or replace. The RD≤200 publish gate, labelled
  editorial and calibrated against an impact table, has no analogue in the literature
  because the literature doesn't publish ratings to the public — it is defensible and
  honestly labelled.
- **The unbeaten upgrade for a static corpus is Whole-History Rating** (Coulom 2008):
  MAP over each athlete's entire trajectory, better prediction than Elo/Glicko/
  TrueSkill/decayed-history in direct comparison. Our corpus is small (~700 bouts),
  fully stored, and batch-recomputed under a pinned run id — the exact setting where
  WHR's cost disappears and its accuracy advantage is free. PoC-E3.
- **No engine has ever been scored.** ADR-03 pre-registered the right criterion —
  out-of-sample log loss on a temporal split, "spread is never a criterion" — and the
  App even exports `expectedScore` "for log-loss work" with **zero callers**. The
  single highest-value/lowest-effort item in this entire review is building that
  harness and running every engine through it (PoC-E0). Everything else about ratings
  is opinion until it exists. `calibrate_k_factor`'s target-σ matching is precisely
  the "spread" criterion ADR-03 forbids, applied one module over; the empirical-
  parameterization literature (arXiv:2512.18013) fits K by predictive loss.
- **The App's V1 departures from Elo are not Elo.** Session floors (no session is a
  net loss), free focus bumps, self-reported outcome offsets, and a multiplicative
  opponent estimate (same difficulty → wildly different ΔR at different ratings) make
  the number a motivational index with upward drift, not a strength estimate — which
  the repo's own churn measurement then observed as σ = 0.8% of mean. Aldous (2017)
  is the readable account of what Elo-class updates estimate and under what
  assumptions; the App's V2 migration already happened for `computedElo`, so the
  remaining V1 surfaces (userElo, edges, decay) inherit a known-distorted scale.
- **The App's V2 is self-referential.** Virtual opponent = own global ± 70·(d−5),
  node seeds = own global, global = aggregate of nodes: nothing external anchors the
  scale, and `score` is binary landed/missed of one's own technique. Glicko-2's
  guarantees are about paired comparisons between agents; this is closer to a
  self-paced skill tracker wearing Glicko-2's math. That is a legitimate product
  choice — but the confidence machinery (RD, volatility) then doesn't mean what
  Glickman's paper says it means, and ADR-14's decision to keep tiers unexposed is
  exactly right until PoC-E0 says otherwise. Two concrete model-fit defects worth
  fixing regardless: session-count-based (not time-based) RD inflation, and no RD cap
  (Glickman caps at 350; ours compounds unbounded).
- **The draw model** in `elo_calibration.py` (P(draw)=1.0 at Δ=0) was home-grown; the
  Elo-Davidson / Rao-Kupper family (Szczecinski & Djebbi 2020) is the derived
  solution. Now flagged in-code; harmless on the draw-free ADCC corpus.

## 4. Path-to-Victory vs the possession-value literature

**What we do.** `analysis/path_to_victory.py`: discounted Markov-reward value
iteration over the empirical transition kernel, γ=0.8, potential-based shaping
(Ng et al. 1999), submission absorption at observed success rates.

**What the literature says.** This is a correct member of the xT/EPV family (Rudd
2011; Singh 2019; Cervone 2016; Routley & Schulte 2015), and the γ-contraction
argument for cyclic graphs is sound — grappling's cyclic position space genuinely
differs from football's field-position lattice, and the doc argues it properly. Three
gaps against the family's standards:

1. **γ and the shaping weights are unvalidated** — the doc admits it. VAEP (Decroos
   2019) exists precisely because hand-set value grids lose to models trained to
   predict "does a score follow within k actions". The calibration is PoC-E4: fit
   γ (and test the shaping term's contribution) against held-out finish prediction.
2. **Memorylessness.** First-order Markov on grappling positions is a strong
   assumption (how you *got* to half guard matters); the semi-Markov critique
   (Sci Rep 2026) and duration-aware models are the current frontier. Cheap test:
   compare log-likelihood of first-order vs second-order chains on held-out bouts.
3. **Evaluation methodology for xT-class models exists** (arXiv:2604.21087) and
   would slot straight onto PtV.

The site renders PtV-derived numbers ("Path-to-Victory value", "decision space") with
no definition — a communication gap owned by the site (§7), but the upstream fix is a
methodology block the exporter emits with the data.

## 5. Similarity, embeddings, archetypes

**What we do.** mpnet text embeddings, ELO-weighted into graph embeddings; a
walk+SVD structural embedder (`graph_embed.py` — repaired 2026-08-23: its walk path
had never executed); KMeans k=6 archetypes on 18-dim composition+deviance features
with Hennig bootstrap machinery; several hand-weighted cosine composites; the site's
"grapples most like" percentages.

**What the literature says.**

- The football player-embedding literature (ACM SAC '22: node2vec + GraphWave for
  player similarity) validates the *approach*; our implementation predates its own
  validation. The ELO-weighted semantic centroid mixes incommensurable quantities
  (a text-space vector weighted by a rating) with no ablation against the unweighted
  mean — a one-afternoon experiment.
- For whole-graph similarity specifically (the "grapples most like" question),
  Tantardini et al. (2019) is the definitive method comparison: graphlet-based
  distances win, portrait divergence is the information-theoretic all-scales option,
  and graph2vec gives trainable embeddings. Any of the three beats an undefined
  percentage. PoC-E5 defines "grapple-like v2" as: pick two methods, evaluate
  against the one ground truth we have (same-athlete-different-period should beat
  different-athlete), publish the definition.
- **Archetypes:** k=6 was never selected by the repo's own `optimal_k_by_stability`;
  Hennig (2007) stability is implemented but the bootstrap Jaccard has two defects
  (majority-vote-only mapping under a docstring claiming Hungarian; duplicate-index
  inflation). HDBSCAN already sits in the file as the density alternative. Running
  the existing machinery and shipping its answer is not research, just follow-through.
- **`deviance.py MIN_POP=3`** — z-scores from a 3-observation population, feeding 8
  of 18 archetype dimensions and the ±1σ signature threshold — is the single worst
  small-N practice in the Python repo. Empirical Bayes shrinkage (Efron-Morris;
  Whelan & Klein 2018 for paired-comparison settings) is the standard fix and needs
  ~20 lines. PoC-E1.

## 6. σ-threshold classification (App signatures / weak links, Web bands)

**What we do.** Signature = ≥+1σ, weak link = ≤−1σ over the athlete's own node
ratings; population σ; MIN_NODES=4 (App) / minNodes=4 (Web); category anchors with
argmax fallback and no minimum n; the V2 projection seeding unseen nodes at exactly
the global mean (a point mass that mechanically deflates σ).

**What the literature says.** At n=4 the maximum attainable |z| with population σ is
(n−1)/√n = 1.5, so ±1σ is barely reachable and at most one node per tail can clear
it — the gate's behaviour is an artefact of the arithmetic, not of the athlete. No
multiple-comparison control exists across the ~15–50 nodes tested. And the argmax
fallback in `detectCategoryAnchors` guarantees a "signature submission" exists
regardless of evidence — the one pattern the BracketAnalysis side explicitly banned
("an ordering presented as a finding is a finding nobody tested"). The classical
solution is shrinkage + credible intervals: a node is a signature when its
shrunken-posterior interval clears the athlete's mean, which self-adjusts for n.
The App already ships ADR-14 confidence machinery it deliberately hides; the same
machinery, applied per-node, is the honest signature detector. PoC-E1 covers both
repos; the judo *tokui waza* literature (via the 2025 judo network analysis) gives
the domain framing.

## 7. Statistical communication (public site) — where practice diverges most from policy

The BracketAnalysis surface is the ecosystem's gold standard: scope labels, refusal
rendering, q-values, coverage gates. The **GrapplingArc public site** — generated by
`export/site_data.py` — violates, in its generated prose, most of the rules the
bracket pipeline enforces, and its `the-data.html` "no black boxes" claim is not met
for the metrics carrying the editorial argument. Catalogued for the generator
(details and file:line in `GrapplingArc/docs/RESEARCH_NOTES.md`):

1. Percent vs percentage-point conflation ("0.60 to 0.25 (a 35% reduction)" — that is
   58% relative; the same page prints one ELO change as "+0.1%" and "+0.1 pp").
2. Undefined named metrics: *Decision space*, *Path-to-Victory value*,
   *grapples-most-like %*, *ELO-weighted defense*, flowchart *Confidence*.
3. Headline percentages on n=1–3 denominators, denominators demoted or dropped
   (response trees render pct only; "100%" = one observation).
4. Two runtime JS patches (`grapple-like.js`, `i18n.js`) that correct known-wrong
   generated claims after the fact — the corpus of record still carries the errors.
5. Self-normalised radar "fingerprints" inviting cross-athlete comparison that the
   normalisation makes meaningless.
6. Corpus-record framed as career record ("15–0"), including one fabricated fixture
   (`leandro-lo-vs-gordon-ryan-2025`, a draw, which also contradicts the "15–0").
7. Raw Glicko-2 values leaking through "avg opp" captions while declared
   unpublishable everywhere else.

None of this is exotic: every rule needed already exists in `bracket_export.py` and
`stats_rigor.py`. The fix is architectural — route site-facing numbers through the
same `gated`/`est` layer and emit a methodology page from the code (PoC-S1) — not a
per-page copy-edit.

## 8. Bracket / tournament analytics

**What we do.** `scripts/bracket_export.py` is descriptive-only by design: no
seeding math, no advancement probabilities, no head-to-head model ("two separate
records do not make one"). Its inferential hygiene is exemplary.

**What the literature offers, if the refusal is ever relaxed.** The knockout-design
literature (Schwenk 2000; Csató 2017; Hennessy & Glickman 2016) formalises seeding
fairness; Brandes et al. (2025) computes exact advancement probabilities by dynamic
programming — no Monte Carlo needed at n=16 — and conformal win probability
(arXiv:2208.08598) offers finite-sample validity, which fits this project's
refusal-first ethos unusually well: a conformal interval that spans [0,1] IS the
honest "we cannot say". The principled middle ground short of head-to-head claims:
publish *bracket-structure* analytics (who must beat whom; how advancement
probability responds to rating uncertainty, propagating Glicko-2 RD through the DP).
PoC-X5. Until then, the refusal is defensible and should stay labelled as a choice.

## 9. Computer vision

**What we do.** YOLOv8-pose → 68-dim torso-normalised keypoint features → RF/XGBoost
with GroupKFold on image_id (correct leakage control), against ViCoS's 18 classes.

**What the literature says.** The staged plan (sklearn baseline ~80%, references at
90–92%) matches the dataset authors' own trajectory. The field's standard for
skeleton sequences is ST-GCN (Yan 2018), with 2025 combat-sports applications
reporting 96.7%/12 classes — the natural phase 2 once the sklearn baseline is
*measured* (no accuracy number for our own model exists anywhere; until one is
written down the CV pipeline is unvalidated by this review's definition). PoC-X4.

## 10. What the ecosystem already does better than the literature

Worth stating, because the gap list is long and the baseline is not weak:

- **Pre-registered acceptance criteria** (`node_replay.py`: log loss first, "spread
  is never a criterion", null result publishable) — rarer in published sports
  analytics than it should be.
- **A published null result with its funnel** (`decision_criteria.py`: 118 triads
  tested, 0 survive, and the report says the FDR killing a p≈0.01 finding "is the
  correction working").
- **Coverage as distinct from precision** (`stats_rigor.py`) — the
  one-athlete-wearing-the-category's-name problem is real in every small-sport
  dataset and almost never handled; here it is a first-class gate.
- **Measured data-model auditing** (`attribution.py` refuting its own convention at
  63.4% with a stated null) — most event-stream papers never audit their coders.
- **Cross-repo numeric parity by golden fixture** (Glicko-2, constellations,
  directed edges) — reproducibility discipline most academic code lacks.

The project's instinct — refuse rather than overclaim — is the right foundation.
The literature's contribution is mostly to replace refusal with *calibrated* claims
where methods exist, and to replace hand-set constants with fitted ones.
