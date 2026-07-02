"""Archetype clustering — fit KMeans over graph feature vectors, persist labels."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score

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


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _dedupe_slugs(slugs: list[str]) -> list[str]:
    """Make slugs unique (two clusters can share a name) by suffixing collisions -2, -3, ..."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for s in slugs:
        seen[s] = seen.get(s, 0) + 1
        out.append(s if seen[s] == 1 else f"{s}-{seen[s]}")
    return out


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


def signature_types(centroid: np.ndarray, min_deviance: float = 1e-3) -> list[str]:
    """Top one or two positive per-type deviance dims of a centroid (v3 layout).

    Deviance block = indices [len(_TYPES) : 2*len(_TYPES)). Returns the emphasized node-type
    slugs (e.g. ["submission","control"]) — empty when no type stands out."""
    nt = len(_TYPES)
    dev = centroid[nt : 2 * nt]
    ranked = sorted(
        ((_TYPES[i], float(dev[i])) for i in range(nt)), key=lambda kv: kv[1], reverse=True
    )
    return [t for t, v in ranked if v > min_deviance][:2]


def name_archetype(centroid: np.ndarray, min_deviance: float = 1e-3) -> str:
    """Name a cluster from its dominant deviance types (e.g. "Submission / Control Specialist").

    If no type stands out, fall back to the dominant composition share ("Guard-Based"),
    else "Balanced"."""
    top = signature_types(centroid, min_deviance)
    if top:
        return f"{' / '.join(_TYPE_NOUN[t] for t in top)} Specialist"
    nt = len(_TYPES)
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

    clear_archetypes(session)  # type: ignore[arg-type]  # drop prior emergent archetypes
    fv = archetype_feature_version(vectors)
    centroids = km.cluster_centers_.tolist()
    names = [name_archetype(c) for c in km.cluster_centers_]
    sig_types = [signature_types(c) for c in km.cluster_centers_]
    keys = _dedupe_slugs([f"emergent-{_slug(n)}" for n in names])
    archetype_ids = save_archetypes(
        centroids, names, keys, sig_types, fv, session  # type: ignore[arg-type]
    )

    for graph_id, label in zip(graph_ids, km.labels_):
        arch_id = archetype_ids[int(label)]
        assign_archetype_to_graph(graph_id, arch_id, session)  # type: ignore[arg-type]

    logger.info("Archetypes fitted: k=%d, athlete graphs=%d, names=%s",
                km.n_clusters, len(graph_ids), names)


# ── Validation: alternative clustering + stability ────────────────────────────

def fit_hdbscan(
    vectors: np.ndarray,
    min_cluster_size: int = 5,
    min_samples: int | None = None,
) -> tuple[np.ndarray | None, float]:
    """HDBSCAN-based archetype detection (alternative to fixed-k KMeans).

    Automatically determines the number of clusters from data density.
    Returns ``(labels, n_clusters_float)`` where labels[i] = -1 means noise.
    """
    from sklearn.cluster import HDBSCAN
    model = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples)
    labels = model.fit_predict(vectors)
    n_clusters = len(set(labels) - {-1})
    return labels, float(n_clusters)


def jaccard_bootstrap_stability(
    vectors: np.ndarray,
    k: int = 6,
    n_iter: int = 100,
    sample_frac: float = 0.8,
    random_state: int = 42,
) -> dict[str, float]:
    """Jaccard bootstrap stability analysis (Hennig 2007).

    Repeatedly samples ``sample_frac`` of the data with replacement, re-runs
    KMeans with fixed k, and measures cluster-wise Jaccard similarity of the
    recovered partition against the original.

    Returns
    -------
    dict with keys:
      - ``mean_jaccard`` — mean pair-wise Jaccard across all bootstrap iterations
      - ``std_jaccard`` — std of Jaccard
      - ``ari_mean`` — mean Adjusted Rand Index
      - ``cluster_jaccards`` — per-cluster mean Jaccard (length k)
      - ``n_noise`` — how many iterations produced a different number of clusters
    """
    rng = np.random.RandomState(random_state)
    n = len(vectors)
    base_km = KMeans(n_clusters=k, random_state=random_state, n_init="auto")
    base_labels = base_km.fit_predict(vectors)

    cluster_jaccards: list[list[float]] = [[] for _ in range(k)]
    aris: list[float] = []
    noise_count = 0

    for _ in range(n_iter):
        idx = rng.choice(n, size=int(n * sample_frac), replace=True)
        sample = vectors[idx]
        if len(sample) < k:
            noise_count += 1
            continue
        km = KMeans(n_clusters=k, random_state=rng.randint(0, 2**31), n_init="auto")
        boot_labels = km.fit_predict(sample)

        # Map boot labels to base labels via Hungarian / majority vote.
        for c in range(k):
            mask = base_labels == c
            if mask.sum() == 0:
                continue
            boot_for_c = boot_labels[idx[mask]]
            majority = np.bincount(boot_for_c).argmax()
            intersection = (boot_labels[idx] == majority) & mask
            union = (boot_labels[idx] == majority) | mask
            j = intersection.sum() / max(union.sum(), 1)
            cluster_jaccards[c].append(j)

        ari = adjusted_rand_score(base_labels[idx], boot_labels)
        aris.append(ari)

    mean_cj = [np.mean(v) if v else 0.0 for v in cluster_jaccards]
    return {
        "mean_jaccard": float(np.mean(mean_cj)),
        "std_jaccard": float(np.std(mean_cj)),
        "ari_mean": float(np.mean(aris)) if aris else 0.0,
        "cluster_jaccards": [round(x, 3) for x in mean_cj],
        "n_noise": noise_count,
    }


def optimal_k_by_stability(
    vectors: np.ndarray,
    k_range: range = range(3, 11),
    n_iter: int = 50,
    random_state: int = 42,
) -> list[dict[str, Any]]:
    """Grid-search over k, returning stability diagnostics for each k.

    Returns list of dicts sorted by ``mean_jaccard`` descending:
      ``[{"k": 6, "mean_jaccard": 0.87, ...}, ...]``
    """
    results: list[dict[str, Any]] = []
    for k in k_range:
        if k >= len(vectors):
            break
        stab = jaccard_bootstrap_stability(vectors, k=k, n_iter=n_iter, random_state=random_state)
        results.append({"k": k, **stab})
    return sorted(results, key=lambda r: r["mean_jaccard"], reverse=True)
