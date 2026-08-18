"""Sparse-node taxonomy — doc 04's named states for every node, instead of a silent
drop.

Today ``category_constellations.division_constellations`` filters out size-1
communities before the report ever sees them (``members_multi = [c for c in
detection.constellations if len(c.members) > 1]``) — a singleton just vanishes.
Doc 04 asks for explicit states: ``detected`` / ``unassigned`` / ``singleton`` /
``low_support``, with the thresholds that decide ``low_support`` as calibration
output, not a fixed plan constant (same status as ``stability.classify_stability``'s
``stable_threshold``).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from analysis.constellations.detect import DetectionResult


class NodeState(StrEnum):
    DETECTED = "detected"        # in a community that clears every threshold below
    SINGLETON = "singleton"      # its own community, exactly one member
    LOW_SUPPORT = "low_support"  # community size >= 2, but below a threshold
    UNASSIGNED = "unassigned"    # in the caller's node universe, but not in this detection at all


@dataclass
class TaxonomyThresholds:
    """First cut (calibration pending — doc 04: "the thresholds are calibration
    outputs, not hard-coded in this plan"). Defaults documented here, not derived
    from any measured corpus."""

    min_community_size: int = 2
    min_internal_support: float = 0.0  # Constellation.support (sum of internal edge weight)


def classify_nodes(
    result: DetectionResult,
    all_nodes: Collection[str] | None = None,
    thresholds: TaxonomyThresholds | None = None,
) -> dict[str, NodeState]:
    """Every node -> exactly one ``NodeState``. Covers 100% of ``all_nodes`` when
    given (falls back to just the nodes ``result`` already covers, in which case
    ``UNASSIGNED`` never appears — there's nothing outside the detection to be
    unassigned from).
    """
    thresholds = thresholds or TaxonomyThresholds()
    out: dict[str, NodeState] = {}
    for c in result.constellations:
        if len(c.members) == 1:
            state = NodeState.SINGLETON
        elif (
            len(c.members) < thresholds.min_community_size
            or c.support < thresholds.min_internal_support
        ):
            state = NodeState.LOW_SUPPORT
        else:
            state = NodeState.DETECTED
        for m in c.members:
            out[m] = state
    if all_nodes is not None:
        for n in all_nodes:
            out.setdefault(n, NodeState.UNASSIGNED)
    return out
