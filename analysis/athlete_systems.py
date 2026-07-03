"""Athlete system detection and cross-athlete system comparison.

Decomposes a per-athlete technique graph into communities (systems) —
groups of techniques that the athlete habitually chains together.
Then compares athletes at the system level to find who plays a
similar game and where their systems overlap.

Usage::

    from analysis.athlete_systems import (
        build_system_profile,
        compare_profiles,
        match_systems,
    )

    profile = build_system_profile("athlete_name", athlete_graph)
    similar = compare_profiles(query_profile, all_profiles, k=5)
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.network_metrics import detect_communities

TYPES = ["guard", "pass", "sweep", "submission", "takedown", "control", "escape", "transition"]
MAX_SYSTEMS_IN_VECTOR = 6


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class AthleteSystem:
    """One detected system (technique community) for an athlete."""
    name: str
    hub: str
    hub_type: str
    members: list[str]
    type_vector: list[float]
    size: int
    system_elo: float | None
    transition_count: int
    internal_edges: list[tuple[str, str, int]]


@dataclass
class SystemMatch:
    """Best-match between two athletes' systems."""
    a_system: str
    a_hub: str
    b_system: str
    b_hub: str
    similarity: float
    type_cosine: float
    hub_match: bool
    size_similarity: float
    elo_closeness: float


@dataclass
class AthleteSystemProfile:
    """Full system profile for one athlete across all systems."""
    athlete_name: str
    systems: list[AthleteSystem]
    composition_vector: list[float]
    system_count: int
    diversity: float
    dominant_type: str
    total_techniques: int


# ── Graph conversion ─────────────────────────────────────────────────────

def athlete_graph_to_nx(graph: AthleteGraph) -> nx.DiGraph:
    """Convert an ``AthleteGraph`` to ``nx.DiGraph`` with occ/type/elo attrs."""
    g = nx.DiGraph()
    for norm, node in graph.nodes.items():
        g.add_node(norm,
                   label=node.label,
                   type=node.type,
                   count=node.count,
                   occ=node.count,
                   computed_elo=node.computed_elo or 0.0)
    for (src, tgt), edge in graph.edges.items():
        w = edge.count if edge.count > 0 else 1
        g.add_edge(src, tgt, weight=w)
    return g


# ── System detection ─────────────────────────────────────────────────────

def detect_athlete_systems(
    graph: AthleteGraph,
    min_system_size: int = 2,
) -> list[AthleteSystem]:
    """Detect technique communities (systems) from a per-athlete ``AthleteGraph``.

    Groups techniques into clusters using greedy-modularity community detection
    on the athlete's transition network.  Each cluster becomes an ``AthleteSystem``
    with its type profile, hub (most central technique), and ELO signal.

    Returns systems sorted largest-first.  Ignores communities smaller than
    ``min_system_size``.
    """
    g = athlete_graph_to_nx(graph)
    if g.number_of_nodes() == 0:
        return []

    communities = detect_communities(g, min_occ=1)

    systems: list[AthleteSystem] = []
    for members in communities:
        members = [m for m in members if m in g]
        if len(members) < min_system_size:
            continue

        type_counts: Counter[str] = Counter()
        elos: list[float] = []
        for m in members:
            nd = g.nodes[m]
            type_counts[nd.get("type", "")] += max(nd.get("occ", 1), 1)
            elo = nd.get("computed_elo", 0.0)
            if elo and elo > 0:
                elos.append(elo)

        total = sum(type_counts.values()) or 1
        type_vector = [round(type_counts.get(t, 0) / total, 4) for t in TYPES]

        sub = g.subgraph(members)
        try:
            pr = nx.pagerank(sub, weight="weight")
        except nx.PowerIterationFailedConvergence:
            pr = {n: 1.0 / len(sub) for n in sub}
        hub = max(pr, key=pr.get) if pr else members[0]

        hub_node = graph.nodes.get(hub)
        hub_type = hub_node.type if hub_node else ""

        internal = [
            (s, t, ed.count)
            for (s, t), ed in graph.edges.items()
            if s in members and t in members
        ]

        system_elo = float(np.mean(elos)) if elos else None

        dom_type = type_counts.most_common(1)[0][0] if type_counts else ""
        label = hub_node.label if hub_node else hub
        name = f"{dom_type.title()} ({label})" if dom_type else label

        systems.append(AthleteSystem(
            name=name,
            hub=hub,
            hub_type=hub_type,
            members=list(members),
            type_vector=type_vector,
            size=len(members),
            system_elo=system_elo,
            transition_count=len(internal),
            internal_edges=internal,
        ))

    systems.sort(key=lambda s: s.size, reverse=True)
    return systems


# ── System similarity ────────────────────────────────────────────────────

