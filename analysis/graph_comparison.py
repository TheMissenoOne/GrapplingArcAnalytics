"""Compare user technique graph vs competition fighters' graphs.

Builds per-fighter technique graphs from flat competition match JSON
(``_analytics_export.json``), then compares them to the user's graph
(from ``user_data_*.json``) for:
- Technical similarity (per-technique cosine)
- Positional strengths (node ELO/proficiency comparison)
- Transition similarity (edge overlap)
- Radar chart data (user + top-k fighters on same axes)
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from analysis.user_profile import TYPES, normalize_technique_name, user_graph_profile

logger = logging.getLogger(__name__)

MIN_FIGHTER_EVENTS = 5
RADAR_TECH_MAX = 12


# ── Graph building from competition data ──

def _athlete_key(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", "", name.lower().strip())
    return re.sub(r"\s+", " ", n).strip()


def build_fighter_graph(
    matches: list[dict[str, Any]],
    fighter_name: str,
) -> dict[str, Any]:
    """Build a technique graph for one fighter from their match events.

    Nodes = unique technique labels seen in the fighter's own events.
    Edges = consecutive same-match events where both belong to the fighter.

    Returns dict with ``nodes``, ``edges``, ``technique_vec``, ``profile``.
    """
    key = _athlete_key(fighter_name)
    node_counts: Counter[str] = Counter()
    node_successes: Counter[str] = Counter()
    node_types: dict[str, str] = {}
    edge_counts: Counter[tuple[str, str]] = Counter()

    for m in matches:
        winner = m.get("fighter", "")
        opponent = m.get("opponent", "")
        if _athlete_key(winner) != key:
            continue

        seq = m.get("sequence", [])
        actor_events: list[dict[str, Any]] = []
        for e in seq:
            if _athlete_key(e.get("actor", "")) == key:
                label = e.get("label", "")
                if not label:
                    continue
                actor_events.append(e)

        for e in actor_events:
            label = e.get("label", "")
            node_counts[label] += 1
            node_types.setdefault(label, e.get("type", ""))
            if e.get("successful", False):
                node_successes[label] += 1

        for i in range(1, len(actor_events)):
            prev = actor_events[i - 1].get("label", "")
            curr = actor_events[i].get("label", "")
            if prev and curr and prev != curr:
                edge_counts[(prev, curr)] += 1

    total = sum(node_counts.values())
    if total < MIN_FIGHTER_EVENTS:
        return {"nodes": [], "edges": [], "technique_vec": [],
                "profile": {"name": fighter_name, "total_events": total, "has_profile": False}}

    max_count = max(node_counts.values()) or 1

    nodes: list[dict[str, Any]] = []
    for label, count in node_counts.most_common():
        success_rate = node_successes.get(label, 0) / count if count > 0 else 0.0
        usage_rate = count / total
        proficiency = (usage_rate * 0.6) + (success_rate * 0.4)
        normalized_proficiency = proficiency * 1000
        nodes.append({
            "label": label,
            "type": node_types.get(label, ""),
            "count": count,
            "usage_rate": round(usage_rate, 4),
            "success_rate": round(success_rate, 4),
            "proficiency": round(normalized_proficiency, 2),
            "usage_norm": round(count / max_count, 4),
        })

    edges: list[dict[str, Any]] = []
    for (src, tgt), count in edge_counts.most_common():
        edges.append({
            "source": src,
            "target": tgt,
            "count": count,
        })

    technique_vec = _build_technique_vector(fighter_name, matches, node_types)

    return {
        "nodes": nodes,
        "edges": edges,
        "technique_vec": technique_vec,
        "profile": {
            "name": fighter_name,
            "key": key,
            "total_events": total,
            "unique_techniques": len(nodes),
            "unique_edges": len(edges),
            "has_profile": True,
        },
    }


def _build_technique_vector(
    fighter_name: str,
    matches: list[dict[str, Any]],
    node_types: dict[str, str],
) -> dict[str, float]:
    """Build technique-level vector: normalized label → usage_proportion."""
    key = _athlete_key(fighter_name)
    counts: Counter[str] = Counter()
    for m in matches:
        if _athlete_key(m.get("fighter", "")) != key:
            continue
        for e in m.get("sequence", []):
            if _athlete_key(e.get("actor", "")) == key:
                label = e.get("label", "").lower().strip()
                if label:
                    counts[label] += 1
    total = sum(counts.values()) or 1
    return {lbl: round(c / total, 5) for lbl, c in counts.most_common()}


# ── User graph loading ──

def load_user_graph(user_json_path: str | Path) -> dict[str, Any]:
    """Load user graph from user_data JSON and normalize labels to English."""
    with open(user_json_path) as f:
        raw: dict[str, Any] = json.load(f)

    graph = raw.get("graph", {})
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])

    translated_labels: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for n in raw_nodes:
        nd = n.get("data", {})
        label = n.get("label", "")
        eng = normalize_technique_name(label)
        translated_labels[label] = eng
        nodes.append({
            "id": n.get("id", ""),
            "label": label,
            "english": eng,
            "type": n.get("type", ""),
            "node_type": nd.get("type", ""),
            "elo": nd.get("computedElo"),
            "usage": nd.get("usageCount", 0) or 0,
            "trend": nd.get("trend", ""),
        })

    edges: list[dict[str, Any]] = []
    for e in raw_edges:
        edges.append({
            "source": e.get("source", ""),
            "target": e.get("target", ""),
            "data": e.get("data", {}),
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "translated_labels": translated_labels,
        "profile": user_graph_profile(raw),
    }


# ── Technical similarity ──

def _build_all_techniques(
    user_graph: dict[str, Any],
    fighter_graphs: dict[str, dict[str, Any]],
) -> list[str]:
    """Build a sorted list of all unique technique labels across user + fighters."""
    tech_set: set[str] = set()
    for n in user_graph.get("nodes", []):
        eng = n.get("english", "")
        if eng:
            tech_set.add(eng.lower().strip())
    for fname, fg in fighter_graphs.items():
        for n in fg.get("nodes", []):
            label = n.get("label", "")
            if label:
                tech_set.add(label.lower().strip())
    return sorted(tech_set)


def compute_technique_similarity(
    user_graph: dict[str, Any],
    fighter_graphs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank fighters by cosine similarity on per-technique usage vectors.

    Both user and fighter vectors are normalized to lowercase keys for matching.
    Uses the 8-bucket type vector for broad style comparison, plus per-technique
    vector for finer granularity.
    """
    user_vec = _user_technique_vector(user_graph)
    if not user_vec:
        return []

    results: list[dict[str, Any]] = []
    for fname, fg in fighter_graphs.items():
        if not fg.get("profile", {}).get("has_profile", False):
            continue
        fvec = fg.get("technique_vec", {})
        if not fvec:
            continue

        # Normalize both sides to lowercase for matching
        fvec_lower = {k.lower().strip(): v for k, v in fvec.items()}
        user_lower = {k.lower().strip(): v for k, v in user_vec.items()}
        all_techs = sorted(set(user_lower.keys()) | set(fvec_lower.keys()))
        um = np.array([user_lower.get(t, 0.0) for t in all_techs], dtype=np.float64)
        fm = np.array([fvec_lower.get(t, 0.0) for t in all_techs], dtype=np.float64)
        un = np.linalg.norm(um)
        fn = np.linalg.norm(fm)
        if un == 0 or fn == 0:
            continue
        sim = float(um @ fm) / (un * fn)
        results.append({
            "name": fname,
            "similarity": round(sim, 4),
            "total_events": fg["profile"]["total_events"],
            "unique_techniques": fg["profile"]["unique_techniques"],
        })

    results.sort(key=lambda x: -x["similarity"])
    return results


