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

import hashlib
from dataclasses import dataclass, field

import networkx as nx


def constellation_fingerprint(members: list[str]) -> str:
    """Deterministic id for a member set — doc 04: ``fingerprint =
    hash(sorted(member_keys))``. Uses ``hashlib.sha256`` rather than builtin
    ``hash()``: Python randomizes ``hash()`` of strings per process
    (``PYTHONHASHSEED``), so it is NOT stable across runs — the one property a
    fingerprint needs. Sorting first makes it order-independent.

    NOT eternal identity. One member joining or leaving changes it completely — it
    is a snapshot key, not a primary key for "the same constellation over time".
    That question is ``lineage.match_lineage`` (Jaccard-matched, stored separately,
    per doc 04's explicit warning not to conflate the two).
    """
    joined = "\x1f".join(sorted(members))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@dataclass
class Constellation:
    """One detected, guaranteed-connected constellation."""

    members: list[str]
    hub: str                 # highest weighted-degree member, ties broken by label
    internal_edges: int
    support: float            # sum of internal edge weight
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        self.fingerprint = constellation_fingerprint(self.members)


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
    # A graph with >=2 nodes and zero edges (all isolated) still yields len(raw) > 1 (one
    # singleton community per node) — nx.community.modularity divides by total degree, which
    # is 0 there. Guard on und actually having edges, not just >1 communities.
    has_modularity = len(raw) > 1 and und.number_of_edges() > 0
    modularity = nx.community.modularity(und, raw, weight="weight") if has_modularity else 0.0

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
