"""Name normalization helpers for BJJ technique names — extracted from export/tech_library.py."""

from __future__ import annotations

import re

# Known name aliases for cross-referencing (norm → canonical)
# NOTE: "guillotine" intentionally omitted — would change existing ADCC behavior
# (raw "guillotine" stays "guillotine", not resolved to "guillotine choke")
NAME_ALIASES: dict[str, str] = {
    "rnc": "rear naked choke",
    "d'arce choke": "darce choke",
    "d'arce": "darce choke",
    "inside heel hook": "heel hook",
    "outside heel hook": "heel hook",
    "mata leao": "rear naked choke",
    "hadaka jime": "rear naked choke",
    "chave de braco": "armbar",
    "chave de calcanhar": "heel hook",
    "triangulo": "triangle choke",
}


def _resolve_aliases(name: str) -> str:
    """Resolve a name to its canonical form via alias map."""
    return NAME_ALIASES.get(name, name)


def _normalize_name(name: str) -> str:
    """Normalize technique name for cross-referencing."""
    n = name.lower().strip()
    n = re.sub(r"[^a-z0-9 ]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n


def _normalize_adcc_sub(name: str) -> str:
    """Normalize + resolve aliases for ADCC submission names.

    Merges variants like "inside heel hook" / "outside heel hook" → "heel hook",
    "rnc" → "rear naked choke", etc.
    """
    return _resolve_aliases(_normalize_name(name))
