"""Tests for the aggregate transition-network engine (pure, off fixture sequences)."""

from __future__ import annotations

from typing import Any

from analysis.network_metrics import (
    detect_communities,
    network_from_sequences,
    pagerank_ranking,
    reward_risk_ranking,
    route_to_submission,
)


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


# Canonical library labels (stay unchanged through clean_label).
BC, RNC, CG, TRI = "Back Control", "Rear Naked Choke", "Closed Guard", "Triangle Choke"


def _sequences() -> list[list[dict[str, Any]]]:
    back_to_sub = [_e(BC, "control", "A"), _e(RNC, "submission", "A", True)]
    return [
        back_to_sub, back_to_sub, back_to_sub,
        [_e(CG, "guard", "B"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(CG, "guard", "B"), _e(TRI, "submission", "B", True)],
    ]


def test_network_shape_and_node_attrs() -> None:
    g = network_from_sequences(_sequences())
    assert {BC, RNC, CG, TRI} <= set(g.nodes)
    assert g.nodes[RNC]["type"] == "submission"
    assert g.nodes[BC]["occ"] == 4  # 3 + 1
    assert g[BC][RNC]["weight"] == 4  # back control → rnc four times


def test_reward_risk_positive_for_back_control() -> None:
    g = network_from_sequences(_sequences())
    # Back Control transitions directly into a same-actor finished submission → positive.
    assert g.nodes[BC]["reward_risk"] > 0
    ranked = reward_risk_ranking(g, min_occ=1, limit=10)
    assert ranked[0][0] in {BC, CG}  # the back-take / guard that lead to finishes top the list


def test_pagerank_ranks_the_finish_hub() -> None:
    g = network_from_sequences(_sequences())
    top = [n for n, _ in pagerank_ranking(g, 5)]
    # The submission everyone funnels into should be a top hub.
    assert RNC in top[:3]


def test_route_to_submission_reaches_a_finish() -> None:
    g = network_from_sequences(_sequences())
    path = route_to_submission(g, BC)
    assert path[0] == BC and path[-1] == RNC
    assert g.nodes[path[-1]]["type"] == "submission"


def test_communities_partition_the_graph() -> None:
    comms = detect_communities(network_from_sequences(_sequences()), min_occ=1)
    members = {n for c in comms for n in c}
    assert members == {BC, RNC, CG, TRI}
    assert len(comms) >= 1
