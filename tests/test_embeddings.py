"""Tests for the map vector layer — structural (pure) + semantic (mocked, no model download)."""

from __future__ import annotations

from typing import Any

import numpy as np

from analysis import embeddings
from analysis.embeddings import embed_texts, node_text
from analysis.names import _normalize_name
from analysis.network_metrics import network_from_sequences
from analysis.vector_store import structural_neighbours_fn


def _e(label: str, typ: str, actor: str = "A", ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


def test_node_text_includes_type_and_translation() -> None:
    assert node_text("Armbar", "submission", "Chave de braço").split(" · ") == [
        "Armbar", "submission", "Chave de braço"]
    # translation equal to label (case-insensitive) is dropped
    assert node_text("Mount", "control", "mount") == "Mount · control"


def test_structural_neighbours_match_similar_out_distribution() -> None:
    # Mount and Side Control both flow only into Armbar → identical out-distribution → similar.
    seqs = [
        [_e("Mount", "control"), _e("Armbar", "submission", ok=True)],
        [_e("Side Control", "control"), _e("Armbar", "submission", ok=True)],
    ]
    fn = structural_neighbours_fn(network_from_sequences(seqs))
    neighbours = dict(fn(_normalize_name("Mount"), 3))
    assert _normalize_name("Side Control") in neighbours
    assert neighbours[_normalize_name("Side Control")] > 0.9


def test_embed_texts_shape_and_norm(monkeypatch) -> None:
    class _Fake:
        def encode(self, texts: list[str], **_: Any) -> np.ndarray:
            return np.eye(len(texts), 768)  # unit rows

    monkeypatch.setattr(embeddings, "_model", lambda: _Fake())
    vecs = embed_texts(["closed guard", "back control"])
    assert vecs.shape == (2, 768)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)


def test_semantic_neighbours_fn_ranks_by_cosine(monkeypatch) -> None:
    keys = ["a", "b", "c"]
    mat = np.array([[1.0, 0.0, 0.0], [0.95, 0.05, 0.0], [0.0, 0.0, 1.0]])
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    monkeypatch.setattr(embeddings, "load_matrix", lambda _s: (keys, mat))
    fn = embeddings.semantic_neighbours_fn(session=None)
    nearest = fn("a", 2)
    assert nearest[0][0] == "b"  # closest direction to "a"
    assert nearest[0][1] > nearest[1][1]
