"""Align GrappleMap position names → analytics node_key system.

Uses _normalize_name from analysis/names.py as the canonical key function.
Provides exact + fuzzy matching for cross-referencing user graph nodes
with GrappleMap positions.
"""

from __future__ import annotations

import difflib

from analysis.names import _normalize_name
from grapplemap.parser import GMapGraph, GMapPosition


def build_lookup(gmap: GMapGraph) -> dict[str, str]:
    """Return {normalized_position_name → raw position name}."""
    return {_normalize_name(pos.name): pos_key for pos_key, pos in gmap.positions.items()}


def find_position(
    node_key: str,
    gmap: GMapGraph,
    lookup: dict[str, str] | None = None,
    fuzzy_threshold: float = 0.75,
) -> GMapPosition | None:
    """Find GrappleMap position matching a node_key.

    Tries exact match first, then fuzzy (SequenceMatcher ratio).
    """
    if lookup is None:
        lookup = build_lookup(gmap)

    norm_key = _normalize_name(node_key)

    # Exact match
    if norm_key in lookup:
        raw_key = lookup[norm_key]
        return gmap.positions.get(raw_key)

    # Fuzzy match
    candidates = list(lookup.keys())
    matches = difflib.get_close_matches(norm_key, candidates, n=1, cutoff=fuzzy_threshold)
    if matches:
        raw_key = lookup[matches[0]]
        return gmap.positions.get(raw_key)

    # Tag-based fallback: if any tag in gmap positions contains node_key words
    words = set(norm_key.split())
    for pos_key, pos in gmap.positions.items():
        tag_words = {t.lower().replace("_", " ") for t in pos.tags}
        if words & tag_words:
            return pos

    return None


def grapplemap_neighbors(
    node_key: str,
    gmap: GMapGraph,
    lookup: dict[str, str] | None = None,
    fuzzy_threshold: float = 0.75,
) -> list[str]:
    """Return normalized names of adjacent GrappleMap positions for a node_key.

    Searches both outgoing (successors) and incoming (predecessors) neighbors.
    Returns empty list if no match found.
    """
    if lookup is None:
        lookup = build_lookup(gmap)

    pos = find_position(node_key, gmap, lookup, fuzzy_threshold)
    if pos is None:
        return []

    # Find the graph key for this position
    graph_key = pos.name.lower().strip()
    if graph_key not in gmap.graph:
        # Try _normalize_name variant
        norm = _normalize_name(pos.name)
        for key in gmap.graph.nodes:
            if _normalize_name(key) == norm:
                graph_key = key
                break
        else:
            return []

    neighbors = set(gmap.graph.successors(graph_key)) | set(gmap.graph.predecessors(graph_key))
    return [_normalize_name(n) for n in neighbors]


def tag_similarity(pos: GMapPosition, node_key: str) -> float:
    """Score 0..1 — how many tag words overlap with node_key words."""
    words = set(_normalize_name(node_key).split())
    tag_words: set[str] = set()
    for tag in pos.tags:
        tag_words.update(tag.lower().replace("_", " ").split())
    if not words or not tag_words:
        return 0.0
    return len(words & tag_words) / len(words | tag_words)
