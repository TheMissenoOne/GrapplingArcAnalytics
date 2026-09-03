"""Audita a ontologia (N0) — cinco famílias de defeito, contadas e exemplificadas.

Contrato: `docs/taxonomy/04_ONTOLOGIA_CANONICA.md`. Este script NÃO conserta nada; ele mede, e
com `--check` recusa qualquer família que CRESÇA em relação a `data/taxonomy/audit_baseline.json`.
É a rede que segura o programa N1-N3: cada fase move rótulos, e a única maneira honesta de dizer
"não pioramos" é contar as mesmas cinco coisas antes e depois.

As cinco famílias:

  ``dual_identity``       um rótulo que lê ``state`` sob um ``type`` e ``action`` sob outro. A
                          classe pertence ao CONCEITO; se ela muda com o log, a classe é derivada
                          do log e não da ontologia.
  ``alias_candidates``    dois ``node_key`` distintos que quase certamente são o mesmo nó (hífen/
                          espaço/plural/``close(d)``, ou distância de edição <= 2), ambos com
                          evento no corpus. Colapsá-los MOVE ``node_key`` — fase N1, replay.
  ``composites``          rótulo com `` to `` ou `` / `` que não está na tabela curada
                          ``data/taxonomy/composite_labels.json`` (ausente = vazia). Fase N2
                          decompõe na INGESTÃO; aqui só se conta.
  ``states_without_orientation``  ``kind == 'state'`` sem linha em ``state_orientation.json``.
  ``athlete_nodes_typed_technique``  ``graph_nodes`` de atleta com ``type='technique'`` — o
                          default do schema, isto é, nenhuma ontologia. Só com banco.

Entrada: ``matches.sequence`` via ``db.base.db_session`` quando há ``DATABASE_URL``, ou
``--dump caminho.json`` (lista de lutas, ou ``{"pairs": [{type,label,count}]}`` — o formato que
este próprio script imprime com ``--emit-dump``). Sem banco, ``athlete_nodes_typed_technique``
fica ``None`` e o baseline é preservado nessa família (medir zero por ausência de banco não é
medir zero).

    uv run python -m scripts.audit_ontology                # relatório
    uv run python -m scripts.audit_ontology --check        # falha se alguma família cresceu
    uv run python -m scripts.audit_ontology --write-baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.names import _normalize_name, canonicalize  # noqa: E402
from analysis.taxonomy_kind import (  # noqa: E402
    kind_of_entry,
    load_orientation_table,
)

BASELINE_PATH = ROOT / "data" / "taxonomy" / "audit_baseline.json"
COMPOSITE_TABLE_PATH = ROOT / "data" / "taxonomy" / "composite_labels.json"

#: Separadores que denunciam um rótulo composto. `` / `` e `` to `` com espaços dos dois lados —
#: sem espaço, "50/50" e "chest-to-chest" seriam falsos positivos por construção.
COMPOSITE_SEPARATORS = (" to ", " / ")

#: Falsos positivos fixos: são nomes de UMA posição que por acaso contêm o separador. Lista curta
#: e fechada de propósito — se ela começar a crescer, o critério é que está errado, não a lista.
COMPOSITE_FALSE_POSITIVES = frozenset({
    "shin to shin guard", "chest-to-chest half guard", "50/50 guard",
})

#: Sufixos/afixos que fazem duas grafias serem o MESMO nó. Aplicados sobre o `node_key` já
#: normalizado (`canonicalize(_normalize_name(label))`), que não tem hífen nem acento.
_ALIAS_SUFFIXES = ("s", "es", "d", "ed")


def _node_key(label: str) -> str:
    return canonicalize(_normalize_name(str(label or "")))


def _alias_fold(key: str) -> str:
    """A forma sob a qual duas grafias colidem se forem plural/particípio uma da outra."""
    words = []
    for w in key.split():
        for suf in _ALIAS_SUFFIXES:
            if len(w) > len(suf) + 2 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        words.append(w)
    return " ".join(words)


def _near_duplicate(a: str, b: str, limit: int = 2) -> bool:
    """Duas grafias próximas o bastante para MERECEREM revisão humana como alias.

    Distância de edição <= ``limit`` é o critério do plano, e sozinho ele é ruidoso em chave
    curta: `tap`/`trip` e `knee bar`/`knee tap` também passam, e não são a mesma coisa. Duas
    guardas, ambas medidas neste corpus:

    - **proporção**: a diferença tem de ser <= 1/5 do comprimento da chave mais longa. Corta
      `tap`/`trip` (2 de 4), `knee bar`/`knee tap` (2 de 8), `leg lace`/`leg lock`,
      `kimura grip`/`kimura trap`; mantém `shin on shin`/`shin to shin` (2 de 18).
    - **guardas nomeadas**: `kguard`/`xguard`/`zguard`/`guard` estão todas a uma edição umas das
      outras por construção (o hífen de "K-Guard" some na normalização). Guardas diferentes com
      o mesmo sufixo NÃO são alias, e nenhuma proporção separa isso. Só vale para chave de UMA
      palavra: `shin on shin guard`/`shin to shin guard` também termina em "guard" e É alias.

    ponytail: `SequenceMatcher` no lugar de uma matriz de Levenshtein — ele dá um LIMITE
    INFERIOR da distância, que é o lado certo para errar numa lista de candidatos (nunca acusa
    um par mais distante do que ele é), e a diferença de tamanhos corta quase tudo antes disso.
    """
    if abs(len(a) - len(b)) > limit:
        return False
    if a == b:
        return True
    if (" " not in a and " " not in b
            and a.endswith("guard") and b.endswith("guard") and a[:-5] != b[:-5]):
        return False
    matched = sum(bl.size for bl in SequenceMatcher(None, a, b).get_matching_blocks())
    distance = max(len(a), len(b)) - matched
    return distance <= limit and distance * 5 <= max(len(a), len(b))


# ── entrada ─────────────────────────────────────────────────────────────────────
def pairs_from_sequences(bouts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``[{type, label, count}]`` sobre uma lista de lutas com ``sequence``."""
    counter: Counter[tuple[str, str]] = Counter()
    for bout in bouts:
        for ev in bout.get("sequence") or []:
            if isinstance(ev, Mapping):
                counter[(str(ev.get("type") or ""), str(ev.get("label") or ""))] += 1
    return [{"type": t, "label": lab, "count": n}
            for (t, lab), n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def pairs_from_dump(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping) and "pairs" in raw:
        return list(raw["pairs"])
    return pairs_from_sequences(raw)


def pairs_from_db() -> tuple[list[dict[str, Any]], int | None]:
    """``(pares, nós de atleta typed 'technique')`` — somente leitura."""
    from sqlalchemy import func, select

    from db.base import db_session
    from db.models import Graph, GraphNode, Match

    with db_session() as session:
        bouts = [{"sequence": seq} for (seq,) in session.execute(select(Match.sequence)) if seq]
        # node_keys DISTINTOS, não linhas: o defeito é "este nó não tem ontologia", e o mesmo
        # nó aparecendo no grafo de trinta atletas é um defeito, não trinta.
        typed = session.execute(
            select(func.count(func.distinct(GraphNode.node_key)))
            .select_from(GraphNode)
            .join(Graph, Graph.id == GraphNode.graph_id)
            .where(Graph.owner_kind == "athlete", GraphNode.type == "technique")
        ).scalar_one()
    return pairs_from_sequences(bouts), int(typed)


# ── as cinco famílias ───────────────────────────────────────────────────────────
def find_dual_identity(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_label: dict[str, dict[str, list[tuple[str, int]]]] = defaultdict(lambda: defaultdict(list))
    for p in pairs:
        kind = kind_of_entry(p["label"], p["type"])
        by_label[_node_key(p["label"])][kind].append((p["type"], int(p["count"])))
    out = []
    for key, kinds in sorted(by_label.items()):
        if len(kinds) > 1:
            out.append({
                "node_key": key,
                "kinds": {k: sorted(v) for k, v in sorted(kinds.items())},
                "count": sum(n for v in kinds.values() for _, n in v),
            })
    return out


def find_alias_candidates(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for p in pairs:
        key = _node_key(p["label"])
        if key:
            counts[key] += int(p["count"])
    keys = sorted(counts)
    out = []
    for i, a in enumerate(keys):
        fold_a = _alias_fold(a)
        for b in keys[i + 1:]:
            reason = None
            if fold_a == _alias_fold(b):
                reason = "plural/particípio"
            elif a.replace(" ", "") == b.replace(" ", ""):
                reason = "espaço"
            elif _near_duplicate(a, b):
                reason = "distância <= 2"
            if reason:
                out.append({"a": a, "b": b, "reason": reason,
                            "count": counts[a] + counts[b]})
    return sorted(out, key=lambda r: (-r["count"], r["a"], r["b"]))


def _composite_table() -> set[str]:
    if not COMPOSITE_TABLE_PATH.is_file():
        return set()      # N2 ainda não escreveu a tabela; ausente é vazia, não é erro
    return {_normalize_name(k) for k in json.loads(
        COMPOSITE_TABLE_PATH.read_text(encoding="utf-8"))}


def find_composites(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    curated = _composite_table()
    seen: dict[str, int] = {}
    for p in pairs:
        raw = str(p["label"] or "")
        low = raw.lower()
        if not any(sep in low for sep in COMPOSITE_SEPARATORS):
            continue
        if low.strip() in COMPOSITE_FALSE_POSITIVES:
            continue
        if _normalize_name(raw) in curated:
            continue
        seen[raw] = seen.get(raw, 0) + int(p["count"])
    return [{"label": lab, "count": n}
            for lab, n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


def find_states_without_orientation(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = load_orientation_table()
    seen: dict[str, int] = {}
    for p in pairs:
        if kind_of_entry(p["label"], p["type"]) != "state":
            continue
        key = _node_key(p["label"])
        if key and key not in table:
            seen[key] = seen.get(key, 0) + int(p["count"])
    return [{"node_key": k, "count": n}
            for k, n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


# ── relatório ───────────────────────────────────────────────────────────────────
FAMILIES = ("dual_identity", "alias_candidates", "composites", "states_without_orientation",
            "athlete_nodes_typed_technique")


def audit(pairs: list[dict[str, Any]], athlete_typed: int | None) -> dict[str, Any]:
    return {
        "events": sum(int(p["count"]) for p in pairs),
        "distinct_pairs": len(pairs),
        "dual_identity": find_dual_identity(pairs),
        "alias_candidates": find_alias_candidates(pairs),
        "composites": find_composites(pairs),
        "states_without_orientation": find_states_without_orientation(pairs),
        "athlete_nodes_typed_technique": athlete_typed,
    }


def counts_of(report: Mapping[str, Any]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for fam in FAMILIES:
        value = report.get(fam)
        out[fam] = value if value is None or isinstance(value, int) else len(value)
    return out


def print_report(report: Mapping[str, Any]) -> None:
    print(f"corpus: {report['events']} eventos, {report['distinct_pairs']} pares (type,label)\n")
    for fam in FAMILIES:
        value = report[fam]
        if value is None:
            print(f"{fam}: (sem banco — não medido)")
            continue
        if isinstance(value, int):
            print(f"{fam}: {value}")
            continue
        print(f"{fam}: {len(value)}")
        for row in value[:5]:
            print(f"    {row}")
        if len(value) > 5:
            print(f"    … +{len(value) - 5}")
        print()


def load_baseline() -> dict[str, Any]:
    baseline: dict[str, Any] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return baseline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", type=Path, help="JSON de lutas (ou {'pairs': …}) em vez do banco")
    ap.add_argument("--check", action="store_true",
                    help="falha se qualquer família crescer em relação ao baseline")
    ap.add_argument("--write-baseline", action="store_true",
                    help="grava data/taxonomy/audit_baseline.json com os números de agora")
    ap.add_argument("--json", action="store_true", help="imprime o relatório inteiro em JSON")
    args = ap.parse_args(argv)

    if args.dump:
        pairs, athlete_typed = pairs_from_dump(args.dump), None
    else:
        pairs, athlete_typed = pairs_from_db()

    report = audit(pairs, athlete_typed)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print_report(report)

    counts = counts_of(report)
    if args.write_baseline:
        previous = load_baseline() if BASELINE_PATH.is_file() else {}
        payload = {
            "generated_by": "scripts/audit_ontology.py --write-baseline",
            "contract": "docs/taxonomy/04_ONTOLOGIA_CANONICA.md",
            "events": report["events"],
            "counts": {k: (counts[k] if counts[k] is not None
                           else previous.get("counts", {}).get(k)) for k in FAMILIES},
            "reclassified": previous.get("reclassified", {}),
        }
        BASELINE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        print(f"baseline gravado: {BASELINE_PATH}")

    if args.check:
        base = load_baseline()["counts"]
        grew = [(fam, base.get(fam), counts[fam]) for fam in FAMILIES
                if counts[fam] is not None and base.get(fam) is not None
                and counts[fam] > base[fam]]
        for fam, was, now in grew:
            print(f"REGREDIU: {fam} {was} -> {now}", file=sys.stderr)
        return 1 if grew else 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
