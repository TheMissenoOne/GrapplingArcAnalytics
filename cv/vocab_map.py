"""Map ViCoS position classes to GrapplingArc technique-library nodes.

The position classifier emits ViCoS labels shaped ``"{position}_{role}"`` (e.g.
``mount_top``, ``guard_bottom``). The app graph speaks a different vocabulary: the
137 nodes in ``GrapplingArcApp/src/data/grappling-arch.nodes.json``, each with a
canonical ``name``, a ``type`` (``guard``/``control``/...), and a ``variations[]``
list of fuzzy-match aliases.

This module resolves the **position** half of a class to an app node (label + type)
via those variations, and surfaces the **role** half (top/bottom) untouched — the
app has no top/bottom field, so the ``actor`` (you/partner) decision is made
downstream during review. Position-only: submissions/sweeps/transitions are added
in later phases.

Reuses ``analysis.names._normalize_name`` for label matching — it lowercases, strips
to ``[a-z0-9 ]`` (dropping ``_``/``-``/accents) and collapses whitespace, so
``side_control``, ``side control`` and ``side-control`` all fold together.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name, _resolve_aliases

logger = logging.getLogger(__name__)

# Node types treated as "positions" a CV system can detect. Submissions, sweeps,
# etc. are excluded so a position never resolves onto, say, an armbar node.
POSITION_TYPES: tuple[str, ...] = ("guard", "control")

# ViCoS position strings (normalized) with no verbatim match in any node's
# variations — remapped to a string that does. The single place to tune
# ViCoS→app naming as the full 18-class list is confirmed.
VICOS_POSITION_ALIASES: dict[str, str] = {
    "guard": "closed guard",  # bare ViCoS "guard" -> Guarda Fechada
}

# Default location of the app node library — sibling repo, mirroring the path
# resolution in ``export/tech_library.py``.
_DEFAULT_NODES_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "GrapplingArcApp"
    / "src"
    / "data"
    / "grappling-arch.nodes.json"
)


@dataclass
class NodeRef:
    """A resolved app node: its canonical label and type."""

    name: str
    type: str


@dataclass
class VocabMatch:
    """Result of mapping one ViCoS class to the app vocabulary."""

    vicos_class: str
    position: str
    role: str
    node_name: str | None
    node_type: str | None
    matched_via: str | None
    ok: bool


def load_app_nodes(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the raw app node dicts from ``grappling-arch.nodes.json``.

    Parameters
    ----------
    path : Path or None
        Override the JSON location. Defaults to the sibling-repo path.

    Returns
    -------
    list[dict]
        The node dicts, or ``[]`` if the file is absent (graceful — the sibling
        repo may not be checked out).
    """
    nodes_path = path or _DEFAULT_NODES_PATH
    try:
        with open(nodes_path) as f:
            nodes: list[dict[str, Any]] = json.load(f)
        logger.info("Loaded %d app nodes from %s", len(nodes), nodes_path)
        return nodes
    except FileNotFoundError:
        logger.warning("App nodes file not found at %s — returning []", nodes_path)
        return []


def build_vocab_index(
    nodes: list[dict[str, Any]],
    position_types: tuple[str, ...] | None = POSITION_TYPES,
) -> dict[str, NodeRef]:
    """Build a ``normalized-alias -> NodeRef`` lookup from app nodes.

    Indexes each node's ``name``, ``translations.{en,pt}`` and every ``variations[]``
    entry (all normalized via ``_normalize_name``). First write wins on collision.

    Parameters
    ----------
    nodes : list[dict]
        Raw node dicts from :func:`load_app_nodes`.
    position_types : tuple[str, ...] or None
        Restrict to nodes of these ``type`` values. ``None`` indexes all nodes.

    Returns
    -------
    dict[str, NodeRef]
    """
    index: dict[str, NodeRef] = {}
    for node in nodes:
        node_type = str(node.get("type", ""))
        if position_types is not None and node_type not in position_types:
            continue
        name = str(node.get("name", ""))
        if not name:
            continue
        ref = NodeRef(name=name, type=node_type)

        keys: list[str] = [name, *node.get("variations", [])]
        translations = node.get("translations", {}) or {}
        keys += [translations.get("en", ""), translations.get("pt", "")]

        for key in keys:
            nk = _normalize_name(str(key))
            if not nk:
                continue
            if nk in index:
                logger.debug("Vocab collision on %r: keeping %s", nk, index[nk].name)
                continue
            index[nk] = ref
    return index


def map_vicos_class(
    label: str,
    index: dict[str, NodeRef],
    aliases: dict[str, str] | None = VICOS_POSITION_ALIASES,
) -> VocabMatch:
    """Map a single ViCoS ``"{position}_{role}"`` class to an app node.

    Splits on the last ``_`` into position/role, normalizes the position, applies
    the ViCoS position aliases, and looks it up in ``index``. Never raises — an
    unmatched position returns ``ok=False`` with ``node_*=None``, letting the caller
    decide what to do.

    Parameters
    ----------
    label : str
        e.g. ``"mount_top"``. A label with no ``_`` is treated as position-only.
    index : dict[str, NodeRef]
        From :func:`build_vocab_index`.
    aliases : dict[str, str] or None
        Normalized-position → replacement-string map. Defaults to
        :data:`VICOS_POSITION_ALIASES`.

    Returns
    -------
    VocabMatch
    """
    alias_map = aliases or {}
    if "_" in label:
        position_raw, role = label.rsplit("_", 1)
    else:
        position_raw, role = label, ""

    norm = _normalize_name(position_raw)
    # ViCoS-specific remap first, then the shared technique-name alias resolver
    # (a no-op for positions, applied for consistency with the rest of the repo).
    if norm in alias_map:
        norm = _normalize_name(alias_map[norm])
    norm = _normalize_name(_resolve_aliases(norm))

    ref = index.get(norm)
    if ref is None:
        return VocabMatch(
            vicos_class=label,
            position=position_raw,
            role=role,
            node_name=None,
            node_type=None,
            matched_via=None,
            ok=False,
        )
    return VocabMatch(
        vicos_class=label,
        position=position_raw,
        role=role,
        node_name=ref.name,
        node_type=ref.type,
        matched_via=norm,
        ok=True,
    )


def map_all(
    labels: list[str],
    index: dict[str, NodeRef],
    aliases: dict[str, str] | None = VICOS_POSITION_ALIASES,
) -> list[VocabMatch]:
    """Map a list of ViCoS classes. Convenience wrapper over :func:`map_vicos_class`."""
    return [map_vicos_class(label, index, aliases) for label in labels]
