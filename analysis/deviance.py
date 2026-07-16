"""Proportional per-node deviance — how *relatively* good an athlete is at each position.

Raw node ELO is misleading for archetype identification: some positions (e.g. back control /
back take) carry high ELO for *everyone* because of high conversion rates, so absolute ELO
over-credits common high-value nodes. Instead we score each node by its **z-score vs the
population** for that node: `z = (athlete_node_elo - pop_mean) / pop_std`. A grappler who is
*specially* good at back takes must beat the (already high) population mean for back takes.

Used by the archetype feature vector (relative-strength-by-type) and by signature detection
(population-relative signatures, z ≥ +1σ). Pure functions over `DerivedNode`-like nodes
(``node_key``, ``node_type``, ``computed_elo``) so they unit-test without a DB.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from typing import Protocol

# Ordered node-type buckets — must match analysis.archetype._TYPES.
TYPES = ["guard", "pass", "sweep", "submission", "takedown", "control", "escape", "transition"]
_STRIKING_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?<![a-z0-9])upper[^a-z0-9]*cut(?![a-z0-9])",
        r"(?<![a-z0-9])jab(?![a-z0-9])",
        r"(?<![a-z0-9])knock[^a-z0-9]*down(?![a-z0-9])",
        r"(?<![a-z0-9])head[^a-z0-9]*kick(?![a-z0-9])",
        r"(?<![a-z0-9])body[^a-z0-9]*kick(?![a-z0-9])",
        r"(?<![a-z0-9])leg[^a-z0-9]*kick(?![a-z0-9])",
        r"(?<![a-z0-9])round[^a-z0-9]*house(?![a-z0-9])",
        r"(?<![a-z0-9])spinning[^a-z0-9]*backfist(?![a-z0-9])",
        r"(?<![a-z0-9])ground[^a-z0-9]*(?:and[^a-z0-9]*)?pound(?![a-z0-9])",
    )
)

MIN_POP = 3  # below this many observations a node_key z is unstable → fall back to its type
_CLAMP = 3.0  # clamp |z| so one outlier node can't dominate the feature vector
SIGNATURE_Z = 1.0  # population-relative signature threshold (+1σ), mirrors the app's +1σ


class _NodeLike(Protocol):
    node_key: str
    node_type: str
    computed_elo: float | None


Stats = dict[str, tuple[float, float, int]]  # key → (mean, std, n)


def _bucket(node_type: str | None) -> str:
    t = (node_type or "").lower().strip()
    return t if t in TYPES else "transition"


def is_grappling_node(node: _NodeLike) -> bool:
    """Return whether a node belongs in grappling-only deviance calculations.

    ``node_type='strike'`` is authoritative. Older nodes without that type fall back
    to the known striking-label patterns. Unknown historical types remain eligible and
    fall back to ``transition`` via :func:`_bucket`.
    """
    if (node.node_type or "").casefold().strip() == "strike":
        return False
    return not any(pattern.search(node.node_key.casefold()) for pattern in _STRIKING_PATTERNS)


def grappling_nodes(nodes: Sequence[_NodeLike]) -> list[_NodeLike]:
    """Return the grappling subset once for a graph-level calculation."""
    return [node for node in nodes if is_grappling_node(node)]


def _stats(values: list[float]) -> tuple[float, float, int]:
    n = len(values)
    if n == 0:
        return (0.0, 0.0, 0)
    mean = statistics.fmean(values)
    std = statistics.pstdev(values) if n > 1 else 0.0
    return (mean, std, n)


def node_population_stats(
    graphs: Sequence[tuple[str, Sequence[_NodeLike]]],
) -> tuple[Stats, Stats]:
    """Population mean/std/n per ``node_key`` and per node ``type``, over many graphs.

    ``graphs`` = ``[(graph_id, [node, ...]), ...]`` (e.g. ``graphs_for_clustering`` output).
    Returns ``(by_node_key, by_type)``. Only nodes with a non-null ``computed_elo`` count.
    """
    by_key: dict[str, list[float]] = {}
    by_type: dict[str, list[float]] = {}
    for _gid, nodes in graphs:
        for nd in grappling_nodes(nodes):
            if nd.computed_elo is None:
                continue
            by_key.setdefault(nd.node_key, []).append(float(nd.computed_elo))
            by_type.setdefault(_bucket(nd.node_type), []).append(float(nd.computed_elo))
    return (
        {k: _stats(v) for k, v in by_key.items()},
        {t: _stats(v) for t, v in by_type.items()},
    )


def node_deviance(
    node: _NodeLike, by_key: Stats, by_type: Stats
) -> float:
    """Proportional deviance (clamped z-score) of one node vs the population.

    Uses the per-``node_key`` baseline when it has ≥ ``MIN_POP`` observations and non-zero
    spread; otherwise falls back to the node's *type* baseline (so a rarely-seen position is
    judged against its family, not itself). Returns 0.0 when neither baseline is usable.
    """
    if node.computed_elo is None:
        return 0.0
    elo = float(node.computed_elo)
    mean, std, n = by_key.get(node.node_key, (0.0, 0.0, 0))
    if n < MIN_POP or std <= 0.0:
        mean, std, n = by_type.get(_bucket(node.node_type), (0.0, 0.0, 0))
    if std <= 0.0:
        return 0.0
    z = (elo - mean) / std
    return max(-_CLAMP, min(_CLAMP, z))


def type_deviance_vector(
    nodes: Sequence[_NodeLike], by_key: Stats, by_type: Stats
) -> list[float]:
    """Per-type relative-strength vector: mean node deviance within each ``TYPES`` bucket.

    Captures *what an athlete is relatively elite at* (back/submission/guard…) independent of
    how universally high-ELO those positions are. Empty buckets score 0.
    """
    sums: dict[str, float] = {t: 0.0 for t in TYPES}
    counts: dict[str, int] = {t: 0 for t in TYPES}
    for nd in grappling_nodes(nodes):
        z = node_deviance(nd, by_key, by_type)
        b = _bucket(nd.node_type)
        sums[b] += z
        counts[b] += 1
    return [sums[t] / counts[t] if counts[t] else 0.0 for t in TYPES]


def signature_nodes(
    nodes: Sequence[_NodeLike], by_key: Stats, by_type: Stats, threshold: float = SIGNATURE_Z
) -> list[tuple[str, float]]:
    """Population-relative signatures: nodes whose deviance ≥ ``threshold`` (default +1σ).

    Returns ``[(node_key, z), ...]`` sorted by z descending — the athlete's genuinely
    above-population positions (the back-take de-bias: only above-average back-takers qualify).
    """
    out = [
        (nd.node_key, node_deviance(nd, by_key, by_type))
        for nd in grappling_nodes(nodes)
    ]
    out = [(k, z) for k, z in out if z >= threshold]
    return sorted(out, key=lambda kv: kv[1], reverse=True)
