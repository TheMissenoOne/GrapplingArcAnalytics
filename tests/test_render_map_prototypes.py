"""render_map_prototypes — 8 HTML prototypes + index + metrics from a user bundle.

Runs on the App's own synthetic ``mock_user_bundle.json`` (not the owner's real data)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.render_map_prototypes import (
    _GRAPH_JS,
    _complete_two_sided,
    _detect_systems,
    _icons_graphview,
    _own_graphview,
    _patch_graph_js,
    _selective_states_and_edges,
    build_aggregate,
    render_all,
)

_BUG_BUNDLE = {
    "sessions": [{"rounds": [{"entries": [
        {"label": "Closed Guard", "type": "guard", "actor": "you"},
        # real bug: action logged with a stale STATE type ("control") — must become an edge,
        # never a node, and must group with the same technique logged under a DIFFERENT
        # (English) library variant in the next round.
        {"label": "Raspagem de Gancho", "type": "control", "actor": "you"},
        {"label": "Mount", "type": "control", "actor": "you"},
    ]}, {"entries": [
        {"label": "Closed Guard", "type": "guard", "actor": "you"},
        {"label": "Hook Sweep", "type": "control", "actor": "you"},  # same technique, en variant
        {"label": "Mount", "type": "control", "actor": "you"},
    ]}]}]
}

_MOCK_BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "GrapplingArcApp" / "src" / "data" / "mockData" / "mock_user_bundle.json"
)

_EXPECTED_FILES = [
    "index.html", "graph.js", "metrics.json",
    "1-baseline.html", "2-migrado-proprio.html", "3-migrado-oponente-completo.html",
    "4-migrado-oponente-seletivo.html", "5-hubs.html", "6-ghost-inferidos.html",
    "7-icones-categoria.html", "8-sistemas-colapsavel.html",
]

_EXPECTED_VARIANT_KEYS = {
    "1-baseline", "2-migrado-proprio", "3-migrado-oponente-completo",
    "4-migrado-oponente-seletivo", "5-hubs", "6-ghost-inferidos",
    "7-icones-categoria", "8-sistemas-colapsavel",
}


def test_render_all_produces_every_artifact_with_valid_counts(tmp_path):
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    out = tmp_path / "run1"

    metrics = render_all(bundle, out)

    for name in _EXPECTED_FILES:
        assert (out / name).exists(), name

    # metrics.json parses and matches the return value
    on_disk = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert on_disk == metrics

    # every variant has a node/edge count, and the migrated ones have some content
    assert set(metrics["variants"]) == _EXPECTED_VARIANT_KEYS
    for name, m in metrics["variants"].items():
        assert m["nodes"] > 0, name
        assert m["edges"] >= 0, name
        assert m["handover_links"] >= 0, name
        assert "knobs" in m and m["knobs"]["charge"] > 0, name

    # handovers only exist where you+partner are both rendered (variants 3-8); variant 1/2
    # never bridge actors
    for name in ("1-baseline", "2-migrado-proprio"):
        assert metrics["variants"][name]["handover_links"] == 0
    for name in ("3-migrado-oponente-completo", "4-migrado-oponente-seletivo", "5-hubs",
                 "6-ghost-inferidos", "7-icones-categoria"):
        assert metrics["variants"][name]["handover_links"] > 0, name

    # variant 3 (full opponent) has at least as many nodes as variant 4 (selective) — the
    # selective gate only ever REMOVES partner elements
    assert metrics["variants"]["3-migrado-oponente-completo"]["nodes"] >= \
        metrics["variants"]["4-migrado-oponente-seletivo"]["nodes"]

    # variant 8's global level: at least one system, and it says so in its own metrics
    assert metrics["variants"]["8-sistemas-colapsavel"]["systems"] >= 1

    cir = metrics["corpus_inference_rate"]
    assert cir["states_total"] > 0
    assert cir["edges_total"] > 0
    assert 0.0 <= cir["states_inferred_pct"] <= 100.0
    assert 0.0 <= cir["edges_inferred_pct"] <= 100.0

    oc = metrics["orientation_counts"]
    assert set(oc) == {"top", "bottom", "neutral"}
    assert sum(oc.values()) > 0
    # counted over UNIQUE (node_key, actor) states, so never more than the deduped state count
    assert sum(oc.values()) <= cir["states_total"]


def test_partner_and_you_nodes_are_never_merged_and_handovers_bridge_them():
    """The owner's correction: you and partner are fundamentally different, never one node
    just because they share a node_key. Each is its own qualified id; the only thing that
    connects the two subgraphs is a handover link."""
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    gv, _edges, handovers = _complete_two_sided(agg)

    ids = [n["id"] for n in gv["nodes"]]
    assert len(ids) == len(set(ids))  # every (node_key, actor) is its own node, never merged

    you_ids = {n["id"] for n in gv["nodes"] if n["fighter"] == "a"}
    opp_ids = {n["id"] for n in gv["nodes"] if n["fighter"] == "b"}
    assert you_ids.isdisjoint(opp_ids)
    assert opp_ids and all(i.startswith("opp:") for i in opp_ids)
    assert you_ids and not any(i.startswith("opp:") for i in you_ids)
    assert not any(n.get("fighter") == "x" for n in gv["nodes"])  # 'x' never on a NODE

    assert handovers, "mock bundle switches actor mid-round, expect at least one handover"
    all_ids = set(ids)
    for h in handovers:
        assert h["from"] in all_ids and h["to"] in all_ids  # bridges real rendered nodes
    handover_links = [link for link in gv["links"] if link.get("fighter") == "x"]
    assert handover_links and all(link["dashed"] for link in handover_links)


def test_action_logged_with_stale_state_type_becomes_an_edge_not_a_node():
    """Root-cause regression: a logged action ('Raspagem de Gancho') carrying a stale
    `type: 'control'` snapshot must compile as an EDGE (kind_of_entry resolves the library's
    real `sweep` type), never a state node — and the same technique logged under a different
    library variant ('Hook Sweep') in another round must group into the SAME edge, not a
    second one, while the RENDERED label keeps the owner's own first-seen wording."""
    agg = build_aggregate(_BUG_BUNDLE)

    node_labels = {v["label"] for v in agg.states.values()}
    assert "Raspagem de Gancho" not in node_labels
    assert "Hook Sweep" not in node_labels
    assert {"Closed Guard", "Mount"} <= node_labels

    sweep_edges = [v for v in agg.edges.values() if v["action_label"] in
                   ("Raspagem de Gancho", "Hook Sweep")]
    assert len(sweep_edges) == 1, agg.edges  # both rounds grouped into ONE action edge
    assert sweep_edges[0]["count"] == 2
    assert sweep_edges[0]["action_label"] == "Raspagem de Gancho"  # first-seen display wording


