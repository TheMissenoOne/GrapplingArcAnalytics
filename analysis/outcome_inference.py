"""Per-action outcome inference — the INTERNAL half of the owner's D7 rule (2026-09-12).

Owner rule: per-action success/failure is the manual flag on the LAST node of a sequence
(``analysis.rating_v2`` / ``scripts.research.rrb_round_rating``'s ``last_flag_only`` mode owns
that half — not this module). Every INTERNAL action's outcome is INFERRED from the resulting
transition, and ``None`` when the sequence cannot resolve it — never auto-success, never
auto-failure (ADR-06 one level down; the same refusal
``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` §8.3 calls "exit orientation may only SUPPRESS an
inference, never create one").

Pre-registered at ``docs/research/outcome_inference_prereg.md`` before any rule below was
scored against ground truth. Contract doc (rules, examples, what stays ``None`` and why):
``docs/taxonomy/05_INFERENCIA_DE_RESULTADO.md``.

This module is pure and reuses production's own curated tables — it invents no second
taxonomy:

* ``analysis.taxonomy_kind.exit_orientation`` / ``data/taxonomy/inference_table.json`` — R0,
  today's rule (``scripts.research.rrb_round_rating.infer_entry_success``, prereg §D2).
* ``analysis.taxonomy_kind.orientation_for_inference`` — R1's wider 3-level stance reading.
* ``analysis.attribution.classify`` — the curated per-(type, label) role every rule below reads.
* ``analysis.lamas_chain.BACK_TAKE_TOKENS`` — R2's back-take label match.

Not wired into ``chain_compiler``/rating yet (owner instruction, 2026-09-12) — this module
delivers the rule and its evidence; wiring it into the App's rating path is a later step.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analysis.attribution import BOTTOM, CONTROLLING, TOP, classify
from analysis.lamas_chain import BACK_TAKE_TOKENS
from analysis.names import _deaccent, _normalize_name
from analysis.taxonomy_kind import (
    exit_orientation as _table_exit_orientation,
)
from analysis.taxonomy_kind import (
    load_inference_table,
    orientation_for_inference,
)


@dataclass(frozen=True)
class StateRef:
    """One state either side of an action. ``actor`` is the state's OWNER, when known — needed
    by R3 only; the other rules never read it."""
    label: str
    type: str
    actor: str | None = None


@dataclass(frozen=True)
class ActionRef:
    """One action. Carries no outcome — that is exactly what this module computes."""
    label: str
    type: str


Rule = Callable[..., "bool | None"]


def _key(label: str) -> str:
    return _normalize_name(_deaccent(str(label or "")))


def _is_back_take(label: str) -> bool:
    return any(t in _key(label) for t in BACK_TAKE_TOKENS)


# ── R0 — today's rule (prereg §D2 / rrb_round_rating.infer_entry_success) ──────────────────
def r0_exit_table(
    prev_state: StateRef | None, action: ActionRef, next_state: StateRef | None,
    actor: str | None, *, table: Mapping[str, Any] | None = None,
    terminal: bool = False, **_: Any,
) -> bool | None:
    """The chain returns to its own source ⇒ failed; the target's curated role matches the
    action type's declared ``action_exit_orientation`` ⇒ landed; a terminal submission with no
    next state ⇒ landed; anything else (including a ``neutral`` declared exit, which is a
    declared NO-CLAIM, never a guess) ⇒ unresolved.

    ``terminal`` is the caller's own signal that this action closes the chain with nothing
    after it (``chain_compiler.ChainEdge.terminal`` once this is wired in) — it defaults to
    ``False`` so an action merely followed by no OBSERVED state (but not actually the chain's
    end) is never mistaken for a submission that landed."""
    del actor
    table = table if table is not None else load_inference_table()
    if next_state is None:
        return True if (terminal and action.type == "submission") else None
    if prev_state is not None and _key(prev_state.label) == _key(next_state.label):
        return False
    orient = _table_exit_orientation(table, action.type)
    if orient == "neutral":
        return None
    role = classify(next_state.type, next_state.label).actor_role
    if role == orient:
        return True
    if role in (TOP, BOTTOM) and role != orient:
        return False
    return None


# ── R1 — orientation flip (sweep/reversal only), the wider 3-level stance reading ──────────
def r1_orientation_flip(
    prev_state: StateRef | None, action: ActionRef, next_state: StateRef | None,
    actor: str | None, **_: Any,
) -> bool | None:
    """Reuses ``orientation_for_inference`` — the 3-level reading (declared table, then the
    library's canonical name, then ``attribution``'s curated role) that resolves labels R0's
    type-keyed exit table cannot (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md §8.2). Landed iff the
    next state reads ``top`` for the actor; failed only when it is STILL ``bottom`` and the
    chain started ``bottom`` too — the same guard family, never a claim across axes."""
    del actor
    if action.type not in ("sweep", "reversal") or next_state is None:
        return None
    next_stance = orientation_for_inference(next_state.type, next_state.label).value
    if next_stance == "top":
        return True
    if next_stance == "bottom" and prev_state is not None:
        prev_stance = orientation_for_inference(prev_state.type, prev_state.label).value
        if prev_stance == "bottom":
            return False
    return None


# ── R2 — type -> expected next-state family ────────────────────────────────────────────────
def r2_type_expectation(
    prev_state: StateRef | None, action: ActionRef, next_state: StateRef | None,
    actor: str | None, *, terminal: bool = False, **_: Any,
) -> bool | None:
    """One curated expectation per action type, read off the SAME ``attribution.classify`` role
    table every other rule in this module reads — no second taxonomy. Submission is handled
    here only for the terminal case; the return-to-source failure is R0's job (fires first in
    every composition that includes R0, so this rule is never the one asked to invent it)."""
    del prev_state, actor
    if next_state is None:
        return None
    role = classify(next_state.type, next_state.label).actor_role

    if action.type in ("pass", "takedown"):
        if role == TOP:
            return True
        if role == BOTTOM:
            return False
        return None
    if action.type == "guard":  # a guard PULL — the actor ends up in a guard, i.e. bottom
        if role == BOTTOM:
            return True
        if role == TOP:
            return False
        return None
    if _is_back_take(action.label):
        if role == CONTROLLING:
            return True
        if role in (TOP, BOTTOM):
            return False
        return None
    if action.type == "escape":
        stance = orientation_for_inference(next_state.type, next_state.label).value
        if stance == "neutral" or (next_state.type == "guard" and stance == "bottom"):
            return True
        if next_state.type == "control" and stance == "bottom":
            return False
        return None
    if action.type == "submission":
        # Landed iff the chain closes on the `finish` anchor (chain_compiler's generic node,
        # `role='finish'`) or this action IS the chain's own terminal action with no next state
        # at all. The "sequence continues from the same state under the opponent's control"
        # failure is already R0's return-to-source rule, which fires first in every composition
        # that includes it — this rule is never asked to invent that negative on its own.
        if _key(next_state.label) == "finish" or terminal:
            return True
        return None
    return None


# ── R3 — actor consistency, gated by reliability ───────────────────────────────────────────
def r3_actor_consistency(
    prev_state: StateRef | None, action: ActionRef, next_state: StateRef | None,
    actor: str | None, *, actor_readable: bool = True, **_: Any,
) -> bool | None:
    """When the actor field can be trusted (``actor_readable`` — the same gate
    ``attribution.bout_flags``/``taxonomy_kind.infer_transition_actions`` already require) and
    the state after the action belongs to someone OTHER than the action's own actor, the action
    failed — the position moved to the other side. Never infers ``True`` from actor agreement:
    staying in a state you already owned is not evidence an action landed, only that nothing
    disproved it."""
    del prev_state
    if not actor_readable or next_state is None or next_state.actor is None or actor is None:
        return None
    if next_state.actor != actor:
        return False
    return None


# R3 is measured (docs/taxonomy/05_INFERENCIA_DE_RESULTADO.md) to buy resolution at a precision
# COST below today's rule — death rule 14's condition fires, so it ships defined (for the
# record, and because it is still correct on the cases it DOES touch — see its own docstring)
# but excluded from the shipped default.
DEFAULT_RULES: tuple[Rule, ...] = (r0_exit_table, r1_orientation_flip, r2_type_expectation)

# Named cumulative orderings, for the prereg's arm comparison (docs/research/
# outcome_inference_prereg.md) — evaluated in this order, first non-`None` wins.
RULE_SETS: dict[str, tuple[Rule, ...]] = {
    "R0": (r0_exit_table,),
    "R0+R1": (r0_exit_table, r1_orientation_flip),
    "R0+R1+R2": DEFAULT_RULES,
    "R0+R1+R2+R3 (rejected, death rule 14)": (*DEFAULT_RULES, r3_actor_consistency),
}


def infer_outcome(
    prev_state: StateRef | None, action: ActionRef, next_state: StateRef | None,
    actor: str | None, *, rules: Sequence[Rule] = DEFAULT_RULES,
    table: Mapping[str, Any] | None = None, actor_readable: bool = True,
    terminal: bool = False,
) -> bool | None:
    """One action's inferred outcome — the first rule in ``rules`` that answers, in order.
    ``None`` when every rule refuses: the sequence genuinely does not say.

    ``terminal`` mirrors ``chain_compiler.ChainEdge.terminal`` — this action closes the chain
    with no observed action/state after it. Defaults to ``False`` so a caller that has not
    computed it never accidentally reads "no next state" as "the chain ended here"."""
    table = table if table is not None else load_inference_table()
    for rule in rules:
        result = rule(prev_state, action, next_state, actor,
                       table=table, actor_readable=actor_readable, terminal=terminal)
        if result is not None:
            return result
    return None
