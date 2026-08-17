"""DB-write side of the rating_v2 replay (wave 7) — the only impure write path here.

Kept separate from ``replay.py`` on purpose: ``replay.py`` stays a pure DB-READ
orchestrator producing a JSON artefact, unit-testable without a DB; this module owns the
write transaction, so it can be exercised against an in-memory SQLite session
(``tests/test_rating_v2.py``) without ever touching Postgres.

ADR-02 (``docs/rating_v2/01_DECISOES.md``): ``run_id`` is a required read key. This module
deliberately exposes no "current state" read helper — only the write path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from analysis.rating_v2.config import EngineConfig
from db.models import AthleteRatingStateV2, RatingEngineRun


def persist_replay_result(session: Session, config: EngineConfig, result: dict[str, Any]) -> str:
    """Write one ``replay.run_replay()`` result to the DB. Returns the new ``run_id``.

    The run row is inserted 'running' and committed immediately, so a failure while
    writing athlete states never leaves a run with no row at all — it leaves a 'failed'
    row, which is the point of the status column (a reader must be able to tell a
    completed run from a dead one, not find nothing). States are inserted in one batch
    (~639 rows today), not a transaction per athlete.
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
        session.execute(
            RatingEngineRun.__table__.update()
            .where(RatingEngineRun.id == run_id)
            .values(status="completed", completed_at=datetime.now(UTC))
        )
        session.commit()
    except Exception:
        session.rollback()
        session.execute(
            RatingEngineRun.__table__.update().where(RatingEngineRun.id == run_id).values(
                status="failed"
            )
        )
        session.commit()
        raise

    return run_id
