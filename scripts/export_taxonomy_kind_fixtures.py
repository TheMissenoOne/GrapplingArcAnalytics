"""Fixtures douradas do contrato de taxonomia (D1/D2) — kind_of + tabela de inferência.

Mesmo padrão de `scripts/export_markov_weight_fixtures.py`. Duas peças do contrato:

1. **``kind_of``** (D1) — classifica cada uma das 141 entradas da biblioteca do App
   (``grappling-arch.nodes.json``) em ``action``/``state``/``transparent``. O rótulo lido é o
   CANÔNICO em inglês (``translations.en`` senão ``name``), a mesma convenção de
   ``export.app_node_scores.canonical_label`` — não o campo `name` bruto, que nesta biblioteca
   está majoritariamente em português (111 de 141 divergem do `en`). Medido: usar o `name` cru
   em vez do canônico muda o `kind` de UMA entrada ("Triângulo de Corpo" → estado; "Body
   Triangle", o `en` correto, → ação via `BACK_TAKE_TOKENS`) — o carve-out de "Back Control"
   em `kind_of` já absorve o segundo caso que existiria sem ele ("Costas"/"Back Control" dá
   estado nas duas leituras, porque agora é sempre estado).
2. **A tabela de inferência D2** — copiada verbatim de ``data/taxonomy/inference_table.json``.

    uv run python -m scripts.export_taxonomy_kind_fixtures
    uv run python -m scripts.export_taxonomy_kind_fixtures --check

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

from analysis.names import _normalize_name  # noqa: E402
from analysis.taxonomy_kind import kind_of, load_inference_table, orientation_of  # noqa: E402
from export.app_node_scores import canonical_label  # noqa: E402

APP_NODES_PATH = ROOT.parent / "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json"
ANALYTICS_OUT = ROOT / "data" / "rating" / "taxonomy_kind_golden.json"
APP_FIXTURE_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__" / "taxonomyKindGolden.json"
)
APP_INFERENCE_TABLE_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "data" / "taxonomy_inference_table.json"
)


def build_kinds() -> dict[str, dict[str, str]]:
    """``{normalized_canonical_label: {kind, type, orientation?}}`` for every App library entry.
    ``orientation`` (top|bottom|neutral, D1's curated ``state_orientation.json``) is only
    meaningful for ``kind == 'state'`` entries — actions/transparent entries don't carry it."""
    nodes = json.loads(APP_NODES_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for node in nodes:
        label = canonical_label(node)
        typ = str(node.get("type") or "")
        if not label:
            continue
        key = _normalize_name(label)
        kind = kind_of(label, typ)
        entry = {"kind": kind, "type": typ}
        if kind == "state":
            entry["orientation"] = orientation_of(label)
        out[key] = entry
    return out


def build_fixture() -> dict[str, Any]:
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_taxonomy_kind_fixtures.py",
        "contract": (
            "kind_of(label, type) -> 'action'|'state'|'transparent' (D1); "
            "inference_table = D2's structural pair -> generic node/edge lookup, verbatim."
        ),
        "kinds": build_kinds(),
        "inference_table": load_inference_table(),
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="não escreve; falha se o que está em disco divergir do gerado")
    args = ap.parse_args()

    fixture_text = render(build_fixture())
    table_text = render(load_inference_table())
    targets = [
        (ANALYTICS_OUT, fixture_text),
        (APP_FIXTURE_OUT, fixture_text),
        (APP_INFERENCE_TABLE_OUT, table_text),
    ]
    if args.check:
        bad = False
        for path, text in targets:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"DIVERGENTE: {path}")
                bad = True
        if bad:
            return 1
        print("fixtures em dia")
        return 0
    for path, text in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"escrito: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
