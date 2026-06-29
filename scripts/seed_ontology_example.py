#!/usr/bin/env python
"""Author one curated strategic System end-to-end (Slice 1 acceptance / RF04-06, RF20, DS-*).

Seeds a representative **Back Attack System** — principles, a dilemma, the six-stage milestone
ladder, two athlete implementations, and per-position Decision Space on a few real positions —
so the whole author → store → export → seed path can be exercised before the ``web/`` admin UI
exists. Idempotent: keyed by stable slugs / ``node_key`` (upsert), so re-running is safe.

    uv run python -m scripts.seed_ontology_example            # write to DB
    uv run python -m scripts.seed_ontology_example --dry-run  # report, no writes
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

SYSTEM_KEY = "back-attack-system"

PRINCIPLES: list[dict[str, Any]] = [
    {
        "key": "control-before-submission",
        "name": "Control before submission",
        "type": "control",
        "description": "Secure positional control (hooks, body triangle) before the strangle.",
    },
    {
        "key": "remove-the-hands",
        "name": "Remove the defending hands",
        "type": "control",
        "description": "Strip the opponent's hand-fighting so the strangle channel opens.",
    },
    {
        "key": "stay-behind-the-shoulders",
        "name": "Stay behind the shoulder line",
        "type": "pressure",
        "description": "Keep your chest behind the opponent's shoulders to deny the escape turn.",
    },
]

DILEMMAS: list[dict[str, Any]] = [
    {
        "key": "strangle-vs-position-fork",
        "name": "Strangle vs. hold position",
        "situation": "Back taken, opponent defends the neck two-on-one.",
        "option_a": "Commit to the strangle (risk losing the back if it fails).",
        "option_b": "Maintain control and wait for the hands to tire (slower, safer).",
        "principle_keys": ["control-before-submission", "remove-the-hands"],
    },
]

REACTIONS: list[dict[str, Any]] = [
    {"key": "hand-fight-defense", "name": "Two-on-one hand fight",
     "description": "Opponent grips the attacking arm to delay the strangle."},
    {"key": "escape-turn-in", "name": "Turn in to guard",
     "description": "Opponent turns into the attacker to recover guard."},
]

# kind, ordinal, name, description, ds_objective (DS-11) — the generic six-stage ladder.
MILESTONES: list[dict[str, Any]] = [
    {"kind": "conceptual", "ordinal": 0, "name": "Understand the back hierarchy",
     "description": "Why back control sits atop the positional hierarchy.", "ds_objective": None},
    {"kind": "execution", "ordinal": 1, "name": "Maintain seatbelt + hooks",
     "description": "Hold the position against a non-resisting partner.",
     "ds_objective": {"hold_seconds": 20}},
    {"kind": "dilemma", "ordinal": 2, "name": "Resolve strangle-vs-position",
     "description": "Choose correctly under the strangle/position fork.",
     "ds_objective": {"dilemma_key": "strangle-vs-position-fork"}},
    {"kind": "chaining", "ordinal": 3, "name": "Chain RNC ↔ body triangle",
     "description": "Flow between strangle attempts and re-control.", "ds_objective": None},
    {"kind": "resistance", "ordinal": 4, "name": "Finish on a resisting opponent",
     "description": "Land the strangle against full hand-fighting.",
     "ds_objective": {"reduce_defender_below": 0.15}},
    {"kind": "recovery", "ordinal": 5, "name": "Recover a slipping back",
     "description": "Re-take the back after losing a hook.", "ds_objective": None},
]

# node_key → Decision Space (DS-01/04). Real positions present in the library.
POSITION_DS: dict[str, dict[str, Any]] = {
    "back control": {
        "offensive": [
            {"id": "rnc", "description": "Rear naked choke", "type": "submission", "weight": 0.8},
            {"id": "body-triangle", "description": "Lock the body triangle", "type": "control",
             "weight": 0.6},
        ],
        "defensive": [
            {"id": "hand-fight", "description": "Two-on-one defend the neck", "type": "defense",
             "weight": 0.5},
        ],
        "expected_reactions": ["hand-fight-defense", "escape-turn-in"],
        "constraints": ["stay-behind-the-shoulders"],
        "attacker_score": 0.82,
        "defender_score": 0.22,
    },
    "rear naked choke": {
        "offensive": [],
        "defensive": [],
        "expected_reactions": ["hand-fight-defense"],
        "constraints": [],
        "attacker_score": 0.9,
        "defender_score": 0.08,
    },
    "mount": {
        "offensive": [
            {"id": "back-take", "description": "Take the back off mount", "type": "control",
             "weight": 0.7},
        ],
        "defensive": [],
        "expected_reactions": ["escape-turn-in"],
        "constraints": [],
        "attacker_score": 0.8,
        "defender_score": 0.25,
    },
}

SYSTEM: dict[str, Any] = {
    "key": SYSTEM_KEY,
    "name": "Back Attack System",
    "objective": "Convert dominant control into a high-percentage strangle while denying escape.",
    "entry_positions": ["back control", "mount", "back take"],
    "activation_conditions": ["seatbelt secured", "at least one hook in"],
    "expected_opponent_responses": ["hand-fight-defense", "escape-turn-in"],
    "alternative_paths": ["body lock pass to mount", "back triangle"],
    "mastery_criteria": ["finish RNC on a resisting opponent", "recover the back after a slip"],
    "ds_progression": [
        {"stage": "execution", "attacker_score": 0.8, "defender_score": 0.25},
        {"stage": "resistance", "attacker_score": 0.88, "defender_score": 0.12},
    ],
    "ds_mode": "expert",
    "principle_keys": [p["key"] for p in PRINCIPLES],
    "dilemma_keys": [d["key"] for d in DILEMMAS],
}

# Athlete name → implementation overlay (deltas only; no knowledge duplication).
IMPLEMENTATIONS: dict[str, dict[str, Any]] = {
    "Gordon Ryan": {
        "name": "Gordon Ryan — body-triangle pressure",
        "overrides": {
            "node_priorities": {"back control": 1.0, "rear naked choke": 0.9},
            "preferred_sequences": [["back control", "rear naked choke"]],
            "notes": "Body triangle first; strangle only once the hands are removed.",
        },
    },
    "Craig Jones": {
        "name": "Craig Jones — strangle-or-leg fork",
        "overrides": {
            "node_priorities": {"back control": 0.8},
            "notes": "Threatens the back but bails to leg entanglements off the turn-in.",
        },
    },
}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Seed one curated System (Back Attack)")
    ap.add_argument("--dry-run", action="store_true", help="report, no DB writes")
    args = ap.parse_args()

    if args.dry_run:
        logger.info("DRY RUN: would seed system %r with %d principles, %d dilemma(s), "
                    "%d milestones, %d implementations, DS on %d positions.",
                    SYSTEM_KEY, len(PRINCIPLES), len(DILEMMAS), len(MILESTONES),
                    len(IMPLEMENTATIONS), len(POSITION_DS))
        return 0

    from sqlalchemy import select, update

    from db.base import db_session
    from db.models import (
        Athlete,
        Dilemma,
        Milestone,
        Principle,
        Reaction,
        System,
        SystemDilemma,
        SystemImplementation,
        SystemPrinciple,
        TechniqueNode,
    )

    def upsert(session: Any, model: Any, key: str, **fields: Any) -> Any:
        row = session.execute(select(model).where(model.key == key)).scalar_one_or_none()
        if row is None:
            row = model(key=key, **fields)
            session.add(row)
            session.flush()
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        return row

    with db_session() as session:
        principles = {p["key"]: upsert(session, Principle, **p) for p in PRINCIPLES}
        dilemmas = {d["key"]: upsert(session, Dilemma, **d) for d in DILEMMAS}
        for r in REACTIONS:
            upsert(session, Reaction, **r)

        sys_fields = {k: v for k, v in SYSTEM.items()
                      if k not in ("key", "principle_keys", "dilemma_keys")}
        system = upsert(session, System, SYSTEM_KEY, **sys_fields)

        # Reset + re-attach join rows and milestones (idempotent).
        session.query(SystemPrinciple).filter_by(system_id=system.id).delete()
        session.query(SystemDilemma).filter_by(system_id=system.id).delete()
        session.query(Milestone).filter_by(system_id=system.id).delete()
        session.flush()
        for pk in SYSTEM["principle_keys"]:
            session.add(SystemPrinciple(system_id=system.id, principle_id=principles[pk].id))
        for dk in SYSTEM["dilemma_keys"]:
            session.add(SystemDilemma(system_id=system.id, dilemma_id=dilemmas[dk].id))
        for m in MILESTONES:
            session.add(Milestone(system_id=system.id, **m))

        # Per-athlete implementations (FK to real athletes; unique on system+athlete).
        for name, impl in IMPLEMENTATIONS.items():
            ath = session.execute(
                select(Athlete).where(Athlete.name == name)
            ).scalar_one_or_none()
            if ath is None:
                logger.warning("Athlete %r not found — skipping implementation", name)
                continue
            existing = session.execute(
                select(SystemImplementation).where(
                    SystemImplementation.system_id == system.id,
                    SystemImplementation.athlete_id == ath.id,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(SystemImplementation(
                    system_id=system.id, athlete_id=ath.id,
                    name=impl["name"], overrides=impl["overrides"]))
            else:
                existing.name = impl["name"]
                existing.overrides = impl["overrides"]

        # Per-position Decision Space (DS-01/04) on real library nodes.
        ds_count = 0
        for node_key, ds in POSITION_DS.items():
            res = session.execute(
                update(TechniqueNode)
                .where(TechniqueNode.node_key == node_key)
                .values(decision_space=ds, ds_mode="expert")
            )
            ds_count += res.rowcount or 0

        logger.info("Seeded %r: %d principles, %d dilemma(s), %d milestones, %d impls, "
                    "DS on %d/%d positions matched.", SYSTEM_KEY, len(PRINCIPLES),
                    len(DILEMMAS), len(MILESTONES), len(IMPLEMENTATIONS), ds_count,
                    len(POSITION_DS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
