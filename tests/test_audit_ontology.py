"""`scripts/audit_ontology.py` sobre um dump sintético — uma luta desenhada para conter
exatamente um exemplar de cada família de defeito, e nada além disso.

Fixture pequeno de propósito (a disciplina de `docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`: casos
minúsculos antes de dados reais). O corpus de verdade é medido pelo script contra o banco; o
que se prova aqui é que cada detector acha o que deve e ignora os falsos positivos declarados.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.audit_ontology import (
    audit,
    counts_of,
    find_alias_candidates,
    find_composites,
    find_dual_identity,
    main,
    pairs_from_dump,
    pairs_from_sequences,
)

# Um evento de cada coisa:
#   Turtle Position sob dois `type`  -> NÃO é dupla identidade (a tabela curada resolve os dois
#                                       lados como state — é a prova de que o detector não grita
#                                       só porque o rótulo aparece sob dois tipos)
#   Guard Recovery guard vs escape   -> dupla identidade real
#   Reap Sweep / Reaps Sweep         -> alias por particípio (synthetic — the corpus's real
#                                       participle pair, "close guard"/"closed guard", is now
#                                       merged by names.SYNONYMS (N1), so it no longer reaches
#                                       this detector; a fresh unresolved pair proves the fold)
#   Knee Cut / Kneecut               -> alias por espaço (synthetic, same reason as above —
#                                       "take down"/"takedown" is now SYNONYMS-merged too)
#   Shin to Shin Guard, 50/50 Guard  -> compostos FALSOS (contêm " to " / "/"), ignorados
#   Escape to Standing               -> composto real
#   Front Headlock                   -> state sem orientação
SYNTHETIC: list[dict[str, Any]] = [{"sequence": [
    {"type": "control", "label": "Turtle Position"},
    {"type": "guard", "label": "Turtle Position"},
    {"type": "guard", "label": "Guard Recovery"},
    {"type": "escape", "label": "Guard Recovery"},
    {"type": "sweep", "label": "Reap Sweep"},
    {"type": "sweep", "label": "Reaps Sweep"},
    {"type": "pass", "label": "Knee Cut"},
    {"type": "pass", "label": "Kneecut"},
    {"type": "guard", "label": "Shin to Shin Guard"},
    {"type": "guard", "label": "50/50 Guard"},
    {"type": "escape", "label": "Escape to Standing"},
    {"type": "control", "label": "Front Headlock"},
]}]


def _pairs() -> list[dict[str, Any]]:
    return pairs_from_sequences(SYNTHETIC)


def test_dual_identity_needs_two_kinds_not_two_types() -> None:
    keys = {row["node_key"] for row in find_dual_identity(_pairs())}
    assert "guard recovery" in keys
    assert "turtle position" not in keys


def test_alias_candidates_catch_participle_and_space() -> None:
    found = {(row["a"], row["b"]) for row in find_alias_candidates(_pairs())}
    assert ("reap sweep", "reaps sweep") in found
    assert ("knee cut", "kneecut") in found


def test_composites_skip_the_declared_false_positives() -> None:
    labels = {row["label"] for row in find_composites(_pairs())}
    assert labels == {"Escape to Standing"}


def test_states_without_orientation_and_full_counts() -> None:
    report = audit(_pairs(), athlete_typed=None)
    missing = {row["node_key"] for row in report["states_without_orientation"]}
    assert "front headlock" in missing
    assert "closed guard" not in missing        # tem linha curada
    counts = counts_of(report)
    assert counts["athlete_nodes_typed_technique"] is None   # sem banco, sem afirmação


def test_check_fails_when_a_family_grows(tmp_path: Path, monkeypatch: Any) -> None:
    """`--check` é o portão: ele compara com o baseline e recusa crescimento. Aqui o baseline é
    um arquivo temporário com zeros, então TODA família deste dump cresce."""
    import scripts.audit_ontology as mod

    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(SYNTHETIC), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"counts": dict.fromkeys(mod.FAMILIES, 0)}), encoding="utf-8")
    monkeypatch.setattr(mod, "BASELINE_PATH", baseline)

    assert main(["--dump", str(dump), "--check"]) == 1

    generous = {k: 99 for k in mod.FAMILIES}
    baseline.write_text(json.dumps({"counts": generous}), encoding="utf-8")
    assert main(["--dump", str(dump), "--check"]) == 0


def test_dump_accepts_both_shapes(tmp_path: Path) -> None:
    """Lista de lutas e ``{"pairs": …}`` (a forma que ``--json`` imprime) leem igual."""
    bouts = tmp_path / "bouts.json"
    bouts.write_text(json.dumps(SYNTHETIC), encoding="utf-8")
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps({"pairs": _pairs()}), encoding="utf-8")
    assert pairs_from_dump(bouts) == pairs_from_dump(pairs) == _pairs()
