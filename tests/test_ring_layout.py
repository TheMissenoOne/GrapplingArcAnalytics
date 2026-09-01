"""O invariante do anel (Fase 5e, `docs/taxonomy/03_ARESTA_COMO_CAMINHO.md` §17).

A golden cross-repo prova que os DOIS ports produzem os mesmos números. Este arquivo prova a
outra metade: que esses números significam o que a página afirma. O anel é uma AFIRMAÇÃO sobre o
dado — *"este estado está a N traços de uma finalização"* — e ela só se sustenta se:

1. todo estado (âncoras à parte, que são a moldura) está EXATAMENTE sobre o raio do seu anel;
2. o anel é a BFS reversa de verdade — distância dirigida ATÉ o centro, não a partir dele;
3. um estado sem rota nenhuma até a finalização não some e não é chutado: cai um anel além do
   mais profundo alcançável;
4. a separação angular resolve sobreposição de rótulo SEM mover um raio — é o motivo de
   `_separate_on_ring` existir em vez da relaxação cartesiana do `flow_layout`;
5. sob a curvatura de aspecto (`target_aspect`), que é afim e preserva o centro, o círculo vira
   uma ELIPSE concêntrica e os pontos continuam sobre ela — é o que autoriza as guias.

A prova sobre DADO REAL (zero nomes de estado sobrepostos no payload do site, nas duas telas)
vive em `tests/test_corpus_paths_ring.py`, junto do consumidor que a produz.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from analysis.flow_layout import label_half_extent
from analysis.path_bundling import RenderPath, bundle_paths
from analysis.ring_layout import (
    ANCHOR_PLACEMENTS,
    DEFAULT_RING_PLACEMENT,
    RING_MIN_GAP,
    _separate_on_ring,
    ring_guides,
    ring_index,
    ring_layout,
)

GOLDEN = Path(__file__).resolve().parents[1] / "data" / "rating" / "ring_layout_golden.json"


def _case(name: str) -> dict[str, Any]:
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return next(c for c in doc["cases"] if c["name"] == name)


def _lay(case: dict[str, Any], placement: str, aspect: float | None = None) -> Any:
    paths = [
        RenderPath(path_id=p["path_id"], source=p["source"], target=p["target"],
                    actions=tuple(p["actions"]), actor="you", count=1)
        for p in case["paths"]
    ]
    bundled = bundle_paths(paths)
    return bundled, ring_layout(
        bundled,
        centre_ids=case["centre_ids"],
        anchor_slots=case["anchor_slots"],
        sector_of=case["sector_of"],
        support=case["support"],
        label_len=case["label_len"],
        placement=placement,
        target_aspect=aspect,
    )


ALL_CASES = [c["name"] for c in
              json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]]


@pytest.mark.parametrize("name", ALL_CASES)
@pytest.mark.parametrize("placement", sorted(ANCHOR_PLACEMENTS))
def test_every_state_sits_exactly_on_its_ring(name: str, placement: str) -> None:
    case = _case(name)
    _, laid = _lay(case, placement)
    anchors = set(case["anchor_slots"])
    for point, (x, y) in laid.pos.items():
        if point in anchors:
            continue
        radius = laid.radius[laid.ring[point]]
        dist = math.hypot(x, y)
        if radius == 0.0:
            assert dist == 0.0, point
        else:
            assert abs(dist / radius - 1.0) < 1e-9, (point, dist, radius)


@pytest.mark.parametrize("name", ALL_CASES)
def test_ring_is_the_directed_distance_to_a_finish(name: str) -> None:
    """Não é "quão longe a finalização está de mim" — é o contrário, e a diferença aparece em
    qualquer aresta de mão única."""
    case = _case(name)
    bundled, _ = _lay(case, DEFAULT_RING_PLACEMENT)
    index = ring_index(bundled, case["centre_ids"])
    for seg in bundled.segments:
        if seg.from_point in index and seg.to_point in index:
            # uma aresta só pode aproximar do centro em UM passo
            assert index[seg.from_point] <= index[seg.to_point] + 1


def test_unreachable_lands_one_ring_beyond_the_deepest_reachable() -> None:
    case = _case("unreachable")
    _, laid = _lay(case, DEFAULT_RING_PLACEMENT)
    assert laid.unreachable, "o caso existe para ter um inalcançável"
    reachable = {p: k for p, k in laid.ring.items()
                  if p not in laid.unreachable and p not in case["anchor_slots"]}
    deepest = max(reachable.values())
    for point in laid.unreachable:
        assert laid.ring[point] == deepest + 1
        # e ele é DESENHADO — o modo de falha real seria filtrá-lo
        assert point in laid.pos


def test_a_crowded_ring_widens_instead_of_moving_a_point_off_it() -> None:
    """`crowded` põe sete estados no mesmo anel e no mesmo setor. A primeira defesa é o RAIO:
    `_ring_radii` dimensiona o anel pelo setor mais cheio, então com rótulos longos ele cresce e
    a distribuição angular continua sendo a uniforme. O que NÃO pode acontecer, em nenhum dos
    dois casos, é um ponto sair do seu anel."""
    case = _case("crowded")
    _, wide = _lay(case, DEFAULT_RING_PLACEMENT)
    narrow_case = dict(case, label_len={k: 0 for k in case["label_len"]})
    _, narrow = _lay(narrow_case, DEFAULT_RING_PLACEMENT)
    anchors = set(case["anchor_slots"])
    for point in wide.pos:
        if point in anchors:
            continue
        assert wide.ring[point] == narrow.ring[point]
    # o anel cresce para caber os rótulos e nunca encolhe abaixo do mínimo (um ponto SEM
    # rótulo ainda é uma bolha de `FLOW_NODE_RADIUS`, então sete deles já pedem mais que o piso)
    assert wide.radius[1] > narrow.radius[1] >= RING_MIN_GAP


def test_separate_on_ring_only_ever_changes_an_angle() -> None:
    """A passada de separação é a segunda defesa, e a razão de ela existir em vez da relaxação
    cartesiana do `flow_layout`: aquela move a caixa no eixo de menor penetração, que num disco é
    quase sempre o RADIAL — ela levantava o estado do anel que a página existe para mostrar.

    Três pontos empilhados no mesmo ângulo, num raio pequeno demais para os rótulos: a passada
    tem de separá-los, manter a média original e não tocar em raio nenhum (aqui, por
    construção: ela só recebe e devolve ângulos)."""
    angles = {"a": 90.0, "b": 90.0, "c": 90.0}
    before_mean = sum(angles.values()) / 3
    _separate_on_ring(angles, ["a", "b", "c"], 200.0, {"a": 12, "b": 12, "c": 12})
    assert len(set(angles.values())) == 3
    assert sum(angles.values()) / 3 == pytest.approx(before_mean)
    ordered = sorted(angles.values())
    half = math.degrees(label_half_extent(12, node=True)[0] / 200.0)
    for lo, hi in zip(ordered, ordered[1:], strict=False):
        assert hi - lo >= 2 * half - 1e-9


def test_a_portrait_surface_is_left_round() -> None:
    """A moldura do anel já é retrato (polos em 90° e 270°), então curvar um celular custa o
    único eixo que os rótulos têm. Medido: 25 → 20 nomes desenhados no mock do App. O caso
    existe para travar essa assimetria — um porte que curvasse simetricamente falha aqui."""
    case = _case("phone_aspect")
    _, laid = _lay(case, DEFAULT_RING_PLACEMENT, aspect=case["target_aspect"])
    assert case["target_aspect"] < 1.0
    assert laid.bend == (1.0, 1.0)
    guides = ring_guides(laid)
    assert guides and all(g["rx"] == g["ry"] for g in guides)


def test_the_bend_keeps_points_on_a_concentric_ellipse() -> None:
    """O que autoriza as guias: a curvatura de aspecto é afim e preserva o centro, então um
    círculo concêntrico vira uma elipse concêntrica com os mesmos (kx, ky) que os pontos
    sofreram — e `ring_guides` lê exatamente esses. A excentricidade resultante é EXATAMENTE a
    da superfície, que é o que "um disco com a forma da tela" quer dizer."""
    case = _case("desktop_aspect")
    _, laid = _lay(case, DEFAULT_RING_PLACEMENT, aspect=case["target_aspect"])
    assert laid.bend != (1.0, 1.0), "o caso existe para curvar"
    guides = {g["ring"]: g for g in ring_guides(laid)}
    anchors = set(case["anchor_slots"])
    cx, cy = laid.centre
    for point, (x, y) in laid.pos.items():
        if point in anchors:
            continue
        radius = laid.radius[laid.ring[point]]
        if radius == 0.0:
            continue
        rx = radius * laid.bend[0]
        ry = radius * laid.bend[1]
        assert abs(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 - 1.0) < 1e-9
        assert guides[laid.ring[point]]["rx"] == pytest.approx(rx, abs=0.05)
        assert guides[laid.ring[point]]["ry"] == pytest.approx(ry, abs=0.05)
    for guide in guides.values():
        assert guide["rx"] / guide["ry"] == pytest.approx(case["target_aspect"], rel=1e-3)
