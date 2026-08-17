"""Bootstrap stability + support for constellations — the category report's gate.

Resamples an opaque list of "units" (match ids, athlete ids — caller's choice) via a
``build_graph`` callback and re-runs ``constellations.detect`` on each resample, never
touching rating. Classifies each baseline constellation ``STABLE`` /
``PARTIALLY_STABLE`` / ``ATHLETE_DRIVEN`` and exposes ``support`` (distinct athletes
contributing) — the "more than one athlete" gate the category report needs before it
can call a pattern a division trend instead of one athlete's game.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import networkx as nx

from analysis.constellations.compare import jaccard
from analysis.constellations.detect import DetectionResult, detect


class Stability(StrEnum):
    STABLE = "STABLE"
    PARTIALLY_STABLE = "PARTIALLY_STABLE"
    ATHLETE_DRIVEN = "ATHLETE_DRIVEN"


@dataclass
class ConstellationStability:
    members: list[str]
    hub: str
    mean_jaccard: float
    classification: Stability
    support_athletes: int


def support(members: list[str], athlete_nodes: dict[str, set[str]], min_shared: int = 1) -> int:
    """Count of distinct athletes whose own node-set overlaps ``members`` by at least
    ``min_shared`` — the category-report publication gate ("more than one athlete
    contributing")."""
    m = set(members)
    return sum(1 for nodes in athlete_nodes.values() if len(nodes & m) >= min_shared)


def _best_jaccard(members: set[str], other_communities: list[list[str]]) -> float:
    return max((jaccard(members, set(c)) for c in other_communities), default=0.0)


def bootstrap_jaccard(
    units: list[str],
    build_graph: Callable[[list[str]], nx.DiGraph],
    baseline: DetectionResult | None = None,
    n_resamples: int = 200,
    resolution: float = 1.0,
    detect_seed: int = 42,
    rng_seed: int = 42,
) -> tuple[DetectionResult, dict[frozenset[str], float]]:
    """Resample ``units`` with replacement, rebuild + redetect each time, and average
    how well each baseline constellation survives (best-match Jaccard per resample).

    Returns ``(baseline_detection, {frozenset(members): mean_jaccard})``.
    """
    if baseline is None:
        baseline = detect(build_graph(units), resolution=resolution, seed=detect_seed)
    rng = random.Random(rng_seed)
    keys = [frozenset(c.members) for c in baseline.constellations]
    totals = dict.fromkeys(keys, 0.0)
    for _ in range(n_resamples):
        sample = [rng.choice(units) for _ in units] if units else []
        result = detect(build_graph(sample), resolution=resolution, seed=detect_seed)
        resample_members = [c.members for c in result.constellations]
        for key in keys:
            totals[key] += _best_jaccard(set(key), resample_members)
    mean = {k: round(v / n_resamples, 5) if n_resamples else 0.0 for k, v in totals.items()}
    return baseline, mean


def leave_one_out_athlete_driven(
    units_by_athlete: dict[str, list[str]],
    build_graph: Callable[[list[str]], nx.DiGraph],
    baseline: DetectionResult | None = None,
    resolution: float = 1.0,
    seed: int = 42,
    vanish_threshold: float = 0.2,
) -> dict[frozenset[str], str | None]:
    """For each athlete, remove their units and redetect. A baseline constellation
    whose best-match Jaccard drops below ``vanish_threshold`` once a given athlete is
    removed is driven by that athlete (first athlete found wins, in sorted order —
    deterministic when more than one removal would sink the same constellation).

    Returns ``{frozenset(members): dominant_athlete_id | None}``.
    """
    all_units = [u for units in units_by_athlete.values() for u in units]
    if baseline is None:
        baseline = detect(build_graph(all_units), resolution=resolution, seed=seed)
    driver: dict[frozenset[str], str | None] = {
        frozenset(c.members): None for c in baseline.constellations
    }
    for athlete_id in sorted(units_by_athlete):
        remaining = [
            u for aid, units in units_by_athlete.items() if aid != athlete_id for u in units
        ]
        result = detect(build_graph(remaining), resolution=resolution, seed=seed)
        resample_members = [c.members for c in result.constellations]
        for key, current in driver.items():
            if current is None and _best_jaccard(set(key), resample_members) < vanish_threshold:
                driver[key] = athlete_id
    return driver


def classify_stability(
    baseline: DetectionResult,
    jaccard_by_key: dict[frozenset[str], float],
    driver_by_key: dict[frozenset[str], str | None],
    athlete_nodes: dict[str, set[str]] | None = None,
    stable_threshold: float = 0.7,
) -> list[ConstellationStability]:
    """Combine bootstrap Jaccard + leave-one-out driver + support into one row per
    baseline constellation. ``stable_threshold`` is a first cut (wave 4); refine
    against a published corpus in the wave 6 report, not by feel here."""
    out: list[ConstellationStability] = []
    for c in baseline.constellations:
        key = frozenset(c.members)
        mj = jaccard_by_key.get(key, 0.0)
        driver = driver_by_key.get(key)
        if driver is not None:
            status = Stability.ATHLETE_DRIVEN
        elif mj >= stable_threshold:
            status = Stability.STABLE
        else:
            status = Stability.PARTIALLY_STABLE
        n_support = support(c.members, athlete_nodes) if athlete_nodes else 0
        out.append(ConstellationStability(
            members=c.members, hub=c.hub, mean_jaccard=mj,
            classification=status, support_athletes=n_support,
        ))
    return out
