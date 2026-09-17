"""scripts/owner_truth_pull.py -- pure matching/extraction helpers only, no DB (mirrors
tests/test_reference_owner.py's mocked-session convention for the DB-touching functions,
which are trivial pass-throughs not covered here)."""
from __future__ import annotations

from typing import Any

from scripts.owner_truth_pull import find_truth_for_slug, round_matches_slug, round_truth_events


def test_round_matches_slug_by_filename() -> None:
    rnd = {"media": [{"filename": "IMG_7501.MOV", "id": "m1"}]}
    assert round_matches_slug(rnd, "img-7501") is True


def test_round_matches_slug_by_media_id() -> None:
    rnd = {"media": [{"id": "round1"}]}
    assert round_matches_slug(rnd, "round1") is True


def test_round_matches_slug_no_media_is_false() -> None:
    assert round_matches_slug({"media": []}, "round1") is False
    assert round_matches_slug({}, "round1") is False


def test_round_truth_events_drops_entries_without_ts_and_sorts() -> None:
    rnd = {
        "entries": [
            {"ts": 20.0, "label": "triangle choke", "actor": "you", "successful": True,
             "type": "submission", "sequenceId": "a"},
            {"label": "no ts here", "actor": "you"},  # dropped
            {"ts": 5.0, "label": "armbar", "actor": "partner", "successful": False,
             "type": "submission", "sequenceId": "a"},
        ]
    }
    out = round_truth_events(rnd)
    assert [e["label"] for e in out["events"]] == ["armbar", "triangle choke"]
    assert out["events"][0]["ts"] == 5.0
    assert out["resets"] == []  # same sequenceId throughout


def test_round_truth_events_reset_at_sequence_boundary() -> None:
    rnd = {
        "entries": [
            {"ts": 5.0, "label": "armbar", "actor": "you", "sequenceId": "a"},
            {"ts": 9.0, "label": "triangle choke", "actor": "you", "sequenceId": "b"},
        ]
    }
    out = round_truth_events(rnd)
    assert out["resets"] == [7.0]


def test_find_truth_for_slug_returns_first_matching_round() -> None:
    sessions_data: list[dict[str, Any]] = [
        {"rounds": [{"media": [{"filename": "other.mov"}], "entries": []}]},
        {"rounds": [
            {"media": [{"filename": "IMG_7501.mov"}],
             "entries": [{"ts": 1.0, "label": "armbar", "actor": "you"}]},
        ]},
    ]
    out = find_truth_for_slug(sessions_data, "img-7501")
    assert out is not None
    assert out["events"][0]["label"] == "armbar"


def test_find_truth_for_slug_none_when_no_round_matches() -> None:
    assert find_truth_for_slug([{"rounds": [{"media": [], "entries": []}]}], "img-7501") is None
