"""Tests for scripts/measure_community_stability_under_weight.py — pure, in-memory,
no DB. Fixture: two athletes each own a disjoint 3-node clique, so topology (and
therefore Louvain's partition) never changes across weight schemes or resamples —
the noise-vs-effect verdict should read "noise" for both non-uniform schemes."""

from __future__ import annotations

from scripts.measure_community_stability_under_weight import (
    _summary,
    resample_pair_distances,
    resample_partitions,
    run_measurement,
)


def _seq(actor: str, a: str, b: str) -> list[dict[str, object]]:
    return [
        {"label": a, "type": "position", "actor_id": actor, "successful": True},
        {"label": b, "type": "position", "actor_id": actor, "successful": True},
    ]


def _fixture_sequences() -> list[list[dict[str, object]]]:
    # gordon: A1->A2->A3->A1 clique, repeated 12x; kade: B1->B2->B3->B1, repeated 12x.
    gordon_edges = [("A1", "A2"), ("A2", "A3"), ("A3", "A1")]
    kade_edges = [("B1", "B2"), ("B2", "B3"), ("B3", "B1")]
    seqs = []
    for _ in range(12):
        for a, b in gordon_edges:
            seqs.append(_seq("gordon", a, b))
        for a, b in kade_edges:
            seqs.append(_seq("kade", a, b))
    return seqs


def test_summary_basic() -> None:
    assert _summary([]) == {"n": 0, "mean": 0.0, "min": 0.0, "median": 0.0, "max": 0.0}
    s = _summary([1.0, 0.5, 0.0])
    assert s["n"] == 3
    assert s["min"] == 0.0
    assert s["max"] == 1.0
    assert s["mean"] == 0.5


def test_resample_pair_distances_identical_partitions_is_one() -> None:
    same = [["A1", "A2"], ["B1", "B2"]]
    partitions = [same, same, same, same]
    dists = resample_pair_distances(partitions)
    assert dists == [1.0, 1.0]


def test_run_measurement_identical_topology_reads_as_noise() -> None:
    sequences = _fixture_sequences()
    athlete_ids = {"gordon", "kade"}
    # differing confidence -- weight scales edges, never removes the clique structure
    rd_by_athlete = {"gordon": 50.0, "kade": 300.0}

    result = run_measurement(sequences, athlete_ids, rd_by_athlete, n_resamples=10, seed=42)

    assert result["n_sequences"] == len(sequences)
    for scheme in ("uniform", "precision", "bounded"):
        assert scheme in result["stability_by_scheme"]

    for scheme in ("precision", "bounded"):
        v = result["between_scheme_vs_uniform"][scheme]
        # topology is identical across schemes (weight scales, doesn't disconnect the
        # cliques) so the between-scheme partition IS the same partition -> jaccard 1.0
        assert v["between_scheme_jaccard"] == 1.0
        assert v["verdict"] == "noise"


def test_resample_partitions_deterministic_given_seed() -> None:
    sequences = _fixture_sequences()

    def build_graph(units: list[str]) -> object:
        from analysis.transitions import network_from_sequences

        idxs = [int(u) for u in units]
        return network_from_sequences([sequences[i] for i in idxs])

    units = [str(i) for i in range(len(sequences))]
    r1 = resample_partitions(units, build_graph, n_resamples=5, rng_seed=7)
    r2 = resample_partitions(units, build_graph, n_resamples=5, rng_seed=7)
    assert r1 == r2
