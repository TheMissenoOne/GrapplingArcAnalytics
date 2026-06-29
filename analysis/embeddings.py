"""Semantic position embeddings — the vector-DB layer of the grappling map.

Encodes each canonical technique node's text (label · type · translation) with a 768-dim
sentence-transformer and persists it into ``technique_nodes.embedding`` (pgvector). Powers
semantic "related positions", synonym-merge candidates, and the vector-suggested transitions in
``analysis.grappling_map``.

    uv run python -m analysis.embeddings backfill   # encode every node → pgvector

Model load is lazy + cached, so importing this module is cheap and the heavy download only
happens when you actually embed. Bulk map enrichment uses an in-memory matrix (one pass) while
``nearest_positions`` demonstrates the DB-side pgvector cosine query.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import TechniqueNode

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-mpnet-base-v2"  # 768-dim, matches the column
_MODEL: Any = None


def _model() -> Any:
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer  # heavy import, lazy

        _MODEL = SentenceTransformer(MODEL_NAME)
    return _MODEL


def node_text(label: str, node_type: str = "", pt: str = "") -> str:
    """The text we embed for a position — name, then type + translation for disambiguation."""
    parts = [label.strip()]
    if node_type:
        parts.append(node_type)
    if pt and pt.strip().lower() != label.strip().lower():
        parts.append(pt.strip())
    return " · ".join(p for p in parts if p)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Encode texts → L2-normalised 768-d vectors (cosine == dot)."""
    return np.asarray(
        _model().encode(texts, normalize_embeddings=True, convert_to_numpy=True),
        dtype=np.float64,
    )


def backfill(session: Session, batch: int = 256) -> int:
    """Embed every technique node's text → ``technique_nodes.embedding``. Returns count."""
    nodes = list(session.execute(select(TechniqueNode)).scalars())
    for i in range(0, len(nodes), batch):
        chunk = nodes[i : i + batch]
        vecs = embed_texts([node_text(n.label, n.node_type) for n in chunk])
        for n, v in zip(chunk, vecs):
            n.embedding = v
    session.commit()
    logger.info("Embedded %d technique nodes → pgvector(768)", len(nodes))
    return len(nodes)


def backfill_graph_edge_embeddings(session: Session, batch: int = 256) -> int:
    """Embed each graph edge's ``"{source_label} to {target_label}"`` → ``graph_edges.embedding``.

    Falls back to the raw node_key when a label is missing. Returns the edge count.
    """
    from db.models import GraphEdge

    labels: dict[str, str] = {
        k: lb
        for k, lb in session.execute(
            select(TechniqueNode.node_key, TechniqueNode.label)
        ).all()
    }
    edges = list(session.execute(select(GraphEdge)).scalars())

    def _text(e: Any) -> str:
        s = labels.get(e.source_key, e.source_key)
        t = labels.get(e.target_key, e.target_key)
        return f"{s} to {t}"

    for i in range(0, len(edges), batch):
        chunk = edges[i : i + batch]
        vecs = embed_texts([_text(e) for e in chunk])
        for e, v in zip(chunk, vecs):
            e.embedding = v
    session.commit()
    logger.info("Embedded %d graph edges → pgvector(768)", len(edges))
    return len(edges)


def backfill_graph_embeddings(session: Session) -> int:
    """Set each graph's embedding = ELO-weighted mean of its nodes' technique embeddings.

    Requires ``technique_nodes.embedding`` to be backfilled first. Graphs with no embedded
    nodes are skipped (stay NULL). Returns the number of graphs updated.
    """
    from db.models import Graph, GraphEdge
    from db.repository import incident_edge_elos

    node_emb = {
        k: np.asarray(v, dtype=np.float64)
        for k, v in session.execute(
            select(TechniqueNode.node_key, TechniqueNode.embedding).where(
                TechniqueNode.embedding.isnot(None)
            )
        ).all()
    }
    n_set = 0
    for g in session.execute(select(Graph)).scalars():
        edges = list(session.execute(select(GraphEdge).where(GraphEdge.graph_id == g.id)).scalars())
        vecs: list[np.ndarray] = []
        weights: list[float] = []
        for key, elos in incident_edge_elos(edges).items():
            emb = node_emb.get(key)
            if emb is None:
                continue
            vecs.append(emb)
            weights.append(max(max(elos) if elos else 0.0, 1e-6))
        if not vecs:
            continue
        arr = np.asarray(vecs)
        w = np.asarray(weights)
        mean = (arr * w[:, None]).sum(axis=0) / w.sum()
        norm = np.linalg.norm(mean)
        g.embedding = mean / norm if norm > 0 else mean
        n_set += 1
    session.commit()
    logger.info("Embedded %d graphs → pgvector(768)", n_set)
    return n_set


