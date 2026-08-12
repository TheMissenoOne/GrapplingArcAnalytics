from decision_vision.audit_frames import frame_filename


def test_frame_filename_no_prediction_leak() -> None:
    name = frame_filename(5308.0)
    assert name == "frame_05308.00s.png"
    for banned in ("role", "pos", "state", "sw"):
        assert banned not in name


def test_frame_filename_lexical_sort_matches_chronological() -> None:
    timestamps = [5427.5, 5308.0, 5440.0, 5410.5, 5308.5]
    names = [frame_filename(ts) for ts in timestamps]
    assert sorted(names) == [frame_filename(ts) for ts in sorted(timestamps)]
