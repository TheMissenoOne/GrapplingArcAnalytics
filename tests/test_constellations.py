"""Tests for the shared constellations layer (wave 4, docs/rating_v2/) — pure, in-memory."""

from __future__ import annotations

from unittest.mock import patch

import networkx as nx

from analysis.constellations.compare import compare_partitions, jaccard
from analysis.constellations.detect import constellation_fingerprint, detect
from analysis.constellations.stability import (
    bootstrap_jaccard,
    classify_stability,
    leave_one_out_athlete_driven,
    support,
)


def _two_cliques() -> nx.DiGraph:
    """Two disjoint triangles — an obvious two-community graph."""
    g = nx.DiGraph()
    for a, b in [("A1", "A2"), ("A2", "A3"), ("A3", "A1")]:
        g.add_edge(a, b, weight=5)
    for a, b in [("B1", "B2"), ("B2", "B3"), ("B3", "B1")]:
        g.add_edge(a, b, weight=5)
    return g


def test_detect_partitions_two_cliques() -> None:
    result = detect(_two_cliques())
    member_sets = [set(c.members) for c in result.constellations]
    assert {"A1", "A2", "A3"} in member_sets
    assert {"B1", "B2", "B3"} in member_sets
    assert result.rejected_count == 0


def test_detect_is_deterministic() -> None:
    g = _two_cliques()
    r1 = detect(g)
    r2 = detect(g)
    assert [c.members for c in r1.constellations] == [c.members for c in r2.constellations]
    assert r1.modularity == r2.modularity


def test_detect_signature_has_no_rating_argument() -> None:
    # The invariant, made concrete: two graphs with identical topology but different
    # node metadata (standing in for "rating") produce the same partition, because
    # detect() only ever looks at edge weight.
    g1 = _two_cliques()
    g2 = _two_cliques()
    for n in g2.nodes:
        g2.nodes[n]["fake_rating"] = 9999  # detect() must never read this
    r1, r2 = detect(g1), detect(g2)
    assert [c.members for c in r1.constellations] == [c.members for c in r2.constellations]


def test_detect_empty_graph() -> None:
    result = detect(nx.DiGraph())
    assert result.constellations == []
    assert result.rejected_rate == 0.0


def test_detect_multiple_isolated_nodes_no_zero_division() -> None:
    # >=2 nodes, zero edges: Louvain hands back one singleton community per node
    # (len(raw) > 1), but nx.community.modularity divides by total degree — 0 here.
    # Found while running the wave-6 detector comparison against real athlete graphs.
    g = nx.DiGraph()
    g.add_nodes_from(["A", "B", "C"])
    result = detect(g)
    assert result.modularity == 0.0
    assert sorted(c.members for c in result.constellations) == [["A"], ["B"], ["C"]]


def test_disconnected_community_is_broken_and_counted() -> None:
    # Force Louvain to hand back one community spanning two disconnected components —
    # the exact ADR-07 failure mode — and check the gate splits + counts it.
    g = _two_cliques()
    und = nx.Graph()
    und.add_nodes_from(g.nodes())
    for u, v, d in g.edges(data=True):
        und.add_edge(u, v, weight=d["weight"])
    fake_partition = [{"A1", "A2", "A3", "B1", "B2", "B3"}]

    with patch("analysis.constellations.detect.nx.community.louvain_communities",
               return_value=fake_partition):
        result = detect(g)

    assert result.rejected_count == 1
    assert result.rejected_rate == 1.0
    member_sets = [set(c.members) for c in result.constellations]
    assert {"A1", "A2", "A3"} in member_sets
    assert {"B1", "B2", "B3"} in member_sets


def test_fingerprint_stable_under_member_reorder() -> None:
    assert constellation_fingerprint(["A1", "A2", "A3"]) == constellation_fingerprint(
        ["A3", "A1", "A2"]
    )


def test_fingerprint_stable_across_reexecution() -> None:
    # Two independent calls, same process, same input -> same fingerprint (the
    # determinism a hash(sorted(...)) built on Python's randomized string hash()
    # could NOT give across separate process runs; sha256 does).
    assert constellation_fingerprint(["closed guard", "armbar"]) == constellation_fingerprint(
        ["closed guard", "armbar"]
    )


def test_fingerprint_changes_when_membership_changes() -> None:
    fp_before = constellation_fingerprint(["A1", "A2", "A3"])
    fp_after = constellation_fingerprint(["A1", "A2", "A3", "A4"])
    assert fp_before != fp_after


def test_detect_attaches_fingerprint_to_every_constellation() -> None:
    result = detect(_two_cliques())
    for c in result.constellations:
        assert c.fingerprint == constellation_fingerprint(c.members)


def test_jaccard() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a"}, {"b"}) == 0.0
    assert jaccard(set(), set()) == 1.0


def test_compare_partitions_coverage_and_best_match() -> None:
    a = [["a", "b"], ["c"]]
    b = [["a", "b", "x"]]
    result = compare_partitions(a, b)
    assert result.only_in_a == {"c"}
    assert result.only_in_b == {"x"}
    assert result.common_nodes == {"a", "b"}


