#!/usr/bin/env python
"""Additive enrichment of EXISTING prod matches with concordance-audited frame-read events.

Step 7 of ``docs/gemini_concordance_audit.md``. Reads ``<slug>.audited.json`` files (one per
bout, produced by the audit pipeline) and a crossref file mapping each slug to a match id
prefix + athlete-name translations, resolves each kept event's actor to the match's two
athlete UUIDs, skips anything that already exists in the DB (same technique + same actor +
close timestamp), and APPENDS the rest to ``matches.sequence``. Never removes, edits, or
reorders an existing event; never overwrites a non-null column (scar: ``_resolve_video`` /
AA-011, see ``scripts/dump_import.py``).

Input shapes
------------
``--audited <dir>``: one ``<slug>.audited.json`` per file::

    {"slug": "...", "kept_events": [{"ts": 125, "label": "Armbar", "actor": "Gordon Ryan",
                                      "successful": true, "type": "submission"}, ...]}

``--crossref <path>``::

    {"enrich_targets": {"<slug>": "<match-id-8-hex-prefix>"},
     "db_name_map": {"<audited actor name>": "<db athlete name>"},
     "bout_start": {"<slug>": 930}}

``db_name_map`` and ``bout_start`` are both optional.

Duplicate rule
--------------
An audited event is skipped (not inserted) when an existing sequence event has the same
``actor_id`` AND the same canonicalized technique key (the shared
``clean_label`` -> ``_normalize_name`` -> ``canonicalize`` chain every graph/map consumer
uses, see ``analysis.technique_match``/``analysis.names``) AND either the existing event has
no ``ts``, or both have a ``ts`` within 30s of each other. This makes a re-run idempotent:
after ``--write``, running again finds 0 inserts.

    uv run python -m scripts.enrich_from_audit --audited data/audit --crossref data/audit/crossref.json
    uv run python -m scripts.enrich_from_audit --audited data/audit --crossref data/audit/crossref.json --write   # ORCHESTRATOR ONLY
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analysis.names import _normalize_name, athlete_key, canonicalize
from analysis.technique_match import clean_label

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from db.models import Match

logger = logging.getLogger(__name__)

# Duplicate window (see module docstring's "Duplicate rule").
DUP_TS_WINDOW_S = 30


def node_key(label: str, type_hint: str) -> str:
    """Canonicalized technique key — the exact chain the graph/map/ocean consumers share."""
    return canonicalize(_normalize_name(clean_label(label, type_hint)))


def resolve_actor(
    actor_name: str,
    db_name_map: dict[str, str],
    name_a: str,
    id_a: str,
    name_b: str,
    id_b: str,
) -> str | None:
    """Translate an audited event's ``actor`` name to one of the match's two athlete ids.

    ``db_name_map`` is applied first (audited name -> db name), then compared via
    ``athlete_key`` against both athlete names. Neither match -> None (caller must skip,
    never guess)."""
    mapped = db_name_map.get(actor_name, actor_name)
    key = athlete_key(mapped)
    if key == athlete_key(name_a):
        return id_a
    if key == athlete_key(name_b):
        return id_b
    return None


def _event_key_and_ts(ev: dict[str, Any]) -> tuple[str, int | None]:
    key = node_key(str(ev.get("label", "")), str(ev.get("type", "")))
    ts = ev.get("ts")
    return key, (ts if isinstance(ts, int) else None)


def is_duplicate(
    existing: list[dict[str, Any]], actor_id: str, key: str, ts: int | None
) -> bool:
    """True when ``existing`` already carries this (actor, technique) per the duplicate rule."""
    for e in existing:
        if not isinstance(e, dict) or e.get("actor_id") != actor_id:
            continue
        if _event_key_and_ts(e)[0] != key:
            continue
        e_ts = e.get("ts")
        if not isinstance(e_ts, int):
            return True  # existing event carries no ts -> can't distinguish, treat as dup
        if isinstance(ts, int) and abs(e_ts - ts) <= DUP_TS_WINDOW_S:
            return True
    return False


def build_event(audited_event: dict[str, Any], actor_id: str) -> dict[str, Any]:
    """New sequence event in the DB's existing shape — never invents fields beyond
    ``label``/``type``/``actor_id``/``successful``/``ts`` (docs/match_event_model.md)."""
    ev: dict[str, Any] = {
        "label": clean_label(str(audited_event.get("label", "")), str(audited_event.get("type", ""))),
        "type": str(audited_event.get("type", "")),
        "actor_id": actor_id,
    }
    if audited_event.get("successful") is not None:
        ev["successful"] = bool(audited_event["successful"])
    ts = audited_event.get("ts")
    if isinstance(ts, int):
        ev["ts"] = ts
    return ev


def merge_sequence(
    existing: list[dict[str, Any]], new_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append ``new_events`` and re-sort by ``ts`` — but only if the existing sequence
    already carries a ``ts`` convention; otherwise leave append order untouched (some bouts'
    events have no ts at all, and there is nothing sane to sort them by).

    Events without ``ts`` are forward-filled to the last known ts before them rather than
    sorted to the front/back, so they stay anchored near their original neighbours."""
    combined = list(existing) + list(new_events)
    if not any(isinstance(e, dict) and isinstance(e.get("ts"), int) for e in existing):
        return combined
    last_ts = -1
    keys: list[tuple[int, int]] = []
    for i, e in enumerate(combined):
        ts = e.get("ts") if isinstance(e, dict) else None
        if isinstance(ts, int):
            last_ts = ts
        keys.append((last_ts, i))
    order = sorted(range(len(combined)), key=lambda i: keys[i])
    return [combined[i] for i in order]


