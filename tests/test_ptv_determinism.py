"""PtV read-order determinism (docs/rating_v2/09_SUCESSO_DERIVADO.md §4).

Root cause: a label carrying >1 `type` across the corpus (e.g. "Takedown" scored
as both takedown and transition) used to resolve by first-writer-wins
(`node_type.setdefault`), so the node's type — and everything PtV derives from
it (`_shaping`, `_terminal_rate`) — depended on which match was read first.
Fixed by majority-occurrence resolution (tie-broken by type name) in
`transitions/build_graph.py`, plus a sorted Gauss-Seidel sweep in
`path_to_victory` for the residual float-summation-order jitter.
"""

from __future__ import annotations

import random
from typing import Any

from analysis.path_to_victory import path_to_victory
from analysis.transitions.build_graph import network_from_sequences

TD, BC, RNC = "Takedown", "Back Control", "Rear Naked Choke"


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


def _sequences() -> list[list[dict[str, Any]]]:
    # "Takedown" is majority type=takedown (2 occurrences) vs type=transition (1) —
    # ambiguous exactly like the 15/211 corpus labels the ADR measured.
    return [
        [_e(TD, "takedown", "A"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(TD, "takedown", "A"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(TD, "transition", "A"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
    ]


def test_ambiguous_label_resolves_to_majority_type_regardless_of_read_order() -> None:
    seqs = _sequences()
    for trial in range(10):
        s = list(seqs)
        random.Random(trial).shuffle(s)
        g = network_from_sequences(s)
        assert g.nodes[TD]["type"] == "takedown"


def test_tied_type_counts_break_by_sorted_type_name() -> None:
    # Exactly one occurrence of each type -> tie -> alphabetically smallest wins,
    # deterministically, regardless of which sequence is read first.
    seqs = [
        [_e("Ambiguous", "guard", "A")],
        [_e("Ambiguous", "escape", "A")],
    ]
    for trial in range(6):
        s = list(seqs)
        random.Random(trial).shuffle(s)
        g = network_from_sequences(s)
        assert g.nodes["Ambiguous"]["type"] == "escape"  # "escape" < "guard"


def test_path_to_victory_identical_across_shuffled_read_orders() -> None:
    seqs = _sequences()
    base = path_to_victory(network_from_sequences(seqs))
    for trial in range(10):
        s = list(seqs)
        random.Random(trial).shuffle(s)
        v = path_to_victory(network_from_sequences(s))
        assert v == base
