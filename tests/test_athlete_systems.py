"""Tests for athlete systems detection and comparison."""

from __future__ import annotations

import pytest

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.athlete_systems import (
    TYPES,
    AthleteSystem,
    AthleteSystemProfile,
    athlete_graph_to_nx,
    build_system_profile,
    compare_profiles,
    comparison_matrix,
    detect_athlete_systems,
    match_systems,
    profile_to_dict,
    system_similarity,
)

# ── Fixtures ─────────────────────────────────────────────────────────────

def _graph_a() -> AthleteGraph:
    """A guard-heavy athlete: closed guard → sweep → submissions, plus some passing."""
    g = AthleteGraph(athlete="Guard Player")
    entries = [
        ("closed guard", "guard"), ("open guard", "guard"), ("half guard", "guard"),
        ("armbar", "submission"), ("triangle", "submission"), ("omoplata", "submission"),
        ("kimura", "submission"),
        ("scissor sweep", "sweep"), ("hook sweep", "sweep"),
        ("guard pass", "pass"), ("knee cut pass", "pass"),
        ("mount", "control"), ("back control", "control"),
        ("single leg", "takedown"),
        ("rear naked choke", "submission"),
    ]
    for label, typ in entries:
        g.nodes[label] = AthleteNode(label=label, type=typ, count=10)

    edges = [
        ("closed guard", "scissor sweep"), ("closed guard", "armbar"),
        ("closed guard", "triangle"), ("closed guard", "kimura"),
        ("open guard", "hook sweep"), ("open guard", "omoplata"),
        ("half guard", "scissor sweep"), ("half guard", "guard pass"),
        ("scissor sweep", "mount"), ("hook sweep", "back control"),
        ("guard pass", "mount"), ("knee cut pass", "mount"),
        ("mount", "armbar"), ("back control", "rear naked choke"),
        ("rear naked choke", "armbar"),
    ]
    for src, tgt in edges:
        g.edges[(src, tgt)] = AthleteEdge(source=src, target=tgt, count=5)

    # Set computed elos
    for norm, node in g.nodes.items():
        node.computed_elo = 850.0
    return g


def _graph_b() -> AthleteGraph:
    """A passing-heavy athlete: guard pass → control → submissions, fewer sweeps."""
    g = AthleteGraph(athlete="Passing Player")
    nodes = [
        ("guard pass", "pass"), ("knee cut pass", "pass"), ("headquarters pass", "pass"),
        ("long step pass", "pass"), ("toreando pass", "pass"),
        ("mount", "control"), ("side control", "control"), ("back control", "control"),
        ("north south", "control"),
        ("armbar", "submission"), ("americana", "submission"), ("rear naked choke", "submission"),
        ("closed guard", "guard"), ("half guard", "guard"),
        ("double leg", "takedown"),
    ]
    for label, typ in nodes:
        g.nodes[label] = AthleteNode(label=label, type=typ, count=10)

    edges = [
        ("guard pass", "mount"), ("guard pass", "side control"),
        ("knee cut pass", "mount"), ("headquarters pass", "guard pass"),
        ("long step pass", "side control"),
        ("toreando pass", "back control"),
        ("side control", "mount"), ("mount", "armbar"),
        ("mount", "americana"), ("side control", "rear naked choke"),
        ("back control", "rear naked choke"),
        ("closed guard", "guard pass"), ("half guard", "guard pass"),
        ("double leg", "guard pass"),
    ]
    for src, tgt in edges:
        g.edges[(src, tgt)] = AthleteEdge(source=src, target=tgt, count=8)

    for norm, node in g.nodes.items():
        node.computed_elo = 920.0
    return g


