import json

from decision_vision.progress import ProgressReporter


def test_progress_reporter_lifecycle(tmp_path) -> None:
    reporter = ProgressReporter(
        output_dir=tmp_path,
        run_id="test-run",
        pipeline="role_timeline",
    )
    reporter.update(
        phase="sampling",
        current=5,
        total=10,
        message="5/10",
        metrics={"role_resolved": 4},
    )

    data = json.loads((tmp_path / "progress.json").read_text())
    assert data["status"] == "running"
    assert data["percent"] == 50.0
    assert data["metrics"]["role_resolved"] == 4

    reporter.complete(metrics={"role_switches": 2})
    data = json.loads((tmp_path / "progress.json").read_text())
    assert data["status"] == "completed"
    assert data["percent"] == 100.0
    assert data["metrics"]["role_switches"] == 2
