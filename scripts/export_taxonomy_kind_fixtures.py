"""Fixtures douradas do contrato de taxonomia (D1/D2) — kind_of + tabela de inferência.

Mesmo padrão de `scripts/export_markov_weight_fixtures.py`. Três peças do contrato:

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
2b. **Os dois insumos da Fase 2** que o App não tem como derivar sozinho, porque moram em
   módulos Python curados (``data/taxonomy/state_orientation.json`` e ``analysis/attribution``):
   ``state_orientation`` (a tabela curada, verbatim) e ``actor_role``/``actor_role_default``
   (``attribution.classify(type,label).actor_role`` ACHATADO em ``"tipo|rótulo" -> papel`` mais
   um default por tipo). `classify` é uma função pura de tabelas finitas — as linhas curadas
   são enumeráveis, então achatar não é uma aproximação: é a mesma função, sem o segundo port
   das 74 linhas de `attribution`. É a mesma disciplina de `library_lookup.json`, na direção
   contrária (lá o Python lê um artefato derivado do App; aqui o App lê um derivado do Python).
3. **O lookup de biblioteca** (``data/taxonomy/library_lookup.json``) — o ÚNICO artefato que
   ``analysis.taxonomy_kind.resolve_library_entry``/``kind_of_entry`` leem em runtime. Este
   script é o único lugar autorizado a abrir o arquivo do App (é o gerador); CI roda esta
   Analytics sozinha, sem o repo irmão, então nenhum módulo de `analysis/`/`export/`/`db/`
   pode depender dele existindo no disco. Regenere com `--check` sempre que
   `grappling-arch.nodes.json` mudar — sem isso, `resolve_library_entry` fica com dados
   obsoletos e ninguém percebe.

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

from analysis import (
    attribution,  # noqa: E402
    taxonomy_kind,  # noqa: E402
)
from analysis.names import _normalize_name  # noqa: E402
from analysis.taxonomy_kind import (  # noqa: E402
    kind_of,
    load_inference_table,
    load_orientation_table,
    orientation_for_inference,
    orientation_of,
)
from export.app_node_scores import _name_variants, canonical_label  # noqa: E402

APP_NODES_PATH = ROOT.parent / "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json"
ANALYTICS_OUT = ROOT / "data" / "rating" / "taxonomy_kind_golden.json"
APP_FIXTURE_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__" / "taxonomyKindGolden.json"
)
APP_INFERENCE_TABLE_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "data" / "taxonomy_inference_table.json"
)
LIBRARY_LOOKUP_OUT = ROOT / "data" / "taxonomy" / "library_lookup.json"


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


def build_library_lookup() -> dict[str, list[str]]:
    """``{_normalize_name(variant): [canonical_english_label, library_type]}`` over every App
    library entry's ``name``/``variations``/``translations.*``. This is the artifact
    ``analysis.taxonomy_kind.resolve_library_entry`` reads at runtime instead of opening the
    App's JSON directly (root CLAUDE.md: no `analysis/`/`export/`/`db/` module may depend on a
    sibling repo checkout).

    Collisions (two entries' variant texts normalize to the same key) are real — 11 in the
    141-entry library, measured 2026-08-27: ``side mount`` (Side Control/Mount), ``ude garami``
    (Kimura/Americana), ``harai goshi`` (Hip Throw/Sweeping Hip Throw), ``deep half guard`` +
    ``zguard`` + ``knee shield`` (Half Guard/Z-Guard/Deep Half Guard overlap), ``ashi garami``
    (X-Guard/Single Leg X), ``saddle`` (Back Control/Saddle), ``body lock das costas`` (Body
    Lock from Back/Body Triangle), ``biceps slicer`` (Calf Slicer/Bicep Slicer), ``presso``
    (Pressure Pass/Pressure). Resolved deterministically: FIRST entry wins in the library's
    own file order (a committed, static file, so file order is a fixed reproducible order —
    same first-wins-by-file-order convention ``export.app_node_scores.build_scores``
    documents for the identical file), never overwritten by a later entry.
    """
    nodes = json.loads(APP_NODES_PATH.read_text(encoding="utf-8"))
    lookup: dict[str, list[str]] = {}
    for node in nodes:
        canon = canonical_label(node)
        if not canon:
            continue
        typ = str(node.get("type") or "")
        for text in _name_variants(node):
            key = _normalize_name(text)
            if key and key not in lookup:
                lookup[key] = [canon, typ]
    return lookup


def _curated_pairs() -> set[tuple[str, str]]:
    """Every ``(event_type, label)`` pair ``attribution.classify`` names explicitly — the
    ``_LABEL`` exact rows plus each type's own curated label sets. Read off the module rather
    than re-listed here, so a new curated row lands in the fixture on the next regeneration
    instead of silently staying out of the contract."""
    pairs: set[tuple[str, str]] = set(attribution._LABEL)
    for label_set, typ in (
        (attribution._GUARD_BOTTOM, "guard"),
        (attribution._GUARD_NEUTRAL, "guard"),
        (attribution._CONTROL_TOP, "control"),
        (attribution._CONTROL_BACK, "control"),
        (attribution._CONTROL_GRIP, "control"),
    ):
        for label in label_set:
            pairs.add((typ, label))
    return pairs


def build_actor_roles() -> tuple[dict[str, str], dict[str, str]]:
    """``({"<type>|<normalized label>": actor_role}, {"<type>": default_actor_role})`` —
    ``analysis.attribution.classify(...).actor_role`` flattened.

    Every row is produced by CALLING ``classify``, never by re-reading its tables in this
    file's own order, so the precedence inside it (``_LABEL`` exact row > the type's curated
    label set > the type default) is preserved by construction rather than re-implemented. The
    key domain is the union of every curated ``(type, label)`` pair, which is finite and small;
    a pair outside it is exactly what the per-type default answers, and a type outside THAT is
    ``unknown`` (``classify``'s own last line).

    This is level 3 of ``orientation_for_inference`` — the level the App would otherwise need a
    second copy of ``attribution.py`` to reach.
    """
    labelled = _curated_pairs()
    # A label the tables never name: forces `classify` down to its own per-type answer. The
    # sentinel cannot collide with a real row (`_normalize_name` strips punctuation, so no
    # curated key can contain it).
    unnamed = "\x00 nao curado \x00"
    types = sorted({t for t, _ in labelled} | set(attribution.EVENT_TYPES))
    default = {t: attribution.classify(t, unnamed).actor_role for t in types}
    default[""] = attribution.classify("", unnamed).actor_role

    rows: dict[str, str] = {}
    for typ, label in sorted(labelled):
        role = attribution.classify(typ, label).actor_role
        if role != default.get(typ, "unknown"):
            rows[f"{typ}|{_normalize_name(label)}"] = role
    return rows, default


def build_orientation_probes() -> dict[str, dict[str, str]]:
    """``{"<type>|<label>": {"value", "source"}}`` for ``orientation_for_inference`` — the
    three-level reading the Fase 2 rule runs on BOTH endpoint states.

    Not a fourth input: it is the OUTPUT of composing the three above, pinned so the App's port
    is checked end to end instead of only on its parts. Probe set = every curated
    ``attribution`` pair (where levels 1 and 2 miss and level 3 has to answer) + every technique
    -library entry under its own library type (where level 1 or 2 usually answers) + the D2
    generic states (the anchors the rule compares against).
    """
    probes = _curated_pairs()
    for node in json.loads(APP_NODES_PATH.read_text(encoding="utf-8")):
        label = canonical_label(node)
        if label:
            probes.add((str(node.get("type") or ""), label))
    for entry in load_inference_table()["generic_states"].values():
        probes.add((str(entry["type"]), str(entry["node_key"])))
    out: dict[str, dict[str, str]] = {}
    for typ, label in sorted(probes):
        reading = orientation_for_inference(typ, label)
        out[f"{typ}|{label}"] = {"value": reading.value, "source": reading.source}
    return out


def build_fixture() -> dict[str, Any]:
    actor_role, actor_role_default = build_actor_roles()
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_taxonomy_kind_fixtures.py",
        "contract": (
            "kind_of(label, type) -> 'action'|'state'|'transparent' (D1); "
            "inference_table = D2's structural pair -> generic node/edge lookup, verbatim; "
            "state_orientation = the curated orientation table, verbatim; "
            "actor_role[type|label] (falling back to actor_role_default[type], then 'unknown') "
            "= attribution.classify(type,label).actor_role, level 3 of "
            "orientation_for_inference; orientation_for_inference[type|label] = the composed "
            "three-level reading."
        ),
        "kinds": build_kinds(),
        "inference_table": load_inference_table(),
        "state_orientation": load_orientation_table(),
        "actor_role": actor_role,
        "actor_role_default": actor_role_default,
        "orientation_for_inference": build_orientation_probes(),
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="não escreve; falha se o que está em disco divergir do gerado")
    args = ap.parse_args()

    # Build + prime the library lookup BEFORE the fixture: `build_orientation_probes` (inside
    # `build_fixture`) calls `orientation_for_inference` -> `resolve_library_entry`, which
    # lazily caches `library_lookup.json` from DISK on first call. Read-before-write bug (found
    # 2026-09-04): computing `fixture_text` first let that cache load the STALE file this same
    # script was about to overwrite, freezing e.g. `back take -> Back Control` in the golden
    # until a second run. Priming the cache with the freshly BUILT lookup (never touching disk)
    # makes one run produce what used to take two.
    lookup = build_library_lookup()
    lookup_text = render(lookup)
    taxonomy_kind._LIBRARY_LOOKUP = {k: (v[0], v[1]) for k, v in lookup.items()}

    fixture_text = render(build_fixture())
    table_text = render(load_inference_table())
    targets = [
        (ANALYTICS_OUT, fixture_text),
        (APP_FIXTURE_OUT, fixture_text),
        (APP_INFERENCE_TABLE_OUT, table_text),
        (LIBRARY_LOOKUP_OUT, lookup_text),
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
