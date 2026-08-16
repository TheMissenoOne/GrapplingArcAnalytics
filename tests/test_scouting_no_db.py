"""Regression: `analysis.scouting_report` must import and run without sqlalchemy.

Local scouting is offline/DB-free by design (see module docstring). This module
previously imported ``MIN_DOSSIER_EVENTS``/``MIN_SEQUENCE_BOUTS``/``reduce_style_events``
from ``analysis.style_profile``, which pulls in sqlalchemy (``from sqlalchemy import
select``) purely to build the DB-backed ``build_style_profile``/``qualifies``. That's
the `postgres` extra — scouting only needs the base install.

Runs in a subprocess with sqlalchemy blocked via ``sys.meta_path`` (it IS installed
on this machine, so we can't rely on it being absent).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BLOCK_SQLALCHEMY = """
import sys, importlib.abc

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise ModuleNotFoundError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, _Blocker())

import analysis.scouting_report as sr

rc = sr.main([
    "--manifest", "data/scouting/adcc_2026_women.json",
    "--audit",
])
sys.exit(rc)
"""


def test_scouting_report_runs_without_sqlalchemy() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK_SQLALCHEMY],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"scouting_report failed without sqlalchemy:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "sqlalchemy" not in result.stderr.lower()
