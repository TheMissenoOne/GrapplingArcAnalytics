#!/usr/bin/env python
"""Read-only measurement: is the 20-27% community-membership change under confidence
weighting (``docs/rating_v2/07_PONDERACAO_POR_CONFIANCA.md``) a real effect of the
weighting, or is it inside the Louvain detector's own bootstrap noise on this corpus
(ADR-07/ADR-08, ``docs/rating_v2/01_DECISOES.md``)?

**The honest test** (as specified by the task): compare the distance between two
independent bootstrap resamples of the SAME weight scheme against the distance between
the (unresampled) baseline partitions of DIFFERENT schemes. If same-scheme resample
noise is the same order of magnitude as the between-scheme distance, the 20-27% number
is not attributable to weighting.

Reuses ``scripts.measure_confidence_weighting.load_corpus`` (same DB read, same
``run_id``/discipline filter — no second corpus definition), ``analysis.confidence_weight``
(the three schemes), ``analysis.transitions.network_from_sequences`` (``weight_fn``),
and ``analysis.constellations.detect``/``compare`` (the same detector + comparator ADR-07/
ADR-08 already measured against). Never writes to the DB, never calls
``export.site_data``. Writes JSON to
``reports/rating_v2/community_stability_under_weight.json`` (gitignored).

    uv run python -m scripts.measure_community_stability_under_weight [--run-id UUID]
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import networkx as nx

from analysis.confidence_weight import SCHEMES, athlete_weights
from analysis.constellations.compare import compare_partitions
from analysis.constellations.detect import detect
from analysis.transitions import network_from_sequences
from scripts.measure_confidence_weighting import DEFAULT_RUN_ID, load_corpus

OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "reports" / "rating_v2" / "community_stability_under_weight.json"
)
N_RESAMPLES = 30  # even -> 15 non-overlapping resample-pairs per scheme; ~213-node graph,
# 3 schemes x 30 detect() calls is seconds, not minutes -- raise if a published number
# needs a tighter interval than the one measured here.


# ── pure measurement (no DB) ─────────────────────────────────────────────────

def _graph_for(
    scheme: str, sequences: list[list[dict[str, Any]]], weights_by_scheme: dict[str, dict[str, float]],
) -> nx.DiGraph:
    if scheme == "uniform":
        return network_from_sequences(sequences)
    # `dict.get` is overloaded; bind it to the single signature `network_from_sequences`
    # declares rather than passing the overload set.
    weights = weights_by_scheme[scheme]
    return network_from_sequences(sequences, weight_fn=lambda key: weights.get(key, 0.0))


def resample_partitions(
    units: list[str],
    build_graph: Callable[[list[str]], nx.DiGraph],
    n_resamples: int,
    resolution: float = 1.0,
    detect_seed: int = 42,
    rng_seed: int = 42,
) -> list[list[list[str]]]:
    """``n_resamples`` independent bootstrap resamples of ``units`` (with replacement),
    each rebuilt + redetected. Returns one member-list-per-community per resample — raw
    material for both baseline-vs-resample and resample-vs-resample comparisons."""
    rng = random.Random(rng_seed)
    out: list[list[list[str]]] = []
    for _ in range(n_resamples):
        sample = [rng.choice(units) for _ in units] if units else []
        result = detect(build_graph(sample), resolution=resolution, seed=detect_seed)
        out.append([c.members for c in result.constellations])
    return out


def resample_pair_distances(partitions: list[list[list[str]]]) -> list[float]:
    """Mean best-match Jaccard between non-overlapping PAIRS of independent resamples —
    "two bootstraps of the same scheme", the task's own definition of noise."""
    return [
        compare_partitions(partitions[i], partitions[i + 1]).mean_jaccard
        for i in range(0, len(partitions) - 1, 2)
    ]


def baseline_vs_resample_distances(
    baseline_members: list[list[str]], partitions: list[list[list[str]]],
) -> list[float]:
    """Mean best-match Jaccard between the fixed baseline and each resample — the same
    statistic ADR-07/ADR-08 (``05_COMPARACAO_DETECTORES.md``) already measured (0.58-0.85
    per athlete) — the scale sanity check for this measurement."""
    return [compare_partitions(p, baseline_members).mean_jaccard for p in partitions]


