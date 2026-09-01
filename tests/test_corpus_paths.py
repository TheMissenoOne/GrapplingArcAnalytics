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

from analysis import corpus_paths
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
    # §13.7: `unresolved` is new and additive — family-only context (support/traffic), never a
    # variant's own count or rating (P5, tests/test_actions_parity.py). §5d: `folded` is new and
    # additive too — always present, empty here because two tiny bouts never cross the budget.
    # §17: `layout` names the frame the positions were computed in ("flow" here; the ring adds
    # `rings`/`ringCentre` on top — see tests/test_corpus_paths_ring.py).
    assert set(payload) == {"nodes", "links", "paths", "stats", "unresolved", "folded", "layout"}
    assert payload["folded"] == []

    ids = {n["id"] for n in payload["nodes"]}
    for node in payload["nodes"]:
        # every point is POSITIONED in Python — site/graph.js's mountPaths never simulates
        assert isinstance(node["x"], int | float) and isinstance(node["y"], int | float)
        assert node["pin"] is True
        assert node["kind"] in {"state", "anchor", "branch", "merge", "branch-merge"}

    path_ids = {p["id"] for p in payload["paths"]}
    for link in payload["links"]:
        assert not link.get("folded"), "no fold expected under the default budget here"
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


# §5d fixtures: four single-submission variants sharing one family (Mount -> Finish, actor
# 'a') and one mixed-type variant (submission attempt + a pass, inferred sweep between) on a
# DIFFERENT family — enough to force both a category fold ("Submissions") and the "Other
# paths" bucket at a small budget.
def _submission_bout(label: str) -> list[dict[str, object]]:
    return [
        {"label": "Mount", "type": "control", "side": "a"},
        {"label": label, "type": "submission", "side": "a", "successful": True},
    ]


_MIXED_BOUT: list[dict[str, object]] = [
    {"label": "Closed Guard", "type": "guard", "side": "a"},
    {"label": "Armbar", "type": "submission", "side": "a", "successful": False},
    {"label": "Knee Cut", "type": "pass", "side": "a", "successful": True},
    {"label": "Mount", "type": "control", "side": "a"},
]

_FOLD_BOUTS = [
    _submission_bout("Armbar"), _submission_bout("Triangle Choke"),
    _submission_bout("Rear Naked Choke"), _submission_bout("Kimura"), _MIXED_BOUT,
]


def test_budget_never_drops_a_variant_only_folds_it() -> None:
    """§5d item 1 — the dynamic budget replaces the old static drop. Every variant beyond
    ``max_variants`` must still be reachable, just under a fold group instead of its own
    stroke: ``variants == paths (kept) + foldedVariants``, and every kept id keeps the exact
    same shape a wide-open payload gave it (no renumbering)."""
    agg = aggregate_bouts(_FOLD_BOUTS)
    wide = path_payload(agg, max_variants=100)
    assert wide["folded"] == []  # 5 variants, budget 100 — nothing to fold
    every = {p["id"]: p for p in wide["paths"]}

    narrow = path_payload(agg, max_variants=1)
    assert narrow["stats"]["variants"] == wide["stats"]["variants"] == 5
    assert narrow["stats"]["paths"] == 1
    assert narrow["stats"]["foldedVariants"] == 4
    assert narrow["stats"]["paths"] + narrow["stats"]["foldedVariants"] == \
        narrow["stats"]["variants"]
    kept = {p["id"]: p for p in narrow["paths"]}
    assert kept.keys() < every.keys()
    for pid, row in kept.items():
        assert row["actions"] == every[pid]["actions"]

    folded_ids = {v["id"] for f in narrow["folded"] for v in f["variants"]}
    assert folded_ids == every.keys() - kept.keys(), "every dropped id must resurface, folded"


def test_category_fold_only_when_every_action_shares_one_type() -> None:
    """§5d item 2 — variants whose OWN actions are all one category fold under a named category
    ("Submissions ×N"); a variant that mixes categories in its own chain never gets a category
    name, it folds under "Other paths" instead."""
    payload = path_payload(aggregate_bouts(_FOLD_BOUTS), max_variants=1)
    by_label = {f["label"]: f for f in payload["folded"]}
    assert by_label.keys() == {"Submissions ×3", "Other paths ×1"}
    subs = by_label["Submissions ×3"]
    assert subs["category"] == "submission" and subs["variantCount"] == 3
    other = by_label["Other paths ×1"]
    assert other["category"] is None and other["variantCount"] == 1
    assert other["variants"][0]["actions"] == ["Armbar", "Sweep", "Knee Cut"]

    fold_link = next(lk for lk in payload["links"] if lk.get("folded") is subs)
    assert fold_link["label"] == "Submissions ×3"
    assert fold_link["actions"] == [], "a synthetic fold id is not a real node_key"


def test_folded_bundle_still_walks_only_real_routes() -> None:
    """PROVA — folding must never draw a phantom route. Every walk the bundler licenses under a
    tight budget resolves to either a kept variant's own occurrence or a fold group's own
    synthetic stroke; nothing else. Mirrors
    ``test_no_phantom_route_over_a_corpus_shaped_aggregate`` for the §5d code path."""
    agg = aggregate_bouts(_FOLD_BOUTS)
    all_paths = render_paths(agg)
    metrics = corpus_paths._metrics_by_path(agg, None)
    family_of = {f"p{i}": (k[0], k[1], k[3]) for i, k in enumerate(sorted(agg.edges))}
    types_of = {f"p{i}": agg.edges[k]["action_types"] for i, k in enumerate(sorted(agg.edges))}
    labels = {
        key: label for row in agg.edges.values()
        for key, label in zip(row["actions"], row["action_labels"], strict=True)
    }
    ranked = sorted(all_paths, key=lambda p: corpus_paths._rank_key(p, metrics[p.path_id]))
    keep_ids = {p.path_id for p in ranked[:1]}
    kept = [p for p in all_paths if p.path_id in keep_ids]
    overflow = [p for p in all_paths if p.path_id not in keep_ids]
    synth, _meta = corpus_paths._fold_overflow(overflow, family_of, types_of, metrics, labels)

    bundled = bundle_paths(kept + synth)
    expected = {(p.source, p.actions, p.target) for p in kept + synth}
    assert bundled.walkable_routes() == expected


