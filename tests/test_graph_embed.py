"""Tests for graph embedding functions."""

from __future__ import annotations

import numpy as np
import pytest

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.graph_embed import graph_vector, node_vector, stack_vectors


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
