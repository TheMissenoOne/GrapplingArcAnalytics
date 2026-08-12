"""Atomic progress reporting for Decision Vision POC runs."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunProgress:
    run_id: str
    pipeline: str
    status: str = "running"
    phase: str = "starting"
    current: int = 0
    total: int = 0
    percent: float = 0.0
    message: str = ""
    started_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ProgressReporter:
    def __init__(self, *, output_dir: Path, run_id: str, pipeline: str) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / "progress.json"
        self.state = RunProgress(run_id=run_id, pipeline=pipeline)
        self._write()

    def update(
        self,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        if phase is not None:
            self.state.phase = phase
        if current is not None:
            self.state.current = int(current)
        if total is not None:
            self.state.total = int(total)
        if message is not None:
            self.state.message = message
        if metrics:
            self.state.metrics.update(metrics)
        if self.state.total > 0:
            self.state.percent = round(
                100.0 * self.state.current / self.state.total,
                2,
            )
        self.state.updated_at = utc_now()
        self._write()

    def complete(
        self,
        *,
        message: str = "completed",
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self.state.status = "completed"
        self.state.phase = "done"
        self.state.message = message
        if metrics:
            self.state.metrics.update(metrics)
        if self.state.total > 0:
            self.state.current = self.state.total
        self.state.percent = 100.0
        self.state.updated_at = utc_now()
        self.state.finished_at = self.state.updated_at
        self._write()

    def fail(self, error: Exception | str) -> None:
        self.state.status = "failed"
        self.state.phase = "failed"
        self.state.error = str(error)
        self.state.message = str(error)
        self.state.updated_at = utc_now()
        self.state.finished_at = self.state.updated_at
        self._write()

    def _write(self) -> None:
        payload = json.dumps(asdict(self.state), indent=2, ensure_ascii=False) + "\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=".progress-",
            suffix=".json",
            dir=self.output_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
