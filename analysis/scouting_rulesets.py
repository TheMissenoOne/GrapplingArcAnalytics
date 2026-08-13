"""Versioned, local ruleset metadata and conservative ADCC projection."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_RULESETS_PATH = Path(__file__).parents[1] / "data/scouting/rulesets.json"
FAMILIES = {"adcc", "cji", "ibjjf", "other"}
UNIFORMS = {"gi", "no_gi", "unknown"}
DECISION_MODELS = {"points", "round_cards", "unknown"}
STATUSES = {
    "officially_awarded",
    "eligible_observed",
    "potentially_eligible",
    "ineligible_phase",
    "unknown",
}


class RulesetError(ValueError):
    pass


def _validate_windows(identifier: str, profile_id: str, profile: Any) -> None:
    windows = profile.get("windows") if isinstance(profile, dict) else None
    if not isinstance(windows, list) or not windows:
        raise RulesetError(f"windows inválidas em {identifier}:{profile_id}")
    previous_end: float | None = None
    for index, window in enumerate(windows):
        if not isinstance(window, dict) or set(window) != {
            "start_s", "end_s", "positive", "negative"
        }:
            raise RulesetError(f"windows inválidas em {identifier}:{profile_id}")
        start, end = window["start_s"], window["end_s"]
        valid_start = isinstance(start, int | float) and not isinstance(start, bool) and start >= 0
        valid_end = (
            end is None
            or isinstance(end, int | float) and not isinstance(end, bool) and end > start
        )
        flags = type(window["positive"]) is bool and type(window["negative"]) is bool
        ordered = index == 0 or previous_end is not None and start >= previous_end
        if not valid_start or not valid_end or not flags or not ordered:
            raise RulesetError(f"windows inválidas em {identifier}:{profile_id}")
        previous_end = float(end) if end is not None else None


def validate_rulesets(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list):
        raise RulesetError("rulesets deve ser uma lista")
    registry: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise RulesetError("cada preset deve declarar id")
        identifier = item["id"]
        if identifier in registry:
            raise RulesetError(f"id duplicado: {identifier}")
        if item.get("family") not in FAMILIES:
            raise RulesetError(f"family inválida em {identifier}")
        if item.get("uniform") not in UNIFORMS:
            raise RulesetError(f"uniform inválido em {identifier}")
        if item.get("decision_model") not in DECISION_MODELS:
            raise RulesetError(f"decision_model inválido em {identifier}")
        if item.get("verification_status") not in {"verified", "unknown"}:
            raise RulesetError(f"verification_status inválido em {identifier}")
        try:
            date.fromisoformat(str(item.get("captured_on")))
        except ValueError as exc:
            raise RulesetError(f"captured_on inválido em {identifier}") from exc
        if item["verification_status"] == "verified":
            parsed = urlparse(str(item.get("source_url") or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RulesetError(f"source_url inválida em {identifier}")
        if not isinstance(item.get("profiles"), dict) or not item["profiles"]:
            raise RulesetError(f"profiles inválido em {identifier}")
        if item["family"] == "adcc" and item["verification_status"] == "verified":
            for profile_id, profile in item["profiles"].items():
                _validate_windows(identifier, profile_id, profile)
        registry[identifier] = dict(item)
    return registry


def load_rulesets(path: Path = DEFAULT_RULESETS_PATH) -> dict[str, dict[str, Any]]:
    try:
        return validate_rulesets(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise RulesetError(f"não foi possível ler {path}: {exc}") from exc


def resolve_profile(
    reference: str, registry: Mapping[str, Mapping[str, Any]]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    preset_id, separator, profile_id = reference.rpartition(":")
    preset = registry.get(preset_id) if separator else None
    if not preset or profile_id not in preset.get("profiles", {}):
        raise RulesetError(f"target ruleset/profile desconhecido: {reference}")
    return preset, preset["profiles"][profile_id]


def validate_target_rulesets(
    manifest: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]]
) -> None:
    if manifest.get("target_uniform") not in {"gi", "no_gi"}:
        raise RulesetError("target_uniform deve ser gi ou no_gi")
    targets = manifest.get("target_ruleset_ids")
    if not isinstance(targets, list) or not targets:
        raise RulesetError("target_ruleset_ids deve ser lista não vazia")
    for reference in targets:
        if not isinstance(reference, str):
            raise RulesetError("target_ruleset_ids deve conter strings")
        preset, _ = resolve_profile(reference, registry)
        if preset.get("family") != "adcc":
            raise RulesetError(f"target deve usar ruleset ADCC: {reference}")
        if preset.get("verification_status") != "verified":
            raise RulesetError(f"target não verificado: {reference}")
        if preset.get("uniform") != manifest["target_uniform"]:
            raise RulesetError(f"target incompatível com target_uniform: {reference}")


def _window_for(ts: float, windows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return next(
        (
            window
            for window in windows
            if float(window["start_s"]) <= ts
            and (window.get("end_s") is None or ts < float(window["end_s"]))
        ),
        None,
    )


def project_adcc_events(
    bout: Mapping[str, Any],
    target_reference: str,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Classify observed actions under an ADCC phase; never calculate a score."""
    registry = registry or load_rulesets()
    preset, profile = resolve_profile(target_reference, registry)
    profile_id = target_reference.rpartition(":")[2]
    if preset.get("family") != "adcc" or preset.get("verification_status") != "verified":
        raise RulesetError("projeção exige preset ADCC verificado")
    relative = bout.get("timing_basis") == "bout_relative"
    absolute_start = (
        bout.get("bout_start_s") if bout.get("timing_basis") == "video_absolute" else None
    )
    projected: list[dict[str, Any]] = []
    for index, event in enumerate(bout.get("events", [])):
        status = "unknown"
        ts = event.get("ts")
        if event.get("officially_awarded") is True:
            status = "officially_awarded"
        elif (
            (not relative and not isinstance(absolute_start, int | float))
            or not isinstance(ts, int | float) or isinstance(ts, bool)
        ):
            status = "unknown"
        else:
            relative_ts = float(ts) if relative else float(ts) - float(absolute_start)
            if profile_id == "overtime":
                overtime_start = bout.get("overtime_start_s")
                if not isinstance(overtime_start, int | float) or isinstance(overtime_start, bool):
                    projected.append({"event_index": index, "status": "unknown"})
                    continue
                if relative_ts < float(overtime_start):
                    projected.append({"event_index": index, "status": "ineligible_phase"})
                    continue
                relative_ts -= float(overtime_start)
            window = _window_for(relative_ts, profile.get("windows", []))
            if window is None:
                status = "unknown"
            else:
                is_negative = event.get("type") in {"penalty", "negative"}
                allowed = window.get("negative") if is_negative else window.get("positive")
                if not allowed:
                    status = "ineligible_phase"
                elif event.get("rule_evidence"):
                    status = "eligible_observed"
                else:
                    status = "potentially_eligible"
        assert status in STATUSES
        projected.append({"event_index": index, "status": status})
    return projected
