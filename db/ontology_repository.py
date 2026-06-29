"""Ontology CRUD helpers for the admin authoring UI (RF04-06, RF20, DS-01/04).

Thin upsert/delete functions over the strategic-ontology models, mirroring the style of
``db.repository`` (id-returning upserts, ``Session`` passed in, commit handled by the caller's
``db_session``). Systems/principles/dilemmas are keyed by stable slug; milestones and
implementations hang off a system; per-position Decision Space lives on ``technique_nodes``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from analysis.names import _normalize_name
from db.models import (
    Archetype,
    Dilemma,
    Milestone,
    Principle,
    System,
    SystemDilemma,
    SystemImplementation,
    SystemPrinciple,
    TechniqueNode,
)


def slugify(text: str) -> str:
    """Stable key from a display name (same normalizer as node_key / athlete slug)."""
    return _normalize_name(text).replace(" ", "-")


def _upsert_keyed(session: Session, model: Any, key: str, **fields: Any) -> str:
    row = session.execute(select(model).where(model.key == key)).scalar_one_or_none()
    if row is None:
        row = model(key=key, **fields)
        session.add(row)
        session.flush()
    else:
        for k, v in fields.items():
            setattr(row, k, v)
    return str(row.id)


def upsert_principle(
    *, key: str, name: str, description: str | None, type: str | None, session: Session
) -> str:
    return _upsert_keyed(
        session, Principle, key, name=name, description=description, type=type
    )


def upsert_dilemma(
    *,
    key: str,
    name: str,
    situation: str | None,
    option_a: str | None,
    option_b: str | None,
    principle_keys: list[str],
    session: Session,
) -> str:
    return _upsert_keyed(
        session, Dilemma, key, name=name, situation=situation, option_a=option_a,
        option_b=option_b, principle_keys=principle_keys,
    )


def upsert_system(
    *,
    key: str,
    name: str,
    objective: str | None,
    entry_positions: list[str],
    activation_conditions: list[str],
    expected_opponent_responses: list[str],
    alternative_paths: list[str],
    mastery_criteria: list[str],
    ds_mode: str,
    session: Session,
) -> str:
    return _upsert_keyed(
        session, System, key, name=name, objective=objective,
        entry_positions=entry_positions, activation_conditions=activation_conditions,
        expected_opponent_responses=expected_opponent_responses,
        alternative_paths=alternative_paths, mastery_criteria=mastery_criteria,
        ds_mode=ds_mode or "expert",
    )


def set_system_principles(system_id: str, principle_ids: list[str], session: Session) -> None:
    session.execute(delete(SystemPrinciple).where(SystemPrinciple.system_id == system_id))
    for pid in principle_ids:
        session.add(SystemPrinciple(system_id=system_id, principle_id=pid))


def set_system_dilemmas(system_id: str, dilemma_ids: list[str], session: Session) -> None:
    session.execute(delete(SystemDilemma).where(SystemDilemma.system_id == system_id))
    for did in dilemma_ids:
        session.add(SystemDilemma(system_id=system_id, dilemma_id=did))


def add_milestone(
    *,
    system_id: str,
    ordinal: int,
    kind: str,
    name: str,
    description: str | None,
    session: Session,
) -> str:
    row = Milestone(
        system_id=system_id, ordinal=ordinal, kind=kind, name=name, description=description
    )
    session.add(row)
    session.flush()
    return str(row.id)


def delete_milestone(milestone_id: str, session: Session) -> None:
    session.execute(delete(Milestone).where(Milestone.id == milestone_id))


def upsert_implementation(
    *, system_id: str, athlete_id: str, name: str | None, notes: str, session: Session
) -> str:
    row = session.execute(
        select(SystemImplementation).where(
            SystemImplementation.system_id == system_id,
            SystemImplementation.athlete_id == athlete_id,
        )
    ).scalar_one_or_none()
    overrides = {"notes": notes} if notes else {}
    if row is None:
        row = SystemImplementation(
            system_id=system_id, athlete_id=athlete_id, name=name, overrides=overrides
        )
        session.add(row)
        session.flush()
    else:
        row.name = name
        row.overrides = overrides
    return str(row.id)


def delete_implementation(impl_id: str, session: Session) -> None:
    session.execute(delete(SystemImplementation).where(SystemImplementation.id == impl_id))


def set_position_decision_space(
    *,
    node_key: str,
    attacker_score: float,
    defender_score: float,
    expected_reactions: list[str],
    constraints: list[str],
    session: Session,
) -> bool:
    """Author DS on an existing library position. Returns False if node_key is unknown."""
    node = session.execute(
        select(TechniqueNode).where(TechniqueNode.node_key == node_key)
    ).scalar_one_or_none()
    if node is None:
        return False
    node.decision_space = {
        "offensive": [],
        "defensive": [],
        "expected_reactions": expected_reactions,
        "constraints": constraints,
        "attacker_score": attacker_score,
        "defender_score": defender_score,
    }
    node.ds_mode = "expert"
    return True


def delete_system(system_id: str, session: Session) -> None:
    # Milestones / implementations / join rows cascade via FK ondelete=CASCADE.
    session.execute(delete(System).where(System.id == system_id))


def delete_principle(principle_id: str, session: Session) -> None:
    session.execute(delete(Principle).where(Principle.id == principle_id))


def delete_dilemma(dilemma_id: str, session: Session) -> None:
    session.execute(delete(Dilemma).where(Dilemma.id == dilemma_id))


def upsert_target_archetype(
    *, name: str, description: str | None, signature_types: list[str], session: Session
) -> str:
    """Author-defined (kind='target') archetype, keyed by slug. Survives recompute (RF01)."""
    return _upsert_keyed(
        session, Archetype, f"target-{slugify(name)}",
        name=name, kind="target", description=description, signature_types=signature_types,
    )


def delete_archetype(archetype_id: str, session: Session) -> None:
    """Delete a target archetype by id (emergent ones are managed by the recompute pipeline)."""
    session.execute(
        delete(Archetype).where(Archetype.id == int(archetype_id), Archetype.kind == "target")
    )
