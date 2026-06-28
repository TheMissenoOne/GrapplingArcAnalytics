"""Fighter DNA — "fighters most like X" by cosine similarity over style vectors.

A lightweight, deterministic exploration layer: each athlete is reduced to a normalised
own-move technique-type distribution (the same 8 buckets the archetype model uses), and we
rank nearest neighbours by cosine. Reuses ``analysis.archetype._TYPES`` so the space agrees
with the clustering. (node2vec / the pgvector–Qdrant graph embeddings are a richer future
upgrade — see docs/graph_analysis_approaches.md — but this needs no extra deps.)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from analysis.archetype import _TYPES

MIN_OWN_EVENTS = 15  # ignore athletes with too little signal


def athlete_style_vectors(session: Any) -> tuple[list[tuple[str, str]], np.ndarray]:
    """Returns ``([(athlete_id, name)], matrix)`` of L2-normalised type-share vectors.

    Each row is one athlete's own-move distribution over ``_TYPES`` across their final matches.
    """
    from export.match_breakdown import _final_matches

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for m in _final_matches(session):
        for e in m.sequence or []:
            aid = e.get("actor_id")
            t = str(e.get("type", ""))
            if aid and t in _TYPES:
                counts[str(aid)][t] += 1

    from db.models import Athlete

    ids: list[tuple[str, str]] = []
    rows: list[np.ndarray] = []
    for aid, c in counts.items():
        total = sum(c.values())
        if total < MIN_OWN_EVENTS:
            continue
        vec = np.array([c[t] / total for t in _TYPES], dtype=np.float64)
        norm = np.linalg.norm(vec)
        if norm == 0:
            continue
        athlete = session.get(Athlete, aid)
        ids.append((aid, athlete.name if athlete else aid))
        rows.append(vec / norm)
    return ids, (np.vstack(rows) if rows else np.empty((0, len(_TYPES))))


def nearest_in(
    ids: list[tuple[str, str]], mat: np.ndarray, name: str, k: int = 5
) -> list[tuple[str, float]]:
    """Top-``k`` closest athletes to ``name`` over a precomputed (ids, matrix) — pure."""
    if not ids:
        return []
    idx = next((i for i, (_, nm) in enumerate(ids) if nm.lower() == name.lower()), None)
    if idx is None:
        return []
    sims = mat @ mat[idx]  # rows are unit vectors → dot == cosine
    out: list[tuple[str, float]] = []
    for j in np.argsort(-sims):
        if j == idx:
            continue
        out.append((ids[j][1], round(float(sims[j]), 3)))
        if len(out) >= k:
            break
    return out


def nearest_fighters(session: Any, name: str, k: int = 5) -> list[tuple[str, float]]:
    """Top-``k`` stylistically closest athletes to ``name`` (by cosine), best-first."""
    ids, mat = athlete_style_vectors(session)
    return nearest_in(ids, mat, name, k)
