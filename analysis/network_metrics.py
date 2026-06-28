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

from collections import Counter, defaultdict
from typing import Any

import networkx as nx

from analysis.technique_match import clean_label

_SUBMISSION = "submission"


def _events(sequence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise a stored sequence to ``[{label, type, actor, ok}]`` (labels canonicalised)."""
    out: list[dict[str, Any]] = []
    for e in sequence or []:
        label = clean_label(str(e.get("label", "")), str(e.get("type", "")))
        if not label:
            continue
        out.append({
            "label": label,
            "type": str(e.get("type", "")),
            "actor": e.get("actor_id"),
            "ok": bool(e.get("successful", False)),
        })
    return out


def network_from_sequences(sequences: list[list[dict[str, Any]]]) -> nx.DiGraph:
    """Pure builder: actor-tagged sequences → aggregate transition ``DiGraph``.

    **Edges are within-actor** — one fighter's own ordered flow (their next *own* action),
    so the network is a map of real technique transitions, never a cross-fighter artifact
    (e.g. A's escape followed by B's takedown is *not* an edge). Edge ``weight`` = count.

    Markov reward-risk per node (Lamas et al. 2024), over appearances that have a successor
    (a position that simply ends the recorded sequence is excluded from the denominator):
      - **reward**: the fighter's own next action from here is a finished submission;
      - **risk**: the very next event (either fighter) is the *opponent* finishing a submission
        — only when both actors are known (unknown attribution is left neutral, never charged).
    Node attrs: ``type``, ``occ`` (total appearances), ``reward``/``risk``, ``reward_risk``.
    """
    g = nx.DiGraph()
    occ: Counter[str] = Counter()
    denom: Counter[str] = Counter()  # appearances that have a successor
    reward: Counter[str] = Counter()
    risk: Counter[str] = Counter()
    node_type: dict[str, str] = {}

    for seq in sequences:
        events = _events(seq)
        n = len(events)
        for e in events:
            occ[e["label"]] += 1
            node_type.setdefault(e["label"], e["type"])

        # index of each event's next *own*-actor event (None actor = no attributable flow)
        by_actor: dict[Any, list[int]] = defaultdict(list)
        for i, e in enumerate(events):
            if e["actor"] is not None:
                by_actor[e["actor"]].append(i)
        next_own: dict[int, int] = {}
        for idxs in by_actor.values():
            for k in range(len(idxs) - 1):
                next_own[idxs[k]] = idxs[k + 1]
            # within-actor flow edges
            for k in range(len(idxs) - 1):
                a, b = events[idxs[k]]["label"], events[idxs[k + 1]]["label"]
                if a != b:
                    if g.has_edge(a, b):
                        g[a][b]["weight"] += 1
                    else:
                        g.add_edge(a, b, weight=1)

        for i, e in enumerate(events):
            if i + 1 >= n:
                continue  # terminal appearance (no successor) — not in the denominator
            label = e["label"]
            denom[label] += 1
            own = next_own.get(i)
            nxt = events[i + 1]
            if own is not None and events[own]["type"] == _SUBMISSION and events[own]["ok"]:
                reward[label] += 1
            elif (nxt["type"] == _SUBMISSION and nxt["ok"]
                  and e["actor"] is not None and nxt["actor"] is not None
                  and nxt["actor"] != e["actor"]):
                risk[label] += 1

    for label, c in occ.items():
        g.add_node(label)
        g.nodes[label]["type"] = node_type.get(label, "")
        g.nodes[label]["occ"] = c
        g.nodes[label]["reward"] = reward[label]
        g.nodes[label]["risk"] = risk[label]
        d = denom[label]
        g.nodes[label]["reward_risk"] = round((reward[label] - risk[label]) / d, 3) if d else 0.0
    # distance = inverse weight, for shortest-path-based betweenness
    for _, _, ed in g.edges(data=True):
        ed["dist"] = 1.0 / ed["weight"]
    return g


def build_transition_network(session: Any) -> nx.DiGraph:
    """DB wrapper: aggregate transition network over all ``final`` matches."""
    from export.match_breakdown import _final_matches

    return network_from_sequences([m.sequence or [] for m in _final_matches(session)])


# ── metrics (pure on the graph) ──────────────────────────────────────────────
def node_centralities(g: nx.DiGraph) -> dict[str, dict[str, float]]:
    """Per-node pagerank / eigenvector / betweenness / in+out degree (weighted)."""
    if g.number_of_nodes() == 0:
        return {}
    pr = nx.pagerank(g, weight="weight")
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


def detect_communities(g: nx.DiGraph, min_occ: int = 1) -> list[list[str]]:
    """Greedy-modularity communities on the (weighted, undirected) network — game families.

    Communities are returned largest-first, members sorted by occurrence within each.
    """
    sub = g.subgraph([n for n, d in g.nodes(data=True) if d.get("occ", 0) >= min_occ])
    if sub.number_of_edges() == 0:
        return [[n] for n in sub.nodes]
    und = sub.to_undirected()
    comms = nx.community.greedy_modularity_communities(und, weight="weight")
    out = [
        sorted(c, key=lambda n: g.nodes[n].get("occ", 0), reverse=True)
        for c in comms
    ]
    return sorted(out, key=len, reverse=True)


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
