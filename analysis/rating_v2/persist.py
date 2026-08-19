"""DB-write side of the rating_v2 replay (wave 7) — the only impure write path here.

Kept separate from ``replay.py`` on purpose: ``replay.py`` stays a pure DB-READ
orchestrator producing a JSON artefact, unit-testable without a DB; this module owns the
write transaction, so it can be exercised against an in-memory SQLite session
(``tests/test_rating_v2.py``) without ever touching Postgres.

ADR-02 (``docs/rating_v2/01_DECISOES.md``): ``run_id`` is a required read key. This module
deliberately exposes no "current state" read helper — only the write path. That includes
``persist_node_states``/``persist_constellations`` below: both take ``run_id`` explicitly,
same as ``persist_replay_result``.

Node states and constellations write to alembic-0036 tables (``athlete_node_rating_
states_v2``, ``athlete_constellations_v2``, ``athlete_constellation_members_v2``) — shadow
tables, no consumer yet (wave 8). ``persist_replay_result`` writes them in the same
transaction as the run + global states when the caller supplies them, so one run never
ends up with global state committed but node/constellation state half-written.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import networkx as nx
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from analysis.constellations.detect import DetectionResult
from analysis.constellations.stability import ConstellationStability
from analysis.network_metrics import weighted_pagerank
from analysis.rating_v2.config import EngineConfig
from db.models import (
    AthleteConstellationMemberV2,
    AthleteConstellationV2,
    AthleteNodeRatingStateV2,
    AthleteRatingStateV2,
    RatingEngineRun,
)


def persist_node_states(
    session: Session, run_id: str, node_states: list[dict[str, Any]]
) -> None:
    """Bulk-write one row per (``run_id``, athlete, node_key) into
    ``athlete_node_rating_states_v2``. Each item: ``{athlete_id, node_key, rating,
    deviation, volatility, bouts_observed?, occurrences?, first_seen_at?, last_seen_at?,
    constellation_fingerprint?}`` — same key names ``persist_replay_result`` already uses
    for global state (``deviation``, not ``rating_deviation``), so a caller building both
    from the same replay output doesn't juggle two conventions.
    """
    rows = [
        AthleteNodeRatingStateV2(
            run_id=run_id,
            athlete_id=ns["athlete_id"],
            node_key=ns["node_key"],
            rating=ns["rating"],
            rating_deviation=ns["deviation"],
            volatility=ns["volatility"],
            bouts_observed=ns.get("bouts_observed", 0),
            occurrences=ns.get("occurrences", 0),
            first_seen_at=ns.get("first_seen_at"),
            last_seen_at=ns.get("last_seen_at"),
            constellation_fingerprint=ns.get("constellation_fingerprint"),
        )
        for ns in node_states
    ]
    session.bulk_save_objects(rows)


def persist_constellations(
    session: Session, run_id: str, constellations: list[dict[str, Any]]
) -> None:
    """Bulk-write constellation summaries + their member rows for one run, into
    ``athlete_constellations_v2`` and ``athlete_constellation_members_v2``.

    Each item: ``{athlete_id, fingerprint, hub_node_key, member_count,
    internal_edge_count, modularity, stability_mean, stability_p10, support_bouts?,
    summary_json?, members: [{node_key, pagerank, weighted_pagerank, degree}, ...]}``.

    This is the "expected shape" the writer is built against (docs/rating_v2 wave-7
    task). See ``build_constellation_rows_from_detection`` below for the adapter that
    turns ``analysis/constellations/``'s real detect+stability output into this shape.
    """
    constellation_rows = []
    member_rows = []
    for c in constellations:
        constellation_rows.append(
            AthleteConstellationV2(
                run_id=run_id,
                athlete_id=c["athlete_id"],
                fingerprint=c["fingerprint"],
                hub_node_key=c["hub_node_key"],
                member_count=c["member_count"],
                internal_edge_count=c["internal_edge_count"],
                support_bouts=c.get("support_bouts", 0),
                modularity=c["modularity"],
                stability_mean=c["stability_mean"],
                stability_p10=c["stability_p10"],
                summary_json=c.get("summary_json", {}),
            )
        )
        for m in c.get("members", []):
            member_rows.append(
                AthleteConstellationMemberV2(
                    run_id=run_id,
                    athlete_id=c["athlete_id"],
                    fingerprint=c["fingerprint"],
                    node_key=m["node_key"],
                    pagerank=m["pagerank"],
                    weighted_pagerank=m["weighted_pagerank"],
                    degree=m["degree"],
                )
            )
    session.bulk_save_objects(constellation_rows)
    session.bulk_save_objects(member_rows)


def build_constellation_rows_from_detection(
    athlete_id: str,
    g: nx.DiGraph,
    detection: DetectionResult,
    stability_rows: list[ConstellationStability],
    support_bouts_by_fingerprint: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Adapter: turn one athlete's ``constellations.detect``/``stability`` output into
    the list-of-dicts shape ``persist_constellations`` above consumes.

    Field provenance (nothing here is guessed):
    - ``fingerprint``/``hub_node_key``/``member_count``/``internal_edge_count`` come
      straight off each ``Constellation``. ``hub`` is already centrality-based (highest
      weighted degree, doc 04's "not chosen by rating" rule) — not recomputed here.
    - ``modularity`` is ``DetectionResult.modularity`` — the whole partition's score;
      neither this module nor networkx defines a per-constellation modularity, so every
      row from one run carries the same value rather than inventing a split.
    - ``stability_mean``/``stability_p10``/``summary_json`` come from the matching
      ``ConstellationStability`` (``classify_stability`` output), matched by member set
      (not list position — a caller's stability list order isn't guaranteed to track
      ``detection.constellations``).
    - ``support_bouts`` (doc 04's "support bouts") has no producer in either module yet
      — neither ``detect.py`` nor ``stability.py`` counts source bouts per constellation
      — so it's an explicit optional parameter, default 0 (``persist_constellations``'s
      own default), not fabricated here.
    - Each member's ``pagerank``/``weighted_pagerank``/``degree`` are computed on the
      constellation's own INDUCED subgraph (in-constellation centrality, doc 04) — a
      node central within its constellation isn't necessarily central athlete-wide.
      ``degree`` is unweighted (int, matches the DB column); weight is already carried
      by ``weighted_pagerank``.
    """
    stability_by_key = {frozenset(s.members): s for s in stability_rows}
    rows: list[dict[str, Any]] = []
    for c in sorted(detection.constellations, key=lambda c: c.fingerprint):
        sub = g.subgraph(c.members)
        pr = nx.pagerank(sub, weight="weight") if sub.number_of_nodes() else {}
        wpr = weighted_pagerank(sub) if sub.number_of_nodes() else {}
        members = [
            {
                "node_key": m,
                "pagerank": round(pr.get(m, 0.0), 5),
                "weighted_pagerank": round(wpr.get(m, 0.0), 5),
                "degree": sub.degree(m),
            }
            for m in sorted(c.members)
        ]
        s = stability_by_key.get(frozenset(c.members))
        rows.append({
            "athlete_id": athlete_id,
            "fingerprint": c.fingerprint,
            "hub_node_key": c.hub,
            "member_count": len(c.members),
            "internal_edge_count": c.internal_edges,
            "support_bouts": (support_bouts_by_fingerprint or {}).get(c.fingerprint, 0),
            "modularity": detection.modularity,
            "stability_mean": s.mean_jaccard if s else 0.0,
            "stability_p10": s.p10_jaccard if s else 0.0,
            "summary_json": {
                "classification": s.classification.value if s else None,
                "support_athletes": s.support_athletes if s else 0,
            },
            "members": members,
        })
    return rows


