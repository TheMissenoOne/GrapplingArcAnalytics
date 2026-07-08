"""Tests for the aggregate transition-network engine (pure, off fixture sequences)."""

from __future__ import annotations

from typing import Any

from analysis.network_metrics import (
    corpus_success_threshold,
    detect_communities,
    edge_arrow,
    network_from_sequences,
    pagerank_ranking,
    reward_risk_ranking,
    route_to_submission,
    success_threshold,
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


def test_edge_ok_counts_target_successes() -> None:
    # BC -> RNC happens 4x, all landing on a *successful* RNC (ok=True in the fixture).
    g = network_from_sequences(_sequences())
    assert g[BC][RNC]["ok"] == 4


def test_edge_ok_only_counts_the_target_event() -> None:
    # A miss on the source shouldn't matter — only whether the TARGET event succeeded.
    seq = [
        [{"label": "Closed Guard", "type": "guard", "actor_id": "A", "successful": False},
         {"label": "Triangle Choke", "type": "submission", "actor_id": "A", "successful": True}],
    ]
    g = network_from_sequences(seq)
    assert g["Closed Guard"]["Triangle Choke"]["weight"] == 1
    assert g["Closed Guard"]["Triangle Choke"]["ok"] == 1


def test_edge_arrow_rules() -> None:
    assert edge_arrow(1, 0) is False          # below min_edge → undirected
    assert edge_arrow(10, 4) is False          # 4 >= 0.34*10 → genuine two-way, no arrow
    assert edge_arrow(10, 2) is True           # 2 < 0.34*10 → one direction dominates
    assert edge_arrow(0, 0) is False


def test_success_threshold_25th_percentile_gated_by_weight_and_type() -> None:
    edges = [
        (5, 1, "submission"),   # success 0.2 — qualifies
        (5, 3, "submission"),   # success 0.6 — qualifies
        (5, 5, "submission"),   # success 1.0 — qualifies
        (5, 5, "control"),      # not a gated target type — excluded
        (2, 0, "submission"),   # below weight floor — excluded
    ]
    thresh = success_threshold(edges, q=0.25, min_n=3)
    assert thresh is not None
    assert 0.19 < thresh < 0.4  # 25th pct of [0.2, 0.6, 1.0]


def test_success_threshold_none_when_too_few_qualify() -> None:
    assert success_threshold([(5, 1, "submission")], min_n=3) is None


def test_corpus_success_threshold_reads_off_the_graph() -> None:
    g = network_from_sequences(_sequences())
    # only 2 distinct submission-targeted edges in the fixture (BC->RNC, CG->TRI) — below
    # min_n=3, so nothing qualifies for a corpus-wide threshold yet.
    assert corpus_success_threshold(g) is None
