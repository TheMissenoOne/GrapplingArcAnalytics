"""Fixture dourada do bundling de caminhos (Fase 4, `docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`
§10). Espelho no App: `src/services/map/pathBundling.ts`.

Os casos são os CINCO do dono de `tests/test_path_bundling.py` mais as três regras que garantem
que o desenho não mente, mais o caso de AÇÃO REPETIDA — a única coisa que o dado contradisse no
plano da Fase 4: sem a recusa de fusão (`_Union._owners`), um caminho passava a cruzar a si
mesmo e o desenho licenciava 19 rotas que nunca aconteceram enquanto perdia 3 que aconteceram
(medido sobre o corpus público de 668 caminhos).

A saída de cada caso é o `BundledGraph` inteiro em ordem determinística — pontos, segmentos com
`path_ids` ordenados, `path_entry` — MAIS `walkable_routes()`, que é a prova de "nenhuma rota
inexistente" e o que um porte tem mais chance de errar em silêncio.

    uv run python -m scripts.export_path_bundling_fixtures
    uv run python -m scripts.export_path_bundling_fixtures --check

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

from analysis.path_bundling import BundledGraph, RenderPath, bundle_paths  # noqa: E402

ANALYTICS_OUT = ROOT / "data" / "rating" / "path_bundling_golden.json"
APP_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__"
    / "pathBundlingGolden.json"
)

#: ``(path_id, source, actions, target)`` — ``actor``/``count`` are constant across these cases
#: (the bundler never reads them; they ride through untouched) and stay out of the fixture's
#: signal-to-noise.
Spec = tuple[str, str, tuple[str, ...], str]

CASES: list[tuple[str, str, list[Spec]]] = [
    (
        "case1_shared_prefix_forks_after_the_common_run",
        "prefixo [1,2] compartilhado; a bifurcação acontece DEPOIS do 2",
        [("p1", "A", ("1", "2", "3"), "C"), ("p2", "A", ("1", "2", "4"), "D")],
    ),
    (
        "case2_internal_run_splits_where_the_path_set_changes",
        "[2,3] é a corrida interna de p1/p3, mas p2 também anda o 2 — a corrida RACHA onde o "
        "conjunto muda, que é a definição inteira de segmento",
        [("p1", "A", ("1", "2", "3"), "C"), ("p2", "A", ("1", "2", "4"), "D"),
         ("p3", "B", ("5", "2", "3"), "C")],
    ),
    (
        "case2_in_isolation_keeps_the_internal_run_whole",
        "sem p2 nada racha a corrida: [2,3] é UM segmento",
        [("p1", "A", ("1", "2", "3"), "C"), ("p3", "B", ("5", "2", "3"), "C")],
    ),
    (
        "case3_shared_suffix_converges_at_the_first_common_action",
        "sufixo [6] compartilhado; convergência no primeiro elemento comum",
        [("p1", "A", ("1", "3", "6"), "C"), ("p2", "B", ("4", "5", "6"), "C")],
    ),
    (
        "case4_same_actions_different_endpoints_never_collapse",
        "a armadilha do dono: mesmas ações, extremos diferentes ⇒ nada funde, e A->D / B->C "
        "não existem em walkable_routes",
        [("p1", "A", ("1", "2"), "C"), ("p2", "B", ("1", "2"), "D")],
    ),
    (
        "case5_nested_fork_inside_a_shared_trunk",
        "tronco de {p1,p2,p3} contendo um trecho só de {p1,p2} — o aninhamento cai de graça "
        "porque os conjuntos de path_id são subconjuntos um do outro",
        [("p1", "A", ("1", "2", "3", "4"), "C"), ("p2", "A", ("1", "2", "3", "5"), "D"),
         ("p3", "A", ("1", "2", "6"), "E")],
    ),
    (
        "rule2_an_internal_position_never_merges_into_a_state_point",
        "A--[1,2]-->C não pode ser redesenhado passando por X só porque A--[1]-->X existe — "
        "seria um estado inventado no meio da cadeia, exatamente o que a Fase 1 apagou",
        [("p1", "A", ("1", "2"), "C"), ("p2", "A", ("1",), "X")],
    ),
    (
        "rule3_a_branch_merge_point_licenses_no_crossed_route",
        "o caso duro: várias entradas E várias saídas num ponto. Os conjuntos de path_id são o "
        "que proíbe pegar a entrada de p3 e sair pela saída de p2",
        [("p1", "A", ("1", "2"), "C"), ("p2", "A", ("1", "3"), "C"),
         ("p3", "B", ("4", "2"), "C")],
    ),
    (
        "repeated_action_never_makes_a_path_cross_itself",
        "REGRESSÃO achada no corpus público, não inventada aqui: back take --[t,t,t,t]--> back "
        "take e o irmão de duas tentativas dividem prefixo E sufixo, e fundir os dois punha a "
        "1ª e a 3ª lacuna do caminho longo no MESMO ponto",
        [("p1", "A", ("t", "t", "t", "t"), "A"), ("p2", "A", ("t", "t"), "A"),
         ("p3", "A", ("t",), "A")],
    ),
    (
        "self_loop_path_still_reconstructs",
        "start top --[headquarters pass]--> start top, real no bundle do dono: as duas pontas "
        "são o mesmo ponto de estado, então não existe entrada de grau 0 para inferir",
        [("p1", "A", ("x",), "A"), ("p2", "A", ("x", "y"), "B")],
    ),
    (
        "run_anchored_at_neither_end_is_deliberately_not_bundled",
        "os dois andam um 2 no meio e ficam com o seu: duas cadeias que nem abrem nem fecham "
        "no mesmo lugar não dividem base, só reusam um verbo",
        [("p1", "A", ("1", "2", "3"), "C"), ("p2", "B", ("4", "2", "5"), "D")],
    ),
    (
        "identical_paths_share_one_stroke",
        "dois caminhos idênticos são UM traço — o ponto inteiro do bundling",
        [("p1", "A", ("1", "2"), "C"), ("p2", "A", ("1", "2"), "C")],
    ),
    (
        "a_single_path_is_untouched",
        "um caminho sozinho: um segmento, nenhum artefato",
        [("p1", "A", ("1", "2", "3"), "C")],
    ),
]


def _paths(specs: list[Spec]) -> list[RenderPath]:
    return [
        RenderPath(path_id=pid, source=src, target=tgt, actions=actions, actor="you", count=1)
        for pid, src, actions, tgt in specs
    ]


def _serialize(g: BundledGraph) -> dict[str, Any]:
    return {
        "points": [{"id": p.id, "kind": p.kind, "state_key": p.state_key} for p in g.points],
        "segments": [
            {
                "id": s.id,
                "actions": list(s.actions),
                "path_ids": sorted(s.path_ids),
                "from_point": s.from_point,
                "to_point": s.to_point,
            }
            for s in g.segments
        ],
        "path_entry": dict(sorted(g.path_entry.items())),
        # The "no phantom route" proof, and the half a port is most likely to get silently
        # wrong. Sorted so the file is a stable diff.
        "walkable_routes": sorted(
            [source, list(actions), target] for source, actions, target in g.walkable_routes()
        ),
    }


def build_fixture() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, why, specs in CASES:
        paths = _paths(specs)
        g = bundle_paths(paths)
        cases.append({
            "name": name,
            "why": why,
            "paths": [
                {"path_id": pid, "source": src, "actions": list(actions), "target": tgt}
                for pid, src, actions, tgt in specs
            ],
            "expected": _serialize(g),
            # Every input path comes back EXACTLY — the other half of the invariant.
            "reconstruct": {
                p.path_id: [
                    g.reconstruct(p.path_id)[0],
                    list(g.reconstruct(p.path_id)[1]),
                    g.reconstruct(p.path_id)[2],
                ]
                for p in paths
            },
            "segments_of": {
                p.path_id: [s.id for s in g.segments_of(p.path_id)] for p in paths
            },
        })
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_path_bundling_fixtures.py",
        "contract": (
            "bundle_paths(RenderPath[]) -> BundledGraph. A segment is the maximal contiguous "
            "run of actions walked by EXACTLY one set of paths; sharing is decided by canonical "
            "action-key equality on a common PREFIX or SUFFIX, transitively, and never by a "
            "free-standing k-gram. walkable_routes() must equal the input set — no invented "
            "route, none lost. App mirror: src/services/map/pathBundling.ts."
        ),
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
