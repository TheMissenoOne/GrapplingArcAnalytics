"""Live transfer smoke: YOLO pose (local) -> ViCoS-trained probe -> state/role.

Transfers the ViCoS pose->state findings to real broadcast video without the
bjj3 Roboflow API: per sampled frame, run the generic local pose estimator,
resolve WHO IS WHO with `role_tracking.PoseIdentityTracker`, assemble
cv.pose_features.pair_to_features in that persistent order, and predict
position + top/bottom role + state with the full-fit probes persisted by
vicos_state.py --train-full.

Identity is tracked, never sorted. This used to order the pair by hip_y, a
per-frame screen-space sort with no memory: an athlete inverting swapped
athlete1/athlete2 with no real role change. Because pair_to_features is not
symmetric and ViCoS's athlete_idx is persistent identity rather than geometry
(~0.47 hip-y correlation, see vicos_state.py), that corrupted the FEATURE
VECTOR — exposing position and state, not merely the role label. A frame whose
identity cannot be resolved is emitted as unusable rather than guessed.

Instrumentation contract (research-methodology, POC Decision Vision):
  - per-head confidence (mean/median/p10/p90) on defined labels
  - per-position breakdown: samples, role_explicit_rate, role/state confidence
  - temporal stability: flips/min per head, median segment durations
  - role_observed_rate_raw = direct frame-level evidence only
  - role_timeline_coverage = observed + temporal propagation (NOT accuracy)
  - role_inferred_rate = share of pair timeline filled by propagation alone
  - smoothing operates ONLY on state/position/role; identity (track_0/track_1)
    remains the tracker's responsibility and never appears in smooth_timeline
    inputs or outputs.

Research-only artifact; CC BY-NC-SA applies to ViCoS-trained probes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("data/cv_decision_poc/vicos_transfer")
DEFAULT_PROBE_DIR = Path("data/cv_decision_poc/vicos_state/models")

ROLE_LABELS = {"athlete1", "athlete2"}


def load_probes(probe_dir: Path) -> tuple[dict[str, Any], dict[str, list[str]]]:
    from joblib import load

    heads = ("state", "position", "role")
    models: dict[str, Any] = {}
    for head in heads:
        eq_path = probe_dir / f"probe_{head}_eq.joblib"
        path = eq_path if eq_path.exists() else probe_dir / f"probe_{head}.joblib"
        models[head] = load(path)

    classes: dict[str, list[str]] = {}
    for head in heads:
        classes_ = models[head].classes_
        classes[head] = (
            list(classes_) if isinstance(classes_, (list, tuple)) else list(classes_.tolist())
        )
    return models, classes


def classify_pose_pair(
    models: dict[str, Any],
    classes: dict[str, list[str]],
    kp0: np.ndarray,
    kp1: np.ndarray,
) -> dict[str, tuple[str, float]]:
    """Predict each head; returns {head: (label, confidence)}."""
    from cv.pose_features import pair_to_features

    x = pair_to_features(kp0, kp1).reshape(1, -1)
    out: dict[str, tuple[str, float]] = {}
    for head, model in models.items():
        proba = model.predict_proba(x)[0]
        idx = int(np.argmax(proba))
        out[head] = (classes[head][idx], float(proba[idx]))
    return out


def _percentiles(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return {"mean": 0.0, "median": 0.0, "p10": 0.0, "p90": 0.0}
    return {
        "mean": round(float(values.mean()), 4),
        "median": round(float(np.median(values)), 4),
        "p10": round(float(np.percentile(values, 10)), 4),
        "p90": round(float(np.percentile(values, 90)), 4),
    }


def _flips_per_minute(rows: list[dict[str, Any]], head: str) -> float:
    labels = [r[head] for r in rows]
    if len(labels) < 2:
        return 0.0
    nulls = {"unknown", "none"}
    changes = sum(
        1
        for a, b in zip(labels, labels[1:])
        if a not in nulls and b not in nulls and a != b
    )
    start = rows[0]["timestamp"]
    end = rows[-1]["timestamp"]
    minutes = max(1e-6, (end - start) / 60.0)
    return round(changes / minutes, 4)


def _median_segment_duration_s(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> float:
    durations: list[float] = []
    prev_key = None
    seg_start: float | None = None
    for row in rows:
        key = tuple(str(row[f]) for f in fields)
        if key != prev_key:
            if seg_start is not None:
                durations.append(row["timestamp"] - seg_start)
            seg_start = row["timestamp"]
            prev_key = key
    if seg_start is not None and rows:
        durations.append(rows[-1]["timestamp"] - seg_start)
    if not durations:
        return 0.0
    return round(float(np.median(durations)), 2)


def smooth_timeline(
    rows: list[dict[str, Any]],
    *,
    min_duration_s: float = 2.0,
    role_conf_min: float = 0.85,
    persist: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Smooth role/position/state over time; identity untouched.

    Passes (in order):
      1. role carry: below role_conf_min, the role sticks to the last committed
         explicit (athlete1/athlete2) label instead of decaying to none/unknown.
         role_conf_min doubles as the COMMIT floor (window3 audit: 0.85 kills
         flicker-commits with frame conf <= 0.81; see pass2_switch_audit.md).
      2. role persistence: a new explicit role commits only after ``persist``
         consecutive agreeing frames; interim frames keep the previous role.
      3. min-duration squash: (position, state) segments shorter than
         min_duration_s are absorbed into the previous segment; the first
         segment, if short, is absorbed into the next. Role is NEVER touched
         by this pass: it is already persistence-smoothed (pass 2).

    Returns (smoothed_rows, segments). segments = run-length encoding:
    {start, end, seconds, position, role, state}.
    """
    out: list[dict[str, Any]] = []
    committed_role: str | None = None
    pending_role: str | None = None
    pending_count = 0

    for row in rows:
        sm = dict(row)
        role = str(row.get("role") or "unknown")
        role_conf = float(row.get("role_conf") or 0.0)

        if role in ROLE_LABELS and role_conf >= role_conf_min:
            if role != committed_role:
                if committed_role is None:
                    committed_role = role
                    pending_role = None
                    pending_count = 0
                elif role == pending_role:
                    pending_count += 1
                else:
                    pending_role = role
                    pending_count = 1
                if pending_count >= persist:
                    committed_role = role
                    pending_role = None
                    pending_count = 0
            else:
                pending_role = None
                pending_count = 0
        sm["role"] = committed_role if committed_role is not None else role
        out.append(sm)

    if not out:
        return [], []

    spans: list[dict[str, Any]] = []
    prev_key: tuple[str, str, str] | None = None
    for row in out:
        key = (str(row["position"]), str(row["role"]), str(row["state"]))
        if key != prev_key:
            spans.append({"start": row["timestamp"], "end": row["timestamp"],
                          "position": key[0], "role": key[1], "state": key[2]})
            prev_key = key
    if len(spans) > 1:
        for i in range(1, len(spans)):
            spans[i - 1]["end"] = spans[i]["start"]
    spans[-1]["end"] = out[-1]["timestamp"]

    void = [str(s["position"]) == "unknown" for s in spans]
    short = [round(s["end"] - s["start"], 2) < min_duration_s for s in spans]

    resolved_key: list[tuple[str, str]] = [
        (spans[i]["position"], spans[i]["state"]) for i in range(len(spans))
    ]

    i = 0
    while i < len(spans):
        if not short[i]:
            i += 1
            continue
        a = i
        while i < len(spans) and short[i]:
            i += 1
        b = i - 1
        run = list(range(a, b + 1))
        real_in_run = [j for j in run if not void[j]]
        left = a - 1
        right = b + 1
        if real_in_run:
            if right < len(spans) and not void[right]:
                key = resolved_key[right]
            elif left >= 0 and not void[left]:
                key = resolved_key[left]
            else:
                counts: dict[tuple[str, str], int] = {}
                for j in real_in_run:
                    k = resolved_key[j]
                    counts[k] = counts.get(k, 0) + 1
                key = max(counts.items(), key=lambda kv: kv[1])[0]
        elif right < len(spans):
            key = resolved_key[right]
        elif left >= 0:
            key = resolved_key[left]
        else:
            key = resolved_key[a]
        for j in run:
            resolved_key[j] = key

    smoothed: list[dict[str, Any]] = []
    for row in out:
        ts = row["timestamp"]
        sm = dict(row)
        found = False
        for i, span in enumerate(spans):
            if span["start"] <= ts < span["end"]:
                k = resolved_key[i]
                sm["position"], sm["state"] = k
                found = True
                break
        if not found:
            k = resolved_key[-1]
            sm["position"], sm["state"] = k
        smoothed.append(sm)

    # Segments are rebuilt from the FINAL rows, keyed on ALL THREE dimensions.
    #
    # They used to be built from the PRE-squash spans and then merged on
    # (position, state) alone — role-blind — so a role switch occurring inside a
    # run of one position/state was swallowed, and the surviving segment
    # inherited the FIRST span's role. That is why segments.csv reported
    # `standing 5414.5->5424.5 athlete2` while state_samples.csv carried
    # `athlete1` from 5415.0: the rows were right and the segments were not.
    # A boundary must exist wherever any semantically relevant dimension changes.
    merged: list[dict[str, Any]] = []
    for row in smoothed:
        key = (str(row["position"]), str(row["role"]), str(row["state"]))
        ts = row["timestamp"]
        if merged:
            merged[-1]["end"] = ts
            prev = (merged[-1]["position"], merged[-1]["role"], merged[-1]["state"])
            if prev == key:
                continue
        merged.append(
            {
                "start": ts,
                "end": ts,
                "position": key[0],
                "role": key[1],
                "state": key[2],
            }
        )
    for seg in merged:
        seg["seconds"] = round(seg["end"] - seg["start"], 2)

    return smoothed, merged


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pair_rows = [r for r in rows if r["pose_pair"]]
    n = len(rows)
    n_pair = len(pair_rows)
    duration_s = (rows[-1]["timestamp"] - rows[0]["timestamp"]) if len(rows) > 1 else 0.0

    conf: dict[str, dict[str, float]] = {}
    for head in ("position", "role", "state"):
        vals = [r[f"{head}_conf"] for r in pair_rows if r[head] != "unknown"]
        conf[head] = _percentiles(np.asarray(vals))

    by_position: dict[str, dict[str, Any]] = {}
    for r in pair_rows:
        pos = r["position"]
        bucket = by_position.setdefault(
            pos,
            {"samples": 0, "role_explicit": 0, "role_conf": [], "state_conf": []},
        )
        bucket["samples"] += 1
        if r["role"] in ROLE_LABELS:
            bucket["role_explicit"] += 1
        bucket["role_conf"].append(r["role_conf"])
        bucket["state_conf"].append(r["state_conf"])
    per_position = {
        pos: {
            "samples": b["samples"],
            "role_explicit_rate": round(b["role_explicit"] / b["samples"], 4),
            "role_conf_mean": round(float(np.mean(b["role_conf"])), 4),
            "state_conf_mean": round(float(np.mean(b["state_conf"])), 4),
        }
        for pos, b in sorted(by_position.items(), key=lambda kv: -kv[1]["samples"])
    }

    return {
        "frames_sampled": n,
        "duration_s": round(duration_s, 2),
        "pose_pair_rate": round(n_pair / max(1, n), 4),
        "position_coverage": round(n_pair / max(1, n), 4),
        "position_overall_conf": conf["position"],
        "role_overall_conf": conf["role"],
        "state_overall_conf": conf["state"],
        "per_position": per_position,
        "flips_per_minute": {
            "state": _flips_per_minute(rows, "state"),
            "position": _flips_per_minute(rows, "position"),
            "role": _flips_per_minute(rows, "role"),
        },
        "median_segment_duration_s": {
            "state": _median_segment_duration_s(rows, ("state",)),
            "role": _median_segment_duration_s(rows, ("role",)),
            "position_role": _median_segment_duration_s(rows, ("position", "role")),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--start", type=float)
    parser.add_argument("--end", type=float)
    parser.add_argument("--sample-every", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--probe-dir", type=Path, default=DEFAULT_PROBE_DIR)
    parser.add_argument("--no-smooth", action="store_true")
    parser.add_argument("--min-duration", type=float, default=2.0)
    parser.add_argument("--role-conf", type=float, default=0.85)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(message)s",
    )
    from dotenv import load_dotenv

    load_dotenv(".env")
    os.environ.setdefault("PYTHONPATH", "poc")

    from cv.pose_estimate import PoseEstimator
    from decision_vision.build_role_timeline import _load_match, _timeline_bounds
    from decision_vision.common import find_analytics_root
    from decision_vision.frame_stream import FrameStream
    from decision_vision.remote_frame_sequence import iter_bgr_frames

    find_analytics_root(None)

    match = _load_match(args.match_id)
    source_url = str(match["video_url"] or "").strip()
    if not source_url:
        raise SystemExit(f"Match {args.match_id} has no video_url")

    bounds = _timeline_bounds(match["timeline"] or match["sequence"])
    if bounds is None and (args.start is None or args.end is None):
        raise SystemExit("No timed events; pass --start and --end")
    default_start, default_end = bounds or (0.0, 0.0)
    start = float(args.start if args.start is not None else default_start)
    end = float(args.end if args.end is not None else default_end)
    if end <= start:
        raise SystemExit(f"Invalid range: {start}..{end}")

    logger.info("loading probes from %s", args.probe_dir)
    models, classes = load_probes(args.probe_dir)

    logger.info("warming pose estimator (YOLO local)")
    pose_estimator = PoseEstimator()
    stream = FrameStream(output_size=320)

    # Persistent identity, NOT a per-frame screen-space sort. The old path
    # (`select_grappler_pair` -> largest two, ordered by hip_y) recomputed
    # athlete1/athlete2 from scratch every frame, so an inversion swapped the
    # labels with no real role change — and because `pair_to_features` is not
    # symmetric and ViCoS trained on a persistent ordering, that corrupted the
    # FEATURE VECTOR, not just the label. Dimensions come from the first frame.
    from decision_vision.role_tracking import PoseIdentityTracker

    identity_tracker: PoseIdentityTracker | None = None

    from decision_vision.progress import ProgressReporter

    reporter = ProgressReporter(
        output_dir=args.output,
        run_id=f"transfer-{args.match_id[:8]}",
        pipeline="vicos_transfer",
    )
    estimated_total = max(1, int((end - start) / args.sample_every) + 1)
    reporter.update(
        phase="sampling",
        current=0,
        total=estimated_total,
        message="Streaming frames + local pose + probe classify",
    )

    rows: list[dict[str, Any]] = []
    sampled = 0
    pose_pair_frames = 0
    for frame_index, (timestamp, frame) in enumerate(
        iter_bgr_frames(
            stream,
            source_url,
            start_seconds=start,
            end_seconds=end,
            sample_every_seconds=args.sample_every,
            output_size=320,
        )
    ):
        sampled += 1
        poses = pose_estimator.estimate(frame)

        if identity_tracker is None:
            h, w = frame.shape[0], frame.shape[1]
            identity_tracker = PoseIdentityTracker(image_width=w, image_height=h)

        assignment = identity_tracker.update(poses)
        if not assignment.identity_resolved:
            # Explicit non-correspondence. Dropping the frame is strictly better
            # than guessing: a silent track_0 swap relabels a whole run.
            rows.append(
                {
                    "timestamp": round(timestamp, 2),
                    "position": "unknown",
                    "position_conf": 0.0,
                    "role": "unknown",
                    "role_conf": 0.0,
                    "state": "unknown",
                    "state_conf": 0.0,
                    "pose_pair": False,
                    "identity_resolved": False,
                }
            )
            continue
        pose_pair_frames += 1
        kp0, kp1 = assignment.track_0, assignment.track_1
        pred = classify_pose_pair(models, classes, kp0, kp1)
        rows.append(
            {
                "timestamp": round(timestamp, 2),
                "position": pred["position"][0],
                "position_conf": pred["position"][1],
                "role": pred["role"][0],
                "role_conf": pred["role"][1],
                "state": pred["state"][0],
                "state_conf": pred["state"][1],
                "identity_resolved": True,
                # Track positions, so an identity swap can be located in time.
                "t0_x": assignment.track_0_xy[0] if assignment.track_0_xy else None,
                "t0_y": assignment.track_0_xy[1] if assignment.track_0_xy else None,
                "t1_x": assignment.track_1_xy[0] if assignment.track_1_xy else None,
                "t1_y": assignment.track_1_xy[1] if assignment.track_1_xy else None,
                "order_flipped": assignment.order_flipped,
                "pose_pair": True,
            }
        )
        if frame_index % 25 == 0:
            logger.info(
                "t=%.1fs %s/%s role=%s pos=%s",
                timestamp,
                sampled,
                estimated_total,
                pred["role"][0],
                pred["position"][0],
            )
        reporter.update(
            phase="sampling",
            current=sampled,
            total=estimated_total,
            message=f"Sampled {sampled}/{estimated_total} frames",
        )

    final_rows = rows
    segments: list[dict[str, Any]] = []
    if not args.no_smooth:
        final_rows, segments = smooth_timeline(
            rows,
            min_duration_s=args.min_duration,
            role_conf_min=args.role_conf,
        )

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "state_samples_raw.csv").write_text(
        pd.DataFrame(rows).to_csv(index=False), encoding="utf-8"
    )
    smooth_frame = pd.DataFrame(final_rows)
    (args.output / "state_samples.csv").write_text(
        smooth_frame.to_csv(index=False), encoding="utf-8"
    )
    if segments:
        (args.output / "segments.csv").write_text(
            pd.DataFrame(segments).to_csv(index=False), encoding="utf-8"
        )

    report = assemble_report(
        rows,
        final_rows,
        segments,
        match_id=args.match_id,
        start=start,
        end=end,
        sample_every=args.sample_every,
    )
    # Identity instrumentation. Without these the rerun proves nothing: a drop in
    # role flips could just as easily mean the tracker refused every frame.
    resolved = sum(1 for r in rows if r.get("identity_resolved"))
    identity_metrics = {
        "identity_resolved_rate": round(resolved / max(1, sampled), 4),
        "tracker_reinitializations": identity_tracker.reinitializations if identity_tracker else 0,
        "assignment_swaps": identity_tracker.assignment_swaps if identity_tracker else 0,
        "third_person_rejections": (
            identity_tracker.third_person_rejections if identity_tracker else 0
        ),
    }
    report["identity"] = identity_metrics
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    reporter.complete(
        message=f"Transfer complete: {sampled} frames, "
        f"role_observed_rate_raw={report['role_observed_rate_raw']}, "
        f"identity_resolved_rate={identity_metrics['identity_resolved_rate']}",
        metrics={
            "pose_pair_rate": report["raw"]["pose_pair_rate"],
            "role_observed_rate": report["role_observed_rate_raw"],
            "role_flips_per_min": report["raw"]["flips_per_minute"]["role"],
            "segments": len(segments),
            **identity_metrics,
        },
    )
    logger.info("report -> %s", args.output / "report.json")
    logger.info("SMOKE RESULT: %s", json.dumps(report, ensure_ascii=False))


def assemble_report(
    rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    *,
    match_id: str,
    start: float,
    end: float,
    sample_every: float,
) -> dict[str, Any]:
    """Compose the transfer smoke report (raw + smoothed)."""
    report_raw = build_report(rows)
    report_smooth = build_report(final_rows)
    role_rate = report_raw["per_position"] or {}
    observed_total = (
        sum(
            round(b["role_explicit_rate"] * b["samples"])
            for b in role_rate.values()
        )
        if role_rate
        else 0
    )
    pair_total = sum(b["samples"] for b in role_rate.values())
    smoothed_explicit = sum(
        1 for r in final_rows if r["pose_pair"] and r["role"] in ROLE_LABELS
    )
    coverage = round(smoothed_explicit / max(1, pair_total), 4)
    return {
        "protocol": "transfer-smoke: YOLO COCO pose (local) -> ViCoS probe + temporal smoothing",
        "match_id": match_id,
        "range": [start, end],
        "sample_every": sample_every,
        "raw": report_raw,
        "smoothed": report_smooth,
        "role_observed_rate_raw": round(observed_total / max(1, pair_total), 4),
        "role_timeline_coverage": coverage,
        "role_inferred_rate": round(
            max(0, smoothed_explicit - observed_total) / max(1, pair_total), 4
        ),
        "role_switch_events_smoothed": int(
            sum(
                1
                for a, b in zip(final_rows, final_rows[1:])
                if a["role"] in ROLE_LABELS
                and b["role"] in ROLE_LABELS
                and a["role"] != b["role"]
            )
        ),
        "segments": len(segments),
    }


if __name__ == "__main__":
    main()
