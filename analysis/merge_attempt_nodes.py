"""One-time merge: fold ``technique_nodes`` rows whose label reads as an "attempt"
variant into their canonical library node, remap incident ``graph_edges``/``map_edges``,
delete the attempt node, and replay affected athlete graphs so exports read clean.

Card kanban/TODO/012 · doc ``docs/deepseek/I-directed-graphs-and-review-mode.md`` §6.

The canonicalizer is ``clean_label`` (``analysis/technique_match.py``) — it only resolves
the handful of techniques whose library ``variants`` list an explicit "<x> attempt" alias
(``analysis/data/technique_library.json``). An attempt node whose label doesn't hit one of
those variants is left alone and reported as needing a library synonym; this script never
guesses a canonical spelling.

    uv run python -m analysis.merge_attempt_nodes              # dry-run (default), no writes
    uv run python -m analysis.merge_attempt_nodes --apply       # write
    uv run python -m analysis.merge_attempt_nodes --check       # 0 = none remain, no writes

ponytail: doesn't touch ``Match.sequence`` (the raw event labels that feed replay) — if a
resolved attempt's matches still carry the unclean label, replaying them re-derives it from
``Match.sequence`` and can recreate the node. Idempotent by design: just rerun the script
(or run ``scripts/clean_match_techniques.py`` first for a permanent fix upstream).
"""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from analysis.names import _normalize_name
from analysis.technique_match import _index, clean_label
from db.models import Athlete, Graph, GraphEdge, MapEdge, TechniqueNode
from db.repository import _register_techniques, replay_and_persist_athlete

logger = logging.getLogger(__name__)

ATTEMPT_RE = re.compile(r"\battempt(s|ed|ing)?\b", re.IGNORECASE)


@dataclass
class MergePlan:
    attempt_key: str
    attempt_label: str
    canonical_key: str
    canonical_label: str
    resolved: bool  # False: clean_label left the key unchanged — needs a library synonym


@dataclass
class MergeReport:
    plans: list[MergePlan] = field(default_factory=list)
    graph_edges_touched: int = 0
    graph_edges_deleted: int = 0
    map_edges_touched: int = 0
    map_edges_deleted: int = 0
    athlete_graphs_replayed: int = 0

    @property
    def resolved(self) -> list[MergePlan]:
        return [p for p in self.plans if p.resolved]

    @property
    def unresolved(self) -> list[MergePlan]:
        return [p for p in self.plans if not p.resolved]


def find_attempt_nodes(session: Session) -> list[TechniqueNode]:
    """Every technique_node whose label matches the attempt regex. Filtered in Python —
    the ~62-row prod set makes a DB-side regex needless, and it keeps this portable to
    the SQLite test fixture (no ``~*`` operator there)."""
    return [
        n for n in session.execute(select(TechniqueNode)).scalars()
        if ATTEMPT_RE.search(n.label or "")
    ]


def plan_merges(nodes: list[TechniqueNode]) -> list[MergePlan]:
    """``clean_label`` strips the "attempt" synonym via the library's variant list. If
    the resulting key is unchanged, clean_label didn't recognise this label — the caller
    skips it (never merge a node into itself) and it's reported as needing a synonym."""
    plans = []
    for n in nodes:
        canonical_label = clean_label(n.label)
        canonical_key = _normalize_name(canonical_label)
        plans.append(
            MergePlan(
                attempt_key=n.node_key,
                attempt_label=n.label,
                canonical_key=canonical_key,
                canonical_label=canonical_label,
                resolved=canonical_key != n.node_key,
            )
        )
    return plans


def _ensure_canonical_nodes(plans: list[MergePlan], session: Session) -> None:
    """Insert-if-absent every resolved merge's canonical node (``source='library'``).
    Reuses ``db.repository._register_techniques`` — the same insert-on-conflict-do-nothing
    shape already used to register library techniques elsewhere."""
    idx = _index()
    techs: dict[str, dict[str, str]] = {}
    for p in plans:
        if not p.resolved or p.canonical_key in techs:
            continue
        _, node_type = idx.get(p.canonical_key, ("", ""))
        techs[p.canonical_key] = {
            "node_key": p.canonical_key,
            "label": p.canonical_label,
            "type": "technique",
            "node_type": node_type,
            "source": "library",
        }
    _register_techniques(techs, session)


