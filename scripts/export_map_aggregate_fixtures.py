"""Fixture dourada do AGREGADO do mapa — o grafo deduplicado sobre o mock bundle do App.
Espelho no App: `src/services/map/mapAggregate.ts` (`buildMapAggregate`).

`export_actions_parity_fixtures` já trava o multiconjunto de OCORRÊNCIAS de ação (P1). Esta trava
a camada de cima, que é onde moram três decisões que P1 não vê:

1. **A chave de deduplicação é a SEQUÊNCIA INTEIRA** de ações, nunca `actions[0]`. Duas
   travessias de Guarda Fechada para Montada com trilhas diferentes são duas linhas; com a mesma
   trilha, uma linha de contagem 2. Um porte que dedupou por `actions[0]` passa em P1 e falha
   aqui.
2. **Perspectiva** (`_perspective_key` + `_actor_for`): as âncoras orientadas são lidas SEMPRE do
   lado do usuário, então uma cadeia do oponente que abre numa passagem nomeia `start top` e o
   agregado a espelha para `start bottom`. É a única inversão do produto e ela não pode viver no
   compilador.
3. **Handovers**: derivados do fluxo BRUTO, intercalado por ator — o compilador nunca vê essa
   troca, só a lista original de eventos vê.

A entrada é o `mock_user_bundle.json` do PRÓPRIO App, que os dois repositórios leem.

    uv run python -m scripts.export_map_aggregate_fixtures
    uv run python -m scripts.export_map_aggregate_fixtures --check

Sem banco, sem rede, sem relógio: reexecutar produz byte-idêntico.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.render_map_prototypes import build_aggregate  # noqa: E402

MOCK_BUNDLE = (
    ROOT.parent / "GrapplingArcApp" / "src" / "data" / "mockData" / "mock_user_bundle.json"
)
ANALYTICS_OUT = ROOT / "data" / "rating" / "map_aggregate_golden.json"
APP_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__"
    / "mapAggregateGolden.json"
)


def build_fixture() -> dict[str, Any]:
    bundle = json.loads(MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)

    states = sorted(
        (
            {
                "node_key": row["node_key"], "actor": row["actor"], "type": row["type"],
                "count": row["count"], "inferred": row["inferred"], "nascent": row["nascent"],
            }
            for row in agg.states.values()
        ),
        key=lambda r: (r["node_key"], r["actor"]),
    )
    # `label` is deliberately OUT of the comparison: it is the OWNER's own logged wording, which
    # the two repos reach through different display-label paths, and pinning it would turn a
    # presentation choice into a contract.
    edges = sorted(
        (
            {
                "source": row["source"], "target": row["target"], "actor": row["actor"],
                "actions": list(row["actions"]),
                "action_inferred": list(row["action_inferred"]),
                "count": row["count"], "inferred": row["inferred"],
            }
            for row in agg.edges.values()
        ),
        key=lambda r: (r["source"], r["target"], r["actions"], r["actor"]),
    )
    handovers = sorted(
        (
            {
                "from": row["from"], "to": row["to"], "from_actor": row["from_actor"],
                "from_key": row["from_key"], "to_actor": row["to_actor"],
                "to_key": row["to_key"], "count": row["count"],
            }
            for row in agg.handovers.values()
        ),
        key=lambda r: (r["from"], r["to"]),
    )
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_map_aggregate_fixtures.py",
        "contract": (
            "build_aggregate(bundle) -> deduped states/edges/handovers. The edge identity is "
            "(source, target, WHOLE action sequence, actor); `inferred` is true only while every "
            "occurrence folded in was wholly inferred. `label` is excluded on purpose — it is "
            "the owner's own wording, a presentation choice, not a contract. App mirror: "
            "src/services/map/mapAggregate.ts."
        ),
        "source_bundle": "GrapplingArcApp/src/data/mockData/mock_user_bundle.json",
        "states": states,
        "edges": edges,
        "handovers": handovers,
    }


def render(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="não escreve; falha se o que está em disco divergir do gerado")
    args = ap.parse_args()

    text = render(build_fixture())
    targets = [ANALYTICS_OUT, APP_OUT]
    if args.check:
        for path in targets:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"DIVERGENTE: {path}")
                return 1
        print("fixtures em dia")
        return 0
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"escrito: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
