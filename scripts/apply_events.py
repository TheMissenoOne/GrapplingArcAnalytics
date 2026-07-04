"""Apply refined events to dumps: read sidecar JSON, splice into module, drop pbp."""
import json
import pprint
import sys
from pathlib import Path

DUMPS = Path(__file__).resolve().parent / "dumps"


def apply_events(module_name: str, events_json_path: str) -> None:
    """Splice events from sidecar JSON into dump; drop pbp.

    Usage:
        uv run python -m scripts.apply_events <module_name> <path/to/events.json>
        uv run python -m scripts.apply_events polaris31_data transcripts/deepseek/polaris31_events.json
    """
    dump_path = DUMPS / f"{module_name}_data.py"
    events_path = Path(events_json_path)

    if not dump_path.exists():
        print(f"ERROR: dump {dump_path} not found")
        sys.exit(1)

    if not events_path.exists():
        print(f"ERROR: events file {events_path} not found")
        sys.exit(1)

    # Load module and events sidecar
    spec = __import__('importlib.util').util.spec_from_file_location(module_name, dump_path)
    mod = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with open(events_path, encoding='utf-8') as f:
        events_by_bout = json.load(f)  # {"<a>|<year>": [{label,type,actor,successful}], ...}

    # Splice events into RAW, drop pbp
    for bout_dict in mod.RAW:
        for (a_name, year), bout_data in bout_dict.items():
            key = f"{a_name}|{year}"
            if key in events_by_bout:
                bout_data["events"] = events_by_bout[key]
            bout_data.pop("pbp", None)

    # Rewrite dump with pprint (greppable)
    raw_str = pprint.pformat(mod.RAW, width=100, sort_dicts=False)
    header = f'''"""{events_path.stem} — auto-generated from transcript, refined."""
# ruff: noqa: E501
from __future__ import annotations
from typing import Any

RAW: list[dict[tuple[str, int], dict[str, Any]]] = {raw_str}
'''
    dump_path.write_text(header, encoding='utf-8')
    print(f"✓ Spliced events into {dump_path}")


def _check():
    """Self-check: round-trip one bout."""
    test_dump = DUMPS / "polaris31_data.py"
    if not test_dump.exists():
        print("(no polaris31_data.py for self-check; skipped)")
        return

    spec = __import__('importlib.util').util.spec_from_file_location("polaris31_data", test_dump)
    mod = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for bout_dict in mod.RAW:
        for (a, year), bd in bout_dict.items():
            assert isinstance(bd.get("winner"), str), f"winner not a string: {bd}"
            assert isinstance(bd.get("events"), list), f"events not a list: {bd}"
            assert "pbp" not in bd, f"pbp still in bout: {(a, year)}"
            print(f"✓ Check: {a} ({year}) — events={len(bd['events'])}, no pbp")
            return


if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    elif len(sys.argv) == 3:
        apply_events(sys.argv[1], sys.argv[2])
    else:
        print("Usage: uv run python -m scripts.apply_events <module_name> <events.json>")
        print("       uv run python -m scripts.apply_events --check")
        sys.exit(1)
