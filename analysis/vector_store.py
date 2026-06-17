"""Qdrant-backed store of per-athlete graph vectors, for similarity priors.

Runs Qdrant **in-process** (embedded: ``:memory:`` for tests, a local path for
persistence) — no server required. Two collections over a shared normalized-label
vocabulary:

- ``athlete_graphs``    — one whole-graph fingerprint per athlete (style similarity).
- ``athlete_positions`` — one ``node_vector`` per (athlete, position): the athlete's
  next-move distribution *from* that position. Filtered queries here drive the
  similar-athlete blend in :func:`analysis.priors.next_move_prior`.

The store is optional: the backend degrades gracefully (no priors blend) when it
can't be constructed. See ``docs/realtime_cv_design.md`` §9.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import numpy as np

from analysis.athlete_graph import AthleteGraph, build_athlete_graph
from analysis.graph_embed import graph_vector, node_vector
from analysis.names import _normalize_name

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

GRAPHS = "athlete_graphs"
POSITIONS = "athlete_positions"
_NS = uuid.UUID("6f9b5d2e-1c3a-4e7b-9a0d-1f2e3c4b5a60")


def build_label_vocab(nodes: list[dict[str, Any]]) -> list[str]:
    """Ordered, de-duplicated vocabulary of normalized app-node labels."""
    labels = {_normalize_name(str(n.get("name", ""))) for n in nodes}
    labels.discard("")
    return sorted(labels)


def _athlete_id(athlete: str) -> str:
    return str(uuid.uuid5(_NS, athlete))


def _position_id(athlete: str, position: str) -> str:
    return str(uuid.uuid5(_NS, f"{athlete}|{position}"))


class AthleteVectorStore:
    """Embedded-Qdrant store of athlete graph + per-position vectors."""

    def __init__(
        self,
        vocab: list[str],
        location: str = ":memory:",
        path: str | None = None,
        client: QdrantClient | None = None,
    ) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.vocab = vocab
        self.client: QdrantClient
        if client is not None:
            self.client = client
        elif path is not None:
            self.client = QdrantClient(path=path)
        else:
            self.client = QdrantClient(location=location)

        for name in (GRAPHS, POSITIONS):
            if not self.client.collection_exists(name):
                self.client.create_collection(
                    name,
                    vectors_config=VectorParams(size=len(vocab), distance=Distance.COSINE),
                )

    def upsert_athlete(self, graph: AthleteGraph) -> None:
        """Embed and upsert an athlete's whole-graph + per-position vectors.

        All-zero vectors (empty graph / leaf positions) are skipped — cosine
        similarity is undefined for them.
        """
        from qdrant_client.models import PointStruct

        gv = graph_vector(graph, self.vocab)
        if gv.any():
            self.client.upsert(
                GRAPHS,
                points=[
                    PointStruct(
                        id=_athlete_id(graph.athlete),
                        vector=gv.tolist(),
                        payload={"athlete": graph.athlete},
                    )
                ],
            )

        points = []
        for source in {src for src, _ in graph.edges}:
            nv = node_vector(graph, source, self.vocab)
            if not nv.any():
                continue
            points.append(
                PointStruct(
                    id=_position_id(graph.athlete, source),
                    vector=nv.tolist(),
                    payload={"athlete": graph.athlete, "position": source},
                )
            )
        if points:
            self.client.upsert(POSITIONS, points=points)

    def similar_athletes(
        self, query_vec: np.ndarray, k: int = 5, exclude: str | None = None
    ) -> list[tuple[str, float]]:
        """Athletes whose whole-graph fingerprint is closest to ``query_vec``."""
        hits = self.client.query_points(
            GRAPHS, query=query_vec.tolist(), limit=k + (1 if exclude else 0)
        ).points
        out = [
            (str(h.payload["athlete"]), float(h.score))
            for h in hits
            if h.payload and h.payload.get("athlete") != exclude
        ]
        return out[:k]

    def position_distribution(self, athlete: str, position: str) -> dict[str, float]:
        """One athlete's next-move distribution *from* ``position`` (or ``{}``).

        Decodes the stored ``node_vector`` for (athlete, position) back into a
        ``{normalized_label: weight}`` distribution over the vocab.
        """
        recs = self.client.retrieve(
            POSITIONS, ids=[_position_id(athlete, position)], with_vectors=True
        )
        if not recs:
            return {}
        vector = recs[0].vector
        if not isinstance(vector, list):
            return {}
        arr = np.asarray(vector, dtype=float)
        return {self.vocab[i]: float(v) for i, v in enumerate(arr) if v > 0}


def ingest_athlete(
    store: AthleteVectorStore, athlete: str, sessions: list[dict[str, Any]]
) -> AthleteGraph:
    """Build an athlete's graph from sessions and upsert it. Returns the graph."""
    graph = build_athlete_graph(athlete, sessions)
    store.upsert_athlete(graph)
    return graph
