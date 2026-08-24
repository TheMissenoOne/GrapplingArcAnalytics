"""Committed PoC fixtures.

``data/fixtures/user_export_recent_slice.json`` is a REAL, privacy-reduced slice
of the repository owner's own GrapplingArc export (27 rounds, 2026-08-11..20),
committed with the owner's explicit consent (2026-08-24) so app-data PoCs run
reproducibly — the interaction-graph PoC (E8), shrinkage (E1) and the app-side
E0 twin all want session-shaped data with reliable you/partner actors, which the
competition corpus cannot supply (43.9% of its bouts have uninformative actors;
``analysis/attribution.py``).

Owner-consented ≠ public: this is the owner's own data serving the owner's own
analysis, which is the allowed direction under the repo's LGPD one-way rule
(CLAUDE.md). It must never feed an archetype centroid, a site page, or any
competitive artefact — the same rule as every other ``owner_kind='user'`` datum.

Provenance caveat: the slice was transcribed by an external LLM from chat text,
not exported by the app directly — treat exact counts as approximate until an
app-native export replaces it (the loader asserts only the shape).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
USER_EXPORT_RECENT_SLICE = REPO / "data" / "fixtures" / "user_export_recent_slice.json"


def load_user_export_slice(path: Path = USER_EXPORT_RECENT_SLICE) -> dict[str, Any]:
    """The raw fixture dict: ``scope``, ``selected_current_node_metrics``, ``rounds``."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def user_export_rounds(path: Path = USER_EXPORT_RECENT_SLICE) -> list[dict[str, Any]]:
    """The rounds, each ``{date, difficulty, intensity, outcome, title, events}``
    with events ``{label, actor: you|partner, successful, type}`` — the app-side
    event shape (NB: ``successful`` is explicit here; in-app ``undefined`` means
    landed)."""
    return list(load_user_export_slice(path).get("rounds", []))
