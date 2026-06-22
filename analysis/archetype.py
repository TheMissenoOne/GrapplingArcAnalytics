"""Archetype clustering — fit KMeans over graph feature vectors, persist labels."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import numpy as np
from sklearn.cluster import KMeans

if TYPE_CHECKING:
    from db.models import GraphNode

logger = logging.getLogger(__name__)

FEATURE_VERSION = "v1"

# Ordered node_type buckets that define the feature vector dimensions.
_TYPES = ["guard", "pass", "sweep", "submission", "takedown", "control", "escape", "transition"]


def graph_feature_vector(nodes: list[GraphNode], edges: list[object] | None = None) -> np.ndarray:
    """Build an L2-normalized feature vector for one graph.

    Dimensions (len = len(_TYPES) + 3):
      - node-type share for each bucket in _TYPES
      - edge_density  = n_edges / max(n_nodes, 1)
      - avg_elo_bucket  = mean(computed_elo) / 400  (scaled to ~[0,5])
      - offense_ratio = (submission + takedown + sweep) / max(total_typed, 1)
    """
    n = len(nodes)
    counts = {t: 0 for t in _TYPES}
    elos: list[float] = []

    for node in nodes:
        t = (node.node_type or "").lower().strip()
        bucket = t if t in counts else "transition"
        counts[bucket] = counts.get(bucket, 0) + 1
        if node.computed_elo is not None:
            elos.append(node.computed_elo)

    shares = np.array([counts[t] / max(n, 1) for t in _TYPES], dtype=np.float64)

    n_edges = len(edges) if edges is not None else 0
    edge_density = n_edges / max(n, 1)

    avg_elo = float(np.mean(elos)) if elos else 0.0
    avg_elo_bucket = avg_elo / 400.0

    offense = (counts.get("submission", 0) + counts.get("takedown", 0) + counts.get("sweep", 0))
    total_typed = sum(counts.values())
    offense_ratio = offense / max(total_typed, 1)

    vec = np.append(shares, [edge_density, avg_elo_bucket, offense_ratio])
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


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
    """Load all graphs from DB, fit archetypes, persist labels and centroids."""
    from db.repository import (
        assign_archetype_to_graph,
        graphs_for_clustering,
        save_archetypes,
    )

    rows = graphs_for_clustering(session)  # type: ignore[arg-type]
    if not rows:
        logger.warning("No graphs found for clustering")
        return

    graph_ids = [r[0] for r in rows]
    vectors = np.array([graph_feature_vector(r[1]) for r in rows])
    km = fit_archetypes(vectors, k=k)

    fv = archetype_feature_version(vectors)
    names = [f"Archetype {i + 1}" for i in range(km.n_clusters)]
    centroids = km.cluster_centers_.tolist()
    archetype_ids = save_archetypes(centroids, names, fv, session)  # type: ignore[arg-type]

    for graph_id, label in zip(graph_ids, km.labels_):
        arch_id = archetype_ids[int(label)]
        assign_archetype_to_graph(graph_id, arch_id, session)  # type: ignore[arg-type]

    logger.info("Archetypes fitted: k=%d, graphs=%d", km.n_clusters, len(graph_ids))
