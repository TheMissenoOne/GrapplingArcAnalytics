"""BJJ Heroes pipeline tests — parser, clean/normalize, registry spec."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pipelines.bjjheroes import BJJHeroesPipeline
from pipelines.registry import DATASETS

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def pipeline() -> BJJHeroesPipeline:
    return BJJHeroesPipeline()


class TestParseFighterPage:
    def test_fighter1(self) -> None:
        html = (FIXTURES / "fighter1.html").read_text(encoding="utf-8")
        result = BJJHeroesPipeline._parse_fighter_page(html)
        assert result["fighter_name"] == "John Doe"
        assert result["nickname"] == "The Beast"
        assert result["belt"] == "Black"
        assert result["team"] == "Team Alpha"
        assert result["weight_class"] == "77 kg"
        assert result["achievements_raw"] == "ADCC Champion 2019"

    def test_fighter2(self) -> None:
        html = (FIXTURES / "fighter2.html").read_text(encoding="utf-8")
        result = BJJHeroesPipeline._parse_fighter_page(html)
        assert result["fighter_name"] == "Jane Smith"
        assert result["nickname"] == ""
        assert result["belt"] == "Brown"
        assert result["team"] == "Gracie Barra"
        assert result["weight_class"] == "64 kg"
        assert result["achievements_raw"] == "World Champion 2022 | Pan American Champion 2021"

    def test_empty_html(self) -> None:
        result = BJJHeroesPipeline._parse_fighter_page("<html></html>")
        assert result == {
            "fighter_name": "",
            "nickname": "",
            "belt": "",
            "team": "",
            "weight_class": "",
            "achievements_raw": "",
        }


class TestCleanNormalize:
    def test_clean_drops_missing_name(self, pipeline: BJJHeroesPipeline) -> None:
        df = pd.DataFrame({
            "fighter_name": ["Alice", None, "Bob"],
            "belt": ["Black", "Brown", "Black"],
        })
        out = pipeline.clean(df)
        assert len(out) == 2
        assert list(out["fighter_name"]) == ["Alice", "Bob"]

    def test_clean_strips_whitespace(self, pipeline: BJJHeroesPipeline) -> None:
        df = pd.DataFrame({
            "fighter_name": ["  Alice  ", " Bob "],
            "belt": [" Black ", "Brown "],
        })
        out = pipeline.clean(df)
        assert list(out["fighter_name"]) == ["Alice", "Bob"]
        assert list(out["belt"]) == ["Black", "Brown"]

    def test_normalize_renames_columns(self, pipeline: BJJHeroesPipeline) -> None:
        df = pd.DataFrame({
            "fighter_name": ["Alice"],
            "nickname": ["The Ace"],
            "belt": ["Black"],
            "team": ["Team X"],
            "weight_class": ["70 kg"],
            "achievements_raw": ["Champ"],
        })
        out = pipeline.normalize(df)
        expected_cols = {
            "fighter_name", "nickname", "belt", "team", "weight_class", "achievements_raw",
        }
        assert set(out.columns) == expected_cols
        assert out["fighter_name"].iloc[0] == "Alice"

    def test_empty_dataframe(self, pipeline: BJJHeroesPipeline) -> None:
        df = pd.DataFrame(columns=["fighter_name", "belt"])
        out = pipeline.clean(df)
        assert len(out) == 0


class TestRegistrySpec:
    def test_bjjheroes_spec_fields(self) -> None:
        spec = DATASETS["bjjheroes"]
        assert spec.key == "bjjheroes"
        assert spec.source == "scrape"
        assert spec.url == "https://www.bjjheroes.com/a-z-bjj-fighters-list"
        assert spec.files == []
        assert spec.rows_approx == 400

    def test_kaggle_specs_unaffected(self) -> None:
        for key in ("grappling_techniques", "adcc_historical", "adcc_fighters"):
            spec = DATASETS[key]
            assert spec.source == "kaggle", f"{key}.source should be 'kaggle'"
            assert spec.url == "", f"{key}.url should be ''"
