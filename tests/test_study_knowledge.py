"""
Tests for study knowledge export (TF-IDF RAG corpus generator).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from export.study_knowledge import reshape_to_study_knowledge


@pytest.fixture
def sample_technique_library() -> list[dict]:
    """Sample technique library matching export/tech_library.py output shape."""
    return [
        {
            "name": "Arm Drag",
            "type": "transition",
            "translations": {"en": "Arm Drag", "pt": "Dragão de Braço"},
            "variations": ["arm drag", "drag the arm"],
            "source": "grappling_techniques_dataset",
        },
        {
            "name": "Closed Guard",
            "type": "guard",
            "translations": {"en": "Closed Guard", "pt": "Guarda Fechada"},
            "variations": ["closed guard"],
            "source": "grappling_techniques_dataset",
        },
        {
            "name": "Armbar",
            "type": "submission",
            "translations": {"en": "Armbar", "pt": "Chave de Braço"},
            "variations": ["arm bar", "arm lock"],
            "source": "adcc_submission_data",
        },
        {
            "name": "Bridge and Roll",
            "type": "sweep",
            "translations": {"en": "Bridge and Roll", "pt": "Ponte"},
            "variations": ["bridge and roll", "bridge escape"],
            "source": "grappling_techniques_dataset",
        },
    ]


def test_reshape_basic(sample_technique_library):
    """Test basic reshape: technique -> knowledge item."""
    result = reshape_to_study_knowledge(sample_technique_library)
    assert len(result) == 4
    assert all("key" in item for item in result)
    assert all("label" in item for item in result)
    assert all("aliases" in item for item in result)
    assert all("type" in item for item in result)


def test_reshape_key_format(sample_technique_library):
    """Test that keys are properly normalized (lowercase, hyphens)."""
    result = reshape_to_study_knowledge(sample_technique_library)

    # Find arm drag
    arm_drag = next((item for item in result if "arm" in item["key"]), None)
    assert arm_drag is not None
    assert arm_drag["key"] == "arm-drag"

    # Find closed guard
    closed_guard = next((item for item in result if "closed" in item["key"]), None)
    assert closed_guard is not None
    assert closed_guard["key"] == "closed-guard"

    # Find bridge and roll
    bridge = next((item for item in result if "bridge" in item["key"]), None)
    assert bridge is not None
    assert bridge["key"] == "bridge-and-roll"


def test_reshape_aliases(sample_technique_library):
    """Test that aliases include variations + Portuguese translation."""
    result = reshape_to_study_knowledge(sample_technique_library)

    arm_drag = next((item for item in result if item["key"] == "arm-drag"), None)
    assert arm_drag is not None
    assert "arm drag" in arm_drag["aliases"]
    assert "drag the arm" in arm_drag["aliases"]
    assert "Dragão de Braço" in arm_drag["aliases"]


def test_reshape_type_preserved(sample_technique_library):
    """Test that technique type is preserved."""
    result = reshape_to_study_knowledge(sample_technique_library)
    types = {item["key"]: item["type"] for item in result}

    assert types["arm-drag"] == "transition"
    assert types["closed-guard"] == "guard"
    assert types["armbar"] == "submission"
    assert types["bridge-and-roll"] == "sweep"


def test_reshape_empty_input():
    """Test reshaping empty library."""
    result = reshape_to_study_knowledge([])
    assert result == []


def test_reshape_missing_translations():
    """Test handling of techniques with missing translations."""
    techs = [
        {
            "name": "Mystery Technique",
            "type": "concept",
            # No translations at all
            "variations": [],
            "source": "test",
        }
    ]
    result = reshape_to_study_knowledge(techs)
    assert len(result) == 1
    assert result[0]["label"] == "Mystery Technique"
    assert result[0]["aliases"] == []


def test_reshape_duplicate_aliases():
    """Test that duplicate aliases are not repeated."""
    techs = [
        {
            "name": "Technique",
            "type": "concept",
            "translations": {"en": "Technique", "pt": "Technique"},  # Same in both
            "variations": ["technique", "tech"],  # "technique" appears twice
            "source": "test",
        }
    ]
    result = reshape_to_study_knowledge(techs)
    aliases = result[0]["aliases"]
    # "technique" should only appear once
    assert aliases.count("technique") == 1


def test_js_payload_generation(sample_technique_library, tmp_path):
    """Test end-to-end JS payload generation."""
    from export.js_payload import js_global

    knowledge = reshape_to_study_knowledge(sample_technique_library)
    js_code = js_global("GA_STUDY_KNOWLEDGE", knowledge)

    # Verify JS syntax
    assert "window.GA_STUDY_KNOWLEDGE" in js_code
    assert js_code.startswith("/* generated — do not edit */")

    # Verify it's valid JSON
    # Extract JSON from: window.GA_STUDY_KNOWLEDGE = {...};
    json_str = js_code.split("= ", 1)[1].rstrip(";\n")
    parsed = json.loads(json_str)
    assert len(parsed) == 4
    assert parsed[0]["key"] == "arm-drag"


def test_reshape_handles_special_chars(sample_technique_library):
    """Test key normalization with special characters."""
    techs = sample_technique_library + [
        {
            "name": "North-South Position",
            "type": "position",
            "translations": {"en": "North-South Position", "pt": "Posição Norte-Sul"},
            "variations": [],
            "source": "test",
        },
        {
            "name": "Leg Drag (Pass)",
            "type": "pass",
            "translations": {"en": "Leg Drag (Pass)", "pt": "Passagem Puxando Perna"},
            "variations": [],
            "source": "test",
        },
    ]
    result = reshape_to_study_knowledge(techs)

    # "North-South Position" → "north-south-position"
    ns_pos = next((item for item in result if "north" in item["key"]), None)
    assert ns_pos is not None
    assert ns_pos["key"] == "north-south-position"

    # "Leg Drag (Pass)" → "leg-drag-pass" (parens removed)
    leg_drag = next((item for item in result if "leg" in item["key"]), None)
    assert leg_drag is not None
    assert leg_drag["key"] == "leg-drag-pass"
    assert "(" not in leg_drag["key"]
