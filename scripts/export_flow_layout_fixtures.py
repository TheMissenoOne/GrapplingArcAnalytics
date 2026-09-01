"""Fixture dourada do layout de fluxo (Fase 4/5, `docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`
§10.5). Espelho no App: `src/services/map/flowLayout.ts`.

`analysis/flow_layout.py` é o algoritmo do PRÓPRIO App (`decisionFlowLayout.ts`) reescrito em
Python — rank por BFS multi-fonte com visited-set (ciclos são reais num mapa técnico: uma aresta
de volta é PULADA, nunca re-ranqueada), ordenação determinística dentro do rank, `x` do rank, e
as âncoras bolt-on numa elipse que emoldura o grid em vez de disputarem uma vaga nele. Voltar
para o TS sem uma fixture é copiar duas vezes e torcer.

Quatro fixtures, e cada uma existe por uma classe de erro:

- `linear` — a cadeia trivial; pega um porte que errou a direção ou o espaçamento (`FLOW_RANK_GAP`
  / `FLOW_ROW_GAP`);
- `fork_merge` — bifurca e reconverge; é onde as duas varreduras de baricentro (`sorted` numa,
  `reversed` na outra; `neighbours_in` numa, `neighbours_out` na outra) precisam bater passo a
  passo, e onde um desempate por id diferente já muda tudo;
- `cycle` — uma volta fechada, SEM nenhum ponto de grau de entrada 0. `flow_ranks` cai na sua
  própria semente (`sorted(points)[0]`); um porte com `while` ingênuo trava aqui;
- `anchored` — âncoras nas duas pontas, que é o caso real: elas não tomam vaga no grid mas a
  linha delas CONTA na baricentragem (deixá-las de fora foi medido em 76 cruzamentos sobre 42
  links no bundle do dono), e depois são parafusadas nos vértices da estrutura.

Cada caso roda nas TRÊS estruturas de âncora (`pentagono`/`losango`/`triangulo`) para travar
`ANCHOR_STRUCTURES` junto — a tabela é parte do contrato, não decoração.

Cada caso carrega também um `label_len` (contagem de caracteres por ponto E por segmento, o
mesmo mapa que a tela passa): é ele que liga as duas etapas novas de 2026-09-01 — a compactação
de aspecto e a relaxação de bolhas de rótulo. Sem ele um porte poderia pular as duas e ainda
passar, que é exatamente o modo de falha que uma golden existe para pegar. O bloco `bubbles`
grava as meias-extensões que `label_half_extent` produz, para que uma divergência de FÓRMULA
apareça como fórmula e não como posição.

A entrada da fixture é o `BundledGraph` JÁ montado (pontos + segmentos), não os `RenderPath`:
`bundle_paths` tem sua própria fixture (`export_path_bundling_fixtures`), e acoplar as duas faria
uma falha de bundling parecer uma falha de layout.

    uv run python -m scripts.export_flow_layout_fixtures
    uv run python -m scripts.export_flow_layout_fixtures --check

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

from analysis.flow_layout import (  # noqa: E402
    ANCHOR_STRUCTURES,
    DEFAULT_ANCHOR_STRUCTURE,
    FLOW_ANCHOR_ROW_SPREAD,
    FLOW_ANCHOR_RX_SHARE,
    FLOW_ANCHOR_RY_SHARE,
    FLOW_BARYCENTRE_SWEEPS,
    FLOW_COMPACT_MIN,
    FLOW_LABEL_CHAR,
    FLOW_LABEL_EM,
    FLOW_LABEL_PAD_X,
    FLOW_LABEL_PAD_Y,
    FLOW_NODE_RADIUS,
    FLOW_RANK_GAP,
    FLOW_RELAX_MAX_BUBBLES,
    FLOW_RELAX_PULL,
    FLOW_RELAX_PUSH,
    FLOW_RELAX_ROUNDS,
    FLOW_RELAX_SLACK,
    FLOW_ROW_GAP,
    FLOW_TARGET_ASPECT,
    anchor_units,
    flow_layout,
    flow_ranks,
    label_half_extent,
)
from analysis.path_bundling import RenderPath, bundle_paths  # noqa: E402


def _r(value: float, places: int = 9) -> float:
    """Round, and collapse negative zero. `-0.0` is a real product of `cos(180deg)` here, and
    JS `Object.is(-0, 0)` is FALSE — a golden carrying it fails a JS deep-equal for a difference
    that does not exist."""
    rounded = round(value, places)
    return rounded if rounded else 0.0


ANALYTICS_OUT = ROOT / "data" / "rating" / "flow_layout_golden.json"
APP_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__"
    / "flowLayoutGolden.json"
)

#: ``(path_id, source, actions, target)``
Spec = tuple[str, str, tuple[str, ...], str]

#: ``(name, why, paths, anchor node_keys)`` — an anchor entry maps a STATE key to its slot name
#: (`neutral`/`top`/`bottom`/`finish_you`/`finish_opp`, resolved per structure below, exactly as
#: `render_map_prototypes._anchor_slot` does).
CASES: list[tuple[str, str, list[Spec], dict[str, str]]] = [
    (
        "linear",
        "cadeia trivial A->B->C: ranks 0,1,2 e uma linha só",
        [("p1", "A", ("1",), "B"), ("p2", "B", ("2",), "C")],
        {},
    ),
    (
        "fork_merge",
        "bifurca em A e reconverge em D — onde as duas varreduras de baricentro têm de bater",
        [("p1", "A", ("1",), "B"), ("p2", "A", ("2",), "C"),
         ("p3", "B", ("3",), "D"), ("p4", "C", ("4",), "D")],
        {},
    ),
    (
        "cycle",
        "volta fechada A->B->C->A: NENHUM ponto tem grau de entrada 0, então flow_ranks cai na "
        "própria semente e o visited-set é o que impede o laço infinito",
        [("p1", "A", ("1",), "B"), ("p2", "B", ("2",), "C"), ("p3", "C", ("3",), "A")],
        {},
    ),
    (
        "anchored",
        "o caso real: âncoras nas DUAS pontas. Elas não tomam vaga no grid mas a linha delas "
        "conta na baricentragem, e só depois são parafusadas nos vértices da estrutura",
        [
            ("p1", "start neutral", ("takedown",), "mount"),
            ("p2", "mount", ("armbar",), "finish"),
            ("p3", "start bottom", ("sweep",), "mount"),
            ("p4", "closed guard", ("kimura", "armbar"), "finish"),
            ("p5", "start neutral", ("guard pull",), "closed guard"),
        ],
        {"start neutral": "neutral", "start bottom": "bottom", "start top": "top",
         "finish": "finish_you"},
    ),
    (
        "crowded",
        "rótulos LONGOS em ranks vizinhos: sem a relaxação as caixas se cobrem mesmo com os "
        "pontos a 300 unidades de distância — é o defeito que o dono viu na captura, e o único "
        "caso em que a compactação de aspecto e as duas passadas de relaxação realmente pesam",
        [
            ("p1", "start neutral", ("double leg takedown",), "side control"),
            ("p2", "start neutral", ("guard pull",), "de la riva guard"),
            ("p3", "de la riva guard", ("berimbolo", "back take"), "back control"),
            ("p4", "side control", ("knee on belly", "mount transition"), "mounted position"),
            ("p5", "mounted position", ("americana",), "finish"),
            ("p6", "back control", ("rear naked choke",), "finish"),
            ("p7", "start bottom", ("scissor sweep",), "mounted position"),
        ],
        {"start neutral": "neutral", "start bottom": "bottom", "finish": "finish_you"},
    ),
]


def _slot_for(base_slot: str, structure: str) -> str:
    """`render_map_prototypes._anchor_slot`'s structure-dependent half: a structure with a
    UNIFIED finish folds both per-actor finish vertices onto one."""
    if base_slot in ("finish_you", "finish_opp"):
        return "finish" if ANCHOR_STRUCTURES[structure]["unified_finish"] else base_slot
    return base_slot


def build_fixture() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, why, specs, anchors in CASES:
        paths = [
            RenderPath(path_id=pid, source=src, target=tgt, actions=actions,
                       actor="you", count=1)
            for pid, src, actions, tgt in specs
        ]
        bundled = bundle_paths(paths)

        # `weight` mirrors the prototype's own: each segment's weight added to BOTH endpoints.
        # Here every path has count 1, so a segment weighs its own path count — enough to make
        # the "support desc, then id" initial sort real without dragging `_segment_weight` in.
        weight: dict[str, float] = {}
        for seg in bundled.segments:
            w = float(len(seg.path_ids))
            weight[seg.from_point] = weight.get(seg.from_point, 0.0) + w
            weight[seg.to_point] = weight.get(seg.to_point, 0.0) + w

        out_of: dict[str, list[str]] = {}
        in_of: dict[str, list[str]] = {}
        for seg in bundled.segments:
            out_of.setdefault(seg.from_point, []).append(seg.to_point)
            in_of.setdefault(seg.to_point, []).append(seg.from_point)
        ids = [p.id for p in bundled.points]
        ranks = flow_ranks(ids, out_of, {p: len(in_of.get(p, [])) for p in ids})

        # The label the renderer would draw: a state's own name, a segment's joined action
        # sequence. Title-cased so the count is the DRAWN one, not the canonical key's.
        label_len: dict[str, int] = {}
        for pt in bundled.points:
            if pt.state_key is not None:
                label_len[pt.id] = len(pt.state_key.title())
        for seg in bundled.segments:
            label_len[seg.id] = len(" → ".join(a.title() for a in seg.actions))
        bubbles = {
            "points": {pid: [_r(hw), _r(hh)] for pid, (hw, hh) in sorted(
                (p.id, label_half_extent(label_len.get(p.id, 0), node=True))
                for p in bundled.points)},
            "segments": {sid: [_r(hw), _r(hh)] for sid, (hw, hh) in sorted(
                (s.id, label_half_extent(label_len.get(s.id, 0), node=False))
                for s in bundled.segments)},
        }

        per_structure: dict[str, Any] = {}
        for structure in sorted(ANCHOR_STRUCTURES):
            anchor_slots = {
                f"s:{state}": _slot_for(slot, structure)
                for state, slot in anchors.items()
                if any(p.state_key == state for p in bundled.points)
            }
            pos = flow_layout(bundled, structure=structure, anchor_slots=anchor_slots,
                              weight=weight, label_len=label_len)
            per_structure[structure] = {
                "anchor_slots": dict(sorted(anchor_slots.items())),
                "positions": {
                    pid: [_r(x), _r(y)] for pid, (x, y) in sorted(pos.items())
                },
            }

        cases.append({
            "name": name,
            "why": why,
            "paths": [
                {"path_id": pid, "source": src, "actions": list(actions), "target": tgt}
                for pid, src, actions, tgt in specs
            ],
            "bundled": {
                "points": [{"id": p.id, "kind": p.kind, "state_key": p.state_key}
                            for p in bundled.points],
                "segments": [{"id": s.id, "actions": list(s.actions),
                               "path_ids": sorted(s.path_ids),
                               "from_point": s.from_point, "to_point": s.to_point}
                              for s in bundled.segments],
            },
            "weight": dict(sorted(weight.items())),
            "label_len": dict(sorted(label_len.items())),
            "bubbles": bubbles,
            "ranks": dict(sorted(ranks.items())),
            "structures": per_structure,
        })

    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_flow_layout_fixtures.py",
        "contract": (
            "flow_layout(bundled, structure, anchor_slots, weight) -> point id -> (x, y). "
            "Ranks by multi-source BFS with a visited set (a back edge is SKIPPED, never "
            "re-ranked); anchors never take a grid slot but their row counts in the barycentre "
            "sweeps, then they are bolted onto the structure's vertices on an ellipse sized to "
            "the grid. App mirror: src/services/map/flowLayout.ts."
        ),
        "constants": {
            "FLOW_RANK_GAP": FLOW_RANK_GAP,
            "FLOW_ROW_GAP": FLOW_ROW_GAP,
            "FLOW_BARYCENTRE_SWEEPS": FLOW_BARYCENTRE_SWEEPS,
            "FLOW_ANCHOR_RX_SHARE": FLOW_ANCHOR_RX_SHARE,
            "FLOW_ANCHOR_RY_SHARE": FLOW_ANCHOR_RY_SHARE,
            "FLOW_ANCHOR_ROW_SPREAD": FLOW_ANCHOR_ROW_SPREAD,
            "FLOW_LABEL_EM": FLOW_LABEL_EM,
            "FLOW_LABEL_CHAR": FLOW_LABEL_CHAR,
            "FLOW_LABEL_PAD_X": FLOW_LABEL_PAD_X,
            "FLOW_LABEL_PAD_Y": FLOW_LABEL_PAD_Y,
            "FLOW_NODE_RADIUS": FLOW_NODE_RADIUS,
            "FLOW_RELAX_ROUNDS": FLOW_RELAX_ROUNDS,
            "FLOW_RELAX_PUSH": FLOW_RELAX_PUSH,
            "FLOW_RELAX_SLACK": FLOW_RELAX_SLACK,
            "FLOW_RELAX_PULL": FLOW_RELAX_PULL,
            "FLOW_TARGET_ASPECT": FLOW_TARGET_ASPECT,
            "FLOW_COMPACT_MIN": FLOW_COMPACT_MIN,
            "FLOW_RELAX_MAX_BUBBLES": FLOW_RELAX_MAX_BUBBLES,
        },
        "default_anchor_structure": DEFAULT_ANCHOR_STRUCTURE,
        "anchor_structures": {
            name: {
                "angles": dict(sorted(row["angles"].items())),
                "unified_finish": row["unified_finish"],
                "units": {
                    slot: [_r(x, 12), _r(y, 12)]
                    for slot, (x, y) in sorted(anchor_units(name).items())
                },
            }
            for name, row in sorted(ANCHOR_STRUCTURES.items())
        },
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
