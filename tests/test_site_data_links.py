"""Tests for the public-site graph-link direction/dash helpers (pure, no DB)."""

from __future__ import annotations

from typing import Any

from analysis.network_metrics import network_from_sequences
from export.site_data import _direct_career_links, _to_graphview


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


def test_to_graphview_breakdown_links_are_always_a_plain_arrow() -> None:
    app_graph = {
        "nodes": [{"id": "a", "data": {"type": "guard"}}, {"id": "b", "data": {"type": "pass"}}],
        "edges": [{"source": "a", "target": "b", "data": {"count": 1}}],
    }
    gv = _to_graphview(app_graph)
    assert gv["links"][0]["arrow"] is True and gv["links"][0]["dashed"] is False


def test_direct_career_links_collapses_pair_and_orients_by_net_weight() -> None:
    net = network_from_sequences([
        [_e("Back Control", "control", "A"), _e("Rear Naked Choke", "submission", "A", True)],
        [_e("Back Control", "control", "A"), _e("Rear Naked Choke", "submission", "A", True)],
        [_e("Back Control", "control", "A"), _e("Rear Naked Choke", "submission", "A", True)],
        [_e("Rear Naked Choke", "submission", "A", True), _e("Back Control", "control", "A")],
    ])
    links = [
        {"from": "back control", "to": "rear naked choke", "fighter": "a", "weight": 1},
        {"from": "rear naked choke", "to": "back control", "fighter": "a", "weight": 1},
    ]
    node_type = {"back control": "control", "rear naked choke": "submission"}
    out = _direct_career_links(links, node_type, net, success_thresh=None)
    assert len(out) == 1  # reciprocal pair collapsed to one link
    lk = out[0]
    assert lk["from"] == "back control" and lk["to"] == "rear naked choke"  # 3 > 1, majority wins
    assert lk["arrow"] is True  # 1 < 0.34*3 → one direction dominates
    assert lk["weight"] == 3  # real net weight, not the placeholder 1 the caller passed in


def test_direct_career_links_dashes_below_threshold() -> None:
    net = network_from_sequences([
        [_e("Closed Guard", "guard", "A"), _e("Armbar", "submission", "A", False)],
        [_e("Closed Guard", "guard", "A"), _e("Armbar", "submission", "A", False)],
        [_e("Closed Guard", "guard", "A"), _e("Armbar", "submission", "A", True)],
    ])
    links = [{"from": "closed guard", "to": "armbar", "fighter": "a", "weight": 1}]
    node_type = {"closed guard": "guard", "armbar": "submission"}
    out = _direct_career_links(links, node_type, net, success_thresh=0.5)
    assert out[0]["dashed"] is True  # 1/3 success < 0.5 threshold, target is a gated type

    out_ok = _direct_career_links(links, node_type, net, success_thresh=0.2)
    assert out_ok[0]["dashed"] is False  # 1/3 success >= 0.2 threshold