def _graph_c() -> AthleteGraph:
    """A leg-lock specialist: low guard, heel hooks, few passes."""
    g = AthleteGraph(athlete="Leg Lock Player")
    nodes = [
        ("asos guard", "guard"), ("saddle", "guard"), ("reverse de la riva", "guard"),
        ("outside ashi", "guard"), ("heel hook", "submission"),
        ("inside heel hook", "submission"), ("kneebar", "submission"),
        ("toe hold", "submission"),
        ("entrada", "transition"), ("finish", "control"),
        ("reap", "transition"),
    ]
    for label, typ in nodes:
        g.nodes[label] = AthleteNode(label=label, type=typ, count=10)

    edges = [
        ("asos guard", "outside ashi"), ("outside ashi", "heel hook"),
        ("asos guard", "saddle"), ("saddle", "heel hook"),
        ("saddle", "inside heel hook"),
        ("reverse de la riva", "asos guard"),
        ("reverse de la riva", "outside ashi"),
        ("entrada", "asos guard"), ("entrada", "saddle"),
        ("reap", "outside ashi"),
        ("heel hook", "finish"), ("inside heel hook", "finish"),
    ]
    for src, tgt in edges:
        g.edges[(src, tgt)] = AthleteEdge(source=src, target=tgt, count=7)

    for norm, node in g.nodes.items():
        node.computed_elo = 880.0
    return g


@pytest.fixture
def guard_graph() -> AthleteGraph:
    return _graph_a()


@pytest.fixture
def pass_graph() -> AthleteGraph:
    return _graph_b()


@pytest.fixture
def leglock_graph() -> AthleteGraph:
    return _graph_c()


# ── Graph conversion ─────────────────────────────────────────────────────

class TestAthleteGraphToNx:
    def test_nodes_transferred(self, guard_graph: AthleteGraph) -> None:
        g = athlete_graph_to_nx(guard_graph)
        assert g.number_of_nodes() == len(guard_graph.nodes)
        for norm, node in guard_graph.nodes.items():
            assert norm in g
            assert g.nodes[norm]["type"] == node.type
            assert g.nodes[norm]["occ"] == node.count

    def test_edges_transferred(self, guard_graph: AthleteGraph) -> None:
        g = athlete_graph_to_nx(guard_graph)
        assert g.number_of_edges() == len(guard_graph.edges)
        for (s, t), edge in guard_graph.edges.items():
            assert g.has_edge(s, t)
            assert g[s][t]["weight"] == edge.count

    def test_empty_graph(self) -> None:
        g = athlete_graph_to_nx(AthleteGraph(athlete="empty"))
        assert g.number_of_nodes() == 0
        assert g.number_of_edges() == 0


# ── System detection ─────────────────────────────────────────────────────

class TestDetectAthleteSystems:
    def test_guard_player_systems(self, guard_graph: AthleteGraph) -> None:
        systems = detect_athlete_systems(guard_graph)
        assert len(systems) >= 1

    def test_all_systems_have_valid_type_vectors(self, guard_graph: AthleteGraph) -> None:
        systems = detect_athlete_systems(guard_graph)
        for s in systems:
            assert len(s.type_vector) == len(TYPES)
            assert abs(sum(s.type_vector) - 1.0) < 1e-3

    def test_systems_sorted_by_size(self, guard_graph: AthleteGraph) -> None:
        systems = detect_athlete_systems(guard_graph)
        for i in range(len(systems) - 1):
            assert systems[i].size >= systems[i + 1].size

    def test_each_system_has_hub(self, guard_graph: AthleteGraph) -> None:
        systems = detect_athlete_systems(guard_graph)
        for s in systems:
            assert s.hub in s.members
            assert s.hub_type in TYPES or s.hub_type == ""

    def test_min_system_size_filters(self, guard_graph: AthleteGraph) -> None:
        all_systems = detect_athlete_systems(guard_graph, min_system_size=1)
        big_systems = detect_athlete_systems(guard_graph, min_system_size=5)
        assert len(big_systems) <= len(all_systems)

    def test_empty_graph(self) -> None:
        systems = detect_athlete_systems(AthleteGraph(athlete="empty"))
        assert systems == []

    def test_single_node(self) -> None:
        g = AthleteGraph(athlete="solo")
        g.nodes["armbar"] = AthleteNode(label="armbar", type="submission", count=1)
        systems = detect_athlete_systems(g)
        assert len(systems) == 0

    def test_detects_leglock_cluster(self, leglock_graph: AthleteGraph) -> None:
        systems = detect_athlete_systems(leglock_graph)
        hass = any("heel" in s.name.lower() or "submission" in s.name.lower()
                   for s in systems)
        assert hass


