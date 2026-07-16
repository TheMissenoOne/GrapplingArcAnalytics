"""Archetype clustering — fit KMeans over graph feature vectors, persist labels."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score

from analysis.deviance import TYPES as _TYPES
from analysis.deviance import Stats, grappling_nodes, type_deviance_vector

logger = logging.getLogger(__name__)

# v4: the ELO signal is now PROPORTIONAL DEVIANCE (per-type mean z-score vs the population),
# replacing the misleading raw avg_elo/400 — archetypes reflect what a grappler is *relatively*
# elite at, not which universally-high-ELO nodes they happen to touch. New vector shape +
# semantics → old (v1/v2) centroids must not be reused.
FEATURE_VERSION = "v4"

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
    """Build an L2-normalized feature vector for one graph (v4).

    Dimensions (len = 2*len(_TYPES) + 2 = 18):
      - node-type share for each bucket in _TYPES                  (composition, 8)
      - per-type proportional deviance * deviance_weight           (relative strength, 8)
      - edge_density  = n_edges / max(n_nodes, 1)
      - offense_ratio = (submission + takedown + sweep) / max(total_typed, 1)

    ``by_key`` / ``by_type`` are the population stats from ``analysis.deviance``.
    """
    nodes = grappling_nodes(nodes)
    n = len(nodes)
    counts = {t: 0 for t in _TYPES}
    for node in nodes:
        t = (node.node_type or "").lower().strip()
        counts[t if t in counts else "transition"] += 1

    shares = np.array([counts[t] / max(n, 1) for t in _TYPES], dtype=np.float64)
    deviance = np.array(type_deviance_vector(nodes, by_key, by_type), dtype=np.float64)

    retained_keys = {node.node_key for node in nodes}
    n_edges = sum(
        1
        for edge in edges or []
        if getattr(edge, "source_key", None) in retained_keys
        and getattr(edge, "target_key", None) in retained_keys
    )
    edge_density = n_edges / max(n, 1)
    offense = counts["submission"] + counts["takedown"] + counts["sweep"]
    total_typed = sum(counts.values())
    offense_ratio = offense / max(total_typed, 1)

    vec = np.concatenate([shares, deviance * deviance_weight, [edge_density, offense_ratio]])
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def signature_types(centroid: np.ndarray, min_deviance: float = 1e-3) -> list[str]:
    """Top one or two positive per-type deviance dims of a centroid (v4 layout).

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


# ── User-graph archetype match + structural similar/differ (App Part B) ───────
#
# A user graph is *assigned* (not clustered): find the nearest archetype, then
# explain the fit with a non-vectorized comparison of the interpretable feature
# dims. The 768-d embedding decides *which* archetype (semantic nearest); the
# 18-d feature vector explains *why* (composition / relative strength / balance).

# Composition-share gaps: below NEAR = "similar", at/above DIFF = "differ".
_COMPOSITION_NEAR = 0.05
_COMPOSITION_DIFF = 0.10
_DEVIANCE_DIFF = 0.15      # per-type relative-strength gap worth calling out
_DENSITY_DIFF = 0.5        # edge-density (interconnectedness) gap worth calling out


@dataclass
class ArchetypeRef:
    """Lightweight archetype record for matching — decoupled from the ORM so the
    pure functions below are unit-testable without a DB."""

    id: int
    name: str
    signature_types: list[str]
    centroid_vec: np.ndarray            # 18-d feature centroid (interpretable dims)
    embedding: np.ndarray | None = field(default=None)  # 768-d semantic centroid


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else -1.0


def nearest_archetype(
    user_vec: np.ndarray,
    archetypes: Sequence[ArchetypeRef],
    user_embedding: np.ndarray | None = None,
) -> ArchetypeRef | None:
    """Pick the archetype closest to the user. Prefers 768-d embedding cosine (the
    semantic space); falls back to nearest 18-d feature centroid (L2) when either
    side lacks an embedding."""
    if not archetypes:
        return None
    if user_embedding is not None:
        # Narrow embedding to non-None inside the comprehension so mypy is happy.
        scored = [
            (a, _cosine(user_embedding, a.embedding))
            for a in archetypes if a.embedding is not None
        ]
        if scored:
            return max(scored, key=lambda t: t[1])[0]
    return min(archetypes, key=lambda a: float(np.linalg.norm(a.centroid_vec - user_vec)))


