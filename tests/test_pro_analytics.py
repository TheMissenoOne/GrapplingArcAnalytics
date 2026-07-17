"""Pure, versioned Pro payload builders."""

from __future__ import annotations

import json

import analysis


def _session(index: int, *, events: int = 5) -> dict[str, object]:
    entries = [
        {"label": "Guard pass", "type": "pass", "actor": "you"},
        {"label": "Closed guard", "type": "guard", "actor": "you"},
        {"label": "Back control", "type": "control", "actor": "you"},
        {"label": "Rear naked choke", "type": "submission", "actor": "you"},
        {"label": "Single leg", "type": "takedown", "actor": "you", "successful": False},
    ][:events]
    return {
        "id": f"s-{index}",
        "duration": 30,
        "rounds": [{"outcome": "succeeded", "entries": entries}],
    }


def test_snapshot_marks_insufficient_input_without_fabricated_metrics() -> None:
    assert hasattr(analysis, "build_performance_snapshot_v1")
    build = analysis.build_performance_snapshot_v1

    payload = build(
        [_session(1, events=2)],
        cadence="daily",
        period_start="2026-07-16T00:00:00+00:00",
        period_end="2026-07-17T00:00:00+00:00",
        generated_at="2026-07-17T03:15:00+00:00",
    )

    assert payload["schemaVersion"] == 1
    assert payload["status"] == "insufficient_data"
    assert payload["dataSufficiency"] == {"sessionCount": 1, "eventCount": 2, "ready": False}
    assert "summary" not in payload


def test_snapshot_uses_app_style_shape_and_never_emits_athlete_rank_elo() -> None:
    assert hasattr(analysis, "build_performance_snapshot_v1")
    build = analysis.build_performance_snapshot_v1

    payload = build(
        [_session(1), _session(2), _session(3)],
        cadence="weekly",
        period_start="2026-07-10T00:00:00+00:00",
        period_end="2026-07-17T00:00:00+00:00",
        generated_at="2026-07-17T04:15:00+00:00",
        graph={"eloDelta": 12.5, "archetypeReport": {"name": "Pressure passer"}},
    )

    assert payload["status"] == "ready"
    assert payload["dataSufficiency"] == {"sessionCount": 3, "eventCount": 15, "ready": True}
    assert payload["summary"] == {
        "durationMin": 90,
        "rounds": 3,
        "successRate": 0.8,
        "eloDelta": 12.5,
    }
    assert payload["style"]["styleMix"]["pass"] == 0.2
    assert payload["style"]["finishing"]["submissionsLanded"] == 3
    assert payload["style"]["finishing"]["submissionsAttempted"] == 3
    assert payload["archetype"] == {"name": "Pressure passer"}
    assert payload["network"]["hubs"]
    assert payload["pathToVictory"]
    assert "athleteRankElo" not in json.dumps(payload)


def test_athlete_dossier_keeps_existing_style_profile_and_optional_graph() -> None:
    assert hasattr(analysis, "build_athlete_dossier_v1")
    build = analysis.build_athlete_dossier_v1

    payload = build(
        athlete={"id": "athlete-1", "name": "Ada Grappler", "nickname": "The Test"},
        style_profile={
            "style_mix": {"pass": 0.5},
            "signature_techniques": [{"label": "Guard pass", "count": 4}],
            "responses": {"guard passed": {"total": 2, "moves": []}},
            "finishing": {"finish_rate": 0.5},
            "archetype": "Pressure passer",
            "bouts": [{"opponent": "Bea", "result": "def. Bea"}],
        },
        graph_id=None,
        network={"pageRank": [{"label": "Guard pass", "value": 0.2}], "betweenness": []},
        path_to_victory=[{"label": "Guard pass", "value": 0.4}],
        generated_at="2026-07-17T04:15:00+00:00",
    )

    assert payload["schemaVersion"] == 1
    assert payload["athlete"] == {"id": "athlete-1", "name": "Ada Grappler", "nickname": "The Test"}
    assert payload["style"]["style_mix"]["pass"] == 0.5
    assert payload["graphId"] is None
    assert payload["bouts"] == [{"opponent": "Bea", "result": "def. Bea"}]
