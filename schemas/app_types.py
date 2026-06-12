"""
Python mirrors of GrapplingArc app TypeScript types.

Use these to parse user bundle exports and produce benchmark comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ── User / Auth ───────────────────────────────────────────────

@dataclass
class UserAuth:
    id: str
    full_name: str
    belt_rank: str
    belt_degrees: int = 0
    is_guest: bool = False


# ── Round / Session ───────────────────────────────────────────

@dataclass
class RoundEntry:
    label: str
    assoc: str | None
    type: str
    actor: Literal["you", "partner"]
    setup: str | None = None
    successful: bool | None = None  # None ≈ True (back-compat)


@dataclass
class RoundSnapshot:
    id: str = ""
    difficulty: int = 5
    intensity: int = 5
    duration_min: int = 5
    item_type: str = ""
    position: str | None = None
    outcome: str | None = None
    entries: list[RoundEntry] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""


@dataclass
class Session:
    id: str
    created_at: str
    duration: float
    topic_type: str = ""
    topic_input: str = ""
    goal: str | None = None
    rounds: list[RoundSnapshot] = field(default_factory=list)
    reflection: str | None = None


# ── Graph ─────────────────────────────────────────────────────

@dataclass
class GraphNode:
    id: str
    label: str
    type: Literal["technique", "position", "concept"]
    computed_elo: float | None = None
    node_type: str = ""  # guard / pass / sweep / submission / …
    usage_count: int = 0
    trend: str = ""  # core / emerging / fading


@dataclass
class GraphEdge:
    id: str
    source: str
    target: str
    elo: float = 0.0
    setup: str = ""


@dataclass
class Graph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    user_elo: float | None = None


# ── Signatures / Systems ──────────────────────────────────────

@dataclass
class Signature:
    technique_id: str
    technique_name: str
    session_count: int
    elo: float
    score: float
    last_used_date: str


@dataclass
class System:
    id: str
    name: str
    signature_technique_id: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    total_elo: float = 0.0
    node_count: int = 0


# ── Bundle ────────────────────────────────────────────────────

@dataclass
class UserBundle:
    schema_version: int = 3
    user: UserAuth | None = None
    graph: Graph | None = None
    sessions: list[Session] = field(default_factory=list)
    signatures: list[Signature] = field(default_factory=list)
    systems: list[System] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> UserBundle:
        """Parse a mock_user_bundle.json export into typed dataclasses."""
        bundle = cls(schema_version=data.get("schemaVersion", 3))

        if "user" in data:
            auth = data["user"].get("auth", data["user"].get("profile", {}))
            bundle.user = UserAuth(
                id=auth.get("id", ""),
                full_name=auth.get("fullName", ""),
                belt_rank=auth.get("beltRank", ""),
                belt_degrees=auth.get("beltDegrees", 0),
                is_guest=auth.get("isGuest", False),
            )

        if "graph" in data:
            g = data["graph"]
            bundle.graph = Graph(user_elo=g.get("userElo"))
            for n in g.get("nodes", []):
                d = n.get("data", {})
                bundle.graph.nodes.append(GraphNode(
                    id=n["id"], label=n["label"], type=n.get("type", "technique"),
                    computed_elo=d.get("computedElo"),
                    node_type=d.get("type", ""),
                    usage_count=d.get("usageCount", 0),
                    trend=d.get("trend", ""),
                ))
            for e in g.get("edges", []):
                ed = e.get("data", {})
                bundle.graph.edges.append(GraphEdge(
                    id=e["id"], source=e["source"], target=e["target"],
                    elo=ed.get("elo", 0.0), setup=ed.get("setup", ""),
                ))

        for s in data.get("sessions", []):
            session = Session(
                id=s["id"], created_at=s.get("createdAt", ""),
                duration=s.get("duration", 0),
                topic_type=s.get("topicType", ""),
                topic_input=s.get("topicInput", ""),
                goal=s.get("goal"),
                reflection=s.get("reflection"),
            )
            for r in s.get("rounds", []):
                entries = [RoundEntry(**e) for e in r.get("entries", [])]
                session.rounds.append(RoundSnapshot(
                    id=r.get("id", ""), difficulty=r.get("difficulty", 5),
                    intensity=r.get("intensity", 5), duration_min=r.get("durationMin", 5),
                    item_type=r.get("itemType", ""), position=r.get("position"),
                    outcome=r.get("outcome"), entries=entries,
                    started_at=r.get("startedAt", ""), ended_at=r.get("endedAt", ""),
                ))
            bundle.sessions.append(session)

        return bundle
