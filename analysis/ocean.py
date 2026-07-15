"""The Ocean — full technique force-graph payload.

Turns the grappling map (``analysis.grappling_map``) into the data the public "The Ocean" page
renders: every observed position as a node coloured by **region** (community), each carrying
quantitative metrics expressed **relative to the population mean** (percentile + ratio), so the
node dialog never shows a raw rating (same rule as Grappling ELO).

Metrics per node: frequency (occ), centrality (pagerank), bridging (betweenness), favorability
(reward/risk) for every node, plus ADCC submission ``effectiveness_score`` where it exists.
Regions are auto-named after each community's most-used technique.

``ocean_from_map`` is pure over an assembled map (unit-testable); ``build_ocean`` is the session
wrapper that also attaches semantic+structural neighbours.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name, canonicalize
from analysis.network_metrics import edge_arrow, edge_dashed
from analysis.technique_match import clean_label

_EFF_PATH = (Path(__file__).resolve().parent.parent
             / "data" / "processed" / "technique_effectiveness.json")
# distinct hues for regions (community 0..N ordered by size)
_REGION_PALETTE = ["#4d86ff", "#fc4c02", "#2dd4bf", "#a78bfa", "#fbbf24",
                   "#f87171", "#34d399", "#f0883e", "#60a5fa"]
_NO_REGION = "#5b5b66"
_METRIC_SRC = {"frequency": "occ", "centrality": "pagerank",
               "bridging": "betweenness", "favorability": "reward_risk"}


def _effectiveness_index() -> dict[str, float]:
    """node_key → ADCC effectiveness_score. Keys (e.g. 'Mata-Leão') canonicalize via clean_label."""
    if not _EFF_PATH.exists():
        return {}
    raw = json.loads(_EFF_PATH.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for name, v in raw.items():
        score = v.get("effectiveness_score") if isinstance(v, dict) else None
        if score is None:
            continue
        key = canonicalize(_normalize_name(clean_label(str(name), "submission")))
        if key:
            out[key] = float(score)  # ponytail: synonym collision → last score wins (rare)
    return out


def _percentile(population: list[float], x: float) -> int:
    """% of the population ≤ x (0..100)."""
    return round(sum(1 for v in population if v <= x) / len(population) * 100) if population else 0


def _ratio(x: float, mean: float) -> float:
    return round(x / mean, 2) if mean else 0.0


def relativize(nodes: list[dict[str, Any]], eff_index: dict[str, float] | None = None) -> None:
    """Stamp each node with ``metrics`` = percentile + ratio-vs-mean for every metric (mutates)."""
    eff = _effectiveness_index() if eff_index is None else eff_index
    pops = {m: [float(n.get(src) or 0.0) for n in nodes] for m, src in _METRIC_SRC.items()}
    means = {m: (statistics.fmean(v) if v else 0.0) for m, v in pops.items()}
    eff_vals = [eff[n["node_key"]] for n in nodes if n["node_key"] in eff]
    eff_mean = statistics.fmean(eff_vals) if eff_vals else 0.0
    for n in nodes:
        m: dict[str, Any] = {}
        for metric, src in _METRIC_SRC.items():
            val = float(n.get(src) or 0.0)
            m[metric] = {"pct": _percentile(pops[metric], val),
                         "ratio": _ratio(val, means[metric]), "raw": round(val, 4)}
        ek = n["node_key"]
        if ek in eff:
            m["effectiveness"] = {"pct": _percentile(eff_vals, eff[ek]),
                                  "ratio": _ratio(eff[ek], eff_mean), "raw": round(eff[ek], 3)}
        n["metrics"] = m


def name_regions(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group nodes by community → named, coloured regions (mutates node region/color). Largest
    community first; named after its most-used technique."""
    comms: dict[int, list[dict[str, Any]]] = {}
    for n in nodes:
        c = n.get("community")
        if c is not None:
            comms.setdefault(int(c), []).append(n)
    regions: list[dict[str, Any]] = []
    for idx, (_cid, members) in enumerate(sorted(comms.items(), key=lambda kv: -len(kv[1]))):
        color = _REGION_PALETTE[idx % len(_REGION_PALETTE)]
        top = max(members, key=lambda n: n.get("occ", 0))
        for n in members:
            n["region"] = idx
            n["color"] = color
        regions.append({"id": idx, "name": f"{top['label']} system",
                        "color": color, "count": len(members)})
    for n in nodes:
        if n.get("region") is None:
            n["region"] = None
            n["color"] = _NO_REGION
    return regions


