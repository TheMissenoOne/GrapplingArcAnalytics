"""Ontology seed + system proposal + additive breakdown tests — no live DB required."""

from __future__ import annotations

import networkx as nx
from sqlalchemy.dialects import postgresql

from analysis.systems import propose_from_network
from db.models import Athlete, Graph, Match
from db.repository import graphs_for_clustering
from export.match_breakdown import build_match_breakdown
from export.ontology import _athlete_graph_owner_map, eligible_grappling_graphs, validate_seed


class _Rows:
    def __init__(self, rows=(), scalars=()):
        self._rows = rows
        self._scalars = scalars

    def all(self):
        return self._rows

    def scalars(self):
        return self._scalars


class _RecordingSession:
    statement = None

    def execute(self, statement):
        self.statement = statement
        return _Rows(rows=[("graph-1", "athlete-1", "archetype-1")])


class _ClusteringRecordingSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 2:
            return _Rows(scalars=["graph-1"])
        return _Rows()


def test_athlete_graph_owner_map_projects_only_scalar_columns():
    session = _RecordingSession()

    owners = _athlete_graph_owner_map(session, Graph)

    assert owners == {"graph-1": ("athlete-1", "archetype-1")}
    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "graphs.id, graphs.owner_id, graphs.archetype_id" in sql
    assert "graphs.embedding" not in sql


