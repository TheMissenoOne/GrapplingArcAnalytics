#!/usr/bin/env python
# ruff: noqa: E501
"""Insert Ethan Crelinsten's matches from the transcript corpus into the DB.

Fixes the existing WNO 24 match (opponent was entered as "Ethan Krellstein"
instead of DeAndre Corban), adds new matches from transcripts, then replays
Ethan's graph for updated ELO.

Sources:
  - Ethan Crelinsten.txt: Polaris 36, ADCC 2022/2024, WNO 24, Combat JJ Worlds
  - CJI2Day2.txt: CJI 2 (2025) team tournament — vs Declan Moody, vs Dorian Olivarez

Idempotent: re-running skips inserts if a match for (event, year, opponent) exists.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import and_, or_, select

from analysis.names import _normalize_name
from db.base import db_session
from db.models import Athlete, Match
from db.repository import (
    register_match,
    replay_and_persist_athlete,
    upsert_athlete,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

ATHLETE_NAME = "Ethan Crelinsten"

MATCHES: list[dict] = [
    # ── 2022 ADCC — R1 loss to Josh Cisneros ─────────────────────────────────
    dict(opponent="Joshua Cisneros", event="ADCC World Championship", year=2022,
         won=False, win_type="SUBMISSION", submission="Shoulder Lock",
         stage="R1", weight_class="77KG",
         sequence=[
             {"label": "Takedown",            "type": "transition",   "actor": "opponent", "successful": False},
             {"label": "Flying Triangle",      "type": "submission",  "actor": "opponent", "successful": False},
             {"label": "Guard Pass",           "type": "pass",        "actor": "opponent"},
             {"label": "Mount",                "type": "control",     "actor": "opponent"},
             {"label": "Shoulder Lock",        "type": "submission",  "actor": "opponent", "successful": True},
         ]),
    # ── 2022 Combat Jiu-Jitsu Worlds — R1 win over Adrian Luna ──────────────
    dict(opponent="Adrian Luna", event="Combat Jiu-Jitsu Worlds", year=2022,
         won=True, win_type="SUBMISSION", submission="Rear Naked Choke",
         stage="R1", weight_class="LWT",
         sequence=[
             {"label": "Takedown",            "type": "takedown",    "actor": "you"},
             {"label": "Mount",                "type": "control",     "actor": "you"},
             {"label": "Back Take",            "type": "control",     "actor": "you"},
             {"label": "Rear Naked Choke",     "type": "submission",  "actor": "you", "successful": True},
         ]),
    # ── 2024 ADCC — R16 win over Ethan Thomas (points) ─────────────────────
    dict(opponent="Ethan Thomas", event="ADCC World Championship", year=2024,
         won=True, win_type="POINTS", submission=None,
         stage="R16", weight_class="77KG",
         sequence=[
             {"label": "Takedown",            "type": "transition",  "actor": "opponent", "successful": False},
             {"label": "Butterfly Guard",      "type": "guard",       "actor": "you"},
             {"label": "Leg Entry",            "type": "transition",  "actor": "opponent"},
             {"label": "50/50 Guard",          "type": "guard",       "actor": "you"},
             {"label": "Back Take",            "type": "control",     "actor": "you"},
             {"label": "Body Triangle",        "type": "control",     "actor": "you"},
             {"label": "Rear Naked Choke",     "type": "submission",  "actor": "you", "successful": False},
         ]),
    # ── 2024 ADCC — QF loss to Diego Pato (kneebar) ────────────────────────
    dict(opponent="Diego Pato", event="ADCC World Championship", year=2024,
         won=False, win_type="SUBMISSION", submission="Kneebar",
         stage="QF", weight_class="77KG",
         sequence=[
             {"label": "Leg Entry",            "type": "transition",  "actor": "opponent"},
             {"label": "Heel Hook",            "type": "submission",  "actor": "opponent", "successful": False},
             {"label": "50/50 Guard",          "type": "guard",       "actor": "you"},
             {"label": "Sweep",                "type": "sweep",       "actor": "opponent", "successful": True},
             {"label": "Kneebar",              "type": "submission",  "actor": "opponent", "successful": True},
         ]),
    # ── 2025 WNO 24 — decision win over DeAndre Corban ─────────────────────
    dict(opponent="DeAndre Corban", event="WNO 24", year=2025,
         won=True, win_type="DECISION", submission=None,
         stage=None, weight_class="LWT",
         sequence=[
             {"label": "Takedown Defense",     "type": "takedown",    "actor": "you"},
             {"label": "Single Leg X",         "type": "guard",       "actor": "opponent"},
             {"label": "Leg Entry",            "type": "transition",  "actor": "opponent"},
             {"label": "Guard Pass",           "type": "pass",        "actor": "you"},
             {"label": "Mount",                "type": "control",     "actor": "you"},
             {"label": "Back Take",            "type": "control",     "actor": "you"},
             {"label": "Rear Naked Choke",     "type": "submission",  "actor": "you", "successful": False},
         ]),
    # ── 2025 CJI 2 — draw vs Declan Moody ──────────────────────────────────
    dict(opponent="Declan Moody", event="Craig Jones Invitational 2", year=2025,
         won=False, win_type="DRAW", submission=None,
         stage="SPF", weight_class="ABS",
         sequence=[
             {"label": "Takedown",            "type": "takedown",    "actor": "opponent"},
             {"label": "Guard",                "type": "guard",       "actor": "you"},
             {"label": "Guard Pass",           "type": "pass",        "actor": "opponent"},
             {"label": "Mount",                "type": "control",     "actor": "opponent"},
             {"label": "Half Guard",           "type": "guard",       "actor": "you"},
             {"label": "Guard Pass",           "type": "pass",        "actor": "opponent"},
             {"label": "Mount",                "type": "control",     "actor": "opponent"},
             {"label": "Arm Lock",             "type": "submission",  "actor": "opponent", "successful": False},
             {"label": "Heel Hook",            "type": "submission",  "actor": "you",      "successful": False},
         ]),
    # ── 2025 CJI 2 — draw vs Dorian Olivarez ───────────────────────────────
    dict(opponent="Dorian Olivarez", event="Craig Jones Invitational 2", year=2025,
         won=False, win_type="DRAW", submission=None,
         stage="SPF", weight_class="ABS",
         sequence=[
             {"label": "Guard",                "type": "guard",       "actor": "you"},
             {"label": "Guard Pass",           "type": "pass",        "actor": "opponent"},
             {"label": "Turtle",               "type": "guard",       "actor": "you"},
             {"label": "Front Headlock",       "type": "control",     "actor": "opponent"},
             {"label": "Closed Guard",         "type": "guard",       "actor": "you"},
             {"label": "Back Take",            "type": "control",     "actor": "you", "successful": False},
         ]),
    # ── 2026 Polaris 36 — RNC win over Shai Gerena ─────────────────────────
    dict(opponent="Shai Gerena", event="Polaris 36", year=2026,
         won=True, win_type="SUBMISSION", submission="Rear Naked Choke",
         stage="F", weight_class="LWT",
         sequence=[
             {"label": "Closed Guard",         "type": "guard",       "actor": "opponent"},
             {"label": "Leg Entry",            "type": "transition",  "actor": "opponent"},
             {"label": "Guard Pass",           "type": "pass",        "actor": "you"},
             {"label": "Back Take",            "type": "control",     "actor": "you"},
             {"label": "Body Triangle",        "type": "control",     "actor": "you"},
             {"label": "Rear Naked Choke",     "type": "submission",  "actor": "you", "successful": True},
         ]),
]


def _match_exists(ethan: Athlete, opp: Athlete, year: int, event: str, session) -> bool:
    """Check if a match already exists between ethan and opp for a given year+event."""
    return session.execute(
        select(Match).where(
            or_(
                and_(Match.athlete_a_id == ethan.id, Match.athlete_b_id == opp.id),
                and_(Match.athlete_a_id == opp.id, Match.athlete_b_id == ethan.id),
            ),
            Match.year == year,
            Match.event == event,
        )
    ).scalar() is not None


def run() -> int:
    with db_session() as session:
        # Build normalized-name index.
        by_norm = {
            _normalize_name(a.name): a
            for a in session.execute(select(Athlete)).scalars()
        }

        def resolve(name: str, source: str = "opponent") -> Athlete:
            key = _normalize_name(name)
            ath = by_norm.get(key)
            if ath is None:
                aid = upsert_athlete(name=name, belt="black", source=source, session=session)
                ath = session.get(Athlete, aid)
                assert ath is not None
                by_norm[key] = ath
                logger.info("Created athlete: %s (source=%s)", name, source)
            return ath

        # Resolve Ethan.
        ethan = by_norm.get(_normalize_name(ATHLETE_NAME))
        if ethan is None:
            logger.error("Ethan Crelinsten not found in DB — seed leaderboard first")
            return 1
        logger.info("Ethan Crelinsten: id=%s elo=%.1f rank_elo=%s",
                     ethan.id, ethan.elo or 0, ethan.rank_elo)

        # ── Fix duplicate athlete "Ethan Krellstein" ──────────────────────────
        krell_key = _normalize_name("Ethan Krellstein")
        krell = by_norm.get(krell_key)
        if krell is not None:
            # Delete the incorrectly-entered match.
            old_matches = session.execute(
                select(Match).where(
                    or_(Match.athlete_a_id == krell.id, Match.athlete_b_id == krell.id)
                )
            ).scalars().all()
            for m in old_matches:
                session.delete(m)
                logger.info("Deleted old match: %s vs %s at %s",
                            krell.name, ethan.name, m.event or "?")
            session.flush()  # flush deletes before deleting athlete (FK constraint)
            # Delete the duplicate athlete row.
            session.delete(krell)
            by_norm.pop(krell_key, None)
            session.flush()
            logger.info("Deleted duplicate athlete: Ethan Krellstein")

        # ── Insert / skip each match ─────────────────────────────────────────
        inserted = 0
        skipped = 0

        for m in MATCHES:
            opp = resolve(m["opponent"])

            if _match_exists(ethan, opp, m["year"], m["event"], session):
                logger.info("SKIP (exists): vs %s at %s %d", opp.name, m["event"], m["year"])
                skipped += 1
                continue

            # Map sequence actor → actual athlete UUID.
            seq = []
            for e in m["sequence"]:
                entry = {k: v for k, v in e.items() if k != "actor"}
                entry["actor_id"] = ethan.id if e.get("actor") == "you" else opp.id
                seq.append(entry)

            winner_id = ethan.id if m["won"] else (opp.id if not m["won"] and m["win_type"] != "DRAW" else None)

            register_match(
                athlete_a_id=ethan.id,
                athlete_b_id=opp.id,
                winner_id=winner_id,
                win_type=m["win_type"],
                submission=m.get("submission"),
                event=m["event"],
                year=m["year"],
                weight_class=m.get("weight_class"),
                stage=m.get("stage"),
                sequence=seq,
                created_by=None,
                session=session,
                status="final",
            )
            logger.info("INSERT: vs %-25s | %s %d | %s",
                        opp.name, m["event"], m["year"], m["win_type"])
            inserted += 1

        session.flush()

        # ── Replay Ethan's graph ─────────────────────────────────────────────
        if inserted > 0 or skipped > 0:
            snaps = replay_and_persist_athlete(ethan, session)
            logger.info("Graph replayed: new ELO = %.1f (was %.1f), %d matches in timeline",
                        ethan.elo or 0, ethan.elo or 0, len(snaps))

        logger.info("Done: %d inserted, %d skipped", inserted, skipped)
        return 0


if __name__ == "__main__":
    sys.exit(run())
