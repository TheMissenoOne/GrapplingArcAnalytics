"""Shared importer for bjj-match-analyzer match dumps (keyed by ``(athlete_a_name, year)``).

Powers the per-event insert scripts (WNO, Khabib, …). It derives the second participant,
maps the method string → ``win_type``/``submission``, canonicalises every technique label to
the library (``clean_label``), de-dupes bouts, and — on a real run — idempotently replaces
each bout (either orientation, same year) then replays every participant (double pass).

Parameterised version of ``insert_ufc_matches`` (whose pure parsers it reuses); the only
per-dataset differences are the dump itself and the ``event`` tag.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from analysis.names import athlete_key, clean_athlete_name
from analysis.technique_match import clean_label
from scripts.insert_ufc_matches import (
    _KO_RE,
    _MIN_GRAPPLING,
    CanonicalMatch,
    _clean_events,
    _derive_opponent,
    _parse_timestamp,
    _submission_from_method,
    _win_type_from_method,
)

logger = logging.getLogger(__name__)

Dump = list[dict[tuple[str, int], dict[str, Any]]]


def _build_timeline(
    a_name: str, b_name: str, raw_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Full event timeline for the breakdown UI — keeps EVERY event (strikes, resets, penalties,
    referee calls, non-technique) unlike ``_clean_events`` which drops them for the graph. Maps
    actor → 'a'/'b'/None (unknown/referee) and normalizes ts to int seconds when present."""
    a_norm, b_norm = athlete_key(a_name), athlete_key(b_name)
    out: list[dict[str, Any]] = []
    for e in raw_events:
        actor_norm = athlete_key(str(e.get("actor", "")))
        actor = "a" if actor_norm == a_norm else "b" if actor_norm == b_norm else None
        raw_ts = e.get("ts", e.get("timestamp"))
        ts = _parse_timestamp(raw_ts) if isinstance(raw_ts, str) else raw_ts
        item: dict[str, Any] = {
            "label": str(e.get("label", "")),
            "type": str(e.get("type", "")),
            "actor": actor,
        }
        if "successful" in e:
            item["successful"] = bool(e["successful"])
        if isinstance(ts, int):
            item["ts"] = ts
        out.append(item)
    return out

def _load_url_mapping() -> dict[str, Any]:
    """Load url_mapping.json if available, else return empty dict."""
    try:
        path = Path(__file__).resolve().parents[1] / "url_mapping.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load url_mapping.json: %s", e)
    return {}


# Trailing round tags in url_mapping "athlete" strings ("Craig Jones vs Kyle Boehm QF").
_STAGE_RE = re.compile(
    r"\s+(?:qf|sf|bm|bronze(?:\s+medal(?:\s+match)?)?|finals?|superfight)$", re.IGNORECASE
)


def video_index() -> dict[tuple[frozenset[str], int | None], str]:
    """(participants key, year) → video URL (+``&t=<start>s`` when the bout start is known)."""
    index: dict[tuple[frozenset[str], int | None], str] = {}
    for mapping in _load_url_mapping().values():
        base = mapping.get("video_url")
        if not base:
            continue
        for m in mapping.get("matches", []):
            a = _STAGE_RE.sub("", str(m.get("athlete") or "").strip())
            b = str(m.get("opponent") or "").strip()
            if " vs " in a:  # keyed by the full matchup: both names live in "athlete"
                a, b = (s.strip() for s in a.split(" vs ", 1))
            if b and athlete_key(a) == athlete_key(b):
                # mapping quirk: "opponent" often mirrors "athlete"; the other participant
                # is then the "winner" field (loser listed as athlete).
                b = str(m.get("winner") or "").strip()
            if not a or not b or athlete_key(a) == athlete_key(b):
                continue
            secs = m.get("seconds")
            url = f"{base}&t={int(secs)}s" if isinstance(secs, int | float) else base
            index.setdefault((frozenset((athlete_key(a), athlete_key(b))), m.get("year")), url)
    return index