def system_similarity(a: AthleteSystem, b: AthleteSystem) -> dict[str, Any]:
    """Multi-factor similarity between two systems.

    Factors (weighted):
      - Type-vector cosine (0.50) — the primary signal
      - Hub type match       (0.20) — same central category
      - Size similarity      (0.15) — comparable breadth
      - ELO closeness        (0.15) — similar proficiency

    Returns breakdown dict with ``score`` (0-1).
    """
    va = np.array(a.type_vector, dtype=np.float64)
    vb = np.array(b.type_vector, dtype=np.float64)
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    type_cos = float(va @ vb / (na * nb)) if (na > 0 and nb > 0) else 0.0

    hub_match = 1.0 if a.hub_type == b.hub_type else 0.0

    max_size = max(a.size, b.size) or 1
    size_sim = 1.0 - abs(a.size - b.size) / max_size

    if a.system_elo is not None and b.system_elo is not None:
        elo_gap = abs(a.system_elo - b.system_elo)
        elo_sim = max(0.0, 1.0 - elo_gap / 400.0)
    else:
        elo_sim = 0.0

    score = 0.50 * type_cos + 0.20 * hub_match + 0.15 * size_sim + 0.15 * elo_sim

    return {
        "score": round(score, 4),
        "type_cosine": round(type_cos, 4),
        "hub_match": hub_match,
        "size_similarity": round(size_sim, 4),
        "elo_closeness": round(elo_sim, 4),
    }


def _type_entropy(type_dist: list[float]) -> float:
    """Shannon entropy of a type distribution (bits)."""
    arr = np.array([v for v in type_dist if v > 0])
    if arr.size == 0:
        return 0.0
    return -float((arr * np.log(arr)).sum())


# ── Profile building ─────────────────────────────────────────────────────

def build_system_profile(
    athlete_name: str,
    graph: AthleteGraph,
) -> AthleteSystemProfile:
    """Build a full system profile for one athlete from their ``AthleteGraph``.

    Detects systems, computes composition vector, aggregate type entropy,
    and dominant type.
    """
    systems = detect_athlete_systems(graph)

    top = systems[:MAX_SYSTEMS_IN_VECTOR]
    comp: list[float] = []
    for s in top:
        comp.extend(s.type_vector)
    pad = MAX_SYSTEMS_IN_VECTOR - len(top)
    comp.extend([0.0] * pad * len(TYPES))

    agg_type: Counter[str] = Counter()
    for s in systems:
        for t, share in zip(TYPES, s.type_vector):
            agg_type[t] += share * s.size
    total = sum(agg_type.values()) or 1
    overall = [agg_type.get(t, 0) / total for t in TYPES]
    diversity = _type_entropy(overall)
    dominant = TYPES[np.argmax(overall)] if overall else ""

    return AthleteSystemProfile(
        athlete_name=athlete_name,
        systems=systems,
        composition_vector=comp,
        system_count=len(systems),
        diversity=round(diversity, 3),
        dominant_type=dominant,
        total_techniques=sum(s.size for s in systems),
    )


# ── Cross-athlete comparison ─────────────────────────────────────────────

def match_systems(
    profile_a: AthleteSystemProfile,
    profile_b: AthleteSystemProfile,
) -> dict[str, Any]:
    """Greedy best-match between two athletes' systems.

    Each system in A picks its closest *unmatched* system in B.  Greedy
    (not Hungarian) — fast and deterministic, no extra deps.

    Returns match detail per pair + ``aggregate_similarity``.
    """
    if not profile_a.systems or not profile_b.systems:
        return {
            "athlete_a": profile_a.athlete_name,
            "athlete_b": profile_b.athlete_name,
            "a_system_count": profile_a.system_count,
            "b_system_count": profile_b.system_count,
            "matches": [],
            "aggregate_similarity": 0.0,
        }

    matched_b: set[int] = set()
    matches: list[dict[str, Any]] = []

    for sa in profile_a.systems:
        best_score = -1.0
        best_idx = -1
        best_detail: dict[str, Any] = {}
        for j, sb in enumerate(profile_b.systems):
            if j in matched_b:
                continue
            detail = system_similarity(sa, sb)
            if detail["score"] > best_score:
                best_score = detail["score"]
                best_idx = j
                best_detail = detail

        if best_idx >= 0:
            matched_b.add(best_idx)
            sb = profile_b.systems[best_idx]
            matches.append({
                "a_system": sa.name,
                "a_hub": sa.hub,
                "b_system": sb.name,
                "b_hub": sb.hub,
                **best_detail,
            })

    agg = float(np.mean([m["score"] for m in matches])) if matches else 0.0

    return {
        "athlete_a": profile_a.athlete_name,
        "athlete_b": profile_b.athlete_name,
        "a_system_count": profile_a.system_count,
        "b_system_count": profile_b.system_count,
        "matches": matches,
        "aggregate_similarity": round(agg, 4),
    }


