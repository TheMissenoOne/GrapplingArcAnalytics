import numpy as np
from decision_vision.role_tracking import (
    Box,
    Detection,
    PairIdentityTracker,
    PoseIdentityTracker,
    parse_vicos_label,
    select_role_pair,
)


def det(
    x: float,
    role: str | None,
    position: str = "mount",
    conf: float = 0.9,
) -> Detection:
    return Detection(
        position=position,
        role=role,
        confidence=conf,
        box=Box(x=x, y=100, width=60, height=100),
        raw_class="test",
    )


def test_parse_vicos_label() -> None:
    assert parse_vicos_label("mount_top") == ("mount", "top")
    assert parse_vicos_label("half guard_bottom") == ("half guard", "bottom")
    assert parse_vicos_label("5050 guard") == ("5050 guard", None)


def test_select_role_pair_prefers_matching_top_bottom() -> None:
    pair = select_role_pair(
        [
            det(100, "top", "mount", 0.8),
            det(200, "bottom", "mount", 0.85),
            det(210, "bottom", "half guard", 0.99),
        ]
    )
    assert len(pair) == 2
    assert {item.role for item in pair} == {"top", "bottom"}
    assert {item.position for item in pair} == {"mount"}


def test_identity_survives_role_swap_when_boxes_move_smoothly() -> None:
    tracker = PairIdentityTracker(image_width=320, image_height=320)

    first = tracker.update(
        [
            det(90, "top"),
            det(230, "bottom"),
        ]
    )
    assert first["track_0"].role == "top"
    assert first["track_1"].role == "bottom"

    # Same people, but BJJ roles have swapped. Their boxes remain locally
    # continuous, so persistent visual identity must not follow the role label.
    second = tracker.update(
        [
            det(100, "bottom"),
            det(220, "top"),
        ]
    )
    assert second["track_0"].role == "bottom"
    assert second["track_1"].role == "top"


def test_explicit_top_seed_binds_real_athlete_ids() -> None:
    tracker = PairIdentityTracker(image_width=320, image_height=320)
    first = tracker.update(
        [
            det(90, "top"),
            det(230, "bottom"),
        ]
    )
    top_track = next(
        track_id
        for track_id, detection in first.items()
        if detection.role == "top"
    )
    tracker.seed_top_athlete(
        top_track_id=top_track,
        top_athlete_id="athlete-a",
        other_athlete_id="athlete-b",
    )

    assert tracker.tracks[top_track].athlete_id == "athlete-a"
    other = next(
        track
        for key, track in tracker.tracks.items()
        if key != top_track
    )
    assert other.athlete_id == "athlete-b"


# --- PoseIdentityTracker (raw YOLO keypoints, no vicos label) ---------------------


def _kp(cx: float, cy: float, scale: float = 40.0, conf: float = 0.9) -> np.ndarray:
    """A synthetic (17, 3) COCO pose whose bbox is centered on (cx, cy)."""
    kp = np.zeros((17, 3))
    kp[:, 0] = cx
    kp[:, 1] = cy
    offsets = [(-scale, -scale), (scale, -scale), (-scale, scale), (scale, scale), (0, 0)]
    for i, (dx, dy) in enumerate(offsets):
        kp[i, 0] = cx + dx
        kp[i, 1] = cy + dy
    kp[:, 2] = conf
    return kp


def test_pose_tracker_survives_two_people_crossing() -> None:
    """Two athletes swap screen positions (a sweep). track_0 must keep following
    the SAME physical person, not the "upper" screen slot.

    Asserted by IDENTITY — is this the same array we handed in? — not by
    coordinate. An earlier version of this test compared `track_0[0, 1]` to the
    pose's centre, but keypoint 0 sits at `(cx - scale, cy - scale)`, so it was
    off by `scale` and the test failed for a reason that had nothing to do with
    tracking.
    """
    tracker = PoseIdentityTracker(image_width=640, image_height=480)

    a0, b0 = _kp(cx=150, cy=100), _kp(cx=150, cy=400)
    tracker.update([a0, b0])
    # A starts higher (cy=100), B starts lower (cy=400). Interpolate A down and
    # B up over several small steps -> they cross around the midpoint.
    steps = [(100, 400), (175, 325), (250, 250), (325, 175), (400, 100)]
    a_now = b_now = None
    for a_y, b_y in steps:
        a_now, b_now = _kp(cx=150, cy=a_y), _kp(cx=150, cy=b_y)
        last = tracker.update([a_now, b_now])
        assert last.identity_resolved

    # track_0 was seeded on A. A has travelled to cy=400 and B to cy=100 — they
    # swapped screen slots. Identity must have followed the PERSON.
    assert np.array_equal(last.track_0, a_now)
    assert np.array_equal(last.track_1, b_now)
    assert tracker.assignment_swaps >= 1


