"""Persistence behavior for the Pro batch publisher."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from db.base import Base
from db.models import Profile, UserPerformanceSnapshot, UserSession
from jobs import publish_pro_analytics


@pytest.fixture()
def session() -> Session:
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _data(index: int) -> dict[str, object]:
    return {
        "id": f"session-{index}",
        "createdAt": "2026-07-16T12:00:00+00:00",
        "duration": 30,
        "rounds": [
            {
                "outcome": "succeeded",
                "entries": [
                    {"label": "Guard pass", "type": "pass", "actor": "you"},
                    {"label": "Closed guard", "type": "guard", "actor": "you"},
                    {"label": "Back control", "type": "control", "actor": "you"},
                    {"label": "Rear naked choke", "type": "submission", "actor": "you"},
                    {"label": "Single leg", "type": "takedown", "actor": "you"},
                ],
            }
        ],
    }


def test_publish_user_snapshot_upserts_the_same_period(session: Session) -> None:
    owner_id = "00000000-0000-0000-0000-000000000001"
    session.add(Profile(id=owner_id, is_pro=True))
    session.add_all(UserSession(id=f"s-{i}", owner_id=owner_id, data=_data(i)) for i in range(3))
    session.flush()

    now = datetime(2026, 7, 17, 3, 15, tzinfo=UTC)
    first = publish_pro_analytics.publish_user_snapshot(session, owner_id, "daily", now)
    second = publish_pro_analytics.publish_user_snapshot(session, owner_id, "daily", now)

    rows = list(session.execute(select(UserPerformanceSnapshot)).scalars())
    assert first.status == "ready"
    assert second.status == "ready"
    assert len(rows) == 1
    assert rows[0].metrics["status"] == "ready"


def test_publish_user_snapshot_preserves_ready_data_when_source_is_malformed(
    session: Session,
) -> None:
    owner_id = "00000000-0000-0000-0000-000000000002"
    session.add(Profile(id=owner_id, is_pro=True))
    session.add_all(UserSession(id=f"s-{i}", owner_id=owner_id, data=_data(i)) for i in range(3))
    session.flush()
    now = datetime(2026, 7, 17, 3, 15, tzinfo=UTC)
    publish_pro_analytics.publish_user_snapshot(session, owner_id, "daily", now)
    session.add(UserSession(id="broken", owner_id=owner_id, data={"rounds": "not-a-list"}))
    session.flush()

    result = publish_pro_analytics.publish_user_snapshot(session, owner_id, "daily", now)
    row = session.execute(select(UserPerformanceSnapshot)).scalar_one()

    assert result.status == "failed"
    assert row.metrics["status"] == "ready"


def test_publish_user_snapshot_rejects_malformed_entries(session: Session) -> None:
    owner_id = "00000000-0000-0000-0000-000000000006"
    session.add(Profile(id=owner_id, is_pro=True))
    session.add_all(UserSession(id=f"s-{i}", owner_id=owner_id, data=_data(i)) for i in range(3))
    session.flush()
    now = datetime(2026, 7, 17, 3, 15, tzinfo=UTC)
    publish_pro_analytics.publish_user_snapshot(session, owner_id, "daily", now)
    session.add(
        UserSession(
            id="broken-entries",
            owner_id=owner_id,
            data={"createdAt": "2026-07-16T12:00:00+00:00", "rounds": [{"entries": "bad"}]},
        )
    )
    session.flush()

    result = publish_pro_analytics.publish_user_snapshot(session, owner_id, "daily", now)
    row = session.execute(select(UserPerformanceSnapshot)).scalar_one()

    assert result.status == "failed"
    assert row.metrics["status"] == "ready"


def test_publish_prunes_daily_history_beyond_ninety_periods(session: Session) -> None:
    owner_id = "00000000-0000-0000-0000-000000000003"
    session.add(Profile(id=owner_id, is_pro=True))
    session.add_all(UserSession(id=f"s-{i}", owner_id=owner_id, data=_data(i)) for i in range(3))
    old_end = datetime(2026, 1, 1, tzinfo=UTC)
    session.add_all(
        UserPerformanceSnapshot(
            owner_id=owner_id,
            cadence="daily",
            period_start=old_end + timedelta(days=index - 1),
            period_end=old_end + timedelta(days=index),
            schema_version=1,
            status="ready",
            metrics={"status": "ready"},
        )
        for index in range(90)
    )
    session.flush()

    publish_pro_analytics.publish_user_snapshot(
        session, owner_id, "daily", datetime(2026, 7, 17, 3, 15, tzinfo=UTC)
    )

    rows = list(session.execute(select(UserPerformanceSnapshot)).scalars())
    assert len(rows) == 90


def test_publish_continues_after_one_user_failure_and_returns_nonzero(session: Session) -> None:
    good_id = "00000000-0000-0000-0000-000000000004"
    bad_id = "00000000-0000-0000-0000-000000000005"
    session.add_all([Profile(id=good_id, is_pro=True), Profile(id=bad_id, is_pro=True)])
    session.add_all(UserSession(id=f"good-{i}", owner_id=good_id, data=_data(i)) for i in range(3))
    session.add(UserSession(id="bad", owner_id=bad_id, data={"rounds": "not-a-list"}))
    session.flush()

    exit_code = publish_pro_analytics.publish(
        session, "daily", datetime(2026, 7, 17, 3, 15, tzinfo=UTC)
    )

    ready = session.execute(
        select(UserPerformanceSnapshot).where(UserPerformanceSnapshot.owner_id == good_id)
    ).scalar_one()
    assert exit_code == 1
    assert ready.status == "ready"
