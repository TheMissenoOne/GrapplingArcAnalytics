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
import math
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

# The page used to dump all 272 observed positions on one force-sim canvas with no
# hierarchy — it read as noise, not a map. Keep only the top slice by importance (owner's
# verdict, 2026-08-26); 25% of a ~272-node corpus lands at ~68, inside the 60-70 the owner
# asked to tune toward. MIN_KEEP_NODES is a floor so a small/test corpus is never gutted by
# the percentage (a 4-node fixture keeps all 4, not 1).
TOP_IMPORTANCE_PCT = 25
MIN_KEEP_NODES = 40
_GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))  # phyllotaxis: even angular spread, no RNG
_LAYOUT_R = 150.0  # px-ish spread, matches the old cos(i)*120 random-init scale


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


def _importance(n: dict[str, Any]) -> float:
    """Composite 0..100 importance: how central + how bridging + how often played.

    Favorability is deliberately excluded — that metric says whether a position is GOOD for
    the athlete in it, not whether the position matters to the map's structure."""
    m = n["metrics"]
    return (m["centrality"]["pct"] + m["bridging"]["pct"] + m["frequency"]["pct"]) / 3


def _radial_layout(nodes: list[dict[str, Any]]) -> None:
    """Stamp deterministic sunflower-spiral ``x``/``y``/``imp`` by importance rank (mutates).

    Rank 0 (most important) sits nearest the centre; radius grows with ``sqrt(rank)`` so ring
    density stays roughly uniform instead of crowding the middle (standard phyllotaxis
    spacing). No RNG anywhere — the exporter runs once and the browser must draw the same map
    on every load (`site/graph.js` uses these coordinates as its seed instead of
    ``Math.random()`` when a node carries them).
    """
    total = len(nodes)
    ranked = sorted(nodes, key=lambda n: -_importance(n))
    for rank, n in enumerate(ranked):
        n["imp"] = 1.0 if total <= 1 else round(1 - rank / (total - 1), 3)
        radius = _LAYOUT_R * math.sqrt((rank + 0.5) / total)
        angle = rank * _GOLDEN_ANGLE
        n["x"] = round(radius * math.cos(angle), 1)
        n["y"] = round(radius * math.sin(angle), 1)


def _filter_top_importance(
    nodes: list[dict[str, Any]], pct: int = TOP_IMPORTANCE_PCT,
) -> list[dict[str, Any]]:
    """Keep the top ``pct``% of ``nodes`` by :func:`_importance`, floored at
    :data:`MIN_KEEP_NODES`, then lay the survivors out radially (mutates + returns a subset).
    """
    ranked = sorted(nodes, key=lambda n: -_importance(n))
    keep_n = min(len(ranked), max(MIN_KEEP_NODES, math.ceil(len(ranked) * pct / 100)))
    kept = ranked[:keep_n]
    _radial_layout(kept)
    return kept


