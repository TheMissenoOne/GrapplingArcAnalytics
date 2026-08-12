"""Build an ephemeral-frame training manifest from DB decision windows.

Despite the historical filename, this v2 does NOT persist images. It reads the
Analytics DB, finds USER→PARTNER→USER windows, and writes only metadata:
source URL + frame timestamp + hierarchical labels.

Actual pixels are fetched later by ``train.py`` through FFmpeg stdout and kept
only in RAM for the training process.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import csv
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from decision_vision.common import (
    TaxonomyResolver,
    find_analytics_root,
    parse_timestamp,
)
from decision_vision.manifest_quality import (
    manifest_quality,
)
from decision_vision.visual_labels import (
    collapse_visual_node_key,
)

logger = logging.getLogger("decision_vision.extract")

GRAPH_EVENT_TYPES = frozenset(
    {
        "guard",
        "pass",
        "control",
        "takedown",
        "sweep",
        "submission",
        "escape",
        "transition",
    }
)
BOUNDARY_TYPES = frozenset(
    {
        "reset",
        "referee",
        "round_end",
        "round-start",
        "round_start",
        "stoppage",
    }
)


@dataclass(frozen=True)
class Event:
    index: int
    actor: str
    label: str
    event_type: str
    timestamp: float | None
    successful: bool | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class CriterionWindow:
    source: Event
    criterion: Event
    response: Event
    bundle_index: int
    bundle_size: int


def _safe_slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return value.strip("-")[:80] or "unknown"


def _normalize_actor(
    raw_event: dict[str, Any],
    *,
    focus_athlete_id: str,
    athlete_a_id: str,
    athlete_b_id: str,
    athlete_a_name: str = "",
    athlete_b_name: str = "",
) -> str:
    actor = raw_event.get("actor_id", raw_event.get("actor"))
    if actor is None:
        return "neutral"

    actor_raw = str(actor).strip()
    if actor_raw in {"a", "A"}:
        actor_id = athlete_a_id
    elif actor_raw in {"b", "B"}:
        actor_id = athlete_b_id
    else:
        actor_id = actor_raw

    if actor_id == focus_athlete_id:
        return "you"
    if actor_id in {athlete_a_id, athlete_b_id}:
        return "partner"

    actor_name = actor_raw.casefold()
    a_name = athlete_a_name.strip().casefold()
    b_name = athlete_b_name.strip().casefold()

    if a_name and actor_name == a_name:
        return "you" if focus_athlete_id == athlete_a_id else "partner"
    if b_name and actor_name == b_name:
        return "you" if focus_athlete_id == athlete_b_id else "partner"

    return "neutral"


def _normalise_events(
    raw_events: list[dict[str, Any]],
    *,
    focus_athlete_id: str,
    athlete_a_id: str,
    athlete_b_id: str,
    athlete_a_name: str = "",
    athlete_b_name: str = "",
) -> list[Event]:
    out: list[Event] = []

    for index, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            continue

        label = str(raw.get("label") or raw.get("name") or "").strip()
        event_type = str(raw.get("type") or "").strip().lower()
        actor = _normalize_actor(
            raw,
            focus_athlete_id=focus_athlete_id,
            athlete_a_id=athlete_a_id,
            athlete_b_id=athlete_b_id,
            athlete_a_name=athlete_a_name,
            athlete_b_name=athlete_b_name,
        )
        if event_type in BOUNDARY_TYPES:
            actor = "neutral"

        timestamp = parse_timestamp(
            raw.get(
                "ts",
                raw.get("timestamp_seconds", raw.get("timestamp")),
            )
        )

        successful = raw.get("successful")
        if successful is not None:
            successful = bool(successful)

        out.append(
            Event(
                index=index,
                actor=actor,
                label=label,
                event_type=event_type,
                timestamp=timestamp,
                successful=successful,
                raw=raw,
            )
        )

    return out


def decision_windows(events: list[Event]) -> list[CriterionWindow]:
    windows: list[CriterionWindow] = []
    previous_own: Event | None = None
    pending_partner: list[Event] = []

    for event in events:
        if event.actor == "neutral":
            previous_own = None
            pending_partner = []
            continue

        if event.actor == "partner":
            if previous_own is not None:
                pending_partner.append(event)
            continue

        if previous_own is not None and pending_partner:
            usable = [
                partner
                for partner in pending_partner
                if partner.timestamp is not None
                and partner.label
                and partner.event_type in GRAPH_EVENT_TYPES
            ]

            for bundle_index, criterion in enumerate(usable):
                windows.append(
                    CriterionWindow(
                        source=previous_own,
                        criterion=criterion,
                        response=event,
                        bundle_index=bundle_index,
                        bundle_size=len(usable),
                    )
                )

        previous_own = event
        pending_partner = []

    return windows


def _sample_offsets(
    frames_per_event: int,
    window_seconds: float,
) -> list[float]:
    if frames_per_event <= 1:
        return [0.0]

    return np.linspace(
        -window_seconds / 2.0,
        window_seconds / 2.0,
        frames_per_event,
    ).tolist()


def _load_match_and_nodes(
    match_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from sqlalchemy import select

    from analysis.names import _normalize_name
    from db.base import db_session
    from db.models import Athlete, Match, TechniqueNode

    with db_session() as session:
        match = session.get(Match, match_id)
        if match is None:
            raise ValueError(f"Match not found: {match_id}")

        athlete_a = session.get(Athlete, match.athlete_a_id)
        athlete_b = session.get(Athlete, match.athlete_b_id)

        match_data = {
            "id": str(match.id),
            "athlete_a_id": str(match.athlete_a_id),
            "athlete_b_id": str(match.athlete_b_id),
            "athlete_a_name": str(athlete_a.name) if athlete_a else "",
            "athlete_b_name": str(athlete_b.name) if athlete_b else "",
            "video_url": str(match.video_url or ""),
            "timeline": list(match.timeline or []),
            "sequence": list(match.sequence or []),
            "status": match.status,
        }

        nodes = session.execute(select(TechniqueNode)).scalars().all()
        node_by_key = {
            str(node.node_key): {
                "node_key": str(node.node_key),
                "label": str(node.label),
                "type": str(node.type or node.node_type or ""),
                "taxonomy_id": (
                    str(node.taxonomy_id)
                    if node.taxonomy_id
                    else None
                ),
            }
            for node in nodes
        }

    for node in list(node_by_key.values()):
        node_by_key.setdefault(_normalize_name(node["label"]), node)

    return match_data, node_by_key


def build_rows(
    *,
    match_id: str,
    focus_athlete_id: str,
    analytics_root: Path,
    frames_per_event: int,
    window_seconds: float,
    source_url_override: str | None = None,
) -> list[dict[str, Any]]:
    from analysis.names import _normalize_name

    match, node_by_key = _load_match_and_nodes(match_id)

    participants = {
        match["athlete_a_id"],
        match["athlete_b_id"],
    }
    if focus_athlete_id not in participants:
        raise ValueError(
            f"focus athlete {focus_athlete_id} is not a participant "
            f"in match {match_id}"
        )

    source_url = (
        str(source_url_override).strip()
        if source_url_override
        else match["video_url"].strip()
    )
    if not source_url:
        raise ValueError(
            f"Match {match_id} has no video_url and no --source-url override"
        )

    partner_athlete_id = (
        match["athlete_b_id"]
        if focus_athlete_id == match["athlete_a_id"]
        else match["athlete_a_id"]
    )

    raw_events = match["timeline"] or match["sequence"]
    if not raw_events:
        logger.warning("Match %s has no timeline/sequence", match_id)
        return []

    events = _normalise_events(
        raw_events,
        focus_athlete_id=focus_athlete_id,
        athlete_a_id=match["athlete_a_id"],
        athlete_b_id=match["athlete_b_id"],
        athlete_a_name=match["athlete_a_name"],
        athlete_b_name=match["athlete_b_name"],
    )
    windows = decision_windows(events)

    if not windows:
        logger.warning(
            "Match %s produced no USER→PARTNER→USER windows",
            match_id,
        )
        return []

    taxonomy = TaxonomyResolver(
        analytics_root / "docs" / "taxonomy.json"
    )
    offsets = _sample_offsets(
        frames_per_event,
        window_seconds,
    )

    rows: list[dict[str, Any]] = []

    for window_number, window in enumerate(windows):
        criterion = window.criterion
        assert criterion.timestamp is not None

        criterion_key = _normalize_name(criterion.label)
        node = node_by_key.get(criterion_key) or {
            "node_key": criterion_key,
            "label": criterion.label,
            "type": criterion.event_type,
            "taxonomy_id": None,
        }

        hierarchy = taxonomy.labels_for(
            node_key=str(node["node_key"]),
            display_label=str(node["label"]),
            event_type=(
                criterion.event_type
                or str(node["type"])
            ),
            taxonomy_id=node.get("taxonomy_id"),
        )

        visual_key, visual_label = collapse_visual_node_key(
            str(node["node_key"]),
            criterion.label,
            node_by_key,
            _normalize_name,
        )
        visual_node = node_by_key.get(visual_key) or {
            "node_key": visual_key,
            "label": visual_label,
            "type": criterion.event_type,
            "taxonomy_id": None,
        }
        visual_hierarchy = taxonomy.labels_for(
            node_key=str(visual_node["node_key"]),
            display_label=str(visual_node["label"]),
            event_type=(
                criterion.event_type
                or str(visual_node["type"])
            ),
            taxonomy_id=visual_node.get("taxonomy_id"),
        )

        source_key = _normalize_name(window.source.label)
        response_key = _normalize_name(window.response.label)
        bundle_id = (
            f"{match_id}:"
            f"{window.source.index}:"
            f"{window.response.index}"
        )

        for offset_index, offset in enumerate(offsets):
            frame_ts = max(
                0.0,
                criterion.timestamp + float(offset),
            )
            sample_id = (
                f"{_safe_slug(match_id)}"
                f"_e{criterion.index:04d}"
                f"_w{window_number:04d}"
                f"_f{offset_index:02d}"
            )

            rows.append(
                {
                    "sample_id": sample_id,
                    "match_id": match_id,
                    "source_url": source_url,
                    "focus_athlete_id": focus_athlete_id,
                    "partner_athlete_id": partner_athlete_id,
                    "bundle_id": bundle_id,
                    "bundle_index": window.bundle_index,
                    "bundle_size": window.bundle_size,
                    "source_event_index": window.source.index,
                    "source_label": window.source.label,
                    "source_key": source_key,
                    "criterion_event_index": criterion.index,
                    "criterion_label": criterion.label,
                    "criterion_type": criterion.event_type,
                    "criterion_ts": round(
                        criterion.timestamp,
                        4,
                    ),
                    "frame_ts": round(frame_ts, 4),
                    "frame_offset": round(
                        float(offset),
                        4,
                    ),
                    "response_event_index": window.response.index,
                    "response_label": window.response.label,
                    "response_key": response_key,
                    "leaf_label": hierarchy.leaf_label,
                    "family_label": hierarchy.family_label,
                    "category_label": hierarchy.category_label,
                    "visual_leaf_label": (
                        visual_hierarchy.leaf_label
                    ),
                    "visual_family_label": (
                        visual_hierarchy.family_label
                    ),
                    "visual_category_label": (
                        visual_hierarchy.category_label
                    ),
                    "visual_label_collapsed": (
                        visual_label
                        != str(criterion.label).strip()
                    ),
                    "taxonomy_path": json.dumps(
                        hierarchy.taxonomy_path,
                        ensure_ascii=False,
                    ),
                }
            )

    logger.info(
        "match=%s windows=%d planned_frames=%d",
        match_id,
        len(windows),
        len(rows),
    )
    return rows


def _load_match_map(
    path: Path,
) -> list[dict[str, str]]:
    with path.open(
        newline="",
        encoding="utf-8",
    ) as handle:
        rows = list(csv.DictReader(handle))

    required = {
        "match_id",
        "focus_athlete_id",
    }
    if not rows:
        raise ValueError(f"Match map is empty: {path}")

    missing = required - set(rows[0])
    if missing:
        raise ValueError(
            f"Match map must contain {sorted(required)}; "
            f"missing {sorted(missing)}"
        )
    return rows


def _write_manifest(
    output: Path,
    rows: list[dict[str, Any]],
    *,
    append: bool,
) -> Path:
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing: list[dict[str, Any]] = []
    if append and output.exists():
        with output.open(
            newline="",
            encoding="utf-8",
        ) as handle:
            existing = list(csv.DictReader(handle))

    combined = [*existing, *rows]
    if not combined:
        raise RuntimeError(
            "No frame plans were generated; manifest would be empty."
        )

    fieldnames = list(combined[0].keys())
    with output.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(combined)

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a no-image manifest for DB-labelled "
            "USER→PARTNER→USER criterion frames."
        )
    )
    source = parser.add_mutually_exclusive_group(
        required=True
    )
    source.add_argument("--match-id")
    source.add_argument(
        "--match-map",
        type=Path,
        help=(
            "CSV columns: match_id,focus_athlete_id "
            "[,source_url]"
        ),
    )

    parser.add_argument("--focus-athlete-id")
    parser.add_argument(
        "--source-url",
        help="Override DB Match.video_url for a single match.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/cv_decision_poc/manifest.csv"
        ),
    )
    parser.add_argument("--analytics-root", type=Path)
    parser.add_argument(
        "--frames-per-event",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=1.2,
    )
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(
            logging,
            args.log_level.upper(),
        ),
        format="%(levelname)s %(message)s",
    )
    load_dotenv()

    analytics_root = find_analytics_root(
        args.analytics_root
    )

    if args.match_map:
        jobs = _load_match_map(args.match_map)
    else:
        if not args.focus_athlete_id:
            raise SystemExit(
                "--match-id requires --focus-athlete-id"
            )
        jobs = [
            {
                "match_id": str(args.match_id),
                "focus_athlete_id": str(
                    args.focus_athlete_id
                ),
                "source_url": str(
                    args.source_url or ""
                ),
            }
        ]

    rows: list[dict[str, Any]] = []
    for job in jobs:
        rows.extend(
            build_rows(
                match_id=job["match_id"],
                focus_athlete_id=job[
                    "focus_athlete_id"
                ],
                analytics_root=analytics_root,
                frames_per_event=max(
                    1,
                    args.frames_per_event,
                ),
                window_seconds=max(
                    0.0,
                    args.window_seconds,
                ),
                source_url_override=(
                    job.get("source_url")
                    or None
                ),
            )
        )

    manifest = _write_manifest(
        args.output.resolve(),
        rows,
        append=args.append,
    )
    quality = manifest_quality(
        pd.read_csv(manifest)
    )
    quality_path = (
        manifest.parent
        / "manifest_quality.json"
    )
    quality_path.write_text(
        json.dumps(
            quality,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Manifest quality -> %s",
        quality_path,
    )
    logger.info(
        "Wrote %d frame plans -> %s",
        len(rows),
        manifest,
    )
    logger.info(
        "No image/video bytes were persisted."
    )


if __name__ == "__main__":
    main()
