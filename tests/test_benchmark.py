"""Tests for user vs pro benchmarking — fixture bundle + synthetic ADCC."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from analysis.benchmark import compare, pro_baseline, user_submission_profile
from export.benchmark_results import export_benchmark_results
from schemas.app_types import UserBundle

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "user_bundle_mini.json"


@pytest.fixture
def bundle() -> UserBundle:
    with open(FIXTURE) as f:
        data = json.load(f)
    return UserBundle.from_json(data)


def test_user_submission_profile_counts(bundle: UserBundle) -> None:
    profile = user_submission_profile(bundle)
    techs = dict(zip(profile["technique"], profile["attempts"]))
    assert techs["rear naked choke"] == 2
    assert techs["armbar"] == 2
    assert techs.get("heel hook") == 1
    assert techs.get("kimura") == 1
    unknown = "unknown technique"
    assert unknown in techs


def test_user_submission_profile_empty() -> None:
    from schemas.app_types import UserBundle

    empty = UserBundle(sessions=[])
    profile = user_submission_profile(empty)
    assert profile.empty


def test_pro_baseline_return() -> None:
    adcc = pd.DataFrame(
        {
            "submission": ["RNC", "armbar", "triangle", "RNC", "heel hook"],
            "year": [2019, 2019, 2021, 2021, 2021],
            "weight_class": ["77", "88", "77", "99", "88"],
            "sex": ["M", "M", "F", "M", "M"],
            "stage": ["F", "SF", "R1", "F", "R2"],
        }
    )
    baseline = pro_baseline(adcc)
    assert "technique" in baseline.columns
    assert "pro_share" in baseline.columns
    assert baseline["pro_share"].sum() > 0


def test_compare_merges() -> None:
    user = pd.DataFrame(
        {
            "technique": ["rear naked choke", "armbar", "not_in_adcc"],
            "attempts": [3, 2, 1],
            "successes": [2, 1, 1],
            "user_share": [0.5, 0.33, 0.17],
        }
    )
    pro = pd.DataFrame(
        {
            "technique": ["rear naked choke", "armbar", "heel hook"],
            "pro_share": [0.3, 0.2, 0.1],
        }
    )
    result = compare(user, pro)
    assert len(result) == 3
    assert "no_pro_data" in result.columns
    assert "ratio" in result.columns
    assert "emphasis" in result.columns
    not_in = result[result["technique"] == "not_in_adcc"]
    assert not_in["no_pro_data"].iloc[0]
    rnc = result[result["technique"] == "rear naked choke"]
    assert not rnc["no_pro_data"].iloc[0]


def test_export_benchmark_results(tmp_path: Path) -> None:
    mock_adcc_data = pd.DataFrame(
        {
            "submission": ["RNC", "armbar", "triangle", "heel hook"],
            "year": [2019, 2021, 2021, 2019],
            "weight_class": ["77", "88", "99", "77"],
            "sex": ["M", "M", "F", "M"],
            "stage": ["F", "SF", "R1", "R2"],
        }
    )

    with (
        patch("export.benchmark_results.PROCESSED_DIR", tmp_path),
        patch("export.benchmark_results.ADCCHistoricalPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline_cls.return_value.run.return_value = mock_adcc_data
        result = export_benchmark_results(FIXTURE)

    assert "total_techniques" in result
    assert result["total_techniques"] >= 4
    assert "output_path" in result

    out_file = tmp_path / "benchmark_results.json"
    assert out_file.exists()
    with open(out_file) as f:
        payload = json.load(f)
    assert "generated_at" in payload
    assert "techniques" in payload
    assert "summary" in payload