def _user_technique_vector(user_graph: dict[str, Any]) -> dict[str, float]:
    """Build technique usage vector from user graph nodes.

    Maps Portuguese labels → English via normalization, returns label → usage_norm.
    Falls back to raw label when no translation exists.
    """
    nodes = user_graph.get("nodes", [])
    max_usage = max((n.get("usage", 0) for n in nodes), default=1)
    vec: dict[str, float] = {}
    for n in nodes:
        eng = n.get("english", "")
        key = eng.lower().strip() if eng else n.get("label", "").lower().strip()
        if key:
            vec[key] = (n.get("usage", 0) or 0) / max_usage
    if not vec:
        return {}
    total = sum(vec.values()) or 1
    return {k: v / total for k, v in vec.items()}


# ── Positional strengths comparison ──

def compare_positions(
    user_graph: dict[str, Any],
    fighter_graphs: dict[str, dict[str, Any]],
    similarity_ranking: list[dict[str, Any]] | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """Compare user's strongest techniques vs each fighter's strongest.

    If ``similarity_ranking`` is given, comparisons are ordered by similarity (best first).
    Otherwise they are sorted alphabetically.

    Returns ``user_top``, ``fighter_top`` per fighter, and ``overlaps``.
    """
    user_nodes = user_graph.get("nodes", [])
    user_top = sorted(
        [n for n in user_nodes if n.get("elo") is not None],
        key=lambda x: x["elo"] or 0,
        reverse=True,
    )[:top_n]

    # Build a set of all fighter names that have profiles
    fighter_names = [fname for fname, fg in fighter_graphs.items()
                     if fg.get("profile", {}).get("has_profile", False)]

    # Order by similarity if available
    if similarity_ranking:
        order = {s["name"]: i for i, s in enumerate(similarity_ranking)}
        fighter_names.sort(key=lambda n: order.get(n, 999))

    results: list[dict] = []
    for fname in fighter_names:
        fg = fighter_graphs[fname]
        fg_nodes = fg.get("nodes", [])
        fg_top = sorted(fg_nodes, key=lambda x: x.get("proficiency", 0), reverse=True)[:top_n]

        user_labels_lower = {n.get("english", "").lower().strip() for n in user_top if n.get("english")}
        fg_labels = {n.get("label", "").lower().strip() for n in fg_top}
        overlap = user_labels_lower & fg_labels

        results.append({
            "fighter": fname,
            "user_top": [
                {"label": n.get("label"), "english": n.get("english", ""),
                 "elo": n.get("elo")} for n in user_top
            ],
            "fighter_top": [
                {"label": n.get("label"), "proficiency": n.get("proficiency")} for n in fg_top
            ],
            "overlapping_techniques": sorted(overlap),
            "overlap_count": len(overlap),
        })

    return {"user_top": [{"label": n.get("label"), "english": n.get("english", ""),
                          "elo": n.get("elo")} for n in user_top],
            "comparisons": results}


# ── Radar chart data ──

def radar_data(
    user_graph: dict[str, Any],
    fighter_graphs: dict[str, dict[str, Any]],
    similarity_ranking: list[dict[str, Any]],
    k: int = 3,
) -> dict[str, Any]:
    """Build radar chart series for user + top-k similar fighters.

    Returns:
      ``series`` — list of {name, values: [per-type], types: [label]}.
      ``technique_radar`` — per-technique values for top-12 techniques.
    """
    # Type-level radar
    user_types = _user_type_vector(user_graph)
    top_fighters = [s["name"] for s in similarity_ranking[:k]
                    if s["name"] in fighter_graphs]

    series = [{"name": "You (user)", "values": user_types}]
    for fname in top_fighters:
        fg = fighter_graphs[fname]
        fvec = _fighter_type_vector(fg)
        series.append({"name": fname, "values": fvec})

    # Technique-level radar — top RadarTechMax techniques by combined user+fighter usage
    tech_scores: Counter[str] = Counter()
    for n in user_graph.get("nodes", []):
        eng = n.get("english", "")
        if eng:
            tech_scores[eng.lower().strip()] += n.get("usage", 0) or 0
        else:
            raw = n.get("label", "").lower().strip()
            if raw:
                tech_scores[raw] += n.get("usage", 0) or 0
    for fname in top_fighters:
        fg = fighter_graphs[fname]
        for n in fg.get("nodes", []):
            label = n.get("label", "").lower().strip()
            tech_scores[label] += n.get("count", 0)
    top_techs = [t for t, _ in tech_scores.most_common(RADAR_TECH_MAX)]

    tech_series = [{"name": "You (user)", "values": []}]
    user_usage: dict[str, float] = {}
    for n in user_graph.get("nodes", []):
        eng = n.get("english", "").lower().strip()
        if eng:
            user_usage[eng] = n.get("usage", 0) or 0
        raw = n.get("label", "").lower().strip()
        if raw and raw not in user_usage:
            user_usage[raw] = n.get("usage", 0) or 0
    user_max = max(user_usage.values()) or 1
    uv = [user_usage.get(t, 0) / user_max for t in top_techs]
    tech_series[0]["values"] = [round(v, 4) for v in uv]

    for fname in top_fighters:
        fg = fighter_graphs[fname]
        fv_counts: dict[str, float] = {}
        for n in fg.get("nodes", []):
            lbl = n.get("label", "").lower().strip()
            fv_counts[lbl] = fv_counts.get(lbl, 0) + (n.get("count", 0) or 0)
        fmax = max(fv_counts.values()) or 1
        fv = [fv_counts.get(t, 0) / fmax for t in top_techs]
        tech_series.append({"name": fname, "values": [round(v, 4) for v in fv]})

    return {
        "type_radar": {
            "series": series,
            "types": TYPES,
        },
        "technique_radar": {
            "series": tech_series,
            "techniques": top_techs,
        },
    }


def _user_type_vector(user_graph: dict[str, Any]) -> list[float]:
    """8-bucket type vector from user graph node types."""
    counts: Counter[str] = Counter()
    for n in user_graph.get("nodes", []):
        t = n.get("node_type", "")
        if t in TYPES:
            counts[t] += n.get("usage", 0) or 0
        else:
            t2 = n.get("type", "")
            if t2 in TYPES:
                counts[t2] += n.get("usage", 0) or 0
    total = sum(counts.values()) or 1
    return [round(counts.get(t, 0) / total, 4) for t in TYPES]


def _fighter_type_vector(fighter_graph: dict[str, Any]) -> list[float]:
    """8-bucket type vector from fighter graph nodes."""
    counts: Counter[str] = Counter()
    for n in fighter_graph.get("nodes", []):
        t = n.get("type", "")
        if t in TYPES:
            counts[t] += n.get("count", 0)
    total = sum(counts.values()) or 1
    return [round(counts.get(t, 0) / total, 4) for t in TYPES]


# ── Edge / transition similarity ──

def compare_transitions(
    user_graph: dict[str, Any],
    fighter_graphs: dict[str, dict[str, Any]],
    similarity_ranking: list[dict[str, Any]],
    k: int = 3,
) -> dict[str, Any]:
    """Compare user's transition patterns vs nearest fighters.

    Builds edge-overlap and transition-distribution similarity.
    """
    user_edges = _user_edge_map(user_graph)
    top_fighters = [s["name"] for s in similarity_ranking[:k]
                    if s["name"] in fighter_graphs]

    results: list[dict] = []
    for fname in top_fighters:
        fg = fighter_graphs[fname]
        fg_edges = {(e["source"], e["target"]): e["count"]
                     for e in fg.get("edges", [])}
        overlap = set(user_edges.keys()) & set(fg_edges.keys())
        union = set(user_edges.keys()) | set(fg_edges.keys())
        jaccard = len(overlap) / len(union) if union else 0.0

        overlap_edges: list[dict] = []
        for edge in sorted(overlap):
            overlap_edges.append({
                "source": edge[0],
                "target": edge[1],
                "user_count": user_edges[edge],
                "fighter_count": fg_edges[edge],
            })

        results.append({
            "fighter": fname,
            "jaccard_similarity": round(jaccard, 4),
            "overlap_count": len(overlap),
            "user_unique": len(user_edges) - len(overlap),
            "fighter_unique": len(fg_edges) - len(overlap),
            "overlap_edges": overlap_edges,
        })

    return {
        "user_edge_count": len(user_edges),
        "user_edges": [{"source": s, "target": t, "count": c}
                       for (s, t), c in sorted(user_edges.items(), key=lambda x: -x[1])[:20]],
        "comparisons": results,
    }


def _user_edge_map(user_graph: dict[str, Any]) -> dict[tuple[str, str], int]:
    """Build edge map from user graph, mapping node IDs → English labels."""
    trans = user_graph.get("translated_labels", {})
    # Build node_id → English label lookup
    id_to_eng: dict[str, str] = {}
    for n in user_graph.get("nodes", []):
        nid = n.get("id", "")
        label = n.get("label", "")
        eng = trans.get(label, label).lower().strip()
        if nid:
            id_to_eng[nid] = eng
    # Also add a reverse label→english mapping for edges that use label as id
    for n in user_graph.get("nodes", []):
        label = n.get("label", "").lower().strip()
        eng = trans.get(label, label).lower().strip()
        if label:
            id_to_eng.setdefault(label, eng)
            id_to_eng.setdefault(label.replace(" ", "-"), eng)
    edges: dict[tuple[str, str], int] = {}
    for e in user_graph.get("edges", []):
        src_id = e.get("source", "")
        tgt_id = e.get("target", "")
        src_eng = id_to_eng.get(src_id) or src_id.lower().strip()
        tgt_eng = id_to_eng.get(tgt_id) or tgt_id.lower().strip()
        if src_eng and tgt_eng and src_eng != tgt_eng:
            key = (src_eng, tgt_eng)
            edges[key] = edges.get(key, 0) + 1
    return edges


# ── Aggregate comparison ──

def build_all_fighter_graphs(
    matches: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build graphs for all fighters with enough event data."""
    fighter_events: Counter[str] = Counter()
    for m in matches:
        winner = m.get("fighter", "")
        for e in m.get("sequence", []):
            if _athlete_key(e.get("actor", "")) == _athlete_key(winner):
                fighter_events[winner] += 1

    graphs: dict[str, dict[str, Any]] = {}
    for fname in sorted(fighter_events):
        if fighter_events[fname] < MIN_FIGHTER_EVENTS:
            continue
        try:
            g = build_fighter_graph(matches, fname)
            if g["profile"]["has_profile"]:
                graphs[fname] = g
        except Exception:
            logger.warning("Failed to build graph for %s", fname)
    return graphs


def full_comparison(
    user_json_path: str | Path,
    competition_path: str | Path = "_analytics_export.json",
) -> dict[str, Any]:
    """Run full graph comparison pipeline.

    Returns dict with:
    - user_graph: user's technique graph (nodes+edges)
    - fighter_graphs: dict[fighter_name, graph]
    - tech_similarity: cosine ranking
    - position_comparison: positional strengths
    - radar: type + technique radar data
    - transition_comparison: edge similarity
    - meta: counts, settings
    """
    # Load data
    with open(competition_path) as f:
        matches: list[dict[str, Any]] = json.load(f)
    user_graph = load_user_graph(user_json_path)

    # Build all fighter graphs
    fighter_graphs = build_all_fighter_graphs(matches)

    # Technical similarity
    tech_sim = compute_technique_similarity(user_graph, fighter_graphs)

    # Positional comparison
    pos_comp = compare_positions(user_graph, fighter_graphs, similarity_ranking=tech_sim)

    # Radar data
    rad = radar_data(user_graph, fighter_graphs, tech_sim)

    # Transition comparison
    trans_comp = compare_transitions(user_graph, fighter_graphs, tech_sim)

    return {
        "meta": {
            "user_node_count": len(user_graph["nodes"]),
            "user_edge_count": len(user_graph["edges"]),
            "fighter_count": len(fighter_graphs),
            "fighters_with_graphs": sorted(fighter_graphs.keys()),
        },
        "user_graph": {
            "nodes": user_graph["nodes"],
            "edges": user_graph["edges"],
        },
        "fighter_graphs": fighter_graphs,
        "similarity_ranking": tech_sim[:10] if tech_sim else [],
        "positional_comparison": pos_comp,
        "radar": rad,
        "transition_comparison": trans_comp,
    }


def _to_json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_to_json_safe(v) for v in obj]
    if hasattr(obj, "dtype"):
        return obj.item() if obj.ndim == 0 else obj.tolist()
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def export_comparison(
    user_json_path: str | Path,
    output_path: str | Path = "graph_comparison.json",
    competition_path: str | Path = "_analytics_export.json",
) -> str:
    """Generate and write graph comparison JSON."""
    result = full_comparison(user_json_path, competition_path)
    safe = _to_json_safe(result)
    out = Path(output_path)
    with open(out, "w") as f:
        json.dump(safe, f, indent=2, default=str, ensure_ascii=False)
    logger.info("Exported graph comparison (%d fighters) → %s", len(safe["meta"]["fighters_with_graphs"]), out)
    return str(out)
