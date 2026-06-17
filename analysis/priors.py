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

from analysis.athlete_graph import AthleteGraph, out_distribution
from analysis.names import _normalize_name
from analysis.rerank import aggregate_class_probs, blend
from cv.vocab_map import NodeRef

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.7
#: Advisory floor added to the prior over candidate keys (keeps it from vetoing).
PRIOR_SMOOTHING = 0.01

Ranked = list[tuple[str, float]]


def next_move_prior(graph: AthleteGraph, prev_label: str) -> dict[str, float]:
    """Prior over next node labels given the previous move, for one athlete.

    Wraps :func:`out_distribution` and re-keys it from normalized labels to the
    graph's canonical display labels, so the keys align with the aggregated CV
    probabilities (which use app node names).

    Parameters
    ----------
    graph : AthleteGraph
    prev_label : str
        The previous move's label (display or normalized — normalized here).

    Returns
    -------
    dict[str, float]
        ``{display_label: probability}``; ``{}`` if the previous move has no
        recorded transitions. (10b will blend in similar athletes.)
    """
    dist = out_distribution(graph, _normalize_name(prev_label))
    prior: dict[str, float] = {}
    for norm_target, prob in dist.items():
        node = graph.nodes.get(norm_target)
        label = node.label if node is not None else norm_target
        prior[label] = prior.get(label, 0.0) + prob
    return prior


def rerank_classification(
    class_probs: dict[str, float],
    prev_label: str,
    graph: AthleteGraph,
    index: dict[str, NodeRef],
    alpha: float = DEFAULT_ALPHA,
    smoothing: float = PRIOR_SMOOTHING,
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
    prior = next_move_prior(graph, prev_label)
    if prior:
        # Floor over the candidate (CV) keys so the prior biases without vetoing.
        prior = {key: prior.get(key, 0.0) + smoothing for key in agg}
    blended = blend(agg, prior, alpha)
    ranked: Ranked = sorted(blended.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return "", 0.0, []
    top, score = ranked[0]
    return top, score, ranked


def suggest_next(graph: AthleteGraph, prev_label: str, k: int = 5) -> Ranked:
    """Top-``k`` likely next moves for the annotation picker (prior only)."""
    prior = next_move_prior(graph, prev_label)
    return sorted(prior.items(), key=lambda kv: kv[1], reverse=True)[:k]
