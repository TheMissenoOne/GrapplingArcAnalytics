"""Tests for graph embedding functions."""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.graph_embed import (
    embed_technique_graph,
    graph_vector,
    node_vector,
    stack_vectors,
    walk_based_fighter_vector,
)


def _make_graph(athlete: str, counts: dict[str, int]) -> AthleteGraph:
    return AthleteGraph(
        athlete=athlete,
        nodes={
            label: AthleteNode(label=label, type="guard", count=cnt)
            for label, cnt in counts.items()
        },
    )


class TestGraphVector:
    def test_l1_normalized(self) -> None:
        g = _make_graph("a", {"closed guard": 3, "mount": 1})
        vec = graph_vector(g, vocab=["closed guard", "mount", "armbar"])
        assert vec.shape == (3,)
        assert vec.sum() == pytest.approx(1.0)
        assert vec[0] == pytest.approx(0.75)
        assert vec[1] == pytest.approx(0.25)
        assert vec[2] == pytest.approx(0.0)

    def test_respects_vocab_order(self) -> None:
        g = _make_graph("a", {"armbar": 5, "closed guard": 2, "mount": 1})
        v1 = graph_vector(g, vocab=["closed guard", "mount", "armbar"])
        v2 = graph_vector(g, vocab=["armbar", "mount", "closed guard"])
        assert v1[0] == pytest.approx(2 / 8)
        assert v2[0] == pytest.approx(5 / 8)

    def test_out_of_vocab_labels_excluded(self) -> None:
        g = _make_graph("a", {"closed guard": 3, "unknown_tech": 10})
        vec = graph_vector(g, vocab=["closed guard", "mount"])
        assert vec.sum() == pytest.approx(1.0)
        assert vec[0] == pytest.approx(1.0)
        assert vec[1] == pytest.approx(0.0)

    def test_empty_graph_all_zeros(self) -> None:
        g = _make_graph("a", {})
        vec = graph_vector(g, vocab=["closed guard", "mount"])
        assert np.all(vec == 0.0)
        assert vec.sum() == pytest.approx(0.0)


class TestNodeVector:
    def test_matches_out_distribution_on_vocab(self) -> None:
        # Graph with edges: closed guard -> sweep (2), closed guard -> armbar (1)
        g = AthleteGraph(
            athlete="a",
            nodes={
                "closed guard": AthleteNode("closed guard", "guard", 3),
                "sweep": AthleteNode("sweep", "sweep", 2),
                "armbar": AthleteNode("armbar", "submission", 1),
            },
        )
        g.edges = {
            ("closed guard", "sweep"): AthleteEdge(
                source="closed guard", target="sweep", count=2,
            ),
            ("closed guard", "armbar"): AthleteEdge(
                source="closed guard", target="armbar", count=1,
            ),
        }

        vocab = ["sweep", "armbar", "mount"]
        vec = node_vector(g, "closed guard", vocab)
        assert vec.shape == (3,)
        assert vec[0] == pytest.approx(2 / 3)
        assert vec[1] == pytest.approx(1 / 3)
        assert vec[2] == pytest.approx(0.0)

    def test_absent_label(self) -> None:
        g = _make_graph("a", {"closed guard": 3})
        vec = node_vector(g, "nonexistent", vocab=["closed guard", "mount"])
        assert np.all(vec == 0.0)


class TestStackVectors:
    def test_shape(self) -> None:
        g1 = _make_graph("a", {"mount": 1})
        g2 = _make_graph("b", {"guard": 2})
        stacked = stack_vectors([g1, g2], vocab=["mount", "guard"])
        assert stacked.shape == (2, 2)
        assert stacked[0, 0] == pytest.approx(1.0)
        assert stacked[1, 1] == pytest.approx(1.0)

    def test_single_graph(self) -> None:
        g = _make_graph("a", {"mount": 1})
        stacked = stack_vectors([g], vocab=["mount"])
        assert stacked.shape == (1, 1)

    def test_empty_graphs_keeps_2d_shape(self) -> None:
        stacked = stack_vectors([], vocab=["mount", "guard", "back"])
        assert stacked.shape == (0, 3)


class TestWalkPath:
    """The walk path had never run: the start distribution was passed to
    np.random.choice unnormalised, so any graph big enough to walk raised
    ValueError and only the small-graph fallback was ever exercised."""

    def _graph(self) -> nx.DiGraph:
        g = nx.DiGraph()
        for a, b in [("guard", "sweep"), ("sweep", "mount"), ("mount", "armbar"),
                     ("guard", "armbar"), ("armbar", "guard")]:
            g.add_edge(a, b, weight=2.0)
        return g

    def test_walk_path_runs_on_a_walkable_graph(self) -> None:
        emb, nodes = embed_technique_graph(self._graph())
        assert emb.shape == (4, 16)
        assert set(nodes) == {"guard", "sweep", "mount", "armbar"}

    def test_embedding_is_reproducible(self) -> None:
        e1, n1 = embed_technique_graph(self._graph())
        e2, n2 = embed_technique_graph(self._graph())
        assert n1 == n2
        assert np.allclose(e1, e2), "seeded walks must give one embedding per graph"

    def test_fighter_vector_is_reproducible_and_normalised(self) -> None:
        seqs = [[{"label": lbl, "type": "control"} for lbl in
                 ("mount", "back control", "armbar", "side control", "mount")]] * 3
        v1 = walk_based_fighter_vector(seqs)
        v2 = walk_based_fighter_vector(seqs)
        assert v1.shape == (16,)
        assert np.allclose(v1, v2)
        assert np.isclose(np.linalg.norm(v1), 1.0), "documented as a unit profile vector"


class TestCrossGraphQuarantine:
    def test_cross_graph_svd_similarity_refuses(self) -> None:
        """Independent SVD bases are sign/rotation-arbitrary: the cross-graph
        cosine measured the coordinate lottery, not the fighters (external PoC
        review: -0.85..+0.85 under geometry-preserving sign flips). The function
        must refuse rather than return a different quantity under the old name."""
        import networkx as nx
        import pytest as _pytest

        from analysis.graph_embed import fighter_embedding_similarity
        g = nx.DiGraph()
        g.add_edge("guard", "sweep", weight=1.0)
        with _pytest.raises(NotImplementedError):
            fighter_embedding_similarity(g, g)

    def test_sign_flip_preserves_within_graph_geometry(self) -> None:
        """The trap, demonstrated: flipping a component's sign changes NOTHING
        within one graph's own space — which is exactly why a cross-graph cosine
        between two such spaces carries no information about the graphs."""
        import networkx as nx
        g = nx.DiGraph()
        for a, b in [("guard", "sweep"), ("sweep", "mount"), ("mount", "armbar"),
                     ("guard", "armbar"), ("armbar", "guard")]:
            g.add_edge(a, b, weight=2.0)
        emb, _ = embed_technique_graph(g)
        flipped = emb.copy()
        flipped[:, 0] *= -1.0
        assert np.allclose(emb @ emb.T, flipped @ flipped.T)