def test_graphs_for_clustering_projects_graph_ids_only():
    session = _ClusteringRecordingSession()

    assert graphs_for_clustering(session, owner_kind="athlete") == [("graph-1", [])]

    sql = str(
        session.statements[1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "SELECT graphs.id" in sql
    assert "graphs.embedding" not in sql


def test_export_eligibility_ignores_strike_only_padding():
    class Node:
        def __init__(self, key: str, node_type: str) -> None:
            self.node_key = key
            self.node_type = node_type
            self.computed_elo = 1000.0

    rows = eligible_grappling_graphs([
        ("padded", [
            Node("mount", "control"),
            Node("closed guard", "guard"),
            Node("jab", "strike"),
            Node("uppercut", "strike"),
        ]),
        ("eligible", [
            Node("mount", "control"),
            Node("closed guard", "guard"),
            Node("armbar", "submission"),
            Node("jab", "strike"),
        ]),
    ])

    assert [(graph_id, [node.node_key for node in nodes]) for graph_id, nodes in rows] == [
        ("eligible", ["mount", "closed guard", "armbar"]),
    ]


def test_propose_from_network_yields_systems():
    g = nx.DiGraph()
    # Two loosely-coupled clusters (a guard family + a leg-lock family). PtV attrs:
    # the submissions land often (ok_count), so ashi garami is a real two-branch fork.
    subs = {"armbar", "triangle", "heel hook", "knee bar"}
    for n in ("closed guard", "armbar", "triangle", "ashi garami", "heel hook", "knee bar"):
        g.add_node(n, occ=5, denom=5, reward=0, risk=0, reward_risk=1.0,
                   ok_count=4 if n in subs else 0,
                   type="submission" if n in subs else "guard")
    g.add_edge("closed guard", "armbar", weight=4)
    g.add_edge("closed guard", "triangle", weight=3)
    g.add_edge("armbar", "triangle", weight=2)
    g.add_edge("ashi garami", "heel hook", weight=5)
    g.add_edge("ashi garami", "knee bar", weight=3)
    g.add_edge("heel hook", "knee bar", weight=2)

    proposals = propose_from_network(g, min_occ=1)
    assert proposals
    for p in proposals:
        assert p["key"].endswith("-system")
        assert len(p["member_positions"]) >= 2
        assert isinstance(p["entry_positions"], list)

    # Dilemma = ≥2 high-PtV out-edges (path_to_victory), not "high reward-risk node":
    # ashi garami forks into two landed finishes → proposed with both branches.
    all_dilemmas = [d for p in proposals for d in p["candidate_dilemmas"]]
    ashi = next(d for d in all_dilemmas if d["around"] == "ashi garami")
    assert set(ashi["branches"]) == {"heel hook", "knee bar"}
    assert "subtree" in ashi


def _athlete(aid: str, name: str) -> Athlete:
    return Athlete(id=aid, name=name, elo=1000.0, elo_series=[1000.0, 1010.0])


def test_build_match_breakdown_is_additive():
    a = _athlete("a-id", "Gordon Ryan")
    b = _athlete("b-id", "Andre Galvao")
    match = Match(
        id="m1",
        athlete_a_id="a-id",
        athlete_b_id="b-id",
        winner_id="a-id",
        year=2022,
        event="ADCC",
        weight_class="ABS",
        win_type="SUBMISSION",
        submission="Rear Naked Choke",
        sequence=[
            {"label": "Takedown", "type": "takedown", "actor_id": "a-id", "successful": True},
            {"label": "Guard Pass", "type": "pass", "actor_id": "a-id", "successful": True},
            {"label": "Back Control", "type": "control", "actor_id": "a-id"},
            {"label": "Rear Naked Choke", "type": "submission", "actor_id": "a-id"},
        ],
    )

    bd = build_match_breakdown(match, a, b)

    # Legacy keys are untouched (the existing site viz keeps rendering).
    for key in ("meta", "sequence", "stats", "transition_graph", "fighters"):
        assert key in bd
    assert bd["meta"]["winner"] == {"side": "a", "name": "Gordon Ryan"}

    # New additive strategic keys (RF14 / DS-12).
    assert "decision_space" in bd
    ds = bd["decision_space"]
    assert ds["mode"] == "expert"
    assert len(ds["timeline"]) == 4
    assert ds["reductions"]  # the compression run produced a major reduction
    assert bd["systems"] == []
    assert bd["principles"] == []
    assert bd["decision_chains"] == []


def test_validate_seed_catches_uuid_and_orphan_refs():
    # A well-formed seed: milestone + implementation reference the system by stable key.
    good = {
        "systems": [{"key": "body-lock-passing-system"}],
        "milestones": [{"name": "Concept", "system_key": "body-lock-passing-system"}],
        "implementations": [
            {
                "name": "Gordon",
                "system_key": "body-lock-passing-system",
                "athlete_key": "gordon-ryan",
            }
        ],
    }
    assert validate_seed(good) == []

    # Regressions F1 guards against: a DB-UUID-style ref, an orphan, and a missing athlete key.
    bad = {
        "systems": [{"key": "body-lock-passing-system"}],
        "milestones": [{"name": "X", "system_key": "f47ac10b-58cc-4372-a567-0e02b2c3d479"}],
        "implementations": [{"name": "Y", "system_key": "nope", "athlete_key": ""}],
    }
    problems = validate_seed(bad)
    assert len(problems) == 3


def test_validate_seed_catches_orphan_archetype_ref():
    # athlete_profile.emergent_archetype_key must resolve to a seed archetype (RF01).
    good = {
        "archetypes": [{"key": "emergent-guard-sweep-specialist", "name": "X", "kind": "emergent"}],
        "athlete_profiles": [
            {"name": "Kade", "emergent_archetype_key": "emergent-guard-sweep-specialist"}
        ],
    }
    assert validate_seed(good) == []
    bad = {
        "archetypes": [{"key": "emergent-guard-sweep-specialist"}],
        "athlete_profiles": [{"name": "Ghost", "emergent_archetype_key": "does-not-exist"}],
    }
    problems = validate_seed(bad)
    assert len(problems) == 1 and "does-not-exist" in problems[0]


def test_curated_ds_overrides_in_breakdown():
    a = _athlete("a-id", "A")
    b = _athlete("b-id", "B")
    match = Match(
        id="m2",
        athlete_a_id="a-id",
        athlete_b_id="b-id",
        winner_id=None,
        year=2024,
        sequence=[{"label": "Closed Guard", "type": "guard", "actor_id": "b-id"}],
    )
    curated = {"closed guard": {"attacker_score": 0.2, "defender_score": 0.9}}
    bd = build_match_breakdown(match, a, b, curated_ds=curated)
    step = bd["decision_space"]["timeline"][0]
    # b is the actor → attacker score 0.2 lands on b; a (defender) gets 0.9.
    assert step["ds_after"]["b"] == 0.2
    assert step["ds_after"]["a"] == 0.9
