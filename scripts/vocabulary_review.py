#!/usr/bin/env python
"""Review queue for user-invented technique labels — Onda A1.

A user's ``graph_nodes`` row (``owner_kind='user'``, ``canonical_node_key IS NULL``) is a
label the app couldn't match to the curated vocabulary. This surfaces those labels for a
human to either promote into the curated library or dismiss as noise.

    uv run python scripts/vocabulary_review.py                          # list (default)
    uv run python scripts/vocabulary_review.py list
    uv run python scripts/vocabulary_review.py approve NODE_KEY --en "Foot Lock" \\
        --pt "Chave de Pé" --type submission --variants "footlock,chave no pe"
    uv run python scripts/vocabulary_review.py dismiss NODE_KEY --reason "typo, one-off"

Against prod (read-only ``list``; ``approve``/``dismiss`` never touch the DB — see below):
    set -a; source .env; set +a
    uv run python scripts/vocabulary_review.py list

Privacy (root CLAUDE.md "Public vs Private Data"): ``graph_nodes`` under a ``owner_kind=
'user'`` graph is PRIVATE — whatever the athlete/user actually typed. ``list`` prints ONLY
``label``/``type``/``node_type``/``node_key``/count, never ``owner_id``/``graph_id`` or any
other session value that could re-identify who typed it.

TRAP: the ``technique_nodes`` row for a canonical key is born by
``scripts/seed_technique_nodes.py``, whose upsert always sets ``source='library'``. NEVER
hand-flip a ``technique_nodes`` row's ``source`` between ``'user'``/``'library'`` outside
that script — ``export/tech_library.py:249-270`` (``_load_match_techniques``) reads
``TechniqueNode WHERE source='user'`` and merges those rows straight into the PUBLIC
technique library export, so ``source`` is provenance the export depends on, not a free
label. ``approve`` below only appends to ``analysis/data/technique_library.json`` (via
``scripts.extend_technique_library.append_entries``); it never writes to the database. Run
``scripts/seed_technique_nodes.py`` afterwards to project the new entry into
``technique_nodes`` (which lands it correctly, with ``source='library'``) and re-link
``graph_nodes.canonical_node_key`` for the graphs that used it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.extend_technique_library import (  # noqa: E402
    LIB,
    ResolutionConflictError,
    _resolution_index,
    append_entries,
)

DISMISS_PATH = Path(__file__).resolve().parents[1] / "analysis/data/vocabulary_review.json"


class PendingRow(TypedDict):
    label: str
    type: str
    node_type: str
    node_key: str
    count: int


def load_dismissals() -> dict[str, dict[str, str]]:
    if not DISMISS_PATH.exists():
        return {}
    data: dict[str, Any] = json.loads(DISMISS_PATH.read_text(encoding="utf-8"))
    dismissed: dict[str, dict[str, str]] = data.get("dismissed", {})
    return dismissed


def pending_labels(session: Any) -> list[PendingRow]:
    """User-invented, not-yet-linked technique labels, most-used first.

    ``session`` is a SQLAlchemy ``Session`` (typed ``Any`` to keep this importable without
    the optional ``postgres`` extra when only ``approve``/``dismiss`` run).
    """
    from sqlalchemy import func, select

    from db.models import Graph, GraphNode

    rows = session.execute(
        select(
            GraphNode.label,
            GraphNode.type,
            GraphNode.node_type,
            GraphNode.node_key,
            func.count(func.distinct(GraphNode.graph_id)),
        )
        .join(Graph, Graph.id == GraphNode.graph_id)
        .where(Graph.owner_kind == "user", GraphNode.canonical_node_key.is_(None))
        .group_by(GraphNode.label, GraphNode.type, GraphNode.node_type, GraphNode.node_key)
        .order_by(func.count(func.distinct(GraphNode.graph_id)).desc())
    ).all()
    return [
        {"label": label, "type": typ or "", "node_type": node_type or "",
         "node_key": node_key, "count": int(n)}
        for label, typ, node_type, node_key, n in rows
    ]


def cmd_list(session: Any) -> None:
    dismissed = load_dismissals()
    curated = _resolution_index(json.loads(LIB.read_text(encoding="utf-8")))

    print(f"{'label':32} {'type':11} {'node_type':16} {'node_key':30} count")
    shown = 0
    for row in pending_labels(session):
        if row["node_key"] in dismissed:
            continue
        note = "  [já curado, aguardando seed]" if row["node_key"] in curated else ""
        print(f"{row['label']:32.32} {row['type']:11.11} {row['node_type']:16.16} "
              f"{row['node_key']:30.30} {row['count']:5d}{note}")
        shown += 1
    if shown == 0:
        print("(nothing pending)")


def cmd_approve(args: argparse.Namespace) -> int:
    variants = [v.strip() for v in (args.variants or "").split(",") if v.strip()]
    try:
        added = append_entries([(args.en, args.pt, args.type, variants)])
    except ResolutionConflictError as exc:
        print(f"REFUSED: {exc}")
        return 1
    if not added:
        print(f"'{args.en}' already in the library — nothing appended")
        return 0
    print(f"approved {args.node_key!r} -> appended '{args.en}' ({args.type}) to {LIB}")
    print("next: uv run python scripts/seed_technique_nodes.py  # project into technique_nodes")
    return 0


def cmd_dismiss(args: argparse.Namespace) -> int:
    dismissed = load_dismissals()
    dismissed[args.node_key] = {
        "reason": args.reason or "",
        "date": datetime.now(UTC).date().isoformat(),
    }
    DISMISS_PATH.write_text(
        json.dumps({"dismissed": dismissed}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"dismissed {args.node_key!r}" + (f": {args.reason}" if args.reason else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="vocabulary_review")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list", help="pending user-invented labels (default)")

    ap_approve = sub.add_parser("approve", help="promote a node_key into the curated library")
    ap_approve.add_argument("node_key")
    ap_approve.add_argument("--en", required=True)
    ap_approve.add_argument("--pt", required=True)
    ap_approve.add_argument("--type", required=True)
    ap_approve.add_argument("--variants", default="", help="comma-separated aliases")

    ap_dismiss = sub.add_parser("dismiss", help="mark a node_key reviewed, not promoted")
    ap_dismiss.add_argument("node_key")
    ap_dismiss.add_argument("--reason", default="")

    args = ap.parse_args()
    cmd = args.cmd or "list"

    if cmd == "list":
        from db.base import db_session

        with db_session() as session:
            cmd_list(session)
        return 0
    if cmd == "approve":
        return cmd_approve(args)
    return cmd_dismiss(args)


if __name__ == "__main__":
    raise SystemExit(main())