def _signature_overlap(user_vec: np.ndarray, arch_sig_types: list[str]) -> dict[str, list[str]]:
    """User's emphasized types vs the archetype's — shared / the archetype's you lack
    (missing) / yours it lacks (extra), as human nouns."""
    user_sig = signature_types(user_vec)

    def nouns(ts: list[str]) -> list[str]:
        return [_TYPE_NOUN.get(t, t.title()) for t in ts]

    return {
        "shared": nouns([t for t in user_sig if t in arch_sig_types]),
        "missing": nouns([t for t in arch_sig_types if t not in user_sig]),
        "extra": nouns([t for t in user_sig if t not in arch_sig_types]),
    }


def compare_feature_vectors(
    user_vec: np.ndarray, centroid_vec: np.ndarray
) -> dict[str, list[dict[str, Any]]]:
    """Non-vectorized similar/differ over the v4 feature layout. Each entry is
    ``{aspect, label, delta}``; differ is sorted by |delta| and capped."""
    nt = len(_TYPES)
    similar: list[dict[str, Any]] = []
    differ: list[dict[str, Any]] = []

    def entry(aspect: str, label: str, delta: float) -> dict[str, Any]:
        return {"aspect": aspect, "label": label, "delta": round(delta, 3)}

    for i, t in enumerate(_TYPES):
        noun = _TYPE_NOUN.get(t, t.title()).lower()
        gap = float(user_vec[i] - centroid_vec[i])              # composition share
        if abs(gap) < _COMPOSITION_NEAR and max(user_vec[i], centroid_vec[i]) > 0.12:
            similar.append(entry("composition", f"both build around {noun}", gap))
        elif abs(gap) >= _COMPOSITION_DIFF:
            more = "more" if gap > 0 else "less"
            differ.append(entry("composition", f"you use {noun} {more}", gap))
        dgap = float(user_vec[nt + i] - centroid_vec[nt + i])   # relative strength (deviance)
        if abs(dgap) >= _DEVIANCE_DIFF:
            word = "stronger" if dgap > 0 else "weaker"
            differ.append(entry("strength", f"your {noun} is {word} vs peers", dgap))

    off_gap = float(user_vec[2 * nt + 1] - centroid_vec[2 * nt + 1])  # offense_ratio
    if abs(off_gap) >= _COMPOSITION_DIFF:
        word = "more" if off_gap > 0 else "less"
        differ.append(entry("offense", f"you are {word} finish-oriented", off_gap))
    elif abs(off_gap) < _COMPOSITION_NEAR:
        similar.append(entry("offense", "similar offense/defense balance", off_gap))

    dens_gap = float(user_vec[2 * nt] - centroid_vec[2 * nt])         # edge_density
    if abs(dens_gap) >= _DENSITY_DIFF:
        word = "more" if dens_gap > 0 else "less"
        differ.append(entry("connectivity", f"your game is {word} interconnected", dens_gap))

    differ.sort(key=lambda d: abs(d["delta"]), reverse=True)
    return {"similar": similar[:4], "differ": differ[:5]}


def assign_user_archetype(
    user_nodes: Sequence[_NodeLike],
    by_key: Stats,
    by_type: Stats,
    archetypes: Sequence[ArchetypeRef],
    edges: list[object] | None = None,
    user_embedding: np.ndarray | None = None,
) -> dict[str, Any] | None:
    """Match one user graph to its nearest archetype and explain the fit.

    Returns the ``archetype_report`` payload the App reads:
    ``{archetype_id, name, similar[], differ[], signature{shared,missing,extra}}``,
    or None when the graph is too small / there are no archetypes.
    """
    user_nodes = grappling_nodes(user_nodes)
    if len(user_nodes) < MIN_GRAPH_NODES or not archetypes:
        return None
    user_vec = graph_feature_vector(user_nodes, by_key, by_type, edges)
    match = nearest_archetype(user_vec, archetypes, user_embedding)
    if match is None:
        return None
    report = compare_feature_vectors(user_vec, match.centroid_vec)
    return {
        "archetype_id": match.id,
        "name": match.name,
        "signature": _signature_overlap(user_vec, match.signature_types),
        **report,
    }


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
    rows = [
        (gid, nodes)
        for gid, raw_nodes in all_rows
        if len(nodes := grappling_nodes(raw_nodes)) >= MIN_GRAPH_NODES
    ]
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
