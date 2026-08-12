"""Track two grapplers across top/bottom role observations.

Important distinction:
- ``role`` means BJJ positional role (top/bottom), as emitted by the role-aware
  bjj3 label family.
- ``track_id`` means persistent visual identity across sampled frames.
- ``athlete_id`` is optional and only becomes known after an explicit seed.

The tracker deliberately does not use faces or venue/background appearance.
Association is based on bounding-box continuity between adjacent samples.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def x1(self) -> float:
        return self.x - self.width / 2.0

    @property
    def y1(self) -> float:
        return self.y - self.height / 2.0

    @property
    def x2(self) -> float:
        return self.x + self.width / 2.0

    @property
    def y2(self) -> float:
        return self.y + self.height / 2.0


@dataclass(frozen=True)
class Detection:
    position: str
    role: str | None
    confidence: float
    box: Box
    raw_class: str


@dataclass
class Track:
    track_id: str
    last_box: Box
    athlete_id: str | None = None
    missing: int = 0
    # Only populated/consulted by PoseIdentityTracker (raw YOLO keypoints have no
    # role/position label to lean on, unlike Detection, so identity needs joint
    # displacement as a second continuity signal).
    last_keypoints: np.ndarray | None = None


def parse_vicos_label(label: str) -> tuple[str, str | None]:
    """Split ``mount_top`` -> (``mount``, ``top``)."""
    value = str(label or "").strip().lower()

    if value.endswith("_top"):
        return value[:-4].strip(), "top"
    if value.endswith("_bottom"):
        return value[:-7].strip(), "bottom"
    return value.replace("_", " ").strip(), None


def detections_from_roboflow(
    payload: dict[str, Any],
) -> list[Detection]:
    out: list[Detection] = []

    for raw in payload.get("detections", []):
        position, role = parse_vicos_label(
            str(raw.get("vicos_class") or raw.get("raw_class") or "")
        )
        out.append(
            Detection(
                position=position,
                role=role,
                confidence=float(raw.get("confidence") or 0.0),
                box=Box(
                    x=float(raw.get("x") or 0.0),
                    y=float(raw.get("y") or 0.0),
                    width=float(raw.get("width") or 0.0),
                    height=float(raw.get("height") or 0.0),
                ),
                raw_class=str(raw.get("raw_class") or ""),
            )
        )

    return out


def _iou(a: Box, b: Box) -> float:
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)

    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    intersection = iw * ih

    area_a = max(0.0, a.width) * max(0.0, a.height)
    area_b = max(0.0, b.width) * max(0.0, b.height)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _association_cost(a: Box, b: Box, image_diag: float) -> float:
    distance = hypot(a.x - b.x, a.y - b.y) / max(1.0, image_diag)
    return 0.65 * distance + 0.35 * (1.0 - _iou(a, b))


def select_role_pair(
    detections: list[Detection],
) -> list[Detection]:
    """Choose the most plausible two-grappler role pair.

    Preference:
    1. highest-confidence top+bottom pair that shares a position;
    2. highest-confidence top + bottom even if model position labels disagree;
    3. two highest-confidence symmetric/unroled detections of one position.
    """
    if not detections:
        return []

    top = sorted(
        [item for item in detections if item.role == "top"],
        key=lambda item: item.confidence,
        reverse=True,
    )
    bottom = sorted(
        [item for item in detections if item.role == "bottom"],
        key=lambda item: item.confidence,
        reverse=True,
    )

    same_position: list[tuple[float, Detection, Detection]] = []
    for top_item in top:
        for bottom_item in bottom:
            if top_item.position == bottom_item.position:
                same_position.append(
                    (
                        top_item.confidence + bottom_item.confidence,
                        top_item,
                        bottom_item,
                    )
                )

    if same_position:
        _, top_item, bottom_item = max(
            same_position,
            key=lambda row: row[0],
        )
        return [top_item, bottom_item]

    if top and bottom:
        return [top[0], bottom[0]]

    unroled = sorted(
        [item for item in detections if item.role is None],
        key=lambda item: item.confidence,
        reverse=True,
    )
    by_position: dict[str, list[Detection]] = {}
    for item in unroled:
        by_position.setdefault(item.position, []).append(item)

    candidates = [
        items[:2]
        for items in by_position.values()
        if len(items) >= 2
    ]
    if candidates:
        return max(
            candidates,
            key=lambda items: sum(item.confidence for item in items),
        )

    return unroled[:2] or top[:1] or bottom[:1]


class PairIdentityTracker:
    """Two-object bbox tracker with optional athlete-ID seeding."""

    def __init__(
        self,
        *,
        image_width: int,
        image_height: int,
        max_missing: int = 3,
    ) -> None:
        self.image_diag = hypot(image_width, image_height)
        self.max_missing = int(max_missing)
        self.tracks: dict[str, Track] = {}
        self.seeded = False
        self.reinitializations = 0

    def seed_top_athlete(
        self,
        *,
        top_track_id: str,
        top_athlete_id: str,
        other_athlete_id: str,
    ) -> None:
        if top_track_id not in self.tracks:
            raise ValueError(f"Unknown track: {top_track_id}")

        self.tracks[top_track_id].athlete_id = top_athlete_id
        other_tracks = [
            track
            for key, track in self.tracks.items()
            if key != top_track_id
        ]
        if other_tracks:
            other_tracks[0].athlete_id = other_athlete_id
        self.seeded = True

    def _initialize(
        self,
        detections: list[Detection],
    ) -> dict[str, Detection]:
        ordered = sorted(
            detections,
            key=lambda item: (
                0 if item.role == "top" else 1 if item.role == "bottom" else 2,
                item.box.x,
            ),
        )

        assignments: dict[str, Detection] = {}
        for index, detection in enumerate(ordered[:2]):
            track_id = f"track_{index}"
            self.tracks[track_id] = Track(
                track_id=track_id,
                last_box=detection.box,
            )
            assignments[track_id] = detection
        return assignments

    def update(
        self,
        detections: list[Detection],
    ) -> dict[str, Detection]:
        detections = detections[:2]
        if not detections:
            for track in self.tracks.values():
                track.missing += 1
            return {}

        if not self.tracks:
            return self._initialize(detections)

        active_tracks = [
            track
            for track in self.tracks.values()
            if track.missing <= self.max_missing
        ]

        if not active_tracks:
            athlete_bindings = {
                key: track.athlete_id
                for key, track in self.tracks.items()
            }
            self.tracks = {}
            assignments = self._initialize(detections)
            for track_id, athlete_id in athlete_bindings.items():
                if athlete_id and track_id in self.tracks:
                    self.tracks[track_id].athlete_id = athlete_id
            self.reinitializations += 1
            return assignments

        assignments: dict[str, Detection] = {}

        if len(active_tracks) >= 2 and len(detections) >= 2:
            t0, t1 = active_tracks[:2]
            d0, d1 = detections[:2]

            direct = (
                _association_cost(t0.last_box, d0.box, self.image_diag)
                + _association_cost(t1.last_box, d1.box, self.image_diag)
            )
            swapped = (
                _association_cost(t0.last_box, d1.box, self.image_diag)
                + _association_cost(t1.last_box, d0.box, self.image_diag)
            )

            if direct <= swapped:
                assignments[t0.track_id] = d0
                assignments[t1.track_id] = d1
            else:
                assignments[t0.track_id] = d1
                assignments[t1.track_id] = d0
        else:
            detection = detections[0]
            best = min(
                active_tracks,
                key=lambda track: _association_cost(
                    track.last_box,
                    detection.box,
                    self.image_diag,
                ),
            )
            assignments[best.track_id] = detection

        for track in self.tracks.values():
            detection = assignments.get(track.track_id)
            if detection is None:
                track.missing += 1
            else:
                track.last_box = detection.box
                track.missing = 0

        return assignments


# --- Pose-native identity tracking (raw YOLO keypoints, no vicos label) -----------
#
# PairIdentityTracker above assumes a Roboflow ``Detection`` (bbox + vicos
# role/position) and its callers pre-filter to <=2 candidates before ``update()``
# ever sees them (``select_role_pair``). The live YOLO path has neither: no
# role/position to disambiguate a bbox-only match, and no upstream filter, so a
# referee or bystander is a real third candidate every frame. PoseIdentityTracker
# considers ALL candidates and assigns by continuity (bbox + keypoint
# displacement), never by size or hip_y.

# Weight split between bbox continuity (centre-distance + IoU, via
# ``_association_cost``) and joint-displacement continuity when scoring a raw
# pose candidate against a track's last observation. Bbox carries slightly more
# weight because it is stable even when a handful of joints go low-confidence
# frame to frame (grip fighting, occlusion); the keypoint term exists to catch
# the case bbox alone can't: two overlapping people whose bboxes are similar in
# size and position but whose actual joints moved.
POSE_COST_BBOX_WEIGHT = 0.6
POSE_COST_KEYPOINT_WEIGHT = 0.4

# Minimum keypoint confidence to use a joint for bbox extraction / displacement
# cost (mirrors cv.pose_estimate._bbox_area's default).
POSE_CONF_THRESH = 0.3

# Above this combined association cost, a candidate is not treated as the same
# person as the track's last observation. Chosen from the cost's own scale, not
# tuned to a clip: a continuing athlete between adjacent sampled frames moves a
# small fraction of the frame diagonal and keeps most bbox overlap, landing well
# under 0.3 in practice; a real cut/occlusion/wrong-person match pushes bbox
# centre-distance and IoU toward their worst case (cost -> ~1.0) well before
# keypoint displacement even matters. 0.5 sits in the gap: generous to real
# motion, strict enough to refuse a guess when identity genuinely isn't clear.
POSE_IDENTITY_COST_MAX = 0.5


def box_from_keypoints(kp: np.ndarray, conf_thresh: float = POSE_CONF_THRESH) -> Box | None:
    """Axis-aligned bbox over confident keypoints, or None if too few to form one."""
    vis = kp[kp[:, 2] > conf_thresh]
    if len(vis) < 2:
        return None
    x1, y1 = float(vis[:, 0].min()), float(vis[:, 1].min())
    x2, y2 = float(vis[:, 0].max()), float(vis[:, 1].max())
    return Box(x=(x1 + x2) / 2.0, y=(y1 + y2) / 2.0, width=x2 - x1, height=y2 - y1)


def _keypoint_displacement(
    a: np.ndarray,
    b: np.ndarray,
    image_diag: float,
    conf_thresh: float = POSE_CONF_THRESH,
) -> float:
    """Mean normalized pixel displacement over joints confident in both poses.

    Falls back to 1.0 (worst case — same scale as a fully non-overlapping bbox)
    when no joint is confidently visible in both, e.g. the person turned around
    between sampled frames and a different subset of joints is occluded.
    """
    both = (a[:, 2] > conf_thresh) & (b[:, 2] > conf_thresh)
    if not both.any():
        return 1.0
    diffs = a[both, :2] - b[both, :2]
    dist = np.sqrt((diffs**2).sum(axis=1))
    return float(dist.mean()) / max(1.0, image_diag)


def _pose_association_cost(
    box_a: Box,
    box_b: Box,
    kp_a: np.ndarray,
    kp_b: np.ndarray,
    image_diag: float,
) -> float:
    """Association cost for raw pose candidates: bbox continuity + joint displacement."""
    bbox_cost = _association_cost(box_a, box_b, image_diag)
    kp_cost = _keypoint_displacement(kp_a, kp_b, image_diag)
    return POSE_COST_BBOX_WEIGHT * bbox_cost + POSE_COST_KEYPOINT_WEIGHT * kp_cost


@dataclass(frozen=True)
class PoseAssignment:
    """Result of one ``PoseIdentityTracker.update`` call.

    ``identity_resolved`` is False whenever the tracker cannot confidently say
    who is who this frame — fewer than two usable candidates, a dropped track,
    or the best available correspondence costing more than
    ``POSE_IDENTITY_COST_MAX``. Callers must treat that frame as unusable rather
    than guess (dropping a frame is strictly better than silently relabeling).
    """

    identity_resolved: bool
    track_0: np.ndarray | None = None
    track_1: np.ndarray | None = None


class PoseIdentityTracker:
    """Persistent two-person identity tracker over raw YOLO ``(17, 3)`` poses.

    ``track_0``/``track_1`` are a STABLE ordering: the same physical person frame
    to frame, independent of who is on top, orientation, screen position, or
    bbox size. Selection favours continuity with the existing tracks over bbox
    size, so a third, larger person (e.g. a referee) does not steal a track once
    the pair is initialised. Geometry (hip_y and friends) may still describe a
    pose; it never decides identity here.
    """

    def __init__(
        self,
        *,
        image_width: int,
        image_height: int,
        max_missing: int = 3,
        conf_thresh: float = POSE_CONF_THRESH,
        cost_max: float = POSE_IDENTITY_COST_MAX,
    ) -> None:
        self.image_diag = hypot(image_width, image_height)
        self.max_missing = int(max_missing)
        self.conf_thresh = conf_thresh
        self.cost_max = cost_max
        self.tracks: dict[str, Track] = {}
        self.reinitializations = 0
        self.assignment_swaps = 0
        self.third_person_rejections = 0
        self._upper_is_track_0: bool | None = None

    def _candidates(self, poses: list[np.ndarray]) -> list[tuple[np.ndarray, Box]]:
        out: list[tuple[np.ndarray, Box]] = []
        for kp in poses:
            box = box_from_keypoints(kp, self.conf_thresh)
            if box is not None:
                out.append((kp, box))
        return out

    def _cold_start(
        self,
        candidates: list[tuple[np.ndarray, Box]],
    ) -> dict[str, tuple[np.ndarray, Box]]:
        # ponytail: no prior identity exists yet to be continuous WITH, so cold
        # start has to pick *some* seed — largest-bbox-first, same heuristic the
        # old hip_y sort used. This is the one place size is allowed to matter;
        # every subsequent frame goes through continuity-based `update()`.
        ordered = sorted(candidates, key=lambda c: -(c[1].width * c[1].height))[:2]
        assignments: dict[str, tuple[np.ndarray, Box]] = {}
        for index, (kp, box) in enumerate(ordered):
            track_id = f"track_{index}"
            self.tracks[track_id] = Track(track_id=track_id, last_box=box, last_keypoints=kp)
            assignments[track_id] = (kp, box)
        return assignments

    def update(self, poses: list[np.ndarray]) -> PoseAssignment:
        candidates = self._candidates(poses)

        if not self.tracks:
            assignments = self._cold_start(candidates) if len(candidates) >= 2 else {}
            return self._finish(assignments)

        active = [track for track in self.tracks.values() if track.missing <= self.max_missing]

        if not active:
            if len(candidates) >= 2:
                bindings = {key: track.athlete_id for key, track in self.tracks.items()}
                self.tracks = {}
                assignments = self._cold_start(candidates)
                for track_id, athlete_id in bindings.items():
                    if athlete_id and track_id in self.tracks:
                        self.tracks[track_id].athlete_id = athlete_id
                self.reinitializations += 1
                return self._finish(assignments)
            for track in self.tracks.values():
                track.missing += 1
            return self._finish({})

        if not candidates:
            for track in self.tracks.values():
                track.missing += 1
            return self._finish({})

        if len(candidates) > len(active):
            # More people in frame than tracks to fill — at least one candidate
            # (e.g. a referee) is necessarily excluded by the assignment below,
            # regardless of its bbox size.
            self.third_person_rejections += 1

        cost = np.array(
            [
                [
                    _pose_association_cost(
                        track.last_box, box, track.last_keypoints, kp, self.image_diag
                    )
                    for kp, box in candidates
                ]
                for track in active
            ]
        )
        row_ind, col_ind = linear_sum_assignment(cost)

        assignments: dict[str, tuple[np.ndarray, Box]] = {}
        for row, col in zip(row_ind, col_ind):
            if cost[row, col] <= self.cost_max:
                assignments[active[row].track_id] = candidates[col]

        for track in self.tracks.values():
            match = assignments.get(track.track_id)
            if match is None:
                track.missing += 1
            else:
                kp, box = match
                track.last_box = box
                track.last_keypoints = kp
                track.missing = 0

        # Revive a track that aged out, from a candidate nobody claimed.
        #
        # Without this, `missing > max_missing` was a ONE-WAY DOOR: an aged-out
        # track left `active` and could never be assigned again, because only
        # active tracks are assigned. The `not active` reinit path could not
        # rescue it either, since that needs EVERY track dead and its partner is
        # still alive. Measured consequence: one brief occlusion — routine in
        # grappling — permanently degraded the tracker to a single track, and it
        # never resolved another frame. On the audited window that showed up as
        # identity_resolved_rate 0.10 against 57% of frames having two usable
        # candidates.
        #
        # Re-seeding is honest but NOT free: continuity with the past is broken,
        # so it is counted as a reinitialization rather than passed off as
        # tracking. The frame is usable going forward; the counter says at whose
        # expense.
        claimed = {id(match[0]) for match in assignments.values()}
        spare = [c for c in candidates if id(c[0]) not in claimed]
        for track in self.tracks.values():
            if track.missing <= self.max_missing or not spare:
                continue
            kp, box = spare.pop(0)
            track.last_box = box
            track.last_keypoints = kp
            track.missing = 0
            track.athlete_id = None
            assignments[track.track_id] = (kp, box)
            self.reinitializations += 1

        return self._finish(assignments)

    def _finish(self, assignments: dict[str, tuple[np.ndarray, Box]]) -> PoseAssignment:
        t0 = assignments.get("track_0")
        t1 = assignments.get("track_1")
        if t0 is None or t1 is None:
            return PoseAssignment(identity_resolved=False)

        upper_is_track_0 = t0[1].y <= t1[1].y
        if self._upper_is_track_0 is not None and self._upper_is_track_0 != upper_is_track_0:
            # Screen-position order flipped (e.g. an inversion or a sweep) but
            # track identity did not follow it — the exact behaviour this
            # tracker exists to guarantee. Counted, not corrected.
            self.assignment_swaps += 1
        self._upper_is_track_0 = upper_is_track_0

        return PoseAssignment(identity_resolved=True, track_0=t0[0], track_1=t1[0])
