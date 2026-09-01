"""``analysis.corpus_paths`` — the public corpus's "edge = path" payload.

The four things a site payload has to be right about, and nothing else (the bundling itself is
already proved in ``tests/test_path_bundling.py``, the metrics in ``tests/test_path_metrics.py``):

1. it draws no route the data never walked (``walkable_routes`` == the input set);
2. it is deterministic — the site bundle is committed, so two runs must agree byte for byte;
3. it can never reach private data;
4. the shapes the renderer contracts on are actually there (``pathIds``, ``actions[]``, pinned
   coordinates, the anchor frame).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from analysis.corpus_paths import aggregate_bouts, path_payload, render_paths
from analysis.path_bundling import bundle_paths

# One bout with a real shape: A opens on the ground and finishes; B passes and controls. Two
# of A's chains share a prefix, which is what gives the bundler something to fuse.
BOUT_A: list[dict[str, Any]] = [
    {"label": "Closed Guard", "type": "guard", "side": "a", "ts": 10},
    {"label": "Armbar", "type": "submission", "side": "a", "successful": False, "ts": 20},
    {"label": "Triangle Choke", "type": "submission", "side": "a", "successful": True, "ts": 31},
    {"label": "Knee Cut", "type": "pass", "side": "b", "successful": True, "ts": 40},
    {"label": "Side Control", "type": "control", "side": "b", "ts": 44},
]
BOUT_B: list[dict[str, Any]] = [
    {"label": "Closed Guard", "type": "guard", "side": "a"},
    {"label": "Armbar", "type": "submission", "side": "a", "successful": False},
    {"label": "Omoplata", "type": "submission", "side": "a", "successful": True},
    {"label": "Mount", "type": "control", "side": "a"},
]


def test_no_phantom_route_over_a_corpus_shaped_aggregate() -> None:
    """The picture must license exactly the walks the corpus actually contains — the strong
    form of the proof (``BundledGraph.walkable_routes``), run on what the exporter really
    feeds the bundler rather than on a hand-made fixture."""
    agg = aggregate_bouts([BOUT_A, BOUT_B, BOUT_A])
    paths = render_paths(agg)
    bundled = bundle_paths(paths)
    expected = {(p.source, p.actions, p.target) for p in paths}
    assert bundled.walkable_routes() == expected


def test_payload_is_deterministic_across_runs_and_input_order() -> None:
    """The site bundle is generated and COMMITTED, so a rerun that changes bytes turns every
    regeneration into a spurious diff. Input order must not matter either: the export loops
    over an unordered ``select(Match)`` (``site-export-perf-campaign``, the export's own known
    non-determinism)."""
    one = path_payload(aggregate_bouts([BOUT_A, BOUT_B]))
    two = path_payload(aggregate_bouts([BOUT_A, BOUT_B]))
    flipped = path_payload(aggregate_bouts([BOUT_B, BOUT_A]))
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert json.dumps(one, sort_keys=True) == json.dumps(flipped, sort_keys=True)


def test_corpus_paths_cannot_reach_the_database_at_all() -> None:
    """PRIVACY, structurally (root CLAUDE.md). A public artefact must filter ``owner_kind``;
    this module goes further and cannot query anything — it takes plain event dicts. Asserted
    on the import graph rather than in prose, so a future ``from db.models import Graph`` fails
    here instead of quietly putting a user's graph on the public site."""
    src = Path(__file__).resolve().parents[1] / "analysis" / "corpus_paths.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    offenders = {m for m in modules if m == "db" or m.startswith("db.")}
    assert not offenders, f"corpus_paths must never touch the DB layer: {offenders}"
    assert not any(m.startswith("schemas.app_types") for m in modules), (
        "corpus_paths must never read an App user bundle — that is private data"
    )


def test_site_path_graph_builders_read_only_public_match_sequences() -> None:
    """The two export-side adapters take a ``Match`` and read ``sequence`` + the two athlete
    ids. Nothing else — in particular no ``graphs`` row, which is the only table where a
    private (``owner_kind='user'``) record lives."""
    src = Path(__file__).resolve().parents[1] / "export" / "site_data.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    wanted = {"_corpus_bouts", "_athlete_path_graph"}
    found = {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name in wanted
    }
    assert found.keys() == wanted, f"missing site path-graph builders: {wanted - found.keys()}"
    for name, fn in found.items():
        attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        assert "sequence" in attrs, f"{name} should read Match.sequence"
        assert not (attrs & {"owner_kind", "owner_id", "graph_id"}), (
            f"{name} touches a graph-ownership field — public artefacts derive from matches"
        )


