import numpy as np
import pandas as pd
from decision_vision.vicos_state import (
    build_features,
    evaluate_head,
    load_annotations,
    position_head,
    role_head,
)


def _fake_annotations(n: int, sequences: int = 3) -> dict:
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n):
        seq = 1 + (i % sequences)
        rows.append(
            {
                "image": f"{seq:02d}{i % 100000:05d}",
                "frame": i + 1,
                "position": ["mount1", "mount2", "half_guard1", "standing"][i % 4],
                "pose1": rng.normal(0, 1, (17, 3)).tolist(),
                "pose2": rng.normal(0, 1, (17, 3)).tolist(),
            }
        )
    return rows


def test_load_annotations_skips_single_pose(tmp_path) -> None:
    import json

    rows = _fake_annotations(4)
    rows[1].pop("pose1")
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    frame = load_annotations(path)
    assert len(frame) == 3


def test_load_annotations(tmp_path) -> None:
    import json

    path = tmp_path / "annotations.json"
    path.write_text(json.dumps(_fake_annotations(8)), encoding="utf-8")
    frame = load_annotations(path)
    assert len(frame) == 8
    assert list(frame.columns) == [
        "sequence_id",
        "frame",
        "position",
        "pose1",
        "pose2",
    ]
    assert frame["pose1"].iloc[0].shape == (17, 3)
    assert set(frame["sequence_id"]) == {1, 2, 3}


def test_head_derivations() -> None:
    assert position_head("mount2") == "mount"
    assert position_head("standing") == "standing"
    assert position_head("5050_guard") == "5050_guard"
    assert role_head("mount1") == "athlete1"
    assert role_head("half_guard2") == "athlete2"
    assert role_head("standing") == "none"
    assert role_head("5050_guard") == "none"


def test_build_features_target_shapes() -> None:
    rows = _fake_annotations(6)
    frame = pd.DataFrame(
        [
            {
                "sequence_id": 1 + (i % 3),
                "frame": i + 1,
                "position": r["position"],
                "pose1": np.asarray(r["pose1"], dtype=np.float64),
                "pose2": np.asarray(r["pose2"], dtype=np.float64),
            }
            for i, r in enumerate(rows)
        ]
    )
    x, y = build_features(frame)
    assert x.shape == (6, 68)
    assert y["state"].shape == (6,)
    assert y["position"].shape == (6,)
    assert y["role"].shape == (6,)
    assert set(y["role"]).issubset({"athlete1", "athlete2", "none"})


def test_evaluate_head_reports_folds_and_skips() -> None:
    rng = np.random.default_rng(11)
    n = 90
    groups = np.repeat([1, 2, 3], n // 3)
    y = np.array(["mount1", "guard1"] * (n // 2) + ["guard2"] * 0)
    y = np.where(np.arange(n) % 3 == 0, "standing", y)
    x = rng.normal(0, 1, (n, 68))
    result = evaluate_head(x, y, groups, max_iter=2000)
    assert result["status"] == "ok"
    assert result["evaluated_samples"] > 0
    assert len(result["folds"]) == 3
    for fold in result["folds"]:
        assert fold["status"] in ("ok", "skipped_no_seen_test_classes")
        if fold["status"] == "ok":
            assert 0.0 <= fold["macro_f1"] <= 1.0
            assert 0.0 < fold["uniform_chance_accuracy"] <= 1.0
            assert "held_out_sequence" in fold
    assert result["skipped_unseen_class_samples"] >= 0