def plan_video_fields(
    cur_video_start_seconds: int | None, cur_ts_origin: str | None, bout_start: int | None
) -> tuple[int | None, str | None]:
    """New ``(video_start_seconds, ts_origin)`` or ``(None, None)`` if no change.

    Follows ``_resolve_video``'s precedence philosophy (scripts/dump_import.py): never
    overwrite a non-null column. Only fires when BOTH are currently null (a value in one but
    not the other is an inconsistent state this script won't try to repair) and a
    ``bout_start`` was supplied for the slug."""
    if cur_video_start_seconds is None and cur_ts_origin is None and bout_start is not None:
        return bout_start, "video_absolute"
    return None, None


@dataclass
class MatchPlan:
    match_id: str
    name_a: str
    name_b: str
    existing_count: int
    insert_events: list[dict[str, Any]] = field(default_factory=list)
    dup_count: int = 0
    unresolved: list[str] = field(default_factory=list)
    new_sequence: list[dict[str, Any]] = field(default_factory=list)
    new_video_start_seconds: int | None = None
    new_ts_origin: str | None = None


def plan_match_enrichment(
    *,
    match_id: str,
    name_a: str,
    id_a: str,
    name_b: str,
    id_b: str,
    existing_sequence: list[dict[str, Any]] | None,
    kept_events: list[dict[str, Any]],
    db_name_map: dict[str, str],
    cur_video_start_seconds: int | None,
    cur_ts_origin: str | None,
    bout_start: int | None,
) -> MatchPlan:
    """Pure planning step for one match: resolve actors, dedupe, build the merged sequence.
    Does not touch the DB — callers apply ``insert_events``/``new_sequence`` under ``--write``."""
    existing = list(existing_sequence or [])
    plan = MatchPlan(match_id=match_id, name_a=name_a, name_b=name_b, existing_count=len(existing))
    staged: list[dict[str, Any]] = []
    for audited_event in kept_events:
        actor_name = str(audited_event.get("actor", ""))
        actor_id = resolve_actor(actor_name, db_name_map, name_a, id_a, name_b, id_b)
        if actor_id is None:
            plan.unresolved.append(actor_name)
            continue
        key, ts = _event_key_and_ts(audited_event)
        if is_duplicate(existing + staged, actor_id, key, ts):
            plan.dup_count += 1
            continue
        new_event = build_event(audited_event, actor_id)
        staged.append(new_event)
        plan.insert_events.append(new_event)

    plan.new_sequence = merge_sequence(existing, staged)
    plan.new_video_start_seconds, plan.new_ts_origin = plan_video_fields(
        cur_video_start_seconds, cur_ts_origin, bout_start
    )
    return plan


# ── file loading + DB glue ───────────────────────────────────────────────────────────────