def backfill_archetype_embeddings(session: Session) -> int:
    """Set each archetype's embedding = normalized mean of its member graphs' embeddings.

    Run after graph embeddings + archetype assignment. Returns archetypes updated.
    """
    from db.models import Archetype, Graph

    groups: dict[int, list[np.ndarray]] = {}
    for aid, emb in session.execute(
        select(Graph.archetype_id, Graph.embedding).where(
            Graph.archetype_id.isnot(None), Graph.embedding.isnot(None)
        )
    ).all():
        groups.setdefault(int(aid), []).append(np.asarray(emb, dtype=np.float64))
    n = 0
    for aid, vs in groups.items():
        arch = session.get(Archetype, aid)
        if arch is None:
            continue
        mean = np.mean(vs, axis=0)
        norm = np.linalg.norm(mean)
        arch.embedding = mean / norm if norm > 0 else mean
        n += 1
    session.commit()
    logger.info("Embedded %d archetype centroids → pgvector(768)", n)
    return n


def nearest_graphs(session: Session, graph_id: str, k: int = 6) -> list[tuple[str, float]]:
    """DB-side: top-``k`` most stylistically similar graphs (cosine over graph embeddings)."""
    from db.models import Graph

    target = session.execute(
        select(Graph.embedding).where(Graph.id == graph_id)
    ).scalar_one_or_none()
    if target is None:
        return []
    dist = Graph.embedding.cosine_distance(target)
    rows = session.execute(
        select(Graph.id, dist)
        .where(Graph.embedding.isnot(None), Graph.id != graph_id)
        .order_by(dist)
        .limit(k)
    )
    return [(gid, round(1.0 - float(d), 3)) for gid, d in rows]


def nearest_positions(session: Session, node_key: str, k: int = 6) -> list[tuple[str, float]]:
    """DB-side: top-``k`` semantically closest nodes to ``node_key`` via pgvector cosine."""
    target = session.execute(
        select(TechniqueNode.embedding).where(TechniqueNode.node_key == node_key)
    ).scalar_one_or_none()
    if target is None:
        return []
    dist = TechniqueNode.embedding.cosine_distance(target)
    rows = session.execute(
        select(TechniqueNode.node_key, dist)
        .where(TechniqueNode.embedding.isnot(None), TechniqueNode.node_key != node_key)
        .order_by(dist)
        .limit(k)
    )
    return [(key, round(1.0 - float(d), 3)) for key, d in rows]


def load_matrix(session: Session) -> tuple[list[str], np.ndarray]:
    """All embedded nodes as ``(keys, matrix)`` — one pass, for bulk in-memory similarity."""
    rows = list(session.execute(
        select(TechniqueNode.node_key, TechniqueNode.embedding)
        .where(TechniqueNode.embedding.isnot(None))
    ))
    keys = [r[0] for r in rows]
    if not rows:
        return keys, np.empty((0, 768))
    mat = np.asarray([np.asarray(r[1], dtype=np.float64) for r in rows])
    return keys, mat


def semantic_neighbours_fn(session: Session) -> Callable[[str, int], list[tuple[str, float]]]:
    """Build an in-memory neighbour function (keys, matrix loaded once) for map enrichment."""
    keys, mat = load_matrix(session)
    index = {k: i for i, k in enumerate(keys)}

    def fn(node_key: str, k: int) -> list[tuple[str, float]]:
        i = index.get(node_key)
        if i is None or mat.shape[0] == 0:
            return []
        sims = mat @ mat[i]  # unit vectors → cosine
        order = np.argsort(-sims)
        out: list[tuple[str, float]] = []
        for j in order:
            if j == i:
                continue
            out.append((keys[j], round(float(sims[j]), 3)))
            if len(out) >= k:
                break
        return out

    return fn


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Position embedding tooling")
    ap.add_argument(
        "cmd",
        choices=["backfill", "graphs", "all"],
        help="backfill = nodes; graphs = edges+graphs+archetypes; all = nodes then graphs",
    )
    cmd = ap.parse_args().cmd
    from db.base import db_session

    with db_session() as session:
        if cmd in ("backfill", "all"):
            backfill(session)
        if cmd in ("graphs", "all"):
            backfill_graph_edge_embeddings(session)
            backfill_graph_embeddings(session)
            backfill_archetype_embeddings(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
