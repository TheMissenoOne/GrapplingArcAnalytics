import json

from decision_vision import dashboard
from fastapi.testclient import TestClient


def test_dashboard_lists_role_run(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "role_timeline"
    run_dir.mkdir(parents=True)
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "run_id": "role",
                "pipeline": "role_timeline",
                "status": "running",
                "phase": "sampling",
                "current": 2,
                "total": 4,
                "percent": 50.0,
                "message": "sampling",
                "metrics": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path.resolve())

    client = TestClient(dashboard.app)
    response = client.get("/api/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"][0]["pipeline"] == "role_timeline"
    assert payload["runs"][0]["percent"] == 50.0


def test_dashboard_root_renders() -> None:
    client = TestClient(dashboard.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Decision Vision POC" in response.text