def compare_profiles(
    query: AthleteSystemProfile,
    targets: list[AthleteSystemProfile],
    k: int = 5,
) -> list[dict[str, Any]]:
    """Rank target athletes by system similarity to the query athlete.

    Returns top-``k`` targets sorted descending by aggregate similarity,
    each with their best-matching system detail.
    """
    results: list[dict[str, Any]] = []
    for target in targets:
        if target.athlete_name == query.athlete_name:
            continue
        comp = match_systems(query, target)
        results.append({
            "athlete": target.athlete_name,
            "system_count": target.system_count,
            "total_techniques": target.total_techniques,
            "dominant_type": target.dominant_type,
            "diversity": target.diversity,
            "aggregate_similarity": comp["aggregate_similarity"],
            "best_match": comp["matches"][0] if comp["matches"] else None,
        })

    results.sort(key=lambda x: -x["aggregate_similarity"])
    return results[:k]


def comparison_matrix(
    profiles: list[AthleteSystemProfile],
) -> dict[str, Any]:
    """Full pairwise comparison matrix for a set of athlete system profiles.

    Returns:
      - ``athletes`` — list of athlete names (order = matrix axis)
      - ``similarity_matrix`` — N×N list-of-lists of aggregate similarities
      - ``dominant_types`` — per-athlete dominant type
      - ``system_counts`` — per-athlete system count
    """
    names = [p.athlete_name for p in profiles]
    n = len(names)
    matrix = np.ones((n, n), dtype=np.float64)

    for i in range(n):
        for j in range(i + 1, n):
            comp = match_systems(profiles[i], profiles[j])
            sim = comp["aggregate_similarity"]
            matrix[i, j] = sim
            matrix[j, i] = sim

    return {
        "athletes": names,
        "similarity_matrix": [[round(float(v), 4) for v in row] for row in matrix.tolist()],
        "dominant_types": {p.athlete_name: p.dominant_type for p in profiles},
        "system_counts": {p.athlete_name: p.system_count for p in profiles},
    }


# ── Integration with graph_comparison format ────────────────────────────

def from_graph_comparison_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    translated_labels: dict[str, str] | None = None,
    athlete_name: str = "user",
) -> AthleteGraph:
    """Build ``AthleteGraph`` from ``graph_comparison.load_user_graph`` dict format.

    ``translated_labels`` maps raw labels → English (used to resolve edge IDs).
    When provided, edges are resolved via node ID → English label.
    """
    trans = translated_labels or {}

    # Build node_id → english_label lookup
    id_to_eng: dict[str, str] = {}
    for n in nodes:
        nid = n.get("id", "")
        raw = n.get("label", "")
        eng = str(trans.get(raw, raw)).lower().strip()
        if nid:
            id_to_eng[nid] = eng

    g = AthleteGraph(athlete=athlete_name)
    for n in nodes:
        label_raw = n.get("label", "")
        label = str(trans.get(label_raw, label_raw)).lower().strip()
        if not label:
            continue
        typ = str(n.get("node_type", "") or n.get("type", ""))
        count = int(n.get("usage", 0) or 0)
        elo = n.get("elo")
        node = AthleteNode(label=label, type=typ, count=max(count, 1))
        if elo is not None:
            node.computed_elo = float(elo)
        g.nodes[label] = node

    for e in edges:
        src_id = str(e.get("source", ""))
        tgt_id = str(e.get("target", ""))
        src = id_to_eng.get(src_id, src_id.lower().strip())
        tgt = id_to_eng.get(tgt_id, tgt_id.lower().strip())
        if src and tgt and src != tgt:
            key = (src, tgt)
            edge = g.edges.get(key)
            if edge:
                edge.count += 1
            else:
                g.edges[key] = AthleteEdge(source=src, target=tgt, count=1)
    return g


def from_fighter_graph(fighter_name: str, fg: dict[str, Any]) -> AthleteGraph:
    """Build ``AthleteGraph`` from ``graph_comparison.build_fighter_graph`` dict."""
    g = AthleteGraph(athlete=fighter_name)
    for n in fg.get("nodes", []):
        label = str(n.get("label", "")).lower().strip()
        if not label:
            continue
        typ = str(n.get("type", ""))
        count = int(n.get("count", 0) or 1)
        elo = n.get("proficiency")
        node = AthleteNode(label=label, type=typ, count=max(count, 1))
        if elo is not None:
            node.computed_elo = float(elo)
        g.nodes[label] = node

    for e in fg.get("edges", []):
        src = str(e.get("source", "")).lower().strip()
        tgt = str(e.get("target", "")).lower().strip()
        c = int(e.get("count", 0) or 1)
        if src and tgt and src != tgt:
            g.edges[(src, tgt)] = AthleteEdge(source=src, target=tgt, count=c)
    return g


