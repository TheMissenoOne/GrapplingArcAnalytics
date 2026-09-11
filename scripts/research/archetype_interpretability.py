"""Baseline interpretability report for the v4 archetype population — read-only.

Exports the CURRENT emergent-archetype state (``run_archetype_pipeline`` output already in
the DB) without changing any assignment: cluster sizes/names, bootstrap stability of the
persisted partition, nearest-athlete exemplars per cluster, and refusal coverage (graphs
too small / never assigned). Nothing here writes to the DB or re-persists archetypes.

    uv run python -m scripts.research.archetype_interpretability \
        --out docs/research/archetype_baseline_v4.json

Public/private: population is ``graphs_for_clustering(session, owner_kind="athlete")`` only
— the same guard ``run_archetype_pipeline`` uses (root CLAUDE.md: every query building a
competitive artefact filters ``owner_kind`` explicitly). Exemplar names come from the
``athletes`` table (public data); an anonymized athlete's name is already NULL at rest
(alembic 0048), so it is silently skipped rather than shown as an id.

Determinism (failure-archaeology #10 — the 2026-07-07 PYTHONHASHSEED reshuffle): every
ranking here carries an explicit sort key (cluster list, exemplar list, refusal id lists,
churn-by-graph dict) — never bare dict/set iteration order.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score

from analysis.archetype import (
    DEVIANCE_WEIGHT,
    FEATURE_VERSION,
    MIN_GRAPH_NODES,
    graph_feature_vector,
)
from analysis.deviance import TYPES, Stats, grappling_nodes, node_population_stats

logger = logging.getLogger(__name__)


@dataclass
class ClusterPopulation:
    """Plain-data snapshot of the archetype population — no DB/session inside, so
    ``build_report_from_population`` is a pure function testable without one."""

    rows: list[tuple[str, list[Any]]]  # eligible (graph_id, [DerivedNode-like, ...])
    excluded: list[tuple[str, str]]  # (graph_id, reason) — dropped for insufficient nodes
    persisted: dict[str, int | None]  # graph_id -> currently-persisted archetype_id
    archetypes: dict[int, dict[str, Any]]  # archetype_id -> {name, key, signature_types, centroid_vec}
    athlete_names: dict[str, tuple[str | None, str | None]]  # graph_id -> (athlete_id, name)
    by_key: Stats = field(default_factory=dict)
    by_type: Stats = field(default_factory=dict)


def _resample_ari(
    vectors: np.ndarray, persisted_labels: np.ndarray, idx: np.ndarray, k: int, seed: int
) -> tuple[float, np.ndarray]:
    """Refit KMeans(k) on ``vectors[idx]``; ARI of the refit vs. ``persisted_labels[idx]``."""
    kk = max(1, min(k, len(idx)))
    km = KMeans(n_clusters=kk, random_state=seed, n_init="auto")
    boot_labels = km.fit_predict(vectors[idx])
    ari = float(adjusted_rand_score(persisted_labels[idx], boot_labels))
    return ari, boot_labels


def _map_boot_to_persisted(persisted_sample: np.ndarray, boot_labels: np.ndarray) -> dict[int, int]:
    """Majority-vote label mapping (same pattern as ``archetype.jaccard_bootstrap_stability``):
    each bootstrap cluster id maps to the persisted archetype id it overlaps most, tie-broken
    by the lowest persisted id so the mapping is deterministic."""
    mapping: dict[int, int] = {}
    for b in sorted(set(boot_labels.tolist())):
        vals, counts = np.unique(persisted_sample[boot_labels == b], return_counts=True)
        ranked = sorted(zip(vals.tolist(), counts.tolist()), key=lambda vc: (-vc[1], vc[0]))
        mapping[b] = ranked[0][0]
    return mapping


def _bootstrap_stability(
    vectors: np.ndarray,
    persisted_labels: np.ndarray,
    graph_ids: list[str],
    *,
    k: int,
    n_bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    """Resample the eligible+assigned population with replacement ``n_bootstrap`` times,
    refit KMeans(k) each time, and compare against the PERSISTED partition (not a fresh
    full-sample fit) — ARI (mean + 95% percentile CI) and per-graph churn rate."""
    n = len(graph_ids)
    if n == 0:
        return {"n_graphs": 0, "mean_ari": None, "ci_low": None, "ci_high": None,
                "mean_churn": None, "churn_by_graph": {}}

    rng = np.random.RandomState(seed)
    aris: list[float] = []
    churn_hits = {gid: 0 for gid in graph_ids}
    churn_seen = {gid: 0 for gid in graph_ids}

    for i in range(n_bootstrap):
        idx = rng.choice(n, size=n, replace=True)
        ari, boot_labels = _resample_ari(vectors, persisted_labels, idx, k, seed + i + 1)
        aris.append(ari)
        mapping = _map_boot_to_persisted(persisted_labels[idx], boot_labels)
        for pos, gi in enumerate(idx):
            gid = graph_ids[gi]
            churn_seen[gid] += 1
            if mapping[boot_labels[pos]] != persisted_labels[gi]:
                churn_hits[gid] += 1

    churn_by_graph = {
        gid: round(churn_hits[gid] / churn_seen[gid], 4) for gid in graph_ids if churn_seen[gid] > 0
    }
    mean_churn = round(sum(churn_by_graph.values()) / len(churn_by_graph), 4) if churn_by_graph else None
    aris_arr = np.array(aris)
    return {
        "n_graphs": n,
        "mean_ari": round(float(np.mean(aris_arr)), 4),
        "ci_low": round(float(np.percentile(aris_arr, 2.5)), 4),
        "ci_high": round(float(np.percentile(aris_arr, 97.5)), 4),
        "mean_churn": mean_churn,
        "churn_by_graph": dict(sorted(churn_by_graph.items())),
    }


def build_report_from_population(
    pop: ClusterPopulation, *, k: int = 6, n_bootstrap: int = 100, seed: int = 42
) -> dict[str, object]:
    """Pure report builder — no DB. See module docstring for the exported shape."""
    graph_ids = [gid for gid, _ in pop.rows]
    nodes_by_id = dict(pop.rows)
    vectors = np.array(
        [graph_feature_vector(nodes_by_id[gid], pop.by_key, pop.by_type) for gid in graph_ids]
    )

    size_by_arch: dict[int, int] = {}
    for gid in graph_ids:
        aid = pop.persisted.get(gid)
        if aid is not None:
            size_by_arch[aid] = size_by_arch.get(aid, 0) + 1
    clusters = [
        {
            "archetype_id": aid,
            "name": (pop.archetypes.get(aid) or {}).get("name") or f"archetype-{aid}",
            "key": (pop.archetypes.get(aid) or {}).get("key"),
            "signature_types": (pop.archetypes.get(aid) or {}).get("signature_types") or [],
            "size": size,
        }
        for aid, size in size_by_arch.items()
    ]
    clusters.sort(key=lambda c: (-c["size"], str(c["name"]), c["archetype_id"]))

    stable_idx = [i for i, gid in enumerate(graph_ids) if pop.persisted.get(gid) is not None]
    if stable_idx:
        stability = _bootstrap_stability(
            vectors,
            np.array([pop.persisted[graph_ids[i]] for i in stable_idx]),
            [graph_ids[i] for i in stable_idx],
            k=k,
            n_bootstrap=n_bootstrap,
            seed=seed,
        )
    else:
        stability = {"n_graphs": 0, "mean_ari": None, "ci_low": None, "ci_high": None,
                     "mean_churn": None, "churn_by_graph": {}}

    exemplars: dict[str, list[dict[str, Any]]] = {}
    for aid, meta in pop.archetypes.items():
        centroid = meta.get("centroid_vec")
        if centroid is None:
            continue
        named = []
        for i, gid in enumerate(graph_ids):
            if pop.persisted.get(gid) != aid:
                continue
            _athlete_id, name = pop.athlete_names.get(gid, (None, None))
            if not name:
                continue  # anonymized / unnamed — never shown by name (LGPD rights-request)
            dist = float(np.linalg.norm(vectors[i] - centroid))
            named.append({"name": name, "athlete_id": _athlete_id, "distance": round(dist, 4),
                          "graph_id": gid})
        named.sort(key=lambda e: (e["distance"], str(e["name"]), e["graph_id"]))
        exemplars[str(aid)] = named[:3]

    refusal = {
        "insufficient_evidence": {
            "reason": f"fewer than MIN_GRAPH_NODES={MIN_GRAPH_NODES} grappling nodes",
            "count": len(pop.excluded),
            "graph_ids": sorted(gid for gid, _ in pop.excluded),
        },
        "no_assignment": {
            "reason": "eligible graph carries no persisted archetype_id "
                      "(never clustered, or cleared by a recompute since)",
            "count": len(graph_ids) - len(stable_idx),
            "graph_ids": sorted(gid for gid in graph_ids if pop.persisted.get(gid) is None),
        },
    }

    feature_dims = (
        [f"composition_share:{t}" for t in TYPES]
        + [f"deviance:{t}(x{DEVIANCE_WEIGHT})" for t in TYPES]
        + ["edge_density", "offense_ratio"]
    )

    return {
        "feature_version_prefix": FEATURE_VERSION,
        "k": k,
        "n_bootstrap": n_bootstrap,
        "n_graphs_total": len(pop.rows) + len(pop.excluded),
        "n_graphs_eligible": len(pop.rows),
        "clusters": clusters,
        "stability": stability,
        "exemplars": exemplars,
        "refusal": refusal,
        "feature_dims": feature_dims,
    }


def _fetch_population(session: Any) -> ClusterPopulation:
    """DB-reading thin wrapper — read-only SELECTs, mirrors ``run_archetype_pipeline``'s
    population/baseline selection exactly, but never persists anything."""
    from sqlalchemy import select

    from analysis.rating_v2.config import SITE_RATING_RUN_ID
    from db.models import Archetype, Athlete, Graph
    from db.repository import graphs_for_clustering, rated_athlete_graph_ids

    all_rows = graphs_for_clustering(session, owner_kind="athlete")
    rows: list[tuple[str, list[Any]]] = []
    excluded: list[tuple[str, str]] = []
    for gid, raw_nodes in all_rows:
        nodes = grappling_nodes(raw_nodes)
        if len(nodes) >= MIN_GRAPH_NODES:
            rows.append((gid, nodes))
        else:
            excluded.append((gid, f"{len(nodes)} grappling node(s)"))

    rated_ids = rated_athlete_graph_ids(session, SITE_RATING_RUN_ID)
    baseline_rows = [r for r in rows if r[0] in rated_ids] or rows
    by_key, by_type = node_population_stats(baseline_rows)

    graph_ids = {gid for gid, _ in rows}
    persisted: dict[str, int | None] = {}
    owner_by_graph: dict[str, str] = {}
    for gid, archetype_id, owner_id in session.execute(
        select(Graph.id, Graph.archetype_id, Graph.owner_id).where(Graph.id.in_(graph_ids))
    ).all():
        persisted[gid] = archetype_id
        owner_by_graph[gid] = owner_id

    archetypes: dict[int, dict[str, Any]] = {}
    for a in session.execute(select(Archetype)).scalars():
        centroid = (a.centroid or {}).get("vector") if a.centroid else None
        archetypes[a.id] = {
            "name": a.name,
            "key": a.key,
            "signature_types": list(a.signature_types or []),
            "centroid_vec": np.asarray(centroid, dtype=np.float64) if centroid else None,
        }

    athlete_ids = set(owner_by_graph.values())
    name_by_athlete: dict[str, str | None] = {}
    if athlete_ids:
        for aid, name, anon in session.execute(
            select(Athlete.id, Athlete.name, Athlete.anonymized_at).where(Athlete.id.in_(athlete_ids))
        ).all():
            name_by_athlete[aid] = None if anon is not None else name

    athlete_names = {
        gid: (owner_id, name_by_athlete.get(owner_id)) for gid, owner_id in owner_by_graph.items()
    }

    return ClusterPopulation(
        rows=rows, excluded=excluded, persisted=persisted, archetypes=archetypes,
        athlete_names=athlete_names, by_key=by_key, by_type=by_type,
    )


def build_baseline_report(
    session: Any, *, k: int = 6, n_bootstrap: int = 100, seed: int = 42
) -> dict[str, object]:
    """Read-only entry point — SELECTs the current archetype population, never writes."""
    return build_report_from_population(_fetch_population(session), k=k, n_bootstrap=n_bootstrap, seed=seed)


def _markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Archetype v4 baseline interpretability report",
        "",
        f"k={report['k']}, n_bootstrap={report['n_bootstrap']}, "
        f"graphs eligible={report['n_graphs_eligible']}/{report['n_graphs_total']}",
        "",
        "## Clusters",
        "",
        "| archetype_id | name | size | signature |",
        "|---|---|---|---|",
    ]
    for c in report["clusters"]:
        lines.append(f"| {c['archetype_id']} | {c['name']} | {c['size']} | "
                      f"{', '.join(c['signature_types'])} |")
    stab = report["stability"]
    lines += [
        "",
        "## Bootstrap stability (vs. persisted assignment)",
        "",
        f"mean ARI = {stab['mean_ari']} (95% CI [{stab['ci_low']}, {stab['ci_high']}]), "
        f"mean per-graph churn = {stab['mean_churn']}",
        "",
        "## Refusal coverage",
        "",
        f"insufficient evidence: {report['refusal']['insufficient_evidence']['count']} graphs",
        f"no assignment: {report['refusal']['no_assignment']['count']} graphs",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/research/archetype_baseline_v4.json")
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--n-bootstrap", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    from db.base import db_session

    with db_session() as session:
        report = build_baseline_report(session, k=args.k, n_bootstrap=args.n_bootstrap, seed=args.seed)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    out_path.with_suffix(".md").write_text(_markdown_summary(report))
    logger.info("wrote %s (+ .md summary)", out_path)


if __name__ == "__main__":
    main()
