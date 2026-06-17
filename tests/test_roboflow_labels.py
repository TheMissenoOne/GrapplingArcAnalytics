"""Tests for Roboflow bjj3 class → ViCoS label conversion."""

from __future__ import annotations

import pytest

from cv.roboflow_labels import roboflow_to_vicos
from cv.vocab_map import map_vicos_class


@pytest.mark.parametrize(
    ("rf", "expected"),
    [
        ("mount1", "mount_top"),
        ("mount2", "mount_bottom"),
        ("back2", "back_bottom"),
        ("open_guard1", "open guard_top"),
        ("side_control2", "side control_bottom"),
        ("5050_guard", "5050 guard"),  # leading 5050 preserved, no role
        ("standing", "standing"),
        ("takedown1", "takedown_top"),
        ("MOUNT1", "mount_top"),  # case-insensitive
    ],
)
def test_default_role_map(rf: str, expected: str) -> None:
    assert roboflow_to_vicos(rf) == expected


def test_variant_mode_drops_suffix() -> None:
    assert roboflow_to_vicos("mount1", role_map={}) == "mount"
    assert roboflow_to_vicos("side_control2", role_map={}) == "side control"
    assert roboflow_to_vicos("5050_guard", role_map={}) == "5050 guard"


def test_round_trips_through_vocab_map() -> None:
    # The output must parse cleanly into (position, role) via the existing splitter.
    label = roboflow_to_vicos("side_control2")  # "side control_bottom"
    match = map_vicos_class(label, {})  # empty index → no node, but parsing still works
    assert match.position == "side control"
    assert match.role == "bottom"
