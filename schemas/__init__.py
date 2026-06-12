"""
Unified data schemas for all BJJ datasets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ── Techniques ────────────────────────────────────────────────

@dataclass
class Technique:
    name: str
    position: str
    origin: Literal["BJJ", "Judo", "Wrestling"]
    technique_type: str  # submission / sweep / pass / takedown / guard / escape / control
    translations: dict[str, str] = field(default_factory=dict)
    variations: list[str] = field(default_factory=list)


# ── ADCC Matches ──────────────────────────────────────────────

@dataclass
class ADCCMatch:
    match_id: str
    year: int
    winner: str
    loser: str
    win_type: Literal["SUBMISSION", "POINTS", "DECISION", "DQ", "INJURY"]
    stage: str  # R1, R2, SF, F, SPF, 3RD, 4F, 8F, E1
    submission: str | None
    weight_class: str
    sex: Literal["M", "F"]
    adv_pen: str | None  # ADV / PEN / None


# ── Fighter Stats ─────────────────────────────────────────────

@dataclass
class FighterStats:
    name: str
    wins: int
    losses: int
    titles: int
    sub_ratio: float  # submissions / total wins
    win_ratio: float  # wins / total fights
    debut_year: int
    favorite_target: Literal["Arm", "Leg", "Neck", "Other/Unknown", ""]
    belt: str = ""
    team: str = ""
    weight_class: str = ""


# ── App Types (mirrors TS types for cross-reference) ──────────

@dataclass
class AppGraphNode:
    id: str
    label: str
    type: Literal["technique", "position", "concept"]
    computed_elo: float | None = None
    node_type: str = ""

@dataclass
class AppRoundEntry:
    label: str
    assoc: str | None
    technique_type: str
    actor: Literal["you", "partner"]
    successful: bool | None = None

@dataclass
class AppSession:
    id: str
    created_at: str
    duration: float
    rounds: list[dict[str, Any]] = field(default_factory=list)
