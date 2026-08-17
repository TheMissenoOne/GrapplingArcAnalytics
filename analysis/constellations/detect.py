"""Community detection over a transition graph — topology and frequency only.

ADR-07 (``docs/rating_v2/01_DECISOES.md``): Louvain can return communities that are
internally *disconnected* in the induced subgraph (Traag, Waltman & van Eck 2019,
*From Louvain to Leiden: guaranteeing well-connected communities*) — the exact
failure the validation plan gates on. Every ``Constellation`` this module returns is
guaranteed connected: anything Louvain hands back that isn't gets split into its
connected components, and that split is counted (``rejected_count``/``rejected_rate``)
as a first-class metric, not swallowed. If that rate turns out material, that is the
measured case for switching to Leiden — not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx


@dataclass
class Constellation:
    """One detected, guaranteed-connected constellation."""

    members: list[str]
    hub: str                 # highest weighted-degree member, ties broken by label
    internal_edges: int
    support: float            # sum of internal edge weight


@dataclass
class DetectionResult:
    constellations: list[Constellation]
    modularity: float
    rejected_count: int   # raw Louvain communities that were internally disconnected
    rejected_rate: float  # rejected_count / number of raw Louvain communities


def _undirected_weighted(g: nx.DiGraph) -> nx.Graph:
    """Project a directed transition graph onto an undirected weighted graph
    (forward + reverse weight summed) — what community detection runs on."""
    und = nx.Graph()
    und.add_nodes_from(g.nodes())
    for u, v, d in g.edges(data=True):
        w = float(d.get("weight", 1.0))
        if und.has_edge(u, v):
            und[u][v]["weight"] += w
        else:
            und.add_edge(u, v, weight=w)
    return und


def _hub(und: nx.Graph, members: list[str]) -> str:
    return sorted(members, key=lambda n: (-und.degree(n, weight="weight"), n))[0]


def detect(g: nx.DiGraph, resolution: float = 1.0, seed: int = 42) -> DetectionResult:
    """Detect constellations in ``g``. Topology/frequency only — no rating argument
    exists on this signature, and none should ever be added.

    Deterministic: same graph + same ``resolution``/``seed`` → same partition, every
    run (output is explicitly sorted; Louvain's own randomness is pinned by ``seed``).
    """
    if g.number_of_nodes() == 0:
        return DetectionResult(
            constellations=[], modularity=0.0, rejected_count=0, rejected_rate=0.0,
        )

    und = _undirected_weighted(g)
    raw = nx.community.louvain_communities(und, weight="weight", resolution=resolution, seed=seed)
    modularity = nx.community.modularity(und, raw, weight="weight") if len(raw) > 1 else 0.0

    raw_sorted = sorted((sorted(c) for c in raw), key=lambda c: (-len(c), c[0] if c else ""))

    rejected = 0
    final: list[list[str]] = []
    for comm in raw_sorted:
        if len(comm) <= 1:
            final.append(comm)
            continue
        sub = und.subgraph(comm)
        if nx.is_connected(sub):
            final.append(comm)
        else:
            rejected += 1
            comps = sorted(
                (sorted(comp) for comp in nx.connected_components(sub)),
                key=lambda c: (-len(c), c[0]),
            )
            final.extend(comps)
    final.sort(key=lambda c: (-len(c), c[0] if c else ""))

    constellations = []
    for members in final:
        sub = und.subgraph(members)
        support = sum(d.get("weight", 0.0) for _, _, d in sub.edges(data=True))
        constellations.append(Constellation(
            members=members,
            hub=_hub(und, members),
            internal_edges=sub.number_of_edges(),
            support=round(support, 3),
        ))

    rejected_rate = rejected / len(raw_sorted) if raw_sorted else 0.0
    return DetectionResult(
        constellations=constellations,
        modularity=round(modularity, 5),
        rejected_count=rejected,
        rejected_rate=round(rejected_rate, 5),
    )
