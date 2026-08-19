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
# engine_version="glicko2-v1-shadow", persisted 2026-08-19, second pass. The corpus repair
# that day was: Cam Herd -> Cam Hurd and Tex Johnson -> Aaron Johnson (both incl. actor_id
# refs inside sequence JSONB), 29 duplicate bouts deleted, 56 years corrected against
# external evidence, 80 ts cleared on bouts proven to show a different fight, and 31
# verified video links written.
#
# TWO of those 29 deletions were then RESTORED. The reviewed year-mismatch rule grouped by
# (athlete pair, event name) ignoring year, which is only safe when the event name is one
# card. `ADCC` and `Grappling Ind.` are generic, and BJJ Heroes' match history proves both
# pairs were separate bouts: Ryan-Pena at ADCC 2017 AND 2024, Ryan-Johnson at Grappling
# Ind. 2016 AND 2017. Final corpus is 866, not 864.
#
# The intermediate run 7b5ba192 read the 864-match corpus and is superseded. Confident
# athletes (RD<=200) are 210 here vs 221 on 2645cce4 -- the deleted duplicates had been
# inflating bout counts. The Johnson merge alone took that athlete from a 182/203 split to
# a single RD of 152.
# Swapping this value changes what the site publishes and requires a full
# `export.site_data --full` regeneration afterward.
SITE_RATING_RUN_ID: str | None = "f8ae4860-9feb-4a5c-9f78-aa8f6ce92c5b"

# Publish-confidence cut. An editorial decision calibrated against measured impact
# (RD<=150 -> 30 trusted athletes / 544 of 894 bouts hidden; raised to RD<=200 -> 87
# trusted / 354 hidden, after seeing the impact table) -- not a property of Glicko-2
# math. Expect this to move again; never inline the number, read the constant.
SITE_MIN_CONFIDENCE_RD = 200.0
