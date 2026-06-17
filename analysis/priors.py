"""Athlete priors → re-rank CV classifications + rank annotation suggestions.

The classifier predicts the *current* position (ViCoS class); an athlete prior
predicts the *next* move given the *previous* position. This module bridges the
two in a shared **app-node-label space**:

1. aggregate the classifier's ViCoS-class probabilities into node-label probs
   (``analysis.rerank.aggregate_class_probs``);
2. build the prior over node labels from the previous node's transitions
   (``analysis.athlete_graph.out_distribution``);
3. geometric-mean blend (``analysis.rerank.blend``).

The prior is **advisory**: a small smoothing floor is applied over the candidate
keys so a recorded prior can bias — but never hard-veto — a class the CV detects
(without it, the geometric blend would zero any CV class the athlete has never
transitioned to). Cold start (no prior) leaves the CV output untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from analysis.athlete_graph import AthleteGraph, out_distribution
from analysis.graph_embed import graph_vector
from analysis.names import _normalize_name
from analysis.rerank import aggregate_class_probs, blend
from cv.vocab_map import NodeRef

if TYPE_CHECKING:
    from analysis.vector_store import AthleteVectorStore

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.7
#: Advisory floor added to the prior over candidate keys (keeps it from vetoing).
PRIOR_SMOOTHING = 0.01
#: Default weight on the athlete's own history vs. similar-athlete blend.
DEFAULT_SELF_WEIGHT = 0.6

Ranked = list[tuple[str, float]]


def label_map_from_index(index: dict[str, NodeRef]) -> dict[str, str]:
    """Build a normalized-label → canonical-display-name map from a vocab index."""
    return {_normalize_name(ref.name): ref.name for ref in index.values()}


def _to_display(
    graph: AthleteGraph,
    dist_norm: dict[str, float],
    label_map: dict[str, str] | None = None,
) -> dict[str, float]:
    """Re-key a normalized-label distribution to canonical display labels.

    Resolves each normalized label via ``label_map`` (app vocab) first, then the
    athlete's own graph, falling back to the normalized form — so labels introduced
    by the similar-athlete blend (absent from this athlete's graph) still display.
    """
    display: dict[str, float] = {}
    for norm_target, prob in dist_norm.items():
        if label_map and norm_target in label_map:
            label = label_map[norm_target]
        elif (node := graph.nodes.get(norm_target)) is not None:
            label = node.label
        else:
            label = norm_target
        display[label] = display.get(label, 0.0) + prob
    return display


def _blend_neighbors(
    own: dict[str, float],
    neighbors: list[tuple[str, float, dict[str, float]]],
    self_weight: float,
) -> dict[str, float]:
    """Weighted blend of own + similar athletes' distributions (normalized space)."""
    combined: dict[str, float] = {k: self_weight * v for k, v in own.items()}
    total_sim = sum(sim for _, sim, _ in neighbors)
    if neighbors and total_sim > 0:
        for _athlete, sim, dist in neighbors:
            w = (1.0 - self_weight) * (sim / total_sim)
            for label, prob in dist.items():
                combined[label] = combined.get(label, 0.0) + w * prob
    total = sum(combined.values())
    return {k: v / total for k, v in combined.items()} if total > 0 else {}


def next_move_prior(
    graph: AthleteGraph,
    prev_label: str,
    *,
    store: AthleteVectorStore | None = None,
    athlete: str | None = None,
    k: int = 5,
    self_weight: float = DEFAULT_SELF_WEIGHT,
    label_map: dict[str, str] | None = None,
) -> dict[str, float]:
    """Prior over next node labels given the previous move, for one athlete.

    Starts from the athlete's own ``out_distribution``. When a ``store`` and
    ``athlete`` are given, blends in similar athletes' transitions *from the same
    position* (Qdrant cosine-weighted), so a sparse/new athlete still gets a useful
    prior. Re-keyed to display labels (``label_map`` resolves blend-introduced labels)
    so it aligns with aggregated CV probabilities.

    Returns ``{display_label: probability}``; ``{}`` if there's no signal.
    """
    prev_norm = _normalize_name(prev_label)
    own = out_distribution(graph, prev_norm)

    if store is not None and athlete is not None:
        style_vec = graph_vector(graph, store.vocab)
        if style_vec.any():
            # Stylistically similar athletes (whole-graph) → their habits *from* this
            # position. Lets a sparse athlete inherit likely transitions they haven't
            # demonstrated yet.
            neighbors: list[tuple[str, float, dict[str, float]]] = []
            for other, sim in store.similar_athletes(style_vec, k=k, exclude=athlete):
                dist = store.position_distribution(other, prev_norm)
                if dist:
                    neighbors.append((other, sim, dist))
            if neighbors:
                blended = _blend_neighbors(own, neighbors, self_weight)
                return _to_display(graph, blended, label_map)

    return _to_display(graph, own, label_map)


def rerank_classification(
    class_probs: dict[str, float],
    prev_label: str,
    graph: AthleteGraph,
    index: dict[str, NodeRef],
    alpha: float = DEFAULT_ALPHA,
    smoothing: float = PRIOR_SMOOTHING,
    *,
    store: AthleteVectorStore | None = None,
    athlete: str | None = None,
) -> tuple[str, float, Ranked]:
    """Re-rank a classifier's output against the athlete's transition prior.

    Parameters
    ----------
    class_probs : dict[str, float]
        ViCoS class -> probability (from ``cv.inference.classify_pose_pair_probs``).
    prev_label : str
        The previous committed move's label (for the prior).
    graph : AthleteGraph
        The athlete's graph.
    index : dict[str, NodeRef]
        Vocab index from ``cv.vocab_map.build_vocab_index``.
    alpha : float
        CV weight in the blend (``[0,1]``; default 0.7). 1.0 ⇒ ignore the prior.
    smoothing : float
        Advisory floor added to the prior over candidate keys.

    Returns
    -------
    tuple[str, float, list[tuple[str, float]]]
        ``(top_node_label, score, ranked)`` where ``ranked`` is descending by score.
        ``("", 0.0, [])`` if ``class_probs`` is empty.
    """
    agg = aggregate_class_probs(class_probs, index)
    prior = next_move_prior(
        graph, prev_label, store=store, athlete=athlete, label_map=label_map_from_index(index)
    )
    if prior:
        # Floor over the candidate (CV) keys so the prior biases without vetoing.
        prior = {key: prior.get(key, 0.0) + smoothing for key in agg}
    blended = blend(agg, prior, alpha)
    ranked: Ranked = sorted(blended.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return "", 0.0, []
    top, score = ranked[0]
    return top, score, ranked


def suggest_next(
    graph: AthleteGraph,
    prev_label: str,
    k: int = 5,
    *,
    store: AthleteVectorStore | None = None,
    athlete: str | None = None,
    label_map: dict[str, str] | None = None,
) -> Ranked:
    """Top-``k`` likely next moves for the annotation picker (prior only)."""
    prior = next_move_prior(
        graph, prev_label, store=store, athlete=athlete, k=k, label_map=label_map
    )
    return sorted(prior.items(), key=lambda kv: kv[1], reverse=True)[:k]
