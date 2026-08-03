"""Opponent conditions — the branching gates of a decision flowchart.

An opponent *reaction* (something the opponent does in response to an athlete
action) is generalized into an **opponent condition**: a state or action of
the opponent that changes what the athlete should do next. Conditions live in
their own key space (``cond:``-prefixed) so a condition key can never collide
with a technique ``node_key`` (both are ``_normalize_name``-shaped).

Classification precedence (deterministic, no embeddings/LLM):
1. exact canonical Reaction key (``reactions`` catalog: key/name match);
2. curated alias mapping (CONDITION_ALIASES);
3. known label patterns (PATTERN_RULES);
4. event-type mapping (EVENT_TYPE_CONDITIONS);
5. deterministic fallback on the normalized label.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from analysis.names import _normalize_name

ConditionKind = Literal[
    "reaction",
    "posture",
    "base",
    "grip",
    "limb_position",
    "movement",
    "pressure",
    "space",
    "control_state",
    "unknown",
]

# Max meaningful opponent events per condition bundle (plan §7.5).
MAX_BUNDLE_EVENTS = 2


@dataclass(frozen=True)
class OpponentCondition:
    """One classified opponent state/action, canonicalized into cond: key space."""

    key: str
    label: str
    kind: ConditionKind
    reaction_key: str | None
    source_event_keys: tuple[str, ...]


def _cond(key: str, label: str, kind: ConditionKind) -> OpponentCondition:
    return OpponentCondition(key=f"cond:{key}", label=label, kind=kind,
                             reaction_key=None, source_event_keys=(f"cond:{key}",))


# Curated alias map: normalized event label → (key, display label, kind).
# Extend by re-reviewing observed opponent labels (analysis.condition_report).
CONDITION_ALIASES: dict[str, OpponentCondition] = {
    "posts near hand": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "posts his hand": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "posts the near hand": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "posts right hand": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "posts left hand": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "bases with his arm": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "bases with the arm": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "puts hand on mat": _cond("posts-hand", "Opponent posts a hand", "reaction"),
    "postures up": _cond("postures", "Opponent postures up", "posture"),
    "postures": _cond("postures", "Opponent postures up", "posture"),
    "sits up": _cond("sits-up", "Opponent sits up", "posture"),
    "stands up": _cond("stands", "Opponent stands", "movement"),
    "stands": _cond("stands", "Opponent stands", "movement"),
    "bases out": _cond("bases-out", "Opponent bases out", "base"),
    "bases wide": _cond("bases-wide", "Opponent bases wide", "base"),
    "sprawls": _cond("sprawls", "Opponent sprawls", "reaction"),
    "sprawls the leg": _cond("sprawls", "Opponent sprawls", "reaction"),
    "hides the trapped arm": _cond("hides-arm", "Opponent hides the trapped arm", "limb_position"),
    "hides his arm": _cond("hides-arm", "Opponent hides the trapped arm", "limb_position"),
    "pulls the elbow free": _cond("elbow-free", "Opponent pulls the elbow free", "limb_position"),
    "pulls elbow free": _cond("elbow-free", "Opponent pulls the elbow free", "limb_position"),
    "retracts the elbow": _cond("elbow-retracted", "Opponent retracts the elbow", "limb_position"),
    "retracts his elbow": _cond("elbow-retracted", "Opponent retracts the elbow", "limb_position"),
    "squares his hips": _cond("squares-hips", "Opponent squares the hips", "posture"),
    "squares the hips": _cond("squares-hips", "Opponent squares the hips", "posture"),
    "squares hips": _cond("squares-hips", "Opponent squares the hips", "posture"),
    "squares back up": _cond("squares-hips", "Opponent squares the hips", "posture"),
    "connects elbows to knees": _cond(
        "elbows-knees", "Opponent connects elbows to knees", "pressure"),
    "elbows to knees": _cond(
        "elbows-knees", "Opponent connects elbows to knees", "pressure"),
    "controls inside space": _cond("inside-space", "Opponent controls inside space", "space"),
    "wins inside position": _cond("inside-space", "Opponent controls inside space", "space"),
    "wins head height": _cond("head-height", "Opponent wins head height", "pressure"),
    "moves forward": _cond("drives-forward", "Opponent moves forward", "movement"),
    "drives forward": _cond("drives-forward", "Opponent moves forward", "movement"),
    "moves toward you": _cond("drives-forward", "Opponent moves forward", "movement"),
    "pulls away": _cond("pulls-away", "Opponent pulls away", "movement"),
    "pulls the arm away": _cond("pulls-away", "Opponent pulls away", "movement"),
    "hand fights": _cond("hand-fight", "Opponent hand fights", "reaction"),
    "hand fight defense": _cond("hand-fight", "Opponent hand fights", "reaction"),
    "hand-fight-defense": _cond("hand-fight", "Opponent hand fights", "reaction"),
    "escape turn in": _cond("turn-in", "Opponent turns in to escape", "reaction"),
    "escape-turn-in": _cond("turn-in", "Opponent turns in to escape", "reaction"),
    "turns in": _cond("turn-in", "Opponent turns in to escape", "reaction"),
}

# Keyword → condition for labels not in the alias map (normalized-label search).
PATTERN_RULES: tuple[tuple[str, str, str, ConditionKind], ...] = (
    ("post", "posts-hand", "Opponent posts a hand", "reaction"),
    ("base", "bases-out", "Opponent bases out", "base"),
    ("sprawl", "sprawls", "Opponent sprawls", "reaction"),
    ("stand", "stands", "Opponent stands", "movement"),
    ("elbow", "elbow-free", "Opponent pulls the elbow free", "limb_position"),
    ("grip", "grips", "Opponent grips", "grip"),
    ("pressure", "pressure", "Opponent applies pressure", "pressure"),
)

# Event-type mapping for opponent *action* events (last resort before fallback).
EVENT_TYPE_CONDITIONS: dict[str, OpponentCondition] = {
    "escape": _cond("opponent-escapes", "Opponent escapes", "reaction"),
    "submission": _cond("opponent-attacks", "Opponent attacks a submission", "reaction"),
    "pass": _cond("opponent-passes", "Opponent passes", "reaction"),
    "sweep": _cond("opponent-sweeps", "Opponent sweeps", "reaction"),
    "takedown": _cond("opponent-takedown", "Opponent attempts a takedown", "reaction"),
    "transition": _cond("opponent-transitions", "Opponent transitions", "movement"),
}

_FALLBACK_KINDS: dict[str, ConditionKind] = {
    "escape": "reaction",
    "submission": "reaction",
    "pass": "reaction",
    "sweep": "reaction",
    "takedown": "reaction",
    "transition": "movement",
}


def classify_event_condition(
    label: str,
    event_type: str,
    reaction_catalog: list[Any] | None = None,
) -> OpponentCondition:
    """Classify ONE opponent event into a cond:-keyed condition (see precedence)."""
    key = _normalize_name(label)
    if reaction_catalog:
        for r in reaction_catalog:
            rname = getattr(r, "name", "")
            rkey = getattr(r, "key", "")
            if rname and key and _normalize_name(str(rname)) == key:
                return OpponentCondition(
                    key=f"cond:{rkey}", label=str(rname), kind="reaction",
                    reaction_key=str(rkey), source_event_keys=(f"cond:{rkey}",))
    if key in CONDITION_ALIASES:
        return CONDITION_ALIASES[key]
    for kw, ckey, clabel, ckind in PATTERN_RULES:
        if kw in key:
            return _cond(ckey, clabel, ckind)
    if event_type in EVENT_TYPE_CONDITIONS:
        return EVENT_TYPE_CONDITIONS[event_type]
    kind = _FALLBACK_KINDS.get(event_type, "unknown")
    display = (label or key).strip() or "Opponent reacts"
    if display and not display[0].isupper():
        display = display.capitalize()
    if key and not display.lower().startswith("opponent"):
        display = f"Opponent {display}"
    return OpponentCondition(
        key=f"cond:{key}" if key else "cond:unknown",
        label=display,
        kind=kind,
        reaction_key=None,
        source_event_keys=(f"cond:{key}",) if key else (),
    )


def classify_opponent_condition(
    events: list[Any],
    reaction_catalog: list[Any] | None = None,
) -> OpponentCondition | None:
    """Collapse a run of opponent events into ONE condition bundle.

    Deterministic rules: stable-state events (guard/control) are excluded by
    the caller; repeated identical conditions collapse; at most
    ``MAX_BUNDLE_EVENTS`` distinct conditions survive, in event order; two
    survivors become one composite condition ("X and Y").
    """
    if not events:
        return None
    bundle: list[OpponentCondition] = []
    seen: set[str] = set()
    for e in events:
        label = getattr(e, "label", None)
        if not label:
            continue
        c = classify_event_condition(label, getattr(e, "event_type", ""), reaction_catalog)
        if c.key in seen:
            continue
        seen.add(c.key)
        bundle.append(c)
        if len(bundle) >= MAX_BUNDLE_EVENTS:
            break
    if not bundle:
        return None
    if len(bundle) == 1:
        return bundle[0]
    keys = tuple(c.key for c in bundle)
    labels = tuple(c.label for c in bundle)
    return OpponentCondition(
        key=f"{keys[0]}-and-{keys[1]}",
        label=f"{labels[0]} and {labels[1].lower()}",
        kind=bundle[0].kind,
        reaction_key=bundle[0].reaction_key,
        source_event_keys=keys,
    )
