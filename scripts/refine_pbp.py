"""Semi-automated pbp refiner: keyword-match pbp text against technique library.

Output: scripts/dumps/<event>_events.json — sidecar ready for apply_events.py.

Usage:
    uv run python -m scripts.refine_pbp                     # all unrefined dumps
    uv run python -m scripts.refine_pbp --only polaris31     # single dump
    uv run python -m scripts.refine_pbp --dump               # show pbp per bout w/ matched events
"""
import json
import re
import sys
from pathlib import Path

DUMPS = Path(__file__).resolve().parent / "dumps"
LIB = Path(__file__).resolve().parent.parent / "analysis" / "data" / "technique_library.json"

# Load technique library
with open(LIB) as f:
    TECH_LIB: list[dict] = json.load(f)

# Build search index: (priority, label, type, variants_lower)
TECH_INDEX: list[tuple[str, str, str, list[str]]] = []
for t in TECH_LIB:
    en = t["en"]
    tp = t["type"]
    if tp in ("concept",):  # skip non-action concepts
        continue
    variants = [v.lower() for v in t.get("variants", [])]
    variants.append(en.lower())
    # also add common verb forms
    verbs = {
        f"going for the {en.lower()}",
        f"hunting the {en.lower()}",
        f"hunting for the {en.lower()}",
        f"looking for the {en.lower()}",
        f"locked in {en.lower()}",
        f"deep in the {en.lower()}",
        en.lower(),
    }
    for v in list(variants) + list(verbs):
        if len(v) > 2:
            TECH_INDEX.append((en, tp, v))

# Sort by variant length descending for longest-match-first
TECH_INDEX.sort(key=lambda x: -len(x[2]))

