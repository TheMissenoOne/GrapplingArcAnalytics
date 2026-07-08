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


def test_ocean_dashes_below_the_corpus_success_threshold() -> None:
    # A submission-targeted edge with weight >= 3 and success well below a low threshold dashes;
    # a same-shape edge with success above it doesn't.
    gmap = map_from_network(network_from_sequences(_sequences()))
    ocean_low = ocean_from_map(gmap, eff_index={}, success_thresh=0.1)
    bc, rnc = _normalize_name(BC), _normalize_name(RNC)
    link = next(lk for lk in ocean_low["links"] if lk["from"] == bc and lk["to"] == rnc)
    assert link["dashed"] is False  # BC->RNC success is 1.0 (all 4 landed) >= 0.1
    ocean_high = ocean_from_map(gmap, eff_index={}, success_thresh=1.01)
    link_high = next(lk for lk in ocean_high["links"] if lk["from"] == bc and lk["to"] == rnc)
    assert link_high["dashed"] is True  # 1.0 < 1.01