def _remap_graph_edges(
    mapping: dict[str, str], session: Session, *, apply: bool
) -> tuple[int, int, set[str]]:
    """Repoint ``graph_edges`` through ``mapping`` (attempt_key -> canonical_key), one
    graph at a time so a same-graph collision (two edges landing on the same new
    ``edge_key``) can be deduped by keeping the higher-elo row instead of violating the
    ``(graph_id, edge_key)`` unique constraint. Returns
    ``(edges_touched, edges_deleted, touched_graph_ids)``."""
    if not mapping:
        return 0, 0, set()
    keys = list(mapping)
    graph_ids = {
        gid for (gid,) in session.execute(
            select(GraphEdge.graph_id)
            .where(or_(GraphEdge.source_key.in_(keys), GraphEdge.target_key.in_(keys)))
            .distinct()
        )
    }
    touched = 0
    deleted = 0
    for gid in graph_ids:
        rows = list(session.execute(select(GraphEdge).where(GraphEdge.graph_id == gid)).scalars())
        groups: dict[str, list[GraphEdge]] = {}
        for e in rows:
            new_src = mapping.get(e.source_key, e.source_key)
            new_tgt = mapping.get(e.target_key, e.target_key)
            groups.setdefault(f"{new_src}→{new_tgt}", []).append(e)
        winners: list[tuple[GraphEdge, str]] = []
        for new_key, grp in groups.items():
            if len(grp) == 1 and grp[0].edge_key == new_key:
                continue  # untouched by this merge
            touched += 1
            deleted += len(grp) - 1
            winner = max(grp, key=lambda e: e.elo)
            winners.append((winner, new_key))
            if apply:
                for loser in grp:
                    if loser is not winner:
                        session.delete(loser)
        if apply and winners:
            # Ordering matters against the (graph_id, edge_key) unique constraint: delete the
            # losers first, then rename winners through a temp key, then to the final key — so a
            # winner never claims a key still held by a not-yet-deleted loser or an un-renamed
            # winner (key-swap). Explicit flushes pin the order; autoflush would break it.
            session.flush()
            for winner, _nk in winners:
                winner.edge_key = f"tmp:{winner.id}"  # unique, no NUL, no "→" → can't collide
            session.flush()
            for winner, new_key in winners:
                new_src, new_tgt = new_key.split("→", 1)
                winner.edge_key, winner.source_key, winner.target_key = new_key, new_src, new_tgt
            session.flush()
    return touched, deleted, graph_ids


def _remap_map_edges(mapping: dict[str, str], session: Session, *, apply: bool) -> tuple[int, int]:
    """Repoint ``map_edges`` (global aggregate, unique on ``(source_key, target_key)``)
    through ``mapping``. Collisions merge by summing ``count`` (no elo field here)."""
    if not mapping:
        return 0, 0
    keys = list(mapping)
    hit = session.execute(
        select(MapEdge.id).where(or_(MapEdge.source_key.in_(keys), MapEdge.target_key.in_(keys)))
    ).first()
    if hit is None:
        return 0, 0
    rows = list(session.execute(select(MapEdge)).scalars())
    groups: dict[tuple[str, str], list[MapEdge]] = {}
    for e in rows:
        new_src = mapping.get(e.source_key, e.source_key)
        new_tgt = mapping.get(e.target_key, e.target_key)
        groups.setdefault((new_src, new_tgt), []).append(e)
    touched = 0
    deleted = 0
    winners: list[tuple[MapEdge, str, str]] = []
    for (new_src, new_tgt), grp in groups.items():
        if len(grp) == 1 and grp[0].source_key == new_src and grp[0].target_key == new_tgt:
            continue
        touched += 1
        deleted += len(grp) - 1
        winner = max(grp, key=lambda e: e.count)
        if apply:
            winner.count = sum(e.count for e in grp)
            winner.suggested = any(e.suggested for e in grp)
            winners.append((winner, new_src, new_tgt))
            for loser in grp:
                if loser is not winner:
                    session.delete(loser)
    if apply and winners:
        # Same collision-safe ordering as graph_edges (unique on (source_key, target_key)):
        # delete losers, temp-rename winners, then final-rename — flushes pin the order.
        session.flush()
        for winner, _s, _t in winners:
            winner.source_key = winner.target_key = f"tmp:{winner.id}"  # unique, no NUL
        session.flush()
        for winner, new_src, new_tgt in winners:
            winner.source_key, winner.target_key = new_src, new_tgt
        session.flush()
    return touched, deleted


