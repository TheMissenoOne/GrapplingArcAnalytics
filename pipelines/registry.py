"""
Dataset registry — central catalog of all supported datasets.

Each entry describes how to download, load, and parse the dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DatasetSpec:
    """Specification for an external dataset."""

    key: str                     # internal slug (used for file/dir names)
    slug: str                    # Kaggle slug: "owner/dataset-name"
    files: list[str]             # CSV filenames inside Kaggle download
    description: str             # Human-readable name
    delimiter: str = ","
    encoding: str = "utf-8"
    rows_approx: int = 0
    source: Literal["kaggle", "scrape"] = "kaggle"
    url: str = ""


DATASETS: dict[str, DatasetSpec] = {
    "grappling_techniques": DatasetSpec(
        key="grappling_techniques",
        slug="liiucbs/grappling-techniques",
        files=["grappling  techniques.csv", "dataset.csv"],
        description="76 grappling techniques from BJJ, Judo, and Wrestling",
        rows_approx=76,
    ),
    "adcc_historical": DatasetSpec(
        key="adcc_historical",
        slug="bjagrelli/adcc-historical-dataset",
        files=["adcc_historical_data.csv"],
        description="1,028 ADCC matches from 1998–2022 with outcomes and divisions",
        delimiter=";",
        rows_approx=1028,
    ),
    "adcc_fighters": DatasetSpec(
        key="adcc_fighters",
        slug="albucathecoder/adcc-fighter-stats",
        files=["fighters_dataset.csv", "adcc_fighter_stats.csv"],
        description="ADCC fighter career stats (wins, titles, submission ratios)",
        rows_approx=614,
    ),
    "bjjheroes": DatasetSpec(
        key="bjjheroes",
        slug="bjjheroes",
        files=[],
        description="~400 BJJ Heroes athlete profiles with belt, team, lineage",
        source="scrape",
        url="https://www.bjjheroes.com/a-z-bjj-fighters-list",
        rows_approx=400,
    ),
}
