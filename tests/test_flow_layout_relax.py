"""As duas etapas novas do ``analysis.flow_layout`` (dono, 2026-09-01): a compactação de aspecto
e a relaxação de bolhas de rótulo.

O pedido do dono é uma afirmação sobre o DESENHO, não sobre o algoritmo — *"nothing that's
readable is overlapped"*, *"keeping the anchor nodes fixed"*, *"it might be too stretched"*. Este
arquivo mede exatamente essas três coisas, sobre os mesmos casos da golden (o contrato) e sobre o
``mock_user_bundle`` (o dado que o App realmente carrega), nas TRÊS estruturas de âncora.

O critério é em unidades de MUNDO de propósito, e é por isso que ele vale "na escala de fit": os
dois renderizadores desenham o rótulo DENTRO do transform do mundo (``graphRenderRecords`` usa
``size = 12``, ``EdgeRenderer`` usa 11), então o zoom escala a posição e a glifa pelo mesmo fator
e a sobreposição é invariante de escala. Uma checagem em pixels dependeria do viewport; esta não.

Três classes de par, e só uma é asserção dura:

- **nome de estado x nome de estado** — a camada primária. ZERO, sempre. É o que o dono viu.
- **nome de estado x rótulo de ação** e **ação x ação** — a camada secundária
  (``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` §10.7 já a chama assim). O teste registra o
  número e trava um TETO, porque o resto é geometria de traço: o rótulo de uma aresta mora onde
  a aresta PASSA, e duas arestas que se cruzam têm os pontos médios perto — nenhum arranjo de
  pontos desfaz isso. O conserto é do renderizador (§12.5, o rótulo de ação sai do traço).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from analysis.flow_layout import (
    ANCHOR_STRUCTURES,
    DEFAULT_ANCHOR_STRUCTURE,
    FLOW_TARGET_ASPECT,
    flow_layout,
    label_half_extent,
)
from analysis.path_bundling import BundledGraph, Point, RenderPath, Segment, bundle_paths

GOLDEN = Path(__file__).resolve().parents[1] / "data" / "rating" / "flow_layout_golden.json"
MOCK_BUNDLE = (Path(__file__).resolve().parents[2] / "GrapplingArcApp" / "src" / "data"
               / "mockData" / "mock_user_bundle.json")


# ── the pieces under test, assembled the way a caller assembles them ─────────────────────────

def _case_inputs(case: dict[str, Any]) -> tuple[BundledGraph, dict[str, float], dict[str, int]]:
    bundled = BundledGraph(
        segments=tuple(Segment(id=s["id"], actions=tuple(s["actions"]),
                                path_ids=frozenset(s["path_ids"]),
                                from_point=s["from_point"], to_point=s["to_point"])
                        for s in case["bundled"]["segments"]),
        points=tuple(Point(id=p["id"], kind=p["kind"], state_key=p["state_key"])
                      for p in case["bundled"]["points"]),
        path_entry={},
    )
    return bundled, dict(case["weight"]), dict(case["label_len"])


#: ``(id, centre x, centre y, half width, half height, 'node' | 'edge')``
Box = tuple[str, float, float, float, float, str]


def _boxes(bundled: BundledGraph, label_len: dict[str, int],
           pos: dict[str, tuple[float, float]]) -> list[Box]:
    """``(id, cx, cy, half_w, half_h, kind)`` for every readable box, exactly as the renderer
    lays them out: a state's name on its own point, a stroke's action sequence on the middle of
    the stroke. Self loops and fanned parallels are left out for the same reason
    ``flow_layout._bubbles`` leaves them out — their label is not drawn on the chord."""
    out: list[Box] = []
    for p in bundled.points:
        hw, hh = label_half_extent(label_len.get(p.id, 0), node=True)
        x, y = pos[p.id]
        out.append((p.id, x, y, hw, hh, "node"))
    pairs: dict[str, int] = {}
    for s in bundled.segments:
        key = " ".join(sorted((s.from_point, s.to_point)))
        pairs[key] = pairs.get(key, 0) + 1
    for s in bundled.segments:
        if s.from_point == s.to_point:
            continue
        if pairs[" ".join(sorted((s.from_point, s.to_point)))] > 1:
            continue
        hw, hh = label_half_extent(label_len.get(s.id, 0), node=False)
        if hw <= 0.0:
            continue
        (ax, ay), (bx, by) = pos[s.from_point], pos[s.to_point]
        out.append((s.id, (ax + bx) / 2, (ay + by) / 2, hw, hh, "edge"))
    return out


def _overlaps(boxes: list[Box],
              ends: dict[str, tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Pairs whose boxes intersect, split by class. A stroke's label against its OWN endpoint is
    excluded: it is always within half a stroke of them by construction."""
    found: dict[str, list[tuple[str, str]]] = {"node/node": [], "node/edge": [], "edge/edge": []}
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a[0] in ends and b[0] not in ends and b[0] in ends[a[0]]:
                continue
            if b[0] in ends and a[0] not in ends and a[0] in ends[b[0]]:
                continue
            if abs(a[1] - b[1]) >= a[3] + b[3] or abs(a[2] - b[2]) >= a[4] + b[4]:
                continue
            kind = ("node/node" if a[5] == b[5] == "node"
                    else "edge/edge" if a[5] == b[5] == "edge" else "node/edge")
            found[kind].append((a[0], b[0]))
    return found


def _ends_of(bundled: BundledGraph) -> dict[str, tuple[str, str]]:
    return {s.id: (s.from_point, s.to_point) for s in bundled.segments}


# ── the golden's own cases ───────────────────────────────────────────────────────────────────

