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
#
# Third pass, same day: AA-011. `url_mapping.json` has `opponent` identical to `athlete` in
# 89 of 307 entries, and four of those reached the corpus as a bout between two spellings of
# one person. Merging the two names would have collapsed a real bout into a self-match, so
# the true opponent was re-derived instead -- each corroborated twice, by name in
# url_mapping AND by the row's first ts landing just after that bout's mapped offset:
#   Rafael Lovato Jr vs "R. Lovato Jr."   -> Evangelista     (mapped @0,    first ts 38s)
#   Isaac Michelle   vs "Izaak Michell"   -> Ryan Aitken     (mapped @2238, first ts 2258s)
#   Owen Flanagan    vs "Eoghan Oflanagan"-> Santeri Lilius  (mapped @3706, first ts 3730s)
#   Ethan Crelinsten vs "Ethan Krellstein"-> DeAndre Corban  (mapped @179,  first ts 243s)
# Three phantom athletes deleted; Eoghan Oflanagan kept (a real athlete with other bouts).
# The last repair revealed the WNO 24 Corban/Crelinsten bout existed twice -- the richer
# 14-event row kept, the verified video link carried across from the row that was dropped.
# 866 -> 865 matches, 644 -> 642 rated athletes.
#
# Fourth pass, 2026-08-20: the ADCC 2026 women's roster carried four athletes whose records
# were split across spellings, and a split record inflates RD directly -- fewer rated bouts
# per identity means less information, means a wider deviation, means the publication gate
# below rejects an athlete who has actually competed enough. Confirmed from the bouts, not
# from a similarity score, and merged via `scripts.dedupe_athletes`:
#   Helena Cravar        -> Helena Crevar   (transposition; WNO 31 fits her circuit)
#   Raphaela Guedes, R. Guedes, "Raphaela Guedes Final" -> Rafaela Guedes
#   Mo Black             -> Morgan Black    (nickname; Trials 2023/24 then ADCC 2024)
#   Gabby Garcia, Gabrielle Garcia -> Gabi Garcia (the CJI 2024 Craig Jones superfight)
# Fifth pass, same day: "Anna Karolina Vieira" -> "Ana Carolina Vieira", confirmed by the
# project owner. The bouts never settled it -- three each, no shared event, a double variation
# -- and corroboration from outside the string is exactly what the rule asks for. 1870/RD175 on
# three rated bouts becomes 1840/RD154 on five.
# Measured effect on the roster: Rafaela Guedes 1642/RD220 on one rated bout -> 1883/RD144 on
# seven; Morgan Black 1615/RD215 -> 1717/RD169; Gabi Garcia 1661/RD213 -> 1590/RD197. Athletes
# clearing the gate went from 6 to 9. 865 -> 864 matches (one pairing became a duplicate).
SITE_RATING_RUN_ID: str | None = "b0def998-6c7c-4011-b9ea-d0ea01e7b6bc"

# Publish-confidence cut. An editorial decision calibrated against measured impact
# (RD<=150 -> 30 trusted athletes / 544 of 894 bouts hidden; raised to RD<=200 -> 87
# trusted / 354 hidden, after seeing the impact table) -- not a property of Glicko-2
# math. Expect this to move again; never inline the number, read the constant.
SITE_MIN_CONFIDENCE_RD = 200.0
