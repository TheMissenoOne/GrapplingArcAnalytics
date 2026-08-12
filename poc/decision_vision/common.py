"""Shared helpers for the Decision Vision POC."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def find_analytics_root(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    env_root = os.environ.get("GRAPPLINGARC_ANALYTICS_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser().resolve())

    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])

    here = Path(__file__).resolve()
    candidates.extend([here.parent, *here.parents])

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "db" / "models.py").exists() and (
            candidate / "analysis" / "names.py"
        ).exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate

    raise RuntimeError(
        "Could not find GrapplingArcAnalytics. Run from the repo root or set "
        "GRAPPLINGARC_ANALYTICS_ROOT=/path/to/GrapplingArcAnalytics."
    )


def parse_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if seconds >= 0 else None

    raw = str(value).strip().lower()
    if not raw:
        return None

    try:
        seconds = float(raw)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass

    if ":" in raw:
        parts = raw.split(":")
        try:
            nums = [float(part) for part in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        return None

    compact = re.fullmatch(
        r"(?:(?P<h>\d+(?:\.\d+)?)h)?"
        r"(?:(?P<m>\d+(?:\.\d+)?)m)?"
        r"(?:(?P<s>\d+(?:\.\d+)?)s)?",
        raw,
    )
    if compact and compact.group(0):
        h = float(compact.group("h") or 0)
        m = float(compact.group("m") or 0)
        s = float(compact.group("s") or 0)
        return h * 3600 + m * 60 + s

    return None


@dataclass(frozen=True)
class TaxonomyInfo:
    leaf_label: str
    family_label: str
    category_label: str
    taxonomy_path: tuple[str, ...]
    display_label: str
    event_type: str


class TaxonomyResolver:
    def __init__(self, taxonomy_path: Path) -> None:
        payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
        nodes = payload.get("nodes", [])
        self.nodes: dict[str, dict[str, Any]] = {
            str(node["id"]): node
            for node in nodes
            if isinstance(node, dict) and node.get("id")
        }

    def path_to_root(self, taxonomy_id: str | None) -> tuple[str, ...]:
        if not taxonomy_id or taxonomy_id not in self.nodes:
            return ()

        out: list[str] = []
        current = taxonomy_id
        visited: set[str] = set()

        while current and current not in visited:
            visited.add(current)
            out.append(current)
            node = self.nodes.get(current, {})
            parents = [
                str(parent)
                for parent in node.get("parents", [])
                if str(parent) in self.nodes
            ]
            current = sorted(parents)[0] if parents else ""

        return tuple(out)

    def labels_for(
        self,
        *,
        node_key: str,
        display_label: str,
        event_type: str,
        taxonomy_id: str | None,
    ) -> TaxonomyInfo:
        path = self.path_to_root(taxonomy_id)
        leaf = node_key
        family = path[0] if len(path) >= 2 else leaf
        category = path[-1] if path else (event_type or "unknown")
        return TaxonomyInfo(
            leaf_label=leaf,
            family_label=family,
            category_label=category,
            taxonomy_path=path,
            display_label=display_label,
            event_type=event_type,
        )
