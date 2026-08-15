"""Deterministic local scouting reports from versioned match dumps.

Manifest: divisions contain ``own_athlete`` and ``athletes``. Each athlete has
``name``, ``country``, ``qualification``, ``aliases`` and ``sources``. A source
names one trusted ``scripts.dumps.*`` module and may freeze rows with ``bouts``
selectors: ``{"a_name": str, "opponent": str, "year": int}``. Without selectors,
only rows in that module containing the athlete are selected. No DB/network/LLM.

    python -m analysis.scouting_report --manifest data/scouting/adcc_2026_women.json --audit
    python -m analysis.scouting_report --manifest data/scouting/adcc_2026_women.json --out reports/adcc-2026
"""

# ruff: noqa: E501 -- print-first HTML/CSS stays readable as a single embedded template.

from __future__ import annotations

import argparse
import html
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name, athlete_key, clean_athlete_name
from analysis.scouting_rulesets import (
    RulesetError,
    load_rulesets,
    project_adcc_events,
    validate_target_rulesets,
)
from analysis.scouting_tables import build_tables
from analysis.style_profile_core import (
    MIN_DOSSIER_EVENTS,
    MIN_SEQUENCE_BOUTS,
    reduce_style_events,
)

ModuleLoader = Callable[[str], Any]
SECTIONS = ("standing", "guard", "passing", "back_and_finishing")
SECTION_LABELS = {
    "standing": "Jogo em pé",
    "guard": "Jogo de guarda",
    "passing": "Passagem",
    "back_and_finishing": "Costas e finalizações",
}
STANDING_WORDS = (
    "arm drag", "body lock", "bodylock", "collar tie", "clinch", "front headlock",
    "grip", "mercy", "overhook", "snap down", "two on one", "underhook",
    "wrist control", "2 on 1",
)
PASSING_WORDS = ("headquarters", "knee cut", "passing", "pressure pass", "split squat")
BEHAVIOR_KINDS = frozenset({"posture", "initiative", "feint_reaction", "setup"})
SLUG_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z")


class ManifestError(ValueError):
    pass


class GenerationBlockedError(RuntimeError):
    pass


GenerationBlocked = GenerationBlockedError


class PdfError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    by_key: Mapping[str, str]

    def resolve(self, name: str) -> str | None:
        return self.by_key.get(athlete_key(name))


