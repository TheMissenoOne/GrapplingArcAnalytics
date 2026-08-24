"""Tests for the aggregate transition-network engine (pure, off fixture sequences)."""

from __future__ import annotations

from typing import Any

from analysis.network_metrics import (
    detect_communities,
    edge_arrow,
    edge_dashed,
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


def test_edge_dashed_fixed_rule() -> None:
    # dash iff weight >= 5 AND target type gated AND success < 0.40
    assert edge_dashed(5, 1, "submission") is True    # 0.2 < 0.40, w>=5, gated
    assert edge_dashed(5, 2, "submission") is False   # 0.4 not < 0.40
    assert edge_dashed(4, 0, "submission") is False   # below weight floor
    assert edge_dashed(8, 0, "submission") is True    # never lands, high volume
    assert edge_dashed(8, 1, "control") is False      # target type not gated
    assert edge_dashed(0, 0, "submission") is False   # weight 0 guard


def test_reward_risk_ci_uses_the_successor_denominator() -> None:
    """The interval must describe the SAME population as the point estimate.

    `reward`/`risk` are counted only over appearances WITH a successor
    (`build_graph` excludes terminal appearances from `denom`), so the Beta
    trials must be `denom`, not `occ`. Until 2026-08 this function used `occ`:
    on the node below (5 appearances, 2 with a successor, 2 rewards) the
    "5-trial" interval was centred at (2+1)/(5+2)=0.43 instead of the
    point-population (2+1)/(2+2)=0.75. Found by an external PoC review.
    """
    from analysis.network_metrics import reward_risk_with_ci

    # 'Mount' appears 5 times: twice followed by an own finished submission,
    # three times terminal (round ended there) -> occ=5, denom=2, reward=2.
    mount = "Mount"
    seqs = [
        [_e(mount, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(mount, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(BC, "control", "A"), _e(mount, "control", "A")],
        [_e(BC, "control", "A"), _e(mount, "control", "A")],
        [_e(BC, "control", "A"), _e(mount, "control", "A")],
    ]
    g = network_from_sequences(seqs)
    assert g.nodes[mount]["occ"] == 5
    assert g.nodes[mount]["denom"] == 2
    assert g.nodes[mount]["reward"] == 2

    rows = {r[0]: r for r in reward_risk_with_ci(g, min_occ=1, limit=20)}
    label, point, lo, hi, occ, denom = rows[mount]
    assert (occ, denom) == (5, 2)
    # Beta(2+1, 2-2+1) posterior mean = 0.75; the old occ-based trials gave 3/7.
    assert abs(point - ((2 + 1) / (2 + 2) - (0 + 1) / (2 + 2))) < 1e-9
    # and the interval is over 2 trials -> properly wide
    assert hi - lo > 0.5

    # gating is on denom: with min_occ=3 Mount (denom=2) must be excluded even
    # though occ=5 clears it
    gated_rows = {r[0] for r in reward_risk_with_ci(g, min_occ=3, limit=20)}
    assert mount not in gated_rows
