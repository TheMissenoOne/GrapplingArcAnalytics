"""Archetype clustering tests (v3 — deviance feature) — no DB required."""

from __future__ import annotations

import numpy as np

from analysis.archetype import (
    ArchetypeRef,
    assign_archetype,
    assign_user_archetype,
    compare_feature_vectors,
    fit_archetypes,
    graph_feature_vector,
    name_archetype,
    nearest_archetype,
)
from analysis.deviance import TYPES

# v3 feature length: composition (8) + per-type deviance (8) + edge_density + offense_ratio.
FEATURE_LEN = 2 * len(TYPES) + 2
_EMPTY: tuple[dict, dict] = ({}, {})  # no population stats → deviance block falls to 0


class _FakeNode:
    def __init__(self, node_type: str, computed_elo: float | None = None, key: str = "") -> None:
        self.node_key = key or f"{node_type}-node"
        self.node_type = node_type
        self.computed_elo = computed_elo


def _nodes(types: list[str]) -> list[_FakeNode]:
    return [_FakeNode(t, 1000.0, key=f"{t}-{i}") for i, t in enumerate(types)]


def test_feature_vector_shape():
    vec = graph_feature_vector(_nodes(["guard", "submission", "pass"]), *_EMPTY)
    assert vec.shape == (FEATURE_LEN,)


def test_feature_vector_normalized():
    vec = graph_feature_vector(_nodes(["guard"] * 5 + ["submission"] * 3), *_EMPTY)
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-6


def test_feature_vector_empty():
    vec = graph_feature_vector([], *_EMPTY)
    assert vec.shape == (FEATURE_LEN,)
    assert np.linalg.norm(vec) == 0.0


def test_deviance_block_shifts_vector():
    # Same composition, but a positive population deviance on the guard nodes must move the
    # vector (relative strength now matters, not just node-type share).
    nodes = _nodes(["guard"] * 4)
    flat = graph_feature_vector(nodes, *_EMPTY)
    by_key = {n.node_key: (800.0, 100.0, 5) for n in nodes}  # athlete (1000) is +2σ
    strong = graph_feature_vector(nodes, by_key, {})
    assert not np.allclose(flat, strong)


def test_fit_archetypes_basic():
    vectors = np.random.default_rng(0).random((20, FEATURE_LEN))
    km = fit_archetypes(vectors, k=4)
    assert km.n_clusters == 4
    assert len(km.labels_) == 20


def test_fit_archetypes_fewer_than_k():
    vectors = np.random.default_rng(1).random((3, FEATURE_LEN))
    km = fit_archetypes(vectors, k=6)
    assert km.n_clusters <= 3


def test_assign_archetype_nearest():
    centroids = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    assert assign_archetype(np.array([0.9, 0.1, 0.0]), centroids) == 0


def test_name_archetype_from_deviance():
    # Build a centroid whose submission-deviance dim dominates.
    c = np.zeros(FEATURE_LEN)
    sub = TYPES.index("submission")
    c[len(TYPES) + sub] = 0.9  # deviance block
    assert "Submission" in name_archetype(c)


def test_name_archetype_balanced_fallback():
    assert name_archetype(np.zeros(FEATURE_LEN)) == "Balanced"


def test_guard_heavy_vs_submission_heavy():
    v1 = graph_feature_vector(_nodes(["guard"] * 8 + ["control"] * 2), *_EMPTY)
    v2 = graph_feature_vector(_nodes(["submission"] * 8 + ["takedown"] * 2), *_EMPTY)
    assert not np.allclose(v1, v2)


# ── user-graph archetype match (App Part B) ──────────────────────────────────

def test_compare_identical_has_no_differ():
    v = graph_feature_vector(_nodes(["guard"] * 6 + ["sweep"] * 4), *_EMPTY)
    rep = compare_feature_vectors(v, v)
    assert rep["differ"] == []


def test_compare_flags_composition_gap():
    guard = graph_feature_vector(_nodes(["guard"] * 9 + ["control"]), *_EMPTY)
    subs = graph_feature_vector(_nodes(["submission"] * 9 + ["takedown"]), *_EMPTY)
    rep = compare_feature_vectors(guard, subs)
    assert rep["differ"], "very different games must produce differ entries"
    assert all("delta" in d and "label" in d for d in rep["differ"])


def test_nearest_archetype_prefers_embedding():
    user_vec = graph_feature_vector(_nodes(["guard"] * 5), *_EMPTY)
    # Feature centroid says A is nearer; embedding says B — embedding must win.
    a = ArchetypeRef(1, "A", ["guard"], centroid_vec=user_vec, embedding=np.array([1.0, 0.0]))
    b = ArchetypeRef(2, "B", ["submission"], centroid_vec=user_vec * 0 + 99, embedding=np.array([0.0, 1.0]))  # noqa: E501
    got = nearest_archetype(user_vec, [a, b], user_embedding=np.array([0.1, 0.9]))
    assert got.id == 2
    # No embedding → falls back to nearest feature centroid (A).
    assert nearest_archetype(user_vec, [a, b]).id == 1


def test_assign_user_archetype_end_to_end():
    nodes = _nodes(["submission"] * 6 + ["control"] * 4)
    user_vec = graph_feature_vector(nodes, *_EMPTY)
    other = graph_feature_vector(_nodes(["guard"] * 10), *_EMPTY)
    refs = [
        ArchetypeRef(7, "Submission / Control Specialist", ["submission", "control"], centroid_vec=user_vec),  # noqa: E501
        ArchetypeRef(8, "Guard-Based", ["guard"], centroid_vec=other),
    ]
    rep = assign_user_archetype(nodes, {}, {}, refs)
    assert rep is not None
    assert rep["archetype_id"] == 7  # nearest by feature centroid
    assert set(rep) >= {"archetype_id", "name", "similar", "differ", "signature"}
    assert set(rep["signature"]) == {"shared", "missing", "extra"}


def test_assign_user_archetype_skips_tiny_graph():
    assert assign_user_archetype(_nodes(["guard"]), {}, {}, [ArchetypeRef(1, "X", [], np.zeros(18))]) is None  # noqa: E501
    assert assign_user_archetype(_nodes(["guard"] * 5), {}, {}, []) is None
