"""
GrapplingArc Analytics — data pipelines for BJJ competition analysis.

Pipelines: download, clean, normalize, cache.
"""

from .etl import DatasetNotFoundError, Pipeline
from .registry import DATASETS, DatasetSpec

__all__ = ["Pipeline", "DatasetNotFoundError", "DATASETS", "DatasetSpec"]
