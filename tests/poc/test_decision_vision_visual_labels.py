import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "poc"))

from decision_vision.visual_labels import (  # noqa: E402
    collapse_visual_node_key,
    strip_attempt_suffix,
)


def test_strip_attempt_suffix() -> None:
    assert strip_attempt_suffix("Double Leg Attempt") == "Double Leg"
    assert strip_attempt_suffix("Armbar attempted") == "Armbar"
    assert strip_attempt_suffix("Mount") == "Mount"


def test_collapse_visual_node_key_resolves_base() -> None:
    nodes = {
        "double-leg": {
            "node_key": "double-leg",
            "label": "Double Leg",
        }
    }

    def norm(value: str) -> str:
        return value.strip().lower().replace(" ", "-")

    assert collapse_visual_node_key(
        "double-leg-attempt",
        "Double Leg Attempt",
        nodes,
        norm,
    ) == ("double-leg", "Double Leg")


def test_collapse_visual_node_key_unresolved_keeps_provenance() -> None:
    nodes = {}

    def norm(value: str) -> str:
        return value.strip().lower().replace(" ", "-")

    assert collapse_visual_node_key(
        "spider-fly-attempt",
        "Spider Fly Attempt",
        nodes,
        norm,
    ) == ("spider-fly-attempt", "Spider Fly")
