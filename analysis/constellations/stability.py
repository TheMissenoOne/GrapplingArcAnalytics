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
import statistics
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
    p10_jaccard: float
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
) -> tuple[DetectionResult, dict[frozenset[str], float], dict[frozenset[str], float]]:
    """Resample ``units`` with replacement, rebuild + redetect each time, and track
    how well each baseline constellation survives (best-match Jaccard per resample).

    Doc 04 asks for mean AND 10th-percentile Jaccard — a constellation that's stable
    on average but falls apart in the worst resamples (mean 0.7, p10 0.2) is fragile
    in a way the mean alone hides. ``statistics.quantiles(..., n=10)[0]`` is the 10th
    percentile (stdlib, no new dependency).

    Returns ``(baseline_detection, {members: mean_jaccard}, {members: p10_jaccard})``.
    """
    if baseline is None:
        baseline = detect(build_graph(units), resolution=resolution, seed=detect_seed)
    rng = random.Random(rng_seed)
    keys = [frozenset(c.members) for c in baseline.constellations]
    samples: dict[frozenset[str], list[float]] = {k: [] for k in keys}
    for _ in range(n_resamples):
        sample = [rng.choice(units) for _ in units] if units else []
        result = detect(build_graph(sample), resolution=resolution, seed=detect_seed)
        resample_members = [c.members for c in result.constellations]
        for key in keys:
            samples[key].append(_best_jaccard(set(key), resample_members))
    mean = {k: round(sum(v) / len(v), 5) if v else 0.0 for k, v in samples.items()}
    p10 = {k: _p10(v) for k, v in samples.items()}
    return baseline, mean, p10


def _p10(values: list[float]) -> float:
    if len(values) >= 2:
        return round(statistics.quantiles(values, n=10)[0], 5)
    if values:
        return round(values[0], 5)
    return 0.0


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
    p10_by_key: dict[frozenset[str], float] | None = None,
    p10_fragile_threshold: float = 0.3,
) -> list[ConstellationStability]:
    """Combine bootstrap Jaccard + leave-one-out driver + support into one row per
    baseline constellation. ``stable_threshold``/``p10_fragile_threshold`` are first
    cuts (wave 4/wave-4-follow-up); refine against a published corpus, not by feel
    here.

    ``p10_by_key`` is optional (callers that only have a mean, e.g. pre-p10 code, can
    omit it) — when absent, a constellation's p10 falls back to its own mean, which
    reproduces the old mean-only classification exactly (mean >= stable_threshold
    already implies that fallback p10 clears ``p10_fragile_threshold`` whenever
    ``p10_fragile_threshold <= stable_threshold``, the default relationship here).
    When p10 IS supplied, a constellation with a high mean but a low p10 (stable on
    average, fragile in the worst resamples) is downgraded out of STABLE — that's the
    whole point of tracking p10 per doc 04.
    """
    out: list[ConstellationStability] = []
    for c in baseline.constellations:
        key = frozenset(c.members)
        mj = jaccard_by_key.get(key, 0.0)
        p10 = p10_by_key.get(key, mj) if p10_by_key is not None else mj
        driver = driver_by_key.get(key)
        if driver is not None:
            status = Stability.ATHLETE_DRIVEN
        elif mj >= stable_threshold and p10 >= p10_fragile_threshold:
            status = Stability.STABLE
        else:
            status = Stability.PARTIALLY_STABLE
        n_support = support(c.members, athlete_nodes) if athlete_nodes else 0
        out.append(ConstellationStability(
            members=c.members, hub=c.hub, mean_jaccard=mj, p10_jaccard=p10,
            classification=status, support_athletes=n_support,
        ))
    return out