# High-confidence pbp → technique patterns.
# Only include patterns unlikely to fire on analyst chatter.
EXTRA_PATTERNS: list[tuple[str, str, str]] = [
    ("Guard Pull", "guard", "pull guard"),
    ("Guard Pull", "guard", "pulls guard"),
    ("Guard Pull", "guard", "guard pull"),
    ("Guard Pass", "pass", "pass the guard"),
    ("Guard Pass", "pass", "passes the guard"),
    ("Guard Pass", "pass", "completed the guard pass"),
    ("Guard Pass", "pass", "guard pass completed"),
    ("Sweep", "sweep", "sweeps to top"),
    ("Sweep", "sweep", "sweeps him over"),
    ("Sweep", "sweep", "sweep and"),
    ("Takedown", "takedown", "takedown!"),
    ("Heel Hook", "submission", "heel hook"),
    ("Heel Hook", "submission", "heel hook attempt"),
    ("Heel Hook", "submission", "heel hook deep"),
    ("Heel Hook", "submission", "heel hook locked"),
    ("Heel Hook", "submission", "heel hook finish"),
    ("Foot Lock", "submission", "foot lock"),
    ("Foot Lock", "submission", "foot lock attempt"),
    ("Ankle Lock", "submission", "ankle lock"),
    ("Toe Hold", "submission", "toe hold"),
    ("Knee Bar", "submission", "knee bar"),
    ("Straight Ankle Lock", "submission", "straight ankle lock"),
    ("Armbar", "submission", "armbar attempt"),
    ("Armbar", "submission", "armbar locked"),
    ("Armbar", "submission", "armbar deep"),
    ("Armbar", "submission", "for the armbar"),
    ("Armbar", "submission", "the armbar"),
    ("Triangle Choke", "submission", "triangle choke"),
    ("Triangle Choke", "submission", "for the triangle"),
    ("Triangle Choke", "submission", "triangle attempt"),
    ("Triangle Choke", "submission", "locked in the triangle"),
    ("Rear Naked Choke", "submission", "rear naked choke"),
    ("Rear Naked Choke", "submission", "r n c"),
    ("Guillotine Choke", "submission", "guillotine choke"),
    ("Guillotine Choke", "submission", "guillotine attempt"),
    ("Guillotine Choke", "submission", "for the guillotine"),
    ("Kimura", "submission", "kimura attempt"),
    ("Kimura", "submission", "for the kimura"),
    ("Kimura", "submission", "kimura grip"),
    ("Omoplata", "submission", "omoplata"),
    ("D'Arce Choke", "submission", "d'arce"),
    ("D'Arce Choke", "submission", "darce choke"),
    ("Anaconda Choke", "submission", "anaconda"),
    ("North-South Choke", "submission", "north south choke"),
    ("Bow and Arrow Choke", "submission", "bow and arrow"),
    ("Clock Choke", "submission", "clock choke"),
    ("Baseball Bat Choke", "submission", "baseball bat choke"),
    ("Back Take", "control", "takes the back"),
    ("Back Take", "control", "taking the back"),
    ("Back Take", "control", "took the back"),
    ("Back Take", "control", "take the back"),
    ("Back Take", "control", "takes his back"),
    ("Back Take", "control", "back take"),
    ("Back Take", "control", "back taken"),
    ("Sweep to Top", "sweep", "sweeps to top"),
    ("Stand Up", "escape", "stand up"),
    ("Front Headlock", "control", "front headlock"),
    ("Single Leg Takedown", "takedown", "single leg takedown"),
    ("Double Leg Takedown", "takedown", "double leg takedown"),
    ("Single Leg", "takedown", "single leg and"),
    ("Guillotine", "submission", "guillotine!"),
    ("Tap", "submission", r"\btaps\b"),
    ("Tap", "submission", "taps out"),
    ("Tap", "submission", r"\btapped\b"),
    ("Submission", "submission", "submits with"),
    ("Submission", "submission", "submitted via"),
    ("Closed Guard", "guard", "closed guard"),
    ("Half Guard", "guard", "half guard"),
    ("Deep Half", "guard", "deep half"),
    ("Butterfly Guard", "guard", "butterfly guard"),
    ("Butterfly Guard", "guard", "butterfly hooks"),
    ("Open Guard", "guard", "open guard"),
    ("X Guard", "guard", "x guard"),
    ("Single Leg X", "guard", "single leg x"),
    ("Spider Guard", "guard", "spider guard"),
    ("Lasso Guard", "guard", "lasso guard"),
    ("Side Control", "control", "side control"),
    ("Mount", "control", "mount position"),
    ("Mount", "control", "mount and"),
    ("Mount", "control", "gets mount"),
    ("Mount", "control", "takes mount"),
    ("Knee on Belly", "control", "knee on belly"),
    ("Knee on Belly", "control", "knee ride"),
    ("North South", "control", "north south"),
    ("Turtle", "control", "turtle position"),
    ("Turtle", "control", "goes to turtle"),
    ("Turtle", "control", "defends turtle"),
    ("Back Control", "control", "back control"),
    ("Back Control", "control", "back mount"),
    ("Body Lock", "control", "body lock"),
    ("Kimura Trap", "submission", "kimura trap"),
    ("Sweep", "sweep", "nice sweep"),
    ("Sweep", "sweep", "great sweep"),
    ("Sweep", "sweep", "swept him"),
    ("Guard Retention", "guard", "guard retention"),
    ("Guard Recovery", "guard", "guard recovery"),
    ("Guard Retention", "guard", "retains guard"),
    ("Reversal", "transition", "reversal!"),
    ("Estima Lock", "submission", "estima lock"),
]


def _fmt_ts(seconds: int) -> str:
    """Convert integer seconds to M:SS or H:MM:SS string matching existing convention."""
    if seconds < 0:
        seconds = 0
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def match_text(text: str) -> list[tuple[str, str, int]]:
    """Return [(label, type, match_start), ...] found in text."""
    text_lower = text.lower()
    results = []
    for label, tp, pattern in EXTRA_PATTERNS:
        m = re.search(pattern, text_lower)
        if m:
            results.append((label, tp, m.start()))
    for label, tp, variant in TECH_INDEX:
        if variant in text_lower:
            results.append((label, tp, text_lower.index(variant)))
    return results


def _clean_name(raw: str) -> str:
    """Strip parenthetical qualifiers like '(Quintet 3)', '(Main Event)' from names."""
    return re.sub(r'\s*\(.*?\)\s*', '', raw).strip()


