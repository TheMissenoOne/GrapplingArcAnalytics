"""Archetype clustering — fit KMeans over graph feature vectors, persist labels."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import Protocol

import numpy as np
from sklearn.cluster import KMeans

from analysis.deviance import TYPES as _TYPES
from analysis.deviance import Stats, type_deviance_vector

logger = logging.getLogger(__name__)

# v3: the ELO signal is now PROPORTIONAL DEVIANCE (per-type mean z-score vs the population),
# replacing the misleading raw avg_elo/400 — archetypes reflect what a grappler is *relatively*
# elite at, not which universally-high-ELO nodes they happen to touch. New vector shape +
# semantics → old (v1/v2) centroids must not be reused.
FEATURE_VERSION = "v3"

# How much the relative-strength (deviance) block outweighs raw composition in clustering.
DEVIANCE_WEIGHT = 1.5

# Minimum nodes for a graph to be archetyped (skip empty leaderboard-seeded athletes).
MIN_GRAPH_NODES = 3

# node_type → human noun for naming archetypes from their dominant deviance dimensions.
_TYPE_NOUN = {
    "guard": "Guard", "pass": "Passing", "sweep": "Sweep", "submission": "Submission",
    "takedown": "Takedown", "control": "Control", "escape": "Escape", "transition": "Scramble",
}


class _NodeLike(Protocol):
    """Structural type for a graph node — satisfied by db.repository.DerivedNode
    (nodes are reconstructed from edges + the shared library; graph_nodes is gone)."""

    node_key: str
    node_type: str
    computed_elo: float | None


def graph_feature_vector(
    nodes: Sequence[_NodeLike],
    by_key: Stats,
    by_type: Stats,
    edges: list[object] | None = None,
    deviance_weight: float = DEVIANCE_WEIGHT,
) -> np.ndarray:
    """Build an L2-normalized feature vector for one graph (v3).

    Dimensions (len = 2*len(_TYPES) + 2 = 18):
      - node-type share for each bucket in _TYPES                  (composition, 8)
      - per-type proportional deviance * deviance_weight           (relative strength, 8)
      - edge_density  = n_edges / max(n_nodes, 1)
      - offense_ratio = (submission + takedown + sweep) / max(total_typed, 1)

    ``by_key`` / ``by_type`` are the population stats from ``analysis.deviance``.
    """
    n = len(nodes)
    counts = {t: 0 for t in _TYPES}
    for node in nodes:
        t = (node.node_type or "").lower().strip()
        counts[t if t in counts else "transition"] += 1

    shares = np.array([counts[t] / max(n, 1) for t in _TYPES], dtype=np.float64)
    deviance = np.array(type_deviance_vector(nodes, by_key, by_type), dtype=np.float64)

    n_edges = len(edges) if edges is not None else 0
    edge_density = n_edges / max(n, 1)
    offense = counts["submission"] + counts["takedown"] + counts["sweep"]
    total_typed = sum(counts.values())
    offense_ratio = offense / max(total_typed, 1)

    vec = np.concatenate([shares, deviance * deviance_weight, [edge_density, offense_ratio]])
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def name_archetype(centroid: np.ndarray, min_deviance: float = 1e-3) -> str:
    """Name a cluster from its centroid's dominant per-type deviance dims (v3 layout).

    Deviance block = indices [len(_TYPES) : 2*len(_TYPES)). The top one or two positive
    types name the archetype (e.g. "Submission / Control Specialist"); if no type stands
    out, fall back to the dominant composition share ("Guard-Based"), else "Balanced"."""
    nt = len(_TYPES)
    dev = centroid[nt : 2 * nt]
    ranked = sorted(
        ((_TYPES[i], float(dev[i])) for i in range(nt)), key=lambda kv: kv[1], reverse=True
    )
    top = [t for t, v in ranked if v > min_deviance][:2]
    if top:
        nouns = " / ".join(_TYPE_NOUN[t] for t in top)
        return f"{nouns} Specialist"
    shares = centroid[:nt]
    j = int(np.argmax(shares))
    return f"{_TYPE_NOUN[_TYPES[j]]}-Based" if shares[j] > 0 else "Balanced"


def fit_archetypes(
    vectors: np.ndarray,
    k: int = 6,
    random_state: int = 42,
) -> KMeans:
    """Fit KMeans on graph feature vectors. Returns fitted model."""
    if len(vectors) < k:
        k = max(1, len(vectors))
        logger.warning("Fewer graphs than k; reducing k to %d", k)
    km = KMeans(n_clusters=k, random_state=random_state, n_init="auto")
    km.fit(vectors)
    return km


def assign_archetype(vec: np.ndarray, centroids: np.ndarray) -> int:
    """Nearest centroid (L2). Returns 0-based cluster index."""
    dists = np.linalg.norm(centroids - vec, axis=1)
    return int(np.argmin(dists))


def archetype_feature_version(vectors: np.ndarray) -> str:
    """Stable hash of the vector shape for versioning."""
    digest = hashlib.md5(str(vectors.shape).encode()).hexdigest()[:8]
    return f"{FEATURE_VERSION}-{digest}"


def run_archetype_pipeline(session: object, k: int = 6) -> None:
    """Cluster pro-athlete graphs by relative-strength profile, persist named archetypes.

    Population baseline + per-graph feature vectors both come from the **athlete** graphs
    (the real-grappler population); clusters are named from their dominant deviance types.
    """
    from analysis.deviance import node_population_stats
    from db.repository import (
        assign_archetype_to_graph,
        clear_archetypes,
        graphs_for_clustering,
        save_archetypes,
    )

    all_rows = graphs_for_clustering(session, owner_kind="athlete")  # type: ignore[arg-type]
    # Drop empty/near-empty graphs (leaderboard-seeded athletes with no matches) — their
    # zero vectors would swamp one cluster and distort the population baseline.
    rows = [(gid, nodes) for gid, nodes in all_rows if len(nodes) >= MIN_GRAPH_NODES]
    if not rows:
        logger.warning("No non-empty athlete graphs found for clustering")
        return

    by_key, by_type = node_population_stats(rows)
    graph_ids = [r[0] for r in rows]
    vectors = np.array([graph_feature_vector(r[1], by_key, by_type) for r in rows])
    km = fit_archetypes(vectors, k=k)

    clear_archetypes(session)  # type: ignore[arg-type]  # drop prior-run (stale) archetypes
    fv = archetype_feature_version(vectors)
    centroids = km.cluster_centers_.tolist()
    names = [name_archetype(c) for c in km.cluster_centers_]
    archetype_ids = save_archetypes(centroids, names, fv, session)  # type: ignore[arg-type]

    for graph_id, label in zip(graph_ids, km.labels_):
        arch_id = archetype_ids[int(label)]
        assign_archetype_to_graph(graph_id, arch_id, session)  # type: ignore[arg-type]

    logger.info("Archetypes fitted: k=%d, athlete graphs=%d, names=%s",
                km.n_clusters, len(graph_ids), names)
