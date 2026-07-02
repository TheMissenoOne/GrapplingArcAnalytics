"""Graph-embedding — vocabulary-based vectors + node2vec-style walk embeddings.

Two layers:
  1. **Vocabulary-based** (legacy API) — ``graph_vector`` / ``node_vector`` /
     ``stack_vectors`` convert ``AthleteGraph`` node counts and edge
     distributions into fixed-dimension vectors using a technique vocabulary.
     Used by ``priors.py`` / ``vector_store.py`` for similarity search.
  2. **Structural embedding** (node2vec-style) — random-walk co-occurrence +
     TruncatedSVD for per-fighter or aggregate technique-graph embeddings.
     Used by ``fighter_similarity.py`` and notebooks.

References
----------
- Zhang, Yang, Radicchi (2021). "Systematic comparison of graph embedding
  methods in practical tasks." *Physica A*.
- Grover & Leskovec (2016). "node2vec: Scalable Feature Learning for Networks."
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np

from analysis.technique_match import clean_label

WALK_LENGTH = 40
N_WALKS = 50
EMBED_DIM = 16
MIN_WALK_NODES = 4


def _biased_random_walk(g: nx.DiGraph, start: str, length: int) -> list[str]:
    """Truncated random walk from *start*, following weighted outgoing edges."""
    walk = [start]
    node = start
    for _ in range(length - 1):
        outs = list(g.out_edges(node, data=True))
        if not outs:
            break
        weights = np.array([ed.get("weight", 1.0) for _, _, ed in outs], dtype=np.float64)
        weights /= weights.sum()
        idx = np.random.choice(len(outs), p=weights)
        node = outs[idx][1]
        walk.append(node)
    return walk


def _co_occurrence_matrix(
    walks: list[list[str]], nodes: list[str]
) -> np.ndarray:
    """Build weighted co-occurrence matrix (window=all pairs in each walk)."""
    idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    cooc = np.zeros((n, n), dtype=np.float64)
    for walk in walks:
        seen = list(dict.fromkeys(walk))  # dedup within walk
        for i in range(len(seen)):
            for j in range(i + 1, len(seen)):
                a, b = idx[seen[i]], idx[seen[j]]
                cooc[a, b] += 1.0
                cooc[b, a] += 1.0
    return cooc


def _normalise(label: str, typ: str) -> str:
    """Canonical label, mirrored from clean_label in network_metrics."""
    return clean_label(str(label), str(typ))


def technique_graph_from_sequences(
    sequences: list[list[dict[str, Any]]],
) -> nx.DiGraph:
    """Build a weighted technique-transition graph from sequences (reuses
    ``network_from_sequences`` logic, but returns a fresh graph for a single
    fighter or aggregate).  Nodes = canonical labels, edges = within-actor
    transitions, weight = count.
    """
    g = nx.DiGraph()
    for seq in sequences:
        events: list[dict[str, Any]] = []
        for e in seq:
            label = _normalise(str(e.get("label", "")), str(e.get("type", "")))
            if not label:
                continue
            events.append({"label": label, "type": str(e.get("type", ""))})
        for i in range(1, len(events)):
            a, b = events[i - 1]["label"], events[i]["label"]
            if a == b:
                continue
            if g.has_edge(a, b):
                g[a][b]["weight"] += 1
            else:
                g.add_edge(a, b, weight=1)
    for n, d in g.nodes(data=True):
        d.setdefault("occ", 0)
    return g


def embed_technique_graph(
    g: nx.DiGraph, dim: int = EMBED_DIM, n_walks: int = N_WALKS,
) -> tuple[np.ndarray, list[str]]:
    """Embed a single technique graph via random-walk co-occurrence → SVD.

    Returns
    -------
    (embedding_matrix, node_labels)
        ``embedding_matrix`` shape ``(n_nodes, dim)`` — each row is a node
        embedding.  Node order corresponds to ``node_labels``.
    """
    nodes = list(g.nodes)
    if len(nodes) < MIN_WALK_NODES:
        # Fall back to one-hot degree features.
        from sklearn.decomposition import PCA
        degs = np.array([g.degree(n, weight="weight") for n in nodes], dtype=np.float64).reshape(-1, 1)
        if len(nodes) <= dim:
            pad = np.zeros((len(nodes), dim - 1))
            emb = np.concatenate([degs, pad], axis=1)
        else:
            emb = PCA(n_components=dim).fit_transform(degs)
        return emb, nodes

    walks: list[list[str]] = []
    for _ in range(n_walks):
        start = np.random.choice(nodes, p=(
            np.array([g.degree(n, weight="weight") for n in nodes], dtype=np.float64) ** 0.75
        ))
        start = str(start)
        walks.append(_biased_random_walk(g, start, WALK_LENGTH))

    cooc = _co_occurrence_matrix(walks, nodes)
    from sklearn.decomposition import TruncatedSVD
    svd = TruncatedSVD(n_components=min(dim, cooc.shape[0] - 1), random_state=42)
    emb = svd.fit_transform(cooc)
    if emb.shape[1] < dim:
        pad = np.zeros((emb.shape[0], dim - emb.shape[1]))
        emb = np.concatenate([emb, pad], axis=1)
    return emb, nodes


def fighter_embedding_similarity(
    g_a: nx.DiGraph, g_b: nx.DiGraph, dim: int = EMBED_DIM,
) -> float:
    """Cosine similarity between two fighters' technique-graph embeddings.

    Embeds each graph independently, then aligns nodes by label and averages
    the pairwise cosines of shared nodes.  Returns 0.0 when there is no shared
    vocabulary.
    """
    emb_a, nodes_a = embed_technique_graph(g_a, dim=dim)
    emb_b, nodes_b = embed_technique_graph(g_b, dim=dim)
    idx_a = {n: i for i, n in enumerate(nodes_a)}
    idx_b = {n: i for i, n in enumerate(nodes_b)}
    shared = [n for n in nodes_a if n in idx_b]
    if not shared:
        return 0.0
    vecs_a = np.array([emb_a[idx_a[n]] for n in shared])
    vecs_b = np.array([emb_b[idx_b[n]] for n in shared])
    norms = np.linalg.norm(vecs_a, axis=1) * np.linalg.norm(vecs_b, axis=1)
    sims = np.sum(vecs_a * vecs_b, axis=1) / np.clip(norms, 1e-10, None)
    return float(np.mean(sims))


def walk_based_fighter_vector(
    sequences: list[list[dict[str, Any]]],
    dim: int = EMBED_DIM,
) -> np.ndarray:
    """Per-fighter embedding from random walks on the *aggregate* transition
    network.  Each fighter is represented by their node-visit profile
    aggregated over all walks seeded from their own technique nodes.

    Returns a single vector of length ``dim``.
    """
    g = technique_graph_from_sequences(sequences)
    nodes = list(g.nodes)
    if len(nodes) < MIN_WALK_NODES:
        return np.zeros(dim)
    walks: list[list[str]] = []
    for _ in range(N_WALKS):
        start = np.random.choice(nodes)
        walks.append(_biased_random_walk(g, start, WALK_LENGTH))
    cooc = _co_occurrence_matrix(walks, nodes)
    from sklearn.decomposition import TruncatedSVD
    svd = TruncatedSVD(n_components=min(dim, cooc.shape[0] - 1), random_state=42)
    _ = svd.fit_transform(cooc)
    return svd.components_[0] if svd.components_.shape[0] > 0 else np.zeros(dim)


# ── Legacy vocabulary-based API (for priors.py / vector_store.py) ─────────────

def graph_vector(graph: Any, vocab: list[str]) -> np.ndarray:
    """L1-normalised technique-frequency vector for an ``AthleteGraph``.

    Parameters
    ----------
    graph : AthleteGraph
    vocab : list[str]
        Ordered list of technique labels (columns of the output vector).

    Returns
    -------
    ndarray, shape ``(len(vocab),)`` — L1 normalised, or all-zero for an empty graph.
    """
    idx = {label: i for i, label in enumerate(vocab)}
    vec = np.zeros(len(vocab), dtype=np.float64)
    total = 0
    for label, node in (graph.nodes or {}).items():
        i = idx.get(label)
        if i is not None:
            vec[i] += node.count
            total += node.count
    if total > 0:
        vec /= total
    return vec


def node_vector(graph: Any, label: str, vocab: list[str]) -> np.ndarray:
    """L1-normalised out-edge distribution from *label* over *vocab* targets.

    Returns all-zero if the label is unknown or has no outgoing edges.
    """
    idx = {lbl: i for i, lbl in enumerate(vocab)}
    vec = np.zeros(len(vocab), dtype=np.float64)
    total = 0
    for (src, tgt), edge in (graph.edges or {}).items():
        if src != label:
            continue
        i = idx.get(tgt)
        if i is not None:
            vec[i] += edge.count
            total += edge.count
    if total > 0:
        vec /= total
    return vec


def stack_vectors(graphs: list[Any], vocab: list[str]) -> np.ndarray:
    """Stack ``graph_vector`` results for many graphs → matrix ``(n_graphs, n_vocab)``."""
    if not graphs:
        return np.empty((0, len(vocab)), dtype=np.float64)
    return np.array([graph_vector(g, vocab) for g in graphs])
