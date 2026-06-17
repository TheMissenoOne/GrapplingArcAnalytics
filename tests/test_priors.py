"""Tests for athlete priors: next-move prior, suggestions, CV re-ranking."""

from __future__ import annotations

import pytest

from analysis.athlete_graph import build_athlete_graph
from analysis.priors import next_move_prior, rerank_classification, suggest_next
from cv.vocab_map import build_vocab_index

# Position nodes the CV can map ViCoS classes onto.
NODES = [
    {"name": "Montada", "type": "control", "variations": ["mount", "full mount"]},
    {"name": "Costas", "type": "control", "variations": ["back", "back control"]},
    {"name": "Controle Lateral", "type": "control", "variations": ["side control"]},
]


def _athlete_graph():
    """Athlete goes Montada→Costas 3×, Montada→Controle Lateral 1×."""
    rounds = []
    for _ in range(3):
        rounds.append({"entries": [
            {"label": "Montada", "type": "control", "actor": "you"},
            {"label": "Costas", "type": "control", "actor": "you"},
        ]})
    rounds.append({"entries": [
        {"label": "Montada", "type": "control", "actor": "you"},
        {"label": "Controle Lateral", "type": "control", "actor": "you"},
    ]})
    return build_athlete_graph("me", [{"rounds": rounds}])


def test_next_move_prior_uses_display_labels() -> None:
    prior = next_move_prior(_athlete_graph(), "Montada")
    assert prior == pytest.approx({"Costas": 0.75, "Controle Lateral": 0.25})


def test_next_move_prior_unknown_label() -> None:
    assert next_move_prior(_athlete_graph(), "Guarda Fechada") == {}


def test_suggest_next_ranks_by_prior() -> None:
    ranked = suggest_next(_athlete_graph(), "Montada", k=2)
    assert [label for label, _ in ranked] == ["Costas", "Controle Lateral"]


def test_rerank_prior_overrides_marginal_cv() -> None:
    graph = _athlete_graph()
    index = build_vocab_index(NODES)
    # CV slightly favors side control, but the athlete almost always takes the back.
    class_probs = {"back_top": 0.45, "side control_top": 0.55}

    top_blended, _, _ = rerank_classification(class_probs, "Montada", graph, index, alpha=0.7)
    assert top_blended == "Costas"  # prior pulls it to the back take

    top_raw, _, _ = rerank_classification(class_probs, "Montada", graph, index, alpha=1.0)
    assert top_raw == "Controle Lateral"  # alpha=1 ignores the prior → raw argmax


def test_rerank_cold_start_passthrough() -> None:
    graph = _athlete_graph()
    index = build_vocab_index(NODES)
    class_probs = {"back_top": 0.45, "side control_top": 0.55}
    # Unknown prev label → empty prior → CV output untouched.
    top, _, ranked = rerank_classification(class_probs, "Guarda Fechada", graph, index)
    assert top == "Controle Lateral"
    assert sum(score for _, score in ranked) == pytest.approx(1.0)


def test_rerank_empty_class_probs() -> None:
    graph = _athlete_graph()
    index = build_vocab_index(NODES)
    assert rerank_classification({}, "Montada", graph, index) == ("", 0.0, [])