def ocean_from_map(
    gmap: dict[str, Any], eff_index: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Pure: assembled map → The Ocean payload (top-importance nodes only, relative metrics)."""
    nodes = [n for n in gmap["nodes"].values() if n.get("observed")]
    total_observed = len(nodes)
    relativize(nodes, eff_index)
    regions = name_regions(nodes)  # names/colours off the FULL population's communities
    nodes = _filter_top_importance(nodes)
    keep = {n["node_key"] for n in nodes}
    node_type = {n["node_key"]: n["type"] for n in nodes}
    out_nodes = [{
        "id": n["node_key"], "label": n["label"], "pt": n.get("pt", ""), "type": n["type"],
        "region": n["region"], "color": n["color"],
        "size": 1 + round(3 * n["imp"]), "x": n["x"], "y": n["y"], "imp": n["imp"],
        "occ": n["occ"], "community": n.get("community"),
        "metrics": n["metrics"],
        "neighbours": [nb for nb in n.get("neighbours", []) if nb["node_key"] in keep][:6],
    } for n in nodes]
    qualifying = [e for e in gmap["edges"]
                  if not e["suggested"] and e["source"] in keep and e["target"] in keep]
    out_links = _direct_map_links(qualifying, node_type)
    return {"nodes": out_nodes, "links": out_links, "regions": regions,
            "meta": {"positions": len(out_nodes), "transitions": len(out_links),
                     "total_positions": total_observed}}


# ── Markov backbone panel (global Lamas-chain transitions) ──────────────────────
_MARKOV_NOTE = (
    "`successful` está ausente na maior parte do corpus e é lida como tentativa — toda "
    "probabilidade aqui é um piso, não uma estimativa (ver analysis/lamas_chain.py)."
)


def _top_transitions(block: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    """Pure: a ``markov_block`` payload → its ``top_n`` biggest counted routes.

    Reads the same ``counts``/``probs`` matrices ``markov_block`` already publishes; this
    just picks the routes worth drawing on a compact panel instead of the full 12x12 grid.
    """
    from analysis.lamas_chain import STATES

    top: list[dict[str, Any]] = []
    for i, src in enumerate(STATES):
        for j, dst in enumerate(STATES):
            n = block["counts"][i][j]
            if n > 0:
                top.append({"from": src, "to": dst, "n": n, "prob": block["probs"][i][j]})
    top.sort(key=lambda t: -t["n"])
    return top[:top_n]


def markov_backbone(session: Any, top_n: int = 10) -> dict[str, Any]:
    """Global Lamas-chain transition backbone, off every final ``matches`` row.

    Public competition data only — ``matches`` has no owner_kind split, it IS the public
    corpus (see this module's file header / the repo's public-vs-private rule). Bootstrap
    intervals are skipped (``n_boot=0``, `lamas_chain.markov_block`'s own cheap mode): this
    panel names the biggest counted routes, not a confidence claim.
    """
    from sqlalchemy import select

    from analysis.lamas_chain import STATE_DEFS, STATES, markov_block
    from db.models import Match

    rows = session.execute(
        select(Match.id, Match.sequence, Match.win_type, Match.winner_id,
               Match.athlete_a_id, Match.athlete_b_id)
        .where(Match.status == "final", Match.sequence.isnot(None))
    ).all()
    bouts = [{"id": r[0], "seq": r[1], "win_type": r[2], "winner": r[3],
              "a_id": r[4], "b_id": r[5]} for r in rows]
    if not bouts:
        return {"states": [], "top": [], "n_bouts": 0, "note": _MARKOV_NOTE}
    block = markov_block(bouts, n_boot=0)
    return {
        "states": [{"code": s, "definition": STATE_DEFS[s]} for s in STATES],
        "top": _top_transitions(block, top_n),
        "n_bouts": block["n_bouts"],
        "note": _MARKOV_NOTE,
    }


# ── ELO distribution panel (per-position-family spread, athlete corpus) ─────────
def _elo_buckets(by_type: dict[str, tuple[float, float, int]]) -> list[dict[str, Any]]:
    """Pure: ``node_population_stats``' by-type stats → relative, sorted buckets.

    Never a raw rating (Grappling ELO presentation convention, see `domain-reference`): each
    bucket is a ratio against the corpus's own grand mean, plus a coefficient of variation
    (``std/mean``) as the "spread" signal, both dimensionless.
    """
    from analysis.deviance import MIN_POP

    usable = {t: v for t, v in by_type.items() if v[2] >= MIN_POP}
    means = [mean for mean, _std, _n in usable.values()]
    grand_mean = statistics.fmean(means) if means else 0.0
    if not grand_mean:
        return []
    buckets = [
        {"type": t, "n": n, "ratio": round(mean / grand_mean, 2),
         "spread": round(std / mean, 2) if mean else 0.0}
        for t, (mean, std, n) in usable.items()
    ]
    buckets.sort(key=lambda b: -b["ratio"])
    return buckets


def elo_distribution(session: Any) -> dict[str, Any]:
    """Per-position-family Grappling ELO spread across the athlete corpus.

    ``owner_kind='athlete'`` is MANDATORY — this feeds the public site (public/private data
    rule, root CLAUDE.md). Reuses `db.repository.graphs_for_clustering` +
    `analysis.deviance.node_population_stats`, the same pair `export/ontology.py`'s
    per-athlete deviance already stands on, so this map cannot drift from that population.
    """
    from analysis.deviance import TYPES, grappling_nodes, node_population_stats
    from db.repository import graphs_for_clustering

    graphs = graphs_for_clustering(session, owner_kind="athlete")
    rows = [(gid, gn) for gid, raw in graphs if len(gn := grappling_nodes(raw)) >= 3]
    if not rows:
        return {"buckets": [], "types": TYPES}
    _by_key, by_type = node_population_stats(rows)
    return {"buckets": _elo_buckets(by_type), "types": TYPES}


def build_ocean(session: Any) -> dict[str, Any]:
    """Session wrapper: assemble the map, attach hybrid neighbours, build the Ocean payload
    plus the Markov backbone and ELO distribution panels."""
    from analysis.embeddings import semantic_neighbours_fn
    from analysis.grappling_map import attach_neighbors, build_grappling_map
    from analysis.vector_store import structural_neighbours_fn

    gmap = build_grappling_map(session)
    graph = gmap.pop("_graph")
    attach_neighbors(gmap, semantic_neighbours_fn(session), structural_neighbours_fn(graph))
    ocean = ocean_from_map(gmap)
    ocean["markov"] = markov_backbone(session)
    ocean["elo"] = elo_distribution(session)
    return ocean
