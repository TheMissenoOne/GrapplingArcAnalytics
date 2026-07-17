"""Tests for user vs pro benchmarking — fixture bundle + synthetic ADCC."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from analysis.benchmark import (
    STYLE_MIX_AXES,
    SUB_FAMILY_KEYS,
    _quartiles,
    athlete_style_distribution_db,
    compare,
    pro_baseline,
    user_submission_profile,
)
from export.benchmark_results import export_benchmark_results, export_pro_baseline_db
from schemas.app_types import UserBundle

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "user_bundle_mini.json"


@pytest.fixture
def bundle() -> UserBundle:
    with open(FIXTURE) as f:
        data = json.load(f)
    return UserBundle.from_json(data)


def test_user_submission_profile_counts(bundle: UserBundle) -> None:
    profile = user_submission_profile(bundle)
    techs = dict(zip(profile["technique"], profile["attempts"]))
    assert techs["rear naked choke"] == 2
    assert techs["armbar"] == 2
    assert techs.get("heel hook") == 1
    assert techs.get("kimura") == 1
    unknown = "unknown technique"
    assert unknown in techs


def test_user_submission_profile_empty() -> None:
    from schemas.app_types import UserBundle

    empty = UserBundle(sessions=[])
    profile = user_submission_profile(empty)
    assert profile.empty


def test_pro_baseline_return() -> None:
    adcc = pd.DataFrame(
        {
            "submission": ["RNC", "armbar", "triangle", "RNC", "heel hook"],
            "year": [2019, 2019, 2021, 2021, 2021],
            "weight_class": ["77", "88", "77", "99", "88"],
            "sex": ["M", "M", "F", "M", "M"],
            "stage": ["F", "SF", "R1", "F", "R2"],
        }
    )
    baseline = pro_baseline(adcc)
    assert "technique" in baseline.columns
    assert "pro_share" in baseline.columns
    assert baseline["pro_share"].sum() > 0


def test_compare_merges() -> None:
    user = pd.DataFrame(
        {
            "technique": ["rear naked choke", "armbar", "not_in_adcc"],
            "attempts": [3, 2, 1],
            "successes": [2, 1, 1],
            "user_share": [0.5, 0.33, 0.17],
        }
    )
    pro = pd.DataFrame(
        {
            "technique": ["rear naked choke", "armbar", "heel hook"],
            "pro_share": [0.3, 0.2, 0.1],
        }
    )
    result = compare(user, pro)
    assert len(result) == 3
    assert "no_pro_data" in result.columns
    assert "ratio" in result.columns
    assert "emphasis" in result.columns
    not_in = result[result["technique"] == "not_in_adcc"]
    assert not_in["no_pro_data"].iloc[0]
    rnc = result[result["technique"] == "rear naked choke"]
    assert not rnc["no_pro_data"].iloc[0]


def test_export_benchmark_results(tmp_path: Path) -> None:
    mock_adcc_data = pd.DataFrame(
        {
            "submission": ["RNC", "armbar", "triangle", "heel hook"],
            "year": [2019, 2021, 2021, 2019],
            "weight_class": ["77", "88", "99", "77"],
            "sex": ["M", "M", "F", "M"],
            "stage": ["F", "SF", "R1", "R2"],
        }
    )

    with (
        patch("export.benchmark_results.PROCESSED_DIR", tmp_path),
        patch("export.benchmark_results.ADCCHistoricalPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline_cls.return_value.run.return_value = mock_adcc_data
        result = export_benchmark_results(FIXTURE)

    assert "total_techniques" in result
    assert result["total_techniques"] >= 4
    assert "output_path" in result

    out_file = tmp_path / "benchmark_results.json"
    assert out_file.exists()
    with open(out_file) as f:
        payload = json.load(f)
    assert "generated_at" in payload
    assert "techniques" in payload
    assert "summary" in payload


def test_quartiles_math() -> None:
    assert _quartiles([]) == {"p25": 0.0, "median": 0.0, "p75": 0.0}
    # evenly spaced 5-value sample — linear-interpolation quantiles land exactly on values.
    assert _quartiles([0.0, 0.25, 0.5, 0.75, 1.0]) == {"p25": 0.25, "median": 0.5, "p75": 0.75}
    # single value → every quantile is that value.
    assert _quartiles([0.4]) == {"p25": 0.4, "median": 0.4, "p75": 0.4}



# ── DB-backed pro-baseline (analysis.benchmark.athlete_style_distribution_db) ──

@pytest.fixture()
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    from sqlalchemy.orm import Session

    import db.models  # noqa: F401 — registers all ORM models
    from db.base import Base
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, checkfirst=True)
    with Session(eng) as s:
        yield s
    Base.metadata.drop_all(eng)


def _athlete(session, name: str):
    from db.models import Athlete
    a = Athlete(name=name, belt="black")
    session.add(a)
    session.flush()
    return a


def _final_match(session, a, b, winner, sequence: list[dict]):
    from db.models import Match
    m = Match(
        athlete_a_id=a.id, athlete_b_id=b.id, winner_id=winner.id if winner else None,
        win_type="SUBMISSION" if winner else "POINTS", status="final", sequence=sequence,
    )
    session.add(m)
    session.flush()
    return m


def _seq(actor_id: str, *, you_pass=0, you_sub=0, you_takedown=0) -> list[dict]:
    """A tiny hand-written match sequence, all events attributed to `actor_id` (the
    athlete whose own-perspective events we want `_perspective_view` to surface)."""
    events = []
    for _ in range(you_pass):
        events.append({"label": "knee slice", "type": "pass", "actor_id": actor_id})
    for _ in range(you_sub):
        events.append({"label": "rear naked choke", "type": "submission",
                       "actor_id": actor_id, "successful": True})
    for _ in range(you_takedown):
        events.append({"label": "double leg", "type": "takedown", "actor_id": actor_id})
    return events


def test_athlete_style_distribution_db_qualifying_and_axes(db_session) -> None:
    a = _athlete(db_session, "A")
    b = _athlete(db_session, "B")
    c = _athlete(db_session, "C")
    d = _athlete(db_session, "D")
    # A: 3 final sequence bouts, against 3 different opponents -> qualifies (>= 3).
    _final_match(db_session, a, b, a, _seq(a.id, you_pass=1, you_sub=1))
    _final_match(db_session, a, c, a, _seq(a.id, you_pass=1, you_sub=1))
    _final_match(db_session, a, d, a, _seq(a.id, you_pass=1, you_sub=1))
    # B: appears in only ONE final sequence bout (the one above vs A) -> below
    # MIN_SEQUENCE_BOUTS, excluded (`qualifies` counts total bout participation,
    # not whose events dominate a bout).

    dist = athlete_style_distribution_db(db_session)

    assert set(dist["styleMix"].keys()) == set(STYLE_MIX_AXES)
    assert set(dist["submissionFamilies"].keys()) == set(SUB_FAMILY_KEYS)
    assert dist["sample"]["athletes"] == 1  # only A qualifies
    assert dist["sample"]["bouts"] == 3

    # A's own-perspective mix across the 3 bouts run through reduce_style_events: A's own
    # events per bout are 1 pass + 1 submission (2 typed events) -> pass=0.5, submission=0.5.
    assert dist["styleMix"]["pass"] == {"p25": 0.5, "median": 0.5, "p75": 0.5}
    assert dist["styleMix"]["submission"] == {"p25": 0.5, "median": 0.5, "p75": 0.5}
    for axis in ("control", "escape", "guard", "sweep", "takedown"):
        assert dist["styleMix"][axis] == {"p25": 0.0, "median": 0.0, "p75": 0.0}

    # A's only landed submission is a strangle (rear naked choke) -> 100% strangle share.
    assert dist["submissionFamilies"]["strangle"] == {"p25": 1.0, "median": 1.0, "p75": 1.0}
    assert dist["submissionFamilies"]["leglock"] == {"p25": 0.0, "median": 0.0, "p75": 0.0}
    assert dist["submissionFamilies"]["armlock"] == {"p25": 0.0, "median": 0.0, "p75": 0.0}


def test_athlete_style_distribution_db_empty(db_session) -> None:
    dist = athlete_style_distribution_db(db_session)
    assert dist["sample"] == {"athletes": 0, "bouts": 0, "events": 0}
    for axis in STYLE_MIX_AXES:
        assert dist["styleMix"][axis] == {"p25": 0.0, "median": 0.0, "p75": 0.0}


def test_export_pro_baseline_db(tmp_path: Path, db_session) -> None:
    a = _athlete(db_session, "A")
    b = _athlete(db_session, "B")
    c = _athlete(db_session, "C")
    d = _athlete(db_session, "D")
    # 3 different opponents so only A (not each single-bout opponent) qualifies.
    _final_match(db_session, a, b, a, _seq(a.id, you_pass=1, you_sub=1))
    _final_match(db_session, a, c, a, _seq(a.id, you_pass=1, you_sub=1))
    _final_match(db_session, a, d, a, _seq(a.id, you_pass=1, you_sub=1))

    out_path = tmp_path / "pro_baseline.json"
    result = export_pro_baseline_db(db_session, out_path)

    assert result["schemaVersion"] == 1
    assert "generatedAt" in result
    assert set(result["styleMix"].keys()) == set(STYLE_MIX_AXES)
    assert set(result["submissionFamilies"].keys()) == set(SUB_FAMILY_KEYS)
    assert result["sample"]["athletes"] == 1
    assert out_path.exists()
    with open(out_path) as f:
        payload = json.load(f)
    assert payload == result
