# Graph-analysis approaches for the grappling corpus

A survey + evaluation of network/graph methods we can run on the match data, what each
produces, what existing code to reuse, and how strong a content candidate it is. This is the
"research ground" reference — the runnable engine lives in `analysis/network_metrics.py`,
`analysis/fighter_similarity.py`, and the report in `analysis/insights.py` (→ `docs/insights/`).

## What we have to mine

Per `final` match: an actor-tagged `sequence` of `{label, type, actor_id, successful}` events
(label canonicalised to the technique library). Aggregated, this is a **directed transition
network**: nodes = positions/techniques, edges = "X was followed by Y", typed by who acted. We
also have per-athlete graphs, a 6-axis style fingerprint, KMeans archetypes, and pgvector(768)
+ an in-process Qdrant store (today used only for CV next-move priors).

## The four approaches

### 1. Markov transition + reward-risk  ★ strongest narrative
Per position, the transition probabilities to the next action, and the **reward-risk balance** =
`P(→ direct successful submission) − P(→ being directly submitted)`. Surfaces "which positions
actually lead to finishes" and the highest-probability route to a tap.
- **Directly mirrors** Lamas et al. (2024), *No-gi Brazilian jiu-jitsu: a Markovian analysis of
  elite-level combat dynamics* (Int. J. Sports Sci. & Coaching, doi 10.1177/17479541231210979),
  which ran exactly this on 93 WSFC-2019 matches: guard-pass→guard-pass 0.30, takedown→submission
  0.15, **back-take→submission 0.45** (highest reward). Our corpus is the same shape → we can
  reproduce and extend (per era / division / fighter).
- Reuse: `network_metrics.reward_risk_ranking` / `route_to_submission`.
- Content: "the 0.45 back-take", per-position finish odds, "the map of where matches are won".

### 2. Centrality / PageRank  ★ easy + visual
Rank positions by network importance — a position is important if reached from other important
positions. PageRank, betweenness (bottlenecks), eigenvector, weighted degree.
- Precedent: PageRank in sports networks — Peña & Touchette soccer passing (Springer,
  10.1007/978-3-319-63907-9_16), and PageRank ranking of tennis players / CFB teams / MLB.
- Reuse: `network_metrics.node_centralities` / `pagerank_ranking`.
- Content: "the hubs of grappling" ranking + an interactive position map sized by centrality.

### 3. Fighter DNA — embeddings + similarity  ★ product-y
"Fighters most like X" and data-driven style clusters. Today: cosine over per-athlete
technique-type vectors (`fighter_similarity.py`). Upgrade path: **node2vec / graph embeddings**
(biased random walks → skip-gram) over the transition network, or use the existing
pgvector(768)/Qdrant graph fingerprints already in the DB.
- Precedent: node2vec player similarity; NBA2Vec (arXiv 2302.13386), baller2vec (2102.03291),
  RisingBALLER (2410.00943) — "a player is a token".
- Reuse: `analysis/graph_embed.py`, `analysis/vector_store.py` (Qdrant), pgvector columns.
- node2vec needs an extra dep (gensim or torch-geometric) — **deferred**; the cosine version
  ships now.
- Content: "stylistically similar fighters" sidebar, "if you like X you'll like Y", style maps.

### 4. Community detection — meta-game families  ★ insight
Greedy-modularity / Louvain communities on the (weighted) transition network → data-driven
clusters of positions that flow together (e.g. a leg-lock family, a back-attack family).
Compare against the hand-labeled KMeans archetypes (`analysis/archetype.py`) — agreement
validates the archetypes; divergence is a finding.
- Precedent: Transition Network Analysis (arXiv 2411.15486) — community finding + centrality on
  learner/process transition graphs; general network-science community detection.
- Reuse: `network_metrics.detect_communities`.
- Content: "the families of grappling", archetype validation.

## Competitive landscape (prior art / inspiration)
- **bjjgraph.org** — BJJ as a graph of positions/principles (Submission Chains, Risk Assessment).
- **Graphling** (Medium, @Graphling) — deterministic BJJ graph models.
- Academic combat-sport modelling: Markov MMA forecasting; Markovian judo time-motion models.

## Under-exploited assets (from the module inventory) → content ideas
- pgvector/Qdrant similarity (unexposed) → "fighters like you".
- `elo_series` arcs → "who's rising/falling" timelines.
- 6-axis style fingerprint → interactive radar + fingerprint clustering.
- response patterns → "how to deal with X's guard pass" guides.
- submission-family dominance → leglock/strangle/armlock leaderboards + badges.
- transition hotspots → flow-chain (4-move combo) heat maps.
- weight-class / era trends (`technique_freq`) → "the featherweight meta", era comparisons.

## Recommended phasing
1. **Now (this round):** engine + internal `docs/insights/` report (1, 2, 4 + cosine-3). Decide
   from the numbers what's compelling.
2. **Next:** turn the winning section(s) into a public content type (position map, reward-risk
   rankings, "fighters like X"), wired through `export/site_data.py` like breakdowns/events.
3. **Later:** node2vec/pgvector embeddings for richer similarity; per-era/division Markov deep-dives.

## Sources
- Lamas et al. 2024 — https://doi.org/10.1177/17479541231210979
- PageRank in soccer — https://link.springer.com/chapter/10.1007/978-3-319-63907-9_16
- Transition Network Analysis — https://arxiv.org/pdf/2411.15486
- NBA2Vec — https://arxiv.org/pdf/2302.13386 ; baller2vec — https://arxiv.org/pdf/2102.03291
- bjjgraph.org — https://bjjgraph.org ; Graphling — https://medium.com/@Graphling
