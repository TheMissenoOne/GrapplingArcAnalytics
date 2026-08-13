from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.scouting_rulesets import (
    RulesetError,
    load_rulesets,
    project_adcc_events,
    validate_rulesets,
    validate_target_rulesets,
)


def test_checked_in_registry_has_required_verified_presets() -> None:
    registry = load_rulesets(Path("data/scouting/rulesets.json"))

    assert {
        "adcc-worlds-current-2026-08-13",
        "adcc-trials-current-2026-08-13",
        "cji-2025-women",
        "ibjjf-v6-gi",
        "ibjjf-v6-no-gi",
        "adcc-historical-unknown",
        "ibjjf-unknown",
        "other-unknown",
    } <= set(registry)
    assert registry["cji-2025-women"]["decision_model"] == "round_cards"
    assert registry["ibjjf-v6-gi"]["uniform"] == "gi"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"duplicate": True}, "duplicado"),
        ({"captured_on": "13/08/2026"}, "captured_on"),
        ({"source_url": "file:///rules"}, "source_url"),
        ({"family": "made-up"}, "family"),
        ({"uniform": "maybe"}, "uniform"),
        ({"decision_model": "made-up"}, "decision_model"),
        ({"profiles": []}, "profiles"),
    ],
)
def test_registry_validation_rejects_invalid_contract(
    change: dict[str, object], message: str
) -> None:
    preset = {
        "id": "valid",
        "family": "adcc",
        "edition": "snapshot",
        "uniform": "no_gi",
        "decision_model": "points",
        "verification_status": "verified",
        "source_url": "https://adcombat.com/adcc-rules-regulations/",
        "captured_on": "2026-08-13",
        "profiles": {"qualifying": {"windows": [
            {"start_s": 0, "end_s": None, "positive": True, "negative": True}
        ]}},
    }
    payload = [preset, dict(preset)] if change.pop("duplicate", False) else [{**preset, **change}]

    with pytest.raises(RulesetError, match=message):
        validate_rulesets(payload)


def test_target_must_resolve_to_verified_profile() -> None:
    registry = load_rulesets(Path("data/scouting/rulesets.json"))
    good = {
        "target_uniform": "no_gi",
        "target_ruleset_ids": ["adcc-worlds-current-2026-08-13:qualifying"],
    }
    validate_target_rulesets(good, registry)

    for target in ("adcc-historical-unknown:unknown", "missing:qualifying"):
        with pytest.raises(RulesetError):
            validate_target_rulesets({**good, "target_ruleset_ids": [target]}, registry)

    with pytest.raises(RulesetError, match="ADCC"):
        validate_target_rulesets(
            {**good, "target_ruleset_ids": ["cji-2025-women:women"]}, registry
        )


def test_adcc_profile_windows_are_strict_ordered_and_boolean() -> None:
    base = {
        "id": "adcc-test",
        "family": "adcc",
        "edition": "snapshot",
        "uniform": "no_gi",
        "decision_model": "points",
        "verification_status": "verified",
        "source_url": "https://adcombat.com/adcc-rules-regulations/",
        "captured_on": "2026-08-13",
        "profiles": {"qualifying": {"windows": [
            {"start_s": 0, "end_s": 300, "positive": False, "negative": False},
            {"start_s": 300, "end_s": None, "positive": True, "negative": True},
        ]}},
    }
    validate_rulesets([base])

    invalid_windows = (
        [{"start_s": True, "end_s": 300, "positive": False, "negative": False}],
        [{"start_s": 0, "end_s": 300, "positive": 0, "negative": False}],
        [
            {"start_s": 0, "end_s": 300, "positive": False, "negative": False},
            {"start_s": 299, "end_s": None, "positive": True, "negative": True},
        ],
        [{"start_s": 300, "end_s": 100, "positive": False, "negative": False}],
    )
    for windows in invalid_windows:
        with pytest.raises(RulesetError, match="windows"):
            validate_rulesets([{**base, "profiles": {"qualifying": {"windows": windows}}}])


def test_trials_final_allows_negatives_in_first_four_minutes() -> None:
    registry = load_rulesets(Path("data/scouting/rulesets.json"))
    _, profile = (
        registry["adcc-trials-current-2026-08-13"],
        registry["adcc-trials-current-2026-08-13"]["profiles"]["final"],
    )

    assert profile["windows"][0] == {
        "start_s": 0, "end_s": 240, "positive": False, "negative": True,
    }


def test_adcc_projection_uses_relative_phase_and_never_sums_score() -> None:
    registry = load_rulesets(Path("data/scouting/rulesets.json"))
    bout = {
        "duration_s": 600,
        "timing_basis": "bout_relative",
        "events": [
            {"label": "Takedown", "type": "takedown", "ts": 120},
            {
                "label": "Guard Pass", "type": "pass", "ts": 360,
                "rule_evidence": {"stabilized": True},
            },
            {"label": "Back Control", "type": "control"},
        ],
    }

    projection = project_adcc_events(
        bout, "adcc-worlds-current-2026-08-13:qualifying", registry
    )

    assert [item["status"] for item in projection] == [
        "ineligible_phase",
        "eligible_observed",
        "unknown",
    ]
    assert all("points" not in item and "total" not in item for item in projection)


def test_adcc_final_keeps_negative_window_distinct() -> None:
    registry = load_rulesets(Path("data/scouting/rulesets.json"))
    bout = {
        "timing_basis": "bout_relative",
        "events": [
            {"type": "penalty", "ts": 100, "officially_awarded": True},
            {"type": "takedown", "ts": 100, "rule_evidence": {"stabilized": True}},
            {"type": "takedown", "ts": 700, "rule_evidence": {"stabilized": True}},
        ],
    }

    projection = project_adcc_events(bout, "adcc-worlds-current-2026-08-13:final", registry)

    assert [item["status"] for item in projection] == [
        "officially_awarded",
        "ineligible_phase",
        "eligible_observed",
    ]
    assert json.dumps(projection, sort_keys=True) == json.dumps(projection, sort_keys=True)


def test_overtime_projection_requires_explicit_start_and_excludes_regular_time() -> None:
    registry = load_rulesets(Path("data/scouting/rulesets.json"))
    bout = {
        "timing_basis": "bout_relative",
        "overtime_start_s": 600,
        "events": [
            {"type": "takedown", "ts": 590, "rule_evidence": {"stabilized": True}},
            {"type": "takedown", "ts": 610, "rule_evidence": {"stabilized": True}},
        ],
    }

    projection = project_adcc_events(
        bout, "adcc-worlds-current-2026-08-13:overtime", registry
    )

    assert [item["status"] for item in projection] == [
        "ineligible_phase", "eligible_observed"
    ]
    assert project_adcc_events(
        {**bout, "overtime_start_s": None},
        "adcc-worlds-current-2026-08-13:overtime",
        registry,
    )[1]["status"] == "unknown"
