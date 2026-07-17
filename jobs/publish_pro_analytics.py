"""Publish versioned Pro analytics snapshots and athlete dossiers.

Run manually until the paired App contract is validated:
``uv run python -m jobs.publish_pro_analytics --cadence daily --dry-run``.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.network_metrics import (
    network_from_sequences,
    node_centralities,
    weighted_pagerank_ranking,
)
from analysis.path_to_victory import path_to_victory
from analysis.pro_analytics import build_athlete_dossier_v1, build_performance_snapshot_v1
from analysis.style_profile import build_style_profile, qualifies
from db.base import db_session
from db.models import Athlete, AthleteDossier, Graph, Profile, UserPerformanceSnapshot, UserSession
from db.repository import (
    _perspective_view,
    clear_match_cache,
    get_matches_for_athlete,
    prime_match_cache,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishResult:
    owner_id: str
    status: str


def period_bounds(cadence: str, now: datetime) -> tuple[datetime, datetime]:
    """Completed UTC day/week — never publish a partial current period."""
    if cadence not in {"daily", "weekly"}:
        raise ValueError("cadence must be 'daily' or 'weekly'")
    now = now.astimezone(UTC)
    end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    if cadence == "weekly":
        end -= timedelta(days=end.weekday())
        return end - timedelta(days=7), end
    return end - timedelta(days=1), end


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _session_time(data: Mapping[str, Any]) -> datetime:
    raw = data.get("createdAt") or data.get("created_at")
    if not isinstance(raw, str):
        raise ValueError("session has no createdAt")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _sessions_for_period(
    session: Session, owner_id: str, start: datetime, end: datetime
) -> list[Mapping[str, Any]]:
    rows = session.execute(
        select(UserSession).where(
            UserSession.owner_id == owner_id,
            UserSession.deleted_at.is_(None),
        )
    ).scalars()
    out: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row.data, Mapping) or not isinstance(row.data.get("rounds", []), list):
            raise ValueError(f"malformed session {row.id}")
        if any(
            not isinstance(round_, Mapping)
            or not isinstance(round_.get("entries", []), list)
            for round_ in row.data["rounds"]
        ):
            raise ValueError(f"malformed session {row.id}")
        created_at = _session_time(row.data)
        if start <= created_at < end:
            out.append(row.data)
    return out


def _user_graph_context(session: Session, owner_id: str) -> dict[str, Any]:
    graph = session.execute(
        select(Graph).where(Graph.owner_kind == "user", Graph.owner_id == owner_id)
    ).scalar_one_or_none()
    if graph is None:
        return {}
    return {"archetypeReport": graph.archetype_report}


def _failed_payload(cadence: str, start: datetime, end: datetime, now: datetime) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "generatedAt": _iso(now),
        "period": {"cadence": cadence, "start": _iso(start), "end": _iso(end)},
        "dataSufficiency": {"sessionCount": 0, "eventCount": 0, "ready": False},
        "status": "failed",
    }


def _upsert_snapshot(
    session: Session,
    *,
    owner_id: str,
    cadence: str,
    start: datetime,
    end: datetime,
    payload: Mapping[str, Any],
    now: datetime,
    dry_run: bool,
) -> None:
    existing = session.execute(
        select(UserPerformanceSnapshot).where(
            UserPerformanceSnapshot.owner_id == owner_id,
            UserPerformanceSnapshot.cadence == cadence,
            UserPerformanceSnapshot.period_end == end,
        )
    ).scalar_one_or_none()
    if dry_run:
        return
    if existing is None:
        session.add(
            UserPerformanceSnapshot(
                owner_id=owner_id,
                cadence=cadence,
                period_start=start,
                period_end=end,
                schema_version=1,
                status=str(payload["status"]),
                metrics=dict(payload),
                generated_at=now,
            )
        )
        return
    existing.status = str(payload["status"])
    existing.metrics = dict(payload)
    existing.generated_at = now


def _prune_snapshots(session: Session, owner_id: str, cadence: str, dry_run: bool) -> None:
    keep = 90 if cadence == "daily" else 52
    rows = list(
        session.execute(
            select(UserPerformanceSnapshot)
            .where(
                UserPerformanceSnapshot.owner_id == owner_id,
                UserPerformanceSnapshot.cadence == cadence,
            )
            .order_by(UserPerformanceSnapshot.period_end.desc())
        ).scalars()
    )
    if not dry_run:
        for row in rows[keep:]:
            session.delete(row)


def publish_user_snapshot(
    session: Session,
    owner_id: str,
    cadence: str,
    now: datetime,
    *,
    dry_run: bool = False,
) -> PublishResult:
    """Generate one user period; never replace an existing ready row on parse failure."""
    start, end = period_bounds(cadence, now)
    existing = session.execute(
        select(UserPerformanceSnapshot).where(
            UserPerformanceSnapshot.owner_id == owner_id,
            UserPerformanceSnapshot.cadence == cadence,
            UserPerformanceSnapshot.period_end == end,
        )
    ).scalar_one_or_none()
    try:
        sessions = _sessions_for_period(session, owner_id, start, end)
        payload = build_performance_snapshot_v1(
            sessions,
            cadence=cadence,
            period_start=_iso(start),
            period_end=_iso(end),
            generated_at=_iso(now),
            graph=_user_graph_context(session, owner_id),
        )
    except (TypeError, ValueError):
        if existing is None:
            _upsert_snapshot(
                session,
                owner_id=owner_id,
                cadence=cadence,
                start=start,
                end=end,
                payload=_failed_payload(cadence, start, end, now),
                now=now,
                dry_run=dry_run,
            )
        return PublishResult(owner_id=owner_id, status="failed")

    _upsert_snapshot(
        session,
        owner_id=owner_id,
        cadence=cadence,
        start=start,
        end=end,
        payload=payload,
        now=now,
        dry_run=dry_run,
    )
    _prune_snapshots(session, owner_id, cadence, dry_run)
    return PublishResult(owner_id=owner_id, status=str(payload["status"]))


def _athlete_network(
    athlete_id: str, session: Session
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sequences: list[list[dict[str, Any]]] = []
    for match in get_matches_for_athlete(athlete_id, session):
        if match.status != "final" or not match.sequence:
            continue
        sequences.append(
            [
                {
                    "label": event.get("label", ""),
                    "type": event.get("type", ""),
                    "actor_id": event.get("actor"),
                    "successful": event.get("successful") is not False,
                }
                for event in _perspective_view(match, athlete_id).sequence
            ]
        )
    graph = network_from_sequences(sequences)
    centrality = node_centralities(graph)
    return (
        {
            "pageRank": [
                {"label": label, "value": value}
                for label, value in weighted_pagerank_ranking(graph, limit=10)
            ],
            "betweenness": [
                {"label": label, "value": values["betweenness"]}
                for label, values in sorted(
                    centrality.items(), key=lambda item: item[1]["betweenness"], reverse=True
                )[:10]
            ],
        },
        [
            {"label": label, "value": value}
            for label, value in sorted(
                path_to_victory(graph).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:10]
        ],
    )


def publish_athlete_dossier(
    session: Session,
    athlete_id: str,
    now: datetime,
    *,
    dry_run: bool = False,
) -> bool:
    athlete = session.get(Athlete, athlete_id)
    if athlete is None or not qualifies(athlete_id, session):
        return False
    graph = session.execute(
        select(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == athlete_id)
    ).scalar_one_or_none()
    profile = build_style_profile(athlete, session)
    network, ptv = _athlete_network(athlete_id, session)
    payload = build_athlete_dossier_v1(
        athlete={
            "id": athlete.id,
            "name": athlete.name,
            "nickname": athlete.nickname,
            "team": athlete.team,
            "weight_class": athlete.weight_class,
            "belt": athlete.belt,
        },
        style_profile=profile,
        graph_id=graph.id if graph else None,
        network=network,
        path_to_victory=ptv,
        generated_at=_iso(now),
    )
    existing = session.get(AthleteDossier, athlete_id)
    if dry_run:
        return True
    if existing is None:
        session.add(
            AthleteDossier(
                athlete_id=athlete_id,
                graph_id=graph.id if graph else None,
                schema_version=1,
                payload=payload,
                generated_at=now,
            )
        )
    else:
        existing.graph_id = graph.id if graph else None
        existing.schema_version = 1
        existing.payload = payload
        existing.generated_at = now
    return True


def publish(
    session: Session,
    cadence: str,
    now: datetime,
    *,
    user_id: str | None = None,
    athlete_id: str | None = None,
    dry_run: bool = False,
) -> int:
    users = (
        [user_id]
        if user_id
        else list(session.execute(select(Profile.id).where(Profile.is_pro.is_(True))).scalars())
    )
    failures = 0
    for owner_id in users:
        try:
            result = publish_user_snapshot(session, owner_id, cadence, now, dry_run=dry_run)
            if result.status == "failed":
                failures += 1
        except Exception:
            session.rollback()
            logger.exception("Failed Pro snapshot for owner %s", owner_id)
            failures += 1

    if cadence == "weekly":
        athlete_ids: Sequence[str] = (
            [athlete_id]
            if athlete_id
            else list(
                session.execute(select(Athlete.id).where(Athlete.is_published.is_(True))).scalars()
            )
        )
        prime_match_cache(session)
        try:
            for candidate in athlete_ids:
                try:
                    publish_athlete_dossier(session, candidate, now, dry_run=dry_run)
                except Exception:
                    session.rollback()
                    logger.exception("Failed Pro dossier for athlete %s", candidate)
                    failures += 1
        finally:
            clear_match_cache()
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cadence", choices=("daily", "weekly"), required=True)
    parser.add_argument("--user-id")
    parser.add_argument("--athlete-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    with db_session() as session:
        return publish(
            session,
            args.cadence,
            datetime.now(UTC),
            user_id=args.user_id,
            athlete_id=args.athlete_id,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    raise SystemExit(main())
