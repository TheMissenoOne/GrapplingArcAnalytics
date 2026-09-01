"""Fixture dourada das métricas de caminho (Fase 3, `docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`
§9). Espelho no App: `src/services/map/pathMetrics.ts`.

Os casos são os de `tests/test_path_metrics.py`, com `rating_of` SINTÉTICO — um dicionário
literal dentro do próprio caso, nunca um rating medido. Mesma razão que
`export_markov_weight_fixtures` dá para o seu bloco sintético: os números do dia mudam quando o
corpus muda, e uma fixture presa a eles falha a cada regeração sem que nenhuma implementação
tenha divergido. A fixture testa a MECÂNICA.

O que cada eixo pega num porte:

- `length`/`observed`/`observed_ratio` — que uma ação inferida CONTA no comprimento (ela anda na
  aresta) e não conta como observada;
- `strength` — que os pesos Markov são calculados sobre a tupla INTEIRA e só depois a média
  pondera o subconjunto observado-e-avaliado, com renormalização (senão a massa de peso excluída
  achata o resultado);
- `role_delta` — os dois eixos que `taxonomy_kind` mantém separados de propósito, e o
  `actor_is_opponent` como o único discriminador entre "Raspagem A" e "Inversão B".

    uv run python -m scripts.export_path_metrics_fixtures
    uv run python -m scripts.export_path_metrics_fixtures --check

Sem banco, sem rede, sem relógio: reexecutar produz byte-idêntico.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.chain_compiler import ChainAction, ChainEdge  # noqa: E402
from analysis.lamas_chain import STATES  # noqa: E402
from analysis.path_metrics import path_metrics  # noqa: E402

ANALYTICS_OUT = ROOT / "data" / "rating" / "path_metrics_golden.json"
APP_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__"
    / "pathMetricsGolden.json"
)

#: Bloco sintético, um peso distinto por estado de Lamas, para que nenhum par de códigos possa
#: trocar de lugar sem mudar `strength`. Mesma construção de `export_markov_weight_fixtures`.
SYNTHETIC_BLOCK: dict[str, float] = {
    code: round(0.25 + 0.15 * i, 4) for i, code in enumerate(STATES)
}


def _a(key: str, label: str, *, type_: str = "transition", actor: str | None = "a",
       inferred: bool = False, actor_is_opponent: bool = False) -> dict[str, Any]:
    return {"key": key, "label": label, "type": type_, "actor": actor, "inferred": inferred,
            "source_event_index": None, "actor_is_opponent": actor_is_opponent}


#: ``(name, why, source_key, target_key, actions, terminal, support, ratings, block)``
CASES: list[tuple[str, str, str, str, list[dict[str, Any]], bool, int,
                  dict[str, float], dict[str, float] | None]] = [
    (
        "length_observed_and_ratio",
        "uma ação inferida CONTA no comprimento e não conta como observada",
        "mount", "side control",
        [_a("a1", "A1"), _a("a2", "A2", inferred=True), _a("a3", "A3")],
        False, 7, {}, None,
    ),
    (
        "empty_path_has_ratio_zero",
        "aresta sem ação: 0/0 é 0.0, nunca uma divisão por zero",
        "mount", "side control", [], False, 1, {}, None,
    ),
    (
        "terminal_passes_through_from_the_edge",
        "terminal é campo de ARESTA, não de ação",
        "armlock", "finish", [_a("armlock", "Armlock", type_="submission")], True, 1, {}, None,
    ),
    (
        "strength_is_the_markov_weighted_mean_of_rated_observed_actions",
        "a inferida tem rating na tabela e ainda assim NÃO entra — nem no numerador nem na "
        "massa de peso; block=None deixa as shares uniformes, então a resposta é a média das "
        "duas observadas",
        "s", "t",
        [_a("k1", "L1"), _a("k2", "L2", inferred=True), _a("k3", "L3")],
        False, 1, {"k1": 1500.0, "k2": 9999.0, "k3": 1600.0}, None,
    ),
    (
        "strength_with_a_real_markov_block_is_not_the_plain_mean",
        "com um bloco de pesos de verdade a média deixa de ser aritmética — é este caso que "
        "pega um porte que ignorou `relative_shares`",
        "s", "t",
        [_a("armbar", "Armbar", type_="submission"),
         _a("single leg takedown", "Single Leg Takedown", type_="takedown")],
        False, 3, {"armbar": 1500.0, "single leg takedown": 1300.0}, SYNTHETIC_BLOCK,
    ),
    (
        "strength_is_none_when_nothing_is_rated",
        "nada qualifica ⇒ None, nunca 0.0 (que seria um rating de verdade)",
        "s", "t", [_a("k1", "L1"), _a("k2", "L2")], False, 1, {}, None,
    ),
    (
        "strength_is_none_when_every_action_is_inferred",
        "uma aresta inteiramente inferida não foi observada em lugar nenhum",
        "s", "t", [_a("k1", "L1", inferred=True)], False, 1, {"k1": 1234.0}, None,
    ),
    (
        "role_delta_none_when_both_ends_share_the_same_stance",
        "mount e side control são os dois `top`: mesmo eixo, mesmo lado",
        "mount", "side control", [_a("control transition", "Control Transition")],
        False, 1, {}, None,
    ),
    (
        "role_delta_same_actor_shift_on_a_topology_flip",
        "o 'Raspagem A' do modelo: o próprio dono da cadeia inverteu a própria posição",
        "closed guard", "mount", [_a("sweep", "Sweep", type_="sweep")], False, 1, {}, None,
    ),
    (
        "role_delta_inversion_when_an_action_is_attributed_to_the_opponent",
        "o 'Inversão B' do modelo — `actor_is_opponent` é o ÚNICO discriminador entre este "
        "caso e o de cima, e as duas arestas são idênticas em todo o resto",
        "closed guard", "mount",
        [_a("reversal", "Reversal", actor_is_opponent=True)], False, 1, {}, None,
    ),
    (
        "role_delta_unknown_when_an_endpoint_has_no_resolvable_stance",
        "'kimura grip' não resolve nem pela tabela declarada nem pela biblioteca, e o `type` do "
        "estado não sobrevive na aresta (§9.3) — degrada honestamente para unknown",
        "kimura grip", "mount", [_a("control transition", "Control Transition")],
        False, 1, {}, None,
    ),
]


def build_fixture() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for (name, why, source_key, target_key, actions, terminal, support,
         ratings, block) in CASES:
        edge = ChainEdge(
            source_key=source_key, target_key=target_key, terminal=terminal,
            actions=tuple(ChainAction(**a) for a in actions),
        )
        metrics = path_metrics(edge, support=support, rating_of=ratings.get, block=block)
        out = asdict(metrics)
        # Round only where a float can drift on a different FP path; `observed_ratio` and
        # `strength` are the only two.
        out["observed_ratio"] = round(out["observed_ratio"], 12)
        if out["strength"] is not None:
            out["strength"] = round(out["strength"], 9)
        cases.append({
            "name": name,
            "why": why,
            "edge": {"source_key": source_key, "target_key": target_key,
                     "terminal": terminal, "actions": actions},
            "support": support,
            "ratings": ratings,
            "block": block,
            "expected": out,
        })
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_path_metrics_fixtures.py",
        "contract": (
            "path_metrics(edge, support, rating_of, block) -> PathMetrics. `strength` weighs "
            "ONLY the observed, rated actions, by Markov mean-1 shares computed over the WHOLE "
            "action tuple and then renormalised over the qualifying subset. `role_delta` "
            "compares the two endpoint stances within ONE axis. App mirror: "
            "src/services/map/pathMetrics.ts."
        ),
        "lamas_states": list(STATES),
        "cases": cases,
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
