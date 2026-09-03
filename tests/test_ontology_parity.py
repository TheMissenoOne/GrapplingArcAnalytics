"""P1 da fase N0 — o que a troca de autoridade move e o que ela NÃO move.

`docs/taxonomy/04_ONTOLOGIA_CANONICA.md` §3 troca quem decide state-vs-action. Isso muda a
classe de 15 pares `(type, label)` e não pode mudar mais nada. Duas provas, sobre o MESMO
fixture (os eventos de entrada de `data/rating/chain_compiler_golden.json`, que é committed):

1. **`observations_for_side` é intocado.** Ele lê os eventos CRUS por `node_key_of(label)` e
   nunca pergunta se algo é ação ou estado, então o multiconjunto de observações de rating tem
   de ser idêntico byte a byte — sem exceções, nem para os rótulos reclassificados. É esta
   invariante que torna a mudança barata: nenhum ELO se move, nenhum replay é exigido.
2. **O compilador move exatamente os rótulos declarados.** Toda diferença no multiconjunto
   `(action node_key, actor, count)` tem de corresponder a uma entrada de `reclassified` em
   `data/taxonomy/audit_baseline.json`. Um rótulo que se mexeu sem estar na lista é uma
   reclassificação acidental, que é precisamente o defeito este teste existe para pegar.

O lado "antes" vive em `tests/fixtures/ontology_parity_before.json` — medido uma vez num
checkout limpo de `main` e nunca regenerado (ver o cabeçalho do próprio arquivo).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from analysis.chain_compiler import compile_chain
from analysis.rating_v2.node_rating import observations_for_side
from analysis.taxonomy_kind import kind_of_entry, load_inference_table

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "rating" / "chain_compiler_golden.json"
BEFORE = Path(__file__).parent / "fixtures" / "ontology_parity_before.json"
BASELINE = ROOT / "data" / "taxonomy" / "audit_baseline.json"


def _cases() -> list[list[dict[str, Any]]]:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [case["events"] for case in golden["cases"]]


def _as_counter(rows: list[list[Any]]) -> Counter[tuple[Any, ...]]:
    return Counter({tuple(row[:-1]): row[-1] for row in rows})


def _before() -> dict[str, Any]:
    snapshot: dict[str, Any] = json.loads(BEFORE.read_text(encoding="utf-8"))
    return snapshot


def _reclassified() -> dict[str, Any]:
    baseline: dict[str, Any] = json.loads(BASELINE.read_text(encoding="utf-8"))
    rows: dict[str, Any] = baseline["reclassified"]
    return rows


def test_observations_for_side_is_untouched_by_the_authority_swap() -> None:
    """Sem exceção nenhuma: o rating lê evento cru, não classe."""
    observed: Counter[tuple[Any, ...]] = Counter()
    for events in _cases():
        # `observations_for_side` exige `actor_id` e `successful`; o golden do compilador carrega
        # `actor` e nada de resultado, então a promoção é feita aqui, igual para os dois lados.
        rating_events = [{**e, "actor_id": e.get("actor"), "successful": True} for e in events]
        for obs in observations_for_side(rating_events, "you", None):
            observed[(obs.node_key, round(obs.weight, 6))] += 1

    assert observed == _as_counter(_before()["observations"])


def test_compiler_action_multiset_moves_only_where_reclassified_says() -> None:
    table = load_inference_table()
    after: Counter[tuple[Any, ...]] = Counter()
    for events in _cases():
        for edge in compile_chain(events, inference_table=table).edges:
            for action in edge.actions:
                if not action.inferred:
                    after[(action.key, action.actor)] += 1

    before = _as_counter(_before()["actions"])
    allowed = {entry["node_key"] for entry in _reclassified().values()}
    moved = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    assert {key[0] for key in moved} <= allowed, sorted(moved)


def test_every_reclassified_row_really_holds_today() -> None:
    """A lista não pode virar folclore: cada linha afirma a classe de HOJE, e a classe anterior
    é a que a linha registra. Se N1/N2 mexer num destes rótulos, esta asserção obriga a
    atualizar a lista junto — que é o mesmo contrato do baseline da auditoria."""
    for pair, entry in _reclassified().items():
        event_type, label = pair.split("|", 1)
        assert kind_of_entry(label, event_type) == entry["to"], pair
        assert entry["from"] != entry["to"], pair
