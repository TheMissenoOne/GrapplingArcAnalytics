"""GrappleMap parser, alignment, and CV export tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

DB_PATH = Path(__file__).parent.parent / "data" / "GrappleMap.txt"

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="GrappleMap.txt not present"
)


@pytest.fixture(scope="module")
def gmap():
    from grapplemap.parser import parse_grapplemap
    return parse_grapplemap(DB_PATH)


# ─── parser ──────────────────────────────────────────────────────────────────

def test_parse_counts(gmap):
    assert len(gmap.positions) > 100
    assert len(gmap.transitions) > 100
    assert gmap.graph.number_of_nodes() > 100
    assert gmap.graph.number_of_edges() > 100


def test_position_joints_shape(gmap):
    pos = next(iter(gmap.positions.values()))
    assert pos.joints.shape == (2, 23, 3)
    assert pos.joints.dtype == np.float32


def test_position_has_tags(gmap):
    for pos in list(gmap.positions.values())[:20]:
        assert len(pos.tags) >= 1, f"{pos.name!r} has no tags"


def test_transition_frames_shape(gmap):
    trans = next(iter(gmap.transitions))
    assert trans.frames.ndim == 4
    assert trans.frames.shape[1:] == (2, 23, 3)


def test_graph_connectivity(gmap):
    # Some nodes should have edges
    nodes_with_edges = [
        n for n in gmap.graph.nodes
        if gmap.graph.degree(n) > 0
    ]
    assert len(nodes_with_edges) > 50


def test_known_position_present(gmap):
    """side control and north south are canonical BJJ positions."""
    names = {p.name.lower() for p in gmap.positions.values()}
    assert any("side" in n and "control" in n for n in names) or \
           any("north" in n and "south" in n for n in names)


# ─── alignment ───────────────────────────────────────────────────────────────

def test_build_lookup(gmap):
    from grapplemap.align import build_lookup
    lookup = build_lookup(gmap)
    assert len(lookup) > 100
    # All values must be valid position keys
    for norm_key, raw_key in lookup.items():
        assert raw_key in gmap.positions


def test_find_position_exact(gmap):
    from grapplemap.align import build_lookup, find_position
    lookup = build_lookup(gmap)
    # Take a real position name and look it up
    sample_pos = next(iter(gmap.positions.values()))
    result = find_position(sample_pos.name, gmap, lookup)
    assert result is not None
    assert result.name == sample_pos.name


def test_find_position_fuzzy(gmap):
    from grapplemap.align import find_position
    # Common BJJ terms likely present with slight variation
    result = find_position("side control", gmap)
    # May or may not match depending on exact name; just check no crash
    # and that if returned it's a GMapPosition
    if result is not None:
        assert hasattr(result, "joints")
        assert result.joints.shape == (2, 23, 3)


def test_grapplemap_neighbors(gmap):
    from grapplemap.align import build_lookup, grapplemap_neighbors
    lookup = build_lookup(gmap)
    # Pick a position that likely has neighbors
    first_key = next(iter(gmap.positions.keys()))
    neighbors = grapplemap_neighbors(first_key, gmap, lookup)
    # Should return a list (possibly empty for unconnected positions)
    assert isinstance(neighbors, list)


# ─── suggestions ─────────────────────────────────────────────────────────────

def test_suggestions_return_type(gmap):
    from analysis.grapplemap_suggestions import get_grapplemap_suggestions
    results = get_grapplemap_suggestions(
        ["side control", "mount", "guard"],
        gmap,
        max_suggestions=5,
    )
    assert isinstance(results, list)
    for r in results:
        assert "label" in r
        assert "type" in r
        assert r["type"] == "explore"


def test_suggestions_no_duplicates_with_input(gmap):
    from analysis.grapplemap_suggestions import get_grapplemap_suggestions
    from analysis.names import _normalize_name
    inputs = ["north south", "closed guard"]
    norm_inputs = {_normalize_name(k) for k in inputs}
    results = get_grapplemap_suggestions(inputs, gmap, max_suggestions=10)
    labels = {r["label"] for r in results}
    # Suggestions should not include the input nodes themselves
    assert labels.isdisjoint(norm_inputs)


# ─── CV export ───────────────────────────────────────────────────────────────

def test_normalize_joints(gmap):
    from grapplemap.cv_export import normalize_joints
    pos = next(iter(gmap.positions.values()))
    norm = normalize_joints(pos.joints)
    assert norm.shape == (2, 23, 3)
    # Each player should be centered near origin
    for p in range(2):
        hip_mid = (norm[p, 9] + norm[p, 8]) / 2.0  # LeftHip=8, RightHip=9 approx
        assert abs(float(hip_mid.mean())) < 1.0


def test_export_cv_dataset(gmap, tmp_path):
    from grapplemap.cv_export import export_cv_dataset
    export_cv_dataset(gmap, tmp_path, verbose=False)

    pos_dir   = tmp_path / "positions"
    trans_dir = tmp_path / "transitions"
    assert pos_dir.exists()
    assert trans_dir.exists()

    # Check at least one position .npy
    pos_files = list(pos_dir.glob("*.npy"))
    assert len(pos_files) > 0
    arr = np.load(pos_files[0])
    assert arr.shape == (2, 23, 3)

    # Check labels.json
    labels = json.loads((tmp_path / "labels.json").read_text())
    assert len(labels) > 0
    first_key = next(iter(labels))
    assert "name" in labels[first_key]
    assert "tags" in labels[first_key]
    assert "type" in labels[first_key]


# ─── icon rendering (quick smoke test — no file I/O) ─────────────────────────

def test_render_position_icon(gmap):
    from grapplemap.icons import render_position_icon
    pos = next(iter(gmap.positions.values()))
    img = render_position_icon(pos, size=64)
    assert img.width == 64
    assert img.height == 64
    # Should contain non-background pixels (the stick figures)
    pixels = np.array(img)
    assert pixels.std() > 0


# ─── exporter: app vocab subset + require-index code-gen ─────────────────────

def test_export_icons_writes_subset_and_index(gmap, tmp_path):
    from export.grapplemap_icons_export import export_icons

    full_dir   = tmp_path / "full"
    app_assets = tmp_path / "app" / "grapplemap_icons"
    index_ts   = tmp_path / "app" / "grapplemapIconIndex.ts"

    matched = export_icons(
        full_icons_dir=full_dir,
        app_assets_dir=app_assets,
        app_index_ts=index_ts,
        size=64,
        verbose=False,
    )

    # Full CV set rendered for every position.
    full_pngs = list(full_dir.glob("*.png"))
    assert len(full_pngs) == len(gmap.positions)

    # Tag + exact matching covers a solid chunk of the app position vocab.
    assert len(matched) >= 15
    subset_pngs = list(app_assets.glob("*.png"))
    assert len(subset_pngs) == len(matched)
    assert len(matched) <= len(gmap.positions)

    # Common positions resolve to the app's canonical (Portuguese) node keys.
    assert "guarda_fechada" in matched   # Closed Guard
    assert "montada" in matched          # Mount
    assert (app_assets / "guarda_fechada.png").exists()

    # Index TS is valid: every matched file has a static require, and node aliases
    # (e.g. English "closed guard") resolve to the same canonical icon.
    text = index_ts.read_text()
    assert "GRAPPLEMAP_ICONS" in text
    for key in matched:
        assert f'"{key}": require("./grapplemap_icons/{key}.png")' in text
    # English alias maps onto the canonical Portuguese icon.
    assert '"closed_guard": require("./grapplemap_icons/guarda_fechada.png")' in text
