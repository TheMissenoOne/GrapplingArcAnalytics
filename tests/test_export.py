"""Tests for the SessionPayload exporter + /export route."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from realtime.app import create_app
from realtime.export import (
    TimelineEvent,
    build_session_payload,
    role_to_actor,
    validate_session_payload,
)


def test_role_to_actor() -> None:
    assert role_to_actor("top", you_role="top") == "you"
    assert role_to_actor("bottom", you_role="top") == "partner"
    assert role_to_actor("top", you_role="bottom") == "partner"
    assert role_to_actor("", you_role="top") == "you"  # unknown defaults to you


def test_build_payload_shape_and_actors() -> None:
    events = [
        TimelineEvent(label="Montada", type="control", role="top"),
        TimelineEvent(label="Armlock", type="submission", role="top", setup="isolate arm"),
        TimelineEvent(label="Fuga de Quadril", type="escape", role="bottom"),
    ]
    payload = build_session_payload(events, you_role="top", difficulty=4, intensity=5)

    assert validate_session_payload(payload)
    assert payload["topics"] == []
    assert len(payload["rounds"]) == 1
    rnd = payload["rounds"][0]
    assert rnd["difficulty"] == 4 and rnd["intensity"] == 5
    entries = rnd["entries"]
    assert [e["actor"] for e in entries] == ["you", "you", "partner"]
    assert entries[0] == {
        "label": "Montada",
        "type": "control",
        "actor": "you",
        "successful": True,
    }
    assert entries[1]["setup"] == "isolate arm"
    assert isinstance(payload["timestamp"], int)


def test_blank_labels_skipped() -> None:
    events = [
        TimelineEvent(label="  ", type="control"),
        TimelineEvent(label="Costas", type="control"),
    ]
    payload = build_session_payload(events)
    assert len(payload["rounds"][0]["entries"]) == 1


def test_outcome_optional() -> None:
    p_no = build_session_payload([TimelineEvent(label="x", type="control")])
    assert "outcome" not in p_no["rounds"][0]
    p_yes = build_session_payload([TimelineEvent(label="x", type="control")], outcome="succeeded")
    assert p_yes["rounds"][0]["outcome"] == "succeeded"


@pytest.mark.parametrize(
    "bad", [None, 42, "str", [], {"foo": 1}]
)
def test_validate_rejects_non_payloads(bad: object) -> None:
    assert validate_session_payload(bad) is False


def test_export_route() -> None:
    client = TestClient(create_app(vocab_index={}))
    body = {
        "events": [
            {"label": "Montada", "type": "control", "role": "top"},
            {"label": "Raspagem", "type": "sweep", "role": "bottom"},
        ],
        "you_role": "top",
        "difficulty": 2,
    }
    r = client.post("/export", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert validate_session_payload(payload)
    entries = payload["rounds"][0]["entries"]
    assert entries[0]["actor"] == "you"
    assert entries[1]["actor"] == "partner"
    assert payload["rounds"][0]["difficulty"] == 2
