from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.apply_events import splice


def raw_bout(*, start: str = "10:00") -> list[dict[tuple[str, int], dict[str, object]]]:
    return [{("Ana", 2026): {"opponent": "Rival", "start": start, "pbp": []}}]


def test_legacy_sidecar_remains_compatible() -> None:
    raw = raw_bout()

    patched, unmatched = splice(
        raw, {"Ana|Rival|2026": [{"label": "Sweep", "actor": "Ana", "ts": "10:30"}]}
    )

    assert (patched, unmatched) == (1, [])
    assert raw[0][("Ana", 2026)]["events"] == [{"label": "Sweep", "actor": "Ana", "ts": 630}]


def test_enriched_sidecar_splices_allowed_fields_and_derives_relative_timing() -> None:
    raw = raw_bout()
    sidecar = {
        "Ana|Rival|2026": {
            "events": [{"label": "Sweep", "actor": "Ana (Final)", "ts": 630}],
            "scouting_observations": [
                {"actor": "Rival", "kind": "initiative", "value": "espera", "ts": 640}
            ],
            "timing": {"end_ts": 1200, "overtime_start_ts": 900},
            "adjudication": {
                "status": "verified",
                "kind": "point_total",
                "result": {
                    "positive": {"Ana (Final)": 2, "Rival": 0},
                    "negative": {"Ana (Final)": 0, "Rival": 1},
                    "advantages": {"Ana (Final)": 0, "Rival": 0},
                    "penalties": {"Ana (Final)": 0, "Rival": 1},
                },
            },
        }
    }

    patched, unmatched = splice(raw, sidecar)
    bout = raw[0][("Ana", 2026)]

    assert (patched, unmatched) == (1, [])
    assert bout["events"][0]["actor"] == "Ana"
    assert bout["scouting_observations"][0]["actor"] == "Rival"
    assert bout["timing"] == {"end_ts": 1200, "overtime_start_ts": 900}
    assert bout["duration_s"] == 600
    assert bout["overtime_start_s"] == 300
    assert bout["timing_basis"] == "video_absolute"
    assert bout["bout_start_s"] == 600
    result = bout["adjudication"]["result"]
    assert result["positive"] == {"Ana": 2, "Rival": 0}
    assert result["negative"] == {"Ana": 0, "Rival": 1}


def test_absolute_timing_without_parseable_start_is_preserved_but_not_relabelled() -> None:
    raw = raw_bout(start="unknown")

    splice(raw, {"Ana|2026": {
        "events": [],
        "scouting_observations": [],
        "timing": {"end_ts": 1200},
        "adjudication": {"status": "unknown", "kind": "none"},
    }})

    bout = raw[0][("Ana", 2026)]
    assert bout["timing"] == {"end_ts": 1200}
    assert "duration_s" not in bout
    assert bout["timing_basis"] == "video_absolute"


def test_enriched_sidecar_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="campo.*desconhecido"):
        splice(raw_bout(), {"Ana|2026": {"events": [], "invented": 1}})


