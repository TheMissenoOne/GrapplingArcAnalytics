#!/usr/bin/env python
"""Insert the UFC/MMA match dump (``scripts/ufc_matches_data.py``) as global matches.

The source dump is keyed by ``(athlete_a_name, year)`` and gives ``winner`` (full name),
``method``, and ``events`` whose ``actor`` is a full name (or ``referee`` for resets). The
SECOND participant is not named directly, so it is DERIVED here:

  - opponent = the winner when the winner isn't athlete_a, else the one non-referee event
    actor that isn't athlete_a (e.g. an athlete_a win recounted from their own side).
  - winner_id = the athlete whose name matches ``winner`` (NULL for no-contest / draw).
  - win_type: MMA has no POINTS — submissions map to "SUBMISSION", everything decisive
    (KO/TKO/all decisions) to "DECISION"; No Contest / Draw stay neutral (winner NULL).
  - sequence: ``actor`` (full name) -> ``actor_id``; ``referee``/``reset`` events are
    dropped (no athlete to tag, not a technique); takedown-labelled ``transition`` events
    are retyped "takedown" to merge with the existing library convention.

The raw dump triple-lists one block and repeats one match across blocks; rows are de-duped
by ``(frozenset(participants), year)`` so each real bout is stored once.

Idempotent: each canonical bout it is about to insert is deleted first (either orientation),
so re-running never duplicates and never clobbers unrelated matches. Every participant is
replayed (the double pass), so opponents get a graph from their side too.

Usage:
    uv run python -m scripts.insert_ufc_matches            # write to DB
    uv run python -m scripts.insert_ufc_matches --dry-run  # parse + report only
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from analysis.names import _normalize_name  # noqa: E402
from scripts.ufc_matches_data import RAW as _RAW  # noqa: E402

# The dump is keyed by (athlete_a_name, year) tuples; the literal types loosely.
RAW = cast("list[dict[tuple[str, int], dict[str, Any]]]", _RAW)

logger = logging.getLogger(__name__)

_TAKEDOWN_RE = re.compile(r"\b(takedown|trip)\b", re.IGNORECASE)


class CanonicalMatch:
    """One de-duped bout, ready for register_match (names not yet resolved to ids)."""

    __slots__ = (
        "a_name", "b_name", "year", "winner_name", "win_type", "submission", "events",
    )

    def __init__(
        self,
        a_name: str,
        b_name: str,
        year: int | None,
        winner_name: str | None,
        win_type: str | None,
        submission: str | None,
        events: list[dict[str, Any]],
    ) -> None:
        self.a_name = a_name
        self.b_name = b_name
        self.year = year
        self.winner_name = winner_name
        self.win_type = win_type
        self.submission = submission
        self.events = events


def _win_type_from_method(method: str) -> str | None:
    """MMA method string -> stored win_type (None = neutral: no contest / draw)."""
    up = method.strip().upper()
    if up.startswith("SUBMISSION"):
        return "SUBMISSION"
    if "NO CONTEST" in up or up.startswith("DRAW") or up == "NC":
        return None
    return "DECISION"


def _submission_from_method(method: str, win_type: str | None) -> str | None:
    """Pull the submission name out of e.g. ``Submission (Rear Naked Choke)``."""
    if win_type != "SUBMISSION":
        return None
    m = re.search(r"\(([^)]+)\)", method)
    return m.group(1).strip() if m else None


def _derive_opponent(
    a_name: str, winner_name: str, events: list[dict[str, Any]]
) -> str | None:
    """The other participant: the winner if it isn't athlete_a, else the lone non-referee
    actor that isn't athlete_a. None when undeterminable (skip the row)."""
    a_norm = _normalize_name(a_name)
    if winner_name and _normalize_name(winner_name) != a_norm:
        return winner_name
    others = {
        e.get("actor", "")
        for e in events
        if str(e.get("actor", "")).lower() != "referee"
        and _normalize_name(str(e.get("actor", ""))) != a_norm
    }
    return next(iter(others)) if len(others) == 1 else None