def resolve_actor(text: str, a_name: str, opponent: str) -> str | None:
    """Heuristic: who is the subject of this pbp line?"""
    name_lower = a_name.lower()
    opp_clean = _clean_name(opponent)
    opp_lower = opp_clean.lower()
    text_lower = text.lower()

    # Direct name mentions
    a_parts = name_lower.split()
    opp_parts = opp_lower.split()
    a_first = a_parts[0] if a_parts else ""
    opp_first = opp_parts[0] if opp_parts else ""

    # Check for name mentions — prefer opponent if both match
    has_opp_name = any(p in text_lower for p in opp_parts if len(p) > 2) or (opp_first and opp_first in text_lower)
    has_a_name = any(p in text_lower for p in a_parts if len(p) > 2) or (a_first and a_first in text_lower)

    # Directional hints
    hints_opponent = {"the red corner", "his opponent", "opponent"}
    hints_athlete = {"the blue corner", name_lower.split()[-1] if len(name_lower.split()) > 1 else ""}

    # "by <actor>" pattern
    by_match = re.search(r'\bby\s+(\w+)', text_lower)
    if by_match:
        name = by_match.group(1).lower()
        if name in name_lower or (len(a_parts) > 1 and name == a_parts[-1].lower()):
            return a_name
        if name in opp_lower or (len(opp_parts) > 1 and name == opp_parts[-1].lower()):
            return opponent

    # "submission from <actor>"
    for_prefix = re.search(r'(?:from|for|by|of)\s+(\w+(?:\s+\w+)?)\s', text_lower)
    if for_prefix:
        candidate = for_prefix.group(1).lower()
        if candidate and candidate != '':
            for full, parts in [(a_name, a_parts), (opponent, opp_parts)]:
                if candidate in ' '.join(parts) or (len(parts) > 1 and candidate == parts[-1].lower()):
                    return full

    if has_a_name and not has_opp_name:
        return a_name
    if has_opp_name and not has_a_name:
        return opponent

    # If both or neither, guess by pronouns
    if re.search(r'\b(he|his|him)\b', text_lower):
        # Could be either — default to athlete
        pass

    # Default: check if text sounds like athlete acting or opponent acting
    defense_words = r"\b(defend|defends|defending|escapes|escaped|avoids|avoids)\b"
    if re.search(defense_words, text_lower):
        # Defending action — default to athlete if no other hints
        return a_name

    return a_name


