"""Rating V2 core data types — ``RatingState``/``Observation``.

Ported from the plan bundle's reference core
(``grapplingarc_glicko2_constellation_engine_plan_bundle/scripts/glicko2_reference_core.py``),
gate-verified against the published Glicko-2 example. See ``docs/rating_v2/``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RatingState:
    """One athlete's Glicko-2 state at a point in time."""

    rating: float
    deviation: float
    volatility: float = 0.06


@dataclass(frozen=True)
class Observation:
    """One scored result against a known opponent state within a rating period.

    ``weight`` supports future sub-node evidence (ADR-03); pure Glicko-2 replay uses 1.0.
    """

    opponent_rating: float
    opponent_deviation: float
    score: float
    weight: float = 1.0