def from_career_graphview(athlete_name: str, graphview: dict[str, Any]) -> AthleteGraph:
    """Build ``AthleteGraph`` from site_data's graphview dict (nodes+links format).

    The graphview dict comes from _career_graphview (truncated to 12 nodes for dossier rendering).
    Nodes: [{"id": key, "label": label, "cat": type, "size": count, ...}]
    Links: [{"from": source, "to": target, "weight": count, ...}]

    ponytail: truncated to 12 nodes; untruncated source is export_fighter_graph via _to_graphview
    if system detection later needs fuller resolution.
    """
    g = AthleteGraph(athlete=athlete_name)
    for n in graphview.get("nodes", []):
        node_id = str(n.get("id", "")).lower().strip()
        label = str(n.get("label", "")).lower().strip()
        if not node_id or not label:
            continue
        typ = str(n.get("cat", ""))
        count = int(n.get("size", 1))
        node = AthleteNode(label=label, type=typ, count=max(count, 1))
        g.nodes[node_id] = node

    for lk in graphview.get("links", []):
        src = str(lk.get("from", "")).lower().strip()
        tgt = str(lk.get("to", "")).lower().strip()
        c = int(lk.get("weight", 1))
        if src and tgt and src != tgt:
            g.edges[(src, tgt)] = AthleteEdge(source=src, target=tgt, count=c)
    return g


def system_comparison_from_files(
    user_json_path: str | Path,
    competition_path: str | Path = "_analytics_export.json",
    k: int = 5,
) -> dict[str, Any]:
    """End-to-end: load user graph + competition fighters, detect systems, compare.

    Wraps the ``graph_comparison`` loaders for compatibility:
      1. Loads user graph via ``graph_comparison.load_user_graph``
      2. Builds fighter graphs via ``graph_comparison.build_all_fighter_graphs``
      3. Converts both to ``AthleteGraph``
      4. Detects systems, builds profiles, ranks nearest

    Returns a JSON-safe dict with user profile, nearest-fighter rankings,
    and a pairwise similarity matrix.
    """
    from analysis.graph_comparison import build_all_fighter_graphs, load_user_graph

    user_dict = load_user_graph(user_json_path)
    user_graph = from_graph_comparison_nodes(
        user_dict.get("nodes", []),
        user_dict.get("edges", []),
        translated_labels=user_dict.get("translated_labels"),
        athlete_name="You (user)",
    )
    user_profile = build_system_profile("You (user)", user_graph)

    with open(competition_path) as f:
        matches: list[dict[str, Any]] = json.load(f)
    fighter_graphs_raw = build_all_fighter_graphs(matches)
    all_profiles: list[AthleteSystemProfile] = [user_profile]
    for fname, fg in fighter_graphs_raw.items():
        if not fg.get("profile", {}).get("has_profile", False):
            continue
        ag = from_fighter_graph(fname, fg)
        if ag.nodes:
            all_profiles.append(build_system_profile(fname, ag))

    nearest = compare_profiles(user_profile, all_profiles, k=k)
    matrix = comparison_matrix(all_profiles)

    return {
        "meta": {
            "user_systems": user_profile.system_count,
            "user_techniques": user_profile.total_techniques,
            "user_dominant_type": user_profile.dominant_type,
            "fighters_profiled": len(all_profiles) - 1,
        },
        "user_profile": profile_to_dict(user_profile),
        "nearest_by_system_similarity": nearest,
        "pairwise_matrix": matrix,
    }


# ── Export helpers ───────────────────────────────────────────────────────

def profile_to_dict(profile: AthleteSystemProfile) -> dict[str, Any]:
    """Serialize a system profile to a JSON-safe dict."""
    return {
        "athlete_name": profile.athlete_name,
        "system_count": profile.system_count,
        "total_techniques": profile.total_techniques,
        "diversity": profile.diversity,
        "dominant_type": profile.dominant_type,
        "composition_vector": [round(v, 4) for v in profile.composition_vector],
        "systems": [
            {
                "name": s.name,
                "hub": s.hub,
                "hub_type": s.hub_type,
                "members": s.members,
                "type_vector": s.type_vector,
                "size": s.size,
                "system_elo": s.system_elo,
                "transition_count": s.transition_count,
                "internal_edges": [
                    {"source": src, "target": tgt, "count": c}
                    for src, tgt, c in s.internal_edges
                ],
            }
            for s in profile.systems
        ],
    }
