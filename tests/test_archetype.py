"""Archetype clustering tests (v4 — deviance feature) — no DB required."""

from __future__ import annotations

import numpy as np

from analysis.archetype import (
    ArchetypeRef,
    archetype_feature_version,
    assign_archetype,
    assign_user_archetype,
    compare_feature_vectors,
    fit_archetypes,
    graph_feature_vector,
    name_archetype,
    nearest_archetype,
)
from analysis.deviance import TYPES

# v4 feature length: composition (8) + per-type deviance (8) + edge_density + offense_ratio.
FEATURE_LEN = 2 * len(TYPES) + 2
_EMPTY: tuple[dict, dict] = ({}, {})  # no population stats → deviance block falls to 0


class _FakeNode:
    def __init__(self, node_type: str, computed_elo: float | None = None, key: str = "") -> None:
        self.node_key = key or f"{node_type}-node"
        self.node_type = node_type
        self.computed_elo = computed_elo


class _Edge:
    def __init__(self, source_key: str, target_key: str) -> None:
        self.source_key = source_key
        self.target_key = target_key


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


def test_feature_version_uses_v4_prefix():
    assert archetype_feature_version(np.zeros((2, FEATURE_LEN))).startswith("v4-")


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


def test_mount_and_mount_plus_jab_have_identical_features():
    mount = _FakeNode("control", 1000.0, key="mount")
    jab = _FakeNode("strike", 3000.0, key="jab")
    mount_only = graph_feature_vector([mount], *_EMPTY, edges=[_Edge("mount", "mount")])
    padded = graph_feature_vector(
        [mount, jab],
        *_EMPTY,
        edges=[_Edge("mount", "mount"), _Edge("mount", "jab"), _Edge("jab", "jab")],
    )

    assert np.allclose(mount_only, padded)


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


def test_strike_only_padding_cannot_make_graph_archetype_eligible():
    nodes = [
        _FakeNode("control", key="mount"),
        _FakeNode("guard", key="closed guard"),
        _FakeNode("strike", key="jab"),
        _FakeNode("strike", key="uppercut"),
        _FakeNode("strike", key="elbow"),
    ]

    assert assign_user_archetype(nodes, {}, {}, [ArchetypeRef(1, "X", [], np.zeros(18))]) is None


def test_colliding_display_names_are_qualified_not_numbered() -> None:
    """Two clusters that emphasise the same node type get the same name from
    name_archetype. Slug dedupe hides that in the DB and it surfaces on the site as two
    athletes wearing one label, so the display name must disambiguate too."""
    import numpy as np

    from analysis.archetype import _TYPES, _dedupe_names

    nt = len(_TYPES)
    # both "pass"-dominant in deviance (same name), differing in composition share
    a = np.zeros(2 * nt)
    b = np.zeros(2 * nt)
    a[nt + _TYPES.index("pass")] = 0.9
    b[nt + _TYPES.index("pass")] = 0.8
    a[_TYPES.index("guard")] = 0.7
    b[_TYPES.index("control")] = 0.7

    out = _dedupe_names(["Passing Specialist", "Passing Specialist"], [a.tolist(), b.tolist()])
    assert len(set(out)) == 2, "colliding names must end up distinct"
    assert all("Passing Specialist" in n for n in out), "the shared root must survive"
    assert not any(n.endswith(" 2") for n in out), "should qualify by axis, not number"


def test_unique_names_are_left_alone() -> None:
    import numpy as np

    from analysis.archetype import _TYPES, _dedupe_names

    v = np.zeros(2 * len(_TYPES)).tolist()
    names = ["Passing Specialist", "Control-Based"]
    assert _dedupe_names(names, [v, v]) == names


# ── interpretability baseline report (scripts/research/archetype_interpretability.py) ────

def _synthetic_population():
    """9 eligible graphs in 3 well-separated groups + 1 excluded — no DB."""
    from scripts.research.archetype_interpretability import ClusterPopulation

    groups = {
        1: ["guard"] * 6 + ["control"] * 2,
        2: ["submission"] * 6 + ["takedown"] * 2,
        3: ["pass"] * 6 + ["escape"] * 2,
    }
    rows, persisted, athlete_names, archetypes = [], {}, {}, {}
    for aid, types in groups.items():
        archetypes[aid] = {
            "name": f"Cluster {aid}",
            "key": f"cluster-{aid}",
            "signature_types": [],
            "centroid_vec": graph_feature_vector(_nodes(types), *_EMPTY),
        }
        for i in range(3):
            gid = f"g{aid}-{i}"
            rows.append((gid, _nodes(types)))
            persisted[gid] = aid
            athlete_names[gid] = (f"athlete-{aid}-{i}", f"Athlete {aid}-{i}")
    excluded = [("gx-empty", "0 grappling node(s)")]
    return ClusterPopulation(
        rows=rows, excluded=excluded, persisted=persisted, archetypes=archetypes,
        athlete_names=athlete_names, by_key={}, by_type={},
    )


def test_build_report_shape():
    from scripts.research.archetype_interpretability import build_report_from_population

    report = build_report_from_population(_synthetic_population(), k=3, n_bootstrap=5, seed=42)
    assert set(report) == {
        "feature_version_prefix", "k", "n_bootstrap", "n_graphs_total", "n_graphs_eligible",
        "clusters", "stability", "exemplars", "refusal", "feature_dims",
    }
    assert report["n_graphs_eligible"] == 9
    assert report["n_graphs_total"] == 10
    assert len(report["clusters"]) == 3
    assert all(c["size"] == 3 for c in report["clusters"])
    assert len(report["feature_dims"]) == FEATURE_LEN
    for aid in ("1", "2", "3"):
        assert len(report["exemplars"][aid]) == 3
    assert report["refusal"]["insufficient_evidence"]["count"] == 1
    assert report["refusal"]["insufficient_evidence"]["graph_ids"] == ["gx-empty"]
    assert report["refusal"]["no_assignment"]["count"] == 0
    stab = report["stability"]
    assert stab["n_graphs"] == 9
    assert 0.0 <= stab["mean_ari"] <= 1.0
    assert set(stab["churn_by_graph"]) <= {gid for gid, _ in _synthetic_population().rows}


def test_build_report_deterministic():
    from scripts.research.archetype_interpretability import build_report_from_population

    r1 = build_report_from_population(_synthetic_population(), k=3, n_bootstrap=5, seed=42)
    r2 = build_report_from_population(_synthetic_population(), k=3, n_bootstrap=5, seed=42)
    assert r1 == r2


def test_resample_ari_identity_is_one():
    from sklearn.cluster import KMeans

    from scripts.research.archetype_interpretability import _resample_ari

    rng = np.random.default_rng(0)
    vectors = np.vstack([
        rng.normal(loc=[0, 0, 0], scale=0.05, size=(5, 3)),
        rng.normal(loc=[5, 0, 0], scale=0.05, size=(5, 3)),
        rng.normal(loc=[0, 5, 0], scale=0.05, size=(5, 3)),
    ])
    seed = 7
    persisted = KMeans(n_clusters=3, random_state=seed, n_init="auto").fit_predict(vectors)
    idx = np.arange(len(vectors))

    ari, _boot_labels = _resample_ari(vectors, persisted, idx, k=3, seed=seed)
    assert ari == 1.0
