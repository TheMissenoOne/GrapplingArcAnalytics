"""PUBLIC path sources for the map prototype pages (variants 15/16/17).

    uv run python -m scripts.render_map_prototypes --bundle PATH --public-sources

Kept out of ``scripts/render_map_prototypes.py`` on purpose: that module renders every variant
and its whole test suite drives it, so it must stay runnable with no database. This one opens a
READ-ONLY session, builds the same aggregates ``analysis.corpus_paths`` builds for the site, and
hands them back as ``PathSource`` rows the pages can offer in a selector.

Three kinds of source, all PUBLIC competition footage (``matches.sequence``) — root CLAUDE.md's
public/private line is what makes them safe to sit on the same page as the owner's own bundle,
and nothing here ever aggregates across that line:

* **corpus** — every final bout, actors COLLAPSED. The ocean's own reading: A's mount and B's
  mount are the same technical fact when the subject is the corpus rather than an athlete.
* **one athlete** — their OWN events across their own bouts, relabelled side ``a``. This is the
  dossier's derivation verbatim (``export.site_data._athlete_path_graph``); a dossier that also
  drew what was done TO them would be a different claim.

Who: Gordon Ryan plus the next best of the published Grappling ELO board
(``GrapplingArc/site/elo-data.js`` — the ranking the site actually publishes, over athletes that
already have a dossier, so "the 5 best of grapple-like" needs no second definition here).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from analysis.corpus_paths import aggregate_bouts
from analysis.corpus_paths import render_paths as corpus_render_paths
from scripts.render_map_prototypes import PathSource, path_source

logger = logging.getLogger(__name__)

_ELO_DATA = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site" / "elo-data.js"


def published_grappling_board(limit: int) -> list[str]:
    """The first ``limit`` names of the published Grappling ELO board, in rank order.

    Read from the generated site bundle rather than recomputed: that file IS the ranking the
    owner sees, and re-deriving it here (trusted set + rating run + gate) would be a second
    definition free to disagree with the one on the website.
    """
    if not _ELO_DATA.is_file():
        return []
    text = _ELO_DATA.read_text(encoding="utf-8")
    match = re.search(r"window\.GA_ELO\s*=\s*(\{.*\});?\s*$", text.strip(), re.S)
    if not match:
        return []
    rows = json.loads(match.group(1)).get("grappling", [])
    return [str(r[1]) for r in rows[:limit]]


def _athlete_bouts(athlete_id: str, matches: list[Any]) -> list[list[dict[str, Any]]]:
    """This athlete's OWN events, per bout, relabelled side ``a`` — the dossier's own input
    (mirrors ``export.site_data._athlete_path_graph``; only the payload step differs)."""
    bouts: list[list[dict[str, Any]]] = []
    for m in matches:
        own = [
            {"label": str(e.get("label", "")), "type": str(e.get("type", "")), "side": "a",
             **({"successful": e["successful"]} if "successful" in e else {})}
            for e in (m.sequence or [])
            if isinstance(e, dict) and e.get("actor_id") == athlete_id
        ]
        if own:
            bouts.append(own)
    return bouts


def public_sources(*, top_athletes: int = 6, include_corpus: bool = True) -> list[PathSource]:
    """Read-only. Never writes, never touches a ``graphs`` row."""
    from sqlalchemy import select

    from db.base import get_session_factory
    from db.models import Athlete
    from db.repository import get_matches_for_athlete
    from export.match_breakdown import _final_matches
    from export.site_data import _corpus_bouts

    # `_final_matches` / `_corpus_bouts` are private imports on purpose: "which bouts are final"
    # and "how a sequence becomes side a/b" are the site's own answers, and a second copy of
    # either here would be free to drift from what the site publishes.
    out: list[PathSource] = []
    with get_session_factory()() as session:
        if include_corpus:
            bouts = _corpus_bouts(session)
            agg = aggregate_bouts(bouts, collapse_actors=True)
            out.append(path_source(
                agg, corpus_render_paths(agg), id="corpus",
                label=f"Corpus ({len(bouts)} lutas)",
                note="dado PÚBLICO — matches.sequence, atores colapsados (leitura do oceano)"))
            logger.info("corpus source: %d bouts, %d occurrences", len(bouts), len(out[-1].paths))

        wanted = published_grappling_board(top_athletes)
        by_name = {
            a.name: a for a in session.execute(
                select(Athlete).where(Athlete.name.in_(wanted))).scalars()
        }
        finals = {m.id for m in _final_matches(session)}
        for rank, name in enumerate(wanted, start=1):
            athlete = by_name.get(name)
            if athlete is None:
                logger.warning("athlete %r is on the published board but not in the DB", name)
                continue
            matches = [m for m in get_matches_for_athlete(athlete.id, session)
                        if m.id in finals]
            bouts = _athlete_bouts(athlete.id, matches)
            if not bouts:
                logger.warning("athlete %r has no own events in any final bout", name)
                continue
            agg = aggregate_bouts(bouts)
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            out.append(path_source(
                agg, corpus_render_paths(agg), id=slug, label=f"#{rank} {name}",
                note=(f"dado PÚBLICO — dossiê: só os eventos DELE, {len(bouts)} lutas "
                       f"(mesma derivação de export.site_data._athlete_path_graph)")))
            logger.info("athlete source %s: %d bouts, %d occurrences",
                         slug, len(bouts), len(out[-1].paths))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for src in public_sources():
        print(f"{src.id:24} {len(src.paths):>5} ocorrências  {len(src.state):>4} estados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