def test_folded_payload_is_still_deterministic() -> None:
    """Same corpus-bundle guarantee as
    ``test_payload_is_deterministic_across_runs_and_input_order``, now exercised with folding
    actually engaged."""
    one = path_payload(aggregate_bouts(_FOLD_BOUTS), max_variants=1)
    two = path_payload(aggregate_bouts(_FOLD_BOUTS), max_variants=1)
    flipped = path_payload(aggregate_bouts(list(reversed(_FOLD_BOUTS))), max_variants=1)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert json.dumps(one, sort_keys=True) == json.dumps(flipped, sort_keys=True)


def test_ocean_ceiling_never_drops_a_fold_group_only_marks_it_undrawn() -> None:
    """Ocean's second ceiling (docs §12, 2026-09-01) — 60 kept variants + one stroke per fold
    GROUP is still a novelo (877 groups measured over the full corpus). ``max_fold_groups``
    caps how many fold groups draw a stroke; the rest stay in ``folded`` (``drawn=False``) and
    their occurrence total rolls into ``stats.undrawn`` — nothing disappears, only its stroke."""
    agg = aggregate_bouts(_FOLD_BOUTS)
    wide = path_payload(agg, max_variants=1)  # default: every fold group drawn
    assert all(fm["drawn"] for fm in wide["folded"])
    assert wide["stats"]["undrawn"] == {"groups": 0, "occurrences": 0}

    narrow = path_payload(agg, max_variants=1, max_fold_groups=1)
    drawn = [fm for fm in narrow["folded"] if fm["drawn"]]
    undrawn = [fm for fm in narrow["folded"] if not fm["drawn"]]
    assert len(drawn) == 1 and len(undrawn) == 1

    # nothing disappears: same fold groups present, drawn or not
    assert {fm["id"] for fm in wide["folded"]} == {fm["id"] for fm in narrow["folded"]}
    # occurrence total is conserved across drawn + undrawn (the invariant the ticket asks for)
    assert sum(fm["count"] for fm in wide["folded"]) == \
        sum(fm["count"] for fm in narrow["folded"])
    assert narrow["stats"]["undrawn"] == {
        "groups": len(undrawn), "occurrences": sum(fm["count"] for fm in undrawn),
    }

    # only the drawn group gets a stroke on the map; the undrawn one has none
    drawn_ids_on_map = {lk["folded"]["id"] for lk in narrow["links"] if lk.get("folded")}
    assert drawn_ids_on_map == {fm["id"] for fm in drawn}
    assert not (drawn_ids_on_map & {fm["id"] for fm in undrawn})


def test_ocean_ceiling_never_draws_a_phantom_route() -> None:
    """An undrawn fold group must never sneak a synthetic stroke into the bundler — the ceiling
    is a stroke ceiling, not a smaller drop under another name."""
    agg = aggregate_bouts(_FOLD_BOUTS)
    payload = path_payload(agg, max_variants=1, max_fold_groups=1)
    undrawn_ids = {fm["id"] for fm in payload["folded"] if not fm["drawn"]}
    assert not any(lk.get("folded", {}).get("id") in undrawn_ids for lk in payload["links"])


def test_ocean_ceiling_is_deterministic() -> None:
    agg = aggregate_bouts(_FOLD_BOUTS)
    one = path_payload(agg, max_variants=1, max_fold_groups=1)
    two = path_payload(agg, max_variants=1, max_fold_groups=1)
    flipped = path_payload(
        aggregate_bouts(list(reversed(_FOLD_BOUTS))), max_variants=1, max_fold_groups=1
    )
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert json.dumps(one, sort_keys=True) == json.dumps(flipped, sort_keys=True)


def test_join_labels_compressed_collapses_consecutive_repeats() -> None:
    """§5d item 3 — consecutive repeats of the SAME action label compress on display; a chain
    with no repeats, or non-adjacent repeats, is untouched."""
    j = corpus_paths._join_labels_compressed
    assert j([]) == ""
    assert j(["Triangle"]) == "Triangle"
    assert j(["Triangle", "Triangle", "Triangle"]) == "Triangle ×3"
    assert j(["Triangle", "Armbar", "Armbar"]) == "Triangle → Armbar ×2"
    assert j(["Sweep", "Triangle", "Sweep"]) == "Sweep → Triangle → Sweep", (
        "non-adjacent repeats are not the same run — must not compress"
    )


def test_anchor_labels_are_english_not_the_app_locale() -> None:
    """``data/taxonomy/inference_table.json`` names the generics in pt-BR (the App's locale).
    Site copy is English (GrapplingArc AGENTS.md rule 4) and the label has to follow the
    node_key AFTER the perspective mirror, not the compiled state's own label."""
    payload = path_payload(aggregate_bouts([BOUT_A, BOUT_B]))
    text = json.dumps(payload, ensure_ascii=False)
    for pt in ("Por Cima", "Por Baixo", "Finalização", "Transição", "Raspagem", "Passagem"):
        assert pt not in text, f"pt-BR label {pt!r} leaked into the English site payload"