def load_audited(audited_dir: Path) -> dict[str, dict[str, Any]]:
    """slug -> parsed ``<slug>.audited.json``."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(audited_dir.glob("*.audited.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        slug = str(data.get("slug") or path.stem.removesuffix(".audited"))
        out[slug] = data
    return out


def _find_match_by_prefix(session: Session, prefix: str) -> Match:
    """Match whose id starts with ``prefix``; errors unless exactly one matches.

    ponytail: filters the small `matches` table in Python instead of a dialect-specific
    ``id::text LIKE`` cast (Postgres' native ``uuid`` column needs an explicit cast that
    SQLite doesn't; this works identically on both and matches table size is in the hundreds).
    """
    from sqlalchemy import select

    from db.models import Match

    hits = [
        m for m in session.execute(select(Match)).scalars() if str(m.id).startswith(prefix)
    ]
    if len(hits) != 1:
        raise ValueError(f"match id prefix {prefix!r} matched {len(hits)} rows, expected 1")
    return hits[0]


def run(audited_dir: Path, crossref_path: Path, write: bool) -> int:
    crossref = json.loads(crossref_path.read_text(encoding="utf-8"))
    enrich_targets: dict[str, str] = crossref.get("enrich_targets", {})
    db_name_map: dict[str, str] = crossref.get("db_name_map", {})
    bout_starts: dict[str, int] = crossref.get("bout_start", {})
    audited = load_audited(audited_dir)

    from db.base import db_session
    from db.models import Athlete

    total_insert = total_dup = total_unresolved = 0
    with db_session() as session:
        for slug, prefix in sorted(enrich_targets.items()):
            data = audited.get(slug)
            if data is None:
                logger.warning("%s: no audited file found, skipping", slug)
                continue
            try:
                match = _find_match_by_prefix(session, prefix)
            except ValueError as e:
                logger.error("%s: %s", slug, e)
                continue

            athlete_a = session.get(Athlete, match.athlete_a_id)
            athlete_b = session.get(Athlete, match.athlete_b_id)
            if athlete_a is None or athlete_b is None:
                logger.error("%s: match %s missing an athlete row", slug, match.id)
                continue

            plan = plan_match_enrichment(
                match_id=match.id,
                name_a=athlete_a.name,
                id_a=athlete_a.id,
                name_b=athlete_b.name,
                id_b=athlete_b.id,
                existing_sequence=match.sequence,
                kept_events=data.get("kept_events", []),
                db_name_map=db_name_map,
                cur_video_start_seconds=match.video_start_seconds,
                cur_ts_origin=match.ts_origin,
                bout_start=bout_starts.get(slug),
            )

            total_insert += len(plan.insert_events)
            total_dup += plan.dup_count
            total_unresolved += len(plan.unresolved)

            logger.info(
                "%s: match %s (%s vs %s) — existing=%d insert=%d dup=%d unresolved=%d",
                slug, match.id, athlete_a.name, athlete_b.name,
                plan.existing_count, len(plan.insert_events), plan.dup_count,
                len(plan.unresolved),
            )
            for ev in plan.insert_events:
                logger.info("    + %s", ev)
            for name in plan.unresolved:
                logger.warning("    ? unresolved actor %r", name)
            if plan.new_video_start_seconds is not None:
                logger.info(
                    "    video_start_seconds -> %d, ts_origin -> %s",
                    plan.new_video_start_seconds, plan.new_ts_origin,
                )

            if write and (plan.insert_events or plan.new_video_start_seconds is not None):
                from sqlalchemy.orm.attributes import flag_modified

                if plan.insert_events:
                    match.sequence = plan.new_sequence
                    flag_modified(match, "sequence")
                if plan.new_video_start_seconds is not None:
                    match.video_start_seconds = plan.new_video_start_seconds
                    match.ts_origin = plan.new_ts_origin
            # write=False: objects above are only ever read, never mutated, so
            # db_session's closing commit() is a no-op — nothing staged, nothing applied.

    logger.info(
        "%s: %d event(s) %s, %d duplicate(s) skipped, %d unresolved actor(s)",
        "DRY-RUN" if not write else "DONE",
        total_insert, "would be inserted" if not write else "inserted",
        total_dup, total_unresolved,
    )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(
        description="Enrich existing prod matches with concordance-audited events"
    )
    ap.add_argument("--audited", required=True, type=Path, help="dir of <slug>.audited.json")
    ap.add_argument("--crossref", required=True, type=Path, help="crossref JSON path")
    ap.add_argument("--write", action="store_true", help="apply the updates (default: dry-run)")
    args = ap.parse_args()
    return run(args.audited, args.crossref, write=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
