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
        "archetypes": [],               # RF01 target + emergent catalog
        "athlete_profiles": [],         # per-athlete emergent archetype + signature deviance
    }


def build_ontology_seed() -> dict[str, Any]:
    """Assemble the seed from the DB. Returns an empty-but-valid seed if no DB."""
    try:
        from sqlalchemy import select

        from analysis.deviance import (
            TYPES,
            node_population_stats,
            signature_nodes,
            type_deviance_vector,
        )
        from db.base import db_session
        from db.models import (
            Archetype,
            Athlete,
            Dilemma,
            Graph,
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
        from db.repository import graphs_for_clustering
        from export.match_breakdown import slugify
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

            # id → stable key maps so milestones/implementations reference systems/athletes
            # by slug (the cross-module contract), never by environment-specific DB UUIDs.
            system_key_by_id: dict[str, str] = {}
            for s in session.execute(select(System)).scalars():
                system_key_by_id[s.id] = s.key
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
                    "system_key": system_key_by_id.get(m.system_id, ""),
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

            impls = list(session.execute(select(SystemImplementation)).scalars())
            # Resolve athlete UUIDs → stable name slugs (the app's athlete identity key).
            athlete_keys: dict[str, str] = {}
            impl_athlete_ids = {impl.athlete_id for impl in impls}
            if impl_athlete_ids:
                for ath in session.execute(
                    select(Athlete).where(Athlete.id.in_(impl_athlete_ids))
                ).scalars():
                    athlete_keys[ath.id] = slugify(ath.name)
            seed["implementations"] = [
                {
                    "system_key": system_key_by_id.get(impl.system_id, ""),
                    "athlete_key": athlete_keys.get(impl.athlete_id, ""),
                    "name": impl.name,
                    "overrides": impl.overrides or {},
                    "milestone_overrides": impl.milestone_overrides or [],
                }
                for impl in impls
            ]

            # Per-position Decision Space (only positions that have a curated DS).
            for n in session.execute(
                select(TechniqueNode.node_key, TechniqueNode.decision_space).where(
                    TechniqueNode.decision_space.isnot(None)
                )
            ).all():
                seed["position_decision_space"][n[0]] = n[1]

            # ── Archetypes (RF01: target + emergent) ──────────────────────────
            archetypes = list(session.execute(select(Archetype)).scalars())
            arch_key_by_id = {a.id: a.key for a in archetypes}
            seed["archetypes"] = [
                {
                    "key": a.key,
                    "name": a.name,
                    "kind": a.kind,
                    "description": a.description,
                    "signature_types": a.signature_types or [],
                }
                for a in archetypes
                if a.key  # skip any legacy rows without a stable key
            ]

            # ── Per-athlete profiles: emergent archetype + proportional deviance ──
            rows = [
                (gid, nodes)
                for gid, nodes in graphs_for_clustering(session, owner_kind="athlete")
                if len(nodes) >= 3
            ]
            if rows:
                by_key, by_type = node_population_stats(rows)
                graph_owner = {
                    g.id: (g.owner_id, g.archetype_id)
                    for g in session.execute(
                        select(Graph).where(Graph.owner_kind == "athlete")
                    ).scalars()
                }
                owner_ids = {graph_owner[gid][0] for gid, _ in rows if gid in graph_owner}
                ath_name = {
                    a.id: a.name
                    for a in session.execute(
                        select(Athlete).where(Athlete.id.in_(owner_ids))
                    ).scalars()
                }
                for gid, nodes in rows:
                    owner_id, arch_id = graph_owner.get(gid, (None, None))
                    name = ath_name.get(owner_id) if owner_id else None
                    if not name:
                        continue
                    devs = type_deviance_vector(nodes, by_key, by_type)
                    seed["athlete_profiles"].append(
                        {
                            "athlete_key": slugify(name),
                            "name": name,
                            "emergent_archetype_key": (
                                arch_key_by_id.get(arch_id) if arch_id is not None else None
                            ),
                            "signature_nodes": [
                                {"node_key": k, "z": round(z, 3)}
                                for k, z in signature_nodes(nodes, by_key, by_type)
                            ],
                            "type_deviance": {
                                t: round(float(z), 3) for t, z in zip(TYPES, devs)
                            },
                        }
                    )
    except Exception as exc:
        logger.warning("Ontology seed: read failed (%s) — emitting empty seed", exc)
        return _empty_seed()

    return seed


def validate_seed(seed: dict[str, Any]) -> list[str]:
    """Referential-integrity check on a seed (cross-module contract guard, F1).

    Every milestone/implementation must reference a System present in the same seed by its
    stable ``system_key`` (never a DB UUID), and implementations must carry an ``athlete_key``.
    Returns a list of human-readable problems; empty means the seed is internally consistent.
    """
    problems: list[str] = []
    system_keys = {s.get("key") for s in seed.get("systems", [])}
    for m in seed.get("milestones", []):
        if m.get("system_key") not in system_keys:
            problems.append(
                f"milestone {m.get('name')!r} → unknown system_key {m.get('system_key')!r}"
            )
    for impl in seed.get("implementations", []):
        name, skey = impl.get("name"), impl.get("system_key")
        if skey not in system_keys:
            problems.append(f"implementation {name!r} → unknown system_key {skey!r}")
        if not impl.get("athlete_key"):
            problems.append(f"implementation {name!r} → missing athlete_key")
    archetype_keys = {a.get("key") for a in seed.get("archetypes", [])}
    for p in seed.get("athlete_profiles", []):
        ak = p.get("emergent_archetype_key")
        if ak is not None and ak not in archetype_keys:
            problems.append(
                f"athlete_profile {p.get('name')!r} → unknown archetype_key {ak!r}"
            )
    return problems


def export_ontology_seed(out_dir: Path | None = None) -> Path:
    """Build + write ``ontology_seed.json``. Returns the written path."""
    out_dir = out_dir or PROCESSED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = build_ontology_seed()
    for problem in validate_seed(seed):
        logger.warning("Ontology seed integrity: %s", problem)
    path = out_dir / "ontology_seed.json"
    with open(path, "w") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
    logger.info(
        "Ontology seed → %s (%d systems, %d principles, %d dilemmas, %d milestones, %d impls, "
        "%d archetypes, %d athlete profiles)",
        path,
        len(seed["systems"]),
        len(seed["principles"]),
        len(seed["dilemmas"]),
        len(seed["milestones"]),
        len(seed["implementations"]),
        len(seed["archetypes"]),
        len(seed["athlete_profiles"]),
    )
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    export_ontology_seed()
