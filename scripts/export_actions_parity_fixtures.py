"""Golden fixture for P1 (``tests/test_actions_parity.py``): the canonical action multiset.

Phase 0 of the actions/states migration (``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md``) adds
``ChainEdge.actions: tuple[ChainAction, ...]`` as a purely additive, backward-compatible
field — today's compiler still emits one action per edge. This script pins the multiset
``(action_key, actor, inferred) -> count`` produced by walking the App's own mock bundle through
``scripts.render_map_prototypes``'s existing corpus-walking pipeline (``partition_by_sequence``
+ ``_resolve_group`` + ``compile_two_sided`` — same convention ``build_aggregate`` uses, not a
new one). No OBSERVED action may appear or disappear across the ``actions[]`` migration; that
is the invariant, and splitting the tally by ``inferred`` is what makes it provable — the
inferred half is a rule output and MOVES by design when the rule changes (Fase 2 added three
inferred ``sweep`` occurrences here), the observed half must not move at all, ever.

    uv run python -m scripts.export_actions_parity_fixtures
    uv run python -m scripts.export_actions_parity_fixtures --check

Deterministic — no DB, no network, no clock.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.chain_compiler import compile_two_sided  # noqa: E402
from analysis.taxonomy_kind import load_inference_table  # noqa: E402
from scripts.render_map_prototypes import (  # noqa: E402
    _actor_of,
    _resolve_group,
    _side_of,
    partition_by_sequence,
)

MOCK_BUNDLE = (
    ROOT.parent / "GrapplingArcApp" / "src" / "data" / "mockData" / "mock_user_bundle.json"
)
OUT = ROOT / "data" / "rating" / "actions_parity_golden.json"


def action_multiset(bundle: dict[str, Any]) -> Counter[tuple[str, str, bool]]:
    """``(action_key, actor, inferred) -> count`` over every action occurrence in the compiled
    corpus.

    Walks ``edge.actions`` (never ``edge.action_key``) so this stays correct once an edge
    carries more than one action — the whole point of the P1 invariant. ``inferred`` is part of
    the key so an inference-rule change can never hide behind an observed action's count."""
    table = load_inference_table()
    tally: Counter[tuple[str, str, bool]] = Counter()
    for session in bundle.get("sessions", []):
        for round_ in session.get("rounds", []):
            entries = round_.get("entries", []) or []
            for group in partition_by_sequence(entries):
                resolved_group, _display = _resolve_group(group)
                compiled = compile_two_sided(resolved_group, _side_of, actor_of=_actor_of,
                                              inference_table=table)
                for side in ("a", "b"):
                    for edge in compiled[side].edges:
                        for action in edge.actions:
                            tally[(action.key, action.actor or "", action.inferred)] += 1
    return tally


def build_fixture() -> dict[str, Any]:
    bundle = json.loads(MOCK_BUNDLE.read_text(encoding="utf-8"))
    tally = action_multiset(bundle)
    rows = sorted(
        [{"action_key": k, "actor": a, "inferred": i, "count": c}
         for (k, a, i), c in tally.items()],
        key=lambda r: (r["action_key"], r["actor"], r["inferred"]),
    )
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_actions_parity_fixtures.py",
        "contract": "P1 — (action_key, actor, inferred) -> count; the inferred=false rows are "
                    "invariant across the whole actions[] migration, the inferred=true rows are "
                    "the inference rule's own output and move with it",
        "source_bundle": "GrapplingArcApp/src/data/mockData/mock_user_bundle.json",
        "multiset": rows,
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="não escreve; falha se o que está em disco divergir do gerado")
    args = ap.parse_args()

    text = render(build_fixture())
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"DIVERGENTE: {OUT}")
            return 1
        print("fixture em dia")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"escrito: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
