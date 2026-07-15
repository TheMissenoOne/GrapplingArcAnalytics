"""The general grappling map — one canonical position/transition graph.

Assembles a single navigable map of grappling, GrappleMap-inspired but built from *our* data:
nodes = positions/techniques (every move observed in matches ∪ the canonical technique library),
edges = within-actor transitions observed in matches (weighted, with reward/risk), enriched with
PageRank/centrality, community, and submission effectiveness. A vector layer (semantic +
structural, see ``analysis.embeddings`` / ``analysis.vector_store``) attaches "related positions"
per node and proposes *suggested* transitions not yet observed.

``build_grappling_map`` is the core assembler (reads the corpus once via
``network_metrics.build_transition_network``); ``attach_neighbors`` is a pure enrichment hook so
the vector layer stays decoupled and the assembly is unit-testable without a model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name, canonical_label, canonicalize
from analysis.network_metrics import (
    build_transition_network,
    detect_communities,
    node_centralities,
)

_LIB_PATH = Path(__file__).resolve().parent / "data" / "technique_library.json"

# (node_key, k) -> [(neighbour_node_key, score)]
NeighbourFn = Callable[[str, int], list[tuple[str, float]]]


def _library() -> dict[str, dict[str, Any]]:
    """node_key → {label, type, pt} from the committed technique library."""
    data = json.loads(_LIB_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for t in data:
        en = str(t.get("en", "")).strip()
        key = canonicalize(_normalize_name(en))
        if key:
            out[key] = {"label": en, "type": str(t.get("type", "")), "pt": str(t.get("pt", ""))}
    return out


def build_grappling_map(session: Any) -> dict[str, Any]:
    """Assemble the canonical map from the match corpus + library (session wrapper)."""
    return map_from_network(build_transition_network(session))


def map_from_network(g: Any) -> dict[str, Any]:
    """Assemble the canonical map (nodes + observed edges) from a transition network.

    Returns ``{"nodes": {key: node}, "edges": [edge], "_graph": DiGraph}`` — ``_graph`` is the
    underlying network kept for the vector/structural step (stripped before export).
    """
    cents = node_centralities(g)
    comm_of: dict[str, int] = {}
    for i, members in enumerate(detect_communities(g, min_occ=3)):
        for m in members:
            comm_of[m] = i
    lib = _library()

    nodes: dict[str, dict[str, Any]] = {}
    for n, d in g.nodes(data=True):
        key = canonicalize(_normalize_name(n))
        le = lib.get(key, {})
        c = cents.get(n, {})
        occ = int(d.get("occ", 0))
        if key in nodes:
            # synonym fold (e.g. "Ankle Pick Takedown" -> "ankle pick"): sum occurrence
            # counts, keep the stronger centrality reading. ponytail: reward_risk/community
            # keep first-seen rather than a proper weighted merge — good enough at current
            # synonym-list size (6 pairs); revisit if reward_risk needs to fold too.
            existing = nodes[key]
            existing["occ"] += occ
            existing["pagerank"] = max(existing["pagerank"], c.get("pagerank", 0.0))
            existing["betweenness"] = max(existing["betweenness"], c.get("betweenness", 0.0))
            continue
        nodes[key] = {
            "node_key": key,
            "label": canonical_label(key, le.get("label") or n),
            "type": d.get("type") or le.get("type", ""),
            "pt": le.get("pt", ""),
            "occ": occ,
            "pagerank": c.get("pagerank", 0.0),
            "betweenness": c.get("betweenness", 0.0),
            "reward_risk": d.get("reward_risk", 0.0),
            "community": comm_of.get(n),
            "observed": True,
            "neighbours": [],
        }
    # library positions never seen in a match still belong on the map (vector-reachable only)
    for key, le in lib.items():
        if key not in nodes:
            nodes[key] = {
                "node_key": key, "label": canonical_label(key, le["label"]),
                "type": le.get("type", ""),
                "pt": le.get("pt", ""), "occ": 0, "pagerank": 0.0, "betweenness": 0.0,
                "reward_risk": 0.0, "community": None, "observed": False, "neighbours": [],
            }

    edges: list[dict[str, Any]] = []
    edge_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for u, v, d in g.edges(data=True):
        rev = int(g[v][u]["weight"]) if g.has_edge(v, u) else 0
        src, tgt = canonicalize(_normalize_name(u)), canonicalize(_normalize_name(v))
        if src == tgt:
            continue  # synonym collapse turned this into a self-loop, not a real transition
        key = (src, tgt)
        if key in edge_by_key:
            e = edge_by_key[key]
            e["count"] += int(d["weight"])
            e["ok"] += int(d.get("ok", 0))
            e["rev"] += rev
            continue
        e = {
            "source": src, "target": tgt,
            "count": int(d["weight"]), "ok": int(d.get("ok", 0)), "rev": rev,
            "suggested": False,
        }
        edge_by_key[key] = e
        edges.append(e)

    return {"nodes": nodes, "edges": edges, "_graph": g}


def _tokens(label: str) -> set[str]:
    return set(re.findall(r"[a-z]+", label.lower()))


def _synonymish(label_a: str, label_b: str) -> bool:
    """True when one label's word-set is a subset of the other — a near-duplicate, not a
    transition. Catches "Armbar" ⊆ "Armbar Attempt", "Triangle Choke" ⊆ "Arm Triangle Choke".
    "Back Control" vs "Side Control" (neither subset) stays a real related-position pair."""
    ta, tb = _tokens(label_a), _tokens(label_b)
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def attach_neighbors(
    gmap: dict[str, Any],
    semantic: NeighbourFn | None = None,
    structural: NeighbourFn | None = None,
    k: int = 6,
    suggest_threshold: float = 0.6,
    synonym_threshold: float = 0.8,
) -> dict[str, Any]:
    """Fill each node's ``neighbours`` (hybrid semantic+structural), append *suggested* edges, and
    collect ``synonym_candidates``.

    Pure over the provided neighbour callbacks (either may be None). A neighbour pair becomes a
    *suggested* edge when it scores ≥ ``suggest_threshold``, isn't already an observed edge, and
    the two labels are not near-duplicates. Near-duplicate pairs (one label's words ⊆ the other's)
    scoring ≥ ``synonym_threshold`` are recorded in ``gmap["synonym_candidates"]`` for merge review
    instead of being emitted as bogus transitions.
    """
    nodes = gmap["nodes"]
    observed = {(e["source"], e["target"]) for e in gmap["edges"]}
    suggested: set[tuple[str, str]] = set()
    synonyms: dict[frozenset[str], float] = {}

    for key in nodes:
        blended: dict[str, float] = {}
        for fn in (semantic, structural):
            if fn is None:
                continue
            for nb, score in fn(key, k):
                if nb in nodes and nb != key:
                    blended[nb] = max(blended.get(nb, 0.0), round(float(score), 3))
        ranked = sorted(blended.items(), key=lambda kv: kv[1], reverse=True)[:k]
        nodes[key]["neighbours"] = [{"node_key": nb, "score": s} for nb, s in ranked]
        for nb, s in ranked:
            if _synonymish(nodes[key]["label"], nodes[nb]["label"]):
                if s >= synonym_threshold:
                    pair_set = frozenset((key, nb))
                    synonyms[pair_set] = max(synonyms.get(pair_set, 0.0), s)
                continue  # near-duplicate → never a transition
            pair = (key, nb)
            if s >= suggest_threshold and pair not in observed and (nb, key) not in observed:
                suggested.add(pair)

    for src, tgt in sorted(suggested):
        gmap["edges"].append({"source": src, "target": tgt, "count": 0, "suggested": True})
    gmap["synonym_candidates"] = [
        {"a": a, "b": b, "score": sc}
        for (a, b), sc in sorted(
            ((tuple(sorted(p)), sc) for p, sc in synonyms.items()),
            key=lambda kv: kv[1], reverse=True,
        )
    ]
    return gmap
