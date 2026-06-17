"""Pipeline clean/normalize tests on synthetic frames — no network."""

from __future__ import annotations

import pandas as pd

from pipelines.adcc_historical import ADCCHistoricalPipeline
from pipelines.grappling_techniques import GrapplingTechniquesPipeline
from pipelines.registry import DATASETS


def test_registry_specs_complete() -> None:
    expected = {"grappling_techniques", "adcc_historical", "adcc_fighters", "bjjheroes"}
    assert set(DATASETS) == expected
    for key, spec in DATASETS.items():
        assert spec.key == key
        if spec.source == "kaggle":
            assert "/" in spec.slug
            assert spec.files
            assert spec.delimiter in (",", ";")
        else:
            assert spec.source == "scrape"
            assert spec.url


def test_adcc_historical_delimiter_is_semicolon() -> None:
    assert DATASETS["adcc_historical"].delimiter == ";"


def test_grappling_techniques_clean_normalize() -> None:
    df = pd.DataFrame(
        {
            "Name": [" Armbar ", "Triangle Choke", None],
            "Position": ["Closed Guard", " Mount ", "X"],
            "Origin": [" BJJ ", "BJJ", "Judo"],
            "Type": ["Submissions", "Submissions", "Sweeps"],
        }
    )
    p = GrapplingTechniquesPipeline()
    out = p.normalize(p.clean(df))
    assert len(out) == 2  # NaN Name dropped
    assert list(out["technique_name"]) == ["Armbar", "Triangle Choke"]
    assert list(out["technique_type"]) == ["submissions", "submissions"]
    assert list(out["martial_art"]) == ["BJJ", "BJJ"]


def test_adcc_historical_clean_normalize() -> None:
    df = pd.DataFrame(
        {
            "match_id": ["1", "2", "3"],
            "year": ["2017", "2019", "bad"],
            "winner_name": ["A", "B", None],
            "loser_name": ["C", "D", "E"],
            "win_type": [" submission ", "decision", "points"],
            "stage": [" f ", "sf", "r1"],
            "submission": ["RNC", "", None],
            "weight_class": ["77", "88", "99"],
            "sex": ["M", "F", "M"],
        }
    )
    p = ADCCHistoricalPipeline()
    out = p.normalize(p.clean(df))
    assert len(out) == 2  # NaN winner dropped
    assert list(out["win_type"]) == ["SUBMISSION", "DECISION"]
    assert list(out["stage"]) == ["F", "SF"]
    assert out["winner"].iloc[0] == "A"
    assert out["year"].iloc[0] == 2017


def test_classify_win_type() -> None:
    f = ADCCHistoricalPipeline._classify_win_type
    assert f("SUBMISSION") == "SUBMISSION"
    assert f("SUB (RNC)") == "SUBMISSION"
    assert f("REFEREE DECISION") == "DECISION"
    assert f("DQ") == "DQ"
    assert f("INJURY") == "INJURY"
    assert f("POINTS 3-0") == "POINTS"
