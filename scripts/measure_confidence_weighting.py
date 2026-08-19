#!/usr/bin/env python
"""Read-only measurement: does confidence-weighting the transition-graph corpus by Rating
V2 RD change the public map? Wave 9, ``docs/rating_v2/07_PONDERACAO_POR_CONFIANCA.md``.

Builds the same aggregate transition graph three ways over the ``submission_grappling``
corpus (``analysis.transitions.network_from_sequences`` + ``weight_fn``, see that
module's docstring): ``uniform`` (today's behaviour, the control), ``precision``
(``w=1/RD²``), ``bounded`` (``w=1/(1+(RD/200)²)``) — schemes from
``analysis.confidence_weight``. RD comes from ``athlete_rating_states_v2`` for one fixed
``run_id`` (ADR-02). For each scheme vs. the uniform baseline, reports:

  - Spearman rank correlation (pagerank, weighted_pagerank, betweenness, frequency,
    reward_risk) over the node set;
  - top-20-by-pagerank movement (entered/exited/rank delta);
  - concentration: top-1 / top-5 athlete share of total weighted corpus volume;
  - community change: how many nodes leave their best-matching baseline constellation
    (``analysis.constellations.detect`` + ``.compare.compare_partitions``).

Never writes to the DB. Never calls ``export.site_data``. Writes JSON to
``reports/rating_v2/confidence_weighting.json`` (gitignored).

    uv run python -m scripts.measure_confidence_weighting [--run-id UUID]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import networkx as nx
from scipy.stats import spearmanr

from analysis.confidence_weight import SCHEMES, athlete_weights
from analysis.constellations.compare import compare_partitions, jaccard
from analysis.constellations.detect import detect
from analysis.network_metrics import node_centralities
from analysis.rating_v2.replay import _discipline_of, load_discipline_map
from analysis.transitions import network_from_sequences

DEFAULT_RUN_ID = "210a5ba7-7f88-4b54-b5a5-1dbadfdab4b2"
OUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "rating_v2" / "confidence_weighting.json"
TOP_N = 20


# ── DB read (only impure part) ───────────────────────────────────────────────
def load_corpus(session: Any, run_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from db.models import Athlete, AthleteRatingStateV2, Match

    events_map, null_discipline = load_discipline_map()
    rows = session.execute(
        select(Match.athlete_a_id, Match.athlete_b_id, Match.event, Match.status, Match.sequence)
    ).all()
    sequences: list[list[dict[str, Any]]] = []
    athlete_ids: set[str] = set()
    for athlete_a_id, athlete_b_id, event, status, sequence in rows:
        if status != "final" or not sequence:
            continue
        if _discipline_of(event, events_map, null_discipline) != "submission_grappling":
            continue
        sequences.append(sequence)
        athlete_ids.add(athlete_a_id)
        athlete_ids.add(athlete_b_id)

    rd_rows = session.execute(
        select(AthleteRatingStateV2.athlete_id, AthleteRatingStateV2.rating_deviation).where(
            AthleteRatingStateV2.run_id == run_id
        )
    ).all()
    rd_by_athlete = {aid: float(rd) for aid, rd in rd_rows}

    names = dict(session.execute(select(Athlete.id, Athlete.name)).all())
    return {
        "sequences": sequences,
        "athlete_ids": athlete_ids,
        "rd_by_athlete": rd_by_athlete,
        "names": names,
    }


# ── pure measurement (unit-testable off a small fixture) ────────────────────
def weight_lookup(weights: dict[str, float]) -> Callable[[Any], float]:
    """A weight function over ``weights``, neutral for anyone it does not know.

    Passing ``weights.get`` directly is what this replaces, and it was not merely a typing
    nuisance: ``athlete_weights`` returns one entry per id in ``athlete_ids``, while the corpus
    contains events whose actor is somebody OUTSIDE that set — an off-roster opponent. For those,
    ``.get`` returns ``None``, which then reaches ``build_graph``'s ``wts`` list and the
    contribution sum as a value neither can do arithmetic with.

    1.0 is the neutral default because it is exactly what the ``uniform`` control uses, so an
    athlete with no weight of their own counts once, the same as they would in the baseline.
    """
    def weight(actor: Any) -> float:
        return weights.get(actor, 1.0)
    return weight


def athlete_contribution(
    sequences: list[list[dict[str, Any]]], weight_fn: Callable[[Any], float]
) -> dict[str, float]:
    """Per-athlete sum of weighted event appearances — the corpus-volume basis for the
    concentration metric. Same attribution rule as ``network_from_sequences``: an event
    counts toward the actor who owns it."""
    out: dict[str, float] = {}
    for seq in sequences:
        for e in seq or []:
            actor = e.get("actor_id")
            if actor is None:
                continue
            out[actor] = out.get(actor, 0.0) + weight_fn(actor)
    return out


def concentration(contribution: dict[str, float]) -> dict[str, float]:
    total = sum(contribution.values())
    if total <= 0:
        return {"top1_share": 0.0, "top5_share": 0.0}
    ranked = sorted(contribution.values(), reverse=True)
    return {
        "top1_share": round(ranked[0] / total, 4),
        "top5_share": round(sum(ranked[:5]) / total, 4),
    }


def _spearman(base: dict[str, float], other: dict[str, float]) -> float:
    nodes = sorted(base)  # deterministic order; node sets are identical across schemes
    if len(nodes) < 2:
        return 1.0
    rho, _p = spearmanr([base[n] for n in nodes], [other[n] for n in nodes])
    return round(float(rho), 4) if rho == rho else 0.0  # NaN guard (constant input)


def _top_n_movement(base: dict[str, float], other: dict[str, float], n: int = TOP_N) -> dict[str, Any]:
    base_rank = {node: i for i, node in enumerate(
        sorted(base, key=lambda k: (-base[k], k))
    )}
    other_rank = {node: i for i, node in enumerate(
        sorted(other, key=lambda k: (-other[k], k))
    )}
    base_top = set(sorted(base_rank, key=lambda n: base_rank[n])[:n])
    other_top = set(sorted(other_rank, key=lambda n: other_rank[n])[:n])
    common = base_top & other_top
    deltas = [abs(base_rank[node] - other_rank[node]) for node in common]
    return {
        "entered": sorted(other_top - base_top),
        "exited": sorted(base_top - other_top),
        "mean_abs_rank_delta_of_stayers": round(sum(deltas) / len(deltas), 2) if deltas else 0.0,
        "max_abs_rank_delta_of_stayers": max(deltas) if deltas else 0,
    }


def community_change(g_base: nx.DiGraph, g_other: nx.DiGraph) -> dict[str, Any]:
    """Nodes that leave their best-matching baseline constellation under ``g_other``."""
    base = detect(g_base)
    other = detect(g_other)
    base_members = [c.members for c in base.constellations]
    other_members = [c.members for c in other.constellations]
    comparison = compare_partitions(other_members, base_members)

    node_to_base = {n: frozenset(c) for c in base_members for n in c}
    moved = 0
    total_nodes = 0
    for comm in other_members:
        best_base = max(base_members, key=lambda c: jaccard(set(comm), set(c)), default=[])
        best_base_set = frozenset(best_base)
        for node in comm:
            total_nodes += 1
            if node_to_base.get(node) != best_base_set:
                moved += 1
    return {
        "mean_best_match_jaccard": comparison.mean_jaccard,
        "nodes_changed_region": moved,
        "nodes_total": total_nodes,
        "nodes_changed_region_pct": round(moved / total_nodes * 100, 2) if total_nodes else 0.0,
    }


def compare_scheme(
    g_base: nx.DiGraph, g_other: nx.DiGraph, contrib_other: dict[str, float],
) -> dict[str, Any]:
    cent_base = node_centralities(g_base)
    cent_other = node_centralities(g_other)
    pr_base = {n: c["pagerank"] for n, c in cent_base.items()}
    pr_other = {n: c["pagerank"] for n, c in cent_other.items()}
    wpr_base = {n: c["weighted_pagerank"] for n, c in cent_base.items()}
    wpr_other = {n: c["weighted_pagerank"] for n, c in cent_other.items()}
    btw_base = {n: c["betweenness"] for n, c in cent_base.items()}
    btw_other = {n: c["betweenness"] for n, c in cent_other.items()}
    freq_base = {n: d.get("occ", 0.0) for n, d in g_base.nodes(data=True)}
    freq_other = {n: d.get("occ", 0.0) for n, d in g_other.nodes(data=True)}
    rr_base = {n: d.get("reward_risk", 0.0) for n, d in g_base.nodes(data=True)}
    rr_other = {n: d.get("reward_risk", 0.0) for n, d in g_other.nodes(data=True)}

    return {
        "spearman": {
            "pagerank": _spearman(pr_base, pr_other),
            "weighted_pagerank": _spearman(wpr_base, wpr_other),
            "betweenness": _spearman(btw_base, btw_other),
            "frequency": _spearman(freq_base, freq_other),
            "reward_risk": _spearman(rr_base, rr_other),
        },
        "top20_pagerank_movement": _top_n_movement(pr_base, pr_other),
        "concentration": concentration(contrib_other),
        "community_change": community_change(g_base, g_other),
    }


def run_measurement(
    sequences: list[list[dict[str, Any]]],
    athlete_ids: set[str],
    rd_by_athlete: dict[str, float],
    names: dict[str, str],
) -> dict[str, Any]:
    """Pure orchestration over already-loaded corpus data."""
    weights_by_scheme = {
        scheme: athlete_weights(athlete_ids, rd_by_athlete, scheme) for scheme in SCHEMES
    }
    graphs = {
        scheme: (
            network_from_sequences(sequences)
            if scheme == "uniform"
            else network_from_sequences(
                sequences, weight_fn=weight_lookup(weights_by_scheme[scheme])
            )
        )
        for scheme in SCHEMES
    }
    contributions = {
        scheme: athlete_contribution(sequences, weight_lookup(weights_by_scheme[scheme]))
        for scheme in SCHEMES
    }

    g_base = graphs["uniform"]
    contrib_base = contributions["uniform"]
    comparisons = {
        "uniform": {"concentration": concentration(contrib_base)},  # the control itself
        "precision": compare_scheme(g_base, graphs["precision"], contributions["precision"]),
        "bounded": compare_scheme(g_base, graphs["bounded"], contributions["bounded"]),
    }

    top1_contributor = max(contrib_base, key=lambda a: contrib_base[a]) if contrib_base else None
    return {
        "run_id": DEFAULT_RUN_ID,
        "n_athletes": len(athlete_ids),
        "n_athletes_with_rd_state": sum(1 for a in athlete_ids if a in rd_by_athlete),
        "n_sequences": len(sequences),
        "n_nodes": g_base.number_of_nodes(),
        # No contributor at all means an empty corpus; there is no name to look up and none to
        # fall back to either.
        "top1_contributor_name": (
            names.get(top1_contributor, top1_contributor) if top1_contributor else None
        ),
        "comparisons": comparisons,
    }


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    from db.base import db_session

    with db_session() as session:
        corpus = load_corpus(session, args.run_id)

    result = run_measurement(
        corpus["sequences"], corpus["athlete_ids"], corpus["rd_by_athlete"], corpus["names"],
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n")
    print(f"wrote {args.out}")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