def _clamp3(n: int) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


def _direct_map_links(
    edges: list[dict[str, Any]], node_type: dict[str, str],
) -> list[dict[str, Any]]:
    """Collapse a directed edge list into one link per unordered pair (rule 1 — no split,
    two-way stays undirected) and dash the low-success edges (rule 2, fixed threshold — see
    ``network_metrics.edge_dashed``). Edges carry ``count``/``ok``/``rev`` from
    ``map_from_network``."""
    by_pair: dict[frozenset[str], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for e in edges:
        by_pair[frozenset((e["source"], e["target"]))][(e["source"], e["target"])] = e

    out: list[dict[str, Any]] = []
    for pair, dirs in by_pair.items():
        u, v = tuple(pair)
        e_fwd, e_bwd = dirs.get((u, v)), dirs.get((v, u))
        f = e_fwd["count"] if e_fwd else 0
        r = e_bwd["count"] if e_bwd else 0
        arrow = edge_arrow(f, r)
        frm, to, maj = (u, v, e_fwd) if f >= r else (v, u, e_bwd)
        weight = max(f, r)
        dashed = bool(maj) and edge_dashed(weight, maj.get("ok", 0), node_type.get(to, ""))
        out.append({
            "from": frm, "to": to, "weight": _clamp3(weight), "arrow": arrow, "dashed": dashed,
        })
    return out


def ocean_from_map(
    gmap: dict[str, Any], eff_index: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Pure: assembled map → The Ocean payload (observed nodes only, with relative metrics)."""
    nodes = [n for n in gmap["nodes"].values() if n.get("observed")]
    relativize(nodes, eff_index)
    regions = name_regions(nodes)
    keep = {n["node_key"] for n in nodes}
    node_type = {n["node_key"]: n["type"] for n in nodes}
    pr_max = max((n["pagerank"] for n in nodes), default=0.0) or 1.0
    out_nodes = [{
        "id": n["node_key"], "label": n["label"], "pt": n.get("pt", ""), "type": n["type"],
        "region": n["region"], "color": n["color"],
        "size": 1 + round(2 * n["pagerank"] / pr_max),
        "occ": n["occ"], "community": n.get("community"),
        "metrics": n["metrics"],
        "neighbours": [nb for nb in n.get("neighbours", []) if nb["node_key"] in keep][:6],
    } for n in nodes]
    qualifying = [e for e in gmap["edges"]
                  if not e["suggested"] and e["source"] in keep and e["target"] in keep]
    out_links = _direct_map_links(qualifying, node_type)
    return {"nodes": out_nodes, "links": out_links, "regions": regions,
            "meta": {"positions": len(out_nodes), "transitions": len(out_links)}}


def build_ocean(session: Any) -> dict[str, Any]:
    """Session wrapper: assemble the map, attach hybrid neighbours, build the Ocean payload."""
    from analysis.embeddings import semantic_neighbours_fn
    from analysis.grappling_map import attach_neighbors, build_grappling_map
    from analysis.vector_store import structural_neighbours_fn

    gmap = build_grappling_map(session)
    graph = gmap.pop("_graph")
    attach_neighbors(gmap, semantic_neighbours_fn(session), structural_neighbours_fn(graph))
    return ocean_from_map(gmap)
