"""Pure distribution math for CV re-ranking — no I/O, no graph/Qdrant deps."""

from __future__ import annotations

import logging

from cv.vocab_map import NodeRef, map_vicos_class

logger = logging.getLogger(__name__)


def aggregate_class_probs(
    class_probs: dict[str, float],
    index: dict[str, NodeRef],
) -> dict[str, float]:
    """Map ViCoS class probabilities to app node-label probabilities.

    For each class that successfully resolves via :func:`map_vicos_class`, the
    probability is accumulated under the app node's canonical name.  Unmapped
    classes are kept under their raw position string so nothing is lost.

    Parameters
    ----------
    class_probs : dict[str, float]
        ViCoS class -> probability.
    index : dict[str, NodeRef]
        Normalised-alias -> NodeRef lookup from :func:`cv.vocab_map.build_vocab_index`.

    Returns
    -------
    dict[str, float]
        Node-label -> summed probability (not renormalised).
    """
    result: dict[str, float] = {}
    for vicos_class, prob in class_probs.items():
        match = map_vicos_class(vicos_class, index)
        if match.ok:
            assert match.node_name is not None
            key = match.node_name
        else:
            key = match.position
        result[key] = result.get(key, 0.0) + prob
    return result


def normalize(d: dict[str, float]) -> dict[str, float]:
    """L1-normalise a non-negative probability dict.

    Parameters
    ----------
    d : dict[str, float]

    Returns
    -------
    dict[str, float]
        Normalised copy.  Empty or all-zero input returns ``{}``.
    """
    total = sum(d.values())
    if total <= 0.0:
        return {}
    return {k: v / total for k, v in d.items()}


def blend(
    agg: dict[str, float],
    prior: dict[str, float],
    alpha: float = 0.7,
) -> dict[str, float]:
    """Geometric-mean blend of aggregated CV probabilities and a prior.

    ``p'(k) ∝ agg(k)^α · prior(k)^(1-α)`` over the union of keys, then
    L1-normalised.

    Parameters
    ----------
    agg : dict[str, float]
        Aggregated class probabilities from :func:`aggregate_class_probs`.
    prior : dict[str, float]
        Prior distribution (e.g. from ``out_distribution``).
    alpha : float
        Blend weight for the CV side.  Clamped to ``[0, 1]``.  Default 0.7.

    Returns
    -------
    dict[str, float]
        Normalised blended distribution.  Empty *prior* returns ``normalize(agg)``.
    """
    alpha = max(0.0, min(1.0, alpha))

    if not prior:
        return normalize(agg)

    if alpha == 1.0:
        return normalize(agg)

    if alpha == 0.0:
        return normalize(prior)

    keys = set(agg) | set(prior)
    blended: dict[str, float] = {}
    for k in keys:
        a = agg.get(k, 0.0)
        p = prior.get(k, 0.0)
        blended[k] = (a ** alpha) * (p ** (1.0 - alpha))
    return normalize(blended)