def build_matches(raw: Dump, *, clean: bool = True) -> list[CanonicalMatch]:
    """Flatten + de-dupe a dump into canonical bouts (labels library-cleaned when ``clean``)."""
    seen: dict[tuple[frozenset[str], int | None], CanonicalMatch] = {}
    for block in raw:
        for (a_name, year), m in block.items():
            winner_name = str(m.get("winner") or "").strip()
            raw_events = m.get("events") or []
            if " vs " in a_name:
                # Dump keyed by the full matchup: both participants come from the key.
                a_name, b_name = (s.strip() for s in a_name.split(" vs ", 1))
            else:
                b_name = _derive_opponent(a_name, winner_name, raw_events)
                # Derivation failed or self-collided (winner is a name-variant of side A):
                # fall back to the explicit "opponent" field (the winner's opponent) —
                # dirtier strings, but recovers bouts that were previously skipped.
                opp = str(m.get("opponent") or "").strip()
                if ((not b_name or athlete_key(b_name) == athlete_key(a_name))
                        and opp and athlete_key(opp) != athlete_key(a_name)):
                    b_name = opp
            if not b_name:
                logger.warning("Skipping %s (%s): cannot determine opponent", a_name, year)
                continue
            # Skip bogus "X vs X" bouts: both sides clean to the same human (dump error /
            # accent+nickname variants of one person). Can't be a real match.
            if athlete_key(a_name) == athlete_key(b_name):
                logger.warning("Skipping self-match %s vs %s (%s)", a_name, b_name, year)
                continue
            # Dedup by cleaned identity key so dirty variants (timestamps/nicknames/accents)
            # of the same human collapse to one bout instead of an "X vs X" self-match.
            key = (frozenset((athlete_key(a_name), athlete_key(b_name))), year)
            if key in seen:
                continue
            method = str(m.get("method") or "")
            # No explicit winner in the dump (the transcript never stated it)? A submission
            # logged ``successful: true`` IS the finish — its actor won. Recovers winners for
            # sub-finish bouts; decision/points bouts honestly stay NULL. See match_event_model.
            if not winner_name:
                fin = next(
                    (e for e in reversed(raw_events)
                     if str(e.get("type")) == "submission" and e.get("successful") is True
                     and athlete_key(str(e.get("actor", "")))
                     in (athlete_key(a_name), athlete_key(b_name))),
                    None,
                )
                if fin:
                    winner_name = str(fin.get("actor", ""))
                    if not method or method.strip().upper() == "UNKNOWN":
                        method = f"Submission ({fin.get('label', '')})"
            win_type = _win_type_from_method(method)
            events = _clean_events(a_name, b_name, raw_events)
            if clean:
                for e in events:
                    e["label"] = clean_label(str(e["label"]), str(e.get("type", "")))
            submission = _submission_from_method(method, win_type)
            strike_count = sum(1 for e in raw_events if str(e.get("type")) == "strike")
            timeline = _build_timeline(a_name, b_name, raw_events)
            seen[key] = CanonicalMatch(
                a_name=a_name, b_name=b_name, year=year,
                winner_name=winner_name or None, win_type=win_type,
                submission=clean_label(submission) if (clean and submission) else submission,
                events=events, strike_count=strike_count,
                ko_finish=bool(_KO_RE.search(method)), timeline=timeline,
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

    videos = video_index()  # (participants, year) → url once, not a scan per bout

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
            # Striking match (MMA) with too little grappling — purge (delete above ran)
            # and don't re-register. Striking evidence = logged strikes OR a KO/TKO finish
            # (catches empty striking marathons). Pure-grappling bouts (no strikes, not KO)
            # are never gated, so fast submissions with 1-3 actions still import.
            if (cm.strike_count > 0 or cm.ko_finish) and len(cm.events) < _MIN_GRAPPLING:
                logger.info("  skip striking match %s vs %s (%s): %d grappling, %d strikes%s",
                            cm.a_name, cm.b_name, cm.year, len(cm.events), cm.strike_count,
                            ", KO" if cm.ko_finish else "")
                continue
            winner_id: str | None = None
            if cm.win_type is not None and cm.winner_name:
                winner_id = (a.id if athlete_key(cm.winner_name) == athlete_key(cm.a_name)
                             else b.id)
            seq = [
                {**{k: v for k, v in e.items() if k != "actor"},
                 "actor_id": a.id if e["actor"] == "a" else b.id}
                for e in cm.events
            ]
            video_url = videos.get(
                (frozenset((athlete_key(cm.a_name), athlete_key(cm.b_name))), cm.year)
            )
            register_match(
                a.id, b.id, winner_id=winner_id, win_type=cm.win_type,
                submission=cm.submission, event=event, year=cm.year,
                weight_class=None, stage=None, sequence=seq, created_by=None,
                video_url=video_url, timeline=cm.timeline, session=session,
            )

        for aid in participants:
            athlete = session.get(Athlete, aid)
            if athlete is not None:
                replay_and_persist_athlete(athlete, session)

        logger.info("Inserted %d bouts; replayed %d athletes", len(matches), len(participants))
    return 0
