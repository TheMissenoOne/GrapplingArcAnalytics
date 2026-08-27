"""render_map_prototypes — 8 HTML prototypes + index + metrics from a user bundle.

Runs on the App's own synthetic ``mock_user_bundle.json`` (not the owner's real data)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.render_map_prototypes import (
    _BRIDGE_COLOR,
    _FIG_HEX,
    _FINISH_COLOR,
    _FINISH_ICON,
    _FINISH_KEY,
    _GRAPH_JS,
    _START_COLOR,
    _SYSTEM_COLOR,
    _actor_for,
    _complete_two_sided,
    _cross_member_links,
    _cross_system_links,
    _detect_systems,
    _excluded_states,
    _icons_graphview,
    _index_parallel_links,
    _own_graphview,
    _patch_graph_js,
    _place_of,
    _qid,
    _selective_states_and_edges,
    _system_members,
    _systems_level1_view,
    _two_sided_graphview,
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
    "7-icones-categoria.html", "8-sistemas-colapsavel.html", "9-sistemas-expande-in-place.html",
]

_EXPECTED_VARIANT_KEYS = {
    "1-baseline", "2-migrado-proprio", "3-migrado-oponente-completo",
    "4-migrado-oponente-seletivo", "5-hubs", "6-ghost-inferidos",
    "7-icones-categoria", "8-sistemas-colapsavel", "9-sistemas-expande-in-place",
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

    # variant 9 mirrors variant 8's metrics shape (same systems, same collapsed-view counts)
    assert metrics["variants"]["9-sistemas-expande-in-place"]["systems"] >= 1
    assert metrics["variants"]["9-sistemas-expande-in-place"]["nodes"] == \
        metrics["variants"]["8-sistemas-colapsavel"]["nodes"]

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
    assert (out1 / "9-sistemas-expande-in-place.html").read_bytes() == \
        (out2 / "9-sistemas-expande-in-place.html").read_bytes()


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
        "map-prototype patch: label collision",
        "map-prototype patch: parallel edges",
        "map-prototype patch: pinned anchor node",
        "map-prototype patch: snapshot live x/y",
        "map-prototype patch: system regions",
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
        assert "color" in n and n["color"], n
        # D: start-anchor nodes are the one exception — no actor ring (never actor-specific)
        if n["color"] != _START_COLOR:
            assert "ring" in n and n["ring"], n


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
    gv3 = _two_sided_graphview(agg.states, edges3, handovers3)
    handover_links = [link for link in gv3["links"] if link.get("fighter") == "x"]
    assert handover_links
    for link in handover_links:
        assert "label" not in link


def test_finish_node_has_glyph_only_in_variant7_color_elsewhere():
    """Owner correction: emoji/glyph belongs to variant 7 (icons) only — every other migrated
    variant keeps the finish node's highlighted colour but no glyph."""
    row = {"node_key": _FINISH_KEY, "label": "Finalizacao", "type": "submission",
           "actor": "you", "count": 1, "inferred": True}
    states = {(_FINISH_KEY, "you"): row}

    gv = _two_sided_graphview(states, [], [])
    n = gv["nodes"][0]
    assert n["color"] == _FINISH_COLOR
    assert "icon" not in n

    gv7 = _icons_graphview(states, [], [])
    n7 = gv7["nodes"][0]
    assert n7["color"] == _FINISH_COLOR
    assert n7["icon"] == _FINISH_ICON


