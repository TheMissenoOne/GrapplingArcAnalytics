"""O layout de anéis DENTRO do payload do site (Fase 5e, §17).

`tests/test_ring_layout.py` prova a geometria. Aqui a pergunta é outra: o que sai de
`path_payload(layout="ring")` — o que o dossiê e o breakdown realmente desenham — ainda satisfaz
a afirmação que o anel faz, e continua sendo o mesmo payload em todo o resto.

Quatro coisas, e cada uma é um modo de falha real:

1. **aditivo** — `layout`/`rings`/`ringCentre` e o `ring` de cada nó entram; nada mais muda de
   forma. Um cliente que não conhece os campos desenha o que sempre desenhou (é isso que torna
   o bump de versão uma questão de CACHE e não de contrato quebrado);
2. **o anel é a afirmação** — todo estado desenhado está exatamente sobre o raio do seu anel, e
   as guias descrevem esses mesmos raios;
3. **`back` mudou de significado** — num disco "para trás" não é uma comparação de x, é o traço
   que se AFASTA da finalização; a comparação de x continua sendo a do fluxo;
4. **legibilidade** — zero nomes de estado sobrepostos, o critério duro do dono (§10.5.3),
   medido no payload de verdade e não numa fixture.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pytest

from analysis.corpus_paths import aggregate_bouts, path_payload
from analysis.flow_layout import label_half_extent

# Duas lutas com forma real: A abre no chão e finaliza, B passa e controla; a terceira traz uma
# volta (Mount -> Closed Guard) para que exista uma aresta que se AFASTA da finalização.
BOUT_A: list[dict[str, Any]] = [
    {"label": "Closed Guard", "type": "guard", "side": "a", "ts": 10},
    {"label": "Armbar", "type": "submission", "side": "a", "successful": False, "ts": 20},
    {"label": "Triangle Choke", "type": "submission", "side": "a", "successful": True, "ts": 31},
    {"label": "Knee Cut", "type": "pass", "side": "b", "successful": True, "ts": 40},
    {"label": "Side Control", "type": "control", "side": "b", "ts": 44},
]
BOUT_B: list[dict[str, Any]] = [
    {"label": "Closed Guard", "type": "guard", "side": "a"},
    {"label": "Armbar", "type": "submission", "side": "a", "successful": False},
    {"label": "Omoplata", "type": "submission", "side": "a", "successful": True},
    {"label": "Mount", "type": "control", "side": "a"},
]
BOUT_C: list[dict[str, Any]] = [
    {"label": "Single Leg Takedown", "type": "takedown", "side": "a", "successful": True},
    {"label": "Side Control", "type": "control", "side": "a"},
    {"label": "Mount", "type": "control", "side": "a"},
    {"label": "Escape to Standing", "type": "escape", "side": "b", "successful": True},
    {"label": "Closed Guard", "type": "guard", "side": "b"},
    {"label": "Kimura", "type": "submission", "side": "b", "successful": True},
]
BOUTS = [BOUT_A, BOUT_B, BOUT_C]

# §N4 (2026-09-03) — one meta dict per BOUTS entry, same index, feeding provenance.
BOUT_META: list[dict[str, Any]] = [
    {"match_id": "m-a", "event": "ADCC 2022", "family": "adcc",
     "athletes": ("ath-1", "ath-2"), "slug": "a-vs-b-2022", "label": "A vs B"},
    {"match_id": "m-b", "event": "IBJJF Worlds", "family": "ibjjf",
     "athletes": ("ath-1", "ath-3"), "slug": "a-vs-c-2022", "label": "A vs C"},
    {"match_id": "m-c", "event": "IBJJF Worlds", "family": "ibjjf",
     "athletes": ("ath-1", "ath-4"), "slug": "a-vs-d-2022", "label": "A vs D"},
]


@pytest.fixture(scope="module")
def ring() -> dict[str, Any]:
    return path_payload(aggregate_bouts(BOUTS), layout="ring")


@pytest.fixture(scope="module")
def ring_provenance() -> dict[str, Any]:
    return path_payload(
        aggregate_bouts(BOUTS, bout_meta=BOUT_META), layout="ring",
    )


@pytest.fixture(scope="module")
def flow() -> dict[str, Any]:
    return path_payload(aggregate_bouts(BOUTS))


def test_ring_mode_is_additive_over_the_flow_payload(ring: dict[str, Any],
                                                      flow: dict[str, Any]) -> None:
    assert flow["layout"] == "flow"
    assert "rings" not in flow and "ringCentre" not in flow
    assert ring["layout"] == "ring"
    assert ring["rings"] and len(ring["ringCentre"]) == 2
    # mesmos nós, mesmos traços, mesmas ocorrências — só as coordenadas (e o quadro) mudam
    assert [n["id"] for n in ring["nodes"]] == [n["id"] for n in flow["nodes"]]
    assert [lk["id"] for lk in ring["links"]] == [lk["id"] for lk in flow["links"]]
    assert ring["stats"] == flow["stats"]
    assert json.dumps(ring["paths"], sort_keys=True) == json.dumps(flow["paths"], sort_keys=True)
    # os únicos campos novos por nó
    for a, b in zip(ring["nodes"], flow["nodes"], strict=True):
        assert set(a) - set(b) <= {"ring", "sector"}
        assert set(b) - set(a) == set()


def test_every_point_carries_its_sector(ring: dict[str, Any]) -> None:
    """L5 — o campo aditivo `sector` (já existia em `RingLayout.sector`, agora serializado):
    todo ponto do payload de anel (estado, âncora, junção) carrega uma das três latitudes."""
    assert all(n.get("sector") in {"top", "neutral", "bottom"} for n in ring["nodes"])
    junctions = [n for n in ring["nodes"] if n.get("junction")]
    assert junctions and all("sector" in n for n in junctions)


def test_every_drawn_state_sits_on_its_ring_and_the_guides_describe_them(
    ring: dict[str, Any],
) -> None:
    cx, cy = ring["ringCentre"]
    radii = {g["ring"]: g["rx"] for g in ring["rings"]}
    assert all(g["rx"] == g["ry"] for g in ring["rings"]), "sem curvatura, as guias são círculos"
    anchors = {n["id"] for n in ring["nodes"] if n.get("kind") == "anchor"
                and n.get("orient") != "finish"}
    seen = 0
    for node in ring["nodes"]:
        if node["id"] in anchors or "ring" not in node:
            continue
        radius = radii.get(node["ring"], 0.0)
        dist = math.hypot(node["x"] - cx, node["y"] - cy)
        # o payload arredonda a 0,1 unidade de mundo antes de sair
        assert abs(dist - radius) <= 0.2, (node["id"], dist, radius)
        seen += 1
    assert seen >= 5, "o payload tem de ter estados desenhados para a asserção significar algo"


def test_back_is_the_stroke_that_moves_away_from_the_finish(ring: dict[str, Any]) -> None:
    ring_of = {n["id"]: n.get("ring") for n in ring["nodes"]}
    backs = 0
    for link in ring["links"]:
        src, tgt = ring_of.get(link["from"]), ring_of.get(link["to"])
        if src is None or tgt is None:
            continue
        assert bool(link.get("back")) is (tgt > src), link["id"]
        backs += int(bool(link.get("back")))
    assert backs > 0, "o corpus de teste traz uma volta de propósito"


def test_no_two_state_names_overlap(ring: dict[str, Any]) -> None:
    """O critério duro do dono (§10.5.3), no payload de verdade. Em unidades de MUNDO de
    propósito: os dois renderizadores desenham o rótulo dentro do transform do mundo, então a
    sobreposição é invariante de escala e a checagem não depende de um viewport."""
    boxes = []
    for node in ring["nodes"]:
        if node.get("junction") or not node.get("label"):
            continue
        hw, hh = label_half_extent(len(node["label"]), node=True)
        boxes.append((node["id"], node["x"] - hw, node["y"] - hh,
                       node["x"] + hw, node["y"] + hh))
    hits = [
        (a[0], b[0])
        for i, a in enumerate(boxes) for b in boxes[i + 1:]
        if a[1] < b[3] and a[3] > b[1] and a[2] < b[4] and a[4] > b[2]
    ]
    assert hits == []
    assert len(boxes) >= 6


def test_provenance_is_additive_and_opt_in(
    ring: dict[str, Any], ring_provenance: dict[str, Any]
) -> None:
    """§N4 — no `bout_meta`, no new fields anywhere (existing dossier/breakdown callers)."""
    assert "meta" not in ring and "matches" not in ring
    assert all("bouts" not in p and "families" not in p for p in ring["paths"])
    assert "meta" in ring_provenance and "matches" in ring_provenance


def test_paths_carry_their_own_bouts_and_families(ring_provenance: dict[str, Any]) -> None:
    all_bouts: set[str] = set()
    for p in ring_provenance["paths"]:
        assert isinstance(p["bouts"], list) and p["bouts"] == sorted(p["bouts"])
        assert set(p["bouts"]) <= {"m-a", "m-b", "m-c"}
        assert p["count"] >= len(p["bouts"])  # count is occurrences, bouts is DISTINCT matches
        assert isinstance(p["families"], dict)
        assert sum(p["families"].values()) == p["count"]
        all_bouts |= set(p["bouts"])
    assert all_bouts == {"m-a", "m-b", "m-c"}, "every fed bout shows up on at least one path"


def test_meta_scope_counts_the_whole_corpus_fed_in(ring_provenance: dict[str, Any]) -> None:
    scope = ring_provenance["meta"]["scope"]
    assert scope["bouts"] == 3
    assert scope["athletes"] == 4  # ath-1..4, shared ath-1 counted once
    assert scope["events"] == 2  # ADCC 2022, IBJJF Worlds
    assert scope["families"] == {"adcc": 1, "ibjjf": 2}


def test_matches_index_resolves_every_referenced_bout_to_slug_and_label(
    ring_provenance: dict[str, Any],
) -> None:
    referenced = {mid for p in ring_provenance["paths"] for mid in p["bouts"]}
    matches = ring_provenance["matches"]
    assert referenced  # the fixture bouts do produce drawn variants
    assert referenced <= matches.keys()
    for mid in referenced:
        row = matches[mid]
        assert "match_id" not in row  # redundant with the dict key
        assert row["slug"] and row["label"] and row["event"]


def test_bout_ids_false_falls_back_to_counts_and_drops_the_matches_index(
    ring_provenance: dict[str, Any],
) -> None:
    payload = path_payload(
        aggregate_bouts(BOUTS, bout_meta=BOUT_META), layout="ring", bout_ids=False,
    )
    assert "matches" not in payload
    assert "meta" in payload and payload["meta"] == ring_provenance["meta"]  # scope unaffected
    by_id = {p["id"]: p for p in ring_provenance["paths"]}
    for p in payload["paths"]:
        assert "bouts" not in p
        assert p["nBouts"] == len(by_id[p["id"]]["bouts"])
