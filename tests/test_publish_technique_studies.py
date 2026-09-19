"""Persistence + ts-precision behavior for the technique-studies batch publisher."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

from db.base import Base
from db.models import Athlete, Match, TechniqueStudy
from jobs import publish_technique_studies as pts

NODE = pts.node_key_of("Rear Naked Choke")

# UUID(as_uuid=False) still round-trips values through uuid.UUID(...) even on the sqlite
# stub below (only the DDL type is swapped) — every id needs to be a real UUID hex string.
A, B, C, D, E, F = (f"00000000-0000-0000-0000-00000000000{c}" for c in "abcdef")
M1, M2, M3 = (f"00000000-0000-0000-0000-00000000000{n}" for n in "123")


@pytest.fixture()
def session() -> Session:
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _seq(
    actor: str, opponent: str, *, rnc_ts: float | None, extra: dict | None = None
) -> list[dict]:
    ev = {
        "label": "Rear Naked Choke",
        "type": "submission",
        "actor_id": actor,
        "successful": True,
    }
    if rnc_ts is not None:
        ev["ts"] = rnc_ts
    if extra:
        ev.update(extra)
    return [
        {"label": "Closed Guard", "type": "guard", "actor_id": opponent},
        {"label": "Back Control", "type": "control", "actor_id": actor, "ts": (rnc_ts or 10) - 5},
        ev,
    ]


def _seed(session: Session) -> None:
    """3 final matches, each with one Rear Naked Choke (occ == 3, clears MIN_OCC), covering
    the three classify_ts_origin outcomes and 3 different actor ELOs for the ordering check."""
    session.add_all(
        [
            Athlete(id=A, name="Athlete A", elo=1500.0),
            Athlete(id=B, name="Athlete B", elo=1400.0),
            Athlete(id=C, name="Athlete C", elo=1800.0),
            Athlete(id=D, name="Athlete D", elo=1300.0),
            Athlete(id=E, name="Athlete E", elo=1200.0),
            Athlete(id=F, name="Athlete F", elo=1100.0),
        ]
    )
    session.add_all(
        [
            # video_absolute: start=300 (>60), event ts=350 >= start -> t_secs = ts = 350.
            Match(
                id=M1,
                athlete_a_id=A,
                athlete_b_id=B,
                status="final",
                year=2024,
                event="Event One",
                video_url="https://youtu.be/AAAAAAAAAAA",
                video_start_seconds=300,
                sequence=_seq(A, B, rnc_ts=350),
            ),
            # bout_relative: start=600 (>60), event ts=45 < start -> t_secs = start + ts = 645.
            Match(
                id=M2,
                athlete_a_id=C,
                athlete_b_id=D,
                status="final",
                year=2025,
                event="Event Two",
                video_url="https://youtu.be/BBBBBBBBBBB",
                video_start_seconds=600,
                sequence=_seq(C, D, rnc_ts=45),
            ),
            # unknown (no start, no duration ever probed) -> precision 'bout', t_secs from the
            # video's own `t=` query param.
            Match(
                id=M3,
                athlete_a_id=E,
                athlete_b_id=F,
                status="final",
                year=2023,
                event="Event Three",
                video_url="https://youtu.be/CCCCCCCCCCC?t=77",
                video_start_seconds=None,
                sequence=_seq(E, F, rnc_ts=None),
            ),
        ]
    )
    session.flush()


def test_build_all_produces_the_node_with_occ_at_min_threshold(session: Session) -> None:
    _seed(session)
    payloads = pts.build_all(session, datetime(2026, 9, 18, tzinfo=UTC))

    assert NODE in payloads
    study = payloads[NODE]
    assert study["stats"]["frequency"] == 3
    assert study["stats"]["bouts"] == 3
    assert study["node_key"] == NODE
    assert study["schemaVersion"] == 1


def test_refs_resolve_all_three_ts_precision_branches(session: Session) -> None:
    _seed(session)
    payloads = pts.build_all(session, datetime(2026, 9, 18, tzinfo=UTC))
    refs = {r["match_id"]: r for r in payloads[NODE]["refs"]}

    assert refs[M1]["precision"] == "event"
    assert refs[M1]["t_secs"] == 350
    assert refs[M2]["precision"] == "event"
    assert refs[M2]["t_secs"] == 645
    assert refs[M3]["precision"] == "bout"
    assert refs[M3]["t_secs"] == 77


def test_refs_are_ordered_by_actor_elo_desc_then_year_desc_then_match_id(session: Session) -> None:
    _seed(session)
    payloads = pts.build_all(session, datetime(2026, 9, 18, tzinfo=UTC))
    refs = payloads[NODE]["refs"]

    assert [r["match_id"] for r in refs] == [M2, M1, M3]  # elo 1800 > 1500 > 1200
    assert refs[0]["actor_name"] == "Athlete C"


def test_payload_and_ref_keys_match_the_app_contract_exactly(session: Session) -> None:
    """`services/techniqueStudies.ts:sanitizeTechniqueStudy` on the App side destructures
    these exact field names (`schemaVersion` camelCase, everything else snake_case) — a
    renamed/missing key here silently drops the whole study (top-level) or that one ref/
    counter/chain/next_move on the App. `label` is a tolerated extra at the top level and on
    `counters` (ptv/success/leads_to too), never required."""
    _seed(session)
    payloads = pts.build_all(session, datetime(2026, 9, 18, tzinfo=UTC))
    study = payloads[NODE]

    assert set(study) == {
        "schemaVersion", "node_key", "generated_at", "label",
        "stats", "next_moves", "counters", "chains", "refs",
    }
    assert set(study["stats"]) == {
        "frequency", "success_rate", "centrality", "reward_risk", "bouts",
    }
    assert set(study["next_moves"][0]) == {"key", "label", "p", "count"}
    # RNC is a submission with no own-actor CHAIN_LEN-window in this 3-event fixture (see
    # `_seq` — each actor logs at most 2 own events) — assert the shape only when non-empty.
    if study["chains"]:
        assert set(study["chains"][0]) == {"actions", "count", "bout_ids"}
    ref_keys = {
        "match_id", "slug", "event", "year", "athletes", "vid", "t_secs",
        "precision", "actor_name", "successful", "ts_origin",
    }
    assert set(study["refs"][0]) == ref_keys
    if study["counters"]:
        assert {"key", "label", "count"} <= set(study["counters"][0])


def test_dry_run_writes_nothing(session: Session) -> None:
    _seed(session)
    now = datetime(2026, 9, 18, tzinfo=UTC)

    code = pts.publish(session, now, dry_run=True)

    assert code == 0
    assert session.execute(select(TechniqueStudy)).scalars().all() == []


def test_publish_upserts_then_prunes_vanished_keys_on_a_full_run(session: Session) -> None:
    _seed(session)
    now = datetime(2026, 9, 18, tzinfo=UTC)
    session.add(TechniqueStudy(node_key="stale key", schema_version=1, payload={}))
    session.flush()

    code = pts.publish(session, now)

    assert code == 0
    rows = {r.node_key: r for r in session.execute(select(TechniqueStudy)).scalars()}
    assert "stale key" not in rows
    assert NODE in rows
    assert rows[NODE].payload["stats"]["frequency"] == 3

    # re-run upserts in place rather than duplicating the row.
    pts.publish(session, now)
    assert len(session.execute(select(TechniqueStudy)).scalars().all()) == len(rows)


def test_publish_with_unknown_node_key_fails_and_writes_nothing(session: Session) -> None:
    _seed(session)
    now = datetime(2026, 9, 18, tzinfo=UTC)

    code = pts.publish(session, now, node_key="not-a-real-node")

    assert code == 1
    assert session.execute(select(TechniqueStudy)).scalars().all() == []


def test_publish_with_a_known_node_key_never_touches_other_rows(session: Session) -> None:
    _seed(session)
    now = datetime(2026, 9, 18, tzinfo=UTC)
    session.add(TechniqueStudy(node_key="unrelated key", schema_version=1, payload={}))
    session.flush()

    code = pts.publish(session, now, node_key=NODE)

    assert code == 0
    rows = {r.node_key for r in session.execute(select(TechniqueStudy)).scalars()}
    assert rows == {"unrelated key", NODE}
