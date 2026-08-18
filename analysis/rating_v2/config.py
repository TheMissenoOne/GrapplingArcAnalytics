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


# ── Wave 8/9: pinned public run ─────────────────────────────────────────────────
# rating_v2 ADR-02 (docs/rating_v2/01_DECISOES.md): every V2 state read is keyed by an
# explicit run_id -- there is no "current" state, reading without one is a defect. This is
# the run every public rating surface (dossier elo_rank/elo_percentile, breakdown elo_pct,
# GA_ELO board) pins itself to. Lives here (not in export/) because analysis/ must not
# import from export/ -- export/site_data.py re-imports this name so its existing uses
# keep working unchanged.
#
# engine_version="glicko2-v1-shadow", persisted 2026-08-18 (replay after the 2026-08-17
# identity corrections: 5 winners resolved, the Musumeci merge, 1 bout deleted). The
# earlier run 210a5ba7 read a corpus that no longer exists -- its input_hash 8a803053
# does not reproduce, and it rated 639 athletes where the corrected corpus rates 646.
# Swapping this value changes what the site publishes and requires a full
# `export.site_data --full` regeneration afterward.
SITE_RATING_RUN_ID: str | None = "2645cce4-ca61-4756-9433-848baba9e297"

# Publish-confidence cut. An editorial decision calibrated against measured impact
# (RD<=150 -> 30 trusted athletes / 544 of 894 bouts hidden; raised to RD<=200 -> 87
# trusted / 354 hidden, after seeing the impact table) -- not a property of Glicko-2
# math. Expect this to move again; never inline the number, read the constant.
SITE_MIN_CONFIDENCE_RD = 200.0
