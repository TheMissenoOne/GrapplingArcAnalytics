"""Export one global match into a self-contained breakdown JSON for the public site.

The public landing (``../GrapplingArc``) is static, so it can't talk to the DB. This
exporter turns each ``matches`` row into a single JSON bundle that a dependency-free
client renders directly (timeline + transition graph + stat cards + ELO sparklines):

    assets/matches/<slug>.json     one bout, fully self-contained
    assets/fighters/<slug>.json    each participant's career graph (app-shaped)
    assets/matches/index.json      slug/fighters/headline per bout (articles index)

It is the public-site half of the Analytics→JSON contract (mirrors export/tech_library
for the app). The client graph renderer consumes the same app-shaped ``{nodes, edges}``
that ``admin/static/graphview.js`` already reads, and node keys use the shared
``analysis.names._normalize_name`` so they stay char-for-char with the app's
``graphSync.ts:normalizeLabel``.

Usage:
    uv run python -m export.match_breakdown --all
    uv run python -m export.match_breakdown --match dricus-du-plessis-vs-khamzat-chimaev-2025
    uv run python -m export.match_breakdown --all --out /tmp/site-assets
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.athlete_elo import _points_for_entry
from analysis.decision_space import sequence_decision_space
from analysis.names import _normalize_name
from db.models import Athlete, Graph, Match
from export.athlete_graph_export import athlete_graph_to_app_json

logger = logging.getLogger(__name__)

# Default output dir = the public repo's assets folder (sibling checkout).
_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "GrapplingArc" / "assets"


def slugify(name: str) -> str:
    """Athlete name → url slug (normalized, spaces → hyphens). Shares the node-key
    normalizer so a fighter's slug is stable across exports."""
    return _normalize_name(name).replace(" ", "-")


def match_slug(a: Athlete, b: Athlete, year: int | None) -> str:
    """Deterministic bout slug: ``<a>-vs-<b>-<year>`` (stored a/b order)."""
    return f"{slugify(a.name)}-vs-{slugify(b.name)}-{year if year is not None else 'tbd'}"


def _method(win_type: str | None, submission: str | None) -> str:
    """Human method string from the stored fields (no free-text method is kept)."""
    if not win_type:
        return "No contest / draw"
    if win_type == "SUBMISSION" and submission:
        return f"Submission ({submission})"
    return win_type.capitalize() if win_type.isupper() else win_type


def _side_of(actor_id: str | None, a: Athlete, b: Athlete) -> str | None:
    if actor_id == a.id:
        return "a"
    if actor_id == b.id:
        return "b"
    return None


def _sequence_view(match: Match, a: Athlete, b: Athlete) -> list[dict[str, Any]]:
    """Stored events → ordered timeline rows with explicit ``side`` + fighter ``name``."""
    rows: list[dict[str, Any]] = []
    for e in match.sequence or []:
        if not isinstance(e, dict):
            continue
        side = _side_of(e.get("actor_id"), a, b)
        if side is None:
            continue
        row: dict[str, Any] = {
            "label": str(e.get("label", "")),
            "type": str(e.get("type", "")),
            "side": side,
            "name": a.name if side == "a" else b.name,
        }
        if "successful" in e:
            row["successful"] = bool(e["successful"])
        if isinstance(e.get("ts"), int):  # absolute video seconds (video-seek contract)
            row["ts"] = e["ts"]
        rows.append(row)
    return rows


# Offensive entries (a fighter advancing) vs the dominant outcomes they convert into.
_ENTRY_TYPES = ("takedown", "pass", "sweep")
_DOMINANT_TYPES = ("control", "submission")


def _blank_side_stats() -> dict[str, Any]:
    return {
        "takedowns_landed": 0, "takedowns_attempted": 0,
        "submission_attempts": 0, "submissions_finished": 0,
        "sweeps": 0, "passes": 0, "escapes": 0, "controls": 0,
        "transitions": 0, "points": 0,
        # positional conversion = entries that reached a dominant position / entries
        "positional_entries": 0, "positional_conversions": 0,
    }


