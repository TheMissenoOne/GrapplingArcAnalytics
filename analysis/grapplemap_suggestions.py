"""GrappleMap-driven technique suggestions.

Given user's current node keys, finds unexplored adjacent positions
in the GrappleMap technique graph to surface as "explore" hints.
"""

from __future__ import annotations

from analysis.names import _normalize_name
from grapplemap.align import build_lookup, grapplemap_neighbors
from grapplemap.parser import GMapGraph


def get_grapplemap_suggestions(
    user_node_keys: list[str],
    gmap: GMapGraph,
    max_suggestions: int = 5,
    fuzzy_threshold: float = 0.75,
) -> list[dict]:  # type: ignore[type-arg]
    """Return GrappleMap-adjacent positions not in user's graph.

    Each result: {"label": str, "from": str, "type": "explore"}
    Sorted by frequency (positions reachable from multiple user nodes first).
    """
    lookup = build_lookup(gmap)
    user_keys_norm = {_normalize_name(k) for k in user_node_keys}

    candidate_count: dict[str, int] = {}
    candidate_from: dict[str, str] = {}

    for user_key in user_node_keys:
        neighbors = grapplemap_neighbors(user_key, gmap, lookup, fuzzy_threshold)
        for nb in neighbors:
            if nb not in user_keys_norm:
                candidate_count[nb] = candidate_count.get(nb, 0) + 1
                if nb not in candidate_from:
                    candidate_from[nb] = _normalize_name(user_key)

    ranked = sorted(candidate_count, key=lambda k: -candidate_count[k])
    return [
        {"label": k, "from": candidate_from[k], "type": "explore"}
        for k in ranked[:max_suggestions]
    ]
