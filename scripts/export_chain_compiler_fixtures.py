"""Fixture dourada do compilador de cadeias — `actions[]` na aresta (Fase 1/1b/2/5).

Mesmo padrão de `scripts/export_taxonomy_kind_fixtures.py` / `export_markov_weight_fixtures.py`:
o Python é a fonte, o App (`src/services/chainCompiler.ts`) é o espelho, e o arquivo escrito nos
DOIS repositórios é byte-idêntico. Regenere com `--check` sempre que a regra mudar.

Cada caso é uma lista de eventos crus + o `CompiledChain` inteiro serializado (estados, arestas
com `actions[]` COMPLETO, descartados, `state_after_event`). O conjunto cobre, um caso por classe
de erro que um porte pode cometer:

- ações EMPILHADAS numa transição (o núcleo da Fase 1 — nenhum estado inventado no meio);
- abertura e fechamento por âncora orientada, nas DUAS pontas (Fase 1b), incluindo o caminho
  declarativo (`submission|$terminal`), o caminho PGD/CDP e o `resolve_anchor_by_role`;
- os sete exemplos do dono da regra de inferência (Fase 2), inclusive a INSERÇÃO NO MEIO de um
  par observado — a prova viva da invariante 3 (ninguém depende do índice de uma ação);
- REDUNDÂNCIA: uma ação observada cuja orientação de SAÍDA já explica a inversão suprime o
  genérico (e nunca cria um — a linha D7 que a Fase 2 mediu e não cruza);
- ABSTENÇÃO de ator (`actor_readable=False`): uma DIFERENÇA de ator deixa de ser evidência;
- `nascent` (a cadeia começa num estado real, sem âncora e sem aresta inventada);
- evento transparente (`concept`) — descartado com trilha de auditoria, nunca silenciosamente.

    uv run python -m scripts.export_chain_compiler_fixtures
    uv run python -m scripts.export_chain_compiler_fixtures --check

Sem banco, sem rede, sem relógio: reexecutar produz byte-idêntico.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.chain_compiler import compile_chain, compile_two_sided  # noqa: E402

ANALYTICS_OUT = ROOT / "data" / "rating" / "chain_compiler_golden.json"
APP_OUT = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__"
    / "chainCompilerGolden.json"
)

#: ``(name, events, actor_readable)``. ``actor_readable=None`` means "let the default (True)
#: stand" — the flag is only spelled out where the case exists to exercise it.
Case = tuple[str, list[dict[str, Any]], bool | None]

_A, _B = "you", "partner"


def _ev(label: str, etype: str, actor: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"label": label, "type": etype}
    if actor is not None:
        row["actor"] = actor
    return row


CASES: list[Case] = [
    # ── Fase 1: actions STACK, no state is invented between them ───────────────────────────
    (
        "stacked_actions_one_transition",
        # Three consecutive actions between two observed states ride ONE edge. Before Fase 1
        # this produced two invented states (`chained submission`) and three edges.
        [
            _ev("Closed Guard", "guard", _A),
            _ev("Armbar", "submission", _A),
            _ev("Triangle Choke", "submission", _A),
            _ev("Kimura", "submission", _A),
            _ev("Mount", "control", _A),
        ],
        None,
    ),
    (
        "single_action_between_two_states",
        # The degenerate case of the same walk — one observed action, one edge, nothing added.
        [
            _ev("Closed Guard", "guard", _A),
            _ev("Hip Bump Sweep", "sweep", _A),
            _ev("Mount", "control", _A),
        ],
        None,
    ),
    # ── Fase 1b: the oriented anchors serve BOTH ends ─────────────────────────────────────
    (
        "opens_and_closes_on_an_action",
        # Opens on a pass (declarative `$start|pass -> start top`) and closes on a submission
        # (declarative `submission|$terminal -> finish`). Both ends declarative.
        [_ev("Guard Pass", "pass", _A), _ev("Armbar", "submission", _A)],
        None,
    ),
    (
        "opening_by_pgd_lamas_code",
        # A guard pull is LABEL-keyed (Lamas `PGD`), not type-keyed, so the table cannot express
        # it — `_opening_state` catches it before the table.
        [_ev("Guard Pull", "transition", _A), _ev("Closed Guard", "guard", _A)],
        None,
    ),
    (
        "opening_by_actor_role_fallback",
        # No declarative `$start|control` row and no Lamas code: resolves by the action's own
        # curated ACTOR role (`Back Take` reads `controlling` -> `start neutral`).
        [_ev("Back Take", "control", _A), _ev("Mount", "control", _A)],
        None,
    ),
    (
        "closing_by_exit_orientation_sweep_ends_on_top",
        # The Fase 2 correction to Fase 1b: a sweep ENDS on top, so the chain closes on
        # `start top` — `classify(...).actor_role` would have said `executor` (a relation, not a
        # position) and dropped it in `start neutral`.
        [_ev("Closed Guard", "guard", _A), _ev("Hip Bump Sweep", "sweep", _A)],
        None,
    ),
    (
        "closing_by_exit_orientation_escape_is_neutral",
        # MEASURED and contradicting the plan's own illustration: 75 of the corpus's 83 escape
        # events are literally "Escape to Standing"/"Stand-up Escape" — escapes to the FEET.
        [_ev("Mount", "control", _A), _ev("Escape to Standing", "escape", _A)],
        None,
    ),
    # ── Fase 2: the owner's seven examples of the inference rule ──────────────────────────
    (
        "rule_guard_to_guard_other_actor_is_a_sweep",
        # "Guarda A -> Guarda B => Raspagem A": inversion whose new dominant played the guard.
        [_ev("Closed Guard", "guard", _A), _ev("Closed Guard", "guard", _B)],
        None,
    ),
    (
        "rule_guard_to_control_other_actor_is_a_guard_pass",
        # "Guarda A -> Controle B => Passagem B": no inversion, guard -> control.
        [_ev("Closed Guard", "guard", _A), _ev("Side Control", "control", _B)],
        None,
    ),
    (
        "rule_control_to_guard_same_actor_is_a_reversal",
        # "Controle A -> Guarda A => Inversão B": the same athlete goes from controlling to
        # playing guard, so the OTHER one took over — `actor_is_opponent` is the only place that
        # answer can live, because a chain compiled per side has no name for them.
        [_ev("Side Control", "control", _A), _ev("Closed Guard", "guard", _A)],
        None,
    ),
    (
        "rule_control_to_guard_other_actor_is_a_recovery",
        # "Controle A -> Guarda B => Recomposição B": control held, the controlled athlete now
        # has a guard, and it is the GUARD player's action, not the dominant one's.
        [_ev("Side Control", "control", _A), _ev("Closed Guard", "guard", _B)],
        None,
    ),
    (
        "rule_guard_to_guard_same_actor_is_a_guard_transition",
        # "Meia-Guarda A -> Guarda Fechada A => Transição de Guarda A".
        [_ev("Half Guard", "guard", _A), _ev("Closed Guard", "guard", _A)],
        None,
    ),
    (
        "rule_control_to_control_same_actor_is_a_control_transition",
        # "Side Control A -> Montada A => Transição de Controle A".
        [_ev("Side Control", "control", _A), _ev("Mount", "control", _A)],
        None,
    ),
    (
        "rule_inserts_in_the_MIDDLE_of_an_observed_pair",
        # Contract invariant 3, live: the two OBSERVED states invert, and the inferred generic
        # lands BETWEEN the two observed actions — before the first one whose ENTRY orientation
        # already presupposes the new dominance (a pass needs the passer on top). A consumer
        # that reads `actions[0]` sees a different answer here than one that reads the list.
        [
            _ev("Side Control", "control", _A),
            _ev("Kimura", "submission", _A),
            _ev("Guard Pass", "pass", _B),
            _ev("Side Control", "control", _B),
        ],
        None,
    ),
    # ── Fase 2: redundancy, and the D7 line ───────────────────────────────────────────────
    (
        "redundancy_observed_exit_suppresses_the_generic",
        # The endpoints invert (bottom -> top), but the observed sweep's own EXIT orientation
        # already IS the bottom-to-top move. Naming it twice would invent a second one.
        [
            _ev("Closed Guard", "guard", _A),
            _ev("Hip Bump Sweep", "sweep", _A),
            _ev("Mount", "control", _A),
        ],
        None,
    ),
    (
        "no_inversion_between_the_endpoints_adds_nothing",
        # Two observed states that agree positionally, with an observed action between them:
        # the buffer is already a complete account, so nothing is inserted.
        [
            _ev("Mount", "control", _A),
            _ev("Kimura", "submission", _A),
            _ev("Side Control", "control", _A),
        ],
        None,
    ),
    # ── Fase 2: actor abstention ──────────────────────────────────────────────────────────
    (
        "actor_not_readable_falls_back_to_the_table",
        # Same events as the guard->guard sweep case. With the actor field unreadable (43.9% of
        # the prod corpus files every event under one athlete), an actor DIFFERENCE is not
        # evidence and the rule must not read an inversion out of it.
        [_ev("Closed Guard", "guard", _A), _ev("Closed Guard", "guard", _B)],
        False,
    ),
    (
        "actor_not_readable_control_pair",
        # `Mount A -> Side Control B` reads `control transition`, not `reversal B`.
        [_ev("Mount", "control", _A), _ev("Side Control", "control", _B)],
        False,
    ),
    # ── nascent + transparent ─────────────────────────────────────────────────────────────
    (
        "nascent_state_opens_the_chain",
        # A chain that opens on a real STATE opens there: no anchor, no invented edge into it.
        # Owner call 2026-08-27 — "costas não deveria ser presumido como precedido por top
        # start". The flag is how a consumer says so.
        [_ev("Back Control", "control", _A), _ev("Kimura", "submission", _A)],
        None,
    ),
    (
        "transparent_event_is_dropped_with_an_audit_trail",
        # A `concept` entry is neither action nor state (D1) — dropped, never folded into
        # either bucket, and it does not break the walk around it.
        [
            _ev("Closed Guard", "guard", _A),
            _ev("Pressure", "concept", _A),
            _ev("Kimura", "submission", _A),
            _ev("Mount", "control", _A),
        ],
        None,
    ),
    (
        "empty_input_compiles_to_an_empty_chain",
        [],
        None,
    ),
]

#: Two-sided cases exercise `compile_two_sided`'s own contract: per-side compilation, index
#: rewriting back to the ORIGINAL stream, the `dropped` pseudo-side, and the `actor_readable`
#: default it derives from its own buckets.
TWO_SIDED_CASES: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "two_sided_interleaved_stream",
        [
            _ev("Closed Guard", "guard", _A),
            _ev("Guard Pass", "pass", _B),
            _ev("Kimura", "submission", _A),
            _ev("Side Control", "control", _B),
            _ev("Mount", "control", _A),
        ],
    ),
    (
        "two_sided_one_sided_bout_abstains_from_actor",
        # Six or more sided events, ALL on one side: `attribution.MIN_EVENTS_FOR_ONE_SIDED`'s
        # own test, expressed in side terms. The other athlete did not stand still — her side
        # was never recorded — so `actor_readable` goes False without the caller saying so.
        [
            _ev("Closed Guard", "guard", _A),
            _ev("Kimura", "submission", _A),
            _ev("Mount", "control", _A),
            _ev("Armbar", "submission", _A),
            _ev("Side Control", "control", _A),
            _ev("Back Control", "control", _A),
        ],
    ),
    (
        "two_sided_event_with_no_side_lands_in_dropped",
        [
            _ev("Closed Guard", "guard", _A),
            _ev("Kimura", "submission", None),
            _ev("Mount", "control", _A),
        ],
    ),
]


def _side_of(event: dict[str, Any]) -> str | None:
    actor = event.get("actor")
    return {"you": "a", "partner": "b"}.get(str(actor)) if actor is not None else None


def _serialize(chain: Any) -> dict[str, Any]:
    return {
        "states": [asdict(s) for s in chain.states],
        "edges": [
            {
                "source_key": e.source_key,
                "target_key": e.target_key,
                "terminal": e.terminal,
                "actions": [asdict(a) for a in e.actions],
            }
            for e in chain.edges
        ],
        "dropped": [asdict(d) for d in chain.dropped],
        # JSON keys are strings; the App's own Map is keyed by number, so the reader parses.
        "state_after_event": {str(k): v for k, v in sorted(chain.state_after_event.items())},
    }


def build_fixture() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, events, actor_readable in CASES:
        kwargs = {} if actor_readable is None else {"actor_readable": actor_readable}
        compiled = compile_chain(events, **kwargs)  # type: ignore[arg-type]
        cases.append({
            "name": name,
            "events": events,
            "actor_readable": actor_readable,
            "expected": _serialize(compiled),
        })
    two_sided: list[dict[str, Any]] = []
    for name, events in TWO_SIDED_CASES:
        compiled_sides = compile_two_sided(events, _side_of)
        two_sided.append({
            "name": name,
            "events": events,
            "expected": {side: _serialize(c) for side, c in sorted(compiled_sides.items())},
        })
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_chain_compiler_fixtures.py",
        "contract": (
            "compile_chain(events, actor_readable=…) -> CompiledChain; an EDGE is a PATH — "
            "`actions` is the ordered sequence and no consumer may depend on an action's index "
            "(docs/taxonomy/03_ARESTA_COMO_CAMINHO.md, invariant 3). App mirror: "
            "src/services/chainCompiler.ts."
        ),
        "cases": cases,
        "two_sided": two_sided,
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
