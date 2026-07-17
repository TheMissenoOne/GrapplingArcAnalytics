"""Persisted Pro contract invariants."""

from db import models


def test_pro_models_expose_entitlement_and_snapshot_contract() -> None:
    assert hasattr(models.Profile, "is_pro")
    assert hasattr(models, "UserPerformanceSnapshot")
    assert hasattr(models, "AthleteDossier")

    snapshot = models.UserPerformanceSnapshot.__table__
    assert {"owner_id", "cadence", "period_start", "period_end", "metrics"} <= set(
        snapshot.c.keys()
    )
    assert any(
        set(constraint.columns.keys()) == {"owner_id", "cadence", "period_end"}
        for constraint in snapshot.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    )

    dossier = models.AthleteDossier.__table__
    assert dossier.c.athlete_id.primary_key
    assert {"graph_id", "schema_version", "payload", "generated_at"} <= set(dossier.c.keys())
