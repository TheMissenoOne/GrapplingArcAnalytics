"""Turn AthleteGraph into fixed-length vectors over a shared vocabulary."""

from __future__ import annotations

import logging

import numpy as np

from analysis.athlete_graph import AthleteGraph, out_distribution

logger = logging.getLogger(__name__)


def graph_vector(graph: AthleteGraph, vocab: list[str]) -> np.ndarray:
    """Whole-graph fingerprint: normalised node-usage counts over *vocab*.

    Parameters
    ----------
    graph : AthleteGraph
    vocab : list[str]
        Ordered vocabulary of normalised label strings.

    Returns
    -------
    np.ndarray
        Shape ``(len(vocab),)``, L1-normalised (sums to 1).  All-zeros if
        *graph* has no nodes.
    """
    vec = np.zeros(len(vocab), dtype=np.float64)
    total = 0
    for i, v in enumerate(vocab):
        node = graph.nodes.get(v)
        if node is not None:
            vec[i] = node.count
            total += node.count
    if total > 0:
        vec = vec / total
    return vec


def node_vector(graph: AthleteGraph, label: str, vocab: list[str]) -> np.ndarray:
    """Per-position vector: out-distribution of *label* projected onto *vocab*.

    Parameters
    ----------
    graph : AthleteGraph
    label : str
        Normalised source label.
    vocab : list[str]
        Ordered vocabulary.

    Returns
    -------
    np.ndarray
        Shape ``(len(vocab),)``.  All-zeros if *label* is absent from *graph*.
    """
    dist = out_distribution(graph, label)
    vec = np.zeros(len(vocab), dtype=np.float64)
    for i, v in enumerate(vocab):
        vec[i] = dist.get(v, 0.0)
    return vec


def stack_vectors(graphs: list[AthleteGraph], vocab: list[str]) -> np.ndarray:
    """Stack multiple graph vectors into a matrix.

    Parameters
    ----------
    graphs : list[AthleteGraph]
    vocab : list[str]

    Returns
    -------
    np.ndarray
        Shape ``(len(graphs), len(vocab))`` — including ``(0, len(vocab))`` for an
        empty ``graphs`` list, so downstream 2-D consumers don't break on cold start.
    """
    if not graphs:
        return np.zeros((0, len(vocab)), dtype=np.float64)
    return np.array([graph_vector(g, vocab) for g in graphs], dtype=np.float64)
