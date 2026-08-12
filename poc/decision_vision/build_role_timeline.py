"""Build a top/bottom + persistent-athlete state timeline from remote match video.

Target of this POC is intentionally simpler than Decision Criterion recognition:

    "Who is top/bottom, what BJJ position are they in, and when do roles switch?"

Frames:
- streamed from the remote video with one FFmpeg process;
- never stored on disk.

Role/position:
- current Analytics ``RoboflowClassifier`` using role-aware bjj3 labels.

Identity:
- two anonymous bbox-continuity tracks by default;
- optionally bind track identities to real Match athlete IDs by explicitly
  seeding which athlete is top at the first role-resolved observation.

Outputs contain metadata only:
- role_samples.csv
- top_bottom_segments.csv
- athlete_state_segments.csv
- role_timeline_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from decision_vision.common import find_analytics_root, parse_timestamp
from decision_vision.frame_stream import FrameStream
from decision_vision.progress import ProgressReporter
from decision_vision.remote_frame_sequence import iter_bgr_frames
from decision_vision.role_tracking import (
    Detection,
    PairIdentityTracker,
    detections_from_roboflow,
    select_role_pair,
)

logger = logging.getLogger("decision_vision.role_timeline")


@dataclass(frozen=True)
class RoleSample:
    timestamp: float
    position: str
    role_resolved: bool
    symmetric: bool

    top_track_id: str
    top_athlete_id: str
    top_confidence: float

    bottom_track_id: str
    bottom_athlete_id: str
    bottom_confidence: float

    track_0_role: str
    track_0_position: str
    track_0_athlete_id: str
    track_0_confidence: float

    track_1_role: str
    track_1_position: str
    track_1_athlete_id: str
    track_1_confidence: float


def _load_match(match_id: str) -> dict[str, Any]:
    from db.base import db_session
    from db.models import Athlete, Match

    with db_session() as session:
        match = session.get(Match, match_id)
        if match is None:
            raise ValueError(f"Match not found: {match_id}")

        athlete_a = session.get(Athlete, match.athlete_a_id)
        athlete_b = session.get(Athlete, match.athlete_b_id)

        return {
            "id": str(match.id),
            "video_url": str(match.video_url or ""),
            "athlete_a_id": str(match.athlete_a_id),
            "athlete_b_id": str(match.athlete_b_id),
            "athlete_a_name": str(athlete_a.name) if athlete_a else "",
            "athlete_b_name": str(athlete_b.name) if athlete_b else "",
            "timeline": list(match.timeline or []),
            "sequence": list(match.sequence or []),
        }


def _timeline_bounds(
    raw_events: list[dict[str, Any]],
) -> tuple[float, float] | None:
    timestamps = []
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        value = parse_timestamp(
            event.get(
                "ts",
                event.get("timestamp_seconds", event.get("timestamp")),
            )
        )
        if value is not None:
            timestamps.append(value)

    if not timestamps:
        return None
    return min(timestamps), max(timestamps)


def _role_of(detection: Detection | None) -> str:
    return detection.role if detection and detection.role else "unknown"


def _position_of(detection: Detection | None) -> str:
    return detection.position if detection else "unknown"


def _confidence_of(detection: Detection | None) -> float:
    return float(detection.confidence) if detection else 0.0


def _athlete_of(tracker: PairIdentityTracker, track_id: str) -> str:
    track = tracker.tracks.get(track_id)
    return str(track.athlete_id) if track and track.athlete_id else ""


def _sample_from_assignments(
    *,
    timestamp: float,
    tracker: PairIdentityTracker,
    assignments: dict[str, Detection],
) -> RoleSample:
    by_role: dict[str, tuple[str, Detection]] = {}
    for track_id, detection in assignments.items():
        if detection.role in {"top", "bottom"}:
            existing = by_role.get(detection.role)
            if (
                existing is None
                or detection.confidence > existing[1].confidence
            ):
                by_role[detection.role] = (track_id, detection)

    top = by_role.get("top")
    bottom = by_role.get("bottom")
    role_resolved = top is not None and bottom is not None

    assigned = list(assignments.items())
    common_positions = [
        detection.position
        for _, detection in assigned
        if detection.position
    ]
    position = (
        common_positions[0]
        if common_positions
        and all(item == common_positions[0] for item in common_positions)
        else "|".join(sorted(set(common_positions)))
        if common_positions
        else "unknown"
    )
    symmetric = bool(assigned) and all(
        detection.role is None
        for _, detection in assigned
    )

    track0 = assignments.get("track_0")
    track1 = assignments.get("track_1")

    return RoleSample(
        timestamp=round(float(timestamp), 4),
        position=position,
        role_resolved=role_resolved,
        symmetric=symmetric,
        top_track_id=top[0] if top else "",
        top_athlete_id=_athlete_of(tracker, top[0]) if top else "",
        top_confidence=round(_confidence_of(top[1]) if top else 0.0, 4),
        bottom_track_id=bottom[0] if bottom else "",
        bottom_athlete_id=_athlete_of(tracker, bottom[0]) if bottom else "",
        bottom_confidence=round(_confidence_of(bottom[1]) if bottom else 0.0, 4),
        track_0_role=_role_of(track0),
        track_0_position=_position_of(track0),
        track_0_athlete_id=_athlete_of(tracker, "track_0"),
        track_0_confidence=round(_confidence_of(track0), 4),
        track_1_role=_role_of(track1),
        track_1_position=_position_of(track1),
        track_1_athlete_id=_athlete_of(tracker, "track_1"),
        track_1_confidence=round(_confidence_of(track1), 4),
    )


def _segments(
    samples: pd.DataFrame,
    *,
    key_columns: list[str],
    value_columns: list[str],
    sample_every: float,
) -> pd.DataFrame:
    if samples.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    start = 0
    n = len(samples)

    def state_at(index: int) -> tuple[str, ...]:
        return tuple(
            str(samples.iloc[index][column])
            for column in key_columns
        )

    while start < n:
        state = state_at(start)
        end = start
        while end + 1 < n and state_at(end + 1) == state:
            end += 1

        first = samples.iloc[start]
        last = samples.iloc[end]

        row: dict[str, Any] = {
            "start": round(float(first["timestamp"]), 4),
            "end": round(
                float(last["timestamp"]) + sample_every,
                4,
            ),
            "duration": round(
                float(last["timestamp"] - first["timestamp"])
                + sample_every,
                4,
            ),
            "samples": end - start + 1,
        }
        for column in value_columns:
            row[column] = first[column]
        rows.append(row)
        start = end + 1

    return pd.DataFrame(rows)


def _athlete_long(samples: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, sample in samples.iterrows():
        for track_id in ("track_0", "track_1"):
            prefix = track_id
            rows.append(
                {
                    "timestamp": sample["timestamp"],
                    "track_id": track_id,
                    "athlete_id": sample[f"{prefix}_athlete_id"],
                    "role": sample[f"{prefix}_role"],
                    "position": sample[f"{prefix}_position"],
                    "confidence": sample[f"{prefix}_confidence"],
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--analytics-root", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cv_decision_poc/role_timeline"),
    )
    parser.add_argument("--source-url")
    parser.add_argument("--start", type=float)
    parser.add_argument("--end", type=float)
    parser.add_argument("--sample-every", type=float, default=1.0)
    parser.add_argument("--frame-size", type=int, default=320)
    parser.add_argument("--position-model-id", default="bjj3/1")
    parser.add_argument(
        "--position-api-url",
        default="https://serverless.roboflow.com",
    )
    parser.add_argument("--cookies-from-browser")
    parser.add_argument(
        "--seed-top-athlete-id",
        help=(
            "Optional explicit identity seed: athlete who is top at the first "
            "role-resolved sampled frame. The other match participant is inferred."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(message)s",
    )
    load_dotenv()
    find_analytics_root(args.analytics_root)

    from cv.roboflow_classifier import RoboflowClassifier

    match = _load_match(args.match_id)
    source_url = str(args.source_url or match["video_url"]).strip()
    if not source_url:
        raise SystemExit("Match has no video_url and no --source-url override")

    bounds = _timeline_bounds(match["timeline"] or match["sequence"])
    if bounds is None and (args.start is None or args.end is None):
        raise SystemExit(
            "No timed DB events. Pass both --start and --end explicitly."
        )

    default_start, default_end = bounds or (0.0, 0.0)
    start = float(args.start if args.start is not None else default_start)
    end = float(args.end if args.end is not None else default_end)
    if end <= start:
        raise SystemExit(f"Invalid range: {start}..{end}")

    participants = {
        match["athlete_a_id"],
        match["athlete_b_id"],
    }
    if args.seed_top_athlete_id and args.seed_top_athlete_id not in participants:
        raise SystemExit("--seed-top-athlete-id is not a match participant")

    other_athlete_id = ""
    if args.seed_top_athlete_id:
        other_athlete_id = next(
            athlete_id
            for athlete_id in participants
            if athlete_id != args.seed_top_athlete_id
        )

    ytdlp_extra = (
        ["--cookies-from-browser", args.cookies_from_browser]
        if args.cookies_from_browser
        else []
    )
    stream = FrameStream(
        output_size=max(224, args.frame_size),
        ytdlp_extra_args=ytdlp_extra,
    )
    classifier = RoboflowClassifier(
        model_id=args.position_model_id,
        api_key=os.environ.get("ROBOFLOW_API_KEY"),
        api_url=args.position_api_url,
    )

    tracker = PairIdentityTracker(
        image_width=max(224, args.frame_size),
        image_height=max(224, args.frame_size),
    )

    reporter = ProgressReporter(
        output_dir=args.output,
        run_id=f"role-{args.match_id}",
        pipeline="role_timeline",
    )
    estimated_total = max(
        1,
        int((end - start) / max(0.1, args.sample_every)) + 1,
    )
    reporter.update(
        phase="sampling",
        current=0,
        total=estimated_total,
        message="Starting remote frame stream",
    )

    samples: list[RoleSample] = []
    seeded = False
    role_switches = 0
    previous_top_track = ""

    for frame_index, (timestamp, frame) in enumerate(
        iter_bgr_frames(
            stream,
            source_url,
            start_seconds=start,
            end_seconds=end,
            sample_every_seconds=max(0.1, args.sample_every),
            output_size=max(224, args.frame_size),
        ),
        start=1,
    ):
        detections = detections_from_roboflow(
            classifier.detect(frame)
        )
        pair = select_role_pair(detections)
        assignments = tracker.update(pair)

        if (
            args.seed_top_athlete_id
            and not seeded
            and assignments
        ):
            top_track = next(
                (
                    track_id
                    for track_id, detection in assignments.items()
                    if detection.role == "top"
                ),
                "",
            )
            if top_track:
                tracker.seed_top_athlete(
                    top_track_id=top_track,
                    top_athlete_id=args.seed_top_athlete_id,
                    other_athlete_id=other_athlete_id,
                )
                seeded = True
                logger.info(
                    "identity seed: top=%s (%s) other=%s",
                    args.seed_top_athlete_id,
                    top_track,
                    other_athlete_id,
                )

        sample = _sample_from_assignments(
            timestamp=timestamp,
            tracker=tracker,
            assignments=assignments,
        )
        samples.append(sample)

        if (
            sample.top_track_id
            and previous_top_track
            and sample.top_track_id != previous_top_track
        ):
            role_switches += 1
        if sample.top_track_id:
            previous_top_track = sample.top_track_id

        if frame_index % 10 == 0 or frame_index == estimated_total:
            resolved_so_far = sum(1 for item in samples if item.role_resolved)
            reporter.update(
                phase="sampling",
                current=min(frame_index, estimated_total),
                total=estimated_total,
                message=f"Sampled {frame_index} frames through {timestamp:.1f}s",
                metrics={
                    "role_resolved_samples": resolved_so_far,
                    "role_switch_events": role_switches,
                    "tracker_reinitializations": tracker.reinitializations,
                },
            )

        if frame_index % 30 == 0:
            logger.info(
                "sampled=%d t=%.1fs role_resolved=%s position=%s",
                frame_index,
                timestamp,
                sample.role_resolved,
                sample.position,
            )

    frame = pd.DataFrame(asdict(item) for item in samples)
    if frame.empty:
        raise RuntimeError("No timeline samples produced")

    args.output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output / "role_samples.csv", index=False)

    top_bottom = _segments(
        frame,
        key_columns=[
            "position",
            "top_track_id",
            "bottom_track_id",
            "symmetric",
        ],
        value_columns=[
            "position",
            "symmetric",
            "top_track_id",
            "top_athlete_id",
            "bottom_track_id",
            "bottom_athlete_id",
        ],
        sample_every=max(0.1, args.sample_every),
    )
    top_bottom.to_csv(
        args.output / "top_bottom_segments.csv",
        index=False,
    )

    athlete_long = _athlete_long(frame)
    athlete_segments = _segments(
        athlete_long,
        key_columns=[
            "track_id",
            "athlete_id",
            "role",
            "position",
        ],
        value_columns=[
            "track_id",
            "athlete_id",
            "role",
            "position",
        ],
        sample_every=max(0.1, args.sample_every),
    )
    athlete_segments.to_csv(
        args.output / "athlete_state_segments.csv",
        index=False,
    )

    role_resolved = int(frame["role_resolved"].sum())
    symmetric = int(frame["symmetric"].sum())
    report = {
        "match_id": args.match_id,
        "athlete_a_id": match["athlete_a_id"],
        "athlete_a_name": match["athlete_a_name"],
        "athlete_b_id": match["athlete_b_id"],
        "athlete_b_name": match["athlete_b_name"],
        "start": start,
        "end": end,
        "sample_every": max(0.1, args.sample_every),
        "samples": len(frame),
        "role_resolved_samples": role_resolved,
        "role_resolved_rate": round(role_resolved / len(frame), 4),
        "symmetric_samples": symmetric,
        "symmetric_rate": round(symmetric / len(frame), 4),
        "role_switch_events": role_switches,
        "identity_seed_requested": bool(args.seed_top_athlete_id),
        "identity_seed_applied": seeded,
        "tracker_reinitializations": tracker.reinitializations,
        "frames_persisted": False,
        "video_persisted": False,
        "identity_method": "bbox_continuity",
        "role_method": f"roboflow:{args.position_model_id}",
    }
    (args.output / "role_timeline_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    reporter.complete(
        message="Role timeline complete",
        metrics={
            "samples": len(frame),
            "role_resolved_rate": report["role_resolved_rate"],
            "role_switch_events": role_switches,
            "tracker_reinitializations": tracker.reinitializations,
        },
    )
    logger.info(
        "role timeline -> %s (samples=%d role_resolved=%.1f%% switches=%d)",
        args.output,
        len(frame),
        100.0 * report["role_resolved_rate"],
        role_switches,
    )
    logger.info("No video/frame media persisted.")


if __name__ == "__main__":
    main()