@pytest.mark.parametrize(
    "value",
    [
        {"events": []},
        {"events": [], "scouting_observations": [], "timing": {}, "adjudication": {}, "x": 1},
        {"events": [], "scouting_observations": [], "timing": {},
         "adjudication": {"status": "bad", "kind": "none"}},
        {"events": [], "scouting_observations": [], "timing": {},
         "adjudication": {"status": "unknown", "kind": "none", "result": {}}},
        {"events": [], "scouting_observations": [], "timing": {},
         "adjudication": {"status": "verified", "kind": "none"}},
        {"events": [], "scouting_observations": [], "timing": {},
         "adjudication": {"status": "verified", "kind": "point_total", "result": {
             "positive": {"Ana": 2}, "negative": {"Ana": 0, "Rival": 0},
             "advantages": {"Ana": 0, "Rival": 0},
             "penalties": {"Ana": 0, "Rival": 0},
         }}},
        {"events": [], "scouting_observations": [], "timing": {},
         "adjudication": {"status": "verified", "kind": "point_total", "result": {
             "positive": {"Ana": True, "Rival": 0}, "negative": {"Ana": 0, "Rival": 0},
             "advantages": {"Ana": 0, "Rival": 0},
             "penalties": {"Ana": 0, "Rival": 0},
         }}},
        {"events": [], "scouting_observations": [], "timing": {},
         "adjudication": {"status": "verified", "kind": "round_cards", "result": {
             "rounds": [],
         }}},
        {"events": [], "scouting_observations": [], "timing": {},
         "adjudication": {"status": "verified", "kind": "round_cards", "result": {
             "rounds": [{"Ana": 11, "Rival": 9}],
         }}},
    ],
)
def test_enriched_sidecar_schema_fails_closed(value: dict[str, object]) -> None:
    raw = raw_bout()
    before = raw[0][("Ana", 2026)].copy()
    with pytest.raises(ValueError):
        splice(raw, {"Ana|2026": value})
    assert raw[0][("Ana", 2026)] == before


def test_point_total_and_round_cards_are_preserved_without_conversion() -> None:
    point_raw = raw_bout()
    card_raw = raw_bout()
    splice(point_raw, {"Ana|2026": {
        "events": [], "scouting_observations": [], "timing": {},
        "adjudication": {
            "status": "verified", "kind": "point_total",
            "result": {
                "positive": {"Ana": 2, "Rival": 0},
                "negative": {"Ana": 0, "Rival": 0},
                "advantages": {"Ana": 0, "Rival": 0},
                "penalties": {"Ana": 0, "Rival": 0},
            },
        },
    }})
    splice(card_raw, {"Ana|2026": {
        "events": [], "scouting_observations": [], "timing": {},
        "adjudication": {
            "status": "verified", "kind": "round_cards",
            "result": {"rounds": [{"Ana": 10, "Rival": 9}]},
        },
    }})

    assert point_raw[0][("Ana", 2026)]["adjudication"]["kind"] == "point_total"
    assert card_raw[0][("Ana", 2026)]["adjudication"]["kind"] == "round_cards"
    assert "positive" not in card_raw[0][("Ana", 2026)]["adjudication"]["result"]


def test_partial_point_total_accepts_nonempty_valid_subset_only() -> None:
    raw = raw_bout()
    splice(raw, {"Ana|2026": {
        "events": [], "scouting_observations": [], "timing": {},
        "adjudication": {
            "status": "partial", "kind": "point_total",
            "result": {"positive": {"Ana": 2, "Rival": 0}},
        },
    }})
    assert raw[0][("Ana", 2026)]["adjudication"]["result"] == {
        "positive": {"Ana": 2, "Rival": 0}
    }

    for result in ({}, {"invented": {"Ana": 0, "Rival": 0}}):
        with pytest.raises(ValueError):
            splice(raw_bout(), {"Ana|2026": {
                "events": [], "scouting_observations": [], "timing": {},
                "adjudication": {
                    "status": "partial", "kind": "point_total", "result": result,
                },
            }})


def test_prompt_and_fixture_share_the_enriched_sidecar_contract() -> None:
    fixture = json.loads(
        Path("tests/fixtures/scouting_rulesets/enriched_sidecar.json").read_text(encoding="utf-8")
    )
    prompt = Path("docs/PROMPT_events_sidecar.md").read_text(encoding="utf-8")

    assert fixture
    assert all(set(value) == {
        "events", "scouting_observations", "timing", "adjudication"
    } for value in fixture.values())
    for field in ("events", "scouting_observations", "timing", "adjudication"):
        assert f"`{field}`" in prompt or f'"{field}"' in prompt
    assert "video-absolute" in prompt
    assert "Do not infer or change ruleset_id, uniform" in prompt
