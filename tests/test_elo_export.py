"""ELO table export tests — pipeline mocking, shape, enrichment, empty join."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from export.adcc_elo_table import export_adcc_elo_table


def fake_adcc_df() -> pd.DataFrame:
    return pd.DataFrame({
        "match_id": ["1", "2", "3"],
        "year": [2019, 2019, 2021],
        "winner": ["Fighter A", "Fighter B", "Fighter C"],
        "loser": ["Fighter B", "Fighter C", "Fighter A"],
        "win_type": ["SUBMISSION", "POINTS", "DECISION"],
        "stage": ["F", "SF", "R1"],
        "submission": ["RNC", None, None],
        "weight_class": ["77", "88", "99"],
        "sex": ["M", "M", "M"],
    })


def fake_fighters_df() -> pd.DataFrame:
    return pd.DataFrame({
        "fighter_name": ["Fighter A", "Fighter B"],
        "titles": [5, 2],
        "sub_ratio": [0.8, 0.6],
        "weight_class": ["77", "88"],
    })


ENRICH_FIELDS = {"titles", "sub_ratio", "weight_class"}
BASE_FIELDS = {"fighter", "elo", "matches", "wins", "losses"}


def _check_record_shape(rec: dict[str, object]) -> None:
    assert BASE_FIELDS.issubset(rec.keys()), f"Missing base fields in {rec}"


@patch("export.adcc_elo_table.ADCCHistoricalPipeline")
@patch("export.adcc_elo_table.ADCCFightersPipeline")
@patch("export.adcc_elo_table.PROCESSED_DIR")
def test_export_shape(
    mock_proc_dir: MagicMock, mock_fighters: MagicMock, mock_adcc: MagicMock,
) -> None:
    """3 fighters, 2 enriched → records sorted desc by elo."""
    tmpdir = Path(tempfile.mkdtemp())
    mock_proc_dir.__truediv__.return_value = tmpdir / "adcc_elo_table.json"
    mock_proc_dir.mkdir.return_value = None
    mock_adcc.return_value.run.return_value = fake_adcc_df()
    mock_fighters.return_value.run.return_value = fake_fighters_df()

    summary = export_adcc_elo_table()

    output_path = Path(summary["output_path"])
    assert output_path.exists()
    with open(output_path) as f:
        records = json.load(f)

    assert len(records) == 3
    elos = [r["elo"] for r in records]
    assert elos == sorted(elos, reverse=True)

    for rec in records:
        _check_record_shape(rec)

    enriched = [r for r in records if ENRICH_FIELDS.issubset(r.keys())]
    bare = [r for r in records if not ENRICH_FIELDS.intersection(r.keys())]
    assert len(enriched) == 2
    assert len(bare) == 1

    fighter_names = {r["fighter"] for r in enriched}
    assert "Fighter A" in fighter_names
    assert "Fighter B" in fighter_names
    a_rec = next(r for r in enriched if r["fighter"] == "Fighter A")
    assert a_rec["titles"] == 5
    assert a_rec["sub_ratio"] == 0.8
    assert a_rec["weight_class"] == "77"

    b_rec = next(r for r in enriched if r["fighter"] == "Fighter B")
    assert b_rec["titles"] == 2
    assert b_rec["sub_ratio"] == 0.6
    assert b_rec["weight_class"] == "88"

    c_rec = bare[0]
    assert c_rec["fighter"] == "Fighter C"


@patch("export.adcc_elo_table.ADCCHistoricalPipeline")
@patch("export.adcc_elo_table.ADCCFightersPipeline")
@patch("export.adcc_elo_table.PROCESSED_DIR")
def test_join_hit_rate(
    mock_proc_dir: MagicMock, mock_fighters: MagicMock, mock_adcc: MagicMock,
) -> None:
    """Summary reflects correct hit/miss counts."""
    tmpdir = Path(tempfile.mkdtemp())
    mock_proc_dir.__truediv__.return_value = tmpdir / "adcc_elo_table.json"
    mock_proc_dir.mkdir.return_value = None
    mock_adcc.return_value.run.return_value = fake_adcc_df()
    mock_fighters.return_value.run.return_value = fake_fighters_df()

    summary = export_adcc_elo_table()
    assert summary["total_fighters"] == 3
    assert summary["enriched"] == 2
    assert summary["missed"] == 1
    assert summary["hit_rate"] == 66.7


@patch("export.adcc_elo_table.ADCCHistoricalPipeline")
@patch("export.adcc_elo_table.ADCCFightersPipeline")
@patch("export.adcc_elo_table.PROCESSED_DIR")
def test_empty_fighters_join(
    mock_proc_dir: MagicMock, mock_fighters: MagicMock, mock_adcc: MagicMock,
) -> None:
    """When fighters_df unavailable, all records have no enrichment."""
    tmpdir = Path(tempfile.mkdtemp())
    mock_proc_dir.__truediv__.return_value = tmpdir / "adcc_elo_table.json"
    mock_proc_dir.mkdir.return_value = None
    mock_adcc.return_value.run.return_value = fake_adcc_df()
    mock_fighters.side_effect = Exception("No network")

    summary = export_adcc_elo_table()

    output_path = Path(summary["output_path"])
    with open(output_path) as f:
        records = json.load(f)

    assert len(records) == 3
    for rec in records:
        assert set(rec.keys()) == BASE_FIELDS
    assert summary["hit_rate"] == 0.0
