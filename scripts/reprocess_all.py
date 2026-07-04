#!/usr/bin/env python
"""Mass-reprocess the full refined match corpus, then re-export the public site assets.

Runs every event dump through the shared importer (``scripts.dump_import.run_dump``) — each
call de-dupes by ``frozenset(participants)+year``, idempotently replaces bouts, and double-pass
replays both athletes — then exports all final bouts once via ``export.match_breakdown``. The
enriched breakdowns carry the additive ``decision_space`` block (RF14 / DS-12).

Per-event tags group bouts into card pages (``export.site_data``); the four ADCC 2022 division
files share one ``ADCC 2022`` tag. Khabib stays ``event=None`` (MMA, no card page).

    uv run python -m scripts.reprocess_all              # import all + re-export
    uv run python -m scripts.reprocess_all --dry-run    # parse + report, no DB writes, no export
    uv run python -m scripts.reprocess_all --no-export  # import only
    uv run python -m scripts.reprocess_all --only WNO22 ADCC2022-88kg   # subset by label
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# (data module under scripts/, event tag, human label). Order = chronological-ish; replay
# folds full history regardless, so order only affects intermediate idempotent rewrites.
DATASETS: list[tuple[str, str | None, str]] = [
    ("scripts.dumps.khabib_data", None, "Khabib"),
    ("scripts.dumps.adcc2022_88kg_data", "ADCC 2022", "ADCC2022-88kg"),
    ("scripts.dumps.adcc2022_99kg_data", "ADCC 2022", "ADCC2022+99kg"),
    ("scripts.dumps.adcc2022_abs_data", "ADCC 2022", "ADCC2022-ABS"),
    ("scripts.dumps.adcc2022_finals_data", "ADCC 2022", "ADCC2022-Finals"),
    ("scripts.dumps.adcc2024_abs_data", "ADCC 2024", "ADCC2024-ABS"),
    ("scripts.dumps.ibjjf2023_worlds_data", "IBJJF Worlds 2023", "IBJJF2023-Worlds"),
    ("scripts.dumps.spyder_kok_data", "Spyder: King of Kings", "SpyderKingOfKings"),
    ("scripts.dumps.polaris37_data", "Polaris 37", "Polaris37"),
    ("scripts.dumps.wno20_data", "WNO 20", "WNO20"),
    ("scripts.dumps.wno22_data", "WNO 22", "WNO22"),
    ("scripts.dumps.wno24_data", "WNO 24", "WNO24"),
    ("scripts.dumps.wno31_data", "WNO 31", "WNO31"),
    ("scripts.dumps.cji_data", "CJI", "CJI"),
    ("scripts.dumps.adcc2022_women_data", "ADCC 2022", "ADCC2022-Women"),
    ("scripts.dumps.adcc2024_p99kg_data", "ADCC 2024", "ADCC2024+99kg"),
    ("scripts.dumps.adcc_trials2022_sa_data", "ADCC Trials 2022 South America", "ADCCTrials2022SA"),
    ("scripts.dumps.adcc_trials2023_ec_finals_data", "ADCC Trials 2023 East Coast",
     "ADCCTrials2023EC-Finals"),
    ("scripts.dumps.adcc_trials2023_ec_semis_data", "ADCC Trials 2023 East Coast",
     "ADCCTrials2023EC-Semis"),
    ("scripts.dumps.adcc_trials2024_wc_data", "ADCC Trials 2024 West Coast", "ADCCTrials2024WC"),
    ("scripts.dumps.ibjjf2025_top10_data", "IBJJF 2025 Top 10", "IBJJF2025top10"),
    ("scripts.dumps.ufc325_data", "UFC 325", "UFC325"),
    ("scripts.dumps.ncaa2024_data", "NCAA 2024", "2024NCAA"),
    ("scripts.dumps.ncaa2025_data", "NCAA 2025", "2025NCAA"),
    ("scripts.dumps.ncaa2026_data", "NCAA 2026", "2026NCAA"),
    ("scripts.dumps.polaris4_data", "Polaris 4", "Polaris4"),
    ("scripts.dumps.polarispro1_data", "Polaris Pro 1", "PolarisPro1"),
    ("scripts.dumps.ethan_crelinsten_data", None, "EthanCrelinsten"),
    ("scripts.dumps.cji2day1_data", "CJI 2, Day 1", "CJI2Day1"),
    ("scripts.dumps.cji2day2_data", "CJI 2, Day 2", "CJI2Day2"),
    ("scripts.dumps.craigjones_data", None, "CraigJones"),
    ("scripts.dumps.pgf_world_2026_week_1_opening_day_data", "PGF World 2026", "PGF2026-W1"),
    ("scripts.dumps.pgf_world_2026_week_2_things_are_heating_up_data", "PGF World 2026", "PGF2026-W2"),
    ("scripts.dumps.pgf_world_2026_week_3_this_marks_the_halfway_point_data", "PGF World 2026", "PGF2026-W3"),
    ("scripts.dumps.pgf_world_2026_week_4_the_playoff_race_is_on_data", "PGF World 2026", "PGF2026-W4"),
    ("scripts.dumps.pgf_world_2026_week_5_regular_season_finale_data", "PGF World 2026", "PGF2026-W5"),
    ("scripts.dumps.polaris_25_prelims_live_full_no_gi_bjj_grappling_undercard_data", "Polaris 25", "Polaris25"),
    ("scripts.dumps.polaris_26_live_prelims_nine_free_matches_live_data", "Polaris 26", "Polaris26"),
    ("scripts.dumps.polaris_18_submission_grappling_full_bjj_event_replay_data", "Polaris 18", "Polaris18"),
    ("scripts.dumps.polaris_bjj_squads_team_usa_vs_team_uk_ireland_grappling_full_event_data", "Polaris BJJ Squads", "PolarisBJJSquads"),
    ("scripts.dumps.polaris28prelims_data", "Polaris 28", "Polaris28"),
    ("scripts.dumps.polaris29_data", "Polaris 29", "Polaris29"),
    ("scripts.dumps.polaris30_data", "Polaris 30", "Polaris30"),
    ("scripts.dumps.polaris31_data", "Polaris 31", "Polaris31"),
    ("scripts.dumps.polaris32_data", "Polaris 32", "Polaris32"),
    ("scripts.dumps.polaris33_data", "Polaris 33", "Polaris33"),
    ("scripts.dumps.polaris34_data", "Polaris 34", "Polaris34"),
    ("scripts.dumps.polaris35_data", "Polaris 35", "Polaris35"),
    ("scripts.dumps.polaris36_data", "Polaris 36", "Polaris36"),
    # polaris37_data.py is the original already-registered event; the queue transcript
    # "Polaris 37" is a separate/matching video — handled below as polaris37complement_data if needed.
    ("scripts.dumps.team_bjj_stars_vs_team_polaris_full_squads_matchup_polaris_37_data",
     "Team BJJ Stars vs Polaris", "BJJStarsVsPolaris"),
    ("scripts.dumps.eddie_bravo_invitational_14_the_absolutes_data", "EBI 14", "EBI14"),
    ("scripts.dumps.wno_30_open_weight_grand_prix_undercard_free_live_prelim_matches_data",
     "WNO 30", "WNO30"),
    ("scripts.dumps.ufc_320_free_fight_marathon_data", "UFC 320", "UFC320"),
    ("scripts.dumps.ufc_324_free_fight_marathon_data", "UFC 324", "UFC324"),
    ("scripts.dumps.ufc_327_free_fight_marathon_data", "UFC 327", "UFC327"),
    ("scripts.dumps.ufc_328_free_fight_marathon_data", "UFC 328", "UFC328"),
    ("scripts.dumps.evento_completo_final_do_jud_equipes_mistas_olimp_adas_paris_2024_data",
     "Paris 2024 Judo Mixed Team", "Judo2024MixedTeam"),
    ("scripts.dumps.supercut_the_entire_2024_adcc_worlds_65kg_bracket_data", "ADCC 2024", "ADCC2024-65kg"),
    ("scripts.dumps.leandro_lo_data", None, "LeandroLo"),
    ("scripts.dumps.ruotolos_data", None, "Ruotolos"),
    ("scripts.dumps.musumeci_data", "Musumeci", "Musumeci"),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Reprocess the full match corpus + re-export site")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    ap.add_argument("--no-export", action="store_true", help="import only, skip site re-export")
    ap.add_argument("--only", nargs="+", metavar="LABEL", help="restrict to these dataset labels")
    args = ap.parse_args()

    from scripts.dump_import import run_dump

    selected = DATASETS
    if args.only:
        wanted = {s.lower() for s in args.only}
        selected = [d for d in DATASETS if d[2].lower() in wanted]
        missing = wanted - {d[2].lower() for d in selected}
        if missing:
            logger.error("Unknown dataset label(s): %s", ", ".join(sorted(missing)))
            return 2

    for module_path, event, label in selected:
        raw = importlib.import_module(module_path).RAW
        logger.info("── %s (event=%r) ──", label, event)
        run_dump(raw, event=event, label=label, dry_run=args.dry_run)

    if args.dry_run or args.no_export:
        return 0

    from export.match_breakdown import _DEFAULT_OUT
    from export.match_breakdown import run as export_run

    logger.info("── Re-exporting site assets → %s ──", _DEFAULT_OUT)
    return export_run(_DEFAULT_OUT, None)


if __name__ == "__main__":
    raise SystemExit(main())