def _athletes(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    divisions = manifest.get("divisions")
    if not isinstance(divisions, list) or not divisions:
        raise ManifestError("manifest.divisions deve ser uma lista não vazia")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for division in divisions:
        if not isinstance(division, dict) or not isinstance(division.get("athletes"), list):
            raise ManifestError("cada divisão deve declarar athletes")
        slug = division.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise ManifestError(f"slug de divisão inválido: {slug!r}")
        for entry in division["athletes"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise ManifestError("cada atleta deve declarar name")
            if entry["name"] not in seen:
                entries.append(entry)
                seen.add(entry["name"])
    return entries


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"não foi possível ler {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError("manifesto deve ser um objeto JSON")
    _athletes(value)
    try:
        validate_target_rulesets(value, load_rulesets())
    except RulesetError as exc:
        raise ManifestError(str(exc)) from exc
    return value


def build_identity(manifest: Mapping[str, Any]) -> Identity:
    by_key: dict[str, str] = {}
    for entry in _athletes(manifest):
        name = str(entry["name"])
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise ManifestError(f"aliases de {name} deve ser lista de strings")
        for candidate in (name, *aliases):
            key = athlete_key(candidate)
            if key in by_key and by_key[key] != name:
                raise ManifestError(f"alias {candidate!r} é ambíguo")
            by_key[key] = name
    return Identity(by_key)


def _participants(a_name: str, bout: Mapping[str, Any]) -> tuple[str, str]:
    if " vs " in a_name:
        return tuple(part.strip() for part in a_name.split(" vs ", 1))  # type: ignore[return-value]
    opponent = str(bout.get("opponent") or "").strip()
    if not opponent:
        raise ManifestError(f"luta {a_name!r} sem opponent")
    return a_name.strip(), opponent


def _iter_rows(raw: Any, module: str) -> Iterable[tuple[str, int, dict[str, Any]]]:
    if not isinstance(raw, list):
        raise ManifestError(f"{module}.RAW deve ser uma lista")
    for index, block in enumerate(raw):
        if not isinstance(block, dict):
            raise ManifestError(f"{module}.RAW[{index}] deve ser um objeto")
        for key, bout in block.items():
            valid = (
                isinstance(key, tuple) and len(key) == 2 and isinstance(key[0], str)
                and isinstance(key[1], int) and isinstance(bout, dict)
            )
            if not valid:
                raise ManifestError(f"{module}.RAW[{index}] exige chave (a_name, year)")
            yield key[0], key[1], bout


def _bout_id(module: str, a_name: str, opponent: str, year: int) -> str:
    return f"{module}:{a_name}|{opponent}|{year}"


def _source_context(
    source: Mapping[str, Any],
    override: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
    module: str,
) -> tuple[str, str]:
    ruleset_id = str(override.get("ruleset_id", source.get("ruleset_id", "other-unknown")))
    uniform = str(override.get("uniform", source.get("uniform", "unknown")))
    preset = registry.get(ruleset_id)
    if preset is None:
        raise ManifestError(f"ruleset_id desconhecido em {module}: {ruleset_id}")
    if uniform not in {"gi", "no_gi", "unknown"}:
        raise ManifestError(f"uniform inválido em {module}: {uniform}")
    if preset["verification_status"] == "verified" and preset["uniform"] != uniform:
        raise ManifestError(f"uniform {uniform} incompatível com ruleset verificado {ruleset_id}")
    return ruleset_id, uniform


def _normalise_bout(
    module: str,
    a_name: str,
    year: int,
    raw: Mapping[str, Any],
    identity: Identity,
    issues: list[dict[str, Any]],
    *,
    ruleset_id: str = "other-unknown",
    uniform: str = "unknown",
) -> dict[str, Any]:
    raw_opponent = str(raw.get("opponent") or "").strip()
    first, second = _participants(a_name, raw)
    participant_by_key = {
        athlete_key(value): identity.resolve(value) or clean_athlete_name(value)
        for value in (first, second)
    }
    bout_id = _bout_id(module, a_name, raw_opponent or second, year)
    raw_events = raw.get("events", raw.get("sequence", [])) or []
    if not isinstance(raw_events, list):
        raise ManifestError(f"events de {bout_id} deve ser lista")
    events: list[dict[str, Any]] = []
    for index, item in enumerate(raw_events):
        if not isinstance(item, dict):
            issues.append({"code": "invalid_event", "bout_id": bout_id, "index": index})
            continue
        actor = str(item.get("actor") or "")
        resolved = participant_by_key.get(athlete_key(actor))
        if not resolved:
            issues.append({"code": "unknown_actor", "bout_id": bout_id, "index": index,
                           "actor": actor})
            continue
        events.append({**item, "actor": resolved, "successful": item.get("successful")})
    bout = {
        "bout_id": bout_id,
        "module": module,
        "a_name": a_name,
        "opponent": raw_opponent or second,
        "year": year,
        "participants": [participant_by_key[athlete_key(first)],
                         participant_by_key[athlete_key(second)]],
        "events": events,
        "ruleset_id": ruleset_id,
        "uniform": uniform,
    }
    result = {field: raw[field] for field in (
        "winner", "method", "event", "win_type", "submission", "stage", "weight_class"
    ) if field in raw}
    if result:
        bout["result"] = result
    if isinstance(raw.get("adjudication"), dict):
        bout["adjudication"] = raw["adjudication"]
    if isinstance(raw.get("timing_basis"), str):
        bout["timing_basis"] = raw["timing_basis"]
    if isinstance(raw.get("bout_start_s"), int | float) and not isinstance(
        raw.get("bout_start_s"), bool
    ):
        bout["bout_start_s"] = raw["bout_start_s"]
    if "duration_s" in raw:
        bout["duration_s"] = raw["duration_s"]
    if "overtime_start_s" in raw:
        candidate = {**bout, "overtime_start_s": raw["overtime_start_s"]}
        if _valid_overtime(candidate):
            bout["overtime_start_s"] = raw["overtime_start_s"]
        else:
            issues.append({"code": "invalid_overtime_start_s", "bout_id": bout_id})

    raw_score = raw.get("official_score")
    if isinstance(raw_score, dict):
        normalized_score: dict[str, Any] = {}
        valid_score_actors = True
        for actor, value in raw_score.items():
            resolved = participant_by_key.get(athlete_key(str(actor)))
            if not resolved:
                valid_score_actors = False
                issues.append({"code": "unknown_score_actor", "bout_id": bout_id,
                               "actor": str(actor)})
            else:
                normalized_score[resolved] = value
        if valid_score_actors:
            bout["official_score"] = normalized_score

    for field in ("score_events", "scouting_observations"):
        raw_items = raw.get(field)
        if not isinstance(raw_items, list):
            if field in raw:
                bout[field] = raw_items
            continue
        normalized_items = []
        for item in raw_items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            resolved = participant_by_key.get(athlete_key(str(item.get("actor") or "")))
            if not resolved:
                issues.append({"code": "unknown_extension_actor", "bout_id": bout_id,
                               "field": field, "actor": str(item.get("actor") or "")})
                continue
            normalized_items.append({**item, "actor": resolved})
        bout[field] = normalized_items
    return bout


def collect_bouts(
    manifest: Mapping[str, Any],
    module_loader: ModuleLoader = importlib.import_module,
    *,
    _source_stats: dict[tuple[str, str], int] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    identity = build_identity(manifest)
    corpus = {str(entry["name"]): [] for entry in _athletes(manifest)}
    issues: list[dict[str, Any]] = []
    modules: dict[str, list[tuple[str, int, dict[str, Any]]]] = {}
    registry = load_rulesets()
    for entry in _athletes(manifest):
        name = str(entry["name"])
        sources = entry.get("sources")
        if not isinstance(sources, list):
            raise ManifestError(f"sources de {name} deve ser lista")
        seen: set[str] = set()
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("module"), str):
                raise ManifestError(f"source de {name} deve declarar module")
            module = source["module"]
            if not module.startswith("scripts.dumps.") or module.count(".") != 2:
                raise ManifestError(f"módulo {module!r} fora do prefixo permitido scripts.dumps.*")
            if module not in modules:
                try:
                    loaded = module_loader(module)
                except Exception as exc:
                    raise ManifestError(f"não foi possível importar {module}: {exc}") from exc
                if not hasattr(loaded, "RAW"):
                    raise ManifestError(f"{module} não declara RAW")
                modules[module] = list(_iter_rows(loaded.RAW, module))
            _source_context(source, {}, registry, module)
            selectors = source.get("bouts")
            selector_context: dict[tuple[str, str, int], Mapping[str, Any]] = {}
            if selectors is None:
                selected = []
                for a_name, year, raw in modules[module]:
                    if " vs " in a_name:
                        first, second = (part.strip() for part in a_name.split(" vs ", 1))
                    else:
                        first, second = a_name, str(raw.get("opponent") or "")
                    if name in (identity.resolve(first), identity.resolve(second)):
                        selected.append((a_name, year, raw))
            else:
                if not isinstance(selectors, list):
                    raise ManifestError(f"bouts de {name} em {module} deve ser lista")
                available = {
                    (a, str(raw.get("opponent") or ""), year): (a, year, raw)
                    for a, year, raw in modules[module]
                }
                selected = []
                for selector in selectors:
                    if not isinstance(selector, dict) or any(
                        field not in selector for field in ("a_name", "opponent", "year")
                    ):
                        raise ManifestError(
                            f"seletor de {name} em {module} exige a_name, opponent e year"
                        )
                    a_value = selector["a_name"]
                    opponent_value = selector["opponent"]
                    year_value = selector["year"]
                    if (
                        not isinstance(a_value, str) or not a_value.strip()
                        or not isinstance(opponent_value, str) or not opponent_value.strip()
                        or not isinstance(year_value, int) or isinstance(year_value, bool)
                    ):
                        raise ManifestError(
                            f"seletor de {name} em {module} exige strings não vazias e year inteiro"
                        )
                    key = (a_value, opponent_value, year_value)
                    _source_context(source, selector, registry, module)
                    if key not in available:
                        issues.append({"code": "selected_bout_not_found", "athlete": name,
                                       "module": module, "selector": dict(selector)})
                    else:
                        selected.append(available[key])
                        selector_context[key] = selector
            participant_rows = 0
            for selected_a, _, selected_raw in selected:
                selected_first, selected_second = _participants(selected_a, selected_raw)
                if name in (identity.resolve(selected_first), identity.resolve(selected_second)):
                    participant_rows += 1
            if _source_stats is not None:
                key = (name, module)
                _source_stats[key] = _source_stats.get(key, 0) + participant_rows
            if participant_rows == 0:
                issues.append({"code": "source_no_participant_bouts", "athlete": name,
                               "module": module})
            for a_name, year, raw in selected:
                context = selector_context.get((a_name, str(raw.get("opponent") or ""), year), {})
                ruleset_id, uniform = _source_context(source, context, registry, module)
                bout = _normalise_bout(
                    module, a_name, year, raw, identity, issues,
                    ruleset_id=ruleset_id, uniform=uniform,
                )
                if name not in bout["participants"]:
                    issues.append({"code": "selected_bout_missing_athlete", "athlete": name,
                                   "bout_id": bout["bout_id"]})
                elif bout["bout_id"] not in seen:
                    corpus[name].append(bout)
                    seen.add(bout["bout_id"])
        corpus[name].sort(key=lambda item: item["bout_id"])
    return corpus, issues


def _sections(event: Mapping[str, Any]) -> tuple[str, ...]:
    typ = str(event.get("type") or "").lower()
    label = _normalize_name(str(event.get("label") or ""))
    found: list[str] = []
    if typ == "takedown" or (
        typ in {"control", "transition"} and any(word in label for word in STANDING_WORDS)
    ):
        found.append("standing")
    if typ == "guard":
        found.append("guard")
    if typ == "pass" or (
        typ in {"control", "transition"} and any(word in label for word in PASSING_WORDS)
    ):
        found.append("passing")
    if typ == "submission" or (typ in {"control", "transition"} and "back" in label):
        found.append("back_and_finishing")
    return tuple(found)


def _fact_id(athlete: str, *parts: object) -> str:
    return ":".join(
        _normalize_name(str(value)).replace(" ", "-") for value in (athlete, *parts)
    )


def _grade(count: int, bouts: int) -> str | None:
    if count >= 8 and bouts >= 4:
        return "forte"
    if count >= 5 and bouts >= 3:
        return "sustentado"
    if count >= 3 and bouts >= 2:
        return "limitado"
    return None


GRADE_RANK = {"limitado": 0, "sustentado": 1, "forte": 2}


def _fact_grade(fact: Mapping[str, Any]) -> str | None:
    stated = fact.get("evidence_grade")
    if stated in GRADE_RANK:
        return str(stated)
    count = int(fact.get("explicit_outcomes", fact.get("count", 0)))
    return _grade(count, len(fact.get("source_bouts", [])))


def _weakest_grade(*facts: Mapping[str, Any]) -> str | None:
    grades = [_fact_grade(fact) for fact in facts]
    if any(grade is None for grade in grades):
        return None
    return min((grade for grade in grades if grade is not None), key=GRADE_RANK.__getitem__)


def _technical_comparison_scope(*facts: Mapping[str, Any]) -> dict[str, Any]:
    rulesets = sorted({
        str(ruleset_id)
        for fact in facts
        for ruleset_id in fact.get("scope", {}).get("ruleset_ids", [])
    })
    return {
        "comparison_basis": "technical_cross_ruleset",
        "uniform": "no_gi",
        "ruleset_ids": rulesets,
    }


def _complete_score(bout: Mapping[str, Any]) -> bool:
    duration = bout.get("duration_s")
    score = bout.get("official_score")
    events = bout.get("score_events")
    participants = bout.get("participants")
    if (
        not isinstance(duration, int | float) or isinstance(duration, bool) or duration <= 0
        or not isinstance(participants, list) or len(participants) != 2
        or len(set(participants)) != 2 or not all(isinstance(name, str) for name in participants)
        or not isinstance(score, dict) or set(score) != set(participants)
        or not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                   for value in score.values())
        or not isinstance(events, list)
    ):
        return False
    totals: Counter[str] = Counter()
    for event in events:
        valid = (
            isinstance(event, dict) and isinstance(event.get("actor"), str)
            and event.get("actor") in score
            and isinstance(event.get("points"), int) and not isinstance(event.get("points"), bool)
            and event["points"] >= 0
            and isinstance(event.get("ts"), int | float) and not isinstance(event.get("ts"), bool)
            and 0 <= event["ts"] <= duration
        )
        if not valid:
            return False
        totals[event["actor"]] += event["points"]
    return bool(events) and all(totals[name] == points for name, points in score.items())


def _valid_overtime(bout: Mapping[str, Any]) -> bool:
    value = bout.get("overtime_start_s")
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        return False
    duration = bout.get("duration_s")
    if duration is None:
        return True
    return (isinstance(duration, int | float) and not isinstance(duration, bool)
            and duration > 0 and value <= duration)


def _analyse_scope(
    athlete: str,
    bouts: Sequence[Mapping[str, Any]],
    *,
    uniform: str,
    include_observations: bool = True,
) -> dict[str, Any]:
    """Reduce one selected corpus to raw support and safely gated facts."""
    labels: dict[str, dict[str, Any]] = {}
    section_labels: dict[str, Counter[str]] = defaultdict(Counter)
    section_bouts: dict[str, set[str]] = defaultdict(set)
    transitions: dict[tuple[str, str], dict[str, Any]] = {}
    responses: dict[tuple[str, str], dict[str, Any]] = {}
    observations: dict[tuple[str, str, str], dict[str, Any]] = {}
    reducer_events: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for bout in bouts:
        bout_id = str(bout["bout_id"])
        previous_own: Mapping[str, Any] | None = None
        previous: Mapping[str, Any] | None = None
        for event in bout.get("events", []):
            if event.get("actor") == athlete:
                label = str(event.get("label") or "")
                if not label:
                    continue
                reducer_events.append({**event, "actor": "you"})
                data = labels.setdefault(
                    label,
                    {"count": 0, "bouts": set(), "outcomes": Counter(),
                     "type": str(event.get("type") or "")},
                )
                data["count"] += 1
                data["bouts"].add(bout_id)
                outcome = event.get("successful")
                outcome_key = "true" if outcome is True else "false" if outcome is False else "unknown"
                data["outcomes"][outcome_key] += 1
                for section in _sections(event):
                    section_labels[section][label] += 1
                    section_bouts[section].add(bout_id)
                if previous_own is not None:
                    pair = (str(previous_own.get("label")), label)
                    item = transitions.setdefault(pair, {"count": 0, "bouts": set()})
                    item["count"] += 1
                    item["bouts"].add(bout_id)
                if previous is not None and previous.get("actor") != athlete:
                    pair = (str(previous.get("label") or ""), label)
                    item = responses.setdefault(pair, {"count": 0, "bouts": set()})
                    item["count"] += 1
                    item["bouts"].add(bout_id)
                previous_own = event
            previous = event
        for observation in bout.get("scouting_observations", []) if include_observations else []:
            if not isinstance(observation, dict) or observation.get("actor") != athlete:
                continue
            kind = str(observation.get("kind") or "")
            value = str(observation.get("value") or "")
            phase = str(observation.get("phase") or "")
            if kind not in BEHAVIOR_KINDS or not value:
                continue
            if phase == "overtime" and not _valid_overtime(bout):
                continue
            item = observations.setdefault((kind, value, phase), {"count": 0, "bouts": set()})
            item["count"] += 1
            item["bouts"].add(bout_id)
        if _complete_score(bout):
            score_text = " × ".join(
                f"{name} {points}" for name, points in sorted(bout["official_score"].items())
            )
            facts.append({"id": _fact_id(athlete, "official-score", bout_id),
                          "kind": "official_score", "statement": f"Placar oficial: {score_text}.",
                          "source_bouts": [bout_id]})
            duration = float(bout["duration_s"])
            if any(event["actor"] == athlete and float(event["ts"]) >= duration - 1
                   for event in bout["score_events"]):
                facts.append({"id": _fact_id(athlete, "last-second-score", bout_id),
                              "kind": "last_second_score",
                              "statement": "Pontuação oficial registrada no último segundo da luta.",
                              "source_bouts": [bout_id]})

    evidence_bouts = sorted(
        str(bout["bout_id"])
        for bout in bouts
        if bout.get("events")
    )
    if len(evidence_bouts) >= MIN_SEQUENCE_BOUTS and len(reducer_events) >= MIN_DOSSIER_EVENTS:
        coverage_grade = _grade(len(reducer_events), len(evidence_bouts))
        assert coverage_grade is not None
        facts.append({
            "id": _fact_id(athlete, "evidence-coverage"),
            "kind": "evidence_coverage",
            "bout_count": len(evidence_bouts),
            "own_event_count": len(reducer_events),
            "count": len(reducer_events),
            "evidence_grade": coverage_grade,
            "statement": (f"Cobertura: {len(reducer_events)} eventos próprios em "
                          f"{len(evidence_bouts)} lutas."),
            "source_bouts": evidence_bouts,
        })

    for label, data in sorted(labels.items()):
        source_bouts = sorted(data["bouts"])
        grade = _grade(data["count"], len(source_bouts))
        if grade:
            facts.append({"id": _fact_id(athlete, "tendency", label), "kind": "tendency",
                          "label": label, "count": data["count"], "evidence_grade": grade,
                          "statement": f"{label}: {data['count']} ocorrências em {len(source_bouts)} lutas.",
                          "source_bouts": source_bouts})
        explicit = data["outcomes"]["true"] + data["outcomes"]["false"]
        coverage = explicit / data["count"] if data["count"] else 0.0
        if explicit >= 3 and coverage >= 0.7:
            rate = data["outcomes"]["true"] / explicit
            facts.append({"id": _fact_id(athlete, "success-rate", label),
                          "kind": "success_rate", "label": label, "rate": round(rate, 3),
                          "explicit_outcomes": explicit, "outcome_coverage": round(coverage, 3),
                          "statement": (f"{label}: {data['outcomes']['true']}/{explicit} resultados "
                                        f"explícitos positivos ({rate:.0%}; cobertura {coverage:.0%})."),
                          "source_bouts": source_bouts})

    for section in SECTIONS:
        total = sum(section_labels[section].values())
        if total == 0 and bouts:
            facts.append({"id": _fact_id(athlete, "absence", section), "kind": "absence",
                          "section": section,
                          "statement": f"{SECTION_LABELS[section]} não observado em {len(bouts)} lutas.",
                          "source_bouts": sorted(str(bout["bout_id"]) for bout in bouts)})
        elif total >= 5 and len(section_bouts[section]) >= 2:
            label, count = section_labels[section].most_common(1)[0]
            share = count / total
            if share >= 0.4:
                facts.append({"id": _fact_id(athlete, "predominance", section, label),
                              "kind": "predominance", "section": section, "label": label,
                              "count": count, "share": round(share, 3),
                              "statement": (f"{label} concentra {share:.0%} dos eventos de "
                                            f"{SECTION_LABELS[section].lower()}."),
                              "source_bouts": sorted(section_bouts[section])})
    for (before, after), data in sorted(transitions.items()):
        source_bouts = sorted(data["bouts"])
        grade = _grade(data["count"], len(source_bouts))
        if grade:
            facts.append({"id": _fact_id(athlete, "transition", before, after),
                          "kind": "transition", "from": before, "to": after,
                          "count": data["count"], "evidence_grade": grade,
                          "statement": f"Transição recorrente: {before} → {after}.",
                          "source_bouts": source_bouts})
    for (condition, label), data in sorted(responses.items()):
        source_bouts = sorted(data["bouts"])
        grade = _grade(data["count"], len(source_bouts))
        if grade:
            facts.append({"id": _fact_id(athlete, "response", condition, label),
                          "kind": "response", "condition_label": condition, "label": label,
                          "count": data["count"], "evidence_grade": grade,
                          "statement": f"Resposta recorrente a {condition}: {label}.",
                          "source_bouts": source_bouts})
    for (kind, value, phase), data in sorted(observations.items()):
        source_bouts = sorted(data["bouts"])
        grade = _grade(data["count"], len(source_bouts))
        if grade:
            phase_text = f" em {phase}" if phase else ""
            facts.append({"id": _fact_id(athlete, "observation", kind, value, phase),
                          "kind": "observation", "observation_kind": kind, "value": value,
                          "phase": phase or None, "count": data["count"],
                          "evidence_grade": grade,
                          "statement": f"Observação recorrente{phase_text}: {value}.",
                          "source_bouts": source_bouts})

    reduced = reduce_style_events(reducer_events)
    raw_labels = {
        label: {"count": data["count"], "bouts": sorted(data["bouts"]), "type": data["type"],
                "outcomes": {key: data["outcomes"][key]
                             for key in ("true", "false", "unknown")}}
        for label, data in sorted(labels.items())
    }
    rulesets = sorted({str(bout.get("ruleset_id", "other-unknown")) for bout in bouts})
    scope = {"uniform": uniform, "ruleset_ids": rulesets}
    for fact in facts:
        fact["scope"] = scope
    return {
        "athlete": athlete,
        "facts": sorted(facts, key=lambda fact: fact["id"]),
        "raw_support": {
            "bout_count": len(bouts), "own_event_count": len(reducer_events),
            "type_counts": dict(sorted(reduced["type_counts"].items())),
            "section_counts": {section: sum(section_labels[section].values())
                               for section in SECTIONS},
            "labels": raw_labels, "top_exact_labels": reduced["signature_techniques"],
            "transitions": [{"from": before, "to": after, "count": data["count"],
                             "source_bouts": sorted(data["bouts"])}
                            for (before, after), data in sorted(transitions.items())],
        },
        "source_bouts": sorted(str(bout["bout_id"]) for bout in bouts),
    }


def analyse_athlete(athlete: str, bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build no-gi primary evidence, isolated ruleset behavior, and a gi supplement."""
    explicit_context = any("uniform" in bout for bout in bouts)
    primary = [bout for bout in bouts if bout.get("uniform") == "no_gi"] if explicit_context else list(bouts)
    gi = [bout for bout in bouts if bout.get("uniform") == "gi"]
    unknown = [bout for bout in bouts if bout.get("uniform") == "unknown"]
    profile = _analyse_scope(
        athlete, primary, uniform="no_gi", include_observations=not explicit_context
    )
    ruleset_slices: dict[str, Any] = {}
    for ruleset_id in sorted({str(bout.get("ruleset_id", "other-unknown")) for bout in primary}):
        selected = [bout for bout in primary if str(bout.get("ruleset_id", "other-unknown")) == ruleset_id]
        slice_profile = _analyse_scope(athlete, selected, uniform="no_gi")
        slice_profile["facts"] = [fact for fact in slice_profile["facts"]
                                  if fact.get("kind") == "observation"]
        ruleset_slices[ruleset_id] = slice_profile
    profile["ruleset_slices"] = ruleset_slices
    profile["gi_supplement"] = _analyse_scope(athlete, gi, uniform="gi")
    profile["excluded_unknown"] = {
        "bout_count": len(unknown),
        "source_bouts": sorted(str(bout["bout_id"]) for bout in unknown),
    }
    return profile


def build_matchup(
    opponent: Mapping[str, Any], own_athlete: Mapping[str, Any]
) -> list[dict[str, Any]]:
    own_responses = [fact for fact in own_athlete.get("facts", [])
                     if fact.get("kind") == "response"]
    own_coverage = next((fact for fact in own_athlete.get("facts", [])
                         if fact.get("kind") == "evidence_coverage"), None)
    conclusions: list[dict[str, Any]] = []
    for threat in opponent.get("facts", []):
        if threat.get("kind") != "tendency":
            continue
        threat_grade = _fact_grade(threat)
        if not threat_grade:
            continue
        response = next(
            (fact for fact in own_responses
             if _normalize_name(str(fact.get("condition_label", "")))
             == _normalize_name(str(threat.get("label", "")))),
            None,
        )
        source_bouts = set(threat["source_bouts"])
        if response:
            grade = _weakest_grade(threat, response)
            if not grade:
                continue
            source_bouts.update(response["source_bouts"])
            kind = "documented_response"
            statement = ("Transferência técnica entre regras: "
                         f"contra {threat['label']}, há resposta recorrente documentada: "
                         f"{response['label']}.")
            own_ids = [response["id"]]
        else:
            if not own_coverage:
                continue
            grade = _weakest_grade(threat, own_coverage)
            if not grade:
                continue
            source_bouts.update(own_coverage["source_bouts"])
            kind = "evidence_gap"
            statement = ("Transferência técnica entre regras: "
                         f"{threat['label']} é ameaça recorrente; a resposta da atleta é uma "
                         "lacuna de evidência, não uma fraqueza demonstrada.")
            own_ids = [own_coverage["id"]]
        conclusions.append({
            "kind": kind, "statement": statement, "evidence_grade": grade,
            "opponent_fact_ids": [threat["id"]], "own_athlete_fact_ids": own_ids,
            "counterevidence": [], "source_bouts": sorted(source_bouts),
            "scope": _technical_comparison_scope(threat, response or own_coverage),
        })
        if response:
            opponent_rate = next(
                (fact for fact in opponent.get("facts", [])
                 if fact.get("kind") == "success_rate"
                 and _normalize_name(str(fact.get("label", "")))
                 == _normalize_name(str(threat["label"]))),
                None,
            )
            own_rate = next(
                (fact for fact in own_athlete.get("facts", [])
                 if fact.get("kind") == "success_rate"
                 and _normalize_name(str(fact.get("label", "")))
                 == _normalize_name(str(response["label"]))),
                None,
            )
            if opponent_rate and own_rate:
                rate_bouts = set(opponent_rate["source_bouts"]) | set(own_rate["source_bouts"])
                rate_grade = _weakest_grade(opponent_rate, own_rate)
                if rate_grade:
                    opportunity = own_rate["rate"] >= opponent_rate["rate"]
                    assessment = "opportunity" if opportunity else "risk"
                    comparative = "superior ou igual" if opportunity else "inferior"
                    conclusions.append({
                        "kind": assessment,
                        "statement": ("Transferência técnica entre regras: nos outcomes "
                                      f"explícitos, {response['label']} tem taxa "
                                      f"{comparative} à de {threat['label']}."),
                        "evidence_grade": rate_grade,
                        "opponent_fact_ids": [opponent_rate["id"]],
                        "own_athlete_fact_ids": [own_rate["id"]],
                        "counterevidence": [],
                        "source_bouts": sorted(rate_bouts),
                        "scope": _technical_comparison_scope(opponent_rate, own_rate),
                    })
    return conclusions


def audit_manifest(
    manifest: Mapping[str, Any], module_loader: ModuleLoader = importlib.import_module
) -> dict[str, Any]:
    source_stats: dict[tuple[str, str], int] = {}
    corpus, issues = collect_bouts(manifest, module_loader, _source_stats=source_stats)
    coverage = []
    for entry in _athletes(manifest):
        name = str(entry["name"])
        bouts = corpus[name]
        target_uniform = str(manifest.get("target_uniform", "no_gi"))
        target_bouts = [bout for bout in bouts if bout.get("uniform") == target_uniform]
        sequence_bouts = [bout for bout in target_bouts if bout["events"]]
        events = [event for bout in sequence_bouts for event in bout["events"]
                  if event.get("actor") == name]
        own_events = len(events)
        explicit = sum(
            event.get("successful") is True or event.get("successful") is False
            for event in events
        )
        unknown = own_events - explicit
        type_counts = Counter(str(event.get("type") or "") for event in events)
        section_counts = Counter(section for event in events for section in _sections(event))
        missing_fields = {
            "successful": sum(
                event.get("successful") is not True and event.get("successful") is not False
                for event in events
            ),
            "timestamp": sum("ts" not in event and "timestamp" not in event for event in events),
            "phase": sum("phase" not in event for event in events),
            "period": sum("period" not in event for event in events),
            "setup": sum(event.get("type") == "takedown" and "setup" not in event
                         for event in events),
        }
        source_counts = [
            {"module": source["module"],
             "selected_participant_rows": source_stats.get((name, source["module"]), 0)}
            for source in entry["sources"]
        ]
        matrix: dict[tuple[str, str], dict[str, int]] = {}
        for bout in bouts:
            key = (str(bout.get("ruleset_id", "other-unknown")),
                   str(bout.get("uniform", "unknown")))
            item = matrix.setdefault(key, {"bouts": 0, "own_events": 0})
            item["bouts"] += 1
            item["own_events"] += sum(event.get("actor") == name for event in bout["events"])
        coverage_matrix = [
            {"ruleset_id": ruleset_id, "uniform": uniform, **matrix[(ruleset_id, uniform)]}
            for ruleset_id, uniform in sorted(matrix)
        ]
        excluded_uniform_counts = Counter(
            str(bout.get("uniform", "unknown")) for bout in bouts
            if bout.get("uniform") != target_uniform
        )
        missing = []
        if len(sequence_bouts) < MIN_SEQUENCE_BOUTS:
            missing.append(f"{MIN_SEQUENCE_BOUTS - len(sequence_bouts)} lutas com sequência")
        if own_events < MIN_DOSSIER_EVENTS:
            missing.append(f"{MIN_DOSSIER_EVENTS - own_events} eventos próprios")
        coverage.append({"athlete": name, "selected_bouts": len(bouts),
                         "selected_bouts_with_sequence": len(sequence_bouts),
                         "own_events": own_events,
                         "type_counts": dict(sorted(type_counts.items())),
                         "section_counts": {section: section_counts[section]
                                            for section in SECTIONS},
                         "explicit_outcomes": explicit, "unknown_outcomes": unknown,
                         "outcome_coverage": round(explicit / own_events, 3) if own_events else 0.0,
                         "missing_fields": missing_fields, "sources": source_counts,
                         "coverage_matrix": coverage_matrix,
                         "excluded_uniform_counts": dict(sorted(excluded_uniform_counts.items())),
                         "missing": missing})
    return {
        "event": manifest.get("event"),
        "ready": not issues and all(not item["missing"] for item in coverage),
        "thresholds": {"selected_bouts_with_sequence": MIN_SEQUENCE_BOUTS,
                       "own_events": MIN_DOSSIER_EVENTS},
        "athletes": coverage,
        "issues": issues,
    }


def generate_reports(
    manifest: Mapping[str, Any], module_loader: ModuleLoader = importlib.import_module
) -> list[dict[str, Any]]:
    audit = audit_manifest(manifest, module_loader)
    if not audit["ready"]:
        failed = {item["athlete"] for item in audit["athletes"] if item["missing"]}
        failed.update(issue["athlete"] for issue in audit["issues"] if "athlete" in issue)
        raise GenerationBlocked(
            "geração bloqueada: mínimo de 3 lutas com sequência e 15 eventos próprios por atleta; "
            f"pendentes: {', '.join(sorted(failed))}"
        )
    corpus, _ = collect_bouts(manifest, module_loader)
    profiles = {name: analyse_athlete(name, bouts) for name, bouts in corpus.items()}
    registry = load_rulesets()
    target_references = list(manifest["target_ruleset_ids"])
    preset_ids = {reference.rpartition(":")[0] for reference in target_references}
    projection_references = sorted(set(target_references) | {
        f"{preset_id}:overtime" for preset_id in preset_ids
        if "overtime" in registry[preset_id]["profiles"]
    })
    target_rulesets = [
        {key: registry[reference.rpartition(":")[0]][key]
         for key in ("id", "edition", "captured_on", "source_url")}
        | {"profile": reference.rpartition(":")[2]}
        for reference in target_references
    ]
    reports = []
    for division in manifest["divisions"]:
        own = str(division["own_athlete"])
        if own not in profiles:
            raise ManifestError(f"own_athlete {own!r} não está em athletes")
        opponents = []
        for entry in division["athletes"]:
            name = str(entry["name"])
            if name == own:
                continue
            profile = profiles[name]
            projections = {
                reference: [
                    {"bout_id": bout["bout_id"], "events": project_adcc_events(
                        bout, reference, registry
                    )}
                    for bout in corpus[name]
                    if bout.get("uniform") == manifest["target_uniform"]
                ]
                for reference in projection_references
            }
            limitations = []
            if profile["excluded_unknown"]["bout_count"]:
                limitations.append("Lutas com uniforme desconhecido foram excluídas da análise.")
            opponents.append({
                "athlete": name, "country": entry.get("country", ""),
                "qualification": entry.get("qualification", ""), "facts": profile["facts"],
                "raw_support": profile["raw_support"],
                "summary": [{"statement": fact["statement"], "fact_ids": [fact["id"]],
                             "source_bouts": fact["source_bouts"]}
                            for fact in profile["facts"]],
                "ruleset_slices": profile["ruleset_slices"],
                "gi_supplement": profile["gi_supplement"],
                "adcc_projection": projections,
                "native_adjudication": [
                    {"bout_id": bout["bout_id"], "ruleset_id": bout["ruleset_id"],
                     **bout["adjudication"]}
                    for bout in corpus[name] if isinstance(bout.get("adjudication"), dict)
                ],
                "coverage_matrix": next(
                    item["coverage_matrix"] for item in audit["athletes"]
                    if item["athlete"] == name
                ),
                "conclusions": build_matchup(profile, profiles[own]),
                "limitations": limitations,
                "source_bouts": profile["source_bouts"],
                "tabelas": build_tables(name, [
                    bout for bout in corpus[name]
                    if bout.get("uniform") == manifest["target_uniform"]
                ]),
            })
        reports.append({"event": manifest.get("event", "ADCC 2026"),
                        "division": division["name"], "slug": division["slug"],
                        "target_uniform": manifest["target_uniform"],
                        "target_rulesets": target_rulesets,
                        "own_athlete": own, "own_profile": profiles[own],
                        "own_tabelas": build_tables(own, [
                            bout for bout in corpus[own]
                            if bout.get("uniform") == manifest["target_uniform"]
                        ]),
                        "opponents": opponents})
    return reports


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _list(items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return '<p class="empty">Nenhum item passou pelos gates de evidência.</p>'
    return "<ul>" + "".join(
        f"<li>{html.escape(str(item['statement']))}"
        f"<small>Fonte: {html.escape(', '.join(item.get('source_bouts', [])))}</small></li>"
        for item in items
    ) + "</ul>"


def render_html(report: Mapping[str, Any]) -> str:
    target_rules = report.get("target_rulesets", [])
    target_text = ", ".join(
        f"{item.get('id')}:{item.get('profile')} ({item.get('edition')}; snapshot {item.get('captured_on')})"
        for item in target_rules
    ) or "ADCC 2026 · snapshot local"
    chapters = []
    for opponent in report["opponents"]:
        limitations = [{"statement": value, "source_bouts": []}
                       for value in opponent.get("limitations", [])]
        behavior = [fact for value in opponent.get("ruleset_slices", {}).values()
                    for fact in value.get("facts", [])]
        gi_facts = opponent.get("gi_supplement", {}).get("facts", [])
        projection_items = [
            {"statement": f"{reference}: {sum(len(bout['events']) for bout in bouts)} ações classificadas.",
             "source_bouts": [bout["bout_id"] for bout in bouts]}
            for reference, bouts in sorted(opponent.get("adcc_projection", {}).items())
        ]
        coverage_items = [
            {"statement": (f"{item['ruleset_id']} · {item['uniform']}: "
                           f"{item['bouts']} lutas, {item['own_events']} eventos próprios."),
             "source_bouts": []}
            for item in opponent.get("coverage_matrix", [])
        ]
        native_items = [
            {"statement": (f"{item.get('ruleset_id')} · {item.get('kind', 'none')} · "
                           f"{item.get('status', 'unknown')}"),
             "source_bouts": [str(item.get("bout_id"))]}
            for item in opponent.get("native_adjudication", [])
        ]
        chapters.append(f"""
<article class="opponent">
  <header><p class="eyebrow">Adversária</p><h2>{html.escape(opponent['athlete'])}</h2>
  <p>{html.escape(str(opponent.get('country', '')))} · {html.escape(str(opponent.get('qualification', '')))}</p></header>
  <section><h3>Perfil técnico no-gi</h3>{_list(opponent.get('facts', []))}</section>
  <section><h3>Fatos observados</h3>{_list(opponent.get('facts', []))}</section>
  <section><h3>Comportamento por ruleset</h3>{_list(behavior)}</section>
  <section><h3>Projeção ADCC</h3>{_list(projection_items)}</section>
  <section><h3>Suplemento gi</h3>{_list(gi_facts)}</section>
  <section><h3>Cobertura por ruleset e uniforme</h3>{_list(coverage_items)}</section>
  <section><h3>Adjudicação nativa</h3>{_list(native_items)}</section>
  <section><h3>Resumo factual</h3>{_list(opponent.get('summary', []))}</section>
  <section><h3>Conclusões do sistema</h3>{_list(opponent.get('conclusions', []))}</section>
  <section><h3>Limitações</h3>{_list(limitations)}</section>
  <section><h3>Lutas-fonte</h3><p class="sources">{html.escape(', '.join(opponent.get('source_bouts', []))) or 'Nenhuma luta selecionada.'}</p></section>
</article>""")
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(report['event'])} · {html.escape(report['division'])}</title>
<style>
:root {{ --paper: oklch(0.965 0.008 86); --ink: oklch(0.24 0.018 52); --muted: oklch(0.48 0.02 60); --rule: oklch(0.76 0.025 74); --accent: oklch(0.42 0.09 34); --space-xs: 4px; --space-sm: 8px; --space-md: 16px; --space-lg: 24px; --space-xl: 48px; }}
@page {{ size: A4; margin: 18mm 16mm 20mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0 auto; max-width: 74ch; padding: var(--space-xl) var(--space-lg); color: var(--ink); background: var(--paper); font: 10.5pt/1.55 "Avenir Next", "Liberation Sans", sans-serif; }}
h1, h2, h3 {{ font-family: Charter, "Bitstream Charter", serif; font-weight: 600; line-height: 1.12; }}
h1 {{ max-width: 16ch; font-size: clamp(2.5rem, 8vw, 5rem); margin: var(--space-sm) 0 var(--space-lg); letter-spacing: -0.035em; }}
h2 {{ font-size: 2.2rem; margin: var(--space-xs) 0; }} h3 {{ font-size: 1.12rem; margin: 0 0 var(--space-sm); }}
p, ul {{ margin: 0; }} ul {{ padding-inline-start: 1.25rem; display: grid; gap: var(--space-sm); }} li::marker {{ color: var(--accent); }}
small {{ display: block; color: var(--muted); font-size: 0.78rem; }}
.cover {{ min-height: 88vh; display: grid; align-content: end; border-block: 1px solid var(--rule); padding-block: var(--space-xl); }}
.eyebrow {{ color: var(--accent); font-size: 0.72rem; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase; }}
.dek {{ max-width: 52ch; color: var(--muted); font-size: 1.05rem; }}
.opponent {{ break-before: page; display: grid; gap: var(--space-lg); }} .opponent > header {{ border-bottom: 1px solid var(--rule); padding-bottom: var(--space-lg); }}
.opponent section {{ display: grid; grid-template-columns: minmax(9rem, 0.38fr) 1fr; gap: var(--space-lg); align-items: start; }}
.empty, .sources {{ color: var(--muted); }} @media print {{ body {{ padding: 0; }} .cover {{ min-height: 240mm; }} }}
@media (max-width: 640px) {{ body {{ padding: var(--space-lg) var(--space-md); }} .opponent section {{ grid-template-columns: 1fr; gap: var(--space-sm); }} }}
</style></head><body><main>
<section class="cover"><p class="eyebrow">Relatório local de scouting</p><h1>{html.escape(report['event'])}<br>{html.escape(report['division'])}</h1>
<p class="dek">Visão do professor para {html.escape(report['own_athlete'])}. Afirmações separadas por nível de derivação e vinculadas às lutas-fonte.</p></section>
<section class="opponent"><header><p class="eyebrow">Regra-alvo</p><h2>Regra-alvo</h2></header>
<section><h3>Snapshot</h3><p>{html.escape(target_text)}</p></section></section>
<section class="opponent"><header><p class="eyebrow">Atleta do técnico</p>
<h2>Perfil de {html.escape(report['own_athlete'])}</h2></header>
<section><h3>Perfil técnico no-gi</h3>{_list(report['own_profile'].get('facts', []))}</section></section>
{''.join(chapters)}</main></body></html>
"""


def chrome_command(chrome: Path, html_path: Path, pdf_path: Path) -> list[str]:
    return [str(chrome), "--headless", "--no-sandbox", "--disable-gpu",
            f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()]


def _find_chrome(finder: Callable[[str], str | None]) -> str | None:
    return finder("/usr/bin/google-chrome") or finder("google-chrome")


def render_pdf(
    html_path: Path,
    pdf_path: Path,
    *,
    finder: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    chrome = _find_chrome(finder)
    if not chrome:
        raise PdfError(f"Chrome não encontrado; HTML preservado em {html_path}")
    with tempfile.NamedTemporaryFile(
        prefix=f".{pdf_path.stem}-", suffix=".pdf", dir=pdf_path.parent, delete=False
    ) as handle:
        temporary_pdf = Path(handle.name)
    try:
        result = runner(
            chrome_command(Path(chrome), html_path, temporary_pdf),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = str(result.stderr).strip() or f"exit {result.returncode}"
            raise PdfError(f"Chrome falhou ({detail}); HTML preservado em {html_path}")
        if not temporary_pdf.is_file() or temporary_pdf.stat().st_size == 0:
            raise PdfError(f"Chrome não produziu o PDF; HTML preservado em {html_path}")
        temporary_pdf.replace(pdf_path)
    finally:
        temporary_pdf.unlink(missing_ok=True)


def write_reports(reports: Sequence[Mapping[str, Any]], out_dir: Path) -> list[Path]:
    root = out_dir.resolve()
    bases = []
    for report in reports:
        slug = report.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise ManifestError(f"slug de relatório inválido: {slug!r}")
        base = (root / slug).resolve()
        if not base.is_relative_to(root):
            raise ManifestError(f"slug de relatório escapa do diretório de saída: {slug!r}")
        bases.append((report, base))
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for report, base in bases:
        json_path, html_path, pdf_path = (base.with_suffix(suffix)
                                         for suffix in (".json", ".html", ".pdf"))
        json_path.write_text(render_json(report), encoding="utf-8")
        html_path.write_text(render_html(report), encoding="utf-8")
        written.extend((json_path, html_path))
        render_pdf(html_path, pdf_path)
        written.append(pdf_path)
    return written


def tables_manifest(
    manifest: Mapping[str, Any], module_loader: ModuleLoader = importlib.import_module
) -> dict[str, Any]:
    """Spreadsheet-shaped cross-tabs for every athlete in the manifest.

    Restricted to ``target_uniform``: gi and no-gi are different games, and folding
    them into one table is the mistake the manifest exists to prevent. Bouts dropped
    for uniform are counted, not silently discarded.
    """
    corpus, issues = collect_bouts(manifest, module_loader)
    target_uniform = str(manifest.get("target_uniform", "no_gi"))
    atletas: dict[str, Any] = {}
    for entry in _athletes(manifest):
        name = str(entry["name"])
        bouts = corpus[name]
        alvo = [bout for bout in bouts if bout.get("uniform") == target_uniform]
        tabelas = build_tables(name, alvo)
        tabelas["cobertura"]["uniform"] = target_uniform
        tabelas["cobertura"]["lutas_fora_do_uniform"] = len(bouts) - len(alvo)
        tabelas["cobertura"]["rulesets"] = dict(
            Counter(str(bout.get("ruleset_id")) for bout in alvo)
        )
        atletas[name] = tabelas
    return {"event": manifest.get("event"), "issues": issues, "atletas": atletas}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("reports/adcc-2026"))
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--tabelas", action="store_true",
                        help="cross-tabs da planilha de scout (log, luta em pé, efetividade, tempo)")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.audit:
            print(json.dumps(audit_manifest(manifest), ensure_ascii=False, indent=2,
                             sort_keys=True))
            return 0
        if args.tabelas:
            print(json.dumps(tables_manifest(manifest), ensure_ascii=False, indent=2,
                             sort_keys=True))
            return 0
        print("\n".join(str(path) for path in write_reports(generate_reports(manifest), args.out)))
        return 0
    except (ManifestError, GenerationBlocked, PdfError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
