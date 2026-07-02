"""Extract user's grappling profile from raw user_data JSON.

Produces the same 8-bucket type vector as ``analysis.deviance._TYPES``
so the user can be compared to competition fighters in the same space.
Works directly on raw JSON dicts to avoid dataclass compatibility issues.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

TYPES = ["guard", "pass", "sweep", "submission", "takedown", "control", "escape", "transition"]


# Portuguese → English label mapping for cross-referencing.
LABEL_TRANSLATIONS: dict[str, str] = {
    "costas": "Back Control",
    "montada": "Mount",
    "guarda de ganchos": "Hook Guard",
    "guarda fechada": "Closed Guard",
    "raspagem de gancho": "Hook Sweep",
    "meia guarda": "Half Guard",
    "quatro apoios": "Turtle",
    "mata-leão": "Rear Naked Choke",
    "mata leão": "Rear Naked Choke",
    "chave de braço": "Armbar",
    "chave de braco": "Armbar",
    "triângulo": "Triangle Choke",
    "triangulo": "Triangle Choke",
    "single leg": "Single Leg Takedown",
    "double leg": "Double Leg Takedown",
    "passagem headquarters": "Guard Pass",
    "headquarters pass": "Guard Pass",
    "puxada para guarda": "Pull Guard",
    "raspagem": "Sweep",
    "americana": "Americana",
    "kimura": "Kimura",
    "omoplata": "Omoplata",
    "chave de calcanhar": "Heel Hook",
    "outside ashi garami": "Outside Ashi Garami",
    "berimbolo": "Berimbolo",
}


def _map_type(raw_type: str) -> str:
    rt = raw_type.lower().strip()
    if rt in ("guard",):
        return "guard"
    if rt in ("pass",):
        return "pass"
    if rt in ("sweep",):
        return "sweep"
    if rt in ("submission",):
        return "submission"
    if rt in ("takedown",):
        return "takedown"
    if rt in ("control", "mount", "side", "back", "north", "knee"):
        return "control"
    if rt in ("escape",):
        return "escape"
    return "transition"


def normalize_technique_name(label: str) -> str:
    """Map a user technique label to English canonical form for cross-referencing."""
    key = label.lower().strip()
    return LABEL_TRANSLATIONS.get(key, label)


def extract_type_vector(data: dict[str, Any]) -> np.ndarray:
    """Extract user's 8-bucket type distribution from session rounds.

    Only counts entries where ``actor == 'you'``.
    Returns L2-normalized vector over ``TYPES``.
    """
    counts = Counter()
    for session in data.get("sessions", []):
        for rnd in session.get("rounds", []):
            for entry in rnd.get("entries", []):
                if entry.get("actor") != "you":
                    continue
                bucket = _map_type(entry.get("type", ""))
                counts[bucket] += 1

    total = sum(counts.values())
    if total == 0:
        return np.zeros(len(TYPES), dtype=np.float64)

    vec = np.array([counts.get(t, 0) / total for t in TYPES], dtype=np.float64)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def extract_technique_counts(data: dict[str, Any]) -> dict[str, int]:
    """Per-technique attempt counts from user sessions (actor='you')."""
    counts: Counter[str] = Counter()
    for session in data.get("sessions", []):
        for rnd in session.get("rounds", []):
            for entry in rnd.get("entries", []):
                if entry.get("actor") != "you":
                    continue
                label = entry.get("label", "")
                if label:
                    counts[label] += 1
    return dict(counts)


def extract_success_rates(data: dict[str, Any]) -> dict[str, float]:
    """Per-technique success rate from user sessions (actor='you')."""
    attempts: Counter[str] = Counter()
    successes: Counter[str] = Counter()
    for session in data.get("sessions", []):
        for rnd in session.get("rounds", []):
            for entry in rnd.get("entries", []):
                if entry.get("actor") != "you":
                    continue
                label = entry.get("label", "")
                if not label:
                    continue
                attempts[label] += 1
                if entry.get("successful", True) is not False:
                    successes[label] += 1
    return {t: successes.get(t, 0) / n for t, n in attempts.items()}


def extract_transition_bigrams(data: dict[str, Any]) -> Counter[tuple[str, str]]:
    """Within-round transition bigrams between consecutive entries (actor='you')."""
    bigrams: Counter[tuple[str, str]] = Counter()
    for session in data.get("sessions", []):
        for rnd in session.get("rounds", []):
            entries = [e for e in rnd.get("entries", [])
                       if e.get("actor") == "you"]
            for i in range(len(entries) - 1):
                t1 = _map_type(entries[i].get("type", ""))
                t2 = _map_type(entries[i + 1].get("type", ""))
                if t1 and t2 and t1 != t2:
                    bigrams[(t1, t2)] += 1
    return bigrams


def extract_positional_map(data: dict[str, Any]) -> dict[str, int]:
    """Position/control labels user reaches most (actor='you')."""
    counts: Counter[str] = Counter()
    for session in data.get("sessions", []):
        for rnd in session.get("rounds", []):
            for entry in rnd.get("entries", []):
                if entry.get("actor") != "you":
                    continue
                t = entry.get("type", "")
                if t in ("control", "guard", "mount", "side"):
                    label = entry.get("label", "")
                    if label:
                        counts[label] += 1
    return dict(counts)


def user_graph_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Extract user's technique graph data (node ELOs, usage, trends)."""
    graph = data.get("graph", {})
    nodes = graph.get("nodes", [])
    if not nodes:
        return {"nodes": [], "node_count": 0, "top_techniques": []}

    parsed = []
    for n in nodes:
        nd = n.get("data", {})
        parsed.append({
            "label": n.get("label", ""),
            "type": n.get("type", ""),
            "node_type": nd.get("type", ""),
            "elo": nd.get("computedElo"),
            "usage": nd.get("usageCount", 0),
            "trend": nd.get("trend", ""),
        })

    top = sorted([p for p in parsed if p["elo"] is not None],
                 key=lambda x: x["elo"] or 0, reverse=True)[:10]
    return {"nodes": parsed, "node_count": len(parsed), "top_techniques": top}
