#!/usr/bin/env python
"""Insert Arman Tsarukyan + Dan Hooker careers (reviewed/cleaned) as global matches.

Source data was one mixed dump keyed by (opponent, year). It was reviewed against the
fighters' real UFC records and SPLIT into two careers, CLEANED, and result-filled:
  - dropped every ``Round End`` / referee ``reset`` event (not a technique; invalid type)
  - retyped takedowns ``transition`` -> ``takedown`` (kept ``Sprawl to Top Position`` as
    transition)
  - win/loss + win_type filled from records where confident; UNKNOWN results stay neutral
    (``won=None`` -> ``winner_id`` NULL -> 0.5 score), never fabricated
  - corrected years: Gamrot 2024->2022, Gilbert Burns 2020->2018
  - MMA has no POINTS; KO/TKO/decision finishes map to win_type "DECISION", submissions to
    "SUBMISSION" (win_type only scales K / flags submissions; W/L is carried by winner_id)

Empty-sequence matches (Vick, Burns, Dariush) add no graph nodes — their result is
recorded but does not move the graph ELO (the engine skips matches with no own-side nodes).

Idempotent: deletes each subject's existing matches before reinserting. Both subjects and
every opponent are replayed (the double pass), so opponents get a graph from their side too.

Usage:
    uv run python -m scripts.insert_mma_matches            # write to DB
    uv run python -m scripts.insert_mma_matches --dry-run  # parse + report only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Each match: opponent, year, event, weight_class, win_type, won, submission, sequence.
# won: True=subject won, False=subject lost, None=unknown (neutral, winner NULL).
# Sequences are already cleaned (no Round End / referee; takedowns typed "takedown").
CAREERS: dict[str, list[dict[str, Any]]] = {
    "Arman Tsarukyan": [
        {
            "opponent": "Charles Oliveira", "year": 2024, "event": "UFC 300",
            "weight_class": "155", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Back Take", "type": "control", "actor": "opponent"},
                {"label": "Mount", "type": "control", "actor": "opponent"},
                {"label": "Sweep", "type": "sweep", "actor": "you", "successful": True},
                {"label": "Closed Guard", "type": "guard", "actor": "opponent"},
                {"label": "Escape to Standing", "type": "escape", "actor": "opponent"},
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Closed Guard", "type": "guard", "actor": "opponent"},
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Side Control", "type": "control", "actor": "you"},
                {"label": "Back Take", "type": "control", "actor": "you"},
                {"label": "Guillotine Attempt", "type": "submission", "actor": "opponent",
                 "successful": False},
            ],
        },
        {
            "opponent": "Mateusz Gamrot", "year": 2022, "event": "UFC Fight Night",
            "weight_class": "155", "win_type": "DECISION", "won": False, "submission": None,
            "sequence": [
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
                {"label": "Arm Triangle Attempt", "type": "submission", "actor": "opponent",
                 "successful": False},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
            ],
        },
        {
            "opponent": "Olivier Aubin-Mercier", "year": 2019, "event": "UFC Copenhagen",
            "weight_class": "155", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Takedown Attempt", "type": "takedown", "actor": "you",
                 "successful": False},
                {"label": "Sweep", "type": "sweep", "actor": "opponent", "successful": True},
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Trip Takedown", "type": "takedown", "actor": "opponent",
                 "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
                {"label": "Sprawl to Top Position", "type": "transition", "actor": "you",
                 "successful": True},
                {"label": "Arm Triangle Attempt", "type": "submission", "actor": "opponent",
                 "successful": False},
            ],
        },
        {
            "opponent": "Christos Giagos", "year": 2021, "event": None,
            "weight_class": "155", "win_type": None, "won": None, "submission": None,
            "sequence": [
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
            ],
        },
        {
            "opponent": "Joel Alvarez", "year": 2022, "event": "UFC Fight Night",
            "weight_class": "155", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Closed Guard", "type": "guard", "actor": "opponent"},
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Side Control", "type": "control", "actor": "you"},
                {"label": "Darce Choke Attempt", "type": "submission", "actor": "you",
                 "successful": False},
            ],
        },
        {
            "opponent": "Joaquim Silva", "year": 2023, "event": None,
            "weight_class": "155", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Back Take", "type": "control", "actor": "you"},
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Takedown", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Back Take", "type": "control", "actor": "you"},
            ],
        },
        {
            "opponent": "Beneil Dariush", "year": 2023, "event": "UFC 296",
            "weight_class": "155", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [],
        },
    ],
    "Dan Hooker": [
        {
            "opponent": "Ian Entwistle", "year": 2014, "event": None,
            "weight_class": "145", "win_type": None, "won": None, "submission": None,
            "sequence": [
                {"label": "Scissor Takedown", "type": "takedown", "actor": "opponent",
                 "successful": True},
                {"label": "Heel Hook Attempt", "type": "submission", "actor": "opponent",
                 "successful": False},
            ],
        },
        {
            "opponent": "Claudio Puelles", "year": 2022, "event": None,
            "weight_class": "155", "win_type": None, "won": None, "submission": None,
            "sequence": [
                {"label": "Heel Hook Attempt", "type": "submission", "actor": "opponent",
                 "successful": False},
                {"label": "Sweep", "type": "sweep", "actor": "you", "successful": True},
            ],
        },
        {
            "opponent": "James Vick", "year": 2019, "event": "UFC Fight Night",
            "weight_class": "155", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [],
        },
        {
            "opponent": "Gilbert Burns", "year": 2018, "event": None,
            "weight_class": "155", "win_type": None, "won": False, "submission": None,
            "sequence": [],
        },
    ],
    # Reviewed from a mixed dump: subject = the non-opponent actor. Dropped rows whose
    # actor was "Zahabi" (a coach, not a fighter) and matchups that never happened
    # (Aoriqileng, O'Malley–Aldo, JDM–Randy Brown, Prates–Leon Edwards); the duplicate
    # ("Marlon Vera", 2024) key collapsed to O'Malley's real UFC 299 win.
    "Sean O'Malley": [
        {
            "opponent": "Petr Yan", "year": 2022, "event": "UFC 280",
            "weight_class": "135", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Double Leg Takedown", "type": "takedown", "actor": "opponent",
                 "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Double Leg Takedown", "type": "takedown", "actor": "opponent",
                 "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown Attempt", "type": "takedown", "actor": "you",
                 "successful": False},
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
                {"label": "Closed Guard", "type": "guard", "actor": "you"},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
            ],
        },
        {
            "opponent": "Raulian Paiva", "year": 2021, "event": "UFC 269",
            "weight_class": "135", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [],
        },
        {
            # UFC 276 was a No Contest (accidental eye poke) — no winner → neutral.
            "opponent": "Pedro Munhoz", "year": 2022, "event": "UFC 276",
            "weight_class": "135", "win_type": None, "won": None, "submission": None,
            "sequence": [
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
            ],
        },
        {
            "opponent": "Marlon Vera", "year": 2024, "event": "UFC 299",
            "weight_class": "135", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Trip", "type": "takedown", "actor": "you", "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "opponent"},
            ],
        },
        {
            "opponent": "Thomas Almeida", "year": 2021, "event": "UFC 260",
            "weight_class": "135", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [],
        },
        {
            "opponent": "Aljamain Sterling", "year": 2023, "event": "UFC 292",
            "weight_class": "135", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
            ],
        },
    ],
    "Jack Della Maddalena": [
        {
            "opponent": "Belal Muhammad", "year": 2025, "event": "UFC 315",
            "weight_class": "170", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
                {"label": "Back Take", "type": "control", "actor": "opponent"},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown", "type": "takedown", "actor": "opponent", "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
            ],
        },
        {
            "opponent": "Ramazan Emeev", "year": 2023, "event": "UFC 284",
            "weight_class": "170", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Guillotine Choke Attempt", "type": "submission", "actor": "opponent",
                 "successful": False},
            ],
        },
        {
            "opponent": "Danny Roberts", "year": 2022, "event": None,
            "weight_class": "170", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
            ],
        },
    ],
    "Carlos Prates": [
        {
            "opponent": "Geoff Neal", "year": 2024, "event": None,
            "weight_class": "170", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [],
        },
        {
            "opponent": "Neil Magny", "year": 2024, "event": None,
            "weight_class": "170", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [
                {"label": "Single Leg Takedown", "type": "takedown", "actor": "opponent",
                 "successful": True},
                {"label": "Escape to Standing", "type": "escape", "actor": "you"},
                {"label": "Takedown Attempt", "type": "takedown", "actor": "opponent",
                 "successful": False},
            ],
        },
        {
            "opponent": "Li Jingliang", "year": 2024, "event": None,
            "weight_class": "170", "win_type": "DECISION", "won": True, "submission": None,
            "sequence": [],
        },
    ],
}


def _winner_id(m: dict[str, Any], subj_id: str, opp_id: str) -> str | None:
    """Subject id if won, opponent id if lost, None for draw/unknown (won is None)."""
    if (m.get("win_type") or "").upper() == "DRAW" or m.get("won") is None:
        return None
    return subj_id if m["won"] else opp_id


def run(dry_run: bool = False) -> int:
    total = sum(len(v) for v in CAREERS.values())
    logger.info("MMA import: %d matches across %d fighters", total, len(CAREERS))
    if dry_run:
        for fighter, matches in CAREERS.items():
            res = {True: "W", False: "L", None: "?"}
            line = ", ".join(f"{m['opponent']}({m['year']},{res[m['won']]})" for m in matches)
            logger.info("  %s: %s", fighter, line)
        return 0

    from sqlalchemy import delete, or_, select

    from analysis.names import _normalize_name
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
        for fighter, matches in CAREERS.items():
            subj = resolve(fighter, source="manual")
            participants.add(subj.id)
            # Idempotency: clear this subject's matches (either side) before reinserting.
            session.execute(
                delete(Match).where(
                    or_(Match.athlete_a_id == subj.id, Match.athlete_b_id == subj.id)
                )
            )
            session.flush()
            for m in matches:
                opp = resolve(m["opponent"], source="opponent")
                participants.add(opp.id)
                seq = [
                    {**{k: v for k, v in e.items() if k != "actor"},
                     "actor_id": subj.id if e.get("actor") == "you" else opp.id}
                    for e in m["sequence"]
                ]
                register_match(
                    subj.id, opp.id,
                    winner_id=_winner_id(m, subj.id, opp.id),
                    win_type=m["win_type"],
                    submission=m["submission"],
                    event=m["event"],
                    year=m["year"],
                    weight_class=m["weight_class"],
                    stage=None,
                    sequence=seq,
                    created_by=None,
                    session=session,
                )

        for aid in participants:
            athlete = session.get(Athlete, aid)
            if athlete is not None:
                replay_and_persist_athlete(athlete, session)

        for fighter in CAREERS:
            a = by_norm[_normalize_name(fighter)]
            logger.info("  %s graph ELO %.1f", fighter, a.elo)
        logger.info("Inserted %d matches; replayed %d athletes", total, len(participants))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Insert Arman Tsarukyan + Dan Hooker careers")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    return run(dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
