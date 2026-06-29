"""Ontology seed export — the canonical strategic-knowledge bundle for the app.

Reads the curated ontology (RF04-06, RF20) + Decision-Space defaults (DS-01/04/16) from
the DB and emits one self-contained JSON the offline-first app bundles + loads on cold
start (mirrors ``export/tech_library.py`` for the technique catalog). This is the producer
half of the ``ontology_seed.json`` cross-module contract — the app consumer keys it under
``@grapplingarch:ontology`` (``src/utils/storage/ontologyStorage.ts``).

DB-optional: if ``DATABASE_URL`` is unset or the read fails, emits an empty-but-valid seed
so the export still runs offline (same guard as ``tech_library._load_match_techniques``).

Output:
    data/processed/ontology_seed.json

Usage:
    uv run python -m export.ontology
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pipelines.etl import PROCESSED_DIR

logger = logging.getLogger(__name__)

SEED_VERSION = "1.0.0"


def _empty_seed() -> dict[str, Any]:
    return {
        "version": SEED_VERSION,
        "principles": [],
        "intents": [],
        "reactions": [],
        "dilemmas": [],
        "systems": [],
        "milestones": [],
        "implementations": [],
        "position_decision_space": {},  # node_key → decision_space (DS-01/04)
    }


def build_ontology_seed() -> dict[str, Any]:
    """Assemble the seed from the DB. Returns an empty-but-valid seed if no DB."""
    try:
        from sqlalchemy import select

        from db.base import db_session
        from db.models import (
            Dilemma,
            Intent,
            Milestone,
            Principle,
            Reaction,
            System,
            SystemDilemma,
            SystemImplementation,
            SystemPrinciple,
            TechniqueNode,
        )
    except Exception as exc:  # import/config failure → offline-safe empty seed
        logger.info("Ontology seed: DB unavailable (%s) — emitting empty seed", exc)
        return _empty_seed()

    seed = _empty_seed()
    try:
        with db_session() as session:
            seed["principles"] = [
                {"key": p.key, "name": p.name, "description": p.description, "type": p.type}
                for p in session.execute(select(Principle)).scalars()
            ]
            seed["intents"] = [
                {"key": i.key, "name": i.name, "description": i.description}
                for i in session.execute(select(Intent)).scalars()
            ]
            seed["reactions"] = [
                {"key": r.key, "name": r.name, "description": r.description}
                for r in session.execute(select(Reaction)).scalars()
            ]
            seed["dilemmas"] = [
                {
                    "key": d.key,
                    "name": d.name,
                    "situation": d.situation,
                    "option_a": d.option_a,
                    "option_b": d.option_b,
                    "principle_keys": d.principle_keys or [],
                }
                for d in session.execute(select(Dilemma)).scalars()
            ]

            # System → its principle/dilemma keys (resolved through the join tables).
            sys_principle_keys: dict[str, list[str]] = {}
            for sp in session.execute(
                select(SystemPrinciple.system_id, Principle.key).join(
                    Principle, Principle.id == SystemPrinciple.principle_id
                )
            ).all():
                sys_principle_keys.setdefault(sp[0], []).append(sp[1])
            sys_dilemma_keys: dict[str, list[str]] = {}
            for sd in session.execute(
                select(SystemDilemma.system_id, Dilemma.key).join(
                    Dilemma, Dilemma.id == SystemDilemma.dilemma_id
                )
            ).all():
                sys_dilemma_keys.setdefault(sd[0], []).append(sd[1])

            for s in session.execute(select(System)).scalars():
                seed["systems"].append(
                    {
                        "key": s.key,
                        "name": s.name,
                        "objective": s.objective,
                        "entry_positions": s.entry_positions or [],
                        "activation_conditions": s.activation_conditions or [],
                        "expected_opponent_responses": s.expected_opponent_responses or [],
                        "alternative_paths": s.alternative_paths or [],
                        "mastery_criteria": s.mastery_criteria or [],
                        "ds_progression": s.ds_progression or [],
                        "ds_mode": s.ds_mode,
                        "principle_keys": sys_principle_keys.get(s.id, []),
                        "dilemma_keys": sys_dilemma_keys.get(s.id, []),
                    }
                )

            seed["milestones"] = [
                {
                    "system_id": m.system_id,
                    "ordinal": m.ordinal,
                    "kind": m.kind,
                    "name": m.name,
                    "description": m.description,
                    "ds_objective": m.ds_objective,
                }
                for m in session.execute(
                    select(Milestone).order_by(Milestone.system_id, Milestone.ordinal)
                ).scalars()
            ]

            seed["implementations"] = [
                {
                    "system_id": impl.system_id,
                    "athlete_id": impl.athlete_id,
                    "name": impl.name,
                    "overrides": impl.overrides or {},
                    "milestone_overrides": impl.milestone_overrides or [],
                }
                for impl in session.execute(select(SystemImplementation)).scalars()
            ]

            # Per-position Decision Space (only positions that have a curated DS).
            for n in session.execute(
                select(TechniqueNode.node_key, TechniqueNode.decision_space).where(
                    TechniqueNode.decision_space.isnot(None)
                )
            ).all():
                seed["position_decision_space"][n[0]] = n[1]
    except Exception as exc:
        logger.warning("Ontology seed: read failed (%s) — emitting empty seed", exc)
        return _empty_seed()

    return seed


def export_ontology_seed(out_dir: Path | None = None) -> Path:
    """Build + write ``ontology_seed.json``. Returns the written path."""
    out_dir = out_dir or PROCESSED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = build_ontology_seed()
    path = out_dir / "ontology_seed.json"
    with open(path, "w") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
    logger.info(
        "Ontology seed → %s (%d systems, %d principles, %d dilemmas, %d milestones, %d impls)",
        path,
        len(seed["systems"]),
        len(seed["principles"]),
        len(seed["dilemmas"]),
        len(seed["milestones"]),
        len(seed["implementations"]),
    )
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    export_ontology_seed()
