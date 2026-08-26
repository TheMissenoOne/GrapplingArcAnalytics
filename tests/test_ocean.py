"""Tests for The Ocean payload builder (pure helpers + assembly off a fixture map)."""

from __future__ import annotations

from typing import Any

from analysis.grappling_map import map_from_network
from analysis.names import _normalize_name
from analysis.network_metrics import network_from_sequences
from analysis.ocean import name_regions, ocean_from_map, relativize


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


BC, RNC, CG, TRI = "Back Control", "Rear Naked Choke", "Closed Guard", "Triangle Choke"


def _sequences() -> list[list[dict[str, Any]]]:
    back = [_e(BC, "control", "A"), _e(RNC, "submission", "A", True)]
    return [
        back, back,
        [_e(CG, "guard", "B"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(CG, "guard", "B"), _e(TRI, "submission", "B", True)],
    ]


def test_relativize_percentile_and_ratio() -> None:
    nodes = [
        {"node_key": "a", "occ": 10, "pagerank": 0.1, "betweenness": 0.0, "reward_risk": 0.5},
        {"node_key": "b", "occ": 2, "pagerank": 0.02, "betweenness": 0.0, "reward_risk": 0.0},
        {"node_key": "c", "occ": 1, "pagerank": 0.01, "betweenness": 0.0, "reward_risk": -0.1},
    ]
    relativize(nodes, eff_index={"a": 0.7})
    # highest occ → top of the frequency population; ratio above the mean
    assert nodes[0]["metrics"]["frequency"]["pct"] == 100
    assert nodes[0]["metrics"]["frequency"]["ratio"] > 1
    assert nodes[2]["metrics"]["frequency"]["pct"] == 33  # 1 of 3 ≤ 1
    # effectiveness only where a score exists
    assert "effectiveness" in nodes[0]["metrics"]
    assert "effectiveness" not in nodes[1]["metrics"]


def test_name_regions_from_dominant_member() -> None:
    nodes = [
        {"node_key": "a", "label": BC, "occ": 10, "community": 0},
        {"node_key": "b", "label": RNC, "occ": 5, "community": 0},
        {"node_key": "c", "label": CG, "occ": 8, "community": 1},
        {"node_key": "d", "label": "X", "occ": 1, "community": None},
    ]
    regions = name_regions(nodes)
    names = {r["name"] for r in regions}
    assert f"{BC} system" in names and f"{CG} system" in names  # named after top-occ member
    assert nodes[0]["region"] == 0 and nodes[0]["color"].startswith("#")
    assert nodes[3]["region"] is None  # no community → unclustered
    assert all(r["color"].startswith("#") for r in regions)


def test_ocean_from_map_shape() -> None:
    gmap = map_from_network(network_from_sequences(_sequences()))
    observed = [n for n in gmap["nodes"].values() if n["observed"]]
    ocean = ocean_from_map(gmap, eff_index={})
    assert ocean["meta"]["positions"] == len(observed)
    assert ocean["meta"]["transitions"] == len(ocean["links"])
    assert all({"metrics", "color", "region", "size"} <= set(n) for n in ocean["nodes"])
    assert all(m in ocean["nodes"][0]["metrics"]
               for m in ("frequency", "centrality", "bridging", "favorability"))
    assert all(isinstance(e["weight"], int) for e in ocean["links"])
    assert all({"arrow", "dashed"} <= set(e) for e in ocean["links"])
    assert isinstance(ocean["regions"], list)


def test_ocean_collapses_reciprocal_pairs_and_orients_the_arrow() -> None:
    # BC <-> RNC both ways, BC dominant (4x) over RNC->BC (1x) — one link, arrow toward BC.
    seqs = _sequences() + [[_e(RNC, "submission", "A", True), _e(BC, "control", "A")]]
    gmap = map_from_network(network_from_sequences(seqs))
    ocean = ocean_from_map(gmap, eff_index={})
    bc, rnc = _normalize_name(BC), _normalize_name(RNC)
    pair_links = [lk for lk in ocean["links"] if {lk["from"], lk["to"]} == {bc, rnc}]
    assert len(pair_links) == 1  # no split, one link per unordered pair
    assert pair_links[0]["from"] == bc and pair_links[0]["to"] == rnc and pair_links[0]["arrow"]


def test_top_importance_filter_reduces_large_corpus_and_floors_small_ones() -> None:
    import math

    from analysis.ocean import MIN_KEEP_NODES, TOP_IMPORTANCE_PCT, _filter_top_importance

    # small corpus (below the floor): keep everyone, same as before the refurbish
    small = [{"node_key": f"n{i}", "occ": i + 1, "pagerank": (i + 1) / 10,
              "betweenness": 0.0, "reward_risk": 0.0} for i in range(5)]
    relativize(small, eff_index={})
    assert len(_filter_top_importance(small)) == 5

    # large corpus: the percentage bites, ranked by composite importance
    big = [{"node_key": f"n{i}", "occ": i + 1, "pagerank": (i + 1) / 200,
            "betweenness": (i + 1) / 200, "reward_risk": 0.0} for i in range(200)]
    relativize(big, eff_index={})
    kept = _filter_top_importance(big)
    assert len(kept) == max(MIN_KEEP_NODES, math.ceil(200 * TOP_IMPORTANCE_PCT / 100))
    kept_keys = {n["node_key"] for n in kept}
    assert "n199" in kept_keys  # highest occ/pagerank/betweenness → most important
    assert "n0" not in kept_keys  # lowest → filtered out


def test_radial_layout_is_deterministic_and_centers_the_most_important() -> None:
    import math

    from analysis.ocean import _radial_layout

    def _mk() -> list[dict[str, Any]]:
        return [{"id": i, "metrics": {"centrality": {"pct": p}, "bridging": {"pct": p},
                                       "frequency": {"pct": p}}}
                for i, p in enumerate((90, 10, 50, 70))]

    a, b = _mk(), _mk()
    _radial_layout(a)
    _radial_layout(b)
    by_a, by_b = {n["id"]: n for n in a}, {n["id"]: n for n in b}
    assert by_a[0]["x"] == by_b[0]["x"] and by_a[0]["y"] == by_b[0]["y"]  # no per-run RNG

    def radius(n: dict[str, Any]) -> float:
        return math.hypot(n["x"], n["y"])

    assert by_a[0]["imp"] == 1.0  # highest composite pct = most important
    assert radius(by_a[0]) < radius(by_a[1])  # most important sits nearer the centre


def test_top_transitions_orders_by_count() -> None:
    from analysis.lamas_chain import STATES
    from analysis.ocean import _top_transitions

    n = len(STATES)
    counts = [[0] * n for _ in range(n)]
    probs = [[0.0] * n for _ in range(n)]
    counts[0][1], probs[0][1] = 5, 0.5
    counts[2][3], probs[2][3] = 20, 0.8
    counts[4][4], probs[4][4] = 1, 1.0
    top = _top_transitions({"counts": counts, "probs": probs}, top_n=2)
    assert len(top) == 2
    assert top[0] == {"from": STATES[2], "to": STATES[3], "n": 20, "prob": 0.8}
    assert top[1]["n"] == 5


def test_elo_buckets_are_relative_not_raw() -> None:
    from analysis.ocean import _elo_buckets

    by_type = {
        "guard": (1200.0, 60.0, 10),
        "submission": (800.0, 40.0, 10),
        "escape": (1000.0, 0.0, 1),  # below MIN_POP → excluded
    }
    buckets = _elo_buckets(by_type)
    assert {b["type"] for b in buckets} == {"guard", "submission"}
    by_t = {b["type"]: b for b in buckets}
    assert by_t["guard"]["ratio"] == 1.2 and by_t["submission"]["ratio"] == 0.8  # vs grand mean
    assert by_t["guard"]["spread"] == 0.05  # std/mean, dimensionless — never a raw rating
    assert buckets[0]["type"] == "guard"  # highest ratio first


def test_ocean_dashes_low_landing_edges() -> None:
    # Fixed rule: dash iff weight >= 5, target type gated, and success < 0.40.
    # CG -> TRI five times, only one landing → success 0.2 → dashed.
    # BC -> RNC four times, all landing → below weight floor AND success 1.0 → not dashed.
    miss = [_e(CG, "guard", "B"), _e(TRI, "submission", "B", False)]
    land = [_e(CG, "guard", "B"), _e(TRI, "submission", "B", True)]
    seqs = _sequences() + [miss, miss, miss, miss, land]
    gmap = map_from_network(network_from_sequences(seqs))
    ocean = ocean_from_map(gmap, eff_index={})
    cg, tri = _normalize_name(CG), _normalize_name(TRI)
    bc, rnc = _normalize_name(BC), _normalize_name(RNC)
    cg_tri = next(lk for lk in ocean["links"] if {lk["from"], lk["to"]} == {cg, tri})
    assert cg_tri["dashed"] is True   # weight 5, success 1/5 = 0.2 < 0.40
    bc_rnc = next(lk for lk in ocean["links"] if {lk["from"], lk["to"]} == {bc, rnc})
    assert bc_rnc["dashed"] is False  # weight 4 < 5 floor (and success 1.0)
