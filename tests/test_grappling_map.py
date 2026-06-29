"""Tests for the general grappling-map assembly (pure, off fixture sequences)."""

from __future__ import annotations

from typing import Any

from analysis.grappling_map import _synonymish, attach_neighbors, map_from_network
from analysis.names import _normalize_name
from analysis.network_metrics import network_from_sequences


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


BC, RNC, CG, TRI = "Back Control", "Rear Naked Choke", "Closed Guard", "Triangle Choke"


def _sequences() -> list[list[dict[str, Any]]]:
    back_to_sub = [_e(BC, "control", "A"), _e(RNC, "submission", "A", True)]
    return [
        back_to_sub, back_to_sub,
        [_e(CG, "guard", "B"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(CG, "guard", "B"), _e(TRI, "submission", "B", True)],
    ]


def _map() -> dict[str, Any]:
    return map_from_network(network_from_sequences(_sequences()))


def test_nodes_keyed_by_normalize_with_stats() -> None:
    gmap = _map()
    bc = gmap["nodes"][_normalize_name(BC)]
    assert bc["label"] == BC and bc["type"] == "control" and bc["observed"] is True
    assert bc["occ"] >= 3
    assert bc["pagerank"] > 0
    # every node carries the full stat surface
    assert {"reward_risk", "betweenness", "community", "neighbours"} <= set(bc)


def test_edges_use_normalized_keys_and_weight() -> None:
    gmap = _map()
    edge = next(e for e in gmap["edges"]
                if e["source"] == _normalize_name(BC) and e["target"] == _normalize_name(RNC))
    assert edge["count"] == 3 and edge["suggested"] is False


def test_attach_neighbors_flags_unobserved_as_suggested() -> None:
    gmap = _map()
    rnc, tri = _normalize_name(RNC), _normalize_name(TRI)
    # RNC↔TRI is a strong neighbour pair with NO observed edge between them
    def fake(node_key: str, k: int) -> list[tuple[str, float]]:
        if node_key == rnc:
            return [(tri, 0.9)]
        if node_key == tri:
            return [(rnc, 0.9)]
        return []

    attach_neighbors(gmap, semantic=fake, structural=None, suggest_threshold=0.55)
    assert gmap["nodes"][rnc]["neighbours"][0]["node_key"] == tri
    assert any(e["suggested"] and e["source"] == rnc and e["target"] == tri
               for e in gmap["edges"])


def test_synonymish_detects_label_subset_not_siblings() -> None:
    assert _synonymish("Armbar", "Armbar Attempt")
    assert _synonymish("Triangle Choke", "Arm Triangle Choke")
    assert not _synonymish("Back Control", "Side Control")  # siblings, real transition


def test_synonym_pair_recorded_not_suggested() -> None:
    gmap = _map()
    bc = _normalize_name(BC)
    # inject a near-duplicate library-only node and force it as BC's top neighbour
    dup = "back_control_attempt"
    gmap["nodes"][dup] = {"node_key": dup, "label": "Back Control Attempt", "type": "control",
                          "pt": "", "occ": 0, "pagerank": 0.0, "betweenness": 0.0,
                          "reward_risk": 0.0, "community": None, "observed": False,
                          "neighbours": []}
    attach_neighbors(gmap, semantic=lambda nk, k: [(dup, 0.95)] if nk == bc else [],
                     structural=None)
    # high-scoring near-duplicate → synonym candidate, NOT a suggested transition edge
    assert any(c["a"] == bc or c["b"] == bc for c in gmap["synonym_candidates"])
    assert not any(e["suggested"] and {e["source"], e["target"]} == {bc, dup}
                   for e in gmap["edges"])


def test_attach_neighbors_does_not_duplicate_observed_edge() -> None:
    gmap = _map()
    bc, rnc = _normalize_name(BC), _normalize_name(RNC)
    # BC→RNC is observed; a high score must not add a second (suggested) edge
    attach_neighbors(gmap, semantic=lambda nk, k: [(rnc, 0.99)] if nk == bc else [],
                     structural=None, suggest_threshold=0.55)
    bc_rnc = [e for e in gmap["edges"] if e["source"] == bc and e["target"] == rnc]
    assert len(bc_rnc) == 1 and bc_rnc[0]["suggested"] is False
