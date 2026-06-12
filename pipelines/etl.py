"""
Base ETL pipeline: download → clean → normalize → cache.

All dataset pipelines inherit from Pipeline and implement:
  - download()   → path to raw file(s)
  - clean(df)    → DataFrame with NaN handled, types cast
  - normalize(df)→ DataFrame in unified schema
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import kagglehub
import pandas as pd

from .registry import DatasetSpec

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"


class DatasetNotFoundError(Exception):
    """Raised when a dataset cannot be downloaded or located."""


class Pipeline(ABC):
    """Base class for all dataset ingestion pipelines."""

    spec: DatasetSpec

    def run(self, force_download: bool = False) -> pd.DataFrame:
        """Execute full ETL: download → clean → normalize → cache."""
        raw_path = self.download(force=force_download)
        df = self._load_raw(raw_path)
        df = self.clean(df)
        df = self.normalize(df)
        self._cache(df)
        logger.info("%s: %d rows → %s", self.spec.key, len(df), self._cache_path())
        return df

    # ── Subclass hooks ──

    @abstractmethod
    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Handle missing values, fix types, normalize strings."""

    @abstractmethod
    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map raw columns to unified schema columns."""

    # ── Shared ──

    def download(self, force: bool = False) -> Path:
        """Download dataset via kagglehub. Returns path to raw file."""
        cache = RAW_DIR / self.spec.key
        if cache.exists() and not force:
            logger.info("%s: cached at %s", self.spec.key, cache)
            return cache / self.spec.files[0]

        logger.info("%s: downloading from Kaggle…", self.spec.key)
        path = Path(kagglehub.dataset_download(self.spec.slug))
        raw_file = path / self.spec.files[0]
        if not raw_file.exists():
            raise DatasetNotFoundError(
                f"Expected {raw_file} not found in Kaggle download for {self.spec.slug}"
            )
        self._symlink_cache(path, cache)
        return raw_file

    def _load_raw(self, path: Path) -> pd.DataFrame:
        kwargs: dict[str, Any] = {"low_memory": False}
        if self.spec.delimiter:
            kwargs["delimiter"] = self.spec.delimiter
        if self.spec.encoding:
            kwargs["encoding"] = self.spec.encoding
        logger.debug("%s: loading %s with %s", self.spec.key, path, kwargs)
        return pd.read_csv(path, **kwargs)

    def _cache(self, df: pd.DataFrame) -> None:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        path = self._cache_path()
        df.to_parquet(path, index=False)

    def _cache_path(self) -> Path:
        return PROCESSED_DIR / f"{self.spec.key}.parquet"

    @staticmethod
    def _symlink_cache(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            return
        os.symlink(str(src.resolve()), str(dst), target_is_directory=True)
