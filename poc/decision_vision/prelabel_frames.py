"""Pre-label every event frame that has a video timestamp, for human review.

There are ~1936 events in the DB carrying both a ``matches.video_url`` and an
``ts`` inside ``matches.sequence``. Each is a real frame with a human-written
label already on it. This walks them, runs the FULL pipeline (pose → persistent
identity → ViCoS probes), and writes a proposal into ``frame_annotations`` for a
human to approve or correct.

Why the full pipeline and not per-frame classification: `pair_to_features` is
not symmetric and ViCoS's athlete_idx is persistent identity, not geometry. A
frame classified in isolation has no identity to be persistent WITH, so its role
label is a coin flip — the exact defect the 2026-08-12 audit traced. So each
frame is predicted with LEAD-IN: the tracker runs over the seconds before it, so
identity is established by the time the frame of interest arrives.

Whole bouts are not streamed. 204 matches x ~1200 frames each is 245k frames to
produce 1936 predictions. Instead the annotated timestamps are grouped per match
and expanded into windows, overlapping windows merged, so one pass covers a
cluster of events.

Resumable by design: a frame that already has a row is skipped unless
``--repredict``, and even then a human decision is never overwritten — a
prediction under a new model must not silently replace a judgement made against
the old one.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("prelabel")

# Seconds of lead-in before an annotated frame, so the tracker has established
# identity before the frame that matters. 4s at 0.5s sampling is 8 frames — the
# same order as the tracker's own history window.
LEAD_IN_S = 4.0
TRAIL_S = 0.5
SAMPLE_EVERY_S = 0.5
# How far a sampled frame may be from the event's own timestamp and still count
# as that event's frame. Half the sampling interval: any further and a nearer
# sample exists.
MATCH_TOLERANCE_S = 0.26


@dataclass(frozen=True)
class FrameTarget:
    match_id: str
    event_index: int
    ts: float
    label: str | None
    type: str | None


def merge_windows(
    timestamps: list[float],
    lead_in: float = LEAD_IN_S,
    trail: float = TRAIL_S,
) -> list[tuple[float, float]]:
    """Expand each timestamp into a window and merge the overlaps.

    Pure, and the piece worth testing: a bout with events 1s apart must become
    ONE stream pass, not one per event, or the job costs an order of magnitude
    more than it needs to.
    """
    if not timestamps:
        return []
    spans = sorted((max(0.0, t - lead_in), t + trail) for t in timestamps)
    merged: list[tuple[float, float]] = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def nearest_prediction(
    predictions: dict[float, dict[str, Any]],
    ts: float,
    tolerance: float = MATCH_TOLERANCE_S,
) -> dict[str, Any] | None:
    """The sampled prediction closest to `ts`, or None if nothing is close enough.

    Returning None rather than the nearest-at-any-distance is deliberate: a
    prediction from two seconds away is not a prediction about this frame, and
    presenting it for review would launder a guess as a proposal.
    """
    if not predictions:
        return None
    best = min(predictions, key=lambda k: abs(k - ts))
    return predictions[best] if abs(best - ts) <= tolerance else None


def fetch_targets(limit: int | None = None) -> list[FrameTarget]:
    """Every event that has both a video URL and a timestamp."""
    from sqlalchemy import text

    from db.base import db_session

    sql = """
        select m.id::text as match_id,
               (e.ord - 1) as event_index,
               (e.value->>'ts')::float as ts,
               e.value->>'label' as label,
               e.value->>'type' as type
        from matches m,
             jsonb_array_elements(coalesce(m.sequence, '[]'::jsonb)) with ordinality e(value, ord)
        where m.video_url is not null
          and (e.value->>'ts') is not null
        order by m.id, ts
    """
    if limit:
        sql += f" limit {int(limit)}"
    with db_session() as session:
        return [
            FrameTarget(
                match_id=r.match_id,
                event_index=int(r.event_index),
                ts=float(r.ts),
                label=r.label,
                type=r.type,
            )
            for r in session.execute(text(sql))
        ]


def existing_keys(match_id: str) -> set[int]:
    """event_index values already annotated for this match."""
    from sqlalchemy import text

    from db.base import db_session

    with db_session() as session:
        return {
            int(r.event_index)
            for r in session.execute(
                text("select event_index from frame_annotations where match_id = :m"),
                {"m": match_id},
            )
        }


def upsert_prediction(
    target: FrameTarget,
    predicted: dict[str, Any] | None,
    model_version: str,
) -> None:
    """Write a proposal, never over a human decision.

    The `where status = 'pending'` on the update is the whole safety property:
    re-running this job after a review pass must not erase what a human said.
    """
    from sqlalchemy import text

    from db.base import db_session

    with db_session() as session:
        session.execute(
            text(
                """
                insert into frame_annotations
                    (match_id, event_index, frame_ts, event_label, event_type,
                     predicted, status, model_version)
                values (:m, :i, :ts, :label, :type, cast(:pred as jsonb), 'pending', :ver)
                on conflict (match_id, event_index) do update
                   set predicted = excluded.predicted,
                       model_version = excluded.model_version,
                       updated_at = now()
                 where frame_annotations.status = 'pending'
                """
            ),
            {
                "m": target.match_id,
                "i": target.event_index,
                "ts": target.ts,
                "label": target.label,
                "type": target.type,
                "pred": None if predicted is None else json.dumps(predicted),
                "ver": model_version,
            },
        )
        session.commit()


def predict_match(
    match_id: str,
    targets: list[FrameTarget],
    models: Any,
    classes: Any,
    output_size: int = 320,
) -> dict[float, dict[str, Any]]:
    """Run the full pipeline over this match's merged windows."""
    from cv.pose_estimate import PoseEstimator
    from decision_vision.build_role_timeline import _load_match
    from decision_vision.frame_stream import FrameStream
    from decision_vision.live_state import classify_pose_pair
    from decision_vision.remote_frame_sequence import iter_bgr_frames
    from decision_vision.role_tracking import (
        PoseIdentityTracker,
        frame_signature,
        is_shot_change,
    )

    match = _load_match(match_id)
    url = str(match["video_url"])
    estimator = PoseEstimator()
    predictions: dict[float, dict[str, Any]] = {}

    for start, end in merge_windows([t.ts for t in targets]):
        # A fresh tracker per window: the windows are not contiguous, so
        # carrying identity across a gap would assert a continuity that was
        # never observed.
        tracker: PoseIdentityTracker | None = None
        prev_signature = None
        stream = FrameStream(output_size=output_size)
        for ts, frame in iter_bgr_frames(
            stream, url, start_seconds=start, end_seconds=end,
            sample_every_seconds=SAMPLE_EVERY_S, output_size=output_size,
        ):
            if frame is None or not getattr(frame, "size", 0):
                continue
            if tracker is None:
                tracker = PoseIdentityTracker(
                    image_width=frame.shape[1], image_height=frame.shape[0]
                )
            gray = frame[:, :, 0] if frame.ndim == 3 else frame
            signature = frame_signature(gray)
            cut = is_shot_change(prev_signature, signature)
            prev_signature = signature

            assignment = tracker.update(estimator.estimate(frame), shot_changed=cut)
            if not assignment.identity_resolved:
                predictions[float(ts)] = {
                    "identity_resolved": False,
                    "identity_broken": assignment.identity_broken,
                }
                continue
            pred = classify_pose_pair(
                models, classes, assignment.track_0, assignment.track_1
            )
            predictions[float(ts)] = {
                "position": pred["position"][0],
                "position_conf": round(float(pred["position"][1]), 4),
                "role": pred["role"][0],
                "role_conf": round(float(pred["role"][1]), 4),
                "state": pred["state"][0],
                "state_conf": round(float(pred["state"][1]), 4),
                "identity_resolved": True,
                "identity_broken": assignment.identity_broken,
            }
    return predictions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir", type=Path, required=True)
    parser.add_argument("--match-id", help="only this match")
    parser.add_argument("--limit-matches", type=int, help="stop after N matches")
    parser.add_argument("--repredict", action="store_true",
                        help="refresh proposals on frames already pre-labelled "
                             "(human decisions are still never overwritten)")
    parser.add_argument("--model-version", default="identity-v1")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    from decision_vision.common import find_analytics_root

    find_analytics_root(None)
    from decision_vision.live_state import load_probes

    targets = fetch_targets()
    if args.match_id:
        targets = [t for t in targets if t.match_id == args.match_id]
    by_match: dict[str, list[FrameTarget]] = {}
    for t in targets:
        by_match.setdefault(t.match_id, []).append(t)
    logger.info("annotatable frames: %d across %d matches", len(targets), len(by_match))

    models, classes = load_probes(args.probe_dir)
    done_matches = 0
    written = missed = 0

    for match_id, match_targets in by_match.items():
        if args.limit_matches and done_matches >= args.limit_matches:
            break
        already = set() if args.repredict else existing_keys(match_id)
        todo = [t for t in match_targets if t.event_index not in already]
        if not todo:
            logger.info(
                "match %s: all %d frames already pre-labelled",
                match_id[:8], len(match_targets),
            )
            continue
        logger.info("match %s: %d frame(s)", match_id[:8], len(todo))
        try:
            predictions = predict_match(match_id, todo, models, classes)
        except Exception as exc:  # noqa: BLE001 - one bad video must not stop the run
            logger.warning("match %s failed, skipping: %s", match_id[:8], exc)
            continue
        for t in todo:
            pred = nearest_prediction(predictions, t.ts)
            if pred is None:
                missed += 1
            upsert_prediction(t, pred, args.model_version)
            written += 1
        done_matches += 1

    logger.info(
        "done: %d written, %d with no frame close enough to their event",
        written, missed,
    )


if __name__ == "__main__":
    sys.exit(main())