def _summary(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0, "mean": 0.0, "min": 0.0, "median": 0.0, "max": 0.0}
    s = sorted(vals)
    n = len(s)
    return {
        "n": n,
        "mean": round(sum(s) / n, 4),
        "min": round(s[0], 4),
        "median": round(s[n // 2], 4),
        "max": round(s[-1], 4),
    }


def run_measurement(
    sequences: list[list[dict[str, Any]]],
    athlete_ids: set[str],
    rd_by_athlete: dict[str, float],
    n_resamples: int = N_RESAMPLES,
    resolution: float = 1.0,
    seed: int = 42,
) -> dict[str, Any]:
    """Pure orchestration over already-loaded corpus data."""
    weights_by_scheme = {s: athlete_weights(athlete_ids, rd_by_athlete, s) for s in SCHEMES}

    baseline_detection = {
        s: detect(_graph_for(s, sequences, weights_by_scheme), resolution=resolution, seed=seed)
        for s in SCHEMES
    }
    baseline_members = {
        s: [c.members for c in baseline_detection[s].constellations] for s in SCHEMES
    }

    units = [str(i) for i in range(len(sequences))]

    def build_graph(scheme: str) -> Callable[[list[str]], nx.DiGraph]:
        def _b(sample_units: list[str]) -> nx.DiGraph:
            subset = [sequences[int(u)] for u in sample_units]
            return _graph_for(scheme, subset, weights_by_scheme)
        return _b

    within: dict[str, dict[str, list[float]]] = {}
    for s in SCHEMES:
        partitions = resample_partitions(units, build_graph(s), n_resamples, resolution, seed, seed)
        within[s] = {
            "resample_pair": resample_pair_distances(partitions),
            "baseline_vs_resample": baseline_vs_resample_distances(baseline_members[s], partitions),
        }

    # between-scheme distance = the exact number reported in wave 9 (0.689 / 0.711) —
    # recomputed here from this run's own baselines as a consistency check.
    between_scheme = {
        s: compare_partitions(baseline_members[s], baseline_members["uniform"]).mean_jaccard
        for s in ("precision", "bounded")
    }

    # noise floor: pooled resample-pair distances across all three schemes -- the
    # detector's own same-scheme bootstrap variability on this corpus, scheme-agnostic.
    pooled_noise = [v for s in SCHEMES for v in within[s]["resample_pair"]]
    noise_floor = _summary(pooled_noise)

    verdicts = {}
    for s in ("precision", "bounded"):
        b = between_scheme[s]
        own_floor = _summary(within[s]["resample_pair"])
        # position of the between-scheme distance relative to this scheme's own
        # same-scheme resample-pair distribution:
        #   below_range -> switching scheme disagrees MORE than resampling ever does
        #                  on this corpus -> real effect, not attributable to noise.
        #   within/above_range -> resampling alone already produces partitions at
        #                  least as different -> indistinguishable from noise.
        if b < own_floor["min"]:
            position = "below_range"
        elif b > own_floor["max"]:
            position = "above_range"
        else:
            position = "within_range"
        verdicts[s] = {
            "between_scheme_jaccard": round(b, 4),
            "own_scheme_resample_pair_floor": own_floor,
            "position_vs_own_noise_floor": position,
            "verdict": "effect" if position == "below_range" else "noise",
        }

    stability_by_scheme = {
        s: {
            "resample_pair": _summary(within[s]["resample_pair"]),
            "baseline_vs_resample": _summary(within[s]["baseline_vs_resample"]),
        }
        for s in SCHEMES
    }
    more_stable_than_uniform = {
        s: stability_by_scheme[s]["baseline_vs_resample"]["mean"]
        > stability_by_scheme["uniform"]["baseline_vs_resample"]["mean"]
        for s in ("precision", "bounded")
    }

    return {
        "run_id": DEFAULT_RUN_ID,
        "n_sequences": len(sequences),
        "n_resamples_per_scheme": n_resamples,
        "n_resample_pairs_per_scheme": n_resamples // 2,
        "stability_by_scheme": stability_by_scheme,
        "noise_floor_pooled_resample_pair": noise_floor,
        "between_scheme_vs_uniform": verdicts,
        "more_stable_than_uniform_baseline_vs_resample_mean": more_stable_than_uniform,
    }


# ── DB read (only impure part; reuses measure_confidence_weighting's loader) ────────────

def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--n-resamples", type=int, default=N_RESAMPLES)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    from db.base import db_session

    with db_session() as session:
        corpus = load_corpus(session, args.run_id)

    result = run_measurement(
        corpus["sequences"], corpus["athlete_ids"], corpus["rd_by_athlete"],
        n_resamples=args.n_resamples,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n")
    print(f"wrote {args.out}")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
