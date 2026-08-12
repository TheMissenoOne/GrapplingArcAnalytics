import pandas as pd

ROLE_SAMPLE_COLUMNS = [
    "timestamp",
    "position",
    "role_resolved",
    "symmetric",
    "top_track_id",
    "top_athlete_id",
    "top_confidence",
    "bottom_track_id",
    "bottom_athlete_id",
    "bottom_confidence",
    "track_0_role",
    "track_0_position",
    "track_0_athlete_id",
    "track_1_role",
    "track_1_position",
    "track_1_athlete_id",
]

ATHLETE_SEGMENT_COLUMNS = [
    "track",
    "athlete_id",
    "start",
    "end",
    "position",
    "role",
]


def _role_samples() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": 12.0,
                "position": "mount",
                "role_resolved": True,
                "symmetric": False,
                "top_track_id": "track_0",
                "top_athlete_id": "athlete-a",
                "top_confidence": 0.9,
                "bottom_track_id": "track_1",
                "bottom_athlete_id": "athlete-b",
                "bottom_confidence": 0.8,
                "track_0_role": "top",
                "track_0_position": "mount",
                "track_0_athlete_id": "athlete-a",
                "track_1_role": "bottom",
                "track_1_position": "mount",
                "track_1_athlete_id": "athlete-b",
            },
            {
                "timestamp": 13.0,
                "position": "guard",
                "role_resolved": True,
                "symmetric": False,
                "top_track_id": "track_1",
                "top_athlete_id": "athlete-b",
                "top_confidence": 0.85,
                "bottom_track_id": "track_0",
                "bottom_athlete_id": "athlete-a",
                "bottom_confidence": 0.8,
                "track_0_role": "bottom",
                "track_0_position": "guard",
                "track_0_athlete_id": "athlete-a",
                "track_1_role": "top",
                "track_1_position": "guard",
                "track_1_athlete_id": "athlete-b",
            },
            {
                "timestamp": 14.0,
                "position": "standup",
                "role_resolved": False,
                "symmetric": True,
                "top_track_id": "",
                "top_athlete_id": "",
                "top_confidence": 0.0,
                "bottom_track_id": "",
                "bottom_athlete_id": "",
                "bottom_confidence": 0.0,
                "track_0_role": "unknown",
                "track_0_position": "standup",
                "track_0_athlete_id": "athlete-a",
                "track_1_role": "unknown",
                "track_1_position": "standup",
                "track_1_athlete_id": "athlete-b",
            },
        ]
    )


def _athlete_segments() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "track": "track_0",
                "athlete_id": "athlete-a",
                "start": 0.0,
                "end": 25.0,
                "position": "mount",
                "role": "top",
            },
            {
                "track": "track_0",
                "athlete_id": "athlete-a",
                "start": 25.0,
                "end": 60.0,
                "position": "guard",
                "role": "bottom",
            },
            {
                "track": "track_1",
                "athlete_id": "athlete-b",
                "start": 0.0,
                "end": 25.0,
                "position": "mount",
                "role": "bottom",
            },
            {
                "track": "track_1",
                "athlete_id": "athlete-b",
                "start": 25.0,
                "end": 60.0,
                "position": "guard",
                "role": "top",
            },
        ]
    )


def test_role_sample_schema() -> None:
    frame = _role_samples()
    assert list(frame.columns) == ROLE_SAMPLE_COLUMNS
    assert frame["timestamp"].is_monotonic_increasing
    assert (frame["role_resolved"].dtype == bool) or (
        frame["role_resolved"].isin([True, False]).all()
    )
    assert frame["symmetric"].isin([True, False]).all()
    assert (frame["top_track_id"] == "track_1").all() or (
        frame["top_track_id"] != ""
    ).any()
    assert (frame["track_0_role"] != "top").any()
    assert (frame["track_1_role"] != "bottom").any()
    swapped = frame[frame["timestamp"] == 13.0].iloc[0]
    assert swapped["top_track_id"] == "track_1"
    assert swapped["bottom_track_id"] == "track_0"


def test_athlete_segment_schema() -> None:
    frame = _athlete_segments()
    assert list(frame.columns) == ATHLETE_SEGMENT_COLUMNS
    assert frame["start"].is_monotonic_increasing or all(
        group["start"].is_monotonic_increasing
        for _, group in frame.groupby("track", sort=False)
    )
    assert (frame["end"] >= frame["start"]).all()
    assert frame["role"].isin(["top", "bottom", "unknown"]).all()
    assert frame["position"].notna().all()
    assert frame["athlete_id"].notna().all()
    assert frame["athlete_id"].nunique() == 2
