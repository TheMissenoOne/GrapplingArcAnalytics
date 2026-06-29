"""Decision Space — strategic control as the progressive reduction of an opponent's options.

Implements the model layer of the Decision Space spec (DS-01..05, DS-16) in **expert mode**:
manually-calibrated default scores per position/event type. ``learned`` mode (data-driven,
paid) replaces these defaults later without changing the domain shape (DS-16).

A score is the *normalized decision space available to a side* (0 = no meaningful options,
1 = full freedom). Control/submission/pass shrink the defender's space; guard/escape expand
it. Per-transition this yields the reduction/recovery deltas (DS-05) and a match timeline
(DS-12: major reductions, recoveries, turning points).

This module derives DS from event ``type`` so it works before any position has a curated
``technique_nodes.decision_space``; a curated per-position DS (DS-01/04) overrides the
default when present.
"""

from __future__ import annotations

from typing import Any

# Expert-mode defaults: {event type → (attacker_ds, defender_ds)}.
# attacker_ds = options for the acting side; defender_ds = options left to the opponent.
# Deeper control → lower defender_ds (the point of the model). On a 0..1 scale.
_DEFAULT_DS: dict[str, tuple[float, float]] = {
    "submission": (0.85, 0.10),
    "control": (0.80, 0.25),
    "pass": (0.75, 0.30),
    "takedown": (0.70, 0.35),
    "sweep": (0.65, 0.40),
    "transition": (0.60, 0.45),
    "guard": (0.50, 0.60),
    "escape": (0.45, 0.70),
}
_NEUTRAL = (0.50, 0.50)

# A side's score change beyond this (absolute) marks a "major" reduction/recovery (DS-12).
_MAJOR_DELTA = 0.12


def position_decision_space(
    event_type: str, curated: dict[str, Any] | None = None
) -> dict[str, float]:
    """Attacker/defender DS scores for an event (DS-04).

    ``curated`` is the position's ``technique_nodes.decision_space`` if authored — its
    ``attacker_score``/``defender_score`` win over the expert default (DS-01/16).
    """
    if curated and "attacker_score" in curated and "defender_score" in curated:
        return {
            "attacker_score": float(curated["attacker_score"]),
            "defender_score": float(curated["defender_score"]),
        }
    atk, dfd = _DEFAULT_DS.get((event_type or "").lower(), _NEUTRAL)
    return {"attacker_score": atk, "defender_score": dfd}


def sequence_decision_space(
    sequence: list[dict[str, Any]],
    curated_by_key: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Decision-Space timeline for a match/training sequence (DS-05, DS-12).

    Each sequence event is ``{label, type, side ('a'|'b'), ...}``. We track the defender's
    available decision space (the side NOT acting) across the sequence; each step records
    the before/after for both sides and the reduction (DS-05). Returns the timeline plus
    the major reductions, recoveries and turning points (DS-12).
    """
    curated_by_key = curated_by_key or {}
    timeline: list[dict[str, Any]] = []
    reductions: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    turning_points: list[dict[str, Any]] = []

    # Running per-side decision space; starts neutral for both.
    space = {"a": 0.5, "b": 0.5}
    prev_dominance: float | None = None  # space[a] - space[b] after the previous step

    for i, ev in enumerate(sequence):
        side = ev.get("side")
        if side not in ("a", "b"):
            continue
        opp = "b" if side == "a" else "a"
        key = _normalized_key(ev.get("label", ""))
        ds = position_decision_space(str(ev.get("type", "")), curated_by_key.get(key))

        before = {"a": space["a"], "b": space["b"]}
        # The acting side's options become the attacker score; the opponent's the defender.
        space[side] = ds["attacker_score"]
        space[opp] = ds["defender_score"]
        after = {"a": space["a"], "b": space["b"]}

        atk_var = round(after[side] - before[side], 4)
        def_var = round(after[opp] - before[opp], 4)
        # Total reduction = how much the opponent's space shrank (positive = compression).
        total_reduction = round(before[opp] - after[opp], 4)

        entry = {
            "index": i,
            "label": ev.get("label"),
            "type": ev.get("type"),
            "actor": side,
            "ds_before": before,
            "ds_after": after,
            "attacker_variation": atk_var,
            "defender_variation": def_var,
            "total_reduction": total_reduction,
            "reduction_pct": round(max(0.0, total_reduction), 4),
            "expansion_pct": round(max(0.0, -total_reduction), 4),
        }
        timeline.append(entry)

        # Reduction (DS-05/12): the acting side shrank the opponent's space sharply.
        if def_var <= -_MAJOR_DELTA:
            reductions.append(entry)
        # Recovery (DS-09): the actor expanded its OWN space while in an inferior spot.
        if atk_var >= _MAJOR_DELTA and before[side] < 0.5:
            recoveries.append(entry)
        # Turning point (DS-12): the lead (who has more decision space) flips.
        dominance = after["a"] - after["b"]
        flipped = prev_dominance is not None and _sign(dominance) != _sign(prev_dominance)
        if flipped and dominance != 0:
            turning_points.append(entry)
        prev_dominance = dominance

    return {
        "mode": "expert",
        "timeline": timeline,
        "reductions": reductions,
        "recoveries": recoveries,
        "turning_points": turning_points,
    }


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _normalized_key(label: str) -> str:
    # Local import to avoid a hard dependency when names isn't needed.
    from analysis.names import _normalize_name

    return _normalize_name(label)
