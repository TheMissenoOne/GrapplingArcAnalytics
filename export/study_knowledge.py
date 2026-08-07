"""
Study knowledge exporter — converts technique_library to client-side RAG corpus.

Reshapes data/processed/technique_library.json into a compact format suitable for
the Study page's client-side TF-IDF retriever. Output: site/study-knowledge.js
(window.GA_STUDY_KNOWLEDGE).

Runs standalone: uv run python -m export.study_knowledge
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from export.js_payload import js_global
from pipelines.etl import PROCESSED_DIR

logger = logging.getLogger(__name__)

# Find the site directory (GrapplingArc/site/)
_ANALYTICS_ROOT = Path(__file__).resolve().parents[1]
_SITE_DIR = _ANALYTICS_ROOT.parent / "GrapplingArc" / "site"


def load_technique_library(lib_path: Path) -> list[dict[str, Any]]:
    """Load generated technique library JSON.

    Args:
        lib_path: Path to data/processed/technique_library.json

    Returns:
        List of technique dicts with {name, type, translations, variations, ...}

    Raises:
        FileNotFoundError: If library file does not exist.
        ValueError: If file is empty or malformed.
    """
    if not lib_path.exists():
        raise FileNotFoundError(f"Technique library not found: {lib_path}")

    with open(lib_path) as f:
        payload = json.load(f)

    if not isinstance(payload, list):
        raise ValueError(f"Expected list, got {type(payload).__name__}")

    if not payload:
        logger.warning("Technique library is empty — skipping export")
        return []

    return payload


def reshape_to_study_knowledge(techs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reshape technique library to Study Knowledge format.

    Each technique becomes {key, label, aliases, type} — compact format for RAG.

    Args:
        techs: List of technique dicts from technique_library.json

    Returns:
        List of Study Knowledge items: [{key, label, aliases, type}, ...]
    """
    knowledge = []

    for tech in techs:
        # Extract canonical key from name or translations
        name_en = tech.get("translations", {}).get("en", tech.get("name", ""))
        if not name_en:
            continue

        # Normalize key: lowercase, replace spaces/punctuation with hyphens
        key = (
            name_en.lower()
            .replace(" ", "-")
            .replace("_", "-")
            .replace("(", "")
            .replace(")", "")
            .replace("/", "-")
        )
        key = "-".join(k for k in key.split("-") if k)  # remove empty parts

        # Collect aliases from variations + translations
        aliases = []
        if tech.get("variations"):
            aliases.extend(tech["variations"])
        pt_trans = tech.get("translations", {}).get("pt", "")
        if pt_trans and pt_trans not in aliases:
            aliases.append(pt_trans)

        knowledge.append({
            "key": key,
            "label": name_en,
            "aliases": aliases,
            "type": tech.get("type", "concept"),
        })

    return knowledge


def export_study_knowledge(
    lib_path: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Export technique library as Study Knowledge JS file.

    Args:
        lib_path: Path to technique_library.json. Defaults to data/processed/.
        out_dir: Output directory. Defaults to GrapplingArc/site/.

    Returns:
        Path to written study-knowledge.js file.

    Raises:
        FileNotFoundError: If library or output directory not found.
    """
    lib_path = lib_path or (PROCESSED_DIR / "technique_library.json")
    out_dir = out_dir or _SITE_DIR

    if not out_dir.exists():
        raise FileNotFoundError(f"Output directory not found: {out_dir}")

    logger.info("Loading technique library from %s", lib_path)
    techs = load_technique_library(lib_path)
    logger.info("Loaded %d techniques", len(techs))

    logger.info("Reshaping to Study Knowledge format")
    knowledge = reshape_to_study_knowledge(techs)
    logger.info("Reshaped to %d knowledge items", len(knowledge))

    # Generate JS
    js_content = js_global("GA_STUDY_KNOWLEDGE", knowledge)

    # Write to site
    out_path = out_dir / "study-knowledge.js"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(js_content)

    logger.info("Exported %s (%d bytes)", out_path, len(js_content))
    return out_path


def main() -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        path = export_study_knowledge()
        print(f"\n✅ Study knowledge exported to {path}")
        return 0
    except Exception as exc:
        logger.error("Export failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