# ── System similarity ────────────────────────────────────────────────────

class TestSystemSimilarity:
    def test_identical_systems(self) -> None:
        s = AthleteSystem(
            name="test", hub="a", hub_type="guard",
            members=["a", "b"], type_vector=[0.5, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0],
            size=2, system_elo=800.0, transition_count=1, internal_edges=[],
        )
        r = system_similarity(s, s)
        assert r["score"] == pytest.approx(1.0, abs=1e-4)
        assert r["type_cosine"] == pytest.approx(1.0, abs=1e-4)

    def test_orthogonal_systems(self) -> None:
        s1 = AthleteSystem(
            name="guard", hub="a", hub_type="guard",
            members=["a"], type_vector=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            size=1, system_elo=800.0, transition_count=0, internal_edges=[],
        )
        s2 = AthleteSystem(
            name="passing", hub="b", hub_type="pass",
            members=["b"], type_vector=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            size=1, system_elo=800.0, transition_count=0, internal_edges=[],
        )
        r = system_similarity(s1, s2)
        assert r["type_cosine"] == pytest.approx(0.0, abs=1e-4)
        assert r["hub_match"] == 0.0

    def test_hub_match_bonus(self) -> None:
        s1 = AthleteSystem(
            name="a", hub="guard", hub_type="guard",
            members=["a"], type_vector=[0.5, 0.3, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0],
            size=1, system_elo=800.0, transition_count=0, internal_edges=[],
        )
        s2 = AthleteSystem(
            name="b", hub="closed guard", hub_type="guard",
            members=["b"], type_vector=[0.6, 0.2, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0],
            size=1, system_elo=800.0, transition_count=0, internal_edges=[],
        )
        r = system_similarity(s1, s2)
        assert r["hub_match"] == 1.0


# ── Profile building ─────────────────────────────────────────────────────

class TestBuildSystemProfile:
    def test_profile_has_name(self, guard_graph: AthleteGraph) -> None:
        p = build_system_profile("Guard Player", guard_graph)
        assert p.athlete_name == "Guard Player"

    def test_profile_has_systems(self, guard_graph: AthleteGraph) -> None:
        p = build_system_profile("Guard Player", guard_graph)
        assert p.system_count >= 1
        assert len(p.systems) == p.system_count

    def test_composition_vector_structure(self, guard_graph: AthleteGraph) -> None:
        p = build_system_profile("Guard Player", guard_graph)
        expected_len = 6 * len(TYPES)  # MAX_SYSTEMS_IN_VECTOR * len(TYPES)
        assert len(p.composition_vector) == expected_len

    def test_dominant_type_known(self, guard_graph: AthleteGraph) -> None:
        p = build_system_profile("Guard Player", guard_graph)
        assert p.dominant_type in TYPES

    def test_passing_player_dominant(self, pass_graph: AthleteGraph) -> None:
        p = build_system_profile("Passing Player", pass_graph)
        assert p.dominant_type == "pass"

    def test_diversity_non_negative(self, guard_graph: AthleteGraph) -> None:
        p = build_system_profile("Guard Player", guard_graph)
        assert p.diversity >= 0.0


# ── Cross-athlete comparison ─────────────────────────────────────────────

