"""Fixtures douradas do detector de constelações — o contrato de topologia entre Python e
TypeScript.

Espelha ``scripts/export_glicko2_fixtures.py``: gera casos determinísticos chamando o
``detect()`` real (``analysis/constellations/detect.py``) e grava nos DOIS repositórios. A
fixture é o contrato — contrato que mora só de um lado não é contrato.

O detector TS (``GrapplingArcApp/src/services/constellations/detect.ts``) usa uma travessia de
nós ORDENADA e determinística em vez de replicar o shuffle de ``random.Random(seed)`` que o
Louvain do networkx usa por passada — ver o cabeçalho de ``louvain.ts`` para a justificativa.
Consequência: paridade exata só é esperada quando o ótimo de modularidade é inequívoco (sem
empate de ganho entre comunidades vizinhas para nenhum nó). Cada caso abaixo carrega
``"expect_parity"`` para deixar isso explícito — ``false`` documenta uma divergência medida, não
esconde uma.

    uv run python -m scripts.export_constellation_fixtures

Sem banco, sem rede, sem relógio: reexecutar produz byte-idêntico.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import networkx as nx

from analysis.constellations.detect import Constellation, DetectionResult, detect

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_OUT = ROOT / "data" / "constellations" / "detect_golden.json"
APP_OUT = ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__" / "constellationGolden.json"


def _graph(nodes: list[str], edges: list[tuple[str, str, float]]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    for u, v, w in edges:
        if g.has_edge(u, v):
            g[u][v]["weight"] += w
        else:
            g.add_edge(u, v, weight=w)
    return g


def _serialize_result(result: DetectionResult) -> dict[str, Any]:
    def _constellation(c: Constellation) -> dict[str, Any]:
        return {
            "members": c.members,
            "hub": c.hub,
            "internal_edges": c.internal_edges,
            "support": c.support,
            "fingerprint": c.fingerprint,
        }

    return {
        "modularity": result.modularity,
        "rejected_count": result.rejected_count,
        "rejected_rate": result.rejected_rate,
        "constellations": [_constellation(c) for c in result.constellations],
    }


def _case(
    name: str,
    nodes: list[str],
    edges: list[tuple[str, str, float]],
    *,
    expect_parity: bool,
    note: str = "",
) -> dict[str, Any]:
    g = _graph(nodes, edges)
    result = detect(g)
    return {
        "name": name,
        "expect_parity": expect_parity,
        "note": note,
        "input": {
            "nodes": sorted(g.nodes()),
            "edges": [{"source": u, "target": v, "weight": float(d["weight"])} for u, v, d in g.edges(data=True)],
        },
        "expected": _serialize_result(result),
    }


def _forced_disconnected_case() -> dict[str, Any]:
    """Mirrors tests/test_constellations.py::test_disconnected_community_is_broken_and_counted —
    forces Louvain to hand back one community spanning two disconnected triangles, the exact
    ADR-07 failure mode. The TS side does not need its own Louvain to reach this raw partition
    (see the parity note above); it exercises the connectivity gate directly against the same
    forced raw partition (``GrapplingArcApp/.../__tests__/detect.test.ts``'s
    "ADR-07 gate" case) and this fixture's "input"/"expected" pair documents what the *real*
    Python detect() produces once the gate runs, for reference.
    """
    edges = [
        ("A1", "A2", 5.0), ("A2", "A3", 5.0), ("A3", "A1", 5.0),
        ("B1", "B2", 5.0), ("B2", "B3", 5.0), ("B3", "B1", 5.0),
    ]
    g = _graph([], edges)
    fake_partition = [{"A1", "A2", "A3", "B1", "B2", "B3"}]
    with patch("analysis.constellations.detect.nx.community.louvain_communities", return_value=fake_partition):
        result = detect(g)
    return {
        "name": "forced_disconnected_community",
        "expect_parity": False,
        "note": (
            "Python's raw Louvain result is FORCED via mock (real Louvain never returns this "
            "union for two disjoint cliques) to exercise the ADR-07 connectivity gate. The TS "
            "test replays the same forced raw partition against its own gate function directly, "
            "not through its detect()/Louvain — so this case is not a detect()-to-detect() "
            "parity check, only a reference for what the gate itself must produce."
        ),
        "input": {
            "nodes": sorted(g.nodes()),
            "edges": [{"source": u, "target": v, "weight": float(d["weight"])} for u, v, d in g.edges(data=True)],
            "forced_raw_partition": [sorted(fake_partition[0])],
        },
        "expected": _serialize_result(result),
    }


def build_fixture() -> dict[str, Any]:
    cases = [
        _case("empty_graph", [], [], expect_parity=True),
        _case("single_node", ["Solo"], [], expect_parity=True),
        _case("multiple_isolated_nodes", ["A", "B", "C"], [], expect_parity=True),
        _case(
            "two_disconnected_clusters",
            [],
            [
                ("A1", "A2", 5.0), ("A2", "A3", 5.0), ("A3", "A1", 5.0),
                ("B1", "B2", 5.0), ("B2", "B3", 5.0), ("B3", "B1", 5.0),
            ],
            expect_parity=True,
            note="Two disjoint triangles — the modularity optimum is unambiguous.",
        ),
        _case(
            "chain_with_a_weak_link",
            [],
            [
                ("Standing", "Takedown", 10.0), ("Takedown", "Standing", 10.0),
                ("Takedown", "Guard Pass", 1.0),
                ("Guard Pass", "Mount", 10.0), ("Mount", "Guard Pass", 10.0),
            ],
            expect_parity=True,
            note=(
                "A 4-node chain with one clearly weak link (weight 1 vs weight 10 elsewhere) — "
                "Louvain should cut the weak link regardless of node-visitation order."
            ),
        ),
        _forced_disconnected_case(),
        _case(
            "bridge_node_tie",
            [],
            [
                ("A1", "A2", 5.0), ("A2", "A1", 5.0), ("A2", "A3", 5.0), ("A3", "A2", 5.0),
                ("A3", "A1", 5.0), ("A1", "A3", 5.0),
                ("B1", "B2", 5.0), ("B2", "B1", 5.0), ("B2", "B3", 5.0), ("B3", "B2", 5.0),
                ("B3", "B1", 5.0), ("B1", "B3", 5.0),
                ("X", "A1", 2.0), ("A1", "X", 2.0), ("X", "B1", 2.0), ("B1", "X", 2.0),
            ],
            expect_parity=False,
            note=(
                "Two triangles bridged by X, connected with EQUAL weight to one node in each — "
                "moving X into A's community or B's community has identical modularity gain, a "
                "genuine tie. Empirically confirmed order-dependent: sweeping seed=0..9 through "
                "networkx's own louvain_communities on this graph gives X->A for every seed except "
                "seed=3, which gives X->B — proof the tie is real, not hypothetical, on the Python "
                "side alone. At the pinned default (seed=42) Python currently agrees with this "
                "port's fixed sorted-order result (X->A), so this case does not show two DIFFERENT "
                "stored answers today — but the agreement is not guaranteed to survive graph edits "
                "the way an unambiguous case's does, which is why it is still `expect_parity: "
                "false` and checked for TS-internal determinism only, never asserted equal to the "
                "Python side by construction."
            ),
        ),
    ]
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_constellation_fixtures.py",
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
        print(f"escrito {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
