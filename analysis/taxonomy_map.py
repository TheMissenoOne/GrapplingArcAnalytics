"""Propose a technique -> taxonomy-subcategory mapping. REVIEW ARTIFACT ONLY, no DB writes.

This is the missing input that kanban card 017 (``technique_nodes.taxonomy_id``) is blocked
on: the taxonomy has 86 subcategories, the DB has ~437 technique nodes, and nothing connects
them. Rather than hand-classify 437 rows, this proposes a mapping and tiers it by how much
trust it deserves, so review effort goes where the machine is unsure.

Tiers
  auto    — the label (or an alias) resolves exactly to a subcategory INSIDE the category
            its ``node_type`` already implies. Both signals agree; safe to accept in bulk.
  review  — a single plausible candidate from a weaker signal (token containment, or
            embedding similarity). Every embedding-derived proposal lands here by design:
            semantic similarity is a hint, not a classification.
  manual  — no candidate, several equally good ones, an out-of-taxonomy ``node_type``, or a
            name the taxonomy itself cannot disambiguate ("body lock" is both a grip and a
            takedown). These need a human decision, and there is no safe default.

The **type gate** is the ELO-safety property, same idea as ``analysis/canonicalize``: a node's
existing ``node_type`` constrains which category it may map into, so the mapping can never
silently reclassify a node that the scorer, graph renderer or directed-edge rules read.

    uv run python -m analysis.taxonomy_map            # read prod (read-only), write report
    uv run python -m analysis.taxonomy_map --check    # in-memory self-check, no DB
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from analysis.canonicalize import Node
from analysis.names import _normalize_name
from analysis.taxonomy import Taxonomy, load_taxonomy

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MAP = _ROOT / "data" / "taxonomy_map.json"
_DEFAULT_REPORT = _ROOT / "docs" / "taxonomy_mapping_review.md"

# node_type -> the ONE taxonomy category a node of that type may map into.
TYPE_TO_CATEGORY: dict[str, str] = {
    "submission": "submission",
    "control": "control",
    "takedown": "takedown",
    "transition": "transition",
    "guard": "guard",
    "pass": "pass",
    "escape": "escape",
    "sweep": "sweep",
    # "takedown defense" / sprawl / single-leg defense: defensive reactions, Escape family.
    "defensive": "escape",
}
# Not grappling techniques. Match-log artifacts that belong in an `event` class, per the
# taxonomy plan Part 5 — reported, never mapped.
OUT_OF_TAXONOMY = frozenset({"strike", "penalty", "match"})
# `concept`-typed rows are mislabeled techniques (Arm Drag, Berimbolo, Worm Guard...), not
# taxonomy concepts. They need a human to say what they actually are.
NEEDS_RETYPE = frozenset({"concept"})

# Bare labels that match a taxonomy name exactly but do not MEAN it, so an exact-name hit
# would be confidently wrong. Forced to manual review.
#   choke      — the taxonomy's `choke` is the airway/tracheal sense, but a BJJ node labelled
#                just "Choke" is nearly always a blood strangle.
#   guillotine — has both a blood and an air variant; the bare label picks neither.
AMBIGUOUS_LABELS = frozenset({"choke", "guillotine", "neck crank", "lock"})

# mpnet cosine between a technique label and a subcategory name. Deliberately permissive —
# everything it produces is `review` anyway, so a low bar costs review time, not correctness.
EMBED_THRESHOLD = 0.50

Tier = str  # "auto" | "review" | "manual"


@dataclass
class Proposal:
    node_key: str
    label: str
    node_type: str
    subcategory: str | None = None
    category: str | None = None
    # "subcategory" normally; "category" when the label IS the generic bucket ("Guard Pass",
    # "Submission"). Mapping those to the category is the honest answer — inventing a
    # subcategory for a node that names a whole family would be a fabricated detail.
    level: str | None = None
    tier: Tier = "manual"
    method: str = "none"
    score: float | None = None
    candidates: list[str] = field(default_factory=list)
    note: str = ""
    # a deliberate "a human must decide this" verdict — the embedding rescue must not
    # quietly overturn it with a similarity score.
    final: bool = False


def _tokens(label: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", label.lower())


# Words that carry no discriminating signal on their own: every category name plus the
# filler that shows up in half the library. A ONE-word subcategory named with one of these
# would match nearly every label in its own family, so it is not allowed to fire alone.
_GENERIC_TOKENS = frozenset(
    {"control", "guard", "pass", "sweep", "escape", "submission", "takedown",
     "transition", "grip", "position", "attack", "defense", "lock", "throw"}
)


def _token_containment(label: str, target: str) -> bool:
    """Every word of the subcategory name appears in the label, order-free.

    Catches "Closed Guard Sweep" -> closed-guard. A single-word target still counts when the
    word is distinctive ("Strangle", "Pin", "Reversal"), but not when it is a category-level
    word — "Guard Pass" must not become the `guard` family just by containing "guard".
    """
    tt = _tokens(target)
    if not tt:
        return False
    if len(tt) == 1 and tt[0] in _GENERIC_TOKENS:
        return False
    lt = set(_tokens(label))
    return all(t in lt for t in tt)


def _candidate_subcategories(tax: Taxonomy, category: str) -> list[str]:
    return [n for n in tax.descendants(category) if tax.nodes[n].kind == "subcategory"]


def propose_one(node: Node, tax: Taxonomy) -> Proposal:
    """String-signal proposal for one node. Pure — no embeddings, no DB, no I/O."""
    p = Proposal(node_key=node.key, label=node.label, node_type=node.node_type)

    if node.node_type in OUT_OF_TAXONOMY:
        p.note = "out-of-taxonomy node_type — belongs in an event class"
        return p
    if node.node_type in NEEDS_RETYPE:
        p.note = "mislabeled `concept` row — needs a real node_type first"
        return p

    category = TYPE_TO_CATEGORY.get(node.node_type)
    if category is None:
        p.note = f"unknown node_type {node.node_type!r}"
        return p
    if _normalize_name(node.label) in AMBIGUOUS_LABELS:
        p.category = category
        p.final = True
        p.note = "bare label matches a taxonomy name but does not mean it — decide explicitly"
        return p
    p.category = category
    subcats = set(_candidate_subcategories(tax, category))
    # the category itself is a legal target: a node literally labelled "Guard Pass" or
    # "Submission" names the whole family, and the coarse mapping is the correct one.
    allowed = subcats | {category}

    # 1. exact name/alias resolution, gated by type
    hit = tax.resolve(node.label)
    if hit is not None:
        if hit in allowed:
            p.subcategory, p.tier, p.method, p.score = hit, "auto", "alias", 1.0
            p.level = "category" if hit == category else "subcategory"
            return p
        # resolves, but into the wrong family — exactly the conflict a human must settle
        p.candidates = [hit]
        p.note = f"resolves to {hit!r}, outside the {category!r} its node_type implies"
        return p

    # 2. token containment against subcategory names + aliases, gated by type
    matches = {
        sub
        for sub in subcats
        for name in (tax.nodes[sub].name, *tax.nodes[sub].aliases)
        if _token_containment(node.label, name)
    }
    if len(matches) == 1:
        p.subcategory = matches.pop()
        p.tier, p.method, p.score = "review", "tokens", None
        p.level = "subcategory"
        return p
    if len(matches) > 1:
        p.candidates = sorted(matches)
        p.note = "several subcategory names appear in the label"
        return p

    p.note = "no string signal"
    return p


def propose_all(
    nodes: list[Node],
    tax: Taxonomy,
    *,
    embed_lookup: Callable[[str], np.ndarray | None] | None = None,
    sub_vectors: dict[str, np.ndarray] | None = None,
    threshold: float = EMBED_THRESHOLD,
) -> list[Proposal]:
    """Propose for every node, using embeddings only to rescue the string misses.

    ``embed_lookup`` maps a node_key to its unit vector; ``sub_vectors`` maps a subcategory
    id to its unit vector. Both optional — without them this is the pure string proposer.
    """
    out: list[Proposal] = []
    for node in nodes:
        p = propose_one(node, tax)
        if (
            p.subcategory is None
            and p.category is not None
            and not p.final
            and not p.candidates
            and embed_lookup is not None
            and sub_vectors
        ):
            vec = embed_lookup(node.key)
            if vec is None:
                # not a classification failure — the node simply has no pgvector row, so the
                # rescue tier never ran. Say which, or review time gets spent on the wrong thing.
                p.note = "no string signal, and no embedding to fall back on"
            else:
                allowed = [
                    s for s in _candidate_subcategories(tax, p.category) if s in sub_vectors
                ]
                if allowed:
                    sims = np.array([float(vec @ sub_vectors[s]) for s in allowed])
                    best = int(sims.argmax())
                    if sims[best] >= threshold:
                        p.subcategory = allowed[best]
                        p.tier, p.method = "review", "embedding"
                        p.level = "subcategory"
                        p.score = round(float(sims[best]), 4)
                        p.note = ""
                        # a near-tie is not a proposal, it is a coin flip
                        rest = np.delete(sims, best)
                        if rest.size and float(rest.max()) >= float(sims[best]) - 0.02:
                            p.candidates = [allowed[int(np.argsort(-sims)[1])]]
                            p.note = "near-tie with the runner-up"
        out.append(p)
    return out


def summarize(proposals: list[Proposal]) -> dict[str, Any]:
    tiers: dict[str, int] = {}
    methods: dict[str, int] = {}
    for p in proposals:
        tiers[p.tier] = tiers.get(p.tier, 0) + 1
        methods[p.method] = methods.get(p.method, 0) + 1
    covered = sum(1 for p in proposals if p.subcategory)
    return {
        "total_nodes": len(proposals),
        "mapped": covered,
        "unmapped": len(proposals) - covered,
        "coverage_pct": round(100.0 * covered / len(proposals), 1) if proposals else 0.0,
        "tiers": tiers,
        "methods": methods,
    }


def _render_markdown(proposals: list[Proposal], summary: dict[str, Any]) -> str:
    lines = [
        "# Technique → taxonomy mapping — review",
        "",
        "Generated by `analysis/taxonomy_map.py`. **Proposal only — nothing is written to "
        "the DB.** Confirm the `review` and `manual` sections; `auto` agreed on two "
        "independent signals (exact name/alias *and* the node_type gate).",
        "",
        f"- nodes: **{summary['total_nodes']}**",
        f"- mapped: **{summary['mapped']}** ({summary['coverage_pct']}%)",
        f"- tiers: {summary['tiers']}",
        f"- methods: {summary['methods']}",
        "",
    ]
    for tier, blurb in (
        ("review", "One plausible candidate from a weak signal — confirm or correct."),
        ("manual", "No safe default. Needs a decision."),
        ("auto", "Two signals agreed. Skim only."),
    ):
        rows = [p for p in proposals if p.tier == tier]
        lines += [f"## {tier} ({len(rows)})", "", f"_{blurb}_", ""]
        if not rows:
            lines += ["_none_", ""]
            continue
        lines += ["| label | node_type | → subcategory | method | score | note |",
                  "|---|---|---|---|---|---|"]
        for p in sorted(rows, key=lambda x: (x.node_type, x.label.lower())):
            target = p.subcategory or (
                " / ".join(p.candidates) if p.candidates else "—"
            )
            score = "" if p.score is None else f"{p.score:.3f}"
            lines.append(
                f"| {p.label} | `{p.node_type}` | `{target}` | {p.method} | {score} | {p.note} |"
            )
        lines.append("")
    return "\n".join(lines)


def generate(
    session: Any,
    *,
    out_map: Path = _DEFAULT_MAP,
    out_report: Path = _DEFAULT_REPORT,
    threshold: float = EMBED_THRESHOLD,
) -> dict[str, Any]:
    """Read technique_nodes + embeddings (read-only), write the proposed map + review doc."""
    from sqlalchemy import select

    from analysis.embeddings import embed_texts
    from db.models import TechniqueNode as T

    rows = list(session.execute(select(T.node_key, T.label, T.node_type, T.source, T.embedding)))
    nodes = [Node(k, lbl, nt or "", src or "user") for k, lbl, nt, src, _ in rows]

    vectors: dict[str, np.ndarray] = {}
    for k, _lbl, _nt, _src, emb in rows:
        if emb is not None:
            v = np.asarray(emb, dtype=np.float64)
            vectors[k] = v / (np.linalg.norm(v) + 1e-12)

    tax = load_taxonomy()
    subs = [n.id for n in tax.by_kind("subcategory")]
    texts = [
        " ".join([tax.nodes[s].name, *tax.nodes[s].aliases[:3]]) for s in subs
    ]
    sub_mat = embed_texts(texts)
    sub_mat = sub_mat / (np.linalg.norm(sub_mat, axis=1, keepdims=True) + 1e-12)
    sub_vectors = dict(zip(subs, sub_mat, strict=True))

    proposals = propose_all(
        nodes, tax,
        embed_lookup=vectors.get, sub_vectors=sub_vectors, threshold=threshold,
    )
    summary = summarize(proposals)

    out_map.parent.mkdir(parents=True, exist_ok=True)
    out_map.write_text(
        json.dumps(
            {
                "taxonomy_version": tax.version,
                "summary": summary,
                "proposals": [vars(p) for p in proposals],
            },
            indent=2, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(_render_markdown(proposals, summary) + "\n", encoding="utf-8")
    logger.info(
        "%d nodes → %d mapped (%s%%); tiers=%s → %s",
        summary["total_nodes"], summary["mapped"], summary["coverage_pct"],
        summary["tiers"], out_report,
    )
    return summary


def _check() -> None:
    """In-memory self-check: the type gate, each tier, and the ambiguity guards."""
    tax = load_taxonomy()

    # exact alias + agreeing node_type -> auto
    p = propose_one(Node("closed guard", "Closed Guard", "guard", "library"), tax)
    assert (p.tier, p.subcategory, p.method) == ("auto", "closed-guard", "alias"), p
    p = propose_one(Node("smash pass", "Smash Pass", "pass", "user"), tax)
    assert (p.tier, p.subcategory) == ("auto", "pressure-pass"), p

    # THE ELO-SAFETY PROPERTY: a name that resolves outside its node_type's category
    # must never auto-map. "Closed Guard" typed `submission` is a data error, not a mapping.
    p = propose_one(Node("closed guard", "Closed Guard", "submission", "user"), tax)
    assert p.tier == "manual" and p.subcategory is None, p
    assert "outside" in p.note, p

    # token containment -> review, never auto
    p = propose_one(Node("bow and arrow", "Bow And Arrow Strangle", "submission", "user"), tax)
    assert (p.tier, p.subcategory, p.method) == ("review", "strangle", "tokens"), p

    # single-word targets must not fire: "Guard Pass" should not become the `guard` family
    p = propose_one(Node("guard pass", "Guard Pass", "pass", "user"), tax)
    assert p.subcategory != "guard", p

    # out-of-taxonomy + mislabeled types stay manual
    for nt in ("strike", "penalty", "match", "concept"):
        p = propose_one(Node(f"x {nt}", f"X {nt}", nt, "user"), tax)
        assert p.tier == "manual" and p.subcategory is None, (nt, p)

    # unknown node_type is manual, not a crash
    assert propose_one(Node("q", "Q", "wat", "user"), tax).tier == "manual"

    # ambiguous taxonomy name resolves to nothing, so it cannot auto-map
    p = propose_one(Node("body lock", "Body Lock", "takedown", "user"), tax)
    assert p.method != "alias" or p.subcategory is None, p

    # embedding tier: lands in `review`, and a near-tie is flagged
    sub_vectors = {
        "arm-lock": np.array([1.0, 0.0]),
        "leg-lock": np.array([0.0, 1.0]),
    }
    node = Node("mystery", "Mystery Lock", "submission", "user")
    got = propose_all(
        [node], tax,
        embed_lookup={"mystery": np.array([1.0, 0.0])}.get,
        sub_vectors=sub_vectors, threshold=0.5,
    )[0]
    assert (got.tier, got.subcategory, got.method) == ("review", "arm-lock", "embedding"), got

    tie = propose_all(
        [node], tax,
        embed_lookup={"mystery": np.array([0.7071, 0.7071])}.get,
        sub_vectors=sub_vectors, threshold=0.5,
    )[0]
    assert tie.candidates and "near-tie" in tie.note, tie

    # below threshold -> no proposal at all
    low = propose_all(
        [node], tax,
        embed_lookup={"mystery": np.array([0.1, 0.0])}.get,
        sub_vectors=sub_vectors, threshold=0.9,
    )[0]
    assert low.subcategory is None and low.tier == "manual", low

    print("taxonomy_map self-check OK")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Propose technique→taxonomy mapping (read-only)")
    ap.add_argument("--threshold", type=float, default=EMBED_THRESHOLD)
    ap.add_argument("--check", action="store_true", help="in-memory self-check, no DB")
    args = ap.parse_args()
    if args.check:
        _check()
        return 0
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from db.base import db_session

    with db_session() as session:
        generate(session, threshold=args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
