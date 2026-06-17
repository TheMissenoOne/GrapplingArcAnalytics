"""Tests for the ViCoS-class → app-node vocab mapper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cv.vocab_map import (
    build_vocab_index,
    load_app_nodes,
    map_all,
    map_vicos_class,
)

# Minimal node set mirroring grappling-arch.nodes.json shape.
NODES = [
    {
        "name": "Montada",
        "type": "control",
        "translations": {"pt": "Montada", "en": "Mount"},
        "variations": ["full mount", "mount", "montada"],
    },
    {
        "name": "Controle Lateral",
        "type": "control",
        "translations": {"pt": "Controle Lateral", "en": "Side Control"},
        "variations": ["side control", "side mount", "controle lateral"],
    },
    {
        "name": "Guarda Fechada",
        "type": "guard",
        "translations": {"pt": "Guarda Fechada", "en": "Closed Guard"},
        "variations": ["closed guard", "full guard", "guarda fechada"],
    },
    {
        # A submission — must be excluded from the position index by default.
        "name": "Armlock",
        "type": "submission",
        "translations": {"pt": "Armlock", "en": "Armbar"},
        "variations": ["armbar", "armlock"],
    },
]


@pytest.fixture
def index() -> dict:
    return build_vocab_index(NODES)


def test_build_index_excludes_non_positions(index: dict) -> None:
    # submission node's aliases should not be indexed
    assert "armbar" not in index
    assert "mount" in index
    assert "closed guard" in index


def test_mount_top_and_bottom_same_node_role_differs(index: dict) -> None:
    top = map_vicos_class("mount_top", index)
    bottom = map_vicos_class("mount_bottom", index)
    assert top.ok and bottom.ok
    assert top.node_name == bottom.node_name == "Montada"
    assert top.node_type == "control"
    assert top.role == "top"
    assert bottom.role == "bottom"


def test_side_control_underscore_folds(index: dict) -> None:
    # "side_control" position folds to "sidecontrol"? no — _normalize_name keeps a
    # space only for "side control"; the underscore form normalizes to "sidecontrol".
    m = map_vicos_class("side control_top", index)
    assert m.ok
    assert m.node_name == "Controle Lateral"
    assert m.role == "top"


def test_guard_alias_resolves_to_closed_guard(index: dict) -> None:
    m = map_vicos_class("guard_bottom", index)
    assert m.ok
    assert m.node_name == "Guarda Fechada"
    assert m.node_type == "guard"
    assert m.role == "bottom"


def test_unknown_position_is_not_ok_and_does_not_raise(index: dict) -> None:
    m = map_vicos_class("flying_armbar_top", index)
    assert m.ok is False
    assert m.node_name is None
    assert m.node_type is None
    assert m.role == "top"


def test_label_without_role(index: dict) -> None:
    m = map_vicos_class("mount", index)
    assert m.ok
    assert m.node_name == "Montada"
    assert m.role == ""


def test_map_all(index: dict) -> None:
    results = map_all(["mount_top", "guard_bottom", "nope_top"], index)
    assert [r.ok for r in results] == [True, True, False]


def test_load_app_nodes_missing_path_returns_empty(tmp_path: Path) -> None:
    assert load_app_nodes(tmp_path / "does_not_exist.json") == []


def test_load_app_nodes_reads_json(tmp_path: Path) -> None:
    p = tmp_path / "nodes.json"
    p.write_text(json.dumps(NODES))
    loaded = load_app_nodes(p)
    assert len(loaded) == len(NODES)
    assert loaded[0]["name"] == "Montada"