def test_support_counts_distinct_contributing_athletes() -> None:
    athlete_nodes = {"gordon": {"BC", "RNC"}, "kade": {"BC"}, "ffion": {"TRI"}}
    assert support(["BC", "RNC"], athlete_nodes) == 2
    assert support(["TRI"], athlete_nodes) == 1
    assert support(["nothing"], athlete_nodes) == 0


def _units_by_athlete() -> dict[str, list[str]]:
    # "gordon" is the ONLY contributor of A-clique units; "kade" the only contributor
    # of B-clique units. Removing either athlete removes their clique entirely.
    return {
        "gordon": [f"a{i}" for i in range(9)],
        "kade": [f"b{i}" for i in range(10)],
    }


def _build_graph(units: list[str]) -> nx.DiGraph:
    g = nx.DiGraph()
    for u in units:
        if u.startswith("a"):
            edges = [("A1", "A2"), ("A2", "A3"), ("A3", "A1")]
        else:
            edges = [("B1", "B2"), ("B2", "B3"), ("B3", "B1")]
        for x, y in edges:
            if g.has_edge(x, y):
                g[x][y]["weight"] += 1
            else:
                g.add_edge(x, y, weight=1)
    return g


def test_bootstrap_jaccard_identical_topology_is_stable() -> None:
    units = _units_by_athlete()
    all_units = [u for us in units.values() for u in us]
    baseline, mean_jaccard, p10_jaccard = bootstrap_jaccard(all_units, _build_graph, n_resamples=20)
    assert baseline.constellations
    # every resample has the same two-clique topology (every unit adds the same edges
    # for its clique) so agreement should be very high, near 1.0 — mean AND p10.
    assert all(v > 0.8 for v in mean_jaccard.values())
    assert all(v > 0.8 for v in p10_jaccard.values())


def test_bootstrap_jaccard_p10_below_mean_for_fragile_corpus() -> None:
    # A community that survives most resamples intact but vanishes in a worst-case
    # slice (support concentrated in a couple of units) should show p10 well below
    # its mean — that's the whole reason to track p10 separately (doc 04).
    def fragile_graph(units: list[str]) -> nx.DiGraph:
        g = nx.DiGraph()
        # A1-A2-A3 clique only exists if "trigger" unit is present in the sample.
        if "trigger" in units:
            for a, b in [("A1", "A2"), ("A2", "A3"), ("A3", "A1")]:
                g.add_edge(a, b, weight=5)
        else:
            g.add_nodes_from(["A1", "A2", "A3"])
        return g

    units = ["trigger"] + [f"filler{i}" for i in range(9)]
    baseline, mean_jaccard, p10_jaccard = bootstrap_jaccard(units, fragile_graph, n_resamples=100)
    key = frozenset({"A1", "A2", "A3"})
    assert key in mean_jaccard
    # "trigger" survives most (not all) bootstrap resamples with replacement from 10
    # units, so mean stays high while the worst 10% of resamples (missing "trigger")
    # drag p10 down toward 0 — the fragility a mean alone would hide.
    assert p10_jaccard[key] < mean_jaccard[key]


def test_leave_one_out_flags_athlete_driven_constellation() -> None:
    units = _units_by_athlete()
    driver = leave_one_out_athlete_driven(units, _build_graph)
    # "gordon" is the sole source of the A-clique's edges — removing him removes the
    # whole community from the redetected partition, so it's flagged athlete-driven.
    a_key = frozenset({"A1", "A2", "A3"})
    b_key = frozenset({"B1", "B2", "B3"})
    assert driver[a_key] == "gordon"
    assert driver[b_key] == "kade"


def test_classify_stability_stable_and_athlete_driven() -> None:
    units = _units_by_athlete()
    all_units = [u for us in units.values() for u in us]
    baseline, mean_jaccard, p10_jaccard = bootstrap_jaccard(all_units, _build_graph, n_resamples=10)
    a_key = frozenset({"A1", "A2", "A3"})
    b_key = frozenset({"B1", "B2", "B3"})
    driver = {a_key: "gordon", b_key: None}
    rows = classify_stability(baseline, mean_jaccard, driver, p10_by_key=p10_jaccard)
    by_key = {frozenset(r.members): r for r in rows}
    assert by_key[a_key].classification.value == "ATHLETE_DRIVEN"
    assert by_key[b_key].classification.value == "STABLE"


def test_classify_stability_downgrades_high_mean_low_p10() -> None:
    # doc 04's own example: mean 0.7, p10 0.2 is fragile and must NOT read as STABLE
    # even though the mean alone clears the default stable_threshold.
    baseline_result = detect(_two_cliques())
    key = frozenset({"A1", "A2", "A3"})
    mean_jaccard = {key: 0.7}
    p10_jaccard = {key: 0.2}
    rows = classify_stability(baseline_result, mean_jaccard, {key: None}, p10_by_key=p10_jaccard)
    assert rows[0].classification.value == "PARTIALLY_STABLE"
    assert rows[0].p10_jaccard == 0.2
