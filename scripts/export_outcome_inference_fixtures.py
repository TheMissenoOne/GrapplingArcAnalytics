"""Golden fixture for `analysis.outcome_inference` — for the App to mirror byte-identically,
same convention as every other cross-repo golden (`docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`'s
goldens table). One case per `(prev, action, next, actor)` -> the shipped composition's outcome
(`analysis.outcome_inference.DEFAULT_RULES` = R0+R1+R2 — R3 is measured, on the record, and
excluded from the default; see `docs/taxonomy/05_INFERENCIA_DE_RESULTADO.md`).

    uv run python -m scripts.export_outcome_inference_fixtures
    uv run python -m scripts.export_outcome_inference_fixtures --check

No DB, no network, no clock: reruns are byte-identical.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.outcome_inference import (  # noqa: E402
    DEFAULT_RULES,
    ActionRef,
    StateRef,
    infer_outcome,
)

GOLDEN_PATH = ROOT / "data" / "fixtures" / "outcomeInferenceGolden.json"


def _state(ref: StateRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {"label": ref.label, "type": ref.type, "actor": ref.actor}


# Hand-picked, one per rule this module carries plus the owner's own pinned worked examples and
# the "no information" refusals — not an exhaustive corpus walk, a contract pin.
_CASES: tuple[tuple[StateRef | None, ActionRef, StateRef | None, str | None, bool, bool], ...] = (
    # (prev, action, next, actor, terminal, actor_readable)
    # Owner's own worked examples (prereg §D2), English then pt-BR.
    (StateRef("Half Guard", "guard", "you"), ActionRef("Sweep", "sweep"),
     StateRef("Mount", "control", "you"), "you", False, True),
    (StateRef("Back Control", "control", "you"), ActionRef("RNC", "submission"),
     StateRef("Back Control", "control", "you"), "you", False, True),
    (StateRef("Meia Guarda", "guard", "you"), ActionRef("Raspagem", "sweep"),
     StateRef("Montada", "control", "you"), "you", False, True),
    (StateRef("Costas", "control", "you"), ActionRef("Mata Leao", "submission"),
     StateRef("Costas", "control", "you"), "you", False, True),
    # R0 — exit-orientation table.
    (None, ActionRef("Armlock", "submission"), None, "you", False, True),
    (None, ActionRef("Armlock", "submission"), None, "you", True, True),
    (None, ActionRef("Stand-up Escape", "escape"), StateRef("Closed Guard", "guard", "you"),
     "you", False, True),
    # R1 — orientation flip, sweep/reversal.
    (StateRef("Half Guard", "guard", "you"), ActionRef("Guard Pass", "pass"),
     StateRef("Mount", "control", "you"), "you", False, True),
    (StateRef("Half Guard", "guard", "you"), ActionRef("Sweep", "sweep"),
     StateRef("Closed Guard", "guard", "you"), "you", False, True),
    # R2 — type -> expected next-state family.
    (None, ActionRef("Guard Pull", "guard"), StateRef("Closed Guard", "guard", "you"),
     "you", False, True),
    (None, ActionRef("Back Take", "transition"), StateRef("Back Control", "control", "you"),
     "you", False, True),
    (None, ActionRef("Back Take", "transition"), StateRef("Mount", "control", "you"),
     "you", False, True),
    (None, ActionRef("Stand-up Escape", "escape"), StateRef("Standing", "transition", "you"),
     "you", False, True),
    (None, ActionRef("Armlock", "submission"), None, "you", False, True),
    # No information at all -> None, even with every rule enabled.
    (None, ActionRef("Something Unmapped", "transition"),
     StateRef("Standing", "transition", "you"), "you", False, True),
)


def build_fixture() -> dict[str, Any]:
    cases = []
    for prev, action, next_, actor, terminal, actor_readable in _CASES:
        cases.append({
            "prev": _state(prev),
            "action": {"label": action.label, "type": action.type},
            "next": _state(next_),
            "actor": actor,
            "terminal": terminal,
            "actor_readable": actor_readable,
            "outcome": infer_outcome(prev, action, next_, actor, rules=DEFAULT_RULES,
                                      terminal=terminal, actor_readable=actor_readable),
        })
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_outcome_inference_fixtures.py",
        "contract": (
            "infer_outcome(prev, action, next, actor, terminal=False, actor_readable=True) -> "
            "True|False|None under DEFAULT_RULES (R0 exit-orientation table, R1 orientation "
            "flip for sweep/reversal, R2 type -> expected next-state family), first non-None "
            "rule wins. `terminal` mirrors chain_compiler.ChainEdge.terminal. R3 (actor "
            "consistency) is defined in the source but excluded from DEFAULT_RULES — measured "
            "below today's precision floor, docs/taxonomy/05_INFERENCIA_DE_RESULTADO.md."
        ),
        "default_rules": [r.__name__ for r in DEFAULT_RULES],
        "cases": cases,
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="do not write; fail if disk content diverges from the generated one")
    args = ap.parse_args()

    text = render(build_fixture())
    if args.check:
        if not GOLDEN_PATH.is_file() or GOLDEN_PATH.read_text(encoding="utf-8") != text:
            print(f"STALE: {GOLDEN_PATH}")
            return 1
        print("fixture up to date")
        return 0
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(text, encoding="utf-8")
    print(f"written: {GOLDEN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