def refine_dump(dump_name: str, dump_pbp: bool = False) -> None | dict:
    """Process one dump, return events dict or None."""
    mod_path = DUMPS / f"{dump_name}_data.py"
    if not mod_path.exists():
        print(f"SKIP {dump_name} — not found")
        return None

    spec = __import__("importlib.util").util.spec_from_file_location(dump_name, mod_path)
    mod = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    events_by_bout: dict[str, list[dict]] = {}

    for bd in mod.RAW:
        for (a_name, year), v in bd.items():
            pbp = v.get("pbp", [])
            opponent = v.get("opponent", "")
            key = f"{a_name}|{opponent}|{year}"

            if not pbp:
                events_by_bout[key] = []
                continue

            if dump_pbp:
                print(f"\n=== {a_name} vs {opponent} ({year}) ===")
                for p in pbp:
                    matches = match_text(p["text"])
                    match_str = ", ".join(f"{l}[{tp}]" for l, tp, _ in matches) if matches else ""
                    print(f"  ts={p['ts']:>5} | {p['text'][:100]}")
                    if match_str:
                        print(f"         → {match_str}")
                continue

            # Process pbp → events
            events: list[dict] = []
            seen_actions: dict[str, int] = {}  # label_lower → total matching lines

            for p in pbp:
                text = p["text"]
                ts = p["ts"]
                matches = match_text(text)

                if not matches:
                    continue

                # Group: if multiple matches within 15s of a prior event on same label, skip (dedup)
                for label, tp, _ in matches:
                    dedup_key = label.lower()
                    # Check if we already have an event for this action within 15s
                    recent = any(
                        abs(ts - e.get("_ts", 0)) < 15 and e.get("label", "").lower() == dedup_key
                        for e in events[-3:]  # only check last 3
                    )
                    if recent:
                        continue

                    actor = resolve_actor(text, a_name, opponent)
                    if actor is None:
                        continue

                    successful = None
                    completion_words = {"completed", "locked in", "taps", "tap", "finishes",
                                        "finish", "submits", "submitted", "swept", "passes", "passed"}
                    attempt_words = {"attempt", "trying", "hunting", "looking for", "searching for"}

                    text_lower = text.lower()
                    is_completion = any(w in text_lower for w in completion_words)
                    is_attempt = any(w in text_lower for w in attempt_words)

                    if is_completion:
                        successful = True
                    elif is_attempt:
                        successful = False

                    event = {
                        "label": label,
                        "type": tp,
                        "actor": actor,
                        "timestamp": _fmt_ts(ts),
                        "_ts": ts,  # internal: dedup + sort key
                    }
                    if successful is not None:
                        event["successful"] = successful

                    events.append(event)

            # Final dedup: consecutive events with same label within 20s → keep first
            deduped = []
            for e in events:
                if deduped and e["label"] == deduped[-1]["label"] and abs(e["_ts"] - deduped[-1]["_ts"]) < 20:
                    continue
                deduped.append(e)

            # Sort by ts
            deduped.sort(key=lambda x: x["_ts"])
            # Strip internal key
            for e in deduped:
                e.pop("_ts", None)

            events_by_bout[key] = deduped

    return events_by_bout


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, help="single dump name (without _data)")
    ap.add_argument("--dump", action="store_true", help="dump pbp with match annotations, no output")
    args = ap.parse_args()

    # 31 READY dumps
    READY = [
        "cji2day1", "cji2day2", "craigjones", "eddie_bravo_invitational_14_the_absolutes",
        "leandro_lo", "musumeci",
        "pgf_world_2026_week_1_opening_day", "pgf_world_2026_week_2_things_are_heating_up",
        "pgf_world_2026_week_3_this_marks_the_halfway_point",
        "pgf_world_2026_week_4_the_playoff_race_is_on",
        "pgf_world_2026_week_5_regular_season_finale",
        "polaris28prelims", "polaris29", "polaris30", "polaris31", "polaris32", "polaris33",
        "polaris34", "polaris35", "polaris36",
        "polaris_18_submission_grappling_full_bjj_event_replay",
        "polaris_25_prelims_live_full_no_gi_bjj_grappling_undercard",
        "polaris_26_live_prelims_nine_free_matches_live",
        "polaris_bjj_squads_team_usa_vs_team_uk_ireland_grappling_full_event",
        "ruotolos", "supercut_the_entire_2024_adcc_worlds_65kg_bracket",
        "team_bjj_stars_vs_team_polaris_full_squads_matchup_polaris_37",
        "ufc_324_free_fight_marathon", "ufc_327_free_fight_marathon",
        "ufc_328_free_fight_marathon",
        "wno_30_open_weight_grand_prix_undercard_free_live_prelim_matches",
    ]
    LOW = [
        "ufc_320_free_fight_marathon",
        "evento_completo_final_do_jud_equipes_mistas_olimp_adas_paris_2024",
    ]

    targets = [args.only] if args.only else READY + LOW

    for name in targets:
        if args.dump:
            refine_dump(name, dump_pbp=True)
            continue

        print(f"\n── Refining {name} ──")
        result = refine_dump(name)
        if result is None:
            continue

        total_events = sum(len(v) for v in result.values())
        print(f"  → {total_events} events across {len(result)} bouts")

        if total_events == 0:
            print(f"  SKIP: no events found for {name}")
            continue

        # Write sidecar
        out_path = DUMPS / f"{name}_events.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"  → wrote {out_path}")

        # Splice into dump
        import subprocess
        subprocess.run(
            ["uv", "run", "python", "-m", "scripts.apply_events", name, str(out_path)],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent,
        )
        print(f"  → spliced into dump")

    if not args.only and not args.dump:
        print("\n✓ All unrefined dumps processed")


if __name__ == "__main__":
    main()
