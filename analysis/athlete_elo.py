"""Rank-aware graph-ELO growth engine for impersonated athlete matches.

Python mirror of the app's ``eloService.ts`` / ``graphDomain.ts``, made
rank-aware.  An impersonated athlete's *graph ELO* (the mean of their move
nodes' ``computed_elo``) starts at a belt-based floor and climbs toward a known
*rank ELO* target.  The gap between graph ELO and target drives the K-factor, so
growth slows and the graph ELO converges near the target.

The rank target is never seeded into the athlete's own graph — it (or a
manually typed value) is only used as the *opponent's* input rating.

This module is pure: no DB and no file IO.  Everything (matches, target,
per-match opponent ratings) is passed in, so it is unit-testable in isolation.
"""

from __future__ import annotations

import math
from typing import Any

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.elo_calibration import _expected
from analysis.names import _normalize_name

# ── Belt-base ladder ────────────────────────────────────────────────────────
# App ``beltSystems.ts`` bases shifted so black belt = 800 (was 1200).  Pro
# athletes are black belts, so 800 is the floor that matters; the lower rungs
# keep the relative spacing for completeness.  Tunable.
BASE_BLACKBELT_ELO: float = 800.0
BELT_BASE_ELO: dict[str, float] = {
    "white": 200.0,
    "blue": 350.0,
    "purple": 500.0,
    "brown": 650.0,
    "black": 800.0,
}

# ── Sequence point-map ──────────────────────────────────────────────────────
# Keyword → IBJJF-ish point value, matched against a move's normalized
# label/type.  Ported from the app ``eloService.ts`` point map.  Tunable.
POINT_MAP: dict[str, int] = {
    "takedown": 2,
    "sweep": 2,
    "pass": 3,
    "mount": 4,
    "back": 4,
    "knee on belly": 2,
    "kob": 2,
}

# Outcome-fallback scores when there is neither a submission nor a scoring
# sequence.
WIN_FALLBACK_SCORE: float = 0.75
LOSS_FALLBACK_SCORE: float = 0.25
# A draw is a neutral result: S = 0.5 moves ELO toward (not away from) the
# opponent's rating, so a draw with a strong opponent still nudges ELO up.
DRAW_SCORE: float = 0.5

# ── K-factor ────────────────────────────────────────────────────────────────
K_BASE_EARLY: float = 40.0  # n_matches <= 10
K_BASE_MID: float = 32.0  # n_matches <= 30
K_BASE_FLOOR: float = 10.0  # log-decay asymptote
GAP_DIVISOR: float = 400.0
GAP_FACTOR_MIN: float = 0.1
GAP_FACTOR_MAX: float = 1.0


def base_elo_for_belt(belt: str | None) -> float:
    """Belt-based starting ELO for a freshly-seen node (defaults to black/800)."""
    if not belt:
        return BASE_BLACKBELT_ELO
    return BELT_BASE_ELO.get(belt.strip().lower(), BASE_BLACKBELT_ELO)


def expected(r_self: float, r_opp: float) -> float:
    """Logistic expected score — reuse the calibrated /400 curve."""
    return _expected(r_self, r_opp)


def _points_for_entry(entry: dict[str, Any]) -> int:
    """Points scored by a single sequence entry via the keyword point-map."""
    label = _normalize_name(str(entry.get("label", "")))
    typ = _normalize_name(str(entry.get("type", "")))
    haystack = f"{label} {typ}"
    for keyword, pts in POINT_MAP.items():
        if keyword in haystack:
            return pts
    return 0


def score_from_match(match: Any) -> float:
    """Per-match score S in [0, 1] driving the ELO update.

    Priority:
      0. Draw — neutral S = 0.5 (no winner; techniques still register).
      1. Submission outcome dominates — SUBMISSION win → 1.0, SUBMISSION loss → 0.0.
      2. Sequence point-map — your_points / (your_points + opp_points) when a
         scoring sequence is present (0.5 if neither side scored).
      3. Outcome fallback — win → 0.75, loss → 0.25.
    """
    won = bool(getattr(match, "won", True))
    win_type = (getattr(match, "win_type", None) or "").upper()
    if win_type == "DRAW":
        return DRAW_SCORE
    if win_type == "SUBMISSION":
        return 1.0 if won else 0.0

    sequence = getattr(match, "sequence", None) or []
    your_pts = 0
    opp_pts = 0
    for entry in sequence:
        if not isinstance(entry, dict):
            continue
        pts = _points_for_entry(entry)
        if entry.get("actor") == "you":
            your_pts += pts
        else:
            opp_pts += pts
    if your_pts or opp_pts:
        return your_pts / (your_pts + opp_pts)

    return WIN_FALLBACK_SCORE if won else LOSS_FALLBACK_SCORE


def _base_k(n_matches: int) -> float:
    """Match-count-driven base K: 40 (≤10), 32 (≤30), then log-decay → 10."""
    if n_matches <= 10:
        return K_BASE_EARLY
    if n_matches <= 30:
        return K_BASE_MID
    # Smooth log decay from 32 toward the 10 floor for n > 30.
    decayed = K_BASE_MID - 8.0 * math.log10(n_matches - 29)
    return max(K_BASE_FLOOR, decayed)


