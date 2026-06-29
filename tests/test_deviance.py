"""Proportional per-node deviance tests (Phase 2A) — no DB required."""

from __future__ import annotations

from analysis.deviance import (
    node_deviance,
    node_population_stats,
    signature_nodes,
    type_deviance_vector,
)


class _N:
    def __init__(self, key: str, ntype: str, elo: float | None) -> None:
        self.node_key = key
        self.node_type = ntype
        self.computed_elo = elo


def _graph(gid: str, nodes: list[_N]) -> tuple[str, list[_N]]:
    return (gid, nodes)


def test_population_stats_mean_std_n():
    graphs = [
        _graph("g1", [_N("back control", "control", 1000.0)]),
        _graph("g2", [_N("back control", "control", 1200.0)]),
        _graph("g3", [_N("back control", "control", 1400.0)]),
    ]
    by_key, by_type = node_population_stats(graphs)
    mean, std, n = by_key["back control"]
    assert n == 3
    assert abs(mean - 1200.0) < 1e-6
    assert std > 0
    # type baseline aggregates the same observations under 'control'
    assert by_type["control"][2] == 3


def test_back_take_debias_equal_raw_elo_different_deviance():
    # Population: back control is a UNIVERSALLY high-ELO node (everyone ~1300); heel hook is
    # rarer/lower (~1000). Two athletes BOTH have raw ELO 1300 on each.
    graphs = [
        _graph(f"g{i}", [_N("back control", "control", e), _N("heel hook", "submission", h)])
        for i, (e, h) in enumerate([(1250, 950), (1300, 1000), (1350, 1050)])
    ]
    by_key, by_type = node_population_stats(graphs)
    # An athlete with 1300 on both: back control 1300 ≈ population mean → ~0 deviance;
    # heel hook 1300 is far above its (~1000) population → strongly positive deviance.
    bc = node_deviance(_N("back control", "control", 1300.0), by_key, by_type)
    hh = node_deviance(_N("heel hook", "submission", 1300.0), by_key, by_type)
    assert abs(bc) < 0.6  # near population average despite high raw ELO
    assert hh > 1.5  # genuinely elite relative to peers
    assert hh > bc


def test_sparse_node_falls_back_to_type_baseline():
    # 'kani basami' appears once (n<MIN_POP) → judged against the 'takedown' type baseline.
    graphs = [
        _graph("g1", [_N("double leg", "takedown", 1000.0)]),
        _graph("g2", [_N("single leg", "takedown", 1100.0)]),
        _graph("g3", [_N("ankle pick", "takedown", 1200.0)]),
    ]
    by_key, by_type = node_population_stats(graphs)
    rare = _N("kani basami", "takedown", 1500.0)  # unseen key, high vs takedown mean (~1100)
    z = node_deviance(rare, by_key, by_type)
    assert z > 1.0  # resolved via the type baseline, not skipped


def test_signature_nodes_threshold_and_order():
    graphs = [
        _graph("g1", [_N("a", "control", 900.0), _N("b", "guard", 950.0)]),
        _graph("g2", [_N("a", "control", 1000.0), _N("b", "guard", 1000.0)]),
        _graph("g3", [_N("a", "control", 1100.0), _N("b", "guard", 1050.0)]),
    ]
    by_key, by_type = node_population_stats(graphs)
    athlete = [_N("a", "control", 2000.0), _N("b", "guard", 1000.0)]  # a far above, b average
    sigs = signature_nodes(athlete, by_key, by_type)
    assert [k for k, _ in sigs] == ["a"]  # only the above-population node is a signature


def test_type_deviance_vector_length_and_zero_default():
    vec = type_deviance_vector([], {}, {})
    assert len(vec) == 8
    assert all(v == 0.0 for v in vec)