def test_render_all_is_deterministic(tmp_path):
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    out1, out2 = tmp_path / "run1", tmp_path / "run2"

    render_all(bundle, out1)
    render_all(bundle, out2)

    assert (out1 / "metrics.json").read_bytes() == (out2 / "metrics.json").read_bytes()
    assert (out1 / "8-sistemas-colapsavel.html").read_bytes() == \
        (out2 / "8-sistemas-colapsavel.html").read_bytes()


def test_graph_js_patch_applies_and_never_touches_the_site_original(tmp_path):
    before = _GRAPH_JS.read_text(encoding="utf-8")

    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    out = tmp_path / "run"
    render_all(bundle, out)

    after = _GRAPH_JS.read_text(encoding="utf-8")
    assert after == before  # site/graph.js is NEVER written by this module

    copy = (out / "graph.js").read_text(encoding="utf-8")
    assert copy != before  # the OUTPUT copy IS patched
    for marker in (
        "map-prototype patch: force-visible labels",
        "map-prototype patch: edge label",
        "map-prototype patch: actor border ring",
        "map-prototype patch: category/finish glyph",
    ):
        assert marker in copy, marker


def test_patch_graph_js_errors_loudly_if_an_anchor_is_missing():
    with pytest.raises(ValueError, match="patch anchor not found"):
        _patch_graph_js("var totally = 'different file contents';")


def test_variant7_nodes_carry_a_category_icon():
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    states4, edges4, handovers4 = _selective_states_and_edges(agg)
    gv7 = _icons_graphview(states4, edges4, handovers4)

    assert gv7["nodes"]
    for n in gv7["nodes"]:
        assert "icon" in n and n["icon"] != "", n
        assert "ring" in n and n["ring"], n
        assert "color" in n and n["color"], n


def test_variant8_embeds_multiple_systems_deterministically():
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    states4, edges4, handovers4 = _selective_states_and_edges(agg)

    run1 = _detect_systems(states4, edges4, handovers4)
    run2 = _detect_systems(states4, edges4, handovers4)

    assert run1 == run2  # deterministic (cicatriz #10)
    assert len(run1["systems"]) >= 2, run1["systems"]
    # every node belongs to exactly one system, and every system knows its own hub
    all_members = [q for s in run1["systems"] for q in s["members"]]
    assert len(all_members) == len(set(all_members))
    for s in run1["systems"]:
        assert s["hub_qid"] in s["members"]
        assert s["actor"] in ("you", "partner")


def test_edges_carry_label_iff_not_inferred_handovers_never_labelled():
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    gv2, edges2 = _own_graphview(agg)

    action_links = [link for link in gv2["links"]]
    assert len(action_links) == len(edges2)
    for link, edge in zip(action_links, edges2):
        if edge["inferred"]:
            assert "label" not in link, edge
        else:
            assert link.get("label") == edge["action_label"], edge

    # handovers (variant 3+) are never labelled, dashed only
    _, edges3, handovers3 = _complete_two_sided(agg)
    from scripts.render_map_prototypes import _two_sided_graphview
    gv3 = _two_sided_graphview(agg.states, edges3, handovers3)
    handover_links = [link for link in gv3["links"] if link.get("fighter") == "x"]
    assert handover_links
    for link in handover_links:
        assert "label" not in link
