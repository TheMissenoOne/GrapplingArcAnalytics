"""Taxonomy loader, validation and ancestor-walk tests.

The validation half matters more than usual: schema v1 shipped with a duplicate id that
went unnoticed precisely because nothing loaded the file. These tests are what stop a v3
from doing the same.
"""

from __future__ import annotations

import json

import pytest

from analysis.taxonomy import (
    SUPPORTED_VERSION,
    TaxonomyError,
    load_taxonomy,
    parse_taxonomy,
)


def _payload(*nodes: dict) -> dict:
    return {"version": SUPPORTED_VERSION, "generated": "test", "nodes": list(nodes)}


def _cat(nid: str) -> dict:
    return {"id": nid, "name": nid.title(), "kind": "category", "parents": []}


def _sub(nid: str, *parents: str, aliases: list[str] | None = None) -> dict:
    return {
        "id": nid,
        "name": nid.replace("-", " ").title(),
        "kind": "subcategory",
        "parents": list(parents),
        "aliases": aliases or [],
    }


# ---------------------------------------------------------------- real file


def test_real_taxonomy_loads_and_validates():
    tax = load_taxonomy()
    assert tax.version == SUPPORTED_VERSION
    assert len(tax.nodes) == 121
    kinds: dict[str, int] = {}
    for n in tax.nodes.values():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    assert kinds == {"category": 9, "subcategory": 86, "concept": 26}


def test_real_taxonomy_has_no_duplicate_ids():
    """The exact v1 defect: 128 rows collapsing to 127 unique ids."""
    raw = json.loads(
        (__import__("pathlib").Path(__file__).resolve().parents[1] / "docs" / "taxonomy.json")
        .read_text(encoding="utf-8")
    )
    ids = [n["id"] for n in raw["nodes"]]
    assert len(ids) == len(set(ids))


def test_guard_recovery_is_one_node_with_two_parents():
    tax = load_taxonomy()
    node = tax.get("guard-recovery")
    assert node is not None
    assert set(node.parents) == {"escape", "transition"}


def test_principles_are_concepts_with_a_facet():
    tax = load_taxonomy()
    principles = [n for n in tax.nodes.values() if n.principle]
    assert len(principles) == 12
    assert all(n.kind == "concept" for n in principles)
    assert not any(n.id.startswith("principle-") for n in tax.nodes.values())


# ---------------------------------------------------------------- validation


def test_duplicate_id_is_rejected():
    with pytest.raises(TaxonomyError, match="duplicate ids"):
        parse_taxonomy(_payload(_cat("a"), _sub("x", "a"), _sub("x", "a")))


def test_unknown_parent_is_rejected():
    with pytest.raises(TaxonomyError, match="unknown parent"):
        parse_taxonomy(_payload(_cat("a"), _sub("x", "nope")))


def test_cycle_is_rejected():
    a = {"id": "a", "name": "A", "kind": "subcategory", "parents": ["b"]}
    b = {"id": "b", "name": "B", "kind": "subcategory", "parents": ["a"]}
    with pytest.raises(TaxonomyError, match="cycle"):
        parse_taxonomy(_payload(a, b))


def test_orphan_subcategory_is_rejected():
    with pytest.raises(TaxonomyError, match="orphaned"):
        parse_taxonomy(_payload(_sub("x")))


def test_category_with_parent_is_rejected():
    bad = {"id": "c", "name": "C", "kind": "category", "parents": ["a"]}
    with pytest.raises(TaxonomyError, match="must be a root"):
        parse_taxonomy(_payload(_cat("a"), bad))


def test_wrong_schema_version_is_rejected():
    with pytest.raises(TaxonomyError, match="unsupported"):
        parse_taxonomy({"version": 1, "nodes": []})


# ---------------------------------------------------------------- traversal


def test_ancestors_walks_root_ward():
    tax = load_taxonomy()
    assert tax.ancestors("closed-guard") == ["guard"]
    assert tax.chain("closed-guard") == ["closed-guard", "guard"]


def test_ancestors_of_multi_parent_node_yields_both_chains():
    tax = load_taxonomy()
    assert set(tax.ancestors("guard-recovery")) == {"escape", "transition"}


def test_ancestors_of_unknown_node_is_empty():
    tax = load_taxonomy()
    assert tax.ancestors("does-not-exist") == []
    assert tax.chain("does-not-exist") == []


def test_ancestors_is_deterministic():
    tax = load_taxonomy()
    assert tax.ancestors("guard-recovery") == tax.ancestors("guard-recovery")
    assert tax.ancestors("guard-recovery") == sorted(tax.ancestors("guard-recovery"))


def test_category_of():
    tax = load_taxonomy()
    assert tax.category_of("closed-guard") == "guard"
    assert tax.category_of("guard") == "guard"
    assert tax.category_of("concept-pressure") is None


def test_children_and_descendants():
    tax = load_taxonomy()
    assert "closed-guard" in tax.children("guard")
    assert "closed-guard" in tax.descendants("guard")
    assert tax.children("closed-guard") == []


def test_siblings_excludes_self_and_shares_a_parent():
    """Level selection contrasts a child against its SIBLINGS, never its parent."""
    tax = load_taxonomy()
    sibs = tax.siblings("closed-guard")
    assert "closed-guard" not in sibs
    assert "open-guard" in sibs
    assert "pressure-pass" not in sibs  # different category


def test_deep_chain_terminates():
    """A grandparent chain resolves specific -> general without repeats."""
    payload = _payload(
        _cat("root"), _sub("mid", "root"),
        {"id": "leaf", "name": "Leaf", "kind": "subcategory", "parents": ["mid"]},
    )
    tax = parse_taxonomy(payload)
    assert tax.chain("leaf") == ["leaf", "mid", "root"]


# ---------------------------------------------------------------- resolution


def test_resolve_matches_name_and_alias():
    tax = load_taxonomy()
    assert tax.resolve("Closed Guard") == "closed-guard"
    assert tax.resolve("full guard") == "closed-guard"
    assert tax.resolve("smash pass") == "pressure-pass"


def test_resolve_is_normalization_insensitive():
    tax = load_taxonomy()
    assert tax.resolve("  CLOSED   guard ") == "closed-guard"


def test_resolve_drops_ambiguous_names():
    """'body lock' is both a grip and a takedown — resolving it either way would be a guess."""
    tax = load_taxonomy()
    assert tax.resolve("body lock") is None
    assert tax.resolve("head control") is None
    assert tax.resolve("scramble") is None


def test_resolve_unknown_is_none():
    tax = load_taxonomy()
    assert tax.resolve("banana") is None