def _clean_events(
    a_name: str, b_name: str, events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop referee/reset events; tag each remaining event with its actor's name (resolved
    to an id later) and retype takedown-labelled transitions."""
    a_norm, b_norm = _normalize_name(a_name), _normalize_name(b_name)
    out: list[dict[str, Any]] = []
    for e in events:
        if str(e.get("type")) == "reset" or str(e.get("actor", "")).lower() == "referee":
            continue
        actor_norm = _normalize_name(str(e.get("actor", "")))
        if actor_norm == a_norm:
            actor = "a"
        elif actor_norm == b_norm:
            actor = "b"
        else:
            logger.warning("  dropping event with unknown actor %r", e.get("actor"))
            continue
        label = str(e.get("label", ""))
        typ = str(e.get("type", ""))
        if typ == "transition" and _TAKEDOWN_RE.search(label):
            typ = "takedown"
        item: dict[str, Any] = {"label": label, "type": typ, "actor": actor}
        if "successful" in e:
            item["successful"] = bool(e["successful"])
        out.append(item)
    return out


def build_matches() -> list[CanonicalMatch]:
    """Flatten + de-dupe the raw dump into canonical bouts."""
    seen: dict[tuple[frozenset[str], int | None], CanonicalMatch] = {}
    for block in RAW:
        for (a_name, year), m in block.items():
            winner_name = str(m.get("winner") or "").strip()
            events = m.get("events") or []
            b_name = _derive_opponent(a_name, winner_name, events)
            if not b_name:
                logger.warning("Skipping %s (%s): cannot determine opponent", a_name, year)
                continue
            key = (
                frozenset((_normalize_name(a_name), _normalize_name(b_name))),
                year,
            )
            if key in seen:
                continue
            method = str(m.get("method") or "")
            win_type = _win_type_from_method(method)
            seen[key] = CanonicalMatch(
                a_name=a_name,
                b_name=b_name,
                year=year,
                winner_name=winner_name or None,
                win_type=win_type,
                submission=_submission_from_method(method, win_type),
                events=_clean_events(a_name, b_name, events),
            )
    return list(seen.values())


def run(dry_run: bool = False) -> int:
    matches = build_matches()
    logger.info("UFC import: %d de-duped bouts (from %d raw entries)",
                len(matches), sum(len(b) for b in RAW))
    if dry_run:
        for cm in matches:
            if cm.win_type is None:
                outcome = "NC/Draw"
            elif _normalize_name(cm.winner_name or "") == _normalize_name(cm.a_name):
                outcome = f"{cm.a_name} W"
            else:
                outcome = f"{cm.b_name} W"
            logger.info("  %s vs %s (%s) — %s, %s, %d events",
                        cm.a_name, cm.b_name, cm.year, outcome,
                        cm.win_type or "neutral", len(cm.events))
        return 0

    from sqlalchemy import and_, delete, or_, select

    from db.base import db_session
    from db.models import Athlete, Match
    from db.repository import register_match, replay_and_persist_athlete, upsert_athlete

    with db_session() as session:
        by_norm = {
            _normalize_name(a.name): a for a in session.execute(select(Athlete)).scalars()
        }

        def resolve(name: str, source: str) -> Athlete:
            key = _normalize_name(name)
            ath = by_norm.get(key)
            if ath is None:
                aid = upsert_athlete(name=name, belt="black", source=source, session=session)
                ath = session.get(Athlete, aid)
                assert ath is not None
                by_norm[key] = ath
            return ath

        participants: set[str] = set()
        for cm in matches:
            a = resolve(cm.a_name, source="manual")
            b = resolve(cm.b_name, source="opponent")
            participants.update((a.id, b.id))
            # Idempotency: clear this exact bout (either orientation, same year) first.
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
                winner_id = (
                    a.id if _normalize_name(cm.winner_name) == _normalize_name(cm.a_name)
                    else b.id
                )
            seq = [
                {**{k: v for k, v in e.items() if k != "actor"},
                 "actor_id": a.id if e["actor"] == "a" else b.id}
                for e in cm.events
            ]
            register_match(
                a.id, b.id,
                winner_id=winner_id,
                win_type=cm.win_type,
                submission=cm.submission,
                event=None,
                year=cm.year,
                weight_class=None,
                stage=None,
                sequence=seq,
                created_by=None,
                session=session,
            )

        for aid in participants:
            athlete = session.get(Athlete, aid)
            if athlete is not None:
                replay_and_persist_athlete(athlete, session)

        logger.info("Inserted %d bouts; replayed %d athletes", len(matches), len(participants))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Insert the UFC/MMA match dump as global matches")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    return run(dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
