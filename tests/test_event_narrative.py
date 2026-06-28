"""Tests for the event-level prose engine (pure, off a fixture profile)."""

from __future__ import annotations

from typing import Any

from analysis.event_profile import _method
from export.narrative import event_narrative, render_markdown


def _fixture() -> dict[str, Any]:
    return {
        "event": "CJI",
        "year": 2024,
        "bout_count": 5,
        "participant_count": 9,
        "decided": 5,
        "finishes": 3,
        "finish_rate": 0.6,
        "submissions": [("Rear Naked Choke", 2), ("Heel Hook", 1)],
        "top_techniques": [("Back Control", 7), ("Guard Pull", 4)],
        "style_mix": {"pass": 0.1, "control": 0.4, "submission": 0.2, "escape": 0.05,
                      "guard": 0.15, "sweep": 0.05, "takedown": 0.05},
        "headliners": ["Gordon Ryan", "Craig Jones"],
        "headline_bout": {"slug": "x", "a": "Gordon Ryan", "b": "Craig Jones",
                          "winner": "Gordon Ryan", "method": "Submission · Rear Naked Choke"},
        "bouts": [],
    }


def test_event_narrative_sections_and_numbers() -> None:
    secs = event_narrative(_fixture())
    headings = [h for h, _ in secs]
    assert headings[0] == "The card"
    assert "Headline bout" in headings
    assert "How they finished" in headings
    body = render_markdown(secs)
    assert "CJI ran 5 bouts in 2024." in body
    assert "60% of the decided bouts ended in a finish." in body
    assert "Gordon Ryan" in body and "Craig Jones" in body
    assert "rear naked choke (2×)" in body  # most-seen finish, lower-cased
    assert "control 40%" in body  # dominant style bucket


def test_event_narrative_skips_finishes_when_undecided() -> None:
    ep = _fixture()
    ep["decided"] = 0
    ep["finishes"] = 0
    headings = [h for h, _ in event_narrative(ep)]
    assert "How they finished" not in headings


def test_method_formatting() -> None:
    assert _method("SUBMISSION", "Heel Hook") == "Submission · Heel Hook"
    assert _method("SUBMISSION", None) == "Submission"
    assert _method("DECISION", None) == "Decision"
    assert _method("", None) == "No decision"
