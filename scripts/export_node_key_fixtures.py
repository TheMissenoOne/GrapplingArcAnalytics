#!/usr/bin/env python
"""Golden fixture for the node_key lookup contract — `_normalize_name` == App's `normalizeLabel`.

`export/app_node_scores.py` keys its output by this normalization (root CLAUDE.md: "Node
key" contract). `markovWeightsGolden.json` already pins the SEPARATE `_key`/`lamasKey`
normalization (de-accent first, used only inside the Lamas action mapping) — it does not
cover this one, which deletes an accented character rather than folding it. Five names,
drawn from `GrapplingArcApp/src/data/grappling-arch.nodes.json`, chosen to each hit a
different punctuation/accent/digit case a naive port could get wrong.

    uv run python -m scripts.export_node_key_fixtures
    uv run python -m scripts.export_node_key_fixtures --check

No DB, no network, no clock: reexecuting is byte-identical.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis.names import _normalize_name

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_OUT = ROOT / "data" / "rating" / "node_key_golden.json"
APP_OUT = ROOT.parent / "GrapplingArcApp" / "src" / "utils" / "__fixtures__" / "nodeKeyGolden.json"

#: Real library names (grappling-arch.nodes.json), one per punctuation/accent/digit case:
#: apostrophe, accented vowel, digit+hyphen, hyphen-only, parenthetical.
CASES: list[tuple[str, str]] = [
    ("apostrophe", "D'Arce"),
    ("accented_vowel", "Chave de Polícia"),
    ("digit_and_hyphen", "Guarda 50-50"),
    ("hyphen_only", "X-Pass"),
    ("parenthetical", "Chave de Pé (Toe Hold)"),
]


def build_fixture() -> dict[str, object]:
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_node_key_fixtures.py",
        "contract": "analysis.names._normalize_name(name) == normalizeLabel(name), char-for-char",
        "cases": [
            {"name": name, "case": case, "expected_key": _normalize_name(name)}
            for case, name in CASES
        ],
    }


def render(fixture: dict[str, object]) -> str:
    return json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="write nothing; fail if what's on disk diverges from the generated text")
    args = ap.parse_args()

    text = render(build_fixture())
    targets = [ANALYTICS_OUT, APP_OUT]
    if args.check:
        for path in targets:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"DIVERGENT: {path}")
                return 1
        print("fixtures up to date")
        return 0
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