def test_finish_states_are_qualified_per_actor_two_distinct_nodes():
    """Adendo: finish is a state like any other w.r.t. actor — a bundle where BOTH you and the
    opponent close their own chain on a submission must produce two SEPARATE finish nodes
    (`finish` / `opp:finish`), never merged into one, and they must stay visually
    distinguishable (shared highlight colour, different actor ring, opponent label suffixed)."""
    bundle = {"sessions": [{"rounds": [{"entries": [
        {"label": "Closed Guard", "type": "guard", "actor": "you"},
        {"label": "Triangle", "type": "submission", "actor": "you"},
        {"label": "Mount", "type": "control", "actor": "partner"},
        {"label": "Rear Naked Choke", "type": "submission", "actor": "partner"},
    ]}]}]}
    agg = build_aggregate(bundle)
    assert (_FINISH_KEY, "you") in agg.states
    assert (_FINISH_KEY, "partner") in agg.states

    gv, _edges, _handovers = _complete_two_sided(agg)
    opp_id = f"opp:{_FINISH_KEY}"
    finish_nodes = {n["id"]: n for n in gv["nodes"] if n["id"] in (_FINISH_KEY, opp_id)}
    assert set(finish_nodes) == {_FINISH_KEY, opp_id}  # two nodes, never merged into one

    you_finish, opp_finish = finish_nodes[_FINISH_KEY], finish_nodes[opp_id]
    assert you_finish["color"] == opp_finish["color"] == _FINISH_COLOR
    assert you_finish["ring"] == _FIG_HEX["a"]
    assert opp_finish["ring"] == _FIG_HEX["b"]
    assert you_finish["ring"] != opp_finish["ring"]
    assert "(oponente)" in opp_finish["label"]
    assert "(oponente)" not in you_finish["label"]


def test_variant9_present_with_multi_expand_and_reconnect_data():
    """9 mirrors 8's systems but keeps everything in ONE view — assert the embedded payload can
    support multi-expand + inter-system reconnection: every cross-placement link's `from`/`to`
    are REAL qids (never a collapsed `sys:*` id, C: sistema-ponte-sistema — a crossing pair's
    endpoints are placement ids, at least one of which is a bridge/individual, never two plain
    system members), so the client can re-resolve them per the current expand state, and every
    system's member slice really is a subset of the selective states."""
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    states4, edges4, handovers4 = _selective_states_and_edges(agg)
    detected = _detect_systems(states4, edges4, handovers4)
    system_of = detected["system_of"]
    excluded = _excluded_states(states4, system_of)
    place_of = _place_of(system_of, excluded)

    cross = _cross_member_links(place_of, edges4, handovers4)
    assert cross, "mock bundle's selective set should cross at least one placement pair"
    real_qids = set(place_of)
    for c in cross:
        assert c["from"] in real_qids and c["to"] in real_qids  # real members, never 'sys:*'
        assert c["fromSys"] != c["toSys"]
        # C: sistema-ponte-sistema — a crossing pair is never two DIFFERENT plain system ids;
        # at least one side is a bridge/individual (its placement id == its own real qid)
        assert not (c["fromSys"].startswith("sys:") and c["toSys"].startswith("sys:"))

    for s in detected["systems"]:
        sub_states, _edges, _handovers = _system_members(s, states4, edges4, handovers4)
        assert sub_states  # every system has at least its hub
        assert set(sub_states) <= set(states4)


def test_community_detection_excludes_opponent_finish_and_start_nodes():
    """C.3/C.4: the opponent never gets a system (never a member, never a hub), and finish/
    start-anchor nodes are global landmarks, never grouped either."""
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    states4, edges4, handovers4 = _selective_states_and_edges(agg)
    detected = _detect_systems(states4, edges4, handovers4)

    for s in detected["systems"]:
        assert s["actor"] == "you"
        for qid in s["members"]:
            assert not qid.startswith("opp:")
            assert qid != _FINISH_KEY

    excluded = _excluded_states(states4, detected["system_of"])
    assert any(k[1] == "partner" for k in excluded), "mock bundle has opponent states to exclude"


def test_bridge_nodes_are_memberless_and_individually_rendered():
    """C (owner whiteboard): a node whose neighbours span >=2 systems is a bridge — pulled out
    of every community, never a member, always in the 'excluded'/individually-rendered bucket."""
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    states4, edges4, handovers4 = _selective_states_and_edges(agg)
    detected = _detect_systems(states4, edges4, handovers4)
    bridge_qids = set(detected["bridge_qids"])

    all_members = {qid for s in detected["systems"] for qid in s["members"]}
    assert bridge_qids.isdisjoint(all_members)  # never a member of any system

    excluded = _excluded_states(states4, detected["system_of"])
    excluded_qids = {_qid(k[1], k[0]) for k in excluded}
    assert bridge_qids <= excluded_qids  # every bridge lands in the individually-rendered bucket