def _compute_stats(
    sequence: list[dict[str, Any]], ptv_v: dict[str, float] | None = None
) -> dict[str, Any]:
    """Per-side tallies, a momentum split + running series, and positional-conversion
    rate. Uses the same keyword point-map the ELO engine scores with, so numbers agree.
    If ptv_v (path-to-victory values) is provided, momentum_series uses PtV momentum;
    otherwise falls back to point-share.

    NOTE: control *time* (seconds) can't be derived — stored events carry no timestamps.
    """
    sides = {"a": _blank_side_stats(), "b": _blank_side_stats()}
    pending_entry = {"a": False, "b": False}  # an unconverted offensive entry is open
    cum = {"a": 0, "b": 0}
    momentum_series: list[float] = []
    for e in sequence:
        side = e["side"]
        s = sides[side]
        typ = e["type"]
        ok = bool(e.get("successful"))
        s["transitions"] += 1
        if typ == "takedown":
            s["takedowns_attempted"] += 1
            if ok:
                s["takedowns_landed"] += 1
        elif typ == "submission":
            s["submission_attempts"] += 1
            if ok:
                s["submissions_finished"] += 1
        elif typ == "sweep":
            s["sweeps"] += 1
        elif typ == "pass":
            s["passes"] += 1
        elif typ == "escape":
            s["escapes"] += 1
        elif typ == "control":
            s["controls"] += 1
        # positional conversion: an entry that later reaches a dominant position.
        if typ in _ENTRY_TYPES:
            s["positional_entries"] += 1
            pending_entry[side] = True
        elif typ in _DOMINANT_TYPES and pending_entry[side]:
            s["positional_conversions"] += 1
            pending_entry[side] = False
        s["points"] += _points_for_entry(e)
        cum[side] = s["points"]
        ctot = cum["a"] + cum["b"]
        momentum_series.append(round(cum["a"] / ctot, 3) if ctot else 0.5)

    # Prefer PtV momentum if available; fall back to point-share if empty.
    if ptv_v:
        from analysis.path_to_victory import ptv_momentum
        ptv_series = ptv_momentum(sequence, ptv_v)
        if ptv_series:
            momentum_series = ptv_series
    for s in sides.values():
        ent = s["positional_entries"]
        s["positional_conversion"] = round(s["positional_conversions"] / ent, 3) if ent else 0.0
    total = sides["a"]["points"] + sides["b"]["points"]
    momentum = {
        "a": sides["a"]["points"] / total if total else 0.5,
        "b": sides["b"]["points"] / total if total else 0.5,
    }
    return {"a": sides["a"], "b": sides["b"], "momentum": momentum,
            "momentum_series": momentum_series}


def _transition_graph(sequence: list[dict[str, Any]]) -> dict[str, Any]:
    """ONE unified per-bout grappling map: node = normalized technique label, edge = each
    consecutive transition along the *match timeline* (regardless of fighter), coloured by the
    ``side`` of the grappler who made the move. Shared positions are a single node, so the two
    grapplers' games read as one connected graph, distinguished by colour — not two separate
    subgraphs. App-shaped ``{nodes, edges}`` (graphview.js contract). Nodes carry ts (first
    occurrence timestamp) if available."""
    nodes: dict[str, dict[str, Any]] = {}
    side_use: dict[str, dict[str, int]] = {}  # node key → per-side usage, for fighter tint

    # Generic labels to skip (bare type-words, not specific techniques)
    _GENERIC = {
        "sweep", "takedown", "pass", "guard pass", "guard", "control", "submission",
        "escape", "transition", "reversal", "scramble", "clinch", "pull guard",
        "takedown attempt", "submission attempt", "sweep attempt", "pass attempt",
        "counter", "combination",
    }

    def touch(label: str, typ: str, side: str, ts: int | None = None) -> str:
        key = _normalize_name(label)
        if not key or key in _GENERIC:
            return ""
        node = nodes.get(key)
        if node is None:
            nodes[key] = {
                "id": key, "label": label,
                "data": {"type": typ, "usageCount": 1, "side": side},
            }
            if ts is not None:
                nodes[key]["data"]["ts"] = ts
            side_use[key] = {"a": 0, "b": 0}
        else:
            node["data"]["usageCount"] += 1
        side_use[key][side] += 1
        # The fighter who used the node most "owns" it (drives the a/b colouring).
        node = nodes[key]
        node["data"]["side"] = "a" if side_use[key]["a"] >= side_use[key]["b"] else "b"
        return key

    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def link(src: str, tgt: str, side: str) -> None:
        if not src or src == tgt:
            return
        ek = (src, tgt, side)
        edge = edges.get(ek)
        if edge is None:
            edges[ek] = {
                "id": f"{src}→{tgt}:{side}", "source": src, "target": tgt,
                "data": {"side": side, "count": 1, "elo": 1000},
            }
        else:
            edge["data"]["count"] += 1

    prev = ""  # previous position on the single match timeline
    for e in sequence:
        side = e["side"]
        ts = e.get("ts")
        key = touch(e["label"], e["type"], side, ts)
        if not key:
            continue
        link(prev, key, side)  # one flow; edge coloured by the grappler making this move
        prev = key
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _fighter_block(athlete: Athlete) -> dict[str, Any]:
    series = [round(float(x), 1) for x in (athlete.elo_series or [])]
    # ELO swing this bout = last snapshot minus the one before it (None if too short).
    # Presented as a RELATIVE % move (the raw rating is never shown user-facing).
    elo_delta = elo_delta_pct = None
    if len(series) >= 2:
        elo_delta = round(series[-1] - series[-2], 1)
        prev = series[-2]
        elo_delta_pct = round((series[-1] - prev) / prev * 100, 1) if prev else None
    return {
        "name": athlete.name,
        "slug": slugify(athlete.name),
        "nickname": athlete.nickname,
        "team": athlete.team,
        "weight_class": athlete.weight_class,
        "graph_elo": round(athlete.elo, 1),
        "elo_series": series,
        "elo_delta": elo_delta,
        "elo_delta_pct": elo_delta_pct,
        "career_graph_ref": f"fighters/{slugify(athlete.name)}.json",
    }


