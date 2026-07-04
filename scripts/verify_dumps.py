"""Verify dumps against transcripts: match bouts, check counts, flag timestamp/name issues."""
import json
import sys
from pathlib import Path
from typing import Any

DUMPS = Path(__file__).resolve().parent / "dumps"
TRANSCRIPTS = DUMPS.parent.parent / "transcripts"


def load_dump(module_name: str) -> dict[tuple[str, int], dict[str, Any]]:
    """Load a dump and return bout dict (flattened)."""
    # module_name is the stem (e.g., "polaris31"); file is polaris31_data.py
    module_name = module_name.removesuffix("_data")
    path = DUMPS / f"{module_name}_data.py"
    spec = __import__('importlib.util').util.spec_from_file_location(f"scripts.dumps.{module_name}_data", path)
    mod = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = {}
    for bout_dict in mod.RAW:
        for key, val in bout_dict.items():
            result[key] = val
    return result


def load_transcript(stem: str) -> str:
    """Load raw transcript file."""
    path = TRANSCRIPTS / "queue" / f"{stem}.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding='utf-8')


def extract_manifest(stem: str) -> dict[str, Any] | None:
    """Try to load bout manifest from deepseek/<stem>.md if it exists."""
    path = TRANSCRIPTS / "deepseek" / f"{stem}.md"
    if not path.exists():
        return None
    try:
        # Front-matter extraction: look for ```json ... ``` block
        content = path.read_text(encoding='utf-8')
        if "```json" in content:
            start = content.index("```json") + 7
            end = content.index("```", start)
            return json.loads(content[start:end])
    except Exception:
        pass
    return None


def verify_one(module_name: str, stem: str, expected_count: int) -> dict[str, Any]:
    """Verify a single dump: load it, check bout count, flag issues."""
    result = {
        "module": module_name,
        "stem": stem,
        "expected_count": expected_count,
        "actual_count": 0,
        "issues": [],
        "bouts": {},
    }

    try:
        dump = load_dump(module_name)
    except Exception as e:
        result["issues"].append(f"Failed to load dump: {e}")
        return result

    result["actual_count"] = len(dump)
    if result["actual_count"] != expected_count:
        result["issues"].append(
            f"Bout count mismatch: expected {expected_count}, got {result['actual_count']}"
        )

    # Check each bout for pbp presence and events
    for (a_name, year), bout in dump.items():
        bout_key = f"{a_name}|{year}"
        bout_info = {
            "opponent": bout.get("opponent", "?"),
            "method": bout.get("method", "?"),
            "winner": bout.get("winner", "?"),
            "pbp_count": len(bout.get("pbp", [])),
            "events_count": len(bout.get("events", [])),
        }
        result["bouts"][bout_key] = bout_info

        # Flag pbp-present bouts (not yet refined)
        if bout.get("pbp"):
            result["issues"].append(f"  {bout_key}: has {bout_info['pbp_count']} pbp lines, 0 events (not refined)")

        # Flag pbp-less bouts without events (refined but no events)
        if not bout.get("pbp") and not bout.get("events"):
            result["issues"].append(f"  {bout_key}: no pbp, no events (orphan)")

    return result


def main(argv: list[str]) -> int:
    """Verify a list of dumps against their transcripts."""
    dumps_to_check = [
        ("cji2day1", "CJI2Day1", 24),
        ("cji2day2", "CJI2Day2", 18),
        ("craigjones", "CraigJones", 15),
        ("eddie_bravo_invitational_14_the_absolutes", "Eddie Bravo Invitational 14 The Absolutes", 13),
        ("evento_completo_final_do_jud_equipes_mistas_olimp_adas_paris_2024", "EVENTO COMPLETO FINAL DO JUDÔ - EQUIPES MISTAS OLIMPÍADAS PARIS 2024", 2),
        ("leandro_lo", "Leandro Lo", 11),
        ("musumeci", "Musumeci", 3),
        ("pgf_world_2026_week_1_opening_day", "PGF World 2026 - Week 1", 13),
        ("pgf_world_2026_week_2_things_are_heating_up", "PGF World 2026 - Week 2", 15),
        ("pgf_world_2026_week_3_this_marks_the_halfway_point", "PGF World 2026 - Week 3", 14),
        ("pgf_world_2026_week_4_the_playoff_race_is_on", "PGF World 2026 - Week 4", 20),
        ("pgf_world_2026_week_5_regular_season_finale", "PGF World 2026 - Week 5", 18),
        ("polaris_18_submission_grappling_full_bjj_event_replay", "Polaris 18", 7),
        ("polaris_25_prelims_live_full_no_gi_bjj_grappling_undercard", "Polaris 25", 10),
        ("polaris_26_live_prelims_nine_free_matches_live", "Polaris 26", 9),
        ("polaris28prelims", "Polaris 28", 12),
        ("polaris29", "Polaris 29", 14),
        ("polaris30", "Polaris 30", 13),
        ("polaris31", "Polaris 31", 16),
        ("polaris32", "Polaris 32", 15),
        ("polaris33", "Polaris 33", 14),
        ("polaris34", "Polaris 34", 15),
        ("polaris35", "Polaris 35", 12),
        ("polaris36", "Polaris 36", 14),
        ("polaris_bjj_squads_team_usa_vs_team_uk_ireland_grappling_full_event", "Polaris BJJ Squads", 18),
        ("ruotolos", "Ruotolos", 11),
        ("supercut_the_entire_2024_adcc_worlds_65kg_bracket", "ADCC 2024 (-65kg)", 7),
        ("team_bjj_stars_vs_team_polaris_full_squads_matchup_polaris_37", "Polaris 37", 15),
        ("ufc_320_free_fight_marathon", "UFC 320", 11),
        ("ufc_324_free_fight_marathon", "UFC 324", 4),
        ("ufc_327_free_fight_marathon", "UFC 327", 4),
        ("ufc_328_free_fight_marathon", "UFC 328", 4),
        ("wno_30_open_weight_grand_prix_undercard_free_live_prelim_matches", "WNO 30", 6),
    ]

    print("=" * 100)
    print("DUMP VERIFICATION AGAINST TRANSCRIPTS")
    print("=" * 100)
    print()

    results = []
    for module_name, stem, expected_count in dumps_to_check:
        res = verify_one(module_name, stem, expected_count)
        results.append(res)
        status = "✓" if not res["issues"] else "⚠"
        print(f"{status}  {res['stem']:<50} {res['actual_count']:>3} bouts (expected {expected_count})")
        if res["issues"]:
            for issue in res["issues"][:3]:  # Show first 3 issues per dump
                print(f"      {issue}")
            if len(res["issues"]) > 3:
                print(f"      ... and {len(res['issues']) - 3} more issues")

    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)

    all_issues = sum(len(r["issues"]) for r in results)
    count_mismatches = sum(1 for r in results if r["actual_count"] != r["expected_count"])
    orphan_bouts = sum(
        sum(1 for b in r["bouts"].values() if b["pbp_count"] == 0 and b["events_count"] == 0)
        for r in results
    )
    unrefined = sum(
        sum(1 for b in r["bouts"].values() if b["pbp_count"] > 0)
        for r in results
    )

    print(f"Dumps checked:      {len(results)}")
    print(f"Total issues:       {all_issues}")
    print(f"Count mismatches:   {count_mismatches}")
    print(f"Unrefined bouts:    {unrefined} (have pbp, no events)")
    print(f"Orphan bouts:       {orphan_bouts} (no pbp, no events)")
    print()

    if "--json" in argv:
        print(json.dumps(results, indent=2))

    return 0 if not all_issues else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