def pytest_approx(value: float, tol: float = 1.0) -> float:
    # tiny local helper so this file doesn't need a pytest import just for approx
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return abs(float(other) - value) <= tol

    return _Approx()  # type: ignore[return-value]


def test_pose_tracker_hip_y_flip_does_not_swap_identity() -> None:
    """Two people barely move frame to frame, but which one has the smaller
    box-y (the old hip_y proxy) flips. Identity must not follow that flip.

    This is the 5415 case in miniature: an athlete inverts, hip_y order swaps,
    and the old pipeline relabelled athlete1/athlete2 with no real role change.
    """
    tracker = PoseIdentityTracker(image_width=640, image_height=480)

    left0, right0 = _kp(cx=150, cy=200), _kp(cx=450, cy=210)
    first = tracker.update([left0, right0])
    assert first.identity_resolved
    track0_started_left = np.array_equal(first.track_0, left0)

    # Small motion only -- but the right-hand body is now "above" the left one,
    # which flips a hip_y sort even though nobody swapped places.
    left1, right1 = _kp(cx=152, cy=203), _kp(cx=448, cy=195)
    second = tracker.update([left1, right1])
    assert second.identity_resolved

    # Whichever side track_0 started on, it must STILL be on.
    if track0_started_left:
        assert np.array_equal(second.track_0, left1)
    else:
        assert np.array_equal(second.track_0, right1)


def test_pose_tracker_third_person_does_not_steal_a_track() -> None:
    tracker = PoseIdentityTracker(image_width=640, image_height=480)
    tracker.update([_kp(cx=150, cy=200), _kp(cx=450, cy=200)])
    tracker.update([_kp(cx=155, cy=202), _kp(cx=445, cy=202)])

    # A referee enters: bigger bbox than either athlete, standing between them.
    referee = _kp(cx=300, cy=250, scale=150.0)
    athlete_a, athlete_b = _kp(cx=158, cy=205), _kp(cx=442, cy=205)
    result = tracker.update([athlete_a, referee, athlete_b])

    assert result.identity_resolved
    # By identity, not coordinate: the referee has the LARGEST bbox, so a
    # largest-two selector would have taken it. Continuity must win over size.
    assert np.array_equal(result.track_0, athlete_a) or np.array_equal(result.track_1, athlete_a)
    assert np.array_equal(result.track_0, athlete_b) or np.array_equal(result.track_1, athlete_b)
    assert not np.array_equal(result.track_0, referee)
    assert not np.array_equal(result.track_1, referee)
    assert tracker.third_person_rejections >= 1


def test_pose_tracker_dropout_beyond_max_missing_reinitializes() -> None:
    tracker = PoseIdentityTracker(image_width=640, image_height=480, max_missing=2)
    tracker.update([_kp(cx=150, cy=200), _kp(cx=450, cy=200)])

    for _ in range(4):  # > max_missing consecutive frames with nobody detected
        result = tracker.update([])
        assert not result.identity_resolved

    result = tracker.update([_kp(cx=150, cy=200), _kp(cx=450, cy=200)])
    assert result.identity_resolved
    assert tracker.reinitializations == 1


def test_pose_tracker_recovers_after_a_long_occlusion() -> None:
    """An aged-out track must be able to come back.

    Regression for a ONE-WAY DOOR: `missing > max_missing` removed a track from
    `active`, and only active tracks are assigned, so it could never return. The
    `not active` reinit path could not rescue it either — that needs EVERY track
    dead, and its partner was alive. Measured on real video, one brief occlusion
    (routine in grappling) pinned identity_resolved_rate at 0.10 while 57% of
    frames had two usable candidates.

    Recovery is counted as a reinitialization, not passed off as tracking:
    continuity with the past really was lost.
    """
    tracker = PoseIdentityTracker(image_width=320, image_height=240, max_missing=3)
    tracker.update([_kp(cx=80, cy=100), _kp(cx=240, cy=100)])

    # One athlete is occluded for longer than max_missing.
    for _ in range(5):
        assert not tracker.update([_kp(cx=80, cy=100)]).identity_resolved

    # Both are visible again — the tracker must not stay dead.
    resolved = [
        tracker.update([_kp(cx=80, cy=100), _kp(cx=240, cy=100)]).identity_resolved
        for _ in range(3)
    ]
    assert all(resolved), "an aged-out track never came back"
    assert tracker.reinitializations >= 1, "the lost continuity was not recorded"
