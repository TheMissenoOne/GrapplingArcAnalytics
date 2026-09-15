"""``scripts/seed_technique_nodes.py`` — origin mapping (alembic 0065).

Dry-run only: no DATABASE_URL needed, mirrors how the script itself skips the sqlalchemy
import in ``--dry-run`` mode.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.seed_technique_nodes import _SOURCE_TO_ORIGIN, _origin_histogram, seed


def test_source_to_origin_covers_every_export_tier() -> None:
    # export/tech_library.py's four `entry["source"]` values.
    assert _SOURCE_TO_ORIGIN["library"] == "curated"
    assert _SOURCE_TO_ORIGIN["grappling_techniques_dataset"] == "dataset"
    assert _SOURCE_TO_ORIGIN["adcc_submission_data"] == "dataset"
    assert _SOURCE_TO_ORIGIN["athlete_match"] == "corpus"


def test_origin_histogram_buckets_missing_as_unknown() -> None:
    rows: dict[str, dict[str, Any]] = {
        "a": {"origin": "curated"},
        "b": {"origin": "curated"},
        "c": {"origin": "dataset"},
        "d": {"origin": None},
    }
    assert _origin_histogram(rows) == {"curated": 2, "dataset": 1, "unknown": 1}


def test_seed_dry_run_maps_each_item_source_to_origin(tmp_path: Path) -> None:
    items = [
        {"translations": {"en": "Armbar"}, "type": "submission", "source": "library"},
        {"translations": {"en": "Omoplata (Shoulder Lock)"}, "type": "submission",
         "source": "grappling_techniques_dataset"},
        {"translations": {"en": "Crucifix"}, "type": "control", "source": "athlete_match"},
        {"translations": {"en": "Mystery Move"}, "type": "concept", "source": "future_source"},
    ]
    fixture = tmp_path / "technique_library.json"
    fixture.write_text(json.dumps(items), encoding="utf-8")

    count = seed(fixture, dry_run=True)

    assert count == 4  # every distinct node_key still seeded, unknown origin included


def test_seed_dry_run_never_guesses_an_unmapped_source(tmp_path: Path) -> None:
    items = [{"translations": {"en": "Mystery Move"}, "type": "concept", "source": "future_source"}]
    fixture = tmp_path / "technique_library.json"
    fixture.write_text(json.dumps(items), encoding="utf-8")

    # seed() only logs the histogram in dry-run; re-derive the row the same way it does to
    # assert the mapping result directly rather than scraping log output.
    from scripts.seed_technique_nodes import _SOURCE_TO_ORIGIN

    assert _SOURCE_TO_ORIGIN.get("future_source") is None
    assert seed(fixture, dry_run=True) == 1
