"""Vision-only label normalization.

The database event remains authoritative and unchanged. This module only creates
a visual target for CV experiments. "X Attempt" and "X" often look the same in
a single short clip, so visual supervision should not force outcome semantics
into the image classifier.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_ATTEMPT_SUFFIXES = (
    " attempt",
    " attempted",
    " try",
)


def strip_attempt_suffix(label: str) -> str:
    """Return a vision label with attempt/outcome wording removed."""
    value = re.sub(r"\s+", " ", str(label or "").strip())
    lower = value.casefold()

    for suffix in _ATTEMPT_SUFFIXES:
        if lower.endswith(suffix):
            value = value[: -len(suffix)].rstrip()
            break

    return value


def collapse_visual_node_key(
    node_key: str,
    label: str,
    node_by_normalized_label: Mapping[str, Mapping[str, Any]],
    normalize_name,
) -> tuple[str, str]:
    """Resolve an attempt-like event to its base TechniqueNode when possible.

    Returns ``(visual_node_key, visual_label)``. No DB rows are mutated.
    """
    clean_label = strip_attempt_suffix(label)
    if clean_label == str(label or "").strip():
        return str(node_key), str(label)

    normalized = normalize_name(clean_label)
    target = node_by_normalized_label.get(normalized)
    if target:
        return str(target["node_key"]), str(target["label"])

    # If the base node does not exist, still remove attempt wording from the
    # visual display target while keeping the original node key for provenance.
    return str(node_key), clean_label
