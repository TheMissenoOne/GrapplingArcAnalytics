"""render_map_prototypes — 6 HTML prototypes + index + metrics from a user bundle.

Runs on the App's own synthetic ``mock_user_bundle.json`` (not the owner's real data)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.render_map_prototypes import _complete_two_sided, build_aggregate, render_all

_MOCK_BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "GrapplingArcApp" / "src" / "data" / "mockData" / "mock_user_bundle.json"
)

_EXPECTED_FILES = [
    "index.html", "graph.js", "metrics.json",
    "1-baseline.html", "2-migrado-proprio.html", "3-migrado-oponente-completo.html",
    "4-migrado-oponente-seletivo.html", "5-hubs.html", "6-ghost-inferidos.html",
]


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
    assert set(metrics["variants"]) == {
        "1-baseline", "2-migrado-proprio", "3-migrado-oponente-completo",
        "4-migrado-oponente-seletivo", "5-hubs", "6-ghost-inferidos",
    }
    for name, m in metrics["variants"].items():
        assert m["nodes"] > 0, name
        assert m["edges"] >= 0, name
        assert m["handover_links"] >= 0, name

    # handovers only exist where you+partner are both rendered (variants 3-6); variant 1/2
    # never bridge actors
    for name in ("1-baseline", "2-migrado-proprio"):
        assert metrics["variants"][name]["handover_links"] == 0
    for name in ("3-migrado-oponente-completo", "4-migrado-oponente-seletivo", "5-hubs",
                 "6-ghost-inferidos"):
        assert metrics["variants"][name]["handover_links"] > 0, name

    # variant 3 (full opponent) has at least as many nodes as variant 4 (selective) — the
    # selective gate only ever REMOVES partner elements
    assert metrics["variants"]["3-migrado-oponente-completo"]["nodes"] >= \
        metrics["variants"]["4-migrado-oponente-seletivo"]["nodes"]

    cir = metrics["corpus_inference_rate"]
    assert cir["states_total"] > 0
    assert cir["edges_total"] > 0
    assert 0.0 <= cir["states_inferred_pct"] <= 100.0
    assert 0.0 <= cir["edges_inferred_pct"] <= 100.0


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


def test_render_all_is_deterministic(tmp_path):
    bundle = json.loads(_MOCK_BUNDLE.read_text(encoding="utf-8"))
    out1, out2 = tmp_path / "run1", tmp_path / "run2"

    render_all(bundle, out1)
    render_all(bundle, out2)

    assert (out1 / "metrics.json").read_bytes() == (out2 / "metrics.json").read_bytes()
