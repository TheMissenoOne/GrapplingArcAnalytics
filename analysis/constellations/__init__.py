"""Constellation detection — which nodes of a transition graph move together.

**Rating never enters this layer.** Membership comes from topology and frequency
only — edge weight on a ``nx.DiGraph`` built by ``analysis/transitions/``. If any
function in ``constellations/`` accepts a rating, ELO, Glicko value, or anything
imported from ``analysis/rating_v2/``, that is a defect: skill and community
structure are two separate layers of the product (see
``docs/rating_v2/README.md`` and ADR-08 in ``docs/rating_v2/01_DECISOES.md``).
Constellation of an athlete's game and constellation of a weight-category feed
BOTH the Rating Engine V2 report and the category trend report from this same
detector — nobody reimplements it.
"""

from analysis.constellations.detect import Constellation, DetectionResult, detect
from analysis.constellations.stability import (
    ConstellationStability,
    Stability,
    bootstrap_jaccard,
    classify_stability,
    leave_one_out_athlete_driven,
    support,
)

__all__ = [
    "Constellation",
    "DetectionResult",
    "detect",
    "ConstellationStability",
    "Stability",
    "bootstrap_jaccard",
    "classify_stability",
    "leave_one_out_athlete_driven",
    "support",
]
