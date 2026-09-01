"""Fixture dourada do layout de ANÉIS (Fase 5e, `docs/taxonomy/03_ARESTA_COMO_CAMINHO.md` §17).
Espelho no App: `src/services/map/ringLayout.ts`.

`analysis/ring_layout.py` deixou de ser geometria de protótipo e virou o layout do PRODUTO
(decisão do dono, 2026-09-01 noite, olhando a variante 17): Finalização no centro, todo estado
num anel discreto cujo raio é o menor número de traços até uma finalização observada, âncoras
genéricas FIXAS no modo bipolar. Portar isso para TS sem uma fixture é copiar duas vezes e
torcer — mesma disciplina de `export_flow_layout_fixtures.py`.

Sete casos, e cada um existe por uma classe de erro:

- `linear` — cadeia trivial até a finalização; pega um porte que errou a BFS REVERSA (contar
  do centro para fora em vez de para dentro) ou o `RING_MIN_GAP`;
- `fork_merge` — duas rotas até a mesma finalização, uma por setor; é onde a ordenação por
  baricentro dentro do setor e o desempate por (bary, -support, id) precisam bater;
- `unreachable` — um estado SEM nenhuma rota até a finalização. Ele não some e não é chutado:
  cai um anel além do mais profundo alcançável, que é exatamente o que "nunca vimos isso
  terminar uma luta" desenha. Um porte que filtrasse o inalcançável passaria em todo o resto;
- `crowded` — sete estados no MESMO anel e no MESMO setor, com rótulos longos: é o único caso
  em que `_separate_on_ring` realmente pesa (empurrão só em ÂNGULO, re-centrado na média
  original), e onde os limites do setor têm de CEDER em vez de mudar um raio;
- `desktop_aspect` — os mesmos caminhos de `crowded` com o aspecto de uma tela larga. Existe
  para travar o PARÂMETRO `target_aspect`: um porte que ignorasse o argumento passaria em todos
  os outros casos e falharia só aqui. É também o único que produz `bend != (1, 1)`, logo o único
  que trava as guias de anel como ELIPSES — e a excentricidade delas é EXATAMENTE a da
  superfície;
- `phone_aspect` — os mesmos caminhos com o aspecto de um celular em pé, e o disco NÃO curva.
  Não é esquecimento: a moldura do anel já é retrato (os polos bipolares ficam em 90° e 270°, o
  que faz a nuvem ~2x o raio externo de altura contra ~1,45x de largura). Esticar mais custa o
  único eixo que os rótulos têm — medido no mock do próprio App a 390x700, curvado vs. não
  curvado: 20 contra 25 nomes desenhados, 11 contra 15 nomes de AÇÃO, e o nome do polo de cima
  perdido. Um porte que curvasse simetricamente falha aqui.
  ⚠️ `target_aspect` é a razão VERDADEIRA da superfície (`largura/altura`), não a razão do eixo
  longo que `flow_layout` recebe. Os dois não são o mesmo número porque o fluxo é VIRADO para
  ler de cima para baixo num celular (`mapPathsView` troca x/y) e o anel nunca é: trocar os
  eixos de um disco troca "por cima" com "à esquerda" e destrói a única coisa que o ângulo
  afirma;
- `no_finish` — nenhuma finalização no grafo (o bundle de um usuário que nunca registrou uma
  submissão). Sem centro, `ring_index` devolve vazio e TODO estado é inalcançável: um anel só,
  âncoras fora dele. Degenerado, não quebrado — e é o caso que o App encontra primeiro.

Cada caso roda nas TRÊS colocações de âncora (`arco`/`tercos`/`bipolar`) para travar
`ANCHOR_PLACEMENTS` junto: a tabela é parte do contrato, e o `bipolar` (o que o dono escolheu)
só é distinguível do `tercos` pelo RAIO da âncora neutra.

Modo: `fixo` e só. Os dois modos livres (`livre`/`livre-total`) existem para a COMPARAÇÃO do
protótipo — eles dissolvem os anéis de propósito, que é o oposto do que o produto desenha — e
por isso o porte TS não os implementa e a fixture não os grava.

    uv run python -m scripts.export_ring_layout_fixtures
    uv run python -m scripts.export_ring_layout_fixtures --check

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
    FLOW_COMPACT_MIN,
    FLOW_LABEL_CHAR,
    FLOW_LABEL_EM,
    FLOW_LABEL_PAD_X,
    FLOW_LABEL_PAD_Y,
    FLOW_NODE_RADIUS,
    label_half_extent,
)
from analysis.path_bundling import RenderPath, bundle_paths  # noqa: E402
from analysis.ring_layout import (  # noqa: E402
    ANCHOR_PLACEMENTS,
    DEFAULT_RING_MODE,
    DEFAULT_RING_PLACEMENT,
    RING_ANCHOR_GAP,
    RING_MIN_GAP,
    SECTOR_CENTRE,
    SECTOR_INSET,
    SECTOR_SPAN,
    ring_guides,
    ring_index,
    ring_layout,
)


def _r(value: float, places: int = 9) -> float:
    """Round, and collapse negative zero — JS `Object.is(-0, 0)` is FALSE, and `cos(180deg)`
    produces a real `-0.0` here."""
    rounded = round(value, places)
    return rounded if rounded else 0.0


ANALYTICS_OUT = ROOT / "data" / "rating" / "ring_layout_golden.json"
APP_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__"
    / "ringLayoutGolden.json"
)

#: ``(path_id, source, actions, target)``
Spec = tuple[str, str, tuple[str, ...], str]

#: ``(name, why, paths, anchors, sectors, target_aspect)``. ``anchors`` maps a state key to its
#: generic slot (`top`/`bottom`/`neutral`) — the FINISH is not here, it is the centre and is
#: found by name. ``sectors`` maps every OTHER state to its orientation, exactly what
#: `taxonomy_kind.orientation_of` returns on the real path.
CASES: list[tuple[str, str, list[Spec], dict[str, str], dict[str, str], float | None]] = [
    (
        "linear",
        "cadeia trivial ate a finalizacao: aneis 2,1,0 lidos de fora para dentro",
        [("p1", "closed guard", ("armbar",), "mount"),
         ("p2", "mount", ("americana",), "finish")],
        {},
        {"closed guard": "bottom", "mount": "top"},
        None,
    ),
    (
        "fork_merge",
        "duas rotas ate a MESMA finalizacao, uma por setor — onde a ordenacao por baricentro "
        "dentro do setor e o desempate (bary, -support, id) tem de bater",
        [("p1", "start neutral", ("takedown",), "side control"),
         ("p2", "start neutral", ("guard pull",), "closed guard"),
         ("p3", "side control", ("mount transition",), "mount"),
         ("p4", "closed guard", ("scissor sweep",), "mount"),
         ("p5", "mount", ("armbar",), "finish")],
        {"start neutral": "neutral"},
        {"side control": "top", "closed guard": "bottom", "mount": "top"},
        None,
    ),
    (
        "unreachable",
        "um estado SEM rota ate a finalizacao: nao some e nao e chutado, cai UM anel alem do "
        "mais profundo alcancavel",
        [("p1", "closed guard", ("armbar",), "finish"),
         ("p2", "turtle", ("back take",), "back control")],
        {},
        {"closed guard": "bottom", "turtle": "bottom", "back control": "top"},
        None,
    ),
    (
        "crowded",
        "sete estados no MESMO anel e no MESMO setor com rotulos longos: o unico caso em que "
        "_separate_on_ring pesa — empurrao so em ANGULO, re-centrado na media, e os limites do "
        "setor cedendo em vez de um raio mudar",
        [("p1", "north south control", ("kimura",), "finish"),
         ("p2", "knee on belly control", ("armbar",), "finish"),
         ("p3", "mounted triangle setup", ("triangle choke",), "finish"),
         ("p4", "high mount control", ("armbar",), "finish"),
         ("p5", "back control body triangle", ("rear naked choke",), "finish"),
         ("p6", "crucifix control", ("short choke",), "finish"),
         ("p7", "side control kesa gatame", ("americana",), "finish"),
         ("p8", "start top", ("headquarters pass",), "north south control")],
        {"start top": "top"},
        {"north south control": "top", "knee on belly control": "top",
         "mounted triangle setup": "top", "high mount control": "top",
         "back control body triangle": "top", "crucifix control": "top",
         "side control kesa gatame": "top"},
        None,
    ),
    (
        "desktop_aspect",
        "os mesmos caminhos de `crowded` numa tela larga (1280/700 = 1,829) — trava o PARAMETRO "
        "target_aspect e, com ele, as guias de anel como ELIPSES da excentricidade da superficie",
        [("p1", "north south control", ("kimura",), "finish"),
         ("p2", "knee on belly control", ("armbar",), "finish"),
         ("p3", "mounted triangle setup", ("triangle choke",), "finish"),
         ("p4", "high mount control", ("armbar",), "finish"),
         ("p5", "back control body triangle", ("rear naked choke",), "finish"),
         ("p6", "crucifix control", ("short choke",), "finish"),
         ("p7", "side control kesa gatame", ("americana",), "finish"),
         ("p8", "start top", ("headquarters pass",), "north south control")],
        {"start top": "top"},
        {"north south control": "top", "knee on belly control": "top",
         "mounted triangle setup": "top", "high mount control": "top",
         "back control body triangle": "top", "crucifix control": "top",
         "side control kesa gatame": "top"},
        1280.0 / 700.0,
    ),
    (
        "phone_aspect",
        "os mesmos caminhos num celular em pe (390/840 = 0,464): o disco NAO curva, porque a "
        "moldura do anel ja e retrato — medido, curvar custa 5 nomes e 4 nomes de acao",
        [("p1", "north south control", ("kimura",), "finish"),
         ("p2", "knee on belly control", ("armbar",), "finish"),
         ("p3", "mounted triangle setup", ("triangle choke",), "finish"),
         ("p4", "high mount control", ("armbar",), "finish"),
         ("p5", "back control body triangle", ("rear naked choke",), "finish"),
         ("p6", "crucifix control", ("short choke",), "finish"),
         ("p7", "side control kesa gatame", ("americana",), "finish"),
         ("p8", "start top", ("headquarters pass",), "north south control")],
        {"start top": "top"},
        {"north south control": "top", "knee on belly control": "top",
         "mounted triangle setup": "top", "high mount control": "top",
         "back control body triangle": "top", "crucifix control": "top",
         "side control kesa gatame": "top"},
        390.0 / 840.0,
    ),
    (
        "no_finish",
        "NENHUMA finalizacao no grafo — o bundle de quem nunca registrou uma submissao. Sem "
        "centro todo estado e inalcancavel: um anel so, ancoras fora dele",
        [("p1", "start neutral", ("takedown",), "side control"),
         ("p2", "side control", ("escape",), "start bottom"),
         ("p3", "side control", ("mount transition",), "mount")],
        {"start neutral": "neutral", "start bottom": "bottom"},
        {"side control": "top", "mount": "top"},
        None,
    ),
]

_FINISH = "finish"


def build_fixture() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, why, specs, anchors, sectors, target_aspect in CASES:
        paths = [
            RenderPath(path_id=pid, source=src, target=tgt, actions=actions,
                       actor="you", count=1)
            for pid, src, actions, tgt in specs
        ]
        bundled = bundle_paths(paths)

        by_state = {p.state_key: p.id for p in bundled.points if p.state_key is not None}
        centre_ids = tuple(sorted(pid for key, pid in by_state.items() if key == _FINISH))
        anchor_slots = {by_state[k]: slot for k, slot in sorted(anchors.items())
                        if k in by_state}
        sector_of = {by_state[k]: orient for k, orient in sorted(sectors.items())
                     if k in by_state}

        # Same `support` the two view builders pass: every segment's own path count added to
        # BOTH endpoints. Every path here counts 1, which is enough to make the (-support, id)
        # tiebreak real without dragging a weighting helper in.
        support: dict[str, float] = {}
        for seg in bundled.segments:
            w = float(len(seg.path_ids))
            support[seg.from_point] = support.get(seg.from_point, 0.0) + w
            support[seg.to_point] = support.get(seg.to_point, 0.0) + w

        # The label the renderer would draw — a state's own name, a stroke's joined actions.
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

        per_placement: dict[str, Any] = {}
        for placement in sorted(ANCHOR_PLACEMENTS):
            laid = ring_layout(
                bundled, centre_ids=centre_ids, anchor_slots=anchor_slots,
                sector_of=sector_of, support=support, label_len=label_len,
                placement=placement, mode=DEFAULT_RING_MODE, target_aspect=target_aspect,
            )
            per_placement[placement] = {
                "positions": {pid: [_r(x), _r(y)] for pid, (x, y) in sorted(laid.pos.items())},
                "ring": dict(sorted(laid.ring.items())),
                "radius": {str(k): _r(v) for k, v in sorted(laid.radius.items())},
                "sector": dict(sorted(laid.sector.items())),
                "unreachable": list(laid.unreachable),
                "bend": [_r(laid.bend[0]), _r(laid.bend[1])],
                "centre": [_r(laid.centre[0]), _r(laid.centre[1])],
                "guides": ring_guides(laid),
            }

        cases.append({
            "name": name,
            "why": why,
            "target_aspect": target_aspect,
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
            "centre_ids": list(centre_ids),
            "anchor_slots": dict(sorted(anchor_slots.items())),
            "sector_of": dict(sorted(sector_of.items())),
            "support": dict(sorted(support.items())),
            "label_len": dict(sorted(label_len.items())),
            "bubbles": bubbles,
            "ring_index": dict(sorted(ring_index(bundled, centre_ids).items())),
            "placements": per_placement,
        })

    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_ring_layout_fixtures.py",
        "contract": (
            "ringLayout(bundled, {centreIds, anchorSlots, sectorOf, support, labelLen, "
            "placement, targetAspect}) -> point id -> (x, y), plus the ring index, the radius "
            "per ring and the guide ellipses. RADIUS IS THE CLAIM: ring(p) is the fewest "
            "strokes on a DIRECTED walk from p to a finish (reverse BFS from the centre); a "
            "point with no route lands one ring beyond the deepest reachable one. ANGLE is the "
            "point's orientation sector (top up / neutral left / bottom down), ordered inside "
            "the sector by the barycentre of its already-placed inner neighbours. Overlap is "
            "resolved in ANGLE ONLY (`separateOnRing`) — nothing may change a radius. Anchors "
            "are FIXED at the placement's own vertices outside the widest ring. Analytics "
            "mirror: analysis/ring_layout.py."
        ),
        "mode": DEFAULT_RING_MODE,
        "default_placement": DEFAULT_RING_PLACEMENT,
        "constants": {
            "RING_MIN_GAP": RING_MIN_GAP,
            "RING_ANCHOR_GAP": RING_ANCHOR_GAP,
            "SECTOR_SPAN": SECTOR_SPAN,
            "SECTOR_INSET": SECTOR_INSET,
            "SECTOR_CENTRE": dict(sorted(SECTOR_CENTRE.items())),
            "FLOW_LABEL_EM": FLOW_LABEL_EM,
            "FLOW_LABEL_CHAR": FLOW_LABEL_CHAR,
            "FLOW_LABEL_PAD_X": FLOW_LABEL_PAD_X,
            "FLOW_LABEL_PAD_Y": FLOW_LABEL_PAD_Y,
            "FLOW_NODE_RADIUS": FLOW_NODE_RADIUS,
            "FLOW_COMPACT_MIN": FLOW_COMPACT_MIN,
        },
        "anchor_placements": {
            key: {"angles": dict(sorted(row["angles"].items())),
                   "radius": dict(sorted(row["radius"].items()))}
            for key, row in sorted(ANCHOR_PLACEMENTS.items())
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
