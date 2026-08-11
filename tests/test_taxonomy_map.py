"""Mapping-proposer tests.

The property that matters most is the **type gate**: a proposal may never move a node into a
category its ``node_type`` does not already imply, because ELO, the graph renderer and the
directed-edge rules all read ``node_type``. Everything else is review ergonomics.
"""

from __future__ import annotations

import numpy as np

from analysis.canonicalize import Node
from analysis.taxonomy import load_taxonomy
from analysis.taxonomy_map import (
    _check,
    _render_markdown,
    propose_all,
    propose_one,
    summarize,
)


def test_self_check_passes():
    """Runs the module's own in-memory check so CI covers it too."""
    _check()


def test_auto_tier_requires_both_signals():
    tax = load_taxonomy()
    p = propose_one(Node("closed guard", "Closed Guard", "guard", "library"), tax)
    assert p.tier == "auto"
    assert p.subcategory == "closed-guard"
    assert p.category == "guard"


def test_type_gate_blocks_cross_category_mapping():
    """The ELO-safety property — a mistyped row must surface, not silently reclassify."""
    tax = load_taxonomy()
    p = propose_one(Node("closed guard", "Closed Guard", "submission", "user"), tax)
    assert p.subcategory is None
    assert p.tier == "manual"
    assert p.candidates == ["closed-guard"]


def test_label_naming_the_whole_family_maps_to_the_category():
    """A node literally called "Guard Pass" names the family. Mapping it to a subcategory
    would invent a detail the label does not carry."""
    tax = load_taxonomy()
    p = propose_one(Node("pass", "Pass", "pass", "library"), tax)
    assert p.subcategory == "pass"
    assert p.level == "category"
    assert p.tier == "auto"


def test_subcategory_hits_are_labelled_as_such():
    tax = load_taxonomy()
    p = propose_one(Node("closed guard", "Closed Guard", "guard", "library"), tax)
    assert p.level == "subcategory"


def test_missing_embedding_is_reported_as_the_reason():
    """Unrescued because there is no vector is a different problem from unclassifiable."""
    tax = load_taxonomy()
    node = Node("odd", "Completely Novel Thing", "submission", "user")
    got = propose_all(
        [node], tax,
        embed_lookup=lambda _k: None,
        sub_vectors={"arm-lock": np.array([1.0, 0.0])},
    )[0]
    assert got.subcategory is None
    assert "no embedding" in got.note


def test_ambiguous_bare_labels_never_auto_map():
    """"Choke" matches the airway subcategory by name, but in BJJ it nearly always means a
    blood strangle. A confident wrong answer is worse here than no answer."""
    tax = load_taxonomy()
    p = propose_one(Node("choke", "Choke", "submission", "library"), tax)
    assert p.tier == "manual"
    assert p.subcategory is None
    assert "does not mean it" in p.note


def test_ambiguity_verdict_survives_the_embedding_rescue():
    """The rescue tier must not overturn a deliberate 'a human decides' with a cosine."""
    tax = load_taxonomy()
    got = propose_all(
        [Node("choke", "Choke", "submission", "library")], tax,
        embed_lookup={"choke": np.array([1.0, 0.0])}.get,
        sub_vectors={"strangle": np.array([1.0, 0.0])},
        threshold=0.1,
    )[0]
    assert got.subcategory is None
    assert got.tier == "manual"


def test_generic_single_word_target_does_not_fire():
    tax = load_taxonomy()
    p = propose_one(Node("guard pass", "Guard Pass", "pass", "user"), tax)
    assert p.subcategory != "guard"


def test_distinctive_single_word_target_does_fire():
    tax = load_taxonomy()
    p = propose_one(Node("bow arrow", "Bow And Arrow Strangle", "submission", "user"), tax)
    assert (p.subcategory, p.tier, p.method) == ("strangle", "review", "tokens")


def test_out_of_taxonomy_types_are_never_mapped():
    tax = load_taxonomy()
    for nt in ("strike", "penalty", "match"):
        p = propose_one(Node(f"k{nt}", f"Some {nt}", nt, "user"), tax)
        assert p.subcategory is None
        assert p.tier == "manual"
        assert "event class" in p.note


def test_mislabeled_concept_rows_go_manual():
    tax = load_taxonomy()
    p = propose_one(Node("berimbolo", "Berimbolo", "concept", "user"), tax)
    assert p.tier == "manual"
    assert "node_type" in p.note


def test_unknown_node_type_does_not_crash():
    tax = load_taxonomy()
    p = propose_one(Node("x", "X", "definitely-not-a-type", "user"), tax)
    assert p.tier == "manual"
    assert p.subcategory is None


def test_embedding_tier_never_reaches_auto():
    tax = load_taxonomy()
    node = Node("m", "Mystery Lock", "submission", "user")
    got = propose_all(
        [node], tax,
        embed_lookup={"m": np.array([1.0, 0.0])}.get,
        sub_vectors={"arm-lock": np.array([1.0, 0.0]), "leg-lock": np.array([0.0, 1.0])},
        threshold=0.5,
    )[0]
    assert got.tier == "review"
    assert got.method == "embedding"


def test_embedding_respects_the_type_gate():
    """A submission must not be rescued into a Guard subcategory by cosine similarity."""
    tax = load_taxonomy()
    node = Node("m", "Mystery Thing", "submission", "user")
    got = propose_all(
        [node], tax,
        embed_lookup={"m": np.array([1.0, 0.0])}.get,
        sub_vectors={"closed-guard": np.array([1.0, 0.0])},  # not in the submission family
        threshold=0.1,
    )[0]
    assert got.subcategory is None


def test_summarize_counts_tiers_and_coverage():
    tax = load_taxonomy()
    nodes = [
        Node("closed guard", "Closed Guard", "guard", "library"),   # auto
        Node("s", "Bow And Arrow Strangle", "submission", "user"),  # review
        Node("p", "Some Strike", "strike", "user"),                 # manual
    ]
    s = summarize(propose_all(nodes, tax))
    assert s["total_nodes"] == 3
    assert s["mapped"] == 2
    assert s["unmapped"] == 1
    assert s["tiers"]["auto"] == 1
    assert s["tiers"]["manual"] == 1


def test_render_markdown_lists_every_tier():
    tax = load_taxonomy()
    nodes = [Node("closed guard", "Closed Guard", "guard", "library")]
    proposals = propose_all(nodes, tax)
    md = _render_markdown(proposals, summarize(proposals))
    assert "## auto" in md and "## review" in md and "## manual" in md
    assert "closed-guard" in md
    assert "nothing is written to the DB" in md