def build_match_breakdown(
    match: Match,
    a: Athlete,
    b: Athlete,
    curated_ds: dict[str, dict[str, Any]] | None = None,
    ptv_v: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Assemble the self-contained breakdown bundle for one bout.

    The ``decision_space`` block (RF14 / DS-12) is additive — the legacy keys (``meta``,
    ``sequence``, ``stats``, ``transition_graph``, ``fighters``) are unchanged so the
    existing site viz keeps rendering. ``curated_ds`` (node_key → ``technique_nodes``
    ``decision_space``) overrides the expert defaults when authored; ``systems`` /
    ``principles`` / ``decision_chains`` are populated once the ontology is curated.

    Per-transition DS (DS-05/07) is carried in the ordered ``decision_space.timeline`` —
    one entry per sequence event, with before/after + reductions. It is intentionally NOT
    written onto ``transition_graph.edges[].data``: that graph dedups repeated transitions,
    so a per-edge ``dsMeta`` would not map 1:1 to the timeline and would mislead (review fix
    F2). The App's ``GraphEdge.data.dsMeta`` stays a forward-declared optional for future
    per-edge DS on non-deduped graphs (e.g. user graphs).

    ``ptv_v`` (optional corpus PtV dict) drives momentum_series; if not provided, falls
    back to point-share momentum.
    """
    sequence = _sequence_view(match, a, b)
    winner_side = _side_of(match.winner_id, a, b)
    winner = None
    if winner_side is not None:
        winner = {"side": winner_side, "name": a.name if winner_side == "a" else b.name}
    return {
        "meta": {
            "slug": match_slug(a, b, match.year),
            "title": f"{a.name} vs {b.name}",
            "a": {"name": a.name, "slug": slugify(a.name)},
            "b": {"name": b.name, "slug": slugify(b.name)},
            "year": match.year,
            "event": match.event,
            "weight_class": match.weight_class,
            "win_type": match.win_type,
            "submission": match.submission,
            "method": _method(match.win_type, match.submission),
            "winner": winner,
            "video_url": match.video_url,
        },
        "sequence": sequence,
        "stats": _compute_stats(sequence, ptv_v),
        "transition_graph": _transition_graph(sequence),
        "fighters": {"a": _fighter_block(a), "b": _fighter_block(b)},
        # ── Strategic layer (RF14 / DS-12) — additive, backward-compatible ──
        "decision_space": sequence_decision_space(sequence, curated_ds),
        "systems": [],
        "principles": [],
        "decision_chains": [],
    }


def export_fighter_graph(athlete: Athlete, session: Session) -> dict[str, Any] | None:
    """The fighter's career technique graph (app-shaped). None if they have no graph row."""
    graph = session.execute(
        select(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == athlete.id)
    ).scalar_one_or_none()
    if graph is None:
        return None
    return athlete_graph_to_app_json(graph.id, session)


def _headline(bd: dict[str, Any]) -> str:
    """One-line summary for the articles index / homepage cards."""
    meta = bd["meta"]
    if meta["winner"]:
        loser = meta["b"] if meta["winner"]["side"] == "a" else meta["a"]
        return f"{meta['winner']['name']} def. {loser['name']} — {meta['method']}"
    return f"{meta['a']['name']} vs {meta['b']['name']} — {meta['method']}"


def _final_matches(session: Session) -> list[Match]:
    """Final bouts that actually have a sequence (an empty bout has nothing to show)."""
    rows = session.execute(select(Match).where(Match.status == "final")).scalars()
    return [m for m in rows if m.sequence]


def _load_curated_ds(session: Session) -> dict[str, dict[str, Any]]:
    """node_key → authored ``technique_nodes.decision_space`` (DS-01/04), if any (F4).

    These override the expert event-type defaults in every breakdown's DS timeline, so a
    curated position scores the same way wherever it appears in the corpus.
    """
    from db.models import TechniqueNode

    rows = session.execute(
        select(TechniqueNode.node_key, TechniqueNode.decision_space).where(
            TechniqueNode.decision_space.isnot(None)
        )
    ).all()
    return {node_key: ds for node_key, ds in rows if ds}


def export_site_assets(
    session: Session, out: Path, only_slug: str | None = None
) -> list[str]:
    """Write match + fighter JSON (and index.json) under ``out``. Returns slugs written."""
    matches_dir = out / "matches"
    fighters_dir = out / "fighters"
    matches_dir.mkdir(parents=True, exist_ok=True)
    fighters_dir.mkdir(parents=True, exist_ok=True)

    curated_ds = _load_curated_ds(session)  # F4: authored per-position DS overrides defaults
    index: list[dict[str, Any]] = []
    written: list[str] = []
    seen_fighters: set[str] = set()
    for match in _final_matches(session):
        a = session.get(Athlete, match.athlete_a_id)
        b = session.get(Athlete, match.athlete_b_id)
        if a is None or b is None:
            continue
        slug = match_slug(a, b, match.year)
        if only_slug and slug != only_slug:
            continue
        bd = build_match_breakdown(match, a, b, curated_ds=curated_ds)
        (matches_dir / f"{slug}.json").write_text(
            json.dumps(bd, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for athlete in (a, b):
            fslug = slugify(athlete.name)
            if fslug in seen_fighters:
                continue
            seen_fighters.add(fslug)
            graph = export_fighter_graph(athlete, session)
            if graph is not None:
                (fighters_dir / f"{fslug}.json").write_text(
                    json.dumps({"fighter": _fighter_block(athlete), "graph": graph},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        index.append({
            "slug": slug, "title": bd["meta"]["title"],
            "a": bd["meta"]["a"]["name"], "b": bd["meta"]["b"]["name"],
            "year": match.year, "event": match.event,
            "headline": _headline(bd), "events": len(bd["sequence"]),
        })
        written.append(slug)

    index.sort(key=lambda r: (r["year"] or 0), reverse=True)
    (matches_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Prune orphan files from prior exports (e.g. stale slugs after an athlete rename/dedupe) so
    # the site never shows a deleted/duplicate bout. Only on a full export — a single-slug export
    # must not wipe the rest.
    if only_slug is None:
        keep_matches = {f"{s}.json" for s in written} | {"index.json"}
        for f in matches_dir.glob("*.json"):
            if f.name not in keep_matches:
                f.unlink()
        keep_fighters = {f"{s}.json" for s in seen_fighters} | {"index.json"}
        for f in fighters_dir.glob("*.json"):
            if f.name not in keep_fighters:
                f.unlink()
    return written


def run(out: Path, only_slug: str | None) -> int:
    from db.base import db_session

    with db_session() as session:
        written = export_site_assets(session, out, only_slug)
    logger.info("Exported %d bout(s) → %s", len(written), out)
    for slug in written:
        logger.info("  %s", slug)
    if only_slug and not written:
        logger.warning("No bout matched slug %r", only_slug)
        return 1
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description="Export match breakdown JSON for the public site")
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="assets output dir")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--match", dest="slug", help="export only this bout slug")
    g.add_argument("--all", action="store_true", help="export every final bout (default)")
    args = ap.parse_args()
    return run(args.out, args.slug)


if __name__ == "__main__":
    raise SystemExit(main())