def test_index_parallel_links_assigns_par_and_par_count_per_unordered_pair():
    """B (owner-reported bug): N links between the SAME two nodes get a stable par/parCount so
    the client can fan them into separate arcs instead of one overlapping line."""
    links = [
        {"from": "a", "to": "b", "weight": 1},
        {"from": "a", "to": "b", "weight": 2},
        {"from": "b", "to": "a", "weight": 3},  # reverse direction, SAME unordered pair
        {"from": "a", "to": "c", "weight": 1},  # different pair, untouched
    ]
    _index_parallel_links(links)
    ab = [link for link in links if {link["from"], link["to"]} == {"a", "b"}]
    assert {link["par"] for link in ab} == {0, 1, 2}
    assert all(link["parCount"] == 3 for link in ab)
    ac = links[3]
    assert "par" not in ac and "parCount" not in ac  # single link, untouched


def test_start_role_node_always_qualifies_as_user_side():
    """D — the other builder's ``inference_table.json`` now carries ``start neutral``/``start
    top``/``start bottom`` tagged ``role: 'start'``: always the user's, even reached from the
    opponent's own chain, and ``_qid`` never prefixes one ``opp:``."""
    assert _actor_for("start neutral", "partner") == "you"
    assert _qid("partner", "start top") == "start top"  # never opp:start top
    assert _qid("partner", "mount") == "opp:mount"  # untouched for a normal node


def test_finish_and_start_anchors_are_pinned_at_a_fixed_axis():
    """D addendum (owner, 2026-08-27): start nodes pin on a vertical axis (top/neutral/bottom,
    per the table's own curated ``orientation`` — the ``role`` value itself is just ``'start'``
    for all three, the node_key tells them apart), finish pins horizontally (you=right,
    opponent=left) — world coords, never simulated (`n.pin`)."""
    row = {"node_key": "x", "label": "x", "type": "control", "actor": "you",
           "count": 1, "inferred": True}
    states = {
        ("start neutral", "you"): {**row, "node_key": "start neutral"},
        ("start top", "you"): {**row, "node_key": "start top"},
        ("start bottom", "you"): {**row, "node_key": "start bottom"},
        (_FINISH_KEY, "you"): {**row, "node_key": _FINISH_KEY},
        (_FINISH_KEY, "partner"): {**row, "node_key": _FINISH_KEY, "actor": "partner"},
    }
    gv = _two_sided_graphview(states, [], [])
    by_id = {n["id"]: n for n in gv["nodes"]}

    assert by_id["start neutral"]["pin"] is True
    assert by_id["start neutral"]["x"] == 0.0 and by_id["start neutral"]["y"] == 0.0
    assert by_id["start top"]["pin"] is True and by_id["start top"]["y"] < 0  # top
    assert by_id["start bottom"]["pin"] is True and by_id["start bottom"]["y"] > 0  # bottom
    assert by_id[_FINISH_KEY]["pin"] is True and by_id[_FINISH_KEY]["x"] > 0  # you = right
    opp_finish = by_id["opp:" + _FINISH_KEY]
    assert opp_finish["pin"] is True and opp_finish["x"] < 0  # opponent = left
    for anchor_id in ("start neutral", "start top", "start bottom"):
        assert by_id[anchor_id]["color"] == _START_COLOR
        assert "ring" not in by_id[anchor_id]  # D: no actor ring on an anchor node


def test_system_node_has_its_own_distinct_visual_treatment():
    """Owner addendum 2026-08-27: a collapsed system must look like an aggregate, not an
    ordinary node — own colour, member count in the label, top label-collision priority."""
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    states4, edges4, handovers4 = _selective_states_and_edges(agg)
    detected = _detect_systems(states4, edges4, handovers4)
    assert detected["systems"], "mock bundle should produce at least one system"

    excluded = _excluded_states(states4, detected["system_of"])
    place_of = _place_of(detected["system_of"], excluded)
    cross_links = _cross_system_links(place_of, edges4, handovers4)
    gv = _systems_level1_view(
        detected["systems"], excluded, cross_links, frozenset(detected["bridge_qids"]), 500.0)
    sys_nodes = [n for n in gv["nodes"] if n["id"].startswith("sys:")]
    assert sys_nodes
    for n in sys_nodes:
        assert n["color"] == _SYSTEM_COLOR
        assert n.get("system") is True
        assert " · " in n["label"]  # member count baked into the label
        assert n["color"] != _BRIDGE_COLOR != _START_COLOR