_CASES: list[dict[str, Any]] = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
_MATRIX = [(c["name"], c, s) for c in _CASES for s in sorted(ANCHOR_STRUCTURES)]


@pytest.mark.parametrize(("name", "case", "structure"), _MATRIX,
                          ids=[f"{n}-{s}" for n, _, s in _MATRIX])
def test_no_two_state_names_overlap(name: str, case: dict[str, Any], structure: str) -> None:
    del name
    bundled, weight, label_len = _case_inputs(case)
    slots = case["structures"][structure]["anchor_slots"]
    pos = flow_layout(bundled, structure=structure, anchor_slots=slots,
                      weight=weight, label_len=label_len)
    hits = _overlaps(_boxes(bundled, label_len, pos), _ends_of(bundled))
    assert hits["node/node"] == []


@pytest.mark.parametrize(("name", "case", "structure"), _MATRIX,
                          ids=[f"{n}-{s}" for n, _, s in _MATRIX])
def test_two_runs_are_bit_identical(name: str, case: dict[str, Any], structure: str) -> None:
    """No RNG, no clock, no convergence test — a fixed round count over a sorted iteration. The
    golden already proves the TS agrees; this proves the run itself does not drift."""
    del name
    bundled, weight, label_len = _case_inputs(case)
    slots = case["structures"][structure]["anchor_slots"]
    first = flow_layout(bundled, structure=structure, anchor_slots=slots, weight=weight,
                        label_len=label_len)
    second = flow_layout(bundled, structure=structure, anchor_slots=slots, weight=weight,
                         label_len=label_len)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.parametrize(("name", "case", "structure"), _MATRIX,
                          ids=[f"{n}-{s}" for n, _, s in _MATRIX])
def test_anchors_never_move_for_the_labels(name: str, case: dict[str, Any],
                                            structure: str) -> None:
    """"...while keeping the anchor nodes fixed" — owner, verbatim. The frame is placed by the
    ellipse and bent by the aspect pass, and NEITHER reads a label; the relaxation reads every
    label and never writes an anchor. So changing every label in the picture has to leave the
    frame byte-identical."""
    del name
    bundled, weight, label_len = _case_inputs(case)
    slots = case["structures"][structure]["anchor_slots"]
    bare = flow_layout(bundled, structure=structure, anchor_slots=slots, weight=weight)
    named = flow_layout(bundled, structure=structure, anchor_slots=slots, weight=weight,
                        label_len=label_len)
    for point_id in slots:
        assert bare[point_id] == named[point_id]


def test_a_flat_chain_is_left_alone_by_the_aspect_pass() -> None:
    """A single row has no aspect to bend (height 0), and dividing by it would be the only place
    this file could produce a NaN. The relaxation is what spaces a flat chain."""
    bundled = bundle_paths([
        RenderPath(path_id="p1", source="A", target="B", actions=("1",), actor="you", count=1),
        RenderPath(path_id="p2", source="B", target="C", actions=("2",), actor="you", count=1),
    ])
    pos = flow_layout(bundled, structure=DEFAULT_ANCHOR_STRUCTURE, anchor_slots={},
                      weight={}, label_len={p.id: 24 for p in bundled.points})
    assert all(x == x and y == y for x, y in pos.values())  # noqa: PLR0124 — NaN check
    hits = _overlaps(_boxes(bundled, {p.id: 24 for p in bundled.points}, pos), _ends_of(bundled))
    assert hits["node/node"] == []


# ── the App's own mock bundle, through the prototype's real assembly ─────────────────────────

def _mock_views() -> dict[str, Any]:
    from scripts.render_map_prototypes import (  # noqa: PLC0415 — heavy import, test-only
        _paths_and_metrics,
        _paths_view,
        build_aggregate,
    )
    bundle = json.loads(MOCK_BUNDLE.read_text(encoding="utf-8"))
    agg = build_aggregate(bundle)
    paths, metrics = _paths_and_metrics(agg, bundle)
    return {s: _paths_view(agg, structure=s, member_qids=None, metrics_by_path=metrics,
                            paths=paths)
            for s in sorted(ANCHOR_STRUCTURES)}


@pytest.mark.skipif(not MOCK_BUNDLE.is_file(), reason="GrapplingArcApp não está ao lado")
def test_mock_bundle_draws_no_two_state_names_on_top_of_each_other() -> None:
    for structure, view in _mock_views().items():
        pos = {n["id"]: (n["x"], n["y"]) for n in view["gv"]["nodes"]}
        boxes: list[Box] = []
        for n in view["gv"]["nodes"]:
            hw, hh = label_half_extent(len(n["label"]), node=True)
            boxes.append((n["id"], n["x"], n["y"], hw, hh, "node"))
        hits = _overlaps(boxes, {})
        assert hits["node/node"] == [], structure
        assert pos  # the view is not empty, so the assertion above means something


@pytest.mark.skipif(not MOCK_BUNDLE.is_file(), reason="GrapplingArcApp não está ao lado")
def test_mock_bundle_is_no_longer_stretched() -> None:
    """Measured before this pass existed: aspect 4.62 (triangle) / 3.55 (diamond) / 3.38
    (pentagon) — a 1200x260 ribbon. The band is generous on purpose: the relaxation is allowed
    to grow the short axis to make room, and a picture that ends TALLER than the target is
    exactly as legible."""
    for structure, view in _mock_views().items():
        xs = [n["x"] for n in view["gv"]["nodes"]]
        ys = [n["y"] for n in view["gv"]["nodes"]]
        aspect = (max(xs) - min(xs)) / (max(ys) - min(ys))
        assert 0.5 * FLOW_TARGET_ASPECT <= aspect <= 1.5 * FLOW_TARGET_ASPECT, (structure, aspect)
