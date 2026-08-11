"""Technique taxonomy — loader, validation, and the ancestor walk.

``docs/taxonomy.json`` (schema v2) is the curated category tree: 9 categories, 86
subcategories and 26 concepts (12 of which are flagged ``principle``). It is reference
data — curated by hand, versioned, never derived from the DB.

This module is the only reader. It exists because nothing could previously answer
"what family does this subcategory belong to", which is what the hierarchical level
selection in ``analysis/decision_criteria`` needs:

    ashi-barai -> foot-sweep-trip-reap-ashi-waza -> takedown

Schema v2 notes (v1 was structurally broken — see the taxonomy plan):
  * ``parents`` is a LIST. A subcategory can sit under two categories: ``guard-recovery``
    is both an Escape and a Transition, which v1 tried to express by writing the row
    twice under one id, silently losing one to any dict build.
  * principles are not a separate kind. They are concepts carrying ``principle: true``,
    so a technique references ONE vocabulary rather than two overlapping ones.

    from analysis.taxonomy import load_taxonomy
    tax = load_taxonomy()
    tax.ancestors("closed-guard")        # ["guard"]
    tax.resolve("smash pass")            # "pressure-pass"

    uv run python -m analysis.taxonomy   # validate + print a summary
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from analysis.names import _normalize_name

Kind = Literal["category", "subcategory", "concept"]

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "docs" / "taxonomy.json"

SUPPORTED_VERSION = 2


class TaxonomyError(ValueError):
    """Raised when the taxonomy file violates one of its invariants."""


@dataclass(frozen=True)
class TaxonomyNode:
    id: str
    name: str
    kind: Kind
    parents: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    principle: bool = False


@dataclass
class Taxonomy:
    nodes: dict[str, TaxonomyNode]
    version: int = SUPPORTED_VERSION
    # normalized alias/name -> node id. Built once; ambiguous names are dropped rather
    # than resolved arbitrarily (the mapping proposer sends those to manual review).
    _index: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self._index:
            self._index = self._build_index()

    def _build_index(self) -> dict[str, str]:
        hits: dict[str, set[str]] = {}
        for node in self.nodes.values():
            for raw in (node.name, *node.aliases):
                key = _normalize_name(raw)
                if key:
                    hits.setdefault(key, set()).add(node.id)
        # an alias claimed by two nodes is not a resolution — drop it
        return {k: next(iter(v)) for k, v in hits.items() if len(v) == 1}

    # ---- lookups ---------------------------------------------------------------
    def get(self, node_id: str) -> TaxonomyNode | None:
        return self.nodes.get(node_id)

    def resolve(self, label: str) -> str | None:
        """Normalized name/alias -> node id. None when unknown or ambiguous."""
        return self._index.get(_normalize_name(label))

    def ancestors(self, node_id: str) -> list[str]:
        """All ancestors root-ward, nearest first, deduped and deterministic.

        Multi-parent nodes yield both chains (``guard-recovery`` -> escape, transition).
        Order is breadth-first by distance, then by id, so callers get a stable
        specific-to-general sequence to walk.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return []
        out: list[str] = []
        seen = {node_id}
        frontier = sorted(node.parents)
        while frontier:
            nxt: list[str] = []
            for pid in frontier:
                if pid in seen or pid not in self.nodes:
                    continue
                seen.add(pid)
                out.append(pid)
                nxt.extend(self.nodes[pid].parents)
            frontier = sorted(set(nxt))
        return out

    def chain(self, node_id: str) -> list[str]:
        """The node itself followed by its ancestors — the levels to test, specific first."""
        return ([node_id] if node_id in self.nodes else []) + self.ancestors(node_id)

    def descendants(self, node_id: str) -> list[str]:
        """Every node beneath this one, breadth-first, deduped and sorted per level."""
        if node_id not in self.nodes:
            return []
        children: dict[str, list[str]] = {}
        for n in self.nodes.values():
            for p in n.parents:
                children.setdefault(p, []).append(n.id)
        out: list[str] = []
        seen = {node_id}
        frontier = sorted(children.get(node_id, []))
        while frontier:
            nxt: list[str] = []
            for cid in frontier:
                if cid in seen:
                    continue
                seen.add(cid)
                out.append(cid)
                nxt.extend(children.get(cid, []))
            frontier = sorted(set(nxt))
        return out

    def children(self, node_id: str) -> list[str]:
        return sorted(n.id for n in self.nodes.values() if node_id in n.parents)

    def siblings(self, node_id: str) -> list[str]:
        """Nodes sharing at least one parent, excluding the node itself.

        This is what the level-selection contrast needs: a child is only more informative
        than its family if it differs from its SIBLINGS. Comparing it to the parent is
        meaningless because the parent's events contain the child's.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return []
        out = {
            n.id
            for n in self.nodes.values()
            if n.id != node_id and set(n.parents) & set(node.parents)
        }
        return sorted(out)

    def by_kind(self, kind: Kind) -> list[TaxonomyNode]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def category_of(self, node_id: str) -> str | None:
        """The top-level category a node rolls up to (first one, for multi-parent nodes)."""
        if (node := self.nodes.get(node_id)) and node.kind == "category":
            return node_id
        for anc in self.ancestors(node_id):
            if self.nodes[anc].kind == "category":
                return anc
        return None


def _validate(nodes: dict[str, TaxonomyNode], raw_count: int) -> None:
    if len(nodes) != raw_count:
        raise TaxonomyError(f"duplicate ids: {raw_count} rows -> {len(nodes)} unique")
    for node in nodes.values():
        if node.kind not in ("category", "subcategory", "concept"):
            raise TaxonomyError(f"{node.id}: bad kind {node.kind!r}")
        if node.kind == "category" and node.parents:
            raise TaxonomyError(f"category {node.id} must be a root")
        if node.kind == "subcategory" and not node.parents:
            raise TaxonomyError(f"subcategory {node.id} is orphaned")
        for pid in node.parents:
            if pid not in nodes:
                raise TaxonomyError(f"{node.id} -> unknown parent {pid!r}")
    # cycles
    for node in nodes.values():
        seen: set[str] = set()
        stack = list(node.parents)
        while stack:
            cur = stack.pop()
            if cur == node.id:
                raise TaxonomyError(f"cycle through {node.id}")
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(nodes[cur].parents)


def parse_taxonomy(payload: dict[str, Any]) -> Taxonomy:
    """Build + validate a Taxonomy from already-loaded JSON (pure — no file I/O)."""
    version = int(payload.get("version", 0))
    if version != SUPPORTED_VERSION:
        raise TaxonomyError(
            f"taxonomy schema v{version} unsupported (expected v{SUPPORTED_VERSION})"
        )
    raw = payload.get("nodes") or []
    nodes: dict[str, TaxonomyNode] = {}
    for item in raw:
        node = TaxonomyNode(
            id=str(item["id"]),
            name=str(item["name"]),
            kind=item["kind"],
            parents=tuple(item.get("parents") or ()),
            aliases=tuple(item.get("aliases") or ()),
            principle=bool(item.get("principle", False)),
        )
        nodes.setdefault(node.id, node)
    _validate(nodes, len(raw))
    return Taxonomy(nodes=nodes, version=version)


@lru_cache(maxsize=4)
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    """Load + validate ``docs/taxonomy.json``. Cached — it is immutable reference data."""
    p = path or _DEFAULT_PATH
    return parse_taxonomy(json.loads(p.read_text(encoding="utf-8")))


def main() -> int:
    tax = load_taxonomy()
    kinds: dict[str, int] = {}
    for n in tax.nodes.values():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    print(f"taxonomy v{tax.version}: {len(tax.nodes)} nodes {kinds}")
    print(f"principles: {sum(1 for n in tax.nodes.values() if n.principle)}")
    print(f"aliases indexed: {len(tax._index)}")
    multi = [n.id for n in tax.nodes.values() if len(n.parents) > 1]
    print(f"multi-parent: {multi}")
    for probe in ("closed-guard", "guard-recovery", "concept-off-balancing"):
        print(f"  {probe}: chain={tax.chain(probe)} category={tax.category_of(probe)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
