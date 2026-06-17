"""Tests for the embedded-Qdrant athlete vector store + similar-athlete blend."""

from __future__ import annotations

import pytest

from analysis.athlete_graph import build_athlete_graph
from analysis.priors import next_move_prior
from analysis.vector_store import AthleteVectorStore, build_label_vocab, ingest_athlete

NODES = [
    {"name": "Montada", "type": "control"},
    {"name": "Costas", "type": "control"},
    {"name": "Controle Lateral", "type": "control"},
]
VOCAB = build_label_vocab(NODES)


def _sessions(transitions: list[tuple[str, str]], reps: int = 1) -> list[dict]:
    rounds = []
    for _ in range(reps):
        for a, b in transitions:
            rounds.append({"entries": [
                {"label": a, "type": "control", "actor": "you"},
                {"label": b, "type": "control", "actor": "you"},
            ]})
    return [{"rounds": rounds}]


def test_build_label_vocab_normalized_sorted() -> None:
    assert VOCAB == ["controle lateral", "costas", "montada"]


def test_upsert_and_similar_athletes() -> None:
    store = AthleteVectorStore(VOCAB, location=":memory:")
    # Two back-takers and one side-control player.
    ingest_athlete(store, "backA", _sessions([("Montada", "Costas")], reps=3))
    ingest_athlete(store, "backB", _sessions([("Montada", "Costas")], reps=3))
    ingest_athlete(store, "sideC", _sessions([("Montada", "Controle Lateral")], reps=3))

    g_back = build_athlete_graph("backA", _sessions([("Montada", "Costas")], reps=3))
    from analysis.graph_embed import graph_vector

    sims = store.similar_athletes(graph_vector(g_back, VOCAB), k=2, exclude="backA")
    assert sims[0][0] == "backB"  # the other back-taker is most similar


def test_position_distribution() -> None:
    store = AthleteVectorStore(VOCAB, location=":memory:")
    ingest_athlete(store, "backB", _sessions([("Montada", "Costas")], reps=3))

    # backB's distribution from mount is all Costas.
    dist = store.position_distribution("backB", "montada")
    assert dist.get("costas", 0.0) == pytest.approx(1.0)
    # Unknown (athlete, position) → empty.
    assert store.position_distribution("backB", "guarda fechada") == {}
    assert store.position_distribution("ghost", "montada") == {}


def test_blend_pulls_sparse_athlete_toward_similar() -> None:
    store = AthleteVectorStore(VOCAB, location=":memory:")
    # A population that overwhelmingly takes the back from mount.
    for name in ("backA", "backB", "backC"):
        ingest_athlete(store, name, _sessions([("Montada", "Costas")], reps=3))

    # New athlete with one mount→side-control observation only.
    g_new = build_athlete_graph("rookie", _sessions([("Montada", "Controle Lateral")], reps=1))
    label_map = {n["name"].lower(): n["name"] for n in NODES}
    own = next_move_prior(g_new, "Montada")
    blended = next_move_prior(
        g_new, "Montada", store=store, athlete="rookie", self_weight=0.5, label_map=label_map
    )

    assert own == {"Controle Lateral": 1.0}
    # Blend introduces Costas from similar athletes while keeping own signal.
    assert blended.get("Costas", 0.0) > 0.0
    assert sum(blended.values()) == pytest.approx(1.0)
