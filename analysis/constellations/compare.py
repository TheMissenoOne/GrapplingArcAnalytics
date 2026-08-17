"""Compare two partitions over the same node set — Jaccard + coverage.

The instrument for wave 6 (ADR-08, ``docs/rating_v2/01_DECISOES.md``): comparing the
shared ``constellations.detect`` output against ``analysis/athlete_systems.py``. This
module only builds the comparison tool — running that comparison is wave 6's job, not
this one's.
"""

from __future__ import annotations

from dataclasses import dataclass


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity. Two empty sets are treated as identical (1.0)."""
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


@dataclass
class PartitionComparison:
    best_match_jaccard: dict[frozenset[str], float]  # each community in `a` → best Jaccard vs `b`
    mean_jaccard: float
    only_in_a: set[str]     # nodes `a` groups that `b` doesn't group at all
    only_in_b: set[str]
    common_nodes: set[str]


def compare_partitions(a: list[list[str]], b: list[list[str]]) -> PartitionComparison:
    """Compare partition ``a`` against partition ``b`` (lists of member lists).

    For each community in ``a``, records its best Jaccard match among ``b``'s
    communities. Coverage tracks nodes one side groups into some community that the
    other side leaves out of every community.
    """
    a_sets = [frozenset(c) for c in a]
    b_sets = [frozenset(c) for c in b]
    nodes_a: set[str] = set().union(*a_sets) if a_sets else set()
    nodes_b: set[str] = set().union(*b_sets) if b_sets else set()

    best = {ca: max((jaccard(set(ca), set(cb)) for cb in b_sets), default=0.0) for ca in a_sets}
    mean_j = sum(best.values()) / len(best) if best else 1.0

    return PartitionComparison(
        best_match_jaccard=best,
        mean_jaccard=round(mean_j, 5),
        only_in_a=nodes_a - nodes_b,
        only_in_b=nodes_b - nodes_a,
        common_nodes=nodes_a & nodes_b,
    )