def test_payload_carries_what_the_renderer_contracts_on() -> None:
    payload = path_payload(aggregate_bouts([BOUT_A, BOUT_B]))
    assert set(payload) == {"nodes", "links", "paths", "stats"}

    ids = {n["id"] for n in payload["nodes"]}
    for node in payload["nodes"]:
        # every point is POSITIONED in Python — site/graph.js's mountPaths never simulates
        assert isinstance(node["x"], int | float) and isinstance(node["y"], int | float)
        assert node["pin"] is True
        assert node["kind"] in {"state", "anchor", "branch", "merge", "branch-merge"}

    path_ids = {p["id"] for p in payload["paths"]}
    for link in payload["links"]:
        assert link["from"] in ids and link["to"] in ids
        assert link["pathIds"], "a stroke with no occurrence would be a drawn claim with no data"
        assert set(link["pathIds"]) <= path_ids
        assert link["actions"], "the multi-label field is the whole point of the new payload"
        for action in link["actions"]:
            assert set(action) >= {"key", "label", "inferred"}
        assert link["label"] == " → ".join(a["label"] for a in link["actions"])

    # the frame: both athletes' chains end somewhere nameable, and the anchors are the vertices
    anchors = {n["label"] for n in payload["nodes"] if n["kind"] == "anchor"}
    assert anchors <= {"Top", "Bottom", "Neutral", "Finish", "Finish (opponent)"}, anchors
    assert "Finish" in anchors, "A submitted her opponent — the finish anchor must be drawn"


def test_video_timestamps_ride_the_actions_not_the_states() -> None:
    """A breakdown seeks the bout video from the map. In this model the ACTION is what happened
    at a moment, so the timestamp belongs to the stroke, not to the position."""
    payload = path_payload(aggregate_bouts([BOUT_A]))
    seek = {lk["ts"] for lk in payload["links"] if lk.get("ts") is not None}
    assert seek, "no stroke carried a timestamp — the breakdown's video seek is dead"
    assert seek <= {10, 20, 31, 40, 44}


def test_collapsing_actors_folds_both_athletes_onto_one_technique_space() -> None:
    """The Ocean is the corpus's technique space: A's side control and B's side control are the
    same corpus fact. A bout map is not — there the two are different states."""
    bout = path_payload(aggregate_bouts([BOUT_A]))
    ocean = path_payload(aggregate_bouts([BOUT_A], collapse_actors=True))
    assert any(n["id"].startswith("s:opp:") for n in bout["nodes"])
    assert not any(n["id"].startswith("s:opp:") for n in ocean["nodes"])
    assert {n["fighter"] for n in ocean["nodes"] if n.get("fighter")} == {"a"}


def test_min_count_gate_keeps_ids_stable_and_only_drops_rare_paths() -> None:
    """The Ocean's only gate. It must be a pure filter — a gated payload's paths keep the ids
    an ungated one gave them, or two exports of the same corpus disagree about what ``p7`` is."""
    agg = aggregate_bouts([BOUT_A, BOUT_A, BOUT_B])
    every = {p["id"]: p for p in path_payload(agg)["paths"]}
    gated = {p["id"]: p for p in path_payload(agg, min_count=2)["paths"]}
    assert gated.keys() < every.keys()
    for pid, row in gated.items():
        assert row["count"] >= 2
        assert row["actions"] == every[pid]["actions"]


def test_anchor_labels_are_english_not_the_app_locale() -> None:
    """``data/taxonomy/inference_table.json`` names the generics in pt-BR (the App's locale).
    Site copy is English (GrapplingArc AGENTS.md rule 4) and the label has to follow the
    node_key AFTER the perspective mirror, not the compiled state's own label."""
    payload = path_payload(aggregate_bouts([BOUT_A, BOUT_B]))
    text = json.dumps(payload, ensure_ascii=False)
    for pt in ("Por Cima", "Por Baixo", "Finalização", "Transição", "Raspagem", "Passagem"):
        assert pt not in text, f"pt-BR label {pt!r} leaked into the English site payload"
