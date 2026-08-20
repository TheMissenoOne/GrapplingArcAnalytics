"""Resolve a claim against the match data, or refuse it.

``build_match_breakdown()`` already assembles the ordered sequence, per-fighter stats,
momentum, transition graph and decision space that drive the site page. The video engine reads
THAT -- it does not recompute takedowns or conversion or momentum. One data truth, three
presentations; a second implementation would drift from the first and nobody would notice
until the page and the video disagreed on screen.

A DERIVED claim is evaluated here, in a deliberately tiny expression language: fact paths, the
four arithmetic operators, parentheses and numbers. No names, no calls, no attribute access on
anything but a path. The point is not expressiveness -- it is that an agent-authored string
can never execute anything.
"""
from __future__ import annotations

import ast
import operator
from collections.abc import Mapping
from typing import Any

from video_engine.contracts import Claim, ClaimKind, EditorialBreakdown

_OPS: dict[type, Any] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                         ast.Div: operator.truediv, ast.USub: operator.neg}


class UnresolvedClaimError(ValueError):
    """A claim that cannot be backed. Never downgraded to a warning: an unbacked objective
    claim burned into a frame is the exact failure this engine exists to prevent."""


def lookup(data: Mapping[str, Any], path: str) -> Any:
    """Dotted path into the breakdown -- ``stats.a.takedowns_landed``. List indices allowed."""
    cur: Any = data
    for part in path.split("."):
        if isinstance(cur, Mapping):
            if part not in cur:
                raise UnresolvedClaimError(f"no such path: {path} (missing {part!r})")
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError) as exc:
                raise UnresolvedClaimError(f"no such index in {path}: {part!r}") from exc
        else:
            raise UnresolvedClaimError(f"cannot descend into {part!r} of {path}")
    return cur


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        raise UnresolvedClaimError("malformed path in formula")
    parts.append(node.id)
    return ".".join(reversed(parts))


def evaluate(data: Mapping[str, Any], formula: str) -> float:
    """Arithmetic over fact paths. Everything else is refused."""
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError as exc:
        raise UnresolvedClaimError(f"unparseable formula: {formula}") from exc

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise UnresolvedClaimError(f"non-numeric constant in formula: {node.value!r}")
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            right = walk(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise UnresolvedClaimError(f"division by zero in formula: {formula}")
            return float(_OPS[type(node.op)](walk(node.left), right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return float(_OPS[type(node.op)](walk(node.operand)))
        if isinstance(node, (ast.Attribute, ast.Name)):
            value = lookup(data, _dotted(node))
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise UnresolvedClaimError(f"path {_dotted(node)} is not numeric: {value!r}")
            return float(value)
        raise UnresolvedClaimError(f"formula may only contain paths and + - * / : {formula}")

    return walk(tree)


def resolve(claim: Claim, breakdown: Mapping[str, Any],
            editorial: EditorialBreakdown | None = None) -> Claim:
    """The claim with ``resolved`` filled, or an exception. There is no third outcome."""
    base = claim.to_dict()
    base["kind"] = claim.kind
    if claim.kind is ClaimKind.FACT:
        if not claim.path:
            raise UnresolvedClaimError(f"FACT claim without a path: {claim.text!r}")
        base["resolved"] = lookup(breakdown, claim.path)
    elif claim.kind is ClaimKind.DERIVED:
        if not claim.formula:
            raise UnresolvedClaimError(f"DERIVED claim without a formula: {claim.text!r}")
        base["resolved"] = evaluate(breakdown, claim.formula)
    else:
        if not claim.segment:
            raise UnresolvedClaimError(
                f"ANALYST claim without a transcript segment: {claim.text!r}")
        if editorial is None or claim.segment not in editorial.segments:
            raise UnresolvedClaimError(
                f"ANALYST claim cites {claim.segment!r}, absent from the editorial transcript")
        base["resolved"] = editorial.segments[claim.segment]
    return Claim(**base)