def _replay_affected(graph_ids: set[str], session: Session, *, apply: bool) -> int:
    """Athlete graphs among the touched set get replayed from Match history so the remap
    is re-derived end to end. User graphs (``owner_kind='user'``) have no server-side
    match log to replay from — the direct edge remap above already fixes those."""
    if not graph_ids:
        return 0
    graphs = list(session.execute(select(Graph).where(Graph.id.in_(graph_ids))).scalars())
    athlete_ids = [g.owner_id for g in graphs if g.owner_kind == "athlete"]
    if not apply:
        return len(athlete_ids)
    n = 0
    for aid in athlete_ids:
        athlete = session.get(Athlete, aid)
        if athlete is not None:
            replay_and_persist_athlete(athlete, session)
            n += 1
    return n


def run(session: Session, *, apply: bool) -> MergeReport:
    """Full merge pass. ``apply=False`` (dry-run) plans + previews counts, no writes."""
    plans = plan_merges(find_attempt_nodes(session))
    report = MergeReport(plans=plans)
    resolved = report.resolved
    if not resolved:
        return report

    mapping = {p.attempt_key: p.canonical_key for p in resolved}
    if apply:
        _ensure_canonical_nodes(resolved, session)

    ge_touched, ge_deleted, ge_graph_ids = _remap_graph_edges(mapping, session, apply=apply)
    me_touched, me_deleted = _remap_map_edges(mapping, session, apply=apply)

    if apply:
        session.execute(delete(TechniqueNode).where(TechniqueNode.node_key.in_(list(mapping))))

    report.graph_edges_touched = ge_touched
    report.graph_edges_deleted = ge_deleted
    report.map_edges_touched = me_touched
    report.map_edges_deleted = me_deleted
    report.athlete_graphs_replayed = _replay_affected(ge_graph_ids, session, apply=apply)
    return report


def check(session: Session) -> int:
    """Zero-write: report how many attempt-labelled node_keys remain (0 = done)."""
    remaining = find_attempt_nodes(session)
    if remaining:
        logger.warning("%d attempt-labelled technique_nodes remain:", len(remaining))
        for n in remaining:
            logger.warning("  %s -> %r", n.node_key, n.label)
    else:
        logger.info("0 attempt-labelled technique_nodes remain — clean")
    return len(remaining)


def _print_report(report: MergeReport, *, apply: bool) -> None:
    verb = "MERGED" if apply else "WOULD MERGE"
    logger.info("%d attempt-labelled technique_nodes found", len(report.plans))
    for p in report.resolved:
        logger.info("  %s: %s -> %s", verb, p.attempt_key, p.canonical_key)
    if report.unresolved:
        logger.warning(
            "%d unresolved (clean_label left them unchanged — need a library "
            "\"<x> attempt\" synonym):", len(report.unresolved)
        )
        for p in report.unresolved:
            logger.warning("  SKIP %s (%r)", p.attempt_key, p.attempt_label)
    del_word = "deleted" if apply else "would delete"
    logger.info("graph_edges: %d touched, %d %s", report.graph_edges_touched,
                report.graph_edges_deleted, del_word)
    logger.info("map_edges: %d touched, %d %s", report.map_edges_touched,
                report.map_edges_deleted, del_word)
    logger.info("athlete graphs %s: %d", "replayed" if apply else "would replay",
                report.athlete_graphs_replayed)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Merge attempt-labelled technique_nodes (012)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write (default: dry-run preview)")
    mode.add_argument(
        "--check", action="store_true", help="report remaining attempt nodes, no writes"
    )
    ap.add_argument("--dry-run", action="store_true", help="explicit no-op alias for the default")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from db.base import db_session

    with db_session() as session:
        if args.check:
            return 1 if check(session) else 0
        report = run(session, apply=args.apply)
        _print_report(report, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