def persist_replay_result(session: Session, config: EngineConfig, result: dict[str, Any]) -> str:
    """Write one ``replay.run_replay()`` result to the DB. Returns the new ``run_id``.

    The run row is inserted 'running' and committed immediately, so a failure while
    writing athlete states never leaves a run with no row at all — it leaves a 'failed'
    row, which is the point of the status column (a reader must be able to tell a
    completed run from a dead one, not find nothing). States are inserted in one batch
    (~639 rows today), not a transaction per athlete.

    ``result["node_states"]``/``result["constellations"]`` are optional — absent or empty
    means this run only carries global athlete state (today's actual replay output).
    When present, they're written in the same try/except as the global states, so a
    failure partway through still lands the run row as 'failed', not a silent partial
    write with the run left 'running'.
    """
    run_id = str(uuid.uuid4())
    run = RatingEngineRun(
        id=run_id,
        engine_version=config.engine_version,
        config_json=config.to_dict(),
        source_hash=result["input_hash"],
        status="running",
    )
    session.add(run)
    session.commit()

    try:
        bout_stats = result.get("athlete_bout_stats", {})
        rows = [
            AthleteRatingStateV2(
                run_id=run_id,
                athlete_id=athlete_id,
                rating=state["rating"],
                rating_deviation=state["deviation"],
                volatility=state["volatility"],
                periods=bout_stats.get(athlete_id, {}).get("periods", 0),
                bout_count=bout_stats.get(athlete_id, {}).get("bout_count", 0),
                # last_active_at stays NULL: matches has no per-bout date yet (ADR-04
                # debt — only `year` exists), too coarse to pass off as a timestamp.
            )
            for athlete_id, state in result["states"].items()
        ]
        session.bulk_save_objects(rows)
        if result.get("node_states"):
            persist_node_states(session, run_id, result["node_states"])
        if result.get("constellations"):
            persist_constellations(session, run_id, result["constellations"])
        session.execute(
            sa_update(RatingEngineRun)
            .where(RatingEngineRun.id == run_id)
            .values(status="completed", completed_at=datetime.now(UTC))
        )
        session.commit()
    except Exception:
        session.rollback()
        session.execute(
            sa_update(RatingEngineRun).where(RatingEngineRun.id == run_id).values(
                status="failed"
            )
        )
        session.commit()
        raise

    return run_id
