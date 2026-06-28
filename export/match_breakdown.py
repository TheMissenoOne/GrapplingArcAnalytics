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
        rows.append(row)
    return rows


def _blank_side_stats() -> dict[str, Any]:
    return {
        "takedowns_landed": 0, "takedowns_attempted": 0,
        "submission_attempts": 0, "submissions_finished": 0,
        "sweeps": 0, "passes": 0, "escapes": 0, "controls": 0,
        "points": 0,
    }


def _compute_stats(sequence: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-side tallies + a momentum split (share of scoring points, using the same
    keyword point-map the ELO engine scores with, so the numbers agree)."""
    sides = {"a": _blank_side_stats(), "b": _blank_side_stats()}
    for e in sequence:
        s = sides[e["side"]]
        typ = e["type"]
        ok = bool(e.get("successful"))
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
        s["points"] += _points_for_entry(e)
    total = sides["a"]["points"] + sides["b"]["points"]
    momentum = {
        "a": sides["a"]["points"] / total if total else 0.5,
        "b": sides["b"]["points"] / total if total else 0.5,
    }
    return {"a": sides["a"], "b": sides["b"], "momentum": momentum}


def _transition_graph(sequence: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-bout grappling map: node = normalized technique label, edge = each consecutive
    same-fighter transition. App-shaped ``{nodes, edges}`` (graphview.js contract)."""
    nodes: dict[str, dict[str, Any]] = {}

    def touch(label: str, typ: str) -> str:
        key = _normalize_name(label)
        if not key:
            return ""
        node = nodes.get(key)
        if node is None:
            nodes[key] = {
                "id": key, "label": label,
                "data": {"type": typ, "usageCount": 1},
            }
        else:
            node["data"]["usageCount"] += 1
        return key

    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    prev_key: dict[str, str] = {"a": "", "b": ""}
    for e in sequence:
        side = e["side"]
        key = touch(e["label"], e["type"])
        if not key:
            continue
        src = prev_key[side]
        if src and src != key:
            ek = (src, key, side)
            edge = edges.get(ek)
            if edge is None:
                edges[ek] = {
                    "id": f"{src}→{key}:{side}", "source": src, "target": key,
                    "data": {"side": side, "count": 1, "elo": 1000},
                }
            else:
                edge["data"]["count"] += 1
        prev_key[side] = key
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _fighter_block(athlete: Athlete) -> dict[str, Any]:
    return {
        "name": athlete.name,
        "slug": slugify(athlete.name),
        "nickname": athlete.nickname,
        "team": athlete.team,
        "weight_class": athlete.weight_class,
        "graph_elo": round(athlete.elo, 1),
        "elo_series": [round(float(x), 1) for x in (athlete.elo_series or [])],
        "career_graph_ref": f"fighters/{slugify(athlete.name)}.json",
    }


def build_match_breakdown(match: Match, a: Athlete, b: Athlete) -> dict[str, Any]:
    """Assemble the self-contained breakdown bundle for one bout."""
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
        },
        "sequence": sequence,
        "stats": _compute_stats(sequence),
        "transition_graph": _transition_graph(sequence),
        "fighters": {"a": _fighter_block(a), "b": _fighter_block(b)},
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


def export_site_assets(
    session: Session, out: Path, only_slug: str | None = None
) -> list[str]:
    """Write match + fighter JSON (and index.json) under ``out``. Returns slugs written."""
    matches_dir = out / "matches"
    fighters_dir = out / "fighters"
    matches_dir.mkdir(parents=True, exist_ok=True)
    fighters_dir.mkdir(parents=True, exist_ok=True)

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
        bd = build_match_breakdown(match, a, b)
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
