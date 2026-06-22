"""Archetype clustering tests — no DB required."""

from __future__ import annotations

import numpy as np

from analysis.archetype import (
    assign_archetype,
    fit_archetypes,
    graph_feature_vector,
)


class _FakeNode:
    def __init__(self, node_type: str, computed_elo: float | None = None) -> None:
        self.node_type = node_type
        self.computed_elo = computed_elo


def _nodes(types: list[str]) -> list[_FakeNode]:
    return [_FakeNode(t, 1000.0) for t in types]


def test_feature_vector_shape():
    nodes = _nodes(["guard", "submission", "pass"])
    vec = graph_feature_vector(nodes)
    # 8 type buckets + 3 scalars = 11 dims
    assert vec.shape == (11,)


def test_feature_vector_normalized():
    nodes = _nodes(["guard"] * 5 + ["submission"] * 3)
    vec = graph_feature_vector(nodes)
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-6


def test_feature_vector_empty():
    vec = graph_feature_vector([])
    assert vec.shape == (11,)
    assert np.linalg.norm(vec) == 0.0


def test_fit_archetypes_basic():
    rng = np.random.default_rng(0)
    vectors = rng.random((20, 11))
    km = fit_archetypes(vectors, k=4)
    assert km.n_clusters == 4
    assert len(km.labels_) == 20


def test_fit_archetypes_fewer_than_k():
    vectors = np.random.default_rng(1).random((3, 11))
    km = fit_archetypes(vectors, k=6)
    assert km.n_clusters <= 3


def test_assign_archetype_nearest():
    centroids = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    vec = np.array([0.9, 0.1, 0.0])
    assert assign_archetype(vec, centroids) == 0


def test_assign_archetype_stable():
    rng = np.random.default_rng(42)
    centroids = rng.random((6, 11))
    vec = centroids[3].copy()
    # Should always assign to own centroid
    assert assign_archetype(vec, centroids) == 3


def test_graph_feature_offense_ratio():
    # All submissions → high offense ratio
    nodes = _nodes(["submission"] * 10)
    vec = graph_feature_vector(nodes)
    # offense_ratio dim is last — unnormalized it's 1.0
    # After normalisation it will be nonzero; just verify vector is nonzero
    assert np.any(vec != 0)


def test_guard_heavy_vs_submission_heavy():
    guard_nodes = _nodes(["guard"] * 8 + ["control"] * 2)
    sub_nodes = _nodes(["submission"] * 8 + ["takedown"] * 2)
    v1 = graph_feature_vector(guard_nodes)
    v2 = graph_feature_vector(sub_nodes)
    # Vectors should differ
    assert not np.allclose(v1, v2)
