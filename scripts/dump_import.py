"""Shared importer for bjj-match-analyzer match dumps (keyed by ``(athlete_a_name, year)``).

Powers the per-event insert scripts (WNO, Khabib, …). It derives the second participant,
maps the method string → ``win_type``/``submission``, canonicalises every technique label to
the library (``clean_label``), de-dupes bouts, and — on a real run — idempotently replaces
each bout (either orientation, same year) then replays every participant (double pass).

Parameterised version of ``insert_ufc_matches`` (whose pure parsers it reuses); the only
per-dataset differences are the dump itself and the ``event`` tag.
"""

from __future__ import annotations

import logging
from typing import Any

from analysis.names import athlete_key, clean_athlete_name
from analysis.technique_match import clean_label
from scripts.insert_ufc_matches import (
    CanonicalMatch,
    _clean_events,
    _derive_opponent,
    _submission_from_method,
    _win_type_from_method,
)

logger = logging.getLogger(__name__)

Dump = list[dict[tuple[str, int], dict[str, Any]]]


def build_matches(raw: Dump, *, clean: bool = True) -> list[CanonicalMatch]:
    """Flatten + de-dupe a dump into canonical bouts (labels library-cleaned when ``clean``)."""
    seen: dict[tuple[frozenset[str], int | None], CanonicalMatch] = {}
    for block in raw:
        for (a_name, year), m in block.items():
            winner_name = str(m.get("winner") or "").strip()
            raw_events = m.get("events") or []
            b_name = _derive_opponent(a_name, winner_name, raw_events)
            if not b_name:
                logger.warning("Skipping %s (%s): cannot determine opponent", a_name, year)
                continue
            # Dedup by cleaned identity key so dirty variants (timestamps/nicknames/accents)
            # of the same human collapse to one bout instead of an "X vs X" self-match.
            key = (frozenset((athlete_key(a_name), athlete_key(b_name))), year)
            if key in seen:
                continue
            method = str(m.get("method") or "")
            win_type = _win_type_from_method(method)
            events = _clean_events(a_name, b_name, raw_events)
            if clean:
                for e in events:
                    e["label"] = clean_label(str(e["label"]), str(e.get("type", "")))
            submission = _submission_from_method(method, win_type)
            seen[key] = CanonicalMatch(
                a_name=a_name, b_name=b_name, year=year,
                winner_name=winner_name or None, win_type=win_type,
                submission=clean_label(submission) if (clean and submission) else submission,
                events=events,
            )
    return list(seen.values())


def run_dump(raw: Dump, *, event: str | None, label: str, dry_run: bool = False) -> int:
    """Build + (unless ``dry_run``) persist a dump, tagging each bout with ``event``."""
    matches = build_matches(raw)
    logger.info("%s import: %d de-duped bouts (from %d raw entries)",
                label, len(matches), sum(len(b) for b in raw))
    if dry_run:
        for cm in matches:
            outcome = "NC/Draw" if cm.win_type is None else f"{cm.winner_name} W"
            logger.info("  %s vs %s (%s) — %s, %s, %d events", cm.a_name, cm.b_name,
                        cm.year, outcome, cm.win_type or "neutral", len(cm.events))
        return 0

    from sqlalchemy import and_, delete, or_, select

    from db.base import db_session
    from db.models import Athlete, Match
    from db.repository import register_match, replay_and_persist_athlete, upsert_athlete

    with db_session() as session:
        by_norm = {
            athlete_key(a.name): a for a in session.execute(select(Athlete)).scalars()
        }

        def resolve(name: str, source: str) -> Athlete:
            key = athlete_key(name)
            ath = by_norm.get(key)
            if ath is None:
                aid = upsert_athlete(
                    name=clean_athlete_name(name), belt="black", source=source, session=session
                )
                ath = session.get(Athlete, aid)
                assert ath is not None
                by_norm[key] = ath
            return ath

        participants: set[str] = set()
        for cm in matches:
            a = resolve(cm.a_name, source="manual")
            b = resolve(cm.b_name, source="opponent")
            participants.update((a.id, b.id))
            session.execute(
                delete(Match).where(
                    Match.year.is_(cm.year) if cm.year is None else Match.year == cm.year,
                    or_(
                        and_(Match.athlete_a_id == a.id, Match.athlete_b_id == b.id),
                        and_(Match.athlete_a_id == b.id, Match.athlete_b_id == a.id),
                    ),
                )
            )
            session.flush()
            winner_id: str | None = None
            if cm.win_type is not None and cm.winner_name:
                winner_id = (a.id if athlete_key(cm.winner_name) == athlete_key(cm.a_name)
                             else b.id)
            seq = [
                {**{k: v for k, v in e.items() if k != "actor"},
                 "actor_id": a.id if e["actor"] == "a" else b.id}
                for e in cm.events
            ]
            register_match(
                a.id, b.id, winner_id=winner_id, win_type=cm.win_type,
                submission=cm.submission, event=event, year=cm.year,
                weight_class=None, stage=None, sequence=seq, created_by=None,
                session=session,
            )

        for aid in participants:
            athlete = session.get(Athlete, aid)
            if athlete is not None:
                replay_and_persist_athlete(athlete, session)

        logger.info("Inserted %d bouts; replayed %d athletes", len(matches), len(participants))
    return 0