def k_factor(n_matches: int, graph_elo: float, rank_target: float) -> float:
    """K = base(n) × gap_factor.

    ``gap_factor`` scales K by how far the graph ELO sits from the rank target,
    clamped to [0.1, 1.0]: a big gap means near-full K (fast climb); near the
    target K is floored at 10% so growth slows and stabilizes.
    """
    base = _base_k(n_matches)
    gap = abs(rank_target - graph_elo) / GAP_DIVISOR
    gap_factor = max(GAP_FACTOR_MIN, min(GAP_FACTOR_MAX, gap))
    return base * gap_factor


def _your_entries(match: Any) -> list[dict[str, Any]]:
    """Athlete's own (``actor == 'you'``) sequence entries with a label."""
    out: list[dict[str, Any]] = []
    for entry in getattr(match, "sequence", None) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("actor") == "you" and entry.get("label"):
            out.append(entry)
    return out


def _mean_elo(graph: AthleteGraph) -> float | None:
    elos = [n.computed_elo for n in graph.nodes.values() if n.computed_elo is not None]
    if not elos:
        return None
    return sum(elos) / len(elos)


def replay_matches(
    athlete_name: str,
    matches: list[Any],
    rank_target: float,
    opp_elos: list[float],
    belt: str | None = "black",
) -> tuple[AthleteGraph, list[float]]:
    """Replay matches chronologically, growing per-node ELO toward ``rank_target``.

    Parameters
    ----------
    athlete_name : str
    matches : list
        Match-like objects (duck-typed: ``.sequence``, ``.won``, ``.win_type``),
        already sorted in chronological order.
    rank_target : float
        The athlete's rank ELO — the convergence target.
    opp_elos : list[float]
        Per-match opponent rating, parallel to ``matches``.
    belt : str | None
        Athlete belt → newly-seen node seed ELO (defaults to black / 800).

    Returns
    -------
    (AthleteGraph, list[float])
        The enriched graph (node ``computed_elo``, edge ``elo``, ``user_elo``)
        and the per-match ``graph_elo_after`` snapshot series.
    """
    graph = AthleteGraph(athlete=athlete_name)
    base = base_elo_for_belt(belt)
    snapshots: list[float] = []

    for i, match in enumerate(matches):
        opp_elo = opp_elos[i] if i < len(opp_elos) else rank_target
        your = _your_entries(match)

        # Seed / count participating nodes.
        participating: list[str] = []
        for entry in your:
            label = str(entry.get("label", ""))
            typ = str(entry.get("type", ""))
            norm = _normalize_name(label)
            node = graph.nodes.get(norm)
            if node is None:
                node = AthleteNode(label=label, type=typ, count=0, computed_elo=base)
                graph.nodes[norm] = node
            node.count += 1
            participating.append(norm)

        # Consecutive-pair edges (skip self-loops), mirroring build_athlete_graph.
        for j in range(1, len(your)):
            src = _normalize_name(your[j - 1].get("label", ""))
            tgt = _normalize_name(your[j].get("label", ""))
            if src == tgt:
                continue
            edge = graph.edges.get((src, tgt))
            if edge is None:
                edge = AthleteEdge(source=src, target=tgt, count=0)
                graph.edges[(src, tgt)] = edge
            edge.count += 1

        # If this match contributed no nodes, there is nothing to grow — record
        # the current mean (or base) and continue.
        current_mean = _mean_elo(graph)
        if not participating or current_mean is None:
            snapshots.append(current_mean if current_mean is not None else base)
            continue

        graph_elo = current_mean
        s = score_from_match(match)
        k = k_factor(i + 1, graph_elo, rank_target)
        delta = k * (s - expected(graph_elo, opp_elo))

        # Apply ``delta`` to each participating node, then converge-clamp so the
        # graph mean lands at most on the target when it would cross it.
        n_total = len(graph.nodes)
        unique = list(dict.fromkeys(participating))
        prospective_mean = graph_elo + delta * len(unique) / n_total
        scale = 1.0
        if (graph_elo < rank_target <= prospective_mean) or (
            graph_elo > rank_target >= prospective_mean
        ):
            denom = prospective_mean - graph_elo
            if denom != 0:
                scale = (rank_target - graph_elo) / denom
        for norm in unique:
            graph.nodes[norm].computed_elo = (
                graph.nodes[norm].computed_elo or base
            ) + delta * scale

        # Derive edge ELO from endpoint node ELO.
        for (src, tgt), edge in graph.edges.items():
            s_node = graph.nodes.get(src)
            t_node = graph.nodes.get(tgt)
            elos = [
                n.computed_elo
                for n in (s_node, t_node)
                if n is not None and n.computed_elo is not None
            ]
            if elos:
                edge.elo = sum(elos) / len(elos)

        snapshots.append(_mean_elo(graph) or base)

    graph.user_elo = _mean_elo(graph)
    return graph, snapshots
