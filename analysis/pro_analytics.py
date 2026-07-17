"""Pure, versioned payload builders for the batch-generated Pro tier."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from analysis.names import _normalize_name
from analysis.network_metrics import (
    network_from_sequences,
    node_centralities,
    reward_risk_ranking,
    weighted_pagerank_ranking,
)
from analysis.path_to_victory import path_to_victory
from analysis.style_profile import reduce_style_events

SCHEMA_VERSION = 1
STYLE_MIX_AXES = ("pass", "control", "submission", "escape", "guard", "sweep", "takedown")
MIN_SESSIONS = 3
MIN_GRAPPLING_EVENTS = 15


def _rounds(sessions: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        round_
        for session in sessions
        for round_ in session.get("rounds", [])
        if isinstance(round_, Mapping)
    ]


def _entries(round_: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        entry
        for entry in round_.get("entries", [])
        if isinstance(entry, Mapping) and entry.get("label")
    ]


def _style_events(rounds: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for round_ in rounds:
        for entry in _entries(round_):
            actor = "you" if entry.get("actor") == "you" else "other"
            event = {
                "label": str(entry.get("label", "")),
                "type": str(entry.get("type", "")),
                "actor": actor,
                "successful": entry.get("successful"),
            }
            # App compatibility: an omitted submission result is a landed finish.
            if actor == "you" and event["type"] == "submission" and event["successful"] is None:
                event["successful"] = True
            events.append(event)
    return events


def _style_profile(rounds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    events = _style_events(rounds)
    reduced = reduce_style_events(events)
    own_events = [event for event in events if event["actor"] == "you"]
    record = {"succeeded": 0, "partial": 0, "failed": 0, "noAttempt": 0}
    outcome_key = {
        "succeeded": "succeeded",
        "partial": "partial",
        "failed": "failed",
        "no_attempt": "noAttempt",
    }
    for round_ in rounds:
        outcome = outcome_key.get(str(round_.get("outcome", "")))
        if outcome is not None:
            record[outcome] += 1

    family = reduced["submission_family"]
    family_counts = family["counts"]
    family_shares = family["shares"]
    return {
        "signatures": [
            {"label": item["label"], "count": item["count"], "pct": item["pct"]}
            for item in reduced["signature_techniques"]
        ],
        "styleMix": {axis: reduced["style_mix"].get(axis, 0.0) for axis in STYLE_MIX_AXES},
        "responses": [
            {"situation": situation, "total": value["total"], "moves": value["moves"]}
            for situation, value in reduced["responses"].items()
        ],
        "finishing": {
            "record": record,
            "submissionsLanded": reduced["submissions_landed"],
            "submissionsAttempted": reduced["submissions_attempted"],
            "finishRate": (
                round(reduced["submissions_landed"] / reduced["submissions_attempted"], 3)
                if reduced["submissions_attempted"]
                else 0.0
            ),
            "submissionFamily": [
                {
                    "family": label,
                    "count": count,
                    "pct": family_shares.get(label, 0.0),
                }
                for label, count in family_counts.items()
            ],
            "favoriteFinishes": reduced["favorite_finishes"],
        },
        "_ownEventCount": len(own_events),
    }


def _network(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    sequences = [
        [
            {
                "label": event["label"],
                "type": event["type"],
                "actor_id": event["actor"],
                "successful": event["successful"] is not False,
            }
            for event in events
        ]
    ]
    graph = network_from_sequences(sequences)
    centralities = node_centralities(graph)
    return {
        "hubs": [
            {"label": label, "score": score}
            for label, score in weighted_pagerank_ranking(graph, limit=5)
        ],
        "centralities": centralities,
        "rewardRisk": [
            {"label": label, "value": value, "occurrences": occurrences}
            for label, value, occurrences in reward_risk_ranking(graph, min_occ=1, limit=5)
        ],
        "pathToVictory": [
            {"label": label, "value": value}
            for label, value in sorted(
                path_to_victory(graph).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ],
    }


def _narrative(style: Mapping[str, Any], cadence: str) -> dict[str, Any]:
    signatures = style["signatures"]
    top = signatures[0]["label"] if signatures else "training"
    bullets = [f"{top} was your most frequent move."] if signatures else []
    attempts = style["finishing"]["submissionsAttempted"]
    if attempts:
        bullets.append(f"You logged {attempts} submission attempts.")
    return {"headline": f"Your {cadence} game centers on {top}.", "bullets": bullets}


def build_performance_snapshot_v1(
    sessions: Sequence[Mapping[str, Any]],
    *,
    cadence: str,
    period_start: str,
    period_end: str,
    generated_at: str,
    graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the JSONB payload stored in ``user_performance_snapshots``.

    The function is intentionally pure: sync/job code supplies raw session dictionaries and
    optional already-derived graph context. The emitted ``style`` matches the App's
    ``StyleProfile`` casing exactly, allowing M5 to render a snapshot without a second adapter.
    """
    if cadence not in {"daily", "weekly"}:
        raise ValueError("cadence must be 'daily' or 'weekly'")

    rounds = _rounds(sessions)
    style = _style_profile(rounds)
    event_count = style.pop("_ownEventCount")
    sufficiency = {
        "sessionCount": len(sessions),
        "eventCount": event_count,
        "ready": len(sessions) >= MIN_SESSIONS and event_count >= MIN_GRAPPLING_EVENTS,
    }
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "period": {"cadence": cadence, "start": period_start, "end": period_end},
        "dataSufficiency": sufficiency,
        "status": "ready" if sufficiency["ready"] else "insufficient_data",
    }
    if not sufficiency["ready"]:
        return payload

    events = _style_events(rounds)
    network = _network(events)
    successes = sum(
        1 for event in events if event["actor"] == "you" and event["successful"] is not False
    )
    own_events = max(event_count, 1)
    graph = graph or {}
    payload.update(
        {
            "summary": {
                "durationMin": sum(float(session.get("duration", 0) or 0) for session in sessions),
                "rounds": len(rounds),
                "successRate": round(successes / own_events, 3),
                "eloDelta": graph.get("eloDelta"),
            },
            "style": style,
            "network": {key: value for key, value in network.items() if key != "pathToVictory"},
            "pathToVictory": network["pathToVictory"],
            "archetype": graph.get("archetypeReport"),
            "similarFighters": list(graph.get("similarFighters", [])),
            "oceanContext": graph.get(
                "oceanContext",
                {
                    "regionIds": [],
                    "nodeKeys": sorted({_normalize_name(event["label"]) for event in events}),
                },
            ),
            "narrative": _narrative(style, cadence),
        }
    )
    return payload


def build_athlete_dossier_v1(
    *,
    athlete: Mapping[str, Any],
    style_profile: Mapping[str, Any],
    graph_id: str | None,
    network: Mapping[str, Any],
    path_to_victory: Sequence[Mapping[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    """Wrap the existing athlete style-profile in the App's gated dossier contract."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "athlete": {
            key: athlete[key]
            for key in ("id", "name", "nickname", "team", "weight_class", "belt")
            if key in athlete
        },
        "style": {
            key: style_profile.get(key)
            for key in ("style_mix", "signature_techniques", "responses", "finishing")
        },
        "network": dict(network),
        "pathToVictory": list(path_to_victory),
        "archetype": style_profile.get("archetype"),
        "graphId": graph_id,
        "bouts": list(style_profile.get("bouts", [])),
    }
