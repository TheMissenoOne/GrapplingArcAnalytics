"""Turn a reviewed position timeline into a GrapplingArc ``SessionPayload``.

Mirrors the contract consumed by ``GrapplingArcApp/src/services/sessionProcessor.ts``
(`processSession` / `validateSession`):

    ChainEntry     = { label, type, actor: "you"|"partner", setup?, successful?, points? }
    Round          = { difficulty, intensity, entries: ChainEntry[], outcome? }
    SessionPayload = { topics: ChainEntry[], rounds: Round[], timestamp?, notes? }

The CV/segmenter and the manual annotation UI both feed a flat list of
:class:`TimelineEvent`; this module resolves each event's ViCoS ``role`` (top/bottom)
to the app ``actor`` (you/partner) and packs the chain into a single round, ready to
import into the app graph engine unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

Actor = str  # "you" | "partner"


@dataclass
class TimelineEvent:
    """One reviewed event on the match timeline.

    ``label``/``type`` are already resolved to the app vocabulary (e.g. via
    ``cv.vocab_map``); unmapped events should pass the raw position label + a
    sensible fallback type rather than being dropped, so nothing is lost silently.
    """

    label: str
    type: str
    role: str = ""  # "top" | "bottom" | "" (ViCoS); resolved to actor on export
    successful: bool = True
    setup: str | None = None


def role_to_actor(role: str, you_role: str = "top") -> Actor:
    """Map a ViCoS role to the app actor.

    Parameters
    ----------
    role : str
        ``"top"``, ``"bottom"`` or ``""``.
    you_role : str
        Which role *you* are this match. An empty/unknown ``role`` defaults to ``"you"``.
    """
    if not role:
        return "you"
    return "you" if role == you_role else "partner"


def build_session_payload(
    events: list[TimelineEvent],
    *,
    you_role: str = "top",
    difficulty: int = 3,
    intensity: int = 3,
    notes: str = "",
    timestamp: int | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Pack a reviewed timeline into a one-round ``SessionPayload`` dict.

    Parameters
    ----------
    events : list[TimelineEvent]
        Time-ordered events. Empty events (blank label) are skipped.
    you_role : str
        Role you played, for actor resolution.
    difficulty, intensity : int
        Round metadata (1–5 in the app UI).
    notes : str
        Free-text session notes.
    timestamp : int or None
        Epoch milliseconds; defaults to now.
    outcome : str or None
        Optional round outcome (``"succeeded"|"partial"|"failed"|"no_attempt"``).

    Returns
    -------
    dict
        A ``SessionPayload`` satisfying :func:`validate_session_payload`.
    """
    entries: list[dict[str, Any]] = []
    for ev in events:
        label = ev.label.strip()
        if not label:
            continue
        entry: dict[str, Any] = {
            "label": label,
            "type": ev.type,
            "actor": role_to_actor(ev.role, you_role),
            "successful": ev.successful,
        }
        if ev.setup:
            entry["setup"] = ev.setup
        entries.append(entry)

    round_obj: dict[str, Any] = {
        "difficulty": difficulty,
        "intensity": intensity,
        "entries": entries,
    }
    if outcome is not None:
        round_obj["outcome"] = outcome

    return {
        "topics": [],
        "rounds": [round_obj],
        "timestamp": timestamp if timestamp is not None else int(time.time() * 1000),
        "notes": notes,
    }


def validate_session_payload(payload: Any) -> bool:
    """Mirror of the app's ``validateSession`` — a dict with a topics or rounds list."""
    if not isinstance(payload, dict):
        return False
    return isinstance(payload.get("topics"), list) or isinstance(payload.get("rounds"), list)


def session_payload_json(events: list[TimelineEvent], **kwargs: Any) -> str:
    """Convenience: build a payload and serialize it to JSON."""
    return json.dumps(build_session_payload(events, **kwargs), ensure_ascii=False)
