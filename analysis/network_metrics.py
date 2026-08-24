"""Aggregate transition-network analysis over the whole match corpus — the research-ground
graph engine.

Builds ONE directed weighted network from every ``final`` match's actor-tagged sequence
(nodes = canonical position/technique labels, edge weight = how often one is followed by the
next), then exposes graph metrics on it:

- **centrality / PageRank** — which positions are the hubs of grappling;
- **community detection** — data-driven game families (compare to the KMeans archetypes);
- **Markov reward-risk** (per Lamas et al. 2024, *No-gi BJJ: a Markovian analysis*) — per
  position ``P(→ direct successful submission) − P(→ being directly submitted)``, plus the
  greedy highest-probability route to a finish.

``network_from_sequences`` is **pure** (list of actor-tagged sequences → ``nx.DiGraph``) so it
unit-tests without a DB; ``build_transition_network`` is the thin DB wrapper. All metric
functions are pure on the graph.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np

from analysis.transitions.build_graph import network_from_sequences as network_from_sequences

_SUBMISSION = "submission"

# Directed-edge rendering constants (data-directed graph edges, see kanban/TODO/013).
MIN_EDGE_ARROW = 2       # below this max(f,r) an edge is drawn undirected
TWO_WAY_RATIO = 0.34     # minority share above which a two-way exchange stays undirected
DASH_WEIGHT_MIN = 5      # edges below this weight are never dashed (unknown, not bad)
DASH_SUCCESS_MAX = 0.40  # fixed per-edge success ceiling — below it, dashed (see kanban/TODO/013)
GATED_TARGET_TYPES = frozenset({"submission", "takedown", "sweep", "pass", "escape"})


def build_transition_network(session: Any) -> nx.DiGraph:
    """DB wrapper: aggregate transition network over all ``final`` matches."""
    from export.match_breakdown import _final_matches

    return network_from_sequences([m.sequence or [] for m in _final_matches(session)])


# ── data-directed edges (aggregate graphs: ocean map + career dossiers) ─────────────────────
def edge_arrow(f: int, r: int, min_edge: int = MIN_EDGE_ARROW,
               two_way_ratio: float = TWO_WAY_RATIO) -> bool:
    """Rule-1 arrow decision for an unordered pair with forward/reverse weights ``f``/``r``.

    ``True`` = draw a plain arrow, oriented in the majority direction (caller orients
    ``f >= r`` → forward, else reversed). ``False`` = undirected (either too sparse to call,
    or a genuine two-way exchange — never split into two arrows).
    """
    m, big_m = min(f, r), max(f, r)
    if big_m < min_edge:
        return False
    if m >= min_edge and m >= two_way_ratio * big_m:
        return False
    return True


def edge_dashed(
    weight: int, ok: int, target_type: str, gated_types: frozenset[str] = GATED_TARGET_TYPES,
) -> bool:
    """Rule-2 dash decision, fixed threshold (zero-inflated success data broke the
    data-driven quartile — see kanban/TODO/013): dashed iff ``weight >= DASH_WEIGHT_MIN``,
    ``target_type`` is gated, and ``ok / weight < DASH_SUCCESS_MAX``. Edges below the sample
    floor or of a non-gated target type are never dashed (solid = unknown, not bad)."""
    if weight < DASH_WEIGHT_MIN or target_type not in gated_types:
        return False
    return (ok / weight) < DASH_SUCCESS_MAX


# ── metrics (pure on the graph) ──────────────────────────────────────────────
def weighted_pagerank(
    g: nx.DiGraph,
    alpha: float = 0.85,
    weight_tradeoff: float = 0.5,
    max_iter: int = 100,
    tol: float = 1.0e-6,
    personalization: dict[str, float] | None = None,
) -> dict[str, float]:
    """Weighted PageRank — Zhang, Wang & Yan (2022), "PageRank centrality and
    algorithms for weighted, directed networks", Physica A 586:126438,
    doi:10.1016/j.physa.2021.126438, arXiv:2104.02764 (identifiers verified
    2026-08-23; distinct from Zhang, Yang & Radicchi 2021, the graph-embedding
    survey cited in ``graph_embed.py``).

    Classical PR uses a uniform transition over outgoing *edges*.  WPR replaces
    this with a convex combination of the weighted (strength) and unweighted
    (degree) transition kernels:

        P(i → j) = τ · w_ij / s_i  +  (1 − τ) · a_ij / d_i

    where:
      - w_ij = edge weight, s_i = out-strength (sum of outgoing weights)
      - a_ij = 1 if edge exists, d_i = out-degree (unweighted)
      - τ    = ``weight_tradeoff`` — 1 = pure weighted, 0 = pure topological

    The standard PR teleportation / random-jump mechanism is unchanged.
    """
    nodes = list(g)
    n = len(nodes)
    if n == 0:
        return {}
    idx = {v: i for i, v in enumerate(nodes)}

    # Precompute out-strength and out-degree.
    out_str = np.array(
        [sum(d.get("weight", 1.0) for _, _, d in g.out_edges(v, data=True)) for v in nodes],
        dtype=np.float64,
    )
    out_deg = np.array([g.out_degree(v) for v in nodes], dtype=np.float64)

    # Build the combined transition matrix.
    M = np.zeros((n, n), dtype=np.float64)  # noqa: N806 (literature notation)
    for v in nodes:
        i = idx[v]
        s = out_str[i]
        d = out_deg[i]
        if s == 0 and d == 0:
            # Sink node — distribute uniformly.
            M[i, :] = 1.0 / n
            continue
        for _, u, ed in g.out_edges(v, data=True):
            j = idx[u]
            w = ed.get("weight", 1.0)
            # Weighted contribution.
            if s > 0:
                M[i, j] += weight_tradeoff * w / s
            # Topological contribution.
            if d > 0:
                M[i, j] += (1.0 - weight_tradeoff) * (1.0 / d)
        # Normalize row (handle floating error).
        row_sum = M[i].sum()
        if row_sum > 0:
            M[i] /= row_sum
        else:
            M[i, :] = 1.0 / n

    # Personalization (uniform if not given).
    p = np.ones(n, dtype=np.float64) / n
    if personalization:
        for v, wt in personalization.items():
            if v in idx:
                p[idx[v]] = wt
        p /= p.sum()

    # Power iteration.
    pr = np.ones(n, dtype=np.float64) / n
    for _ in range(max_iter):
        prev = pr.copy()
        pr = alpha * M.T @ pr + (1.0 - alpha) * p
        if np.linalg.norm(pr - prev, 1) < tol:
            break

    return {v: float(pr[i]) for v, i in idx.items()}


def node_centralities(g: nx.DiGraph) -> dict[str, dict[str, float]]:
    """Per-node pagerank (vanilla + weighted Zhang) / eigenvector / betweenness / in+out degree."""
    if g.number_of_nodes() == 0:
        return {}
    pr = nx.pagerank(g, weight="weight")
    wpr = weighted_pagerank(g)
    try:
        eig = nx.eigenvector_centrality_numpy(g, weight="weight")
    except (nx.NetworkXException, ValueError):
        eig = dict.fromkeys(g, 0.0)
    btw = nx.betweenness_centrality(g, weight="dist", normalized=True)
    indeg = dict(g.in_degree(weight="weight"))
    outdeg = dict(g.out_degree(weight="weight"))
    return {
        n: {
            "pagerank": round(pr.get(n, 0.0), 5),
            "weighted_pagerank": round(wpr.get(n, 0.0), 5),
            "eigenvector": round(float(eig.get(n, 0.0)), 5),
            "betweenness": round(btw.get(n, 0.0), 5),
            "in_weight": int(indeg.get(n, 0)),
            "out_weight": int(outdeg.get(n, 0)),
        }
        for n in g.nodes
    }


def pagerank_ranking(g: nx.DiGraph, limit: int = 15) -> list[tuple[str, float]]:
    """Positions ranked by PageRank — the hubs of grappling."""
    pr = node_centralities(g)
    rows = sorted(((n, c["pagerank"]) for n, c in pr.items()), key=lambda x: x[1], reverse=True)
    return rows[:limit]


def weighted_pagerank_ranking(g: nx.DiGraph, limit: int = 15) -> list[tuple[str, float]]:
    """Positions ranked by Weighted PageRank (Zhang 2022)."""
    wpr = weighted_pagerank(g)
    rows = sorted(wpr.items(), key=lambda x: x[1], reverse=True)
    return rows[:limit]


def detect_communities(g: nx.DiGraph, min_occ: int = 1) -> list[list[str]]:
    """Greedy-modularity communities on the (weighted, undirected) network — game families.

    Communities are returned largest-first, members sorted by occurrence within each.
    """
    sub = g.subgraph([n for n, d in g.nodes(data=True) if d.get("occ", 0) >= min_occ])
    if sub.number_of_edges() == 0:
        return [[n] for n in sorted(sub.nodes)]
    und = sub.to_undirected()
    comms = nx.community.greedy_modularity_communities(und, weight="weight")
    # greedy_modularity_communities returns frozensets — sort with the node label as a stable
    # tiebreaker (both within a community and across communities) so ties don't resolve by
    # hash-seed'd set iteration. Without this the downstream systems/hubs/analogues reshuffle
    # every export process (non-deterministic diffs).
    out = [
        sorted(c, key=lambda n: (-g.nodes[n].get("occ", 0), n))
        for c in comms
    ]
    return sorted(out, key=lambda c: (-len(c), c[0]))


def reward_risk_ranking(
    g: nx.DiGraph, min_occ: int = 5, limit: int = 15
) -> list[tuple[str, float, int]]:
    """Positions by reward-risk balance (only nodes seen ≥ ``min_occ`` times), best-first.

    Returns ``(label, reward_risk, occ)``.
    """
    rows = [
        (n, d["reward_risk"], d["occ"])
        for n, d in g.nodes(data=True)
        if d.get("occ", 0) >= min_occ
    ]
    return sorted(rows, key=lambda x: x[1], reverse=True)[:limit]


def route_to_submission(g: nx.DiGraph, start: str, max_steps: int = 6) -> list[str]:
    """Greedy highest-probability walk from ``start`` until a submission node (or a dead end)."""
    if start not in g:
        return []
    path = [start]
    seen = {start}
    node = start
    for _ in range(max_steps):
        if g.nodes[node].get("type") == _SUBMISSION and len(path) > 1:
            break
        outs = [(v, d["weight"]) for _, v, d in g.out_edges(node, data=True) if v not in seen]
        if not outs:
            break
        node = max(outs, key=lambda x: x[1])[0]
        path.append(node)
        seen.add(node)
    return path


# ── Bayesian reward-risk (Lamas 2024 style) ──────────────────────────────────

def _beta_ci(successes: int, trials: int, ci: float = 0.95) -> tuple[float, float, float]:
    """Beta posterior point estimate + credible interval for a binomial proportion.

    Uses Beta(successes + 1, trials - successes + 1) — uniform prior.  Returns
    ``(mean, lower, upper)`` where lower/upper are the ``(1-ci)/2`` and
    ``1-(1-ci)/2`` percentiles of the posterior.
    """
    if trials == 0:
        return (0.0, 0.0, 0.0)
    from scipy.stats import beta as beta_dist
    a = successes + 1
    b = trials - successes + 1
    mean = a / (a + b)
    lo = beta_dist.ppf((1.0 - ci) / 2.0, a, b)
    hi = beta_dist.ppf(1.0 - (1.0 - ci) / 2.0, a, b)
    return (mean, lo, hi)


def reward_risk_with_ci(
    g: nx.DiGraph, min_occ: int = 5, limit: int = 15, ci: float = 0.95
) -> list[tuple[str, float, float, float, int, int]]:
    """Reward-risk balance with Bayesian credible intervals (Lamas 2024 style).

    For each position, models:
      - reward (own submission from here) as Beta(reward+1, denom-reward+1)
      - risk (opponent submission from here)   as Beta(risk+1, denom-risk+1)

    The trials count is the node's ``denom`` — appearances WITH a successor,
    which is the population ``reward``/``risk`` are counted over (a terminal
    appearance can produce neither). Until 2026-08 this function used ``occ``
    (every appearance) as the trials: the interval then described a different
    population than the point estimate — on a node seen 5 times with 2
    successor-bearing appearances, the "5-trial" interval was both too narrow
    and centred too low. Found by an external PoC review, verified against
    ``transitions/build_graph.py``; see docs/research/05_EXTERNAL_POC_REVIEW.md.

    Gating: ``min_occ`` is applied to ``denom`` — the inferential sample size —
    not to ``occ``, which is display context. Both ship in the row.

    Returns ``[(label, point_estimate, ci_lower, ci_upper, occ, denom), ...]``
    sorted best-first by the point estimate.
    """
    rows: list[tuple[str, float, float, float, int, int]] = []
    for n, d in g.nodes(data=True):
        occ = d.get("occ", 0)
        denom = d.get("denom", 0)
        if denom < min_occ:
            continue
        r = d.get("reward", 0)
        rk = d.get("risk", 0)
        r_mean, r_lo, r_hi = _beta_ci(r, denom, ci)
        k_mean, k_lo, k_hi = _beta_ci(rk, denom, ci)
        point = r_mean - k_mean
        ci_lo = r_lo - k_hi  # worst case: low reward, high risk
        ci_hi = r_hi - k_lo  # best  case: high reward, low risk
        rows.append((n, round(point, 3), round(ci_lo, 3), round(ci_hi, 3), occ, denom))
    return sorted(rows, key=lambda x: x[1], reverse=True)[:limit]