class TestMatchSystems:
    def test_matches_both_directions(self) -> None:
        pa = build_system_profile("Guard Player", _graph_a())
        pb = build_system_profile("Passing Player", _graph_b())
        result = match_systems(pa, pb)
        assert result["athlete_a"] == "Guard Player"
        assert result["athlete_b"] == "Passing Player"
        assert len(result["matches"]) > 0
        assert 0 <= result["aggregate_similarity"] <= 1.0

    def test_self_comparison(self) -> None:
        p = build_system_profile("Guard Player", _graph_a())
        result = match_systems(p, p)
        assert result["aggregate_similarity"] == pytest.approx(1.0, abs=1e-3)

    def test_empty_profile_returns_zero(self) -> None:
        empty = AthleteSystemProfile(
            athlete_name="empty", systems=[], composition_vector=[],
            system_count=0, diversity=0.0, dominant_type="",
            total_techniques=0,
        )
        p = build_system_profile("Guard Player", _graph_a())
        result = match_systems(p, empty)
        assert result["aggregate_similarity"] == 0.0

    def test_guard_leglock_guard_is_low(self) -> None:
        pg = build_system_profile("Guard Player", _graph_a())
        pl = build_system_profile("Leg Lock Player", _graph_c())
        pp = build_system_profile("Passing Player", _graph_b())
        g_vs_l = match_systems(pg, pl)["aggregate_similarity"]
        g_vs_p = match_systems(pg, pp)["aggregate_similarity"]
        assert g_vs_l < g_vs_p

    def test_each_match_has_detail(self) -> None:
        pa = build_system_profile("Guard Player", _graph_a())
        pb = build_system_profile("Passing Player", _graph_b())
        result = match_systems(pa, pb)
        for m in result["matches"]:
            assert "a_system" in m
            assert "b_system" in m
            assert "score" in m
            assert "type_cosine" in m


class TestCompareProfiles:
    def test_query_not_in_results(self) -> None:
        pa = build_system_profile("Guard Player", _graph_a())
        pb = build_system_profile("Passing Player", _graph_b())
        pc = build_system_profile("Leg Lock Player", _graph_c())
        results = compare_profiles(pa, [pa, pb, pc], k=5)
        names = [r["athlete"] for r in results]
        assert "Guard Player" not in names

    def test_k_limits_results(self) -> None:
        pa = build_system_profile("Guard Player", _graph_a())
        pb = build_system_profile("Passing Player", _graph_b())
        pc = build_system_profile("Leg Lock Player", _graph_c())
        results = compare_profiles(pa, [pa, pb, pc], k=1)
        assert len(results) == 1

    def test_comparison_matrix(self) -> None:
        pa = build_system_profile("Guard Player", _graph_a())
        pb = build_system_profile("Passing Player", _graph_b())
        pc = build_system_profile("Leg Lock Player", _graph_c())
        matrix = comparison_matrix([pa, pb, pc])
        assert len(matrix["athletes"]) == 3
        assert len(matrix["similarity_matrix"]) == 3
        # Diagonal should be 1.0
        for i in range(3):
            assert matrix["similarity_matrix"][i][i] == pytest.approx(1.0, abs=1e-3)

    def test_results_sorted_by_similarity(self) -> None:
        pa = build_system_profile("Guard Player", _graph_a())
        pb = build_system_profile("Passing Player", _graph_b())
        pc = build_system_profile("Leg Lock Player", _graph_c())
        results = compare_profiles(pa, [pa, pb, pc], k=5)
        for i in range(len(results) - 1):
            assert results[i]["aggregate_similarity"] >= results[i + 1]["aggregate_similarity"]


# ── Export ───────────────────────────────────────────────────────────────

class TestProfileToDict:
    def test_serializable(self, guard_graph: AthleteGraph) -> None:
        p = build_system_profile("Guard Player", guard_graph)
        d = profile_to_dict(p)
        assert d["athlete_name"] == "Guard Player"
        assert isinstance(d["systems"], list)
        assert len(d["systems"]) == p.system_count

    def test_system_dict_keys(self, guard_graph: AthleteGraph) -> None:
        p = build_system_profile("Guard Player", guard_graph)
        d = profile_to_dict(p)
        for sd in d["systems"]:
            for key in ("name", "hub", "hub_type", "members", "type_vector",
                        "size", "system_elo", "transition_count", "internal_edges"):
                assert key in sd

    def test_roundtrip_empty(self) -> None:
        p = AthleteSystemProfile(
            athlete_name="empty", systems=[], composition_vector=[],
            system_count=0, diversity=0.0, dominant_type="",
            total_techniques=0,
        )
        d = profile_to_dict(p)
        assert d["system_count"] == 0
        assert d["systems"] == []
