"""Rating V2 engine parameters — versioned as DATA, not constants scattered across modules.

Any parameter change (tau, seed, disciplines) is a new ``engine_version`` and requires a
full replay (see ``docs/rating_v2/01_DECISOES.md`` ADR-02). The replay artefact embeds the
config that produced it (``asdict(config)``), so a report is self-describing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EngineConfig:
    engine_version: str = "glicko2-v1-shadow"
    athlete_seed: float = 1750.0
    initial_rd: float = 250.0
    initial_volatility: float = 0.06
    tau: float = 0.5
    seed_from_rank_elo: bool = False
    disciplines: tuple[str, ...] = ("submission_grappling",)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
