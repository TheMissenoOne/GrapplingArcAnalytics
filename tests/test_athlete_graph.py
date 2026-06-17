"""Tests for the athlete graph builder."""

from __future__ import annotations

import pytest

from analysis.athlete_graph import build_athlete_graph, out_distribution


def _session(
    rounds: list[list[dict]],
    topics: list[str] | None = None,
) -> dict:
    topics = topics or ["drilling"]
    return {
        "topics": topics,
        "rounds": [{"entries": r} for r in rounds],
    }


def _entry(label: str, actor: str = "you", typ: str = "guard") -> dict:
    return {"label": label, "type": typ, "actor": actor}


class TestBuildAthleteGraph:
    def test_empty_sessions(self) -> None:
        graph = build_athlete_graph("test_user", [])
        assert graph.athlete == "test_user"
        assert graph.nodes == {}
        assert graph.edges == {}

    def test_node_counts(self) -> None:
        sessions = [
            _session([
                [_entry("closed guard"), _entry("armbar")],
                [_entry("closed guard")],
            ]),
        ]
        graph = build_athlete_graph("a", sessions)
        assert graph.nodes["closed guard"].count == 2
        assert graph.nodes["armbar"].count == 1

    def test_only_actor_you(self) -> None:
        sessions = [
            _session([[_entry("closed guard", actor="you"), _entry("armbar", actor="partner")]]),
        ]
        graph = build_athlete_graph("a", sessions)
        assert "closed guard" in graph.nodes
        assert "armbar" not in graph.nodes

    def test_no_self_loop(self) -> None:
        sessions = [
            _session([[_entry("closed guard"), _entry("closed guard")]]),
        ]
        graph = build_athlete_graph("a", sessions)
        assert ("closed guard", "closed guard") not in graph.edges
        assert graph.nodes["closed guard"].count == 2

    def test_edges_across_consecutive_distinct_entries(self) -> None:
        sessions = [
            _session([
                [_entry("closed guard"), _entry("sweep"), _entry("mount")],
            ]),
        ]
        graph = build_athlete_graph("a", sessions)
        assert len(graph.edges) == 2
        assert graph.edges[("closed guard", "sweep")].count == 1
        assert graph.edges[("sweep", "mount")].count == 1

    def test_two_sessions_aggregate_counts(self) -> None:
        sessions = [
            _session([[_entry("closed guard"), _entry("armbar")]]),
            _session([[_entry("closed guard"), _entry("triangle")]]),
        ]
        graph = build_athlete_graph("a", sessions)
        assert graph.nodes["closed guard"].count == 2
        assert graph.nodes["armbar"].count == 1
        assert graph.nodes["triangle"].count == 1
        assert graph.edges[("closed guard", "armbar")].count == 1
        assert graph.edges[("closed guard", "triangle")].count == 1

    def test_consecutive_identical_entries_no_edge_but_node_counted(self) -> None:
        sessions = [
            _session([[_entry("mount"), _entry("mount"), _entry("armbar")]]),
        ]
        graph = build_athlete_graph("a", sessions)
        assert graph.nodes["mount"].count == 2
        assert len(graph.edges) == 1
        assert ("mount", "mount") not in graph.edges
        assert ("mount", "armbar") in graph.edges


class TestOutDistribution:
    def test_sums_to_one(self) -> None:
        sessions = [
            _session([
                [_entry("closed guard"), _entry("sweep")],
                [_entry("closed guard"), _entry("armbar")],
            ]),
        ]
        graph = build_athlete_graph("a", sessions)
        dist = out_distribution(graph, "closed guard")
        assert abs(sum(dist.values()) - 1.0) < 1e-9
        assert dist["sweep"] == pytest.approx(0.5)
        assert dist["armbar"] == pytest.approx(0.5)

    def test_unknown_label(self) -> None:
        sessions = [_session([[_entry("closed guard")]])]
        graph = build_athlete_graph("a", sessions)
        assert out_distribution(graph, "nonexistent") == {}

    def test_leaf_label(self) -> None:
        sessions = [_session([[_entry("closed guard"), _entry("armbar")]])]
        graph = build_athlete_graph("a", sessions)
        assert out_distribution(graph, "armbar") == {}
